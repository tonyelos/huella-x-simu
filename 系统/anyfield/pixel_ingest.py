"""Local pixel ingest for C-P1A (NPZ / FITS).

P1A deliberately reuses the app's science code for FITS/NPZ parsing and source
detection. This is a **P1A dependency**, not the long-term C architecture:
``sys.path`` is temporarily extended with ``系统/app`` and the app ``core``
package is imported under its normal name. The service's own module is named
``anyfield_core`` specifically to avoid colliding with app ``core``.

Scope: local files only. No IRSA/S3/TAP, no URL, no reprojection/common grid,
no forced photometry/series recomputation, no planetary/new-object logic.
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from wcs_footprint import FOOTPRINT_METHOD_CENTER, FOOTPRINT_METHOD_WCS, footprint_from_header

SERVICE_ROOT = Path(__file__).resolve().parent
APP_ROOT = SERVICE_ROOT.parent / "app"  # 系统/app
ALLOWED_SUFFIXES = {".npz", ".fits", ".fit", ".fts"}
MAX_FILE_BYTES = 256 * 1024 * 1024
_VERSION_RE = re.compile(r"(l2b-v[0-9A-Za-z._-]+)")


class InvalidPixelFile(ValueError):
    """Bad/malformed local NPZ/FITS; HTTP layer maps this to 400."""


@dataclass
class PixelData:
    image: np.ndarray
    variance: np.ndarray
    flags: np.ndarray
    header: dict[str, Any]
    mjd: float
    obsid: str
    detector: int
    date_obs: str
    release: str
    psf: np.ndarray | None
    kind: str
    path: str
    sha256: str


def _app_modules():
    """Temporarily add ``系统/app`` to sys.path and import app core modules.

    The path is removed again after the import; this is a P1A compatibility
    dependency, not the long-term C architecture. Imported modules stay in
    ``sys.modules`` for the process lifetime.
    """
    app_root = str(APP_ROOT)
    added = app_root not in sys.path
    if added:
        sys.path.insert(0, app_root)
    try:
        from core.fitsio import read_fits  # type: ignore[import-not-found]
        from core.pipeline import (  # type: ignore[import-not-found]
            dedupe_sources,
            detect_sources,
            source_quality,
        )
        from core.simulator import Epoch  # type: ignore[import-not-found]
        from core.wcs import WCS  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 - clear P1A dependency error
        raise InvalidPixelFile(f"app core 不可用（P1A 依赖 系统/app/core）：{exc}") from exc
    finally:
        if added:
            try:
                sys.path.remove(app_root)
            except ValueError:
                pass
    return read_fits, detect_sources, dedupe_sources, source_quality, Epoch, WCS


def _as_2d(value: Any, name: str) -> np.ndarray:
    arr = np.asarray(value)
    if arr.ndim != 2:
        raise InvalidPixelFile(f"{name} 必须是二维数组，实际 {arr.shape}")
    if arr.shape[0] < 16 or arr.shape[1] < 16:
        raise InvalidPixelFile(f"{name} 太小：{arr.shape}")
    if not np.isfinite(np.asarray(arr, float)).any():
        raise InvalidPixelFile(f"{name} 全部非有限")
    return arr


def _string_meta(data: Any, key: str, default: str = "") -> str:
    try:
        value = data[key]
    except Exception:  # noqa: BLE001 - npz mapping / dict
        return default
    if isinstance(value, np.ndarray) and value.shape == ():
        value = value.item()
    return str(value).strip() if value is not None else default


def _float_meta(data: Any, key: str) -> float | None:
    try:
        value = data[key]
    except Exception:  # noqa: BLE001
        return None
    if isinstance(value, np.ndarray) and value.shape == ():
        value = value.item()
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _header_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, np.ndarray) and raw.shape == ():
        raw = raw.item()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise InvalidPixelFile(f"NPZ header 不是合法 JSON：{exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    if isinstance(raw, dict):
        return dict(raw)
    raise InvalidPixelFile("NPZ header 必须是 JSON 对象")


def _release_from(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip().lower()
        if text in ("dr1", "qr3", "qr2", "qr1"):
            return text
        if text.startswith("dr1"):
            return "dr1"
        if text.startswith("qr3"):
            return "qr3"
    return "local"


def _version_from(*values: Any) -> str:
    for value in values:
        match = _VERSION_RE.search(str(value or ""))
        if match:
            return match.group(1)
    return ""


def _derive_meta(
    *,
    header: dict[str, Any],
    fallback: dict[str, Any],
    path: Path,
    sha256: str,
) -> dict[str, Any]:
    obsid = str(header.get("OBSID") or fallback.get("obsid") or "").strip()
    detector_raw = header.get("DETECTOR", fallback.get("detector"))
    try:
        detector = int(detector_raw)
    except (TypeError, ValueError) as exc:
        raise InvalidPixelFile(f"DETECTOR 非法：{detector_raw!r}") from exc
    if detector < 1 or detector > 6:
        raise InvalidPixelFile(f"DETECTOR={detector} 不在 1–6")
    release = _release_from(header.get("RELEASE"), fallback.get("release"), path.name)
    version = _version_from(
        header.get("VERSION"),
        header.get("PROCVER"),
        header.get("L2VERSION"),
        header.get("CALIBVER"),
        path.name,
    )
    if not version:
        version = f"local-{sha256[:12]}"
    mjd = _float_meta(header, "MJD-OBS")
    if mjd is None:
        mjd = _float_meta(fallback, "mjd")
    if mjd is None:
        raise InvalidPixelFile("缺 MJD-OBS/mjd")
    date_obs = str(header.get("DATE-OBS") or fallback.get("date_obs") or "").strip()
    if not date_obs:
        # ISO from MJD; keep it explicit rather than storing an empty field.
        from anyfield_core import mjd_to_iso

        date_obs = mjd_to_iso(mjd)
    ra = _float_meta(header, "CRVAL1")
    dec = _float_meta(header, "CRVAL2")
    if ra is None or dec is None or not (-90.0 <= dec <= 90.0):
        raise InvalidPixelFile("缺合法 CRVAL1/CRVAL2")
    if not obsid:
        raise InvalidPixelFile("缺 OBSID")
    week = obsid.rsplit("_", 2)[0] if "_" in obsid else ""
    footprint = footprint_from_header(header)
    if footprint is None:
        footprint = {
            "method": FOOTPRINT_METHOD_CENTER,
            "corners": [],
            "polygon": [],
            "center_ra": round(ra % 360.0, 8),
            "center_dec": round(dec, 8),
            "sip_applied": False,
        }
    return {
        "obsid": obsid,
        "detector": detector,
        "release": release,
        "version": version,
        "week": week or "unknown",
        "mjd": float(mjd),
        "date_obs": date_obs,
        "ra": float(ra) % 360.0,
        "dec": float(dec),
        "footprint": footprint,
        "source": "ingest",
        "frame_id": None,
    }


def _load_npz(path: Path, sha256: str) -> PixelData:
    try:
        with np.load(path, allow_pickle=False) as data:
            keys = set(data.files)
            missing = {"image", "variance", "flags", "header", "mjd", "obsid", "detector"} - keys
            if missing:
                raise InvalidPixelFile(f"NPZ 缺字段：{sorted(missing)}")
            image = _as_2d(data["image"], "image")
            variance = _as_2d(data["variance"], "variance")
            flags = _as_2d(data["flags"], "flags")
            header = _header_json(data["header"])
            mjd = _float_meta(data, "mjd")
            obsid = _string_meta(data, "obsid")
            detector = _string_meta(data, "detector")
            release = _string_meta(data, "release")
            date_obs = _string_meta(data, "date_obs")
            psf = np.asarray(data["psf"], float) if "psf" in keys else None
    except InvalidPixelFile:
        raise
    except Exception as exc:  # noqa: BLE001 - corrupt npz/zip
        raise InvalidPixelFile(f"读取 NPZ 失败：{exc}") from exc
    if image.shape != variance.shape or image.shape != flags.shape:
        raise InvalidPixelFile("NPZ image/variance/flags 形状不一致")
    if mjd is None:
        raise InvalidPixelFile("NPZ mjd 非法")
    try:
        detector_i = int(detector)
    except (TypeError, ValueError) as exc:
        raise InvalidPixelFile(f"NPZ detector 非法：{detector!r}") from exc
    return PixelData(
        image=np.asarray(image, float),
        variance=np.asarray(variance, float),
        flags=np.asarray(flags, np.int32),
        header=header,
        mjd=float(mjd),
        obsid=obsid,
        detector=detector_i,
        date_obs=date_obs,
        release=release,
        psf=psf,
        kind="npz",
        path=str(path),
        sha256=sha256,
    )


def _load_fits(path: Path, sha256: str) -> PixelData:
    read_fits, _detect, _dedupe, _quality, _Epoch, _WCS = _app_modules()
    try:
        fits = read_fits(path)
        image_hdu = fits.get("IMAGE")
        variance_hdu = fits.get("VARIANCE")
        flags_hdu = fits.get("FLAGS")
        if image_hdu is None or variance_hdu is None or flags_hdu is None:
            raise InvalidPixelFile("FITS 必须包含 IMAGE/VARIANCE/FLAGS")
        image = _as_2d(image_hdu.data, "IMAGE")
        variance = _as_2d(variance_hdu.data, "VARIANCE")
        flags = _as_2d(flags_hdu.data, "FLAGS")
        # P1A reads but does not use these extensions.
        fits.get("ZODI")
        fits.get("WCS-WAVE")
        header = dict(image_hdu.header)
    except InvalidPixelFile:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed FITS
        raise InvalidPixelFile(f"读取 FITS 失败：{exc}") from exc
    if image.shape != variance.shape or image.shape != flags.shape:
        raise InvalidPixelFile("FITS IMAGE/VARIANCE/FLAGS 形状不一致")
    mjd = _float_meta(header, "MJD-OBS")
    if mjd is None:
        raise InvalidPixelFile("FITS 缺 MJD-OBS")
    return PixelData(
        image=np.asarray(image, float),
        variance=np.asarray(variance, float),
        flags=np.asarray(flags, np.int32),
        header=header,
        mjd=float(mjd),
        obsid=str(header.get("OBSID") or "").strip(),
        detector=int(header.get("DETECTOR", 0) or 0),
        date_obs=str(header.get("DATE-OBS") or "").strip(),
        release=str(header.get("RELEASE") or ""),
        psf=None,  # EPSF explicitly does not participate in P1A
        kind="fits",
        path=str(path),
        sha256=sha256,
    )


def load_pixel_data(path: str | Path, sha256: str) -> PixelData:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".npz":
        return _load_npz(p, sha256)
    if suffix in (".fits", ".fit", ".fts"):
        return _load_fits(p, sha256)
    raise InvalidPixelFile(f"只接受 .npz/.fits，收到 {suffix}")


def inspect_file(path: str | Path, sha256: str) -> dict[str, Any]:
    """Synchronous validation/metadata; no source detection."""
    pixel = load_pixel_data(path, sha256)
    meta = _derive_meta(header=pixel.header, fallback={
        "obsid": pixel.obsid,
        "detector": pixel.detector,
        "release": pixel.release,
        "mjd": pixel.mjd,
        "date_obs": pixel.date_obs,
    }, path=Path(path), sha256=sha256)
    meta["file_kind"] = pixel.kind
    meta["input_path"] = pixel.path
    meta["file_sha256"] = sha256
    meta["image_shape"] = list(pixel.image.shape)
    meta["image_min"] = float(np.nanmin(pixel.image))
    meta["image_max"] = float(np.nanmax(pixel.image))
    return meta


def detect_file_sources(path: str | Path, sha256: str, epoch_meta: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load pixels, construct app Epoch, run detect_sources, return source rows."""
    read_fits, detect_sources, dedupe_sources, source_quality, Epoch, WCS = _app_modules()
    pixel = load_pixel_data(path, sha256)
    try:
        wcs = WCS.from_header(pixel.header)
        epoch = Epoch(
            id=f"anyfield-{epoch_meta['obsid']}-D{epoch_meta['detector']}",
            mjd=float(pixel.mjd),
            image=np.asarray(pixel.image, float),
            variance=np.asarray(pixel.variance, float),
            flags=np.asarray(pixel.flags, np.int32),
            wcs=wcs,
            psf=pixel.psf,
            provenance="real",
            band=f"D{epoch_meta['detector']}",
            obsid=epoch_meta["obsid"],
            date_obs=epoch_meta["date_obs"],
            source=str(path),
            meta={"release": epoch_meta["release"], "version": epoch_meta["version"]},
        )
        detected = dedupe_sources(detect_sources(epoch, max_sources=80, threshold=6.0))
    except Exception as exc:  # noqa: BLE001 - detection error becomes failed job
        raise InvalidPixelFile(f"检测失败：{exc}") from exc

    sources: list[dict[str, Any]] = []
    for idx, src in enumerate(detected):
        try:
            ra, dec = epoch.wcs.pixel_to_world(np.array([src.x + 1.0]), np.array([src.y + 1.0]))
            ra_f = float(np.atleast_1d(ra)[0])
            dec_f = float(np.atleast_1d(dec)[0])
        except Exception:  # noqa: BLE001 - one bad source must not kill the epoch
            continue
        if not all(math.isfinite(v) for v in (ra_f, dec_f, src.flux, src.flux_err, src.snr)):
            continue
        quality = list(source_quality(src, epoch.image.shape))
        sources.append(
            {
                "source_index": idx,
                "x": round(float(src.x), 3),
                "y": round(float(src.y), 3),
                "ra": round(ra_f % 360.0, 8),
                "dec": round(dec_f, 8),
                "flux": float(src.flux),
                "flux_err": float(src.flux_err),
                "snr": round(float(src.snr), 3),
                "saturated": bool(src.saturated),
                "quality": quality,
                "source_origin": "ingested",
                "method": str(src.method),
            }
        )
    epoch_meta = dict(epoch_meta)
    epoch_meta["source_count"] = len(sources)
    epoch_meta["ingest_kind"] = pixel.kind
    return epoch_meta, sources
