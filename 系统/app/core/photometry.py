"""测光与质心：背景、掩膜、匹配滤波探测、PSF 模板拟合、孔径测光。

所有函数只依赖 numpy；图像单位假定为 MJy/sr。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- 掩膜

FLAG_BITS = {
    "TRANSIENT": 1 << 0,
    "OVERFLOW": 1 << 1,
    "SUR_ERROR": 1 << 2,
    "NONFUNC": 1 << 6,
    "DICHROIC": 1 << 7,
    "MISSING_DATA": 1 << 9,
    "HOT": 1 << 10,
    "COLD": 1 << 11,
    "FULLSAMPLE": 1 << 12,
    "PHANMISS": 1 << 14,
    "NONLINEAR": 1 << 15,
    "PERSIST": 1 << 17,
    "OUTLIER": 1 << 19,
    "CROSSTALK": 1 << 20,
    "SOURCE": 1 << 21,
    "GHOST": 1 << 22,
    "GHOST_FPA": 1 << 23,
    "GHOST_EXT": 1 << 24,
    "STREAK": 1 << 25,
    "BLOOM": 1 << 26,
    "SNOWBALL": 1 << 27,
    "HALO": 1 << 28,
}

# 这些位出现 = 该像素不可用于测光
BAD_BITS = (
    FLAG_BITS["TRANSIENT"]
    | FLAG_BITS["OVERFLOW"]
    | FLAG_BITS["SUR_ERROR"]
    | FLAG_BITS["NONFUNC"]
    | FLAG_BITS["MISSING_DATA"]
    | FLAG_BITS["HOT"]
    | FLAG_BITS["COLD"]
    | FLAG_BITS["PERSIST"]
    | FLAG_BITS["GHOST"]
    | FLAG_BITS["GHOST_FPA"]
    | FLAG_BITS["GHOST_EXT"]
    | FLAG_BITS["STREAK"]
    | FLAG_BITS["SNOWBALL"]
)
# 这些位出现 = 该像素可保留但方差要放大
SOFT_BITS = (
    FLAG_BITS["DICHROIC"]
    | FLAG_BITS["PHANMISS"]
    | FLAG_BITS["NONLINEAR"]
    | FLAG_BITS["OUTLIER"]
    | FLAG_BITS["CROSSTALK"]
    | FLAG_BITS["BLOOM"]
    | FLAG_BITS["HALO"]
)
SAT_BITS = FLAG_BITS["OVERFLOW"] | FLAG_BITS["BLOOM"] | FLAG_BITS["HALO"]


def build_mask(flags: np.ndarray | None, shape: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """返回 (bad, soft)。bad=True 表示必须剔除；soft=True 表示方差膨胀。"""
    if flags is None:
        if shape is None:
            raise ValueError("need flags or shape")
        z = np.zeros(shape, bool)
        return z, z.copy()
    f = np.asarray(flags, dtype=np.int64)
    bad = (f & BAD_BITS) != 0
    soft = (f & SOFT_BITS) != 0
    return bad, soft


def is_saturated(flags: np.ndarray | None, x: float, y: float, r: int = 1) -> bool:
    """检查 (x,y) 周围 r 像素内是否存在饱和标志。

    调用方可能在目标预测位置越界时调用（例如切片被探测器边缘截断）；
    这里必须 clamp 到图像有效范围，越界/全空窗口返回 False，不能抛异常。
    """
    if flags is None:
        return False
    f = np.asarray(flags)
    if f.ndim != 2 or f.size == 0:
        return False
    try:
        xi, yi = int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError, OverflowError):
        return False
    h, w = f.shape
    x0, x1 = max(0, xi - r), min(w, xi + r + 1)
    y0, y1 = max(0, yi - r), min(h, yi + r + 1)
    if x1 <= x0 or y1 <= y0:
        return False
    sub = f[y0:y1, x0:x1]
    return bool(np.any((sub & SAT_BITS) != 0))


# ---------------------------------------------------------------- 统计


def sigma_clipped_median_std(
    data: np.ndarray, mask: np.ndarray | None = None, sigma: float = 3.0, iters: int = 6
) -> tuple[float, float]:
    a = np.asarray(data, dtype=float).ravel()
    if mask is not None:
        m = np.asarray(mask, bool).ravel()
        a = a[~m]
    a = a[np.isfinite(a)]
    if a.size == 0:
        return 0.0, 1.0
    med = float(np.median(a))
    std = float(np.std(a))
    for _ in range(iters):
        keep = np.abs(a - med) < sigma * std
        if keep.sum() < 5:
            break
        med = float(np.median(a[keep]))
        std = float(np.std(a[keep]))
    return med, std


def background_level(image, mask=None) -> float:
    med, _ = sigma_clipped_median_std(image, mask)
    return med


# ---------------------------------------------------------------- 探测


def _gauss_kernel(sigma_px: float) -> np.ndarray:
    r = max(2, int(math.ceil(3 * sigma_px)))
    y, x = np.mgrid[-r : r + 1, -r : r + 1]
    k = np.exp(-(x * x + y * y) / (2 * sigma_px * sigma_px))
    return k / k.sum()


def matched_filter(image: np.ndarray, noise: np.ndarray, mask: np.ndarray | None = None, sigma_px: float = 1.0) -> np.ndarray:
    """PSF 加权匹配滤波，返回 SNR 图。用归一化互相关近似。"""
    img = np.asarray(image, float).copy()
    bad = np.asarray(mask, bool) if mask is not None else ~np.isfinite(img)
    # 必须先减背景，否则整幅图的 SNR 会被背景 DC 抬高
    finite = (~bad) & np.isfinite(img)
    bkg = float(np.median(img[finite])) if finite.any() else 0.0
    img = np.where(finite, img - bkg, 0.0)
    k = _gauss_kernel(sigma_px)
    r = k.shape[0] // 2
    pad = np.pad(img, r, mode="constant")
    acc = np.zeros_like(img)
    for dy in range(k.shape[0]):
        for dx in range(k.shape[1]):
            acc += k[dy, dx] * pad[dy : dy + img.shape[0], dx : dx + img.shape[1]]
    return acc / np.maximum(np.asarray(noise, float), 1e-12)


def find_peaks(snr: np.ndarray, threshold: float = 5.0, min_sep: int = 2, bad_mask=None) -> list[tuple[int, int, float]]:
    """返回 [(x, y, snr)]，x/y 为 0-based 数组索引。"""
    s = np.asarray(snr, float)
    s = np.where(np.isfinite(s), s, 0.0)
    cand = []
    ny, nx = s.shape
    for y in range(1, ny - 1):
        for x in range(1, nx - 1):
            if bad_mask is not None and bad_mask[y, x]:
                continue
            v = s[y, x]
            if v < threshold:
                continue
            win = s[max(0, y - 1) : y + 2, max(0, x - 1) : x + 2]
            if v < win.max() - 1e-9:
                continue
            cand.append((x, y, float(v)))
    cand.sort(key=lambda t: -t[2])
    # 按距离去重
    keep = []
    for c in cand:
        if all((c[0] - k[0]) ** 2 + (c[1] - k[1]) ** 2 >= min_sep**2 for k in keep):
            keep.append(c)
    return keep


# ---------------------------------------------------------------- PSF


def select_psf(epsf: dict, x_full: float, y_full: float, wave: float | None = None) -> np.ndarray | None:
    """从 EPSF 表里选最接近 (x,y,λ) 的一行，返回归一化的 33×33 过采样 PSF。"""
    if not epsf:
        return None
    X = epsf["XCENTER"]
    Y = epsf["YCENTER"]
    W = epsf["CWAVE"]
    d2 = (X - x_full) ** 2 + (Y - y_full) ** 2
    idx = int(np.argmin(d2))
    if wave is not None and np.isfinite(wave):
        # 先在同位置附近 20 行里挑波长最接近的，避免用错波段
        near = np.argsort(d2)[:20]
        idx = int(near[np.argmin(np.abs(W[near] - wave))])
    psf = np.asarray(epsf["EPSF"][idx], float)
    s = psf.sum()
    return psf / s if s > 0 else None


def psf_at_offset(psf: np.ndarray, dx: float, dy: float, half: int, oversample: int = 5) -> np.ndarray:
    """把过采样 PSF 采样到 (2*half+1)² 的像素网格上。dx,dy 为源相对窗口中心的像素偏移。"""
    n = 2 * half + 1
    c = (psf.shape[0] - 1) / 2.0
    out = np.zeros((n, n))
    for j in range(n):
        for i in range(n):
            # 像素 (j,i) 的中心相对源的偏移（像素）
            ox = (i - half) - dx
            oy = (j - half) - dy
            px = c + ox * oversample
            py = c + oy * oversample
            x0 = int(np.floor(px))
            y0 = int(np.floor(py))
            if x0 < 0 or y0 < 0 or x0 + 1 >= psf.shape[1] or y0 + 1 >= psf.shape[0]:
                continue
            fx, fy = px - x0, py - y0
            out[j, i] = (
                psf[y0, x0] * (1 - fx) * (1 - fy)
                + psf[y0, x0 + 1] * fx * (1 - fy)
                + psf[y0 + 1, x0] * (1 - fx) * fy
                + psf[y0 + 1, x0 + 1] * fx * fy
            )
    # PSF 在过采样网格上归一化为 1；一个像素对应 oversample² 个采样点
    return out * (oversample * oversample)


def centroid_psf(
    image: np.ndarray,
    noise: np.ndarray,
    bad: np.ndarray,
    x0: int,
    y0: int,
    psf: np.ndarray,
    half: int = 5,
    oversample: int = 5,
) -> dict | None:
    """PSF 模板质心拟合。返回 dx,dy（相对 x0,y0 的亚像素偏移）与误差。"""
    h, w = image.shape
    if x0 - half < 1 or y0 - half < 1 or x0 + half >= w - 1 or y0 + half >= h - 1:
        half = max(2, min(x0, y0, w - 1 - x0, h - 1 - y0) - 1)
    if half < 2:
        return None
    sub = image[y0 - half : y0 + half + 1, x0 - half : x0 + half + 1].astype(float)
    subv = noise[y0 - half : y0 + half + 1, x0 - half : x0 + half + 1].astype(float)
    subb = bad[y0 - half : y0 + half + 1, x0 - half : x0 + half + 1]
    ok = (~subb) & np.isfinite(sub) & (subv > 0)
    if ok.sum() < 9:
        return None
    bg = float(np.median(sub[ok]))
    wgt = 1.0 / np.maximum(subv[ok], 1e-12)

    def chi2_of(dx, dy):
        model = psf_at_offset(psf, dx, dy, half, oversample)
        resid = sub - bg - model * 0.0
        return model, resid, wgt

    # 粗网格 + 局部细化
    def score(dx, dy):
        model = psf_at_offset(psf, dx, dy, half, oversample)
        m = model[ok]
        y = (sub - bg)[ok]
        if m.sum() <= 0:
            return 1e30, 0.0
        amp = float(np.sum(m * y * wgt) / max(np.sum(m * m * wgt), 1e-30))
        r = y - amp * m
        return float(np.sum(r * r * wgt)), amp

    best = (1e30, 0.0, 0.0, 0.0)
    for dy in np.linspace(-1.0, 1.0, 9):
        for dx in np.linspace(-1.0, 1.0, 9):
            s, a = score(dx, dy)
            if s < best[0]:
                best = (s, dx, dy, a)
    # 抛物线细化（在最佳点周围 0.1 px 步长）
    for step in (0.2, 0.05):
        improved = True
        while improved:
            improved = False
            for ddx, ddy in ((step, 0), (-step, 0), (0, step), (0, -step)):
                s, a = score(best[1] + ddx, best[2] + ddy)
                if s < best[0]:
                    best = (s, best[1] + ddx, best[2] + ddy, a)
                    improved = True

    chi2, dx, dy, amp = best
    # 误差：用 Fisher 信息近似，σ ≈ 1/sqrt(Σ (A/σ)² (dP/dx)²)
    model = psf_at_offset(psf, dx, dy, half, oversample)
    gy, gx = np.gradient(model)
    denom_x = float(np.sum((amp * gx[ok] / subv[ok]) ** 2))
    denom_y = float(np.sum((amp * gy[ok] / subv[ok]) ** 2))
    sig_x = 1.0 / math.sqrt(denom_x) if denom_x > 0 else 0.5
    sig_y = 1.0 / math.sqrt(denom_y) if denom_y > 0 else 0.5
    core = np.abs(model) > 0.02 * np.abs(model).max()
    npix = int(ok.sum())

    return {
        "x": float(x0 + dx),
        "y": float(y0 + dy),
        "dx": float(dx),
        "dy": float(dy),
        "sigma_x": float(min(sig_x, 1.0)),
        "sigma_y": float(min(sig_y, 1.0)),
        "amp": float(amp),
        "flux": float(amp),
        "chi2": float(chi2),
        "npix": npix,
        "core_ok": bool(np.all(ok[core])),
        "background": bg,
    }


def centroid_bloom(image: np.ndarray, noise: np.ndarray, bad: np.ndarray, x0: int, y0: int, r: int = 8) -> dict | None:
    """饱和源（核心缺失/溢出）的质心：用环带内被截断的强度加权。"""
    h, w = image.shape
    x_lo, x_hi = max(0, x0 - r), max(0, min(w, x0 + r + 1))
    y_lo, y_hi = max(0, y0 - r), max(0, min(h, y0 + r + 1))
    sub = np.asarray(image[y_lo:y_hi, x_lo:x_hi], float)
    subb = np.asarray(bad[y_lo:y_hi, x_lo:x_hi], bool)
    ok = (~subb) & np.isfinite(sub)
    if ok.sum() < 20:
        return None
    bg = float(np.median(sub[ok]))
    yy, xx = np.mgrid[y_lo:y_hi, x_lo:x_hi]
    v = np.clip(sub - bg, 0, None)
    # 截断到 85 分位，避免核心 NaN 边缘的极端值主导
    cap = np.percentile(v[ok], 85)
    v = np.minimum(v, cap)
    v[~ok] = 0.0
    sw = v.sum()
    if sw <= 0:
        return None
    cx = float((v * xx).sum() / sw)
    cy = float((v * yy).sum() / sw)
    return {
        "x": cx,
        "y": cy,
        "dx": cx - x0,
        "dy": cy - y0,
        "sigma_x": 0.35,
        "sigma_y": 0.35,
        "amp": float(np.max(sub[ok])),
        "flux": float(sw),
        "chi2": float("nan"),
        "npix": int(ok.sum()),
        "core_ok": False,
        "background": bg,
        "method": "bloom",
    }


# ---------------------------------------------------------------- 测光


def aperture_flux(
    image: np.ndarray,
    noise: np.ndarray,
    bad: np.ndarray,
    x: float,
    y: float,
    r_ap: float = 4.0,
    r_in: float = 6.0,
    r_out: float = 9.0,
) -> dict:
    """孔径测光 + 局部环带背景。返回 MJy/sr × pixel² 的积分流量。"""
    img = np.asarray(image, float)
    var = np.asarray(noise, float) ** 2
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - x, yy - y)
    ap = (d <= r_ap) & (~bad) & np.isfinite(img)
    an = (d >= r_in) & (d <= r_out) & (~bad) & np.isfinite(img)
    if ap.sum() < 3 or an.sum() < 5:
        return {"flux": float("nan"), "flux_err": float("nan"), "npix": int(ap.sum()), "bkg": float("nan")}
    bkg = float(np.median(img[an]))
    bkg_err = float(np.std(img[an]) / math.sqrt(max(an.sum(), 1)))
    flux = float(np.sum(img[ap] - bkg))
    ferr = math.sqrt(float(np.sum(var[ap])) + ap.sum() * bkg_err**2)
    return {
        "flux": flux,
        "flux_err": ferr,
        "npix": int(ap.sum()),
        "bkg": bkg,
        "bkg_err": bkg_err,
        "r_ap": r_ap,
    }


def measure_at(
    image: np.ndarray,
    variance: np.ndarray,
    flags: np.ndarray | None,
    x: float,
    y: float,
    psf: np.ndarray | None,
    sys_frac: float = 0.02,
) -> dict:
    """在给定位置测质心与流量。x,y 为 0-based 浮点坐标。

    位置越界（例如切片只覆盖探测器边缘、目标在切片外）时返回显式的
    ``out_of_frame=True`` 与 NaN 流量，交由管线跳过该历元，绝不抛异常。
    """
    img = np.asarray(image, float)
    if img.ndim != 2:
        raise ValueError("image must be 2D")
    h, w = img.shape
    method = "psf" if psf is not None else "moments"
    try:
        xf, yf = float(x), float(y)
        xi, yi = int(round(xf)), int(round(yf))
    except (TypeError, ValueError, OverflowError):
        xf = yf = float("nan")
        xi = yi = -10**9
    if not (math.isfinite(xf) and math.isfinite(yf)) or not (0 <= xi < w and 0 <= yi < h):
        return {
            "saturated": False,
            "method": method,
            "out_of_frame": True,
            "x": xf,
            "y": yf,
            "sigma_x": 1.0,
            "sigma_y": 1.0,
            "flux": float("nan"),
            "flux_err": float("nan"),
        }

    var = np.asarray(variance, float) if variance is not None else np.ones_like(img) * 1e-6
    noise = np.sqrt(np.maximum(var, 0)) + sys_frac * np.abs(img)
    bad, soft = build_mask(flags, img.shape)
    bad = bad | ~np.isfinite(img)
    sat = is_saturated(flags, xi, yi, 1) or bool(np.any(~np.isfinite(img[max(0, yi - 1) : yi + 2, max(0, xi - 1) : xi + 2])))
    out: dict = {"saturated": sat, "method": method, "out_of_frame": False}

    cen = None
    if psf is not None and not sat:
        cen = centroid_psf(img, noise, bad, xi, yi, psf)
    if cen is None or sat:
        cen = centroid_bloom(img, noise, bad, xi, yi)
        out["method"] = "bloom"
    if cen is None:
        return {**out, "x": x, "y": y, "sigma_x": 1.0, "sigma_y": 1.0, "flux": float("nan"), "flux_err": float("nan")}

    phot = aperture_flux(img, noise, bad, cen["x"], cen["y"])
    out.update(cen)
    out.update({"flux": phot["flux"], "flux_err": phot["flux_err"], "aperture": phot})
    return out


def to_jansky_per_pixel(flux_mjy_sr_pix: float, omega_arcsec2: float = 37.6967) -> float:
    """MJy/sr × px → Jy（点源积分流量）。1 px = 37.6967 arcsec²。"""
    omega_sr = omega_arcsec2 * (math.pi / 180.0 / 3600.0) ** 2
    return flux_mjy_sr_pix * omega_sr * 1e6


def centroid_fast(image: np.ndarray, noise: np.ndarray, bad: np.ndarray, x0: int, y0: int, r: int = 3) -> dict | None:
    """快速窗口质心（用于参考星）。5×5–9×9 窗口，边框估背景，两轮迭代。"""
    h, w = image.shape
    r = min(r, x0, y0, w - 1 - x0, h - 1 - y0)
    if r < 2:
        return None
    sub = np.asarray(image[y0 - r : y0 + r + 1, x0 - r : x0 + r + 1], float)
    subb = np.asarray(bad[y0 - r : y0 + r + 1, x0 - r : x0 + r + 1], bool)
    if subb.sum() > 0.4 * subb.size:
        return None
    yy, xx = np.mgrid[y0 - r : y0 + r + 1, x0 - r : x0 + r + 1]
    edge = ~subb
    edge[:1, :] = edge[-1:, :] = True
    edge[:, :1] = edge[:, -1:] = True
    border = sub[edge & ~subb]
    bg = float(np.median(border)) if border.size else float(np.median(sub[~subb]))
    v = np.clip(sub - bg, 0, None)
    v[subb] = 0.0
    sw = v.sum()
    if sw <= 0:
        return None
    cx = float((v * xx).sum() / sw)
    cy = float((v * yy).sum() / sw)
    # 再迭代一次（以新中心重算背景与权重）
    for _ in range(2):
        d = np.hypot(xx - cx, yy - cy)
        m = (d <= r) & (~subb)
        if m.sum() < 3:
            break
        b = float(np.median(sub[m & (d > r * 0.75)])) if (m & (d > r * 0.75)).sum() > 0 else bg
        v = np.where(m, np.clip(sub - b, 0, None), 0.0)
        sw = v.sum()
        if sw <= 0:
            break
        cx = float((v * xx).sum() / sw)
        cy = float((v * yy).sum() / sw)
    peak = float(np.nanmax(v)) if np.isfinite(v).any() else 0.0
    subnoise = np.asarray(noise, float)[y0 - r : y0 + r + 1, x0 - r : x0 + r + 1]
    snr = peak / max(float(np.median(subnoise[~subb])) if (~subb).any() else 0.03, 1e-6)
    sig = max(0.05, 0.45 / max(snr, 1.0))
    return {
        "x": cx,
        "y": cy,
        "dx": cx - x0,
        "dy": cy - y0,
        "sigma_x": sig,
        "sigma_y": sig,
        "amp": peak,
        "flux": float(sw),
        "chi2": float("nan"),
        "npix": int((~subb).sum()),
        "core_ok": True,
        "background": bg,
        "method": "fast",
    }
