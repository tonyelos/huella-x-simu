"""观测模拟器：用真实 SPHEREx PSF / 背景纹理 / 噪声水平生成多历元切片。

用途（在 UI 与 JSON 中必须显式标注）：
- 目标天区尚未有足够真实历元时，用 HYBRID/SIM 数据验证识别算法；
- 注入已知运动规律，检验流水线能否原样回收（injection-recovery）。

绝不把模拟数据伪装成真实观测：`Epoch.provenance` 恒为 "synthetic" 或 "hybrid"。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np

from . import photometry as ph
from .motion import YEAR, earth_position_au
from .wcs import WCS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ARCSEC = 206264.806


@dataclass
class Epoch:
    id: str
    mjd: float
    image: np.ndarray
    variance: np.ndarray
    flags: np.ndarray
    wcs: WCS
    psf: np.ndarray | None = None
    provenance: str = "synthetic"
    band: str = "D2"
    obsid: str = ""
    date_obs: str = ""
    source: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def shape(self):
        return self.image.shape

    def json(self) -> dict:
        return {
            "id": self.id,
            "mjd": self.mjd,
            "date_obs": self.date_obs,
            "band": self.band,
            "obsid": self.obsid,
            "provenance": self.provenance,
            "source": self.source,
            "shape": list(self.image.shape),
            "wcs": {
                "crval1": self.wcs.crval1,
                "crval2": self.wcs.crval2,
                "crpix1": self.wcs.crpix1,
                "crpix2": self.wcs.crpix2,
                "scale_arcsec": self.wcs.pixel_scale_arcsec(),
                "cd": self.wcs.cd.tolist(),
            },
        }


# ------------------------------------------------------------------ 背景模板


def load_background(name: str = "campoA", target_level: float = 0.55) -> np.ndarray:
    """读取真实 SPHEREx 背景切片，去掉亮源，缩放为低表面亮度纹理。"""
    path = os.path.join(DATA, "backgrounds", f"{name}.npz")
    if not os.path.exists(path):
        path = os.path.join(DATA, "backgrounds", "campoA.npz")
    if not os.path.exists(path):
        return np.full((117, 117), target_level)
    d = np.load(path)
    img = np.asarray(d["image"], float)
    med = float(np.nanmedian(img))
    img = np.where(np.isfinite(img), img, med)
    mad = float(np.nanmedian(np.abs(img - med))) * 1.4826
    if mad <= 0 or not np.isfinite(mad):
        mad = 0.05
    tex = np.clip(img, med - 3 * mad, med + 3 * mad)
    # 去掉亮源：高于 5σ 的像素用中值替代
    tex = np.where(tex > med + 5 * mad, med, tex)
    # 强平滑：保留大尺度 cirrus 纹理，去掉点源与高频结构
    k = 9
    pad = np.pad(tex, k // 2, mode="edge")
    sm = np.zeros_like(tex)
    for dy in range(k):
        for dx in range(k):
            sm += pad[dy : dy + tex.shape[0], dx : dx + tex.shape[1]]
    sm /= k * k
    tex = (sm - np.nanmedian(sm)) * 0.6 + target_level
    return np.where(np.isfinite(tex), tex, target_level)


def make_wcs(ra: float, dec: float, fov_deg: float, scale_arcsec: float = 6.15, angle_deg: float = 0.0,
             jitter_px: float = 0.0, rng: np.random.Generator | None = None) -> tuple[WCS, tuple[int, int]]:
    n = int(round(fov_deg * 3600 / scale_arcsec))
    n = max(n, 24)
    ang = math.radians(angle_deg)
    cd = (scale_arcsec / 3600.0) * np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    jx = rng.normal(0, jitter_px) if (rng is not None and jitter_px > 0) else 0.0
    jy = rng.normal(0, jitter_px) if (rng is not None and jitter_px > 0) else 0.0
    w = WCS(
        crval1=ra,
        crval2=dec,
        crpix1=n / 2 + 0.5 + jx,
        crpix2=n / 2 + 0.5 + jy,
        cd=cd,
        naxis1=n,
        naxis2=n,
    )
    return w, (n, n)


# ------------------------------------------------------------------ 渲染


def render_source(image: np.ndarray, psf: np.ndarray, x: float, y: float, flux: float, half: int = 8):
    """把归一化 PSF 放到亚像素位置 (x,y) 上，流量单位：MJy/sr·px 的积分。"""
    n = image.shape[0]
    xi, yi = int(round(x)), int(round(y))
    patch = ph.psf_at_offset(psf, x - xi, y - yi, half, 5)
    x0, x1 = xi - half, xi + half + 1
    y0, y1 = yi - half, yi + half + 1
    if x0 < 0 or y0 < 0 or x1 > n or y1 > n:
        return
    image[y0:y1, x0:x1] += flux * patch


def app_positions(ra0, dec0, pmra_mas, pmdec_mas, parallax_mas, times_mjd):
    """按自行 + 视差生成每个历元的视位置（度）。"""
    t_yr = (np.asarray(times_mjd, float) - 51544.5) / YEAR
    dra = pmra_mas / 3600e3 / max(math.cos(math.radians(dec0)), 1e-6) * t_yr
    ddec = pmdec_mas / 3600e3 * t_yr
    ra = ra0 + dra
    dec = dec0 + ddec
    if parallax_mas:
        pos = earth_position_au(times_mjd)
        ra_r, dec_r = math.radians(ra0), math.radians(dec0)
        X, Y, Z = pos[:, 0], pos[:, 1], pos[:, 2]
        p = parallax_mas / 1000.0
        dra_p = -p * (X * math.sin(ra_r) - Y * math.cos(ra_r)) / max(math.cos(dec_r), 1e-6) / 3600.0
        ddec_p = -p * (X * math.cos(ra_r) * math.sin(dec_r) + Y * math.sin(ra_r) * math.sin(dec_r) - Z * math.cos(dec_r)) / 3600.0
        ra = ra + dra_p
        dec = dec + ddec_p
    return np.asarray(ra), np.asarray(dec)


def variability_flux(times, base_flux, amp_mag, period_d, rng, irregular=0.0):
    t = np.asarray(times, float)
    if amp_mag <= 0:
        return np.full(len(t), base_flux)
    if period_d and period_d > 0:
        phase = 2 * np.pi * (t - t[0]) / period_d + rng.uniform(0, 2 * np.pi)
        mag = -2.5 * np.log10(base_flux) - amp_mag / 2 * np.sin(phase)
        flux = 10 ** (-0.4 * mag)
    else:
        flux = np.full(len(t), base_flux)
    if irregular > 0:
        flux = flux * (1 + rng.normal(0, irregular, len(t)))
    return flux


def generate_epochs(
    target: dict,
    times_mjd: np.ndarray,
    fov_deg: float = 0.2,
    n_stars: int = 44,
    seed: int = 7,
    inject_scale: float = 1.08,
    inject_pa_shift: float = 3.5,
    base_flux: float | None = None,
    background: str = "campoA",
    jitter_px: float = 0.18,
    provenance: str = "synthetic",
) -> tuple[list[Epoch], dict]:
    """生成多历元切片。返回 (epochs, truth)。"""
    rng = np.random.default_rng(seed)
    psf = np.load(os.path.join(DATA, "psf_d2.npy"))
    ra0, dec0 = float(target["ra"]), float(target["dec"])
    truth = target.get("truth", {}) or {}
    mu = truth.get("mu_mas_yr") or 0.0
    pa = truth.get("pa_deg") or 0.0
    par = truth.get("parallax_mas") or 0.0
    pmra_cat = truth.get("pmra_mas_yr")
    pmdec_cat = truth.get("pmdec_mas_yr")
    if pmra_cat is None:
        pmra_cat = mu * math.sin(math.radians(pa))
    if pmdec_cat is None:
        pmdec_cat = mu * math.cos(math.radians(pa))
    times = np.asarray(times_mjd, float)
    t_mid = float(times.mean())
    t_yr_mid = (t_mid - 51544.5) / YEAR

    # 目录位置的视运动（真实、可被预测）
    base_ras, base_decs = app_positions(ra0, dec0, pmra_cat, pmdec_cat, par, times)

    # 注入扰动：只改**观测窗口内**的残差运动，避免把 26 年的历史整体放大
    mu_ra_inj = pmra_cat * inject_scale * math.cos(math.radians(inject_pa_shift)) - pmdec_cat * inject_scale * math.sin(math.radians(inject_pa_shift))
    mu_dec_inj = pmra_cat * inject_scale * math.sin(math.radians(inject_pa_shift)) + pmdec_cat * inject_scale * math.cos(math.radians(inject_pa_shift))
    d_ra = mu_ra_inj - pmra_cat
    d_dec = mu_dec_inj - pmdec_cat
    off_ra = rng.uniform(-1.2, 1.2)  # 角秒
    off_dec = rng.uniform(-1.2, 1.2)
    t_yr = (times - 51544.5) / YEAR
    ras = base_ras + (off_ra + d_ra * (t_yr - t_yr_mid) / 1000.0) / 3600.0 / max(math.cos(math.radians(dec0)), 1e-6)
    decs = base_decs + (off_dec + d_dec * (t_yr - t_yr_mid) / 1000.0) / 3600.0
    mu_inj = float(math.hypot(mu_ra_inj, mu_dec_inj))
    pa_inj = float(math.degrees(math.atan2(mu_ra_inj, mu_dec_inj)) % 360.0)

    if base_flux is None:
        base_flux = {"rw_aur": 2.6, "r_dra": 2.4, "barnard": 3.0, "epsind_b": 4.6, "wise0855": 3.0}.get(target["id"], 1.6)
    amp = truth.get("amp_mag") or 0.0
    period = truth.get("period_d") or 0.0
    fluxes = variability_flux(times, base_flux, amp, period, rng, irregular=0.05 if amp else 0.0)

    bg = load_background(background)
    # 背景纹理固定 117²；视场变化时重采样到目标尺寸
    n_pix = max(int(round(fov_deg * 3600 / 6.15)), 24)
    if bg.shape != (n_pix, n_pix):
        from PIL import Image as _Image

        bg = np.asarray(
            _Image.fromarray(bg.astype(np.float32)).resize((n_pix, n_pix), _Image.BILINEAR), dtype=float
        )
    epochs: list[Epoch] = []
    truth_xy = []
    # 场星固定在**天球**上：这是参考星相对天体测量的前提
    half_fov = fov_deg / 2.0 * 0.92
    field_sky = []
    for _ in range(n_stars):
        sra = ra0 + rng.uniform(-half_fov, half_fov) / max(math.cos(math.radians(dec0)), 1e-6)
        sdec = dec0 + rng.uniform(-half_fov, half_fov)
        field_sky.append((sra, sdec, 10 ** rng.uniform(-0.9, 0.75)))
    # 望远镜指向固定在基线中点的**目录预测位置**（与注入扰动无关），否则相对天体测量会把真实运动一起扣掉
    point_ra, point_dec = float(base_ras[len(times) // 2]), float(base_decs[len(times) // 2])
    for i, (t, ra, dec, flux) in enumerate(zip(times, ras, decs, fluxes)):
        wcs, (ny, nx) = make_wcs(point_ra, point_dec, fov_deg, 6.15, angle_deg=rng.uniform(-4, 4), jitter_px=jitter_px, rng=rng)
        img = bg.copy()
        # 真实纹理的轻微平移，避免每期背景完全一样
        dx, dy = rng.integers(-3, 4), rng.integers(-3, 4)
        img = np.roll(np.roll(img, dy, axis=0), dx, axis=1)
        # 场星：固定天球坐标 → 每期投影到各自 WCS
        field_xy = []
        for sra, sdec, f in field_sky:
            sx, sy = wcs.world_to_pixel(sra, sdec)
            sx, sy = float(np.asarray(sx)) - 1, float(np.asarray(sy)) - 1
            if 6 <= sx < nx - 6 and 6 <= sy < ny - 6:
                render_source(img, psf, sx, sy, f, half=8)
                field_xy.append((sx, sy, f))
        # 目标：放在视场中心附近（由 WCS 决定像素）
        tx, ty = wcs.world_to_pixel(ra, dec)
        tx, ty = float(tx) - 1, float(ty) - 1  # 1-based → 0-based
        render_source(img, psf, tx, ty, float(flux), half=8)
        truth_xy.append((tx, ty))

        sigma = 0.018 + 0.006 * np.sqrt(np.clip(img, 0, None))
        noise = rng.normal(0, sigma)
        img = img + noise
        var = sigma**2
        flags = np.zeros((ny, nx), np.int32)
        hi = img > 60
        flags[hi] |= ph.FLAG_BITS["BLOOM"] | ph.FLAG_BITS["OVERFLOW"]
        flags |= ph.FLAG_BITS["SOURCE"]
        # 少量宇宙线/瞬变
        for _ in range(3):
            cy, cx = rng.integers(2, ny - 2), rng.integers(2, nx - 2)
            flags[cy, cx] |= ph.FLAG_BITS["TRANSIENT"]
            img[cy, cx] += rng.uniform(0.5, 2.0)

        epochs.append(
            Epoch(
                id=f"sim-{i:02d}",
                mjd=float(t),
                image=img.astype(np.float64),
                variance=np.full((ny, nx), var, float),
                flags=flags,
                wcs=wcs,
                psf=psf,
                provenance=provenance,
                band="D2",
                obsid=f"SIM-{target['id']}-{i}",
                date_obs=mjd_to_iso(t),
                source="simulator",
                meta={"truth_xy": [tx, ty], "field_xy": field_xy},
            )
        )

    inj = {
        "mu_mas_yr": mu_inj,
        "pa_deg": pa_inj,
        "pmra_mas_yr": mu_ra_inj,
        "pmdec_mas_yr": mu_dec_inj,
        "parallax_mas": par,
        "amp_mag": amp,
        "period_d": period or None,
        "base_flux": base_flux,
        "flux_series": [float(x) for x in fluxes],
        "epoch_truth_xy": [[float(a), float(b)] for a, b in truth_xy],
        "catalog_mu_mas_yr": mu,
        "catalog_pa_deg": pa,
    }
    return epochs, inj


def mjd_to_iso(mjd: float) -> str:
    import datetime as _dt

    t = _dt.datetime(1858, 11, 17, tzinfo=_dt.timezone.utc) + _dt.timedelta(days=float(mjd))
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def default_times(n: int = 8, start_mjd: float = 60930.0, span_days: float = 205.0) -> np.ndarray:
    """非均匀采样，模拟 SPHEREx 的周扫描节奏。"""
    base = np.linspace(0, 1, n)
    jitter = np.array([0.0, 0.02, -0.015, 0.03, -0.01, 0.015, -0.02, 0.0, 0.01, -0.01])[:n]
    return start_mjd + span_days * np.clip(base + jitter[: len(base)], 0, 1)
