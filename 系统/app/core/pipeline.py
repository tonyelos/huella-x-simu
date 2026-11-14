"""从多历元图像到运动指纹的完整流水线。

阶段（对应 docs/01_理论设计编排.md §6）：
  1. 预处理：背景、掩膜、噪声
  2. 探测：匹配滤波 + 局部极大
  3. 测量：PSF 质心 / 饱和源质心 + 孔径测光
  4. 相对定标：用同视场参考星拟合仿射改正（消除 WCS 抖动）
  5. 关联：按预测位置选目标、构造轨迹
  6. 推断：motion.analyze_track
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import photometry as ph
from .motion import Track, analyze_track, quality_flags


def merge_quality(*groups) -> list[str]:
    """合并多来源质量标记，去重且保序（后加的不会被覆盖）。"""
    out: list[str] = []
    for group in groups:
        for item in group or []:
            if item and item not in out:
                out.append(item)
    return out
from .simulator import Epoch
from .wcs import WCS, ARCSEC


@dataclass
class Source:
    x: float  # 0-based
    y: float
    flux: float
    flux_err: float
    snr: float
    sigma_x: float
    sigma_y: float
    saturated: bool = False
    method: str = "psf"

    def json(self):
        return {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "flux": float(self.flux),
            "flux_err": float(self.flux_err),
            "snr": round(float(self.snr), 2),
            "sigma_x": round(float(self.sigma_x), 3),
            "sigma_y": round(float(self.sigma_y), 3),
            "saturated": bool(self.saturated),
            "method": self.method,
        }


# ------------------------------------------------------------------ 单历元


def epoch_products(epoch: Epoch):
    img = np.asarray(epoch.image, float)
    var = np.asarray(epoch.variance, float) if epoch.variance is not None else np.ones_like(img) * 1e-4
    flags = epoch.flags
    bad, soft = ph.build_mask(flags, img.shape)
    bad = bad | ~np.isfinite(img)
    med, std = ph.sigma_clipped_median_std(img, bad)
    noise = np.sqrt(np.maximum(var, 0) + (0.02 * np.abs(img)) ** 2)
    floor = max(0.5 * std, 1e-6)
    noise = np.maximum(noise, floor)
    return img, var, flags, bad, soft, med, std, noise


def detect_sources(epoch: Epoch, max_sources: int = 80, threshold: float = 6.0) -> list[Source]:
    img, var, flags, bad, soft, med, std, noise = epoch_products(epoch)
    snr = ph.matched_filter(img, noise, bad, sigma_px=0.7)
    peaks = ph.find_peaks(snr, threshold=threshold, min_sep=2, bad_mask=bad)
    out: list[Source] = []
    h, w = img.shape
    for (px, py, sn) in peaks:
        if px < 2 or py < 2 or px >= w - 2 or py >= h - 2:
            continue
        sat = ph.is_saturated(flags, px, py, 1) or bool(
            np.any(~np.isfinite(img[max(0, py - 1) : py + 2, max(0, px - 1) : px + 2]))
        )
        if sat:
            cen = ph.centroid_bloom(img, noise, bad, px, py, r=9)
        else:
            cen = ph.centroid_fast(img, noise, bad, px, py)
        if cen is None:
            continue
        phot = ph.aperture_flux(img, noise, bad, cen["x"], cen["y"], r_ap=4.0, r_in=6.0, r_out=9.0)
        f = phot["flux"]
        fe = phot["flux_err"]
        out.append(
            Source(
                x=float(cen["x"]),
                y=float(cen["y"]),
                flux=float(f),
                flux_err=float(fe),
                snr=float(sn if np.isfinite(fe) and fe > 0 else sn),
                sigma_x=float(cen.get("sigma_x", 0.3)),
                sigma_y=float(cen.get("sigma_y", 0.3)),
                saturated=bool(sat),
                method=cen.get("method", "fast"),
            )
        )
    out.sort(key=lambda s: -s.snr)
    return out[:max_sources]


# ------------------------------------------------------------------ 相对定标


def _affine_params(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    """求 6 参数仿射：dst = A src + t。"""
    n = len(src)
    if n < 3:
        return None
    M = np.zeros((2 * n, 6))
    b = np.zeros(2 * n)
    M[0::2, 0] = src[:, 0]
    M[0::2, 1] = src[:, 1]
    M[0::2, 2] = 1
    M[1::2, 3] = src[:, 0]
    M[1::2, 4] = src[:, 1]
    M[1::2, 5] = 1
    b[0::2] = dst[:, 0]
    b[1::2] = dst[:, 1]
    sol, *_ = np.linalg.lstsq(M, b, rcond=None)
    return sol


def _apply_affine(sol: np.ndarray, pts: np.ndarray) -> np.ndarray:
    out = np.empty_like(pts)
    out[:, 0] = sol[0] * pts[:, 0] + sol[1] * pts[:, 1] + sol[2]
    out[:, 1] = sol[3] * pts[:, 0] + sol[4] * pts[:, 1] + sol[5]
    return out


def match_sources(epochs: list[Epoch], catalogs: list[list[Source]], radius_px: float = 2.5,
                  exclude: list[tuple[float, float]] | None = None):
    """以第 0 期为参考，逐期用 WCS 投影 + 最近邻匹配 + 鲁棒仿射。

    exclude: 每个历元里需要排除的目标位置（0-based 像素）。目标本身在运动，
    绝不能当参考星，否则仿射改正会把它的运动一起扣掉。
    """
    ref = epochs[0]
    ref_cat = catalogs[0]
    if exclude is not None:
        ex = exclude[0]
        ref_cat = [s for s in ref_cat if math.hypot(s.x - ex[0], s.y - ex[1]) > 6.0]
    corrections = [np.array([1.0, 0, 0, 0, 1.0, 0])]
    stats = []
    ref_pts = np.array([[s.x, s.y] for s in ref_cat]) if ref_cat else np.zeros((0, 2))

    for i in range(1, len(epochs)):
        ep = epochs[i]
        cat = catalogs[i]
        if exclude is not None:
            ex = exclude[i]
            cat = [s for s in cat if math.hypot(s.x - ex[0], s.y - ex[1]) > 6.0]
        if len(cat) < 3 or len(ref_pts) < 3:
            corrections.append(None)
            stats.append({"epoch": i, "n_match": 0, "rms_px": None})
            continue
        # 把本期源投影到参考期的像素平面
        ra, dec = ep.wcs.pixel_to_world(np.array([s.x for s in cat]) + 1, np.array([s.y for s in cat]) + 1)
        xr, yr = ref.wcs.world_to_pixel(ra, dec)
        proj = np.stack([np.asarray(xr) - 1, np.asarray(yr) - 1], axis=1)
        src_pts = np.array([[s.x, s.y] for s in cat])

        pairs = []
        for j in range(len(cat)):
            d = np.hypot(ref_pts[:, 0] - proj[j, 0], ref_pts[:, 1] - proj[j, 1])
            k = int(np.argmin(d))
            if d[k] < radius_px:
                pairs.append((src_pts[j], ref_pts[k]))
        if len(pairs) < 3:
            corrections.append(None)
            stats.append({"epoch": i, "n_match": len(pairs), "rms_px": None})
            continue
        src = np.array([p[0] for p in pairs])
        dst = np.array([p[1] for p in pairs])
        sol = _affine_params(src, dst)
        # 鲁棒剔除
        for _ in range(3):
            res = _apply_affine(sol, src) - dst
            r = np.hypot(res[:, 0], res[:, 1])
            keep = r < max(0.45, 3 * np.median(r) + 0.06)
            if keep.all() or keep.sum() < 3:
                break
            src, dst = src[keep], dst[keep]
            sol = _affine_params(src, dst)
        res = _apply_affine(sol, src) - dst
        rms = float(np.sqrt(np.mean(np.sum(res**2, axis=1))))
        corrections.append(sol)
        stats.append({"epoch": i, "n_match": int(len(src)), "rms_px": rms})
    return corrections, stats


# ------------------------------------------------------------------ 目标关联


def predicted_pixels(target: dict, epoch: Epoch) -> tuple[float, float]:
    """按目录的自行/视差预测目标在该历元的像素位置（0-based）。"""
    from .simulator import app_positions

    truth = target.get("truth", {}) or {}
    ra, dec = app_positions(
        target["ra"],
        target["dec"],
        truth.get("pmra_mas_yr") or 0.0,
        truth.get("pmdec_mas_yr") or 0.0,
        truth.get("parallax_mas") or 0.0,
        [epoch.mjd],
    )
    x, y = epoch.wcs.world_to_pixel(float(ra[0]), float(dec[0]))
    return float(x) - 1, float(y) - 1


def select_target_source(cat: list[Source], px: float, py: float, radius: float = 7.0) -> Source | None:
    best, bestd = None, radius
    for s in cat:
        d = math.hypot(s.x - px, s.y - py)
        if d < bestd:
            best, bestd = s, d
    return best


# ------------------------------------------------------------------ 主流程


def run_pipeline(target: dict, epochs: list[Epoch], truth: dict | None = None, mode: str = "auto") -> dict:
    if len(epochs) < 1:
        raise ValueError("no epochs")
    catalogs: list[list[Source]] = []
    products = []
    for ep in epochs:
        cat = detect_sources(ep)
        catalogs.append(cat)
        img, var, flags, bad, soft, med, std, noise = epoch_products(ep)
        products.append({"bg": med, "std": std, "noise": noise, "bad": bad})

    pred_positions = [predicted_pixels(target, ep) for ep in epochs]
    corrections, match_stats = match_sources(epochs, catalogs, exclude=pred_positions)

    # 目标的逐期测量
    tr_t, tr_xi, tr_eta, tr_sx, tr_sy, tr_f, tr_fe, tr_sat, tr_ids, tr_meta = ([] for _ in range(10))
    ref_wcs = epochs[0].wcs
    ra0, dec0 = target["ra"], target["dec"]
    target_xy_common = []

    for i, (ep, cat) in enumerate(zip(epochs, catalogs)):
        px, py = predicted_pixels(target, ep)
        unc = float(target.get("coord_unc_arcsec", 0.0) or 0.0)
        radius = max(4.0, unc / max(ep.wcs.pixel_scale_arcsec(), 0.1) + 2.0)
        src = select_target_source(cat, px, py, radius=radius)
        if src is None:
            # 饱和源可能没有被列进源表：强制在预测位置测一次
            img, var, flags, bad, soft, med, std, noise = epoch_products(ep)
            psf = ep.psf
            m = ph.measure_at(img, var, flags, px, py, psf)
            if not np.isfinite(m.get("flux", np.nan)):
                continue
            if m.get("flux_err", 0) > 0 and m["flux"] / m["flux_err"] < 3.0:
                continue  # 目标太暗/没探测到，不能用噪声硬凑一个位置
            src = Source(
                x=m["x"], y=m["y"], flux=m["flux"], flux_err=m["flux_err"], snr=5.0,
                sigma_x=m.get("sigma_x", 0.5), sigma_y=m.get("sigma_y", 0.5),
                saturated=m.get("saturated", False), method=m.get("method", "forced"),
            )
        # 目标单独做一次 PSF 拟合（参考星用快速质心即可）
        img_t, var_t, flags_t, bad_t, soft_t, med_t, std_t, noise_t = epoch_products(ep)
        tgt_meas = ph.measure_at(img_t, var_t, flags_t, src.x, src.y, ep.psf)
        if tgt_meas and np.isfinite(tgt_meas.get("x", np.nan)):
            src = Source(
                x=tgt_meas["x"], y=tgt_meas["y"],
                flux=float(tgt_meas.get("flux", src.flux)),
                flux_err=float(tgt_meas.get("flux_err", src.flux_err)),
                snr=src.snr,
                sigma_x=float(tgt_meas.get("sigma_x", src.sigma_x)),
                sigma_y=float(tgt_meas.get("sigma_y", src.sigma_y)),
                saturated=bool(tgt_meas.get("saturated", src.saturated)),
                method=tgt_meas.get("method", "psf"),
            )
        # 用第 0 期坐标系表达位置
        xy = np.array([[src.x, src.y]])
        sol = corrections[i]
        if sol is None:
            # 没有足够参考星：退回 WCS 投影
            ra, dec = ep.wcs.pixel_to_world(src.x + 1, src.y + 1)
            xr, yr = ref_wcs.world_to_pixel(ra, dec)
            xy0 = np.array([[float(xr) - 1, float(yr) - 1]])
            correction_ok = False
        else:
            xy0 = _apply_affine(sol, xy)
            correction_ok = True
        target_xy_common.append([float(xy0[0, 0]), float(xy0[0, 1])])

        # 参考系 → 切平面角秒
        ra_c, dec_c = ref_wcs.pixel_to_world(xy0[0, 0] + 1, xy0[0, 1] + 1)
        xi, eta = ref_wcs.sky_to_tangent_arcsec(ra_c, dec_c, ra0, dec0)
        # 误差：质心误差按像素尺度换算；若参考星定标，附加 0.08 px 系统项
        scale = ref_wcs.pixel_scale_arcsec()
        sig_px = max(math.hypot(src.sigma_x, src.sigma_y), 0.03) + (0.05 if correction_ok else 0.25)
        tr_t.append(ep.mjd)
        tr_xi.append(float(np.atleast_1d(xi)[0]))
        tr_eta.append(float(np.atleast_1d(eta)[0]))
        tr_sx.append(sig_px * scale)
        tr_sy.append(sig_px * scale)
        tr_f.append(float(src.flux))
        tr_fe.append(float(max(src.flux_err, 1e-6)))
        tr_sat.append(bool(src.saturated))
        tr_ids.append(f"{ep.id}")

    if len(tr_t) == 0:
        # 目标在该天区没有被探测到：给出明确的“不可判定”，不要伪造轨迹
        fp = {k: 0.0 for k in ("mu_mas_yr", "mu_err_mas_yr", "displacement_arcsec", "displacement_err_arcsec",
                               "amp_mag", "accel_sig", "linearity", "period_d", "var_chi2_red", "delta_bic_var")}
        fp.update({"pa_deg": None, "pa_disp_deg": None, "n_epochs": 0, "baseline_days": 0.0,
                   "scale_arcsec_px": 6.15, "mu_px_yr": 0.0, "mu_signif": 0.0, "mov_signif": 0.0,
                   "accel_arcsec_yr2": 0.0, "delta_bic_accel": 0.0})
        return {
            "class": "U", "confidence": 0.1, "readiness": "L0", "fingerprint": fp,
            "models": [], "best_model": None, "mu_mas_yr": 0.0, "mu_err_mas_yr": float("nan"),
            "pa_deg": None, "moving": False, "variable": False, "detected_epochs": 0,
            "baseline_days": 0.0, "n_boot": 0, "multi_band": False, "bands": [],
            "quality": ["TARGET_NOT_DETECTED"], "match_stats": match_stats,
            "n_detected_in_catalog": [len(c) for c in catalogs],
            "target_xy_common": [],
            "track": {"t": [], "xi": [], "eta": [], "sx": [], "sy": [], "flux": [], "flux_err": []},
            "measurements": [],
            "label_zh": "不可判定（目标未被探测到）",
            "evidence": ["在预测位置附近没有达到 3σ 的探测", f"共尝试 {len(epochs)} 期"],
            "falsifier": "若后续更深的观测或更大的视场里出现该目标，则本结论失效。",
            "public_text": "在这个天区、这些历元里，我们没有探测到目标。它可能太暗，或位置预测偏差较大。需要更深的数据或核对该天体的坐标。",
        }

    order = np.argsort(tr_t)
    track = Track(
        t=np.array(tr_t)[order],
        xi=np.array(tr_xi)[order],
        eta=np.array(tr_eta)[order],
        sigma_xi=np.array(tr_sx)[order],
        sigma_eta=np.array(tr_sy)[order],
        flux=np.array(tr_f)[order],
        flux_err=np.array(tr_fe)[order],
        band=np.array([epochs[i].band for i in order]),
        epoch_id=[tr_ids[i] for i in order],
        saturated=np.array(tr_sat)[order],
    )
    result = analyze_track(track, ra0, dec0, truth=truth)
    result["quality"] = merge_quality(result.get("quality"), quality_flags(track))
    result["match_stats"] = match_stats
    result["n_detected_in_catalog"] = [len(c) for c in catalogs]

    # 真值对照
    if truth:
        inj = truth.get("mu_mas_yr")
        if inj:
            result["truth_check"] = {
                "injected_mu_mas_yr": inj,
                "injected_pa_deg": truth.get("pa_deg"),
                "measured_mu_mas_yr": result["mu_mas_yr"],
                "ratio": (result["mu_mas_yr"] / inj) if inj else None,
            }
    result["measurements"] = [
        {
            "epoch_id": tr_ids[i],
            "mjd": float(track.t[i]),
            "xi_arcsec": float(track.xi[i]),
            "eta_arcsec": float(track.eta[i]),
            "sigma_arcsec": float(track.sigma_xi[i]),
            "flux": float(track.flux[i]),
            "flux_err": float(track.flux_err[i]),
            "saturated": bool(track.saturated[i]) if track.saturated is not None else False,
        }
        for i in range(len(track))
    ]
    result["target_xy_common"] = target_xy_common
    result["track"] = {
        "t": [float(x) for x in track.t],
        "xi": [float(x) for x in track.xi],
        "eta": [float(x) for x in track.eta],
        "sx": [float(x) for x in track.sigma_xi],
        "sy": [float(x) for x in track.sigma_eta],
        "flux": [float(x) for x in track.flux],
        "flux_err": [float(x) for x in track.flux_err],
        "band": [str(b) for b in (track.band if track.band is not None else [])],
    }
    return result


# ------------------------------------------------------------------ 公共网格重投影


def reproject_to_common(epochs: list[Epoch], common: WCS | None = None, order: int = 1) -> list[np.ndarray]:
    """把每期图像重采样到公共像素网格（第 0 期的 WCS）。"""
    if common is None:
        common = epochs[0].wcs
    n = int(common.naxis1)
    yy, xx = np.mgrid[0:n, 0:n]
    ra, dec = common.pixel_to_world(xx + 1.0, yy + 1.0)
    out = []
    for ep in epochs:
        x, y = ep.wcs.world_to_pixel(ra, dec)
        x = np.asarray(x) - 1.0
        y = np.asarray(y) - 1.0
        img = np.asarray(ep.image, float)
        h, w = img.shape
        ok = np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x < w - 1) & (y >= 0) & (y < h - 1)
        res = np.full((n, n), np.nan)
        xv = np.clip(x, 0, w - 1.001)
        yv = np.clip(y, 0, h - 1.001)
        x0 = np.floor(xv).astype(int)
        y0 = np.floor(yv).astype(int)
        fx = xv - x0
        fy = yv - y0
        v = (
            img[y0, x0] * (1 - fx) * (1 - fy)
            + img[y0, x0 + 1] * fx * (1 - fy)
            + img[y0 + 1, x0] * (1 - fx) * fy
            + img[y0 + 1, x0 + 1] * fx * fy
        )
        res[ok] = v[ok]
        out.append(res)
    return out
