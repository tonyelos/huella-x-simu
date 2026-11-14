"""数据适配层：真实历元、离线样本、模拟历元三种来源。

优先级（mode="auto"）：
  1. app/data/epoch_catalog.json 里有 ≥2 个真实历元 → real
  2. 只有一个真实历元 → hybrid（真实背景 + 注入模拟目标，用于演示算法）
  3. 没有真实历元 → synthetic
"""
from __future__ import annotations

import json
import math
import os
import re
import ssl
import urllib.request
import uuid
import zlib
from dataclasses import dataclass

import numpy as np

from . import epoch_source
from . import photometry as ph
from .fitsio import read_epsf, read_fits, read_wcs_wave
from .simulator import Epoch, default_times, generate_epochs, mjd_to_iso
from .wcs import WCS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "epochcache")
BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"


def _ssl_ctx():
    """打包的 Python 不带系统 CA；优先用 Homebrew 的 cert.pem，其次 /etc/ssl。"""
    for f in ("/opt/homebrew/etc/openssl@3/cert.pem", "/etc/ssl/cert.pem"):
        if os.path.exists(f):
            try:
                return ssl.create_default_context(cafile=f)
            except Exception:  # noqa: BLE001
                pass
    return ssl.create_default_context()


def load_targets() -> list[dict]:
    with open(os.path.join(DATA, "targets.json"), encoding="utf-8") as fh:
        return json.load(fh)["targets"]


def get_target(tid: str) -> dict:
    for t in load_targets():
        if t["id"] == tid:
            return t
    raise KeyError(tid)


def load_epoch_catalog() -> dict:
    path = os.path.join(DATA, "epoch_catalog.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("epochs", {})


def apparent_position(target: dict, mjd: float) -> tuple[float, float]:
    from .simulator import app_positions

    tr = target.get("truth", {}) or {}
    ra, dec = app_positions(
        target["ra"],
        target["dec"],
        tr.get("pmra_mas_yr") or 0.0,
        tr.get("pmdec_mas_yr") or 0.0,
        tr.get("parallax_mas") or 0.0,
        [mjd],
    )
    return float(ra[0]), float(dec[0])


def slice_url(ep: dict, ra: float, dec: float, size_deg: float) -> str:
    key = ep["key"]
    rel = ep["release"]
    relpath = key[len(rel) + 1 :] if key.startswith(rel + "/") else key
    return f"{BASE}{rel}/{relpath}?center={ra:.6f},{dec:.6f}d&size={size_deg}"


def _cache_path(ep: dict, ra: float, dec: float, size: float) -> str:
    """缓存键必须包含处理版本：不同 version 的像素不能复用。"""
    os.makedirs(CACHE, exist_ok=True)
    release = str(ep.get("release") or "unknown")
    version = str(ep.get("version") or "unknown")
    obsid = str(ep.get("obsid") or "unknown")
    det = str(ep.get("detector") or "?")
    raw = f"{release}_{version}_{obsid}_D{det}_{ra:.4f}_{dec:.4f}_{size:.3f}"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)[:180]
    return os.path.join(CACHE, safe + ".npz")


def _epoch_from_cache(path: str, ep: dict) -> Epoch | None:
    """读取缓存；任何损坏都删除并返回 None，交由上层重取。"""
    try:
        with np.load(path, allow_pickle=False) as d:
            header = json.loads(str(d["header"]))
            psf = np.asarray(d["psf"], float) if "psf" in d else None
            if psf is not None and (psf.ndim != 2 or psf.size < 9):
                psf = None
            return Epoch(
                id=f"real-{ep['obsid']}-D{ep['detector']}",
                mjd=float(d["mjd"]),
                image=np.asarray(d["image"], float),
                variance=np.asarray(d["variance"], float),
                flags=np.asarray(d["flags"], np.int32),
                wcs=WCS.from_header(header),
                psf=psf,
                provenance="real",
                band=f"D{ep['detector']}",
                obsid=str(d["obsid"]),
                date_obs=str(d["date_obs"]),
                source="IRSA static cutout",
                meta={
                    "release": str(d["release"]),
                    "sep_deg": float(d["sep_deg"]),
                    "wave": float(d["wave"]) if "wave" in d else None,
                },
            )
    except Exception as exc:  # noqa: BLE001 - 坏缓存不允许永久 500
        print(f"[cache] corrupt {os.path.basename(path)}: {exc}; removing")
        try:
            os.remove(path)
        except OSError:
            pass
        return None


