"""AnyField P0: local footprint coverage + metadata-only ingest.

P0 scope:

- read the existing ``catalog.sqlite`` read-only;
- build real WCS footprints in a sidecar for the 74 frames linked to a
  ``fits-header`` blob;
- fall back to ``center_approx`` for the 5,819 frames without headers;
- return 422 only for a query inside an explicitly ``complete=1``
  ``coverage_region`` with no epochs;
- return 503 ``INDEX_INCOMPLETE`` otherwise when there is no local evidence;
- accept a local JSON ingest manifest (metadata + footprint + optional
  measurements) and publish it atomically into the sidecar.

It does not fetch pixels, call IRSA/S3/TAP, run detection/reprojection, or
generate SIM. The P0 ingest is not the full C ingest pipeline.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import urllib.parse
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pixel_ingest
from sidecar import Sidecar
from wcs_footprint import (
    FOOTPRINT_METHOD_CENTER,
    FOOTPRINT_METHOD_WCS,
    angular_sep_deg,
    point_in_polygon,
)

SERVICE_NAME = "anyfield-p1a"
SERVICE_VERSION = "0.3.0"
SCHEMA_VERSION = "1.0"
# Legacy C-MVP field name; the authoritative per-epoch field is
# coverage_method/footprint_method. Kept for the original 18 tests.
COVERAGE_MODE = "center_approx"
INDEX_COMPLETENESS = "partial"
CENTER_RADIUS_DEG = 2.47
TARGET_MATCH_TOL_DEG = 0.05
FOV_MIN = 0.02
FOV_MAX = 3.0
IRSA_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)
SERVICE_ROOT = Path(__file__).resolve().parent
DEFAULT_SIDECAR = SERVICE_ROOT / "var" / "anyfield.sqlite"
DEFAULT_BLOB_ROOT = SERVICE_ROOT.parent / "数据库工具" / "var" / "blobs"
REPO_ROOT = SERVICE_ROOT.parent.parent
MAX_FIELD_SOURCES = 200
_VERSION_RE = re.compile(r"_spx_([^/_]+)\.fits$")
_ALLOWED_FOOTPRINT_METHODS = {FOOTPRINT_METHOD_WCS, FOOTPRINT_METHOD_CENTER}


class ApiError(Exception):
    """HTTP error with a stable machine-readable code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.details = details or {}
        self.retryable = bool(retryable)


def mjd_to_iso(mjd: float) -> str:
    moment = MJD_EPOCH + timedelta(days=float(mjd))
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def coverage_level(n_epochs: int, baseline_days: float) -> str:
    """Same observational-opportunity levels as the DB tool (C0-C3)."""
    if n_epochs <= 1:
        return "C0"
    if n_epochs == 2:
        return "C1"
    if n_epochs < 6:
        return "C2"
    return "C3" if baseline_days >= 180.0 else "C2"


def build_fits_key(
    release: str | None,
    week: str | None,
    version: str | None,
    obs_id: str,
    detector: int | None,
) -> str | None:
    """Reconstruct a level2 key without network access."""
    if not (release and week and version and detector is not None):
        return None
    parts = str(obs_id).rsplit("_", 2)
    if len(parts) != 3:
        return None
    _, field_s, repeat_s = parts
    try:
        field_no = int(field_s)
        repeat = int(repeat_s)
    except ValueError:
        return None
    name = f"level2_{week}_{field_no:04d}_{repeat}D{int(detector)}_spx_{version}.fits"
    return f"{release}/level2/{week}/{version}/{int(detector)}/{name}"


def build_slice_url(key: str | None, ra: float, dec: float, fov: float) -> str | None:
    if not key:
        return None
    return f"{IRSA_BASE}{key}?center={ra:.6f},{dec:.6f}d&size={fov:g}"


def _version_from_key(key: str | None) -> str:
    if not key:
        return ""
    match = _VERSION_RE.search(key)
    return match.group(1) if match else ""


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result


def _validate_query(ra: Any, dec: Any, fov: Any) -> tuple[float, float, float]:
    ra_f = _as_float(ra)
    dec_f = _as_float(dec)
    fov_f = _as_float(fov)
    if ra_f is None or dec_f is None:
        raise ApiError(400, "INVALID_QUERY", "ra/dec 必须是有限数字")
    if fov_f is None:
        raise ApiError(400, "INVALID_QUERY", "fov 必须是有限数字")
    if not (-90.0 <= dec_f <= 90.0):
        raise ApiError(400, "INVALID_QUERY", "dec 必须在 [-90, 90] 度")
    if not (FOV_MIN <= fov_f <= FOV_MAX):
        raise ApiError(400, "INVALID_QUERY", f"fov 必须在 [{FOV_MIN}, {FOV_MAX}] 度")
    return ra_f % 360.0, dec_f, fov_f


def _default_ingest_roots() -> list[Path]:
    env = os.environ.get("ANYFIELD_INGEST_ROOT", "").strip()
    if env:
        raw_parts = [p for p in env.split(os.pathsep) if p.strip()]
    else:
        raw_parts = [
            str(SERVICE_ROOT.parent / "app" / "data"),
            str(SERVICE_ROOT.parent / "参考 资料" / "muestras"),
        ]
    roots: list[Path] = []
    for raw in raw_parts:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        roots.append(path.resolve())
    return roots


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _epoch_id_for(release: str, version: str, obsid: str, detector: int) -> str:
    return _canonical_sha({"release": release, "version": version, "obsid": obsid, "detector": detector})


