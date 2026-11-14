"""天球坐标与 SPHEREx WCS（TAN + SIP）实现。

约定：
- 像素坐标对外一律 **1-based**（FITS 约定）；数组索引由调用方转换。
- 角度单位：输入输出为度；切平面内部用弧度。
- 不使用 astropy，所有公式可在 `docs/01_理论设计编排.md` §4.1、§7.2 找到对应。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

ARCSEC = 180.0 / math.pi * 3600.0


def _f(h: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(h.get(key, default))
    except (TypeError, ValueError):
        return default


def gnomonic_forward(ra, dec, ra0, dec0):
    """天球 → 切平面标准坐标（弧度）。支持 numpy 数组。"""
    ra = np.deg2rad(np.asarray(ra, dtype=float))
    dec = np.deg2rad(np.asarray(dec, dtype=float))
    ra0 = math.radians(ra0)
    dec0 = math.radians(dec0)
    dra = ra - ra0
    denom = np.sin(dec) * math.sin(dec0) + np.cos(dec) * math.cos(dec0) * np.cos(dra)
    xi = np.cos(dec) * np.sin(dra) / denom
    eta = (np.sin(dec) * math.cos(dec0) - np.cos(dec) * math.sin(dec0) * np.cos(dra)) / denom
    return xi, eta


def gnomonic_inverse(xi, eta, ra0, dec0):
    """切平面标准坐标（弧度）→ 天球（度）。"""
    xi = np.asarray(xi, dtype=float)
    eta = np.asarray(eta, dtype=float)
    ra0 = math.radians(ra0)
    dec0 = math.radians(dec0)
    denom = np.cos(dec0) - eta * math.sin(dec0)
    ra = ra0 + np.arctan2(xi, denom)
    dec = np.arctan2(math.sin(dec0) + eta * math.cos(dec0), np.hypot(denom, xi))
    return np.rad2deg(ra) % 360.0, np.rad2deg(dec)


def angular_sep(ra1, dec1, ra2, dec2) -> float:
    """两点角距（度），小角下稳定。"""
    xi, eta = gnomonic_forward(ra1, dec1, ra2, dec2)
    return float(np.hypot(xi, eta) * 180.0 / math.pi)


@dataclass
class WCS:
    crval1: float
    crval2: float
    crpix1: float
    crpix2: float
    cd: np.ndarray  # 2x2, deg/pixel
    sip_a: dict = field(default_factory=dict)
    sip_b: dict = field(default_factory=dict)
    sip_ap: dict = field(default_factory=dict)
    sip_bp: dict = field(default_factory=dict)
    a_order: int = 0
    b_order: int = 0
    ctype: str = "TAN"
    radesys: str = "ICRS"
    naxis1: int = 0
    naxis2: int = 0

    # ---------- 构造 ----------
    @classmethod
    def from_header(cls, h: dict) -> "WCS":
        cdelt1 = _f(h, "CDELT1", 1.0)
        cdelt2 = _f(h, "CDELT2", 1.0)
        if "CD1_1" in h or "CD1_2" in h:
            cd = np.array([[_f(h, "CD1_1"), _f(h, "CD1_2")], [_f(h, "CD2_1"), _f(h, "CD2_2")]])
        else:
            pc = np.array([[_f(h, "PC1_1", 1.0), _f(h, "PC1_2")], [_f(h, "PC2_1"), _f(h, "PC2_2", 1.0)]])
            cd = pc * np.array([[cdelt1], [cdelt2]])
        sip_a, sip_ap = _sip_coeffs(h, "A")
        sip_b, sip_bp = _sip_coeffs(h, "B")
        a_order = int(_f(h, "A_ORDER", 0))
        b_order = int(_f(h, "B_ORDER", 0))
        if "AP_ORDER" in h:
            sip_ap, _ = _sip_coeffs(h, "AP")
        if "BP_ORDER" in h:
            sip_bp, _ = _sip_coeffs(h, "BP")
        ctype = str(h.get("CTYPE1", "RA---TAN")).strip().strip("'")
        return cls(
            crval1=_f(h, "CRVAL1"),
            crval2=_f(h, "CRVAL2"),
            crpix1=_f(h, "CRPIX1", 1.0),
            crpix2=_f(h, "CRPIX2", 1.0),
            cd=cd,
            sip_a=sip_a,
            sip_b=sip_b,
            sip_ap=sip_ap,
            sip_bp=sip_bp,
            a_order=a_order,
            b_order=b_order,
            ctype=ctype,
            radesys=str(h.get("RADESYS", "ICRS")).strip().strip("'"),
            naxis1=int(_f(h, "NAXIS1", 0)),
            naxis2=int(_f(h, "NAXIS2", 0)),
        )

    # ---------- 基本量 ----------
    @property
    def cd_inv(self) -> np.ndarray:
        return np.linalg.inv(self.cd)

    def pixel_scale_arcsec(self) -> float:
        return math.sqrt(abs(np.linalg.det(self.cd))) * 3600.0

    def _sip(self, u, v):
        """返回 SIP 修正量 (du, dv)。"""
        du = np.zeros_like(u, dtype=float)
        dv = np.zeros_like(v, dtype=float)
        for (p, q), a in self.sip_a.items():
            du = du + a * (u ** p) * (v ** q)
        for (p, q), b in self.sip_b.items():
            dv = dv + b * (u ** p) * (v ** q)
        return du, dv

    # ---------- 正变换：像素 → 天球 ----------
    def pixel_to_world(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        u = x - self.crpix1
        v = y - self.crpix2
        du, dv = self._sip(u, v)
        up, vp = u + du, v + dv
        xi = np.deg2rad(self.cd[0, 0] * up + self.cd[0, 1] * vp)
        eta = np.deg2rad(self.cd[1, 0] * up + self.cd[1, 1] * vp)
        return gnomonic_inverse(xi, eta, self.crval1, self.crval2)

    # ---------- 逆变换：天球 → 像素 ----------
    def world_to_pixel(self, ra, dec, tol=1e-10, maxiter=20):
        ra = np.asarray(ra, dtype=float)
        dec = np.asarray(dec, dtype=float)
        xi, eta = gnomonic_forward(ra, dec, self.crval1, self.crval2)
        # 线性部分先给初值
        uv = np.rad2deg(np.stack([xi, eta], axis=0)).reshape(2, -1)
        lin = self.cd_inv @ uv
        up = lin[0].reshape(np.shape(xi))
        vp = lin[1].reshape(np.shape(xi))
        u, v = up.copy(), vp.copy()
        for _ in range(maxiter):
            du, dv = self._sip(u, v)
            u_new = up - du
            v_new = vp - dv
            if np.max(np.abs(u_new - u)) < tol and np.max(np.abs(v_new - v)) < tol:
                u, v = u_new, v_new
                break
            u, v = u_new, v_new
        return u + self.crpix1, v + self.crpix2

    # ---------- 切平面 ----------
    def sky_to_tangent_arcsec(self, ra, dec, ra0=None, dec0=None):
        ra0 = self.crval1 if ra0 is None else ra0
        dec0 = self.crval2 if dec0 is None else dec0
        xi, eta = gnomonic_forward(ra, dec, ra0, dec0)
        return xi * ARCSEC, eta * ARCSEC

    def tangent_arcsec_to_sky(self, xi, eta, ra0, dec0):
        return gnomonic_inverse(np.asarray(xi) / ARCSEC, np.asarray(eta) / ARCSEC, ra0, dec0)

    # ---------- 视场 ----------
    def footprint(self) -> list[tuple[float, float]]:
        if self.naxis1 <= 0 or self.naxis2 <= 0:
            return []
        corners = [(1, 1), (self.naxis1, 1), (self.naxis1, self.naxis2), (1, self.naxis2)]
        ra, dec = self.pixel_to_world(np.array([c[0] for c in corners]), np.array([c[1] for c in corners]))
        return list(zip(np.atleast_1d(ra).tolist(), np.atleast_1d(dec).tolist()))

    def contains(self, ra: float, dec: float, margin_px: float = 0.0) -> bool:
        x, y = self.world_to_pixel(ra, dec)
        return bool(
            -margin_px <= float(x) <= self.naxis1 + margin_px
            and -margin_px <= float(y) <= self.naxis2 + margin_px
        )


def _sip_coeffs(h: dict, prefix: str) -> tuple[dict, dict]:
    coeffs = {}
    for k, v in h.items():
        if not k.startswith(prefix + "_") or k in (prefix + "_ORDER",):
            continue
        parts = k.split("_")
        if len(parts) != 3:
            continue
        try:
            p, q = int(parts[1]), int(parts[2])
            coeffs[(p, q)] = float(v)
        except (ValueError, TypeError):
            continue
    return coeffs, {}


def offset_from_full_frame(cutout_crpix: float, full_crpix: float = 1020.5) -> float:
    """切片相对于全幅的像素偏移（0-based）。

    SPHEREx level2 全幅的 CRPIX=1020.5（实测 D2/D6）。切片服务把 CRPIX 平移了
    -(x0)，因此 x0 = full_crpix - cutout_crpix。
    """
    return full_crpix - cutout_crpix


def wavelength_at_pixel(wave_grid: dict, x_full, y_full):
    """在 WCS-WAVE 控制点网格上双线性插值 λ（µm）。"""
    if not wave_grid:
        return np.full(np.shape(x_full), np.nan)
    gx = np.asarray(wave_grid["x"], dtype=float)
    gy = np.asarray(wave_grid["y"], dtype=float)
    lam = np.asarray(wave_grid["wavelength"], dtype=float)  # (ny, nx)
    x = np.clip(np.asarray(x_full, dtype=float), gx[0], gx[-1])
    y = np.clip(np.asarray(y_full, dtype=float), gy[0], gy[-1])
    ix = np.clip(np.searchsorted(gx, x) - 1, 0, len(gx) - 2)
    iy = np.clip(np.searchsorted(gy, y) - 1, 0, len(gy) - 2)
    fx = (x - gx[ix]) / (gx[ix + 1] - gx[ix])
    fy = (y - gy[iy]) / (gy[iy + 1] - gy[iy])
    return (
        lam[iy, ix] * (1 - fx) * (1 - fy)
        + lam[iy, ix + 1] * fx * (1 - fy)
        + lam[iy + 1, ix] * (1 - fx) * fy
        + lam[iy + 1, ix + 1] * fx * fy
    )
