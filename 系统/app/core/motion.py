"""运动规律推断：切平面模型族、BIC 模型选择、运动指纹、分类与公众话术。

模型族（对应 docs/01_理论设计编排.md §4.2）：
  M0  静止 + 恒亮      k=3
  M1  静止 + 周期变光   k=6（周期由网格搜索给出）
  M2  匀速自行 + 恒亮   k=5
  M2b 匀速自行 + 线性变光 k=6
  M3  自行 + 视差       k=6
  M4  自行 + 加速       k=7
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np

YEAR = 365.25
MAS_PER_ARCSEC = 1000.0


# ------------------------------------------------------------------ 基础工具


def weighted_polyfit(t: np.ndarray, y: np.ndarray, sigma: np.ndarray, order: int):
    """加权多项式最小二乘。返回 (coef, cov, chi2, dof)。"""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    sigma = np.asarray(sigma, float)
    ok = np.isfinite(t) & np.isfinite(y) & np.isfinite(sigma) & (sigma > 0)
    t, y, sigma = t[ok], y[ok], sigma[ok]
    n = len(t)
    if n < order + 1:
        return None, None, float("nan"), 0
    A = np.vander(t, order + 1, increasing=True)
    W = 1.0 / sigma**2
    ATA = A.T @ (A * W[:, None])
    ATy = A.T @ (y * W)
    try:
        cov = np.linalg.inv(ATA)
    except np.linalg.LinAlgError:
        return None, None, float("nan"), 0
    coef = cov @ ATy
    resid = y - A @ coef
    chi2 = float(np.sum((resid / sigma) ** 2))
    return coef, cov, chi2, n - (order + 1)


def bic(chi2: float, k: int, n: int) -> float:
    if not np.isfinite(chi2):
        return float("inf")
    return float(chi2 + k * math.log(max(n, 2)))


def model_probs(bics: dict[str, float]) -> dict[str, float]:
    vals = np.array(list(bics.values()), float)
    vals = vals - np.nanmin(vals)
    w = np.exp(-0.5 * vals)
    w[~np.isfinite(w)] = 0
    s = w.sum()
    if s <= 0:
        return {k: 0.0 for k in bics}
    return {k: float(v / s) for k, v in zip(bics, w)}


# ------------------------------------------------------------------ 地球位置（视差基函数）


def earth_position_au(mjd):
    """低精度日心地球位置（AU，赤道 J2000）。精度 ~0.01 AU，足够做视差基函数。"""
    T = (np.asarray(mjd, float) - 51544.5) / 36525.0
    L = np.deg2rad((280.460 + 36000.771 * T) % 360.0)
    g = np.deg2rad((357.528 + 35999.050 * T) % 360.0)
    lam = L + np.deg2rad(1.915) * np.sin(g) + np.deg2rad(0.020) * np.sin(2 * g)
    R = 1.00014 - 0.01671 * np.cos(g) - 0.00014 * np.cos(2 * g)
    x = R * np.cos(lam)
    y_ecl = R * np.sin(lam)
    eps = np.deg2rad(23.4393 - 0.0130 * T)
    return np.stack([x, y_ecl * np.cos(eps), y_ecl * np.sin(eps)], axis=-1)


def parallax_basis(t_mjd, ra_deg, dec_deg):
    """返回 (dxi, deta)（角秒/角秒视差），即视差基函数，已含负号约定。"""
    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    pos = earth_position_au(t_mjd)
    X, Y, Z = pos[:, 0], pos[:, 1], pos[:, 2]
    dxi = -(X * math.sin(ra) - Y * math.cos(ra))
    deta = -(X * math.cos(ra) * math.sin(dec) + Y * math.sin(ra) * math.sin(dec) - Z * math.cos(dec))
    return dxi, deta


# ------------------------------------------------------------------ 数据容器


@dataclass
class Track:
    """一条轨迹的测量序列。角度单位：角秒；时间：MJD。"""

    t: np.ndarray
    xi: np.ndarray
    eta: np.ndarray
    sigma_xi: np.ndarray
    sigma_eta: np.ndarray
    flux: np.ndarray
    flux_err: np.ndarray
    band: np.ndarray | None = None
    epoch_id: list[str] = field(default_factory=list)
    saturated: np.ndarray | None = None

    def __len__(self):
        return len(self.t)

    def subset(self, idx):
        return Track(
            t=self.t[idx],
            xi=self.xi[idx],
            eta=self.eta[idx],
            sigma_xi=self.sigma_xi[idx],
            sigma_eta=self.sigma_eta[idx],
            flux=self.flux[idx],
            flux_err=self.flux_err[idx],
            band=None if self.band is None else self.band[idx],
            epoch_id=[self.epoch_id[i] for i in np.asarray(idx)],
            saturated=None if self.saturated is None else self.saturated[idx],
        )


# ------------------------------------------------------------------ 位置模型


def _design_matrix(t_years, kind, ra, dec, t_mjd):
    cols = [np.ones_like(t_years)]
    if kind in ("M2", "M2b", "M3", "M4"):
        cols.append(t_years)
    if kind == "M3":
        bx, by = parallax_basis(t_mjd, ra, dec)
        cols.append(bx)
    if kind == "M4":
        cols.append(0.5 * t_years**2)
    return np.stack(cols, axis=1)


def fit_position(track: Track, kind: str, ra0: float, dec0: float, robust: bool = True) -> dict:
    t_years = (track.t - track.t[0]) / YEAR
    A = _design_matrix(t_years, kind, ra0, dec0, track.t)
    k_pos = A.shape[1]
    ys = [track.xi, track.eta]
    sigs = [track.sigma_xi, track.sigma_eta]
    coefs, covs, chi2s, dofs = [], [], [], []
    for y, s in zip(ys, sigs):
        c, cov, chi2, dof = weighted_polyfit(t_years, y, s, 0) if kind == "M0" else _wls(A, y, s)
        coefs.append(c)
        covs.append(cov)
        chi2s.append(chi2)
        dofs.append(dof)
    ok = coefs[0] is not None and coefs[1] is not None
    chi2 = float(sum(chi2s)) if ok else float("inf")
    n = 2 * len(track.t)
    return {
        "kind": kind,
        "coef_xi": coefs[0],
        "coef_eta": coefs[1],
        "cov_xi": covs[0],
        "cov_eta": covs[1],
        "chi2": chi2,
        "k": k_pos * 2,
        "n": n,
        "bic": bic(chi2, k_pos * 2, n),
        "ok": ok,
        "t0": float(track.t[0]),
        "t_years": t_years,
    }


def _wls(A, y, sigma):
    ok = np.isfinite(y) & np.isfinite(sigma) & (sigma > 0)
    if ok.sum() < A.shape[1] + 1:
        return None, None, float("nan"), 0
    A2, y2, s2 = A[ok], y[ok], sigma[ok]
    W = 1.0 / s2**2
    ATA = A2.T @ (A2 * W[:, None])
    try:
        cov = np.linalg.inv(ATA)
    except np.linalg.LinAlgError:
        return None, None, float("nan"), 0
    coef = cov @ (A2.T @ (y2 * W))
    resid = y2 - A2 @ coef
    return coef, cov, float(np.sum((resid / s2) ** 2)), int(ok.sum()) - A.shape[1]


# ------------------------------------------------------------------ 变光模型


def fit_constant_flux(t, f, ef):
    ok = np.isfinite(f) & np.isfinite(ef) & (ef > 0)
    if ok.sum() < 1:
        return {"chi2": float("inf"), "k": 1, "f0": float("nan"), "level": float("nan")}
    w = 1 / ef[ok] ** 2
    f0 = float(np.sum(f[ok] * w) / np.sum(w))
    chi2 = float(np.sum(((f[ok] - f0) / ef[ok]) ** 2))
    return {"chi2": chi2, "k": 1, "f0": f0, "level": 0.0}


def lomb_scargle(t, y, dy, fmin=1 / 1000.0, fmax=1 / 0.5, nfreq=4000):
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    dy = np.asarray(dy, float)
    ok = np.isfinite(t) & np.isfinite(y) & np.isfinite(dy) & (dy > 0)
    t, y, w = t[ok], y[ok], 1 / dy[ok] ** 2
    if len(t) < 4:
        return None
    y = y - np.average(y, weights=w)
    freqs = np.linspace(fmin, fmax, nfreq)
    best = (-1.0, None)
    tau = np.arctan2(np.sum(w * np.sin(2 * 2 * np.pi * freqs[:, None] * t), axis=1),
                     np.sum(w * np.cos(2 * 2 * np.pi * freqs[:, None] * t), axis=1)) / (2 * 2 * np.pi)
    # 向量化：对每个频率建设计矩阵太慢；用循环（历元很少）
    for f in freqs:
        ph = 2 * np.pi * f * (t - tau[0])
        c, s = np.cos(ph), np.sin(ph)
        yw = y * np.sqrt(w)
        C = np.stack([np.ones_like(c), c, s], axis=1)
        coef, *_ = np.linalg.lstsq(C * np.sqrt(w)[:, None], yw, rcond=None)
        model = C @ coef
        chi2 = float(np.sum(((y - model) / dy) ** 2))
        power = 1.0 - chi2 / max(np.sum(((y - np.average(y, weights=w)) / dy) ** 2), 1e-12)
        if power > best[0]:
            best = (power, float(f))
    return {"power": best[0], "freq": best[1], "period": 1.0 / best[1] if best[1] else None}


def fit_sinusoid(t, f, ef, fmin=1 / 500.0, fmax=1 / 0.5):
    """固定周期网格上的正弦拟合；返回最优 P 与线性参数。"""
    t = np.asarray(t, float)
    f = np.asarray(f, float)
    ef = np.asarray(ef, float)
    ok = np.isfinite(t) & np.isfinite(f) & np.isfinite(ef) & (ef > 0)
    if ok.sum() < 4:
        return None
    t, f, ef = t[ok], f[ok], ef[ok]
    ps = np.geomspace(1 / fmax, 1 / fmin, 1200)
    best = None
    for P in ps:
        ph = 2 * np.pi * t / P
        A = np.stack([np.ones_like(ph), np.cos(ph), np.sin(ph)], axis=1)
        coef, *_ = np.linalg.lstsq(A / ef[:, None], f / ef, rcond=None)
        model = A @ coef
        chi2 = float(np.sum(((f - model) / ef) ** 2))
        if best is None or chi2 < best[0]:
            best = (chi2, P, coef)
    chi2, P, coef = best
    amp = float(np.hypot(coef[1], coef[2]))
    return {"chi2": chi2, "k": 4, "period": float(P), "amp": amp, "f0": float(coef[0]), "phase": float(np.arctan2(-coef[2], coef[1]))}


# ------------------------------------------------------------------ 综合推断


def _blend_check(t, xi, eta, sxi, seta, flux, ok):
    """检测"质心随流量漂移"：位置 = a + b t + c F。

    若加入 cF 项后 χ² 显著下降、流量系数显著，且剩余自行不显著，
    则运动更可能是变星与邻星混叠造成的假信号，而不是真实自行。
    """
    if ok.sum() < 5:
        return {"flag": False, "delta_chi2": 0.0, "flux_sig": 0.0, "motion_sig": float("inf"), "corr_xi_flux": 0.0}
    tt = (np.asarray(t, float)[ok] - float(np.asarray(t, float)[ok][0])) / YEAR
    F = np.asarray(flux, float)[ok]
    Fn = (F - F.mean()) / max(F.std(), 1e-9)
    A3 = np.stack([np.ones_like(tt), tt, Fn], axis=1)
    A2 = A3[:, :2]
    chi3 = chi2 = 0.0
    flux_sigs = []
    motion_sigs = []
    for y, sig in ((np.asarray(xi, float)[ok], np.asarray(sxi, float)[ok]),
                   (np.asarray(eta, float)[ok], np.asarray(seta, float)[ok])):
        c2, cov2, ch2, _ = _wls(A2, y, sig)
        c3, cov3, ch3, _ = _wls(A3, y, sig)
        if c2 is None or c3 is None:
            return {"flag": False, "delta_chi2": 0.0, "flux_sig": 0.0, "motion_sig": float("inf"), "corr_xi_flux": 0.0}
        chi2 += ch2
        chi3 += ch3
        flux_sigs.append(abs(c3[2]) / math.sqrt(max(cov3[2, 2], 1e-30)))
        motion_sigs.append(abs(c3[1]) / math.sqrt(max(cov3[1, 1], 1e-30)))
    delta = chi2 - chi3
    flux_sig = max(flux_sigs)
    motion_sig = float(math.hypot(*motion_sigs))
    corr = float(np.corrcoef(xi[ok], flux[ok])[0, 1]) if len(F) > 2 else 0.0
    return {
        "flag": bool(delta > 9.0 and flux_sig > 2.0),
        "delta_chi2": float(delta),
        "flux_sig": float(flux_sig),
        "motion_sig": motion_sig,
        "corr_xi_flux": corr,
    }


def analyze_track(
    track: Track,
    ra0: float,
    dec0: float,
    truth: dict | None = None,
    n_boot: int = 300,
) -> dict:
    n = len(track)
    t_years = (track.t - track.t[0]) / YEAR
    baseline_years = float(t_years[-1]) if n else 0.0
    flux_ok = np.isfinite(track.flux) & np.isfinite(track.flux_err) & (track.flux_err > 0)
    bands = sorted(set(np.asarray(track.band, dtype=str).tolist())) if track.band is not None else []
    multi_band = len(bands) > 1
    if multi_band:
        flux_ok[:] = False  # 不同探测器的流量不可直接比较
    n_flux = int(flux_ok.sum())

    # ---- 位置模型（历元太少时不拟合高阶模型）
    pos_models = {}
    if n >= 2:
        pos_models["M0"] = fit_position(track, "M0", ra0, dec0)
    if n >= 3:
        pos_models["M2"] = fit_position(track, "M2", ra0, dec0)
    if n >= 4:
        pos_models["M4"] = fit_position(track, "M4", ra0, dec0)
    if n >= 5 and baseline_years > 0.6:
        pos_models["M3"] = fit_position(track, "M3", ra0, dec0)

    # ---- 变光模型
    flux_models: dict[str, dict] = {}
    if n_flux >= 1:
        flux_models["C"] = fit_constant_flux(track.t, track.flux, track.flux_err)
    if n_flux >= 4:
        fmin = 1.0 / max(600.0, 4 * (track.t.max() - track.t.min() + 1))
        sin = fit_sinusoid(track.t[flux_ok], track.flux[flux_ok], track.flux_err[flux_ok], fmin=fmin, fmax=1 / 0.5)
        if sin:
            flux_models["SIN"] = sin
        flux_models["TREND"] = _fit_flux_trend(track.t[flux_ok], track.flux[flux_ok], track.flux_err[flux_ok])

    # ---- 组合 BIC：位置 χ² + 流量 χ²，k 与 n 都相加
    combined = {}
    for pk, pm in pos_models.items():
        if not pm["ok"]:
            continue
        for fk, fm in flux_models.items():
            k = pm["k"] + int(fm.get("k", 0))
            ntot = pm["n"] + n_flux
            chi2 = pm["chi2"] + float(fm.get("chi2", 0.0))
            combined[f"{pk}+{fk}"] = {"bic": bic(chi2, k, max(ntot, 2)), "chi2": chi2, "k": k, "n": ntot}
    if not flux_models:
        # 多波段或流量不可比：只比较位置模型，不把流量塞进 χ²
        for pk, pm in pos_models.items():
            if pm["ok"]:
                combined[pk] = {
                    "bic": bic(pm["chi2"], pm["k"], max(pm["n"], 2)),
                    "chi2": pm["chi2"],
                    "k": pm["k"],
                    "n": pm["n"],
                }
    probs = model_probs({k: v["bic"] for k, v in combined.items()}) if combined else {}
    best_model = min(combined, key=lambda k: combined[k]["bic"]) if combined else None
    bics = {k: v["bic"] for k, v in combined.items()}

    # ---- 运动参数
    mu_mas = mu_xi = mu_eta = 0.0
    sig_mu_mas = sig_mu_xi = sig_mu_eta = float("nan")
    pa = float("nan")
    pm = pos_models.get("M2") or pos_models.get("M4")
    if pm and pm["ok"] and pm["coef_xi"] is not None and len(pm["coef_xi"]) >= 2:
        cxi, ceta = pm["coef_xi"], pm["coef_eta"]
        mu_xi, mu_eta = float(cxi[1]) * 1000.0, float(ceta[1]) * 1000.0
        sig_mu_xi = float(math.sqrt(max(pm["cov_xi"][1, 1], 0))) * 1000.0
        sig_mu_eta = float(math.sqrt(max(pm["cov_eta"][1, 1], 0))) * 1000.0
        mu_mas = float(math.hypot(mu_xi, mu_eta))
        sig_mu_mas = float(math.hypot(sig_mu_xi, sig_mu_eta))
        pa = float(math.degrees(math.atan2(mu_xi, mu_eta)) % 360.0)
    elif n == 2:
        # 两历元：用位移/基线给速率，不声称模型选择
        dt = baseline_years if baseline_years > 0 else 1.0
        mu_xi = float((track.xi[-1] - track.xi[0]) / dt) * 1000.0
        mu_eta = float((track.eta[-1] - track.eta[0]) / dt) * 1000.0
        mu_mas = float(math.hypot(mu_xi, mu_eta))
        sx = math.hypot(track.sigma_xi[-1], track.sigma_xi[0]) / dt * 1000.0
        sy = math.hypot(track.sigma_eta[-1], track.sigma_eta[0]) / dt * 1000.0
        sig_mu_mas = float(math.hypot(sx, sy))
        pa = float(math.degrees(math.atan2(mu_xi, mu_eta)) % 360.0)

    # ---- 首末位移
    if n >= 2:
        dxi = float(track.xi[-1] - track.xi[0])
        deta = float(track.eta[-1] - track.eta[0])
        disp = float(math.hypot(dxi, deta))
        sig_disp = float(math.hypot(track.sigma_xi[-1], track.sigma_xi[0], track.sigma_eta[-1], track.sigma_eta[0]))
        pa_disp = float(math.degrees(math.atan2(dxi, deta)) % 360.0)
    else:
        disp, sig_disp, pa_disp = 0.0, float("nan"), float("nan")

    # ---- 加速
    accel_sig = 0.0
    accel = 0.0
    if "M4" in pos_models and pos_models["M4"]["ok"] and n >= 4:
        c4 = pos_models["M4"]
        a2 = float(math.hypot(c4["coef_xi"][2], c4["coef_eta"][2]))
        sa = float(math.hypot(math.sqrt(max(c4["cov_xi"][2, 2], 0)), math.sqrt(max(c4["cov_eta"][2, 2], 0))))
        accel = a2
        accel_sig = a2 / sa if sa > 0 else 0.0

    # ---- 变光特征
    var = {"amp_mag": 0.0, "chi2_const": float("nan"), "period": None, "sin_sig": 0.0}
    if "C" in flux_models and n_flux >= 2:
        c = flux_models["C"]
        dof = max(n_flux - 1, 1)
        var["chi2_const"] = c["chi2"] / dof
        f = track.flux[flux_ok]
        if np.all(f > 0):
            var["amp_mag"] = float(2.5 * math.log10(np.max(f) / np.min(f)) / 2)
    if "SIN" in flux_models:
        s = flux_models["SIN"]
        cchi2 = flux_models["C"]["chi2"] if "C" in flux_models else 0.0
        var["sin_sig"] = max(0.0, float(cchi2) - float(s["chi2"]))
        var["period"] = s["period"]
        baseline_days = float(track.t[-1] - track.t[0])
        dt = np.diff(np.sort(np.asarray(track.t, float)))
        med_dt = float(np.median(dt)) if len(dt) else float("nan")
        too_short = np.isfinite(med_dt) and s["period"] < 2.0 * med_dt  # 采样不足以分辨
        too_long = baseline_days < 1.3 * s["period"]                # 基线不足 1.3 个周期
        if too_short or too_long:
            var["period"] = None
        var["sin_amp"] = s["amp"]
        if s["f0"] > 0 and s["amp"] < s["f0"]:
            var["sin_amp_mag"] = float(2.5 * math.log10((s["f0"] + s["amp"]) / (s["f0"] - s["amp"])) / 2)

    # ---- 判定
    px_scale_arcsec = 6.15
    mov_signif = (abs(disp) / sig_disp) if (sig_disp and np.isfinite(sig_disp) and sig_disp > 0) else 0.0
    mu_signif = (mu_mas / sig_mu_mas) if (sig_mu_mas and np.isfinite(sig_mu_mas) and sig_mu_mas > 0) else 0.0
    moving = bool(
        (n == 2 and mov_signif >= 5.0)
        or (n >= 3 and mu_signif >= 3.0 and abs(mu_mas) * baseline_years / 1000.0 > 0.3 * px_scale_arcsec)
    )
    variable = bool(
        (n_flux >= 3 and np.isfinite(var["chi2_const"]) and var["chi2_const"] > 6.0 and var["amp_mag"] > 0.3)
        or (var.get("sin_sig", 0) > 20.0 and var.get("period") and var.get("sin_amp_mag", 0) > 0.3)
    )
    m2_key = "M2+C" if "M2+C" in bics else ("M2" if "M2" in bics else None)
    m4_key = "M4+C" if "M4+C" in bics else ("M4" if "M4" in bics else None)
    delta_bic_accel = (bics[m2_key] - bics[m4_key]) if (m2_key and m4_key) else 0.0
    delta_bic_var = (bics.get("M0+C", 0.0) - bics.get("M0+SIN", 0.0)) if ("M0+C" in bics and "M0+SIN" in bics) else 0.0
    blend = _blend_check(track.t, track.xi, track.eta, track.sigma_xi, track.sigma_eta, track.flux, flux_ok)
    blend_downgraded = False
    cls_hint = "U"
    if blend["flag"] and blend["motion_sig"] < 3.0 and moving:
        moving = False
        blend_downgraded = True
        cls_hint = "V" if variable else "U"

    if n < 2:
        cls, conf = "U", 0.2
    elif blend_downgraded:
        cls = cls_hint
        conf = 0.55
    elif not moving:
        if variable:
            cls = "V"
            conf = float(min(0.97, 0.55 + 0.02 * min(var["chi2_const"], 30)))
        else:
            cls = "S"
            conf = 0.72 if n >= 3 else 0.5
    else:
        if accel_sig > 4.0 and delta_bic_accel > 6.0:
            cls = "A"
            conf = float(min(0.95, 0.6 + 0.02 * accel_sig))
        else:
            cls = "L"
            conf = float(min(0.98, 0.5 + 0.05 * max(mov_signif, mu_signif)))

    # 数据就绪度
    if n <= 1:
        readiness = "L0"
    elif n == 2:
        readiness = "L1"
    else:
        readiness = "L2"
    if n >= 6 and baseline_years >= 0.6:
        readiness = "L3"

    lin_resid = _linearity(track)
    fingerprint = {
        "mu_mas_yr": mu_mas,
        "mu_err_mas_yr": sig_mu_mas,
        "pa_deg": pa if np.isfinite(pa) else None,
        "displacement_arcsec": disp,
        "displacement_err_arcsec": sig_disp,
        "pa_disp_deg": pa_disp if np.isfinite(pa_disp) else None,
        "linearity": lin_resid,
        "accel_arcsec_yr2": accel,
        "accel_sig": accel_sig,
        "delta_bic_accel": delta_bic_accel,
        "amp_mag": var["amp_mag"],
        "period_d": var.get("period"),
        "var_chi2_red": var["chi2_const"],
        "delta_bic_var": delta_bic_var,
        "n_epochs": n,
        "baseline_days": float(track.t[-1] - track.t[0]) if n else 0.0,
        "scale_arcsec_px": px_scale_arcsec,
        "mu_px_yr": mu_mas / 1000.0 / px_scale_arcsec,
        "mov_signif": mov_signif,
        "mu_signif": mu_signif,
    }

    models = [
        {"id": name, "bic": float(v["bic"]), "chi2": float(v["chi2"]), "k": int(v["k"]), "prob": float(probs.get(name, 0.0))}
        for name, v in sorted(combined.items(), key=lambda kv: kv[1]["bic"])
    ]

    result = {
        "class": cls,
        "confidence": conf,
        "readiness": readiness,
        "fingerprint": fingerprint,
        "models": models,
        "best_model": best_model,
        "mu_mas_yr": mu_mas,
        "mu_err_mas_yr": sig_mu_mas,
        "pa_deg": pa if np.isfinite(pa) else None,
        "moving": bool(moving),
        "variable": bool(variable),
        "detected_epochs": int(n),
        "baseline_days": float(track.t[-1] - track.t[0]) if n else 0.0,
        "n_boot": 0,
    }
    result["blend_check"] = blend
    if blend["flag"] and not blend_downgraded:
        result["quality"] = list(dict.fromkeys((result.get("quality") or []) + ["BLEND_SUSPECTED"]))
        result["caveats"] = list(result.get("caveats") or []) + [
            "位置序列与流量相关（Δχ²=%.1f）：可能有邻星混叠，运动结论保留，但质心可能受影响。" % blend["delta_chi2"]
        ]
    if blend_downgraded:
        result["quality"] = list(dict.fromkeys((result.get("quality") or []) + ["BLEND_INDUCED_WANDER"]))
        result["caveats"] = list(result.get("caveats") or []) + [
            "位置趋势与流量显著相关（Δχ²=%.1f），且扣除流量项后自行不显著；"
            "判断为邻星混叠导致的质心漂移，不是真实移动。" % blend["delta_chi2"]
        ]
    result["multi_band"] = bool(multi_band)
    result["bands"] = bands
    if multi_band:
        result.setdefault("caveats", []).append("多波段历元：流量不可直接比较，未评估变光；天体测量仍有效。")
    result["evidence"] = _evidence(result)
    result["falsifier"] = _falsifier(result)
    result["label_zh"] = CLASS_LABELS.get(cls, "不可判定")
    result["public_text"] = public_text(result, ra0, dec0)
    # 物理合理性：恒星/褐矮星的自行先验 < 30″/yr；超过就说明很可能关联错了
    if result["class"] == "L" and abs(mu_mas) > 30000 and n >= 3:
        result["quality"] = list(dict.fromkeys((result.get("quality") or []) + ["IMPLAUSIBLE_PM"]))
        result["confidence"] = min(result["confidence"], 0.3)
        result["caveats"] = list(result.get("caveats") or []) + [
            f"拟合自行 {mu_mas/1000:.1f}″/yr 超过恒星先验上限（30″/yr），极可能是跨历元关联错误，结论降级。"
        ]
        result["class"] = "U"
        result["label_zh"] = "不可判定（关联可疑）"
        result["public_text"] = (
            f"拟合出的自行 {mu_mas/1000:.1f} 角秒/年 远超恒星/褐矮星的物理上限，"
            "很可能是把不同历元里的不同天体串成了一条轨迹。这个结果不可信，需要人工核对。"
        )
        result["evidence"] = _evidence(result)
        result["falsifier"] = _falsifier(result)
    return result


def _fit_flux_trend(t, f, ef):
    ok = np.isfinite(f) & np.isfinite(ef) & (ef > 0)
    if ok.sum() < 3:
        return {"chi2": float("inf"), "k": 2}
    t2 = (t[ok] - t[ok][0]) / max(t[ok][-1] - t[ok][0], 1e-9)
    c, cov, chi2, dof = weighted_polyfit(t2, f[ok], ef[ok], 1)
    return {"chi2": chi2, "k": 2, "coef": c}


def _linearity(track: Track) -> float:
    """直线拟合残差 RMS / 位移幅度；越小越直。"""
    if len(track) < 3:
        return float("nan")
    t = (track.t - track.t[0]) / YEAR
    res = []
    for y, s in ((track.xi, track.sigma_xi), (track.eta, track.sigma_eta)):
        c, cov, chi2, dof = _wls(np.stack([np.ones_like(t), t], axis=1), y, s)
        if c is None:
            return float("nan")
        res.append(np.sqrt(np.mean((y - (c[0] + c[1] * t)) ** 2)))
    rms = float(math.hypot(*res))
    amp = float(math.hypot(track.xi[-1] - track.xi[0], track.eta[-1] - track.eta[0]))
    return rms / amp if amp > 0 else float("inf")


CLASS_LABELS = {
    "S": "静止（无明显位移）",
    "V": "变光（位置不动）",
    "L": "匀速直线运动（自行）",
    "A": "加速或弯曲轨迹",
    "P": "视差（周年视运动）",
    "O": "轨道运动",
    "U": "不可判定（数据不足）",
}


def _evidence(r: dict) -> list[str]:
    fp = r["fingerprint"]
    ev = []
    if r["moving"]:
        ev.append(
            f"位移 {fp['displacement_arcsec']:.2f} ± {fp['displacement_err_arcsec']:.2f} 角秒，"
            f"方向 {fp['pa_disp_deg']:.0f}°（北起东向）"
        )
        ev.append(f"拟合自行 {fp['mu_mas_yr']:.0f} mas/yr（≈{fp['mu_px_yr']:.2f} 像素/年）")
    else:
        ev.append("位置变化与 0 相容（<5σ）")
    if fp["amp_mag"] > 0.1:
        ev.append(f"亮度变化幅度 {fp['amp_mag']:.2f} 等")
    if fp.get("period_d"):
        ev.append(f"周期候选 {fp['period_d']:.1f} 天")
    ev.append(f"有效历元 {fp['n_epochs']} 个，基线 {fp['baseline_days']:.0f} 天")
    return ev


def _falsifier(r: dict) -> str:
    if r["class"] == "V":
        return "若后续历元显示位置以 >5σ 显著移动，则变星判据失效，应改判为移动天体。"
    if r["class"] == "L":
        return "若新增历元显示轨迹显著弯曲（ΔBIC>10）或位移在更长基线下不成线性，则“匀速直线”结论被推翻。"
    if r["class"] == "A":
        return "若更多历元表明二次项不显著（<3σ），则退化为匀速直线。"
    return "若增加历元后位移或亮度变化变为 >5σ 显著，则“不可判定”应升级。"


def public_text(r: dict, ra0: float, dec0: float) -> str:
    fp = r["fingerprint"]
    if r["class"] == "V":
        return (
            f"这个目标在 {fp['n_epochs']} 次观测中位置基本不动（变化在误差范围内），"
            f"但亮度变化约 {fp['amp_mag']:.2f} 等。它是一颗变星，不是移动天体。"
        )
    if r["class"] == "L":
        return (
            f"在 {fp['baseline_days']:.0f} 天里，目标在天球上移动了 {fp['displacement_arcsec']:.2f} 角秒，"
            f"方向 {fp['pa_disp_deg']:.0f}°。这相当于每年 {fp['mu_mas_yr']/1000:.2f} 角秒 —— "
            f"是一条匀速直线，典型的高自行恒星或褐矮星。"
        )
    if r["class"] == "A":
        return (
            f"目标移动了 {fp['displacement_arcsec']:.2f} 角秒，而且轨迹明显弯曲（加速显著性 {fp['accel_sig']:.1f}σ）。"
            f"这更像太阳系天体或双星轨道，而不是直线自行。"
        )
    if r["class"] == "S":
        if fp["displacement_arcsec"] > 0.5 and fp["displacement_err_arcsec"] > 0:
            return (
                f"在 {fp['n_epochs']} 次观测中，目标的位移是 {fp['displacement_arcsec']:.2f} ± "
                f"{fp['displacement_err_arcsec']:.2f} 角秒，与 0 在 3σ 内相容；以当前数据还测不出可靠的自行。"
                f"它可能是一颗背景恒星或遥远星系，也可能是一颗移动很慢的天体。"
            )
        return (
            f"在 {fp['n_epochs']} 次观测中，目标的位置和亮度都没有显著变化。"
            f"它可能是一颗背景恒星或遥远星系；以当前数据还测不出运动。"
        )
    if r.get("detected_epochs") == 1:
        return (
            "只在 1 期里可靠地探测到目标，其余历元都没有达到探测阈值。"
            "这通常意味着目标太暗，或坐标与真实位置偏差较大。需要更深的数据，或先用星表核对坐标。"
        )
    return "数据不足以判断这个天体的运动规律。需要更多历元（至少 3 个，最好跨 6 个月）。"


# ------------------------------------------------------------------ 质量检查


def quality_flags(track: Track) -> list[str]:
    flags = []
    if track.saturated is not None and np.any(track.saturated):
        flags.append("SATURATED_SOURCE")
    if len(track) < 3:
        flags.append("FEW_EPOCHS")
    if len(track) and (track.t[-1] - track.t[0]) < 30:
        flags.append("SHORT_BASELINE")
    if len(track) and np.nanmax(track.sigma_xi) > 0.5:
        flags.append("LARGE_CENTROID_ERROR")
    return flags