def _reject_network_strings(value: Any, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in {"url", "uri", "access_url", "download_url", "source_url"}:
                raise ApiError(400, "NETWORK_SOURCE_NOT_ALLOWED", f"{path}.{key} 不允许网络 URL")
            _reject_network_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _reject_network_strings(item, f"{path}[{idx}]")
    elif isinstance(value, str):
        low = value.strip().lower()
        if low.startswith(("http://", "https://")):
            raise ApiError(400, "NETWORK_SOURCE_NOT_ALLOWED", f"{path} 不允许网络 URL")


def _validate_footprint(raw: Any, ra: float, dec: float) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(400, "INVALID_MANIFEST", "epoch.footprint 必须是对象")
    method = str(raw.get("method") or "").strip()
    if method not in _ALLOWED_FOOTPRINT_METHODS:
        raise ApiError(400, "INVALID_MANIFEST", "footprint.method 必须是 wcs_header 或 center_approx")
    corners_raw = raw.get("corners")
    corners: list[list[float]] = []
    if corners_raw is not None:
        if not isinstance(corners_raw, list):
            raise ApiError(400, "INVALID_MANIFEST", "footprint.corners 必须是列表")
        for idx, point in enumerate(corners_raw):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ApiError(400, "INVALID_MANIFEST", f"footprint.corners[{idx}] 必须是 [ra,dec]")
            p_ra = _as_float(point[0]) % 360.0 if _as_float(point[0]) is not None else None
            p_dec = _as_float(point[1])
            if p_ra is None or p_dec is None or not (-90 <= p_dec <= 90):
                raise ApiError(400, "INVALID_MANIFEST", f"footprint.corners[{idx}] 坐标非法")
            corners.append([round(p_ra, 8), round(p_dec, 8)])
    if method == FOOTPRINT_METHOD_WCS and len(corners) < 3:
        raise ApiError(400, "INVALID_MANIFEST", "wcs_header footprint 至少需要 3 个 corners")
    return {
        "method": method,
        "corners": corners,
        "polygon": corners,
        "center_ra": round(float(ra), 8),
        "center_dec": round(float(dec), 8),
        "sip_applied": False,
    }


def _validate_measurement(raw: Any, idx: int, epoch_id: str, epoch_mjd: float) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(400, "INVALID_MANIFEST", f"measurements[{idx}] 必须是对象")
    target_key = str(raw.get("target_key") or "").strip()
    if not target_key:
        raise ApiError(400, "INVALID_MANIFEST", f"measurements[{idx}].target_key 不能为空")
    ra = _as_float(raw.get("ra"))
    dec = _as_float(raw.get("dec"))
    if ra is None or dec is None or not (-90 <= dec <= 90):
        raise ApiError(400, "INVALID_MANIFEST", f"measurements[{idx}] 缺少数值 ra/dec")
    mjd = _as_float(raw.get("mjd"))
    if mjd is None:
        mjd = epoch_mjd
    quality = raw.get("quality") or []
    if not isinstance(quality, list):
        raise ApiError(400, "INVALID_MANIFEST", f"measurements[{idx}].quality 必须是列表")
    measurement_id = _canonical_sha({"epoch_id": epoch_id, "target_key": target_key})
    return {
        "measurement_id": measurement_id,
        "target_key": target_key,
        "mjd": float(mjd),
        "ra": float(ra) % 360.0,
        "dec": float(dec),
        "flux": _as_float(raw.get("flux")),
        "flux_err": _as_float(raw.get("flux_err")),
        "snr": _as_float(raw.get("snr")),
        "quality": [str(q) for q in quality],
    }


class Store:
    """Read-only catalog + sidecar footprint/index."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        sidecar_path: str | Path | None = None,
        blob_root: str | Path | None = None,
        ingest_roots: list[str | Path] | None = None,
        build_footprints: bool = True,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.sidecar_path = Path(sidecar_path or DEFAULT_SIDECAR).expanduser().resolve()
        self.blob_root = Path(blob_root or DEFAULT_BLOB_ROOT).expanduser().resolve()
        if ingest_roots is None:
            self.ingest_roots = _default_ingest_roots()
        else:
            self.ingest_roots = [Path(p).expanduser().resolve() for p in ingest_roots]
        self._snapshot_lock = threading.Lock()
        self._ingest_lock = threading.Lock()
        self._snapshot_signature: tuple[Any, ...] | None = None
        self._snapshot_value: str | None = None
        self.sidecar = Sidecar(self.sidecar_path)
        self.footprint_stats: dict[str, Any] = {
            "headers": 0,
            "built": 0,
            "failed": 0,
            "blob_root": str(self.blob_root),
        }
        if build_footprints:
            self.refresh_footprints()

    def refresh_footprints(self) -> dict[str, Any]:
        try:
            self.footprint_stats = self.sidecar.sync_footprints(self.db_path, self.blob_root)
        except Exception as exc:  # noqa: BLE001 - service must stay up
            self.footprint_stats = {
                "headers": 0,
                "built": 0,
                "failed": 0,
                "error": str(exc),
                "blob_root": str(self.blob_root),
            }
        return self.footprint_stats

    # ------------------------------------------------------------------ db
    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise ApiError(
                503,
                "DB_UNAVAILABLE",
                f"本地数据库不存在：{self.db_path}",
                details={
                    "db_path": str(self.db_path),
                    "read_only": True,
                    "strong_assertion": False,
                    "no_local_coverage": False,
                },
                retryable=True,
            )
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("SELECT 1").fetchone()
            return conn
        except sqlite3.Error as exc:
            raise ApiError(
                503,
                "DB_UNAVAILABLE",
                f"本地数据库不可读：{exc}",
                details={
                    "db_path": str(self.db_path),
                    "read_only": True,
                    "strong_assertion": False,
                    "no_local_coverage": False,
                },
                retryable=True,
            ) from exc

    def _signature(self) -> tuple[int, int] | None:
        try:
            stat = self.db_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def index_snapshot(self) -> str:
        """Stable id from catalog metadata + sidecar footprint/ingest counts.

        The cache key includes sidecar counts so an ingest/rollback/complete
        region insertion changes the snapshot even when catalog.sqlite is
        untouched.
        """
        db_signature = self._signature()
        if db_signature is None:
            return "local-unavailable"
        try:
            sidecar_counts = self.sidecar.counts()
        except Exception:
            sidecar_counts = {}
        signature = (db_signature[0], db_signature[1], tuple(sorted(sidecar_counts.items())))
        with self._snapshot_lock:
            if self._snapshot_value and self._snapshot_signature == signature:
                return self._snapshot_value
            try:
                with closing(self._connect()) as conn:
                    counts = {
                        "frames": int(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]),
                        "observations": int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),
                        "target_epochs": int(conn.execute("SELECT COUNT(*) FROM target_epochs").fetchone()[0]),
                        "targets": int(conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]),
                    }
                    row = conn.execute("SELECT MAX(frame_id), MAX(mjd) FROM frames").fetchone()
                    counts["max_frame_id"] = row[0]
                    counts["max_mjd"] = row[1]
                    meta_row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                    payload = {
                        "kind": "local-catalog+sidecar",
                        "db_schema": meta_row[0] if meta_row else "unknown",
                        "counts": counts,
                        "sidecar": sidecar_counts,
                    }
            except (sqlite3.Error, ApiError):
                self._snapshot_value = "local-unavailable"
                self._snapshot_signature = signature
                return self._snapshot_value
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:12]
            self._snapshot_value = f"local-{digest}"
            self._snapshot_signature = signature
            return self._snapshot_value

    def health(self) -> dict[str, Any]:
        db_ok = True
        db_error: str | None = None
        try:
            with closing(self._connect()) as conn:
                conn.execute("SELECT COUNT(*) FROM frames").fetchone()
        except ApiError as exc:
            db_ok = False
            db_error = exc.message
        except sqlite3.Error as exc:
            db_ok = False
            db_error = str(exc)
        sidecar_counts = self.sidecar.counts()
        body: dict[str, Any] = {
            "status": "ok" if db_ok else "degraded",
            "service": SERVICE_NAME,
            "service_version": SERVICE_VERSION,
            "schema_version": SCHEMA_VERSION,
            # Legacy C-MVP field; authoritative values are coverage_method /
            # footprint_method in the field response.
            "coverage_mode": COVERAGE_MODE,
            "index_completeness": INDEX_COMPLETENESS,
            "index_snapshot": self.index_snapshot() if db_ok else "local-unavailable",
            "network": "none",
            "writes": "none",  # catalog DB only; sidecar writes are separate
            "catalog_writes": "none",
            "sidecar_writes": "enabled",
            "db": {
                "path": str(self.db_path),
                "readable": db_ok,
                "read_only": True,
            },
            "sidecar": {
                "path": str(self.sidecar_path),
                "counts": sidecar_counts,
            },
            "footprints": dict(self.footprint_stats),
            "ingest": {
                "pixel_kinds": ["npz", "fits"],
                "pixel_ingest": True,
                "allowed_roots": [str(p) for p in self.ingest_roots],
                "network": False,
            },
        }
        if db_error:
            body["error"] = {
                "code": "DB_UNAVAILABLE",
                "message": db_error,
                "retryable": True,
            }
        return body

    # ------------------------------------------------------------------ target / frames
    def _match_target(self, conn: sqlite3.Connection, ra: float, dec: float) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        try:
            rows = conn.execute("SELECT id, name, ra, dec, official FROM targets").fetchall()
        except sqlite3.Error as exc:
            raise ApiError(503, "DB_UNAVAILABLE", f"读取 targets 失败：{exc}", retryable=True) from exc
        for row in rows:
            sep = angular_sep_deg(ra, dec, row["ra"], row["dec"])
            if not math.isfinite(sep) or sep > TARGET_MATCH_TOL_DEG:
                continue
            if best is None or sep < best[0]:
                best = (sep, {k: row[k] for k in row.keys()} | {"sep_deg": sep})
        return best[1] if best else None

    def _target_epochs(
        self, conn: sqlite3.Connection, target_id: str, ra: float, dec: float, fov: float
    ) -> list[dict[str, Any]]:
        try:
            rows = conn.execute(
                """
                SELECT e.id, e.obs_id, e.detector, e.release, e.week, e.mjd,
                       e.date_obs, e.sep_deg, e.fits_key, e.slice_url, e.frame_id,
                       w.version, f.ra AS frame_ra, f.dec AS frame_dec
                FROM target_epochs e
                LEFT JOIN weeks w
                       ON w.week = e.week AND w.release = e.release
                LEFT JOIN frames f ON f.frame_id = e.frame_id
                WHERE e.target_id = ?
                ORDER BY e.mjd ASC, e.detector ASC
                """,
                (str(target_id),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ApiError(503, "DB_UNAVAILABLE", f"读取 target_epochs 失败：{exc}", retryable=True) from exc

        out: list[dict[str, Any]] = []
        for row in rows:
            epoch = self._target_epoch(row, ra, dec, fov)
            if epoch is not None:
                out.append(epoch)
        return out

    def _target_epoch(
        self, row: sqlite3.Row, ra: float, dec: float, fov: float
    ) -> dict[str, Any] | None:
        obs_id = str(row["obs_id"] or "").strip()
        detector = row["detector"]
        mjd = _as_float(row["mjd"])
        if not obs_id or detector is None or mjd is None:
            return None
        try:
            detector = int(detector)
        except (TypeError, ValueError):
            return None
        if detector < 1 or detector > 6:
            return None

        release = str(row["release"] or "").strip()
        week = str(row["week"] or "").strip()
        key = str(row["fits_key"] or "").strip()
        version = str(row["version"] or "").strip()
        if not key:
            key = build_fits_key(release, week, version, obs_id, detector) or ""
        if not version:
            version = _version_from_key(key)
        if not release and key:
            release = key.split("/", 1)[0]
        if not week:
            week = obs_id.rsplit("_", 2)[0] if "_" in obs_id else ""
        if not (release and week and key):
            return None

        sep = _as_float(row["sep_deg"])
        frame_ra = _as_float(row["frame_ra"])
        frame_dec = _as_float(row["frame_dec"])
        if frame_ra is not None and frame_dec is not None:
            computed = angular_sep_deg(ra, dec, frame_ra, frame_dec)
            if math.isfinite(computed):
                sep = computed
        if sep is None or not math.isfinite(sep):
            sep = 0.0

        date_obs = str(row["date_obs"] or "").strip() or mjd_to_iso(mjd)
        return {
            "obsid": obs_id,
            "detector": detector,
            "release": release,
            "week": week,
            "version": version or "unknown",
            "mjd": float(mjd),
            "date_obs": date_obs,
            "sep_deg": round(float(sep), 6),
            "key": key,
            "slice_url": build_slice_url(key, ra, dec, fov),
            "provenance": "real",
            "frame_id": int(row["frame_id"]) if row["frame_id"] is not None else None,
        }

    def _frames(
        self, conn: sqlite3.Connection, ra: float, dec: float, fov: float
    ) -> tuple[list[dict[str, Any]], bool]:
        radius = CENTER_RADIUS_DEG
        dec_min = max(-90.0, dec - radius)
        dec_max = min(90.0, dec + radius)
        where = ["f.dec BETWEEN ? AND ?", "f.ra IS NOT NULL", "f.dec IS NOT NULL", "f.mjd IS NOT NULL"]
        args: list[Any] = [dec_min, dec_max]

        if dec_min > -90.0 and dec_max < 90.0:
            cos_dec = max(math.cos(math.radians(dec)), 0.02)
            ra_width = min(180.0, radius / cos_dec)
            if ra_width < 180.0:
                lo = (ra - ra_width) % 360.0
                hi = (ra + ra_width) % 360.0
                if lo <= hi:
                    where.append("f.ra BETWEEN ? AND ?")
                    args.extend([lo, hi])
                else:
                    where.append("(f.ra >= ? OR f.ra <= ?)")
                    args.extend([lo, hi])

        sql = f"""
            SELECT f.frame_id, f.obs_id, f.detector,
                   COALESCE(f.release, o.release) AS release,
                   f.week, f.mjd, f.date_obs, f.ra, f.dec, w.version
            FROM frames f
            JOIN observations o ON o.obs_id = f.obs_id
            LEFT JOIN weeks w
                   ON w.week = f.week
                  AND w.release = COALESCE(f.release, o.release)
            WHERE {' AND '.join(where)}
            ORDER BY f.mjd ASC, f.detector ASC
            LIMIT 5000
        """
        try:
            rows = conn.execute(sql, args).fetchall()
        except sqlite3.Error as exc:
            raise ApiError(503, "DB_UNAVAILABLE", f"读取 frames 失败：{exc}", retryable=True) from exc

        truncated = len(rows) >= 5000
        out: list[dict[str, Any]] = []
        for row in rows:
            sep = angular_sep_deg(ra, dec, row["ra"], row["dec"])
            if not math.isfinite(sep) or sep > radius + 1e-9:
                continue
            epoch = self._frame_epoch(row, sep, ra, dec, fov)
            if epoch is not None:
                out.append(epoch)
        return out, truncated

    def _frame_epoch(
        self, row: sqlite3.Row, sep: float, ra: float, dec: float, fov: float
    ) -> dict[str, Any] | None:
        obs_id = str(row["obs_id"] or "").strip()
        detector = row["detector"]
        mjd = _as_float(row["mjd"])
        if not obs_id or detector is None or mjd is None:
            return None
        try:
            detector = int(detector)
        except (TypeError, ValueError):
            return None
        if detector < 1 or detector > 6:
            return None
        release = str(row["release"] or "").strip()
        week = str(row["week"] or "").strip()
        version = str(row["version"] or "").strip()
        key = build_fits_key(release, week, version, obs_id, detector)
        if not key:
            return None
        date_obs = str(row["date_obs"] or "").strip() or mjd_to_iso(mjd)
        return {
            "obsid": obs_id,
            "detector": detector,
            "release": release,
            "week": week,
            "version": version,
            "mjd": float(mjd),
            "date_obs": date_obs,
            "sep_deg": round(float(sep), 6),
            "key": key,
            "slice_url": build_slice_url(key, ra, dec, fov),
            "provenance": "real",
            "frame_id": int(row["frame_id"]) if row["frame_id"] is not None else None,
        }

    # ------------------------------------------------------------------ sidecar epochs
    def _sidecar_epochs(self, qra: float, qdec: float, qfov: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in self.sidecar.visible_epochs():
            ra = _as_float(row.get("ra"))
            dec = _as_float(row.get("dec"))
            mjd = _as_float(row.get("mjd"))
            detector = _as_int(row.get("detector"))
            if ra is None or dec is None or mjd is None or detector is None:
                continue
            sep = angular_sep_deg(qra, qdec, ra, dec)
            footprint = row.get("footprint") if isinstance(row.get("footprint"), dict) else None
            method = str(row.get("footprint_method") or FOOTPRINT_METHOD_CENTER)
            if method == FOOTPRINT_METHOD_WCS and footprint:
                corners = footprint.get("corners") or []
                if not point_in_polygon(qra, qdec, corners):
                    continue
            elif sep > CENTER_RADIUS_DEG:
                continue
            key = build_fits_key(row.get("release"), row.get("week"), row.get("version"), row["obs_id"], detector)
            if not key:
                continue
            out.append(
                {
                    "obsid": str(row["obs_id"]),
                    "detector": detector,
                    "release": str(row.get("release") or ""),
                    "week": str(row.get("week") or ""),
                    "version": str(row.get("version") or ""),
                    "mjd": float(mjd),
                    "date_obs": mjd_to_iso(mjd),
                    "sep_deg": round(float(sep), 6),
                    "key": key,
                    "slice_url": build_slice_url(key, qra, qdec, qfov),
                    "provenance": "real",
                    "frame_id": int(row["frame_id"]) if row.get("frame_id") is not None else None,
                    "coverage_method": method,
                    "footprint_method": method,
                    "epoch_id": str(row["epoch_id"]),
                    "source_count": int(row.get("source_count") or 0),
                }
            )
        return out

    # ------------------------------------------------------------------ filtering
    @staticmethod
    def _apply_footprint(
        epoch: dict[str, Any],
        footprints: dict[str, Any],
        qra: float,
        qdec: float,
    ) -> dict[str, Any] | None:
        frame_id = epoch.get("frame_id")
        fp = footprints["by_frame"].get(int(frame_id)) if frame_id is not None else None
        if fp is None:
            fp = footprints["by_obs_det"].get((str(epoch["obsid"]), int(epoch["detector"])))
        if fp is not None and fp.get("method") == FOOTPRINT_METHOD_WCS:
            if not point_in_polygon(qra, qdec, fp.get("corners") or []):
                return None
            epoch["coverage_method"] = FOOTPRINT_METHOD_WCS
            epoch["footprint_method"] = FOOTPRINT_METHOD_WCS
            return epoch
        if float(epoch.get("sep_deg") or 0.0) > CENTER_RADIUS_DEG:
            return None
        epoch["coverage_method"] = FOOTPRINT_METHOD_CENTER
        epoch["footprint_method"] = FOOTPRINT_METHOD_CENTER
        return epoch

    @staticmethod
    def _dedupe_epochs(epochs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One EpochRef per obsid; prefer a real footprint then nearest detector."""
        by_obs: dict[str, dict[str, Any]] = {}
        for epoch in sorted(
            epochs,
            key=lambda e: (
                0 if e.get("coverage_method") == FOOTPRINT_METHOD_WCS else 1,
                e["mjd"],
                e.get("sep_deg", 9.0),
                e["detector"],
            ),
        ):
            cur = by_obs.get(epoch["obsid"])
            if cur is None or epoch.get("sep_deg", 9.0) < cur.get("sep_deg", 9.0):
                by_obs[epoch["obsid"]] = epoch
        return sorted(by_obs.values(), key=lambda e: (e["mjd"], e["detector"]))

    # ------------------------------------------------------------------ public resolver
    def resolve_field(self, ra: Any, dec: Any, fov: Any) -> dict[str, Any]:
        qra, qdec, qfov = _validate_query(ra, dec, fov)
        footprints = self.sidecar.footprint_map()
        target: dict[str, Any] | None = None
        base: list[dict[str, Any]] = []
        truncated = False
        source = "frames"
        with closing(self._connect()) as conn:
            target = self._match_target(conn, qra, qdec)
            if target is not None:
                base = self._target_epochs(conn, target["id"], qra, qdec, qfov)
                source = "target_epochs"
            if not base:
                frames, truncated = self._frames(conn, qra, qdec, qfov)
                base = frames
                source = "frames"

        filtered: list[dict[str, Any]] = []
        for epoch in base:
            kept = self._apply_footprint(epoch, footprints, qra, qdec)
            if kept is not None:
                filtered.append(kept)
        side = self._sidecar_epochs(qra, qdec, qfov)
        raw = filtered + side
        if raw:
            if side and not filtered:
                source = "ingest"
            elif side and filtered:
                source = "mixed"
            body = self._response(
                query={"ra": round(qra, 6), "dec": round(qdec, 6), "fov": qfov},
                raw_epochs=raw,
                target=target,
                source=source,
                truncated=truncated,
            )
            return self._attach_sources(body, qra, qdec, qfov)

        region = self.sidecar.complete_region_for(qra, qdec)
        if region is not None:
            raise ApiError(
                422,
                "NO_LOCAL_COVERAGE",
                "该查询落在显式 complete=1 的 coverage_region 内，且没有本地历元；"
                "这是 P0 唯一允许的 NO_LOCAL_COVERAGE 强断言。",
                details={
                    "coverage_mode": COVERAGE_MODE,
                    "coverage_method": "complete_region",
                    "index_completeness": "complete",
                    "index_snapshot": self.index_snapshot(),
                    "strong_assertion": True,
                    "no_local_coverage": True,
                    "sim_allowed": True,
                    "region": {
                        "region_id": region.get("region_id"),
                        "ra": region.get("ra"),
                        "dec": region.get("dec"),
                        "radius_deg": region.get("radius_deg"),
                        "sep_deg": round(float(region.get("sep_deg") or 0.0), 6),
                    },
                },
                retryable=False,
            )

        raise ApiError(
            503,
            "INDEX_INCOMPLETE",
            "C-P0 只有本地帧/74 条 header 足迹/已 ingest 的元数据；该天区没有本地证据，"
            "且不在 complete=1 的 coverage_region 内，不能断言 NO_LOCAL_COVERAGE。",
            details={
                "coverage_mode": COVERAGE_MODE,
                "coverage_method": "unknown",
                "coverage_state": "unknown",
                "index_completeness": INDEX_COMPLETENESS,
                "index_snapshot": self.index_snapshot(),
                "strong_assertion": False,
                "no_local_coverage": False,
            },
            retryable=True,
        )

    def resolve_epochs(self, ra: Any, dec: Any, fov: Any) -> dict[str, Any]:
        field = self.resolve_field(ra, dec, fov)
        return {
            "schema_version": SCHEMA_VERSION,
            "service_version": SERVICE_VERSION,
            "index_snapshot": field["index_snapshot"],
            "index_completeness": field.get("index_completeness", INDEX_COMPLETENESS),
            "coverage_mode": COVERAGE_MODE,
            "query": field["query"],
            "coverage": field["coverage"],
            "epochs": field["epochs"],
            "count": field["count"],
            "returned": field["returned"],
            "fallback_reason": None,
            "warnings": field["warnings"],
            "generated_at": field["generated_at"],
        }

    def _response(
        self,
        *,
        query: dict[str, Any],
        raw_epochs: list[dict[str, Any]],
        target: dict[str, Any] | None,
        source: str,
        truncated: bool,
    ) -> dict[str, Any]:
        epochs = self._dedupe_epochs(raw_epochs)
        mjds = [e["mjd"] for e in epochs]
        baseline = (max(mjds) - min(mjds)) if len(mjds) >= 2 else 0.0
        n_epochs = len(epochs)
        n_weeks = len({e["week"] for e in epochs if e["week"]})
        detectors = sorted({e["detector"] for e in raw_epochs})
        releases = sorted({e["release"] for e in raw_epochs if e["release"]})
        target_id = target["id"] if target else None
        methods = {str(e.get("coverage_method")) for e in epochs if e.get("coverage_method")}
        if methods == {FOOTPRINT_METHOD_WCS}:
            coverage_method = FOOTPRINT_METHOD_WCS
        elif methods == {FOOTPRINT_METHOD_CENTER}:
            coverage_method = FOOTPRINT_METHOD_CENTER
        elif methods:
            coverage_method = "mixed"
        else:
            coverage_method = "none"
        n_wcs = sum(1 for e in epochs if e.get("coverage_method") == FOOTPRINT_METHOD_WCS)
        n_center = sum(1 for e in epochs if e.get("coverage_method") == FOOTPRINT_METHOD_CENTER)
        coverage = {
            "state": "covered",
            # Legacy C-MVP fields. They describe the candidate-search mode, not
            # the authoritative per-epoch method.
            "coverage_mode": COVERAGE_MODE,
            "method": "center_radius_2.47",
            "method_deprecated": True,
            "radius_deg": CENTER_RADIUS_DEG,
            "search_radius_deg": CENTER_RADIUS_DEG,
            # P0 authoritative fields.
            "coverage_method": coverage_method,
            "footprint_method": coverage_method,
            "n_wcs_header": n_wcs,
            "n_center_approx": n_center,
            "n_frames": len(raw_epochs),
            "n_epochs": n_epochs,
            "n_observations": n_epochs,
            "n_weeks": n_weeks,
            "n_releases": len(releases),
            "releases": releases,
            "detectors": detectors,
            "mjd_min": round(min(mjds), 6) if mjds else None,
            "mjd_max": round(max(mjds), 6) if mjds else None,
            "baseline_days": round(baseline, 3),
            "coverage_level": coverage_level(n_epochs, baseline),
            "index_completeness": INDEX_COMPLETENESS,
            "target_id": target_id,
            "target_name": target.get("name") if target else None,
            "target_sep_deg": round(float(target["sep_deg"]), 6) if target else None,
        }
        warnings = [
            "CENTER_APPROX_COVERAGE_LEGACY_FIELD: coverage.method/radius are kept for C-MVP compatibility; "
            "use coverage.coverage_method/footprint_method and per-epoch coverage_method",
            "LOCAL_INDEX_PARTIAL: only 74 linked FITS-header footprints + 5,819 center-approx frames; "
            "no complete sky index",
        ]
        if n_wcs:
            warnings.append(
                "WCS_HEADER_FOOTPRINT: real four-corner footprints for frames with linked fits-header; SIP not applied"
            )
        if n_center:
            warnings.append("CENTER_APPROX_EPOCHS: frames without linked header use center_approx, not a real footprint")
        if target:
            warnings.append(f"TARGET_MATCHED: {target_id} within {TARGET_MATCH_TOL_DEG}°")
        else:
            warnings.append("NO_PRESET_TARGET_MATCH: frame/footprint query")
        if truncated:
            warnings.append("FRAME_QUERY_TRUNCATED_AT_5000: narrow the field or add an index")
        return {
            "schema_version": SCHEMA_VERSION,
            "service_version": SERVICE_VERSION,
            "index_snapshot": self.index_snapshot(),
            "index_completeness": INDEX_COMPLETENESS,
            "coverage_mode": COVERAGE_MODE,
            "footprint_method": coverage_method,
            "query": query,
            "coverage": coverage,
            "epochs": epochs,
            "count": n_epochs,
            "returned": n_epochs,
            "source": source,
            "warnings": warnings,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    # ------------------------------------------------------------------ local file ingest (P1A)
    @staticmethod
    def _is_file_manifest(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if any(key in payload for key in ("path", "file", "filename")):
            return True
        value = payload.get("input")
        return isinstance(value, (str, dict))

    @staticmethod
    def _extract_file_path(payload: dict[str, Any]) -> str | None:
        for key in ("path", "file", "filename"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        value = payload.get("input")
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            for key in ("path", "file", "filename"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    return item.strip()
        return None

    def _resolve_ingest_path(self, raw: str) -> Path:
        text = str(raw).strip()
        low = text.lower()
        if low.startswith(("http://", "https://", "file://")):
            raise ApiError(400, "NETWORK_SOURCE_NOT_ALLOWED", "P1A 只接受本地文件路径，不接受 URL")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        path = candidate.resolve()
        if not path.is_file():
            raise ApiError(400, "INGEST_FILE_NOT_FOUND", f"本地文件不存在：{text}")
        suffix = path.suffix.lower()
        if suffix not in (".npz", ".fits", ".fit", ".fts"):
            raise ApiError(400, "UNSUPPORTED_INGEST_FILE", f"只接受 .npz/.fits，收到 {suffix}")
        if path.stat().st_size > pixel_ingest.MAX_FILE_BYTES:
            raise ApiError(400, "INGEST_FILE_TOO_LARGE", "本地像素文件超过 256 MB")
        if not any(path == root or root in path.parents for root in self.ingest_roots):
            raise ApiError(
                400,
                "PATH_NOT_ALLOWED",
                "本地文件不在允许的 ingest root 内",
                details={"allowed_roots": [str(p) for p in self.ingest_roots]},
            )
        return path

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def validate_file_manifest(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_MANIFEST", "manifest 必须是 JSON 对象")
        _reject_network_strings(payload)
        source = payload.get("source")
        if source is not None:
            if not isinstance(source, dict):
                raise ApiError(400, "INVALID_MANIFEST", "source 必须是对象")
            kind = str(source.get("kind") or "local_file")
            if kind not in ("local_file", "local_manifest"):
                raise ApiError(400, "NETWORK_SOURCE_NOT_ALLOWED", "P1A 只接受本地文件/本地 manifest")
        raw_path = self._extract_file_path(payload)
        if not raw_path:
            raise ApiError(400, "INVALID_MANIFEST", "缺 input.path/path/file")
        path = self._resolve_ingest_path(raw_path)
        sha = self._hash_file(path)
        try:
            meta = pixel_ingest.inspect_file(path, sha)
        except pixel_ingest.InvalidPixelFile as exc:
            raise ApiError(400, "INVALID_INGEST_FILE", str(exc)) from exc
        epoch_id = _epoch_id_for(meta["release"], meta["version"], meta["obsid"], meta["detector"])
        epoch = {
            "epoch_id": epoch_id,
            "obsid": meta["obsid"],
            "detector": int(meta["detector"]),
            "release": meta["release"],
            "version": meta["version"],
            "week": meta["week"],
            "mjd": float(meta["mjd"]),
            "date_obs": meta["date_obs"],
            "ra": float(meta["ra"]),
            "dec": float(meta["dec"]),
            "frame_id": None,
            "footprint": meta["footprint"],
            "source": "ingest",
            "source_count": 0,
            "ingest_kind": meta["file_kind"],
            "input_path": str(path),
            "file_sha256": sha,
            "image_shape": meta.get("image_shape"),
        }
        content_sha = _canonical_sha({"file_sha256": sha, "epoch": epoch})
        epoch["content_sha256"] = content_sha
        return {
            "file": {"path": str(path), "kind": meta["file_kind"], "sha256": sha},
            "epoch": epoch,
            "measurements": [],
            "content_sha256": content_sha,
        }

    @staticmethod
    def _source_measurements(epoch: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for src in sources:
            idx = int(src["source_index"])
            out.append(
                {
                    "measurement_id": _canonical_sha(
                        {"epoch_id": epoch["epoch_id"], "source_index": idx, "source_origin": "ingested"}
                    ),
                    "target_key": f"source:{idx}",
                    "mjd": epoch["mjd"],
                    "ra": float(src["ra"]),
                    "dec": float(src["dec"]),
                    "flux": src.get("flux"),
                    "flux_err": src.get("flux_err"),
                    "snr": src.get("snr"),
                    "source_index": idx,
                    "x": src.get("x"),
                    "y": src.get("y"),
                    "saturated": bool(src.get("saturated")),
                    "quality": list(src.get("quality") or []),
                    "source_origin": "ingested",
                }
            )
        return out

    # ------------------------------------------------------------------ sources API
    def resolve_sources(
        self,
        ra: Any,
        dec: Any,
        fov: Any = 0.2,
        epoch: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        qra, qdec, qfov = _validate_query(ra, dec, fov)
        try:
            limit_i = max(1, min(int(limit), 5000))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "INVALID_QUERY", "limit 必须是整数") from exc
        sources = self.sidecar.sources(
            ra=qra,
            dec=qdec,
            radius_deg=qfov / 2.0,
            epoch=epoch or None,
            limit=limit_i,
            origin="ingested",
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "service_version": SERVICE_VERSION,
            "index_snapshot": self.index_snapshot(),
            "index_completeness": INDEX_COMPLETENESS,
            "query": {
                "ra": round(qra, 6),
                "dec": round(qdec, 6),
                "fov": qfov,
                "radius_deg": round(qfov / 2.0, 6),
                "epoch": epoch,
                "limit": limit_i,
            },
            "source_origin": "ingested",
            "provenance": "real",
            "count": len(sources),
            "sources": sources,
            "sources_truncated": len(sources) >= limit_i,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _attach_sources(self, body: dict[str, Any], ra: float, dec: float, fov: float) -> dict[str, Any]:
        sources = self.sidecar.sources(
            ra=ra,
            dec=dec,
            radius_deg=fov / 2.0,
            limit=MAX_FIELD_SOURCES,
            origin="ingested",
        )
        body["sources"] = sources
        body["source_count"] = len(sources)
        body["source_origin"] = "ingested"
        body["sources_truncated"] = len(sources) >= MAX_FIELD_SOURCES
        body["sources_url"] = (
            "/api/v1/sources?"
            + urllib.parse.urlencode(
                {"ra": f"{ra:.6f}", "dec": f"{dec:.6f}", "fov": f"{fov:g}", "limit": MAX_FIELD_SOURCES}
            )
        )
        body.setdefault("coverage", {})["source_count"] = len(sources)
        epoch_ids = [str(e["epoch_id"]) for e in body.get("epochs", []) if e.get("epoch_id")]
        counts = self.sidecar.source_counts_for_epochs(epoch_ids)
        for epoch in body.get("epochs", []):
            eid = str(epoch.get("epoch_id") or "")
            epoch["source_count"] = int(counts.get(eid, epoch.get("source_count") or 0))
        return body

    # ------------------------------------------------------------------ ingest
    def validate_manifest(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_MANIFEST", "manifest 必须是 JSON 对象")
        _reject_network_strings(payload)
        source = payload.get("source")
        if source is not None:
            if not isinstance(source, dict):
                raise ApiError(400, "INVALID_MANIFEST", "source 必须是对象")
            if str(source.get("kind") or "") != "local_manifest":
                raise ApiError(400, "NETWORK_SOURCE_NOT_ALLOWED", "P0 只接受 source.kind=local_manifest")
        epoch_raw = payload.get("epoch")
        if not isinstance(epoch_raw, dict):
            raise ApiError(400, "INVALID_MANIFEST", "缺 epoch 对象")
        obsid = str(epoch_raw.get("obsid") or "").strip()
        detector = _as_int(epoch_raw.get("detector"))
        release = str(epoch_raw.get("release") or "").strip()
        version = str(epoch_raw.get("version") or "").strip()
        mjd = _as_float(epoch_raw.get("mjd"))
        ra = _as_float(epoch_raw.get("ra"))
        dec = _as_float(epoch_raw.get("dec"))
        if not obsid or detector is None or detector < 1 or detector > 6:
            raise ApiError(400, "INVALID_MANIFEST", "epoch.obsid/detector 非法")
        if not release or not version:
            raise ApiError(400, "INVALID_MANIFEST", "epoch.release/version 不能为空")
        if mjd is None or ra is None or dec is None or not (-90 <= dec <= 90):
            raise ApiError(400, "INVALID_MANIFEST", "epoch.mjd/ra/dec 非法")
        week = str(epoch_raw.get("week") or "").strip()
        if not week:
            parts = obsid.rsplit("_", 2)
            if len(parts) != 3:
                raise ApiError(400, "INVALID_MANIFEST", "无法从 epoch.obsid 推断 week")
            week = parts[0]
        epoch_id = _epoch_id_for(release, version, obsid, int(detector))
        declared_id = epoch_raw.get("epoch_id")
        if declared_id is not None and str(declared_id) != epoch_id:
            raise ApiError(400, "INVALID_MANIFEST", "epoch.epoch_id 与派生 id 不一致")
        footprint = _validate_footprint(epoch_raw.get("footprint"), ra % 360.0, dec)
        frame_id = _as_int(epoch_raw.get("frame_id"))
        measurements_raw = payload.get("measurements") or []
        if not isinstance(measurements_raw, list):
            raise ApiError(400, "INVALID_MANIFEST", "measurements 必须是列表")
        measurements = []
        seen_targets: set[str] = set()
        for idx, raw in enumerate(measurements_raw):
            m = _validate_measurement(raw, idx, epoch_id, float(mjd))
            if m["target_key"] in seen_targets:
                raise ApiError(400, "INVALID_MANIFEST", f"measurements 重复 target_key={m['target_key']}")
            seen_targets.add(m["target_key"])
            measurements.append(m)
        epoch = {
            "epoch_id": epoch_id,
            "obsid": obsid,
            "detector": int(detector),
            "release": release,
            "version": version,
            "week": week,
            "mjd": float(mjd),
            "ra": float(ra) % 360.0,
            "dec": float(dec),
            "frame_id": frame_id,
            "footprint": footprint,
            "source": "ingest",
        }
        content_sha = _canonical_sha({"epoch": epoch, "measurements": measurements})
        epoch["content_sha256"] = content_sha
        return {"epoch": epoch, "measurements": measurements, "content_sha256": content_sha}

    def submit_ingest(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, str):
            payload = {"input": {"path": payload}}
        if self._is_file_manifest(payload):
            normalized = self.validate_file_manifest(payload)
        else:
            normalized = self.validate_manifest(payload)
        epoch = normalized["epoch"]
        epoch_id = epoch["epoch_id"]
        with self._ingest_lock:
            existing = self.sidecar.epoch_by_id(epoch_id)
            if existing is not None:
                if int(existing.get("visible") or 0) == 1 and existing.get("content_sha256") == normalized["content_sha256"]:
                    return self._replay_job(epoch, normalized)
                if int(existing.get("visible") or 0) == 1:
                    raise ApiError(409, "INGEST_CONFLICT", "同一 epoch_id 已存在不同内容")
                raise ApiError(409, "EPOCH_RETRACTED", "同一 epoch_id 已存在但已软回滚；P0 不允许直接复用")
            active = self.sidecar.active_job_for_epoch(epoch_id)
            if active is not None:
                return self._job_dto(active, replayed=True)
            job_id = "ing_" + uuid.uuid4().hex[:16]
            request_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            file_info = normalized.get("file") or {}
            self.sidecar.create_job(
                job_id,
                epoch_id,
                normalized["content_sha256"],
                request_json,
                state="queued",
                input_kind=str(file_info.get("kind") or "manifest"),
                input_path=file_info.get("path") or epoch.get("input_path"),
            )
            thread = threading.Thread(
                target=self._process_ingest,
                args=(job_id, normalized),
                name=f"anyfield-ingest-{job_id}",
                daemon=True,
            )
            thread.start()
            return {
                "schema_version": SCHEMA_VERSION,
                "job_id": job_id,
                "epoch_id": epoch_id,
                "state": "queued",
                "replayed": False,
                "poll": f"/api/v1/ingest/{job_id}",
            }

    def _replay_job(self, epoch: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        job_id = "ing_" + uuid.uuid4().hex[:16]
        file_info = normalized.get("file") or {}
        self.sidecar.create_job(
            job_id,
            epoch["epoch_id"],
            normalized["content_sha256"],
            json.dumps({"replayed": True}, separators=(",", ":")),
            state="committed",
            replayed=True,
            input_kind=str(file_info.get("kind") or "manifest"),
            input_path=file_info.get("path") or epoch.get("input_path"),
        )
        row = self.sidecar.get_job(job_id) or {}
        dto = self._job_dto(row, replayed=True)
        dto["measurements_added"] = 0
        return dto

    def _process_ingest(self, job_id: str, normalized: dict[str, Any]) -> None:
        self.sidecar.mark_running(job_id)
        try:
            if normalized.get("file"):
                file_info = normalized["file"]
                epoch, sources = pixel_ingest.detect_file_sources(
                    file_info["path"], file_info["sha256"], normalized["epoch"]
                )
                measurements = self._source_measurements(epoch, sources)
                epoch["source_count"] = len(sources)
                epoch["ingest_kind"] = file_info["kind"]
                epoch["input_path"] = file_info["path"]
                epoch["file_sha256"] = file_info["sha256"]
                normalized = dict(normalized)
                normalized["epoch"] = epoch
                normalized["measurements"] = measurements
            self.sidecar.publish_job(job_id, normalized["epoch"], normalized["measurements"])
        except pixel_ingest.InvalidPixelFile as exc:
            self.sidecar.fail_job(job_id, "INVALID_INGEST_FILE", str(exc))
        except Exception as exc:  # noqa: BLE001 - job must be marked failed
            self.sidecar.fail_job(job_id, "INGEST_FAILED", str(exc))

    def _job_dto(self, row: dict[str, Any], *, replayed: bool | None = None) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "job_id": row.get("job_id"),
            "epoch_id": row.get("epoch_id"),
            "state": row.get("state"),
            "replayed": bool(row.get("replayed")) if replayed is None else bool(replayed),
            "measurements_added": int(row.get("measurements_added") or 0),
            "input_kind": row.get("input_kind"),
            "input_path": row.get("input_path"),
            "error": (
                {"code": row.get("error_code"), "message": row.get("error_message")}
                if row.get("error_code")
                else None
            ),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }

    def job_status(self, job_id: str) -> dict[str, Any]:
        row = self.sidecar.get_job(job_id)
        if row is None:
            raise ApiError(404, "JOB_NOT_FOUND", f"未知 job_id：{job_id}")
        return self._job_dto(row)

    def rollback_job(self, job_id: str) -> dict[str, Any]:
        try:
            row = self.sidecar.rollback_job(job_id)
        except KeyError as exc:
            raise ApiError(404, "JOB_NOT_FOUND", f"未知 job_id：{job_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "JOB_NOT_COMMITTED", str(exc)) from exc
        return self._job_dto(row)


def error_payload(exc: ApiError, *, index_snapshot: str | None = None, request_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "service_version": SERVICE_VERSION,
        "coverage_mode": COVERAGE_MODE,
        "request_id": request_id,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
            "details": dict(exc.details),
        },
    }
    if index_snapshot is not None:
        body["index_snapshot"] = index_snapshot
    if exc.code == "NO_LOCAL_COVERAGE":
        body["index_completeness"] = "complete"
        body["coverage"] = {
            "state": "none",
            "coverage_mode": COVERAGE_MODE,
            "coverage_method": "complete_region",
            "footprint_method": "none",
            "index_completeness": "complete",
            "strong_assertion": True,
            "no_local_coverage": True,
            "sim_allowed": True,
        }
    elif exc.code in ("INDEX_INCOMPLETE", "DB_UNAVAILABLE"):
        body["index_completeness"] = INDEX_COMPLETENESS
        body["coverage"] = {
            "state": "unknown",
            "coverage_mode": COVERAGE_MODE,
            "coverage_method": "unknown",
            "footprint_method": "unknown",
            "index_completeness": INDEX_COMPLETENESS,
            "strong_assertion": False,
            "no_local_coverage": False,
        }
    return body