def _save_cache(path: str, **arrays) -> None:
    """原子写入：先写临时文件再 os.replace，失败清理临时文件。"""
    tmp = f"{path}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}.npz"
    try:
        np.savez_compressed(tmp, **arrays)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _download(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MoveScope/0.1"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
        return r.read()


def fetch_epoch(ep: dict, ra: float, dec: float, size_deg: float = 0.2, timeout: int = 90) -> Epoch | None:
    path = _cache_path(ep, ra, dec, size_deg)
    if os.path.exists(path):
        cached = _epoch_from_cache(path, ep)
        if cached is not None:
            return cached
    url = slice_url(ep, ra, dec, size_deg)
    blob = None
    for attempt in range(3):
        try:
            blob = _download(url, timeout)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                print(f"[fetch] {ep['obsid']} D{ep['detector']} failed after 3 tries: {exc}")
                return None
            import time as _t
            _t.sleep(1.5 * (attempt + 1))
    if blob is None:
        return None
    try:
        fits = read_fits(blob)
        img = np.asarray(fits["IMAGE"].data, float)
        var = np.asarray(fits["VARIANCE"].data, float)
        flags = np.asarray(fits["FLAGS"].data, np.int32)
        header = dict(fits["IMAGE"].header)
        epsf = read_epsf(fits["EPSF"])
        wgrid = read_wcs_wave(fits["WCS-WAVE"])
        # 目标附近的 PSF（全幅坐标 = 切片坐标 + 偏移）
        offx = 1020.5 - float(header.get("CRPIX1", 1020.5))
        offy = 1020.5 - float(header.get("CRPIX2", 1020.5))
        cy, cx = np.array(img.shape) / 2
        psf = ph.select_psf(epsf, cx + offx, cy + offy, None) if epsf else None
        wave = None
        if wgrid:
            try:
                wave = float(
                    np.nanmean(
                        __import__("core.wcs", fromlist=["wavelength_at_pixel"]).wavelength_at_pixel(
                            wgrid, np.array([cx + offx]), np.array([cy + offy])
                        )
                    )
                )
            except Exception:  # noqa: BLE001
                wave = None
        _save_cache(
            path,
            image=img.astype(np.float32),
            variance=var.astype(np.float32),
            flags=flags,
            header=json.dumps({k: v for k, v in header.items() if isinstance(v, (int, float, str, bool))}),
            psf=(psf if psf is not None else np.zeros((1, 1))).astype(np.float64),
            mjd=float(header.get("MJD-OBS", ep["mjd"])),
            obsid=str(header.get("OBSID", ep["obsid"])),
            detector=str(header.get("DETECTOR", ep["detector"])),
            date_obs=str(header.get("DATE-OBS", ep.get("date_obs", ""))),
            release=ep["release"],
            sep_deg=float(ep.get("sep_deg", 0.0)),
            wave=wave if wave is not None else -1.0,
        )
        return _epoch_from_cache(path, ep)
    except Exception as exc:  # noqa: BLE001
        print(f"[parse] {ep['obsid']} failed: {exc}")
        return None


def dedupe_epochs(eps: list[dict], tol_days: float = 0.02) -> list[dict]:
    """同一曝光被多个探测器重复记录时，按时序去重，保留角距最小的那个。"""
    eps = sorted(eps, key=lambda e: (e.get("mjd") or 0, e.get("sep_deg") or 9))
    out: list[dict] = []
    for e in eps:
        if not out or abs((e.get("mjd") or 0) - (out[-1].get("mjd") or 0)) > tol_days:
            out.append(e)
        else:
            if (e.get("sep_deg") or 9) < (out[-1].get("sep_deg") or 9):
                out[-1] = e
    return out


def load_sample_epochs() -> list[Epoch]:
    """两份离线真实切片（各 1 期）。"""
    root = os.path.abspath(os.path.join(ROOT, ".."))
    files = [
        os.path.join(root, "参考 资料", "muestras", "M101_D2_2026W25_qr3_cutout.fits"),
        os.path.join(root, "参考 资料", "muestras", "campoA_D6_2025W48_dr1_cutout.fits"),
    ]
    out = []
    for p in files:
        if not os.path.exists(p):
            continue
        fits = read_fits(p)
        h = fits["IMAGE"].header
        epsf = read_epsf(fits["EPSF"])
        psf = None
        if epsf:
            cy, cx = np.array(fits["IMAGE"].data.shape) / 2
            psf = ph.select_psf(epsf, cx, cy, None)
        out.append(
            Epoch(
                id=os.path.basename(p).replace(".fits", ""),
                mjd=float(h.get("MJD-OBS", 0.0)),
                image=np.asarray(fits["IMAGE"].data, float),
                variance=np.asarray(fits["VARIANCE"].data, float),
                flags=np.asarray(fits["FLAGS"].data, np.int32),
                wcs=WCS.from_header(h),
                psf=psf,
                provenance="real",
                band="D%s" % h.get("DETECTOR", "?"),
                obsid=str(h.get("OBSID", "")),
                date_obs=str(h.get("DATE-OBS", "")),
                source="bundled sample",
                meta={"file": p},
            )
        )
    return out


def build_dataset(
    tid: str,
    mode: str = "auto",
    n_sim: int = 8,
    max_real: int = 8,
    size_deg: float = 0.2,
    *,
    ra: float | None = None,
    dec: float | None = None,
):
    """构建一次分析的数据集。

    预设目标（``tid`` 是 7 个目录目标之一）继续走本地 ``epoch_catalog.json``，
    保证 5 目标不回归。``tid="custom"`` 走 T2 的解析/取数/降级分支：

      解析（DB v1 / 旧端点 / 本地匹配）→ 真实切片取数 → real / hybrid / unavailable / synthetic

    返回值仍然是 ``(target, epochs, truth, meta)``；``meta`` 新增
    ``fallback_reason / real_epochs / source / matched_target_id / coverage``。
    """
    if tid == "custom":
        return _build_custom_dataset(
            ra=ra,
            dec=dec,
            mode=mode,
            n_sim=n_sim,
            max_real=max_real,
            size_deg=size_deg,
        )

    target = get_target(tid)
    if mode == "sample":
        eps = load_sample_epochs()
        return target, eps, None, {
            "mode": "sample",
            "note": "两份离线真实切片（不同天区，仅用于解析/渲染核验）",
            "fallback_reason": "SAMPLE_MODE",
            "real_epochs": len(eps),
            "source": "local_json",
        }

    cat = load_epoch_catalog().get(tid, [])
    eps = dedupe_epochs(cat) if cat else []
    catalog_epochs = len(eps)
    eps = _sample_epochs(eps, max_real)
    wanted_real = mode in ("auto", "real")
    real_days = (max(e["mjd"] for e in eps) - min(e["mjd"] for e in eps)) if len(eps) >= 2 else 0.0
    coverage = epoch_source.coverage_for_epochs(eps)
    meta = {
        "mode": "synthetic",
        "note": "",
        "fallback_reason": None,
        "real_epochs": 0,
        "catalog_epochs": catalog_epochs,
        "fetch_attempted": 0,
        "fetch_succeeded": 0,
        "coverage": coverage,
        "catalog_baseline_days": real_days,
        "source": "local_json",
        "matched_target_id": None,
        "warnings": [],
    }

    if wanted_real and len(eps) >= 2 and real_days >= 30:
        from concurrent.futures import ThreadPoolExecutor

        def _one(ep):
            ra_ep, dec_ep = apparent_position(target, ep["mjd"])
            return fetch_epoch(ep, ra_ep, dec_ep, size_deg)

        with ThreadPoolExecutor(max_workers=4) as ex:
            fetched = [e for e in ex.map(_one, eps) if e is not None]
        success = len(fetched)
        meta["fetch_attempted"] = len(eps)
        meta["fetch_succeeded"] = success

        if success >= 2:
            span = max(e.mjd for e in fetched) - min(e.mjd for e in fetched)
            if span >= 30:
                note = (
                    f"IRSA 静态切片取数 {success}/{len(eps)} 期；参考星相对定标。"
                )
                if success < len(eps):
                    note += f" 另有 {len(eps) - success} 期取数失败。"
                meta.update({"mode": "real", "fallback_reason": None, "real_epochs": success, "note": note})
                return target, fetched, None, meta

            sim_epochs, truth = _simulate_with_complement(target, eps, fetched, n_sim, size_deg, tid)
            note = (
                f"真实切片取数 {success}/{len(eps)} 期，取数跨度仅 {span:.0f} 天（目录基线 {real_days:.0f} 天），"
                f"不足以判定运动；以下为验证时序（HYBRID：{success} 期 REAL + {max(len(sim_epochs) - success, 0)} 期 SIM）。"
            )
            meta.update({"mode": "hybrid", "fallback_reason": "BASELINE_TOO_SHORT", "real_epochs": success, "note": note})
            return target, sim_epochs, truth, meta

        if success == 1:
            sim_epochs, truth = _simulate_with_complement(target, eps, fetched, n_sim, size_deg, tid)
            note = (
                f"目录有 {len(eps)} 期真实历元，切片只成功取回 1 期（{fetched[0].mjd:.3f}），无法测运动；"
                f"以下为验证时序（HYBRID：1 期 REAL + {max(len(sim_epochs) - 1, 0)} 期 SIM）。"
            )
            meta.update({"mode": "hybrid", "fallback_reason": "INSUFFICIENT_EPOCHS", "real_epochs": 1, "note": note})
            return target, sim_epochs, truth, meta

        # 一条都没取回：不要把失败写成“基线不足”，也不要用 SIM 冒充 real
        sim_epochs, _sim_truth = _simulate_fallback(target, tid, n_sim, size_deg, start_mjd=float(eps[0]["mjd"]))
        note = (
            f"目录有 {len(eps)} 期真实历元，但切片取数全部失败（0/{len(eps)}，可能网络或 IRSA 不可用）；"
            "未做真实判定，以下画面仅为 SIM 演示，请恢复网络后重试。"
        )
        meta.update({"mode": "unavailable", "fallback_reason": "FETCH_FAILED", "real_epochs": 0, "note": note})
        return target, sim_epochs, None, meta

    if not eps:
        sim_epochs, truth = _simulate_fallback(target, tid, n_sim, size_deg)
        note = "该天区没有匹配到真实历元（NO_LOCAL_COVERAGE）；以下为基于真实 SPHEREx PSF / 噪声 / 背景纹理生成的验证时序（SIM）。"
        meta.update({"mode": "synthetic", "fallback_reason": "NO_LOCAL_COVERAGE", "note": note})
        return target, sim_epochs, truth, meta

    if not wanted_real:
        # 用户主动选 mode=synthetic：显式 SIM，fallback_reason 可以为 null
        sim_epochs, truth = _simulate_fallback(target, tid, n_sim, size_deg, start_mjd=float(eps[0]["mjd"]))
        note = "用户主动选择 SIM 模式；以下为基于真实 SPHEREx PSF / 噪声 / 背景纹理生成的验证时序（不是真实观测）。"
        meta.update({"mode": "synthetic", "fallback_reason": None, "note": note})
        return target, sim_epochs, truth, meta

    # 有目录历元但不够 real：1 期 → INSUFFICIENT_EPOCHS；≥2 期但 <30 天 → BASELINE_TOO_SHORT
    sim_epochs, truth = _simulate_fallback(target, tid, n_sim, size_deg, start_mjd=float(eps[0]["mjd"]))
    if len(eps) < 2:
        note = (
            f"只找到 1 期真实观测（{eps[0]['week']}），无法测运动；"
            "以下为验证时序（HYBRID，真实 PSF/噪声 + 注入目标）。"
        )
        meta.update({"mode": "hybrid", "fallback_reason": "INSUFFICIENT_EPOCHS", "note": note})
    else:
        note = (
            f"真实历元 {len(eps)} 期，目录基线仅 {real_days:.0f} 天，不足以判定运动规律；"
            "以下为验证时序（HYBRID，真实 PSF/噪声 + 注入目标）。"
        )
        meta.update({"mode": "hybrid", "fallback_reason": "BASELINE_TOO_SHORT", "note": note})
    return target, sim_epochs, truth, meta


# ------------------------------------------------------------------ 自定义坐标（T2）


def _custom_target(ra: float, dec: float, matched: dict | None = None) -> dict:
    """用户输入坐标始终作为查询中心；命中官方目标时只借用 truth / coord_unc。"""
    target = {
        "id": "custom",
        "name": f"RA {ra:.4f}  Dec {dec:.4f}",
        "name_zh": "自定义天区",
        "ra": float(ra),
        "dec": float(dec),
        "type": "unknown",
        "expected": "U",
        "truth": {},
        "coord_unc_arcsec": 0.0,
    }
    if matched:
        target["truth"] = dict(matched.get("truth") or {})
        target["coord_unc_arcsec"] = float(matched.get("coord_unc_arcsec") or 0.0)
        target["matched_target_id"] = matched.get("id")
        target["matched_name"] = matched.get("name")
    return target


def _sim_target(target: dict, matched: dict | None, *, with_truth: bool) -> dict:
    """给模拟器用的目标副本：命中目标时复用其 base_flux/seed，但不一定注入真值。"""
    sim = dict(target)
    if matched:
        sim["id"] = matched.get("id") or "custom"
        sim["truth"] = dict(matched.get("truth") or {}) if with_truth else {}
    return sim


def _sample_epochs(eps: list[dict], max_real: int) -> list[dict]:
    if max_real <= 0 or len(eps) <= max_real:
        return list(eps)
    # 均匀抽样，保留完整时间基线（不能只取最早的 N 期）
    idx = np.linspace(0, len(eps) - 1, max_real).round().astype(int)
    return [eps[i] for i in sorted(set(idx.tolist()))]


def _simulate_fallback(
    target: dict,
    seed_key: str,
    n_sim: int,
    size_deg: float,
    *,
    start_mjd: float = 60930.0,
    span_days: float = 205.0,
):
    times = default_times(n_sim, start_mjd=float(start_mjd), span_days=float(span_days))
    seed = zlib.crc32(str(seed_key).encode()) % 10000
    return generate_epochs(target, times, fov_deg=size_deg, seed=seed)


def _simulate_with_complement(
    target: dict,
    catalog_eps: list[dict],
    real_epochs,
    n_sim: int,
    size_deg: float,
    seed_key: str,
):
    """生成 n_sim 期总数：真实期 + 补足模拟期，按 MJD 排序。"""
    n_real = len(real_epochs)
    if n_real >= n_sim:
        return sorted(real_epochs, key=lambda e: float(e.mjd)), {}

    n_need = n_sim - n_real
    start = float(catalog_eps[0]["mjd"]) if catalog_eps else min(float(e.mjd) for e in real_epochs)
    real_mjds = [float(e.mjd) for e in real_epochs]
    candidates = [float(t) for t in default_times(n_sim, start_mjd=start, span_days=205.0)]
    chosen = [t for t in candidates if all(abs(t - m) > 1.0 for m in real_mjds)][:n_need]
    if len(chosen) < n_need:
        # 真实期较多时上面的网格不够：在真实期之后补一条均匀网格
        extra_start = max([start] + real_mjds) + 7.0
        for t in np.linspace(extra_start, extra_start + 200.0, n_need * 2 + 2):
            t = float(t)
            if len(chosen) >= n_need:
                break
            if all(abs(t - m) > 1.0 for m in real_mjds) and all(abs(t - c) > 0.5 for c in chosen):
                chosen.append(t)

    seed = zlib.crc32(str(seed_key).encode()) % 10000
    sim_epochs, truth = generate_epochs(target, np.asarray(chosen, float), fov_deg=size_deg, seed=seed)
    combined = sorted(list(sim_epochs) + list(real_epochs), key=lambda e: (float(e.mjd), str(getattr(e, "provenance", ""))))
    return combined, truth


def _build_custom_dataset(
    *,
    ra: float | None,
    dec: float | None,
    mode: str,
    n_sim: int,
    max_real: int,
    size_deg: float,
):
    """任意坐标：先解析真实历元，再按“成功取回的期数”决定 real/hybrid/unavailable/synthetic。"""
    if ra is None or dec is None:
        raise ValueError("自定义坐标需要同时提供 ra 和 dec")
    ra, dec = float(ra), float(dec)
    if not (math.isfinite(ra) and math.isfinite(dec)):
        raise ValueError("ra/dec 必须是有限数字")
    seed_key = f"custom:{ra:.6f},{dec:.6f}"
    target = _custom_target(ra, dec)
    meta = {
        "mode": "synthetic",
        "note": "",
        "fallback_reason": None,
        "real_epochs": 0,
        "catalog_epochs": 0,
        "fetch_attempted": 0,
        "fetch_succeeded": 0,
        "coverage": epoch_source.coverage_for_epochs([]),
        "catalog_baseline_days": 0.0,
        "source": "none",
        "matched_target_id": None,
        "matched_name": None,
        "warnings": [],
    }

    if mode == "synthetic":
        sim_epochs, truth = _simulate_fallback(target, seed_key, n_sim, size_deg)
        meta["note"] = "自定义坐标：用户主动选择 SIM 模式；以下为模拟时序，不是真实观测（fallback_reason=null 是允许的）。"
        meta["source"] = "none"
        return target, sim_epochs, truth, meta

    resolution = epoch_source.resolve_epochs(ra, dec, size_deg)
    matched = epoch_source.get_known_target(resolution.matched_target_id) if resolution.matched_target_id else None
    target = _custom_target(ra, dec, matched)
    cat_mjds = [float(e["mjd"]) for e in resolution.epochs if e.get("mjd") is not None]
    cat_baseline = (max(cat_mjds) - min(cat_mjds)) if len(cat_mjds) >= 2 else 0.0
    meta.update(
        {
            "catalog_baseline_days": cat_baseline,
            "fallback_reason": resolution.fallback_reason or "NO_LOCAL_COVERAGE",
            "coverage": resolution.coverage or epoch_source.coverage_for_epochs([]),
            "source": resolution.source,
            "matched_target_id": resolution.matched_target_id,
            "matched_name": matched.get("name") if matched else None,
            "warnings": list(resolution.warnings),
            "catalog_epochs": len(resolution.epochs),
        }
    )

    if resolution.epochs:
        eps = _sample_epochs(resolution.epochs, max_real)
        meta["fetch_attempted"] = len(eps)

        def _one(ep):
            try:
                if matched:
                    center_ra, center_dec = apparent_position(target, ep["mjd"])
                else:
                    center_ra, center_dec = ra, dec
                return fetch_epoch(ep, center_ra, center_dec, size_deg)
            except Exception as exc:  # noqa: BLE001 - 单期异常不拖垮整次分析
                print(f"[fetch] custom {ep.get('obsid')} D{ep.get('detector')} failed: {exc}")
                return None

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as ex:
            fetched = [e for e in ex.map(_one, eps) if e is not None]
        success = len(fetched)
        meta["fetch_succeeded"] = success
        match_prefix = f"0.05° 内匹配到 {matched.get('name')}（官方目标）；" if matched else ""

        if success >= 2:
            span = max(e.mjd for e in fetched) - min(e.mjd for e in fetched)
            if span >= 30:
                note = (
                    f"{match_prefix}数据库返回 {len(resolution.epochs)} 期真实历元（取 {len(eps)} 期），"
                    f"IRSA 静态切片取数 {success}/{len(eps)} 期；参考星相对定标。"
                )
                if success < len(eps):
                    note += f" 另有 {len(eps) - success} 期取数失败。"
                meta.update({"mode": "real", "fallback_reason": None, "real_epochs": success, "note": note})
                return target, fetched, None, meta

            sim_target = _sim_target(target, matched, with_truth=True)
            sim_epochs, truth = _simulate_with_complement(
                sim_target, eps, fetched, n_sim, size_deg, matched.get("id") if matched else seed_key
            )
            note = (
                f"{match_prefix}真实历元取数 {success} 期，取数跨度仅 {span:.0f} 天，不足以判定运动；"
                f"以下为验证时序（HYBRID：{success} 期 REAL + {max(len(sim_epochs) - success, 0)} 期 SIM）。"
            )
            meta.update({"mode": "hybrid", "fallback_reason": "BASELINE_TOO_SHORT", "real_epochs": success, "note": note})
            return target, sim_epochs, truth, meta

        if success == 1:
            sim_target = _sim_target(target, matched, with_truth=True)
            sim_epochs, truth = _simulate_with_complement(
                sim_target, eps, fetched, n_sim, size_deg, matched.get("id") if matched else seed_key
            )
            note = (
                f"{match_prefix}目录/数据库有 {len(resolution.epochs)} 期真实历元，"
                f"切片只成功取回 1 期（{fetched[0].mjd:.3f}），不足 2 期；"
                f"以下为验证时序（HYBRID：1 期 REAL + {max(len(sim_epochs) - 1, 0)} 期 SIM）。"
            )
            meta.update({"mode": "hybrid", "fallback_reason": "INSUFFICIENT_EPOCHS", "real_epochs": 1, "note": note})
            return target, sim_epochs, truth, meta

        # 有真实历元但一条都取不回：UNAVAILABLE + FETCH_FAILED，不静默用 SIM 判定
        sim_target = _sim_target(target, matched, with_truth=False)
        sim_epochs, _sim_truth = _simulate_fallback(sim_target, seed_key, n_sim, size_deg)
        note = (
            f"{match_prefix}数据库返回 {len(resolution.epochs)} 期真实历元，但切片取数全部失败"
            f"（0/{len(eps)}，可能网络或 IRSA 不可用）；未做真实判定，以下画面仅为 SIM 演示，请恢复网络后重试。"
        )
        meta.update({"mode": "unavailable", "fallback_reason": "FETCH_FAILED", "real_epochs": 0, "note": note})
        return target, sim_epochs, None, meta

    # 解析失败 / 无覆盖：显式 SIM。DB 不可用时仍然保留 matched_target_id，但不用真值冒充真实数据。
    reason = resolution.fallback_reason or "NO_LOCAL_COVERAGE"
    sim_target = _sim_target(target, matched, with_truth=False)
    sim_epochs, truth = _simulate_fallback(sim_target, seed_key, n_sim, size_deg)
    if reason == "DB_UNAVAILABLE":
        note = "数据库不可用（DB_UNAVAILABLE）：没有取到真实历元；以下为 SIM 演示，不是真实观测。"
        if matched:
            note += f" 坐标 0.05° 内匹配到 {matched.get('name')}，但未使用真实数据。"
    else:
        note = "该天区没有匹配到真实历元（NO_LOCAL_COVERAGE）；以下为 SIM 演示，不是真实观测。"
    meta.update({"mode": "synthetic", "fallback_reason": reason, "note": note, "real_epochs": 0})
    return sim_target, sim_epochs, truth, meta
