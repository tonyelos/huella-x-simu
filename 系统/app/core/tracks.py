"""跨历元 tracklet 关联与 D0–D4 分级（D 阶段发现层）。

输入是每历元 cascade_layers 的 L1 + residual 节点；纯函数不访问网络/DB。
D1 的星表匹配由调用方通过 ``catalog_lookup`` 注入（API 层调 B），
本模块不伪造 known，也不输出发现声明。
"""
from __future__ import annotations

import math

import numpy as np

TRACK_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_SEP_PX = 3.0
DEFAULT_MAX_GAP = 1
DEFAULT_MAX_CATALOG_CHECKS = 20
DEFAULT_PIXEL_SCALE_ARCSEC = 6.15

ARTIFACT_QUALITY = {"EDGE", "SATURATED", "LOW_SNR", "DUPLICATE", "PSF_ABNORMAL"}
LEVELS = ("D0", "D1", "D2", "D3", "D4")

DISCLAIMER = (
    "轨迹候选不是新天体；D5 才需要人工/后续确认。"
    "D0–D4 只是候选分级，不构成发现声明。"
)


def _finite(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _quality(node) -> list[str]:
    q = node.get("quality")
    return [str(x) for x in q] if isinstance(q, list) else []


def _valid_node(node) -> bool:
    if not isinstance(node, dict):
        return False
    return _finite(node.get("x")) is not None and _finite(node.get("y")) is not None


def _norm_node(node: dict, epoch: dict) -> dict:
    return {
        "source_id": str(node.get("source_id") or ""),
        "epoch_id": str(epoch.get("epoch_id") or epoch.get("obsid") or ""),
        "mjd": _finite(node.get("mjd"), _finite(epoch.get("mjd"))),
        "stage": str(node.get("stage") or "l1"),
        "x": _finite(node.get("x")),
        "y": _finite(node.get("y")),
        "common_x": _finite(node.get("common_x")),
        "common_y": _finite(node.get("common_y")),
        "ra": _finite(node.get("ra")),
        "dec": _finite(node.get("dec")),
        "snr": _finite(node.get("snr"), 0.0),
        "flux": _finite(node.get("flux"), 0.0),
        "flux_err": _finite(node.get("flux_err"), 0.0),
        "quality": _quality(node),
        "parent": node.get("parent"),
        "masked_by": node.get("masked_by"),
        "reason": node.get("reason"),
    }


def _pixel_scale(epoch: dict) -> float:
    return _finite(epoch.get("pixel_scale_arcsec"), DEFAULT_PIXEL_SCALE_ARCSEC) or DEFAULT_PIXEL_SCALE_ARCSEC


def _distance(a: dict, b: dict) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _associate(epochs: list[dict], max_sep_px: float, max_gap: int) -> list[dict]:
    """按时间逐历元贪心最近邻；允许 track 缺 1 期；节点不复用。"""
    tracks: list[dict] = []
    active: list[dict] = []

    for ei, epoch in enumerate(epochs):
        nodes = [n for n in (_norm_node(x, epoch) for x in epoch.get("nodes", [])) if _valid_node(n)]
        # 同一历元按 S/N 降序，确定性
        nodes.sort(key=lambda n: (-(n["snr"] or 0.0), n["source_id"]))
        used_nodes: set[int] = set()
        claimed_tracks: set[int] = set()

        # 关闭 gap 已经超过 max_gap 的 track
        for tr in active:
            if ei - tr["last_idx"] - 1 > max_gap:
                tr["closed"] = True
        active = [t for t in active if not t["closed"]]

        pairs = []
        for ti, tr in enumerate(active):
            if tr["closed"]:
                continue
            gap = ei - tr["last_idx"] - 1
            if gap < 0 or gap > max_gap:
                continue
            gate = max_sep_px * (1.0 + 0.5 * gap)
            last = tr["nodes"][-1]
            for ni, node in enumerate(nodes):
                d = _distance(last, node)
                if d <= gate:
                    pairs.append((d, ti, ni))
        pairs.sort(key=lambda p: p[0])

        for _d, ti, ni in pairs:
            if ti in claimed_tracks or ni in used_nodes:
                continue
            tr = active[ti]
            tr["nodes"].append(nodes[ni])
            tr["last_idx"] = ei
            claimed_tracks.add(ti)
            used_nodes.add(ni)

        # 未匹配节点各自起一条 track
        for ni, node in enumerate(nodes):
            if ni in used_nodes:
                continue
            tr = {"nodes": [node], "last_idx": ei, "closed": False}
            tracks.append(tr)
            active.append(tr)

    return tracks


def _fit_poly(ts: np.ndarray, ys: np.ndarray, sigma: np.ndarray, order: int):
    """加权多项式拟合；返回 coef, chi2, cov。order: 0/1/2。"""
    n = len(ts)
    if n == 0:
        return None, float("inf"), None
    if order == 0:
        A = np.ones((n, 1))
    elif order == 1:
        A = np.stack([np.ones(n), ts], axis=1)
    else:
        A = np.stack([np.ones(n), ts, ts * ts], axis=1)
    if n <= order:
        return None, float("inf"), None
    w = 1.0 / np.maximum(sigma, 1e-6)
    Aw = A * w[:, None]
    yw = ys * w
    try:
        coef, *_ = np.linalg.lstsq(Aw, yw, rcond=None)
        resid = ys - A @ coef
        chi2 = float(np.sum((resid / np.maximum(sigma, 1e-6)) ** 2))
        cov = np.linalg.inv(Aw.T @ Aw)
    except (np.linalg.LinAlgError, ValueError):
        return None, float("inf"), None
    return coef, chi2, cov


def _bic(chi2: float, k: int, n: int) -> float:
    return float(chi2 + k * math.log(max(n, 2)))


def _flux_variable(nodes: list[dict]) -> bool:
    if len(nodes) < 3:
        return False
    fluxes = np.array([n["flux"] for n in nodes], float)
    errs = np.array([max(n["flux_err"], 1e-9) for n in nodes], float)
    if not np.all(np.isfinite(fluxes)) or np.allclose(fluxes, 0):
        return False
    w = 1.0 / errs**2
    mean = float(np.sum(fluxes * w) / max(np.sum(w), 1e-12))
    chi2 = float(np.sum(((fluxes - mean) / errs) ** 2))
    dof = max(len(nodes) - 1, 1)
    return chi2 / dof > 9.0


def _model_and_motion(track: dict, total_epochs: int) -> dict:
    nodes = track["nodes"]
    n = len(nodes)
    t0 = min(float(node["mjd"]) for node in nodes if node["mjd"] is not None) if nodes else 0.0
    ts = np.array([(float(node["mjd"]) - t0) / 365.25 for node in nodes], float)
    xs = np.array([float(node["x"]) for node in nodes], float)
    ys = np.array([float(node["y"]) for node in nodes], float)
    # 6.15″/px 在 4 期 tracklet 里不是主误差项；用 0.5 px 表示质心+像元系统项。
    sigma = np.full(n, 0.5, float)

    mu_arcsec_yr = 0.0
    pa_deg = None
    mu_sig = 0.0
    model = "transient" if n < 2 else "stationary"

    if n >= 2:
        _, chi_c, _ = _fit_poly(ts, xs, sigma, 0)
        coef_l, chi_l, cov_l = _fit_poly(ts, xs, sigma, 1)
        # y 方向独立拟合；模型显著性用两轴 chi2 联合
        _, chi_cy, _ = _fit_poly(ts, ys, sigma, 0)
        _, chi_ly, cov_ly = _fit_poly(ts, ys, sigma, 1)
        chi_c += chi_cy
        chi_l += chi_ly
        bic_c = _bic(chi_c, 2, n)
        bic_l = _bic(chi_l, 4, n)
        if coef_l is not None:
            vx, vy = float(coef_l[1]), float(coef_l[1])
            # x/y 分别取斜率；用 cov 对角估计速度显著性
            vx = float(coef_l[1]) if coef_l is not None else 0.0
            vy = 0.0
            try:
                vy = float(np.linalg.lstsq(np.stack([np.ones(n), ts], axis=1) * (1 / sigma)[:, None], ys * (1 / sigma), rcond=None)[0][1])
            except (np.linalg.LinAlgError, ValueError):
                vy = 0.0
            speed = math.hypot(vx, vy)
            # 速度误差：用两轴协方差近似
            svx = math.sqrt(max(cov_l[1, 1], 0.0)) if cov_l is not None else 0.0
            svy = math.sqrt(max(cov_ly[1, 1], 0.0)) if cov_ly is not None else 0.0
            mu_sig = speed / max(math.hypot(svx, svy), 1e-9) if speed > 0 else 0.0
            moving = (bic_c - bic_l > 6.0) and mu_sig >= 3.0
            if n >= 4 and moving:
                # 二次项：x/y 各拟合，ΔBIC 与加速度显著性
                cx, chi_qx, cov_qx = _fit_poly(ts, xs, sigma, 2)
                cy, chi_qy, cov_qy = _fit_poly(ts, ys, sigma, 2)
                chi_q = chi_qx + chi_qy
                bic_q = _bic(chi_q, 6, n)
                if bic_l - bic_q > 6.0:
                    accel = 0.0
                    try:
                        accel = math.hypot(2 * cx[2], 2 * cy[2])
                    except (TypeError, IndexError):
                        accel = 0.0
                    model = "curved" if accel > 0 else "linear"
                else:
                    model = "linear"
            elif moving:
                model = "linear"
            else:
                model = "variable" if _flux_variable(nodes) else "stationary"
            scale = DEFAULT_PIXEL_SCALE_ARCSEC
            mu_arcsec_yr = speed * scale
            pa_deg = round(math.degrees(math.atan2(vx, vy)) % 360.0, 2) if speed > 0 else None
    if n < 2:
        model = "transient"
    elif n < total_epochs - 1:
        # 缺 ≥2 期：不能当稳定轨迹，模型降级为 transient（但保留节点）
        model = "transient"
    if model == "stationary" and _flux_variable(nodes):
        model = "variable"
    if model not in ("linear", "curved"):
        # 静止/变光/暂现 track 不报告有意义的 μ/PA，避免 0 速度但 PA=45° 的假精度。
        mu_arcsec_yr = 0.0
        pa_deg = None
        mu_sig = 0.0

    ra = None
    dec = None
    ras = [float(x["ra"]) for x in nodes if x.get("ra") is not None]
    decs = [float(x["dec"]) for x in nodes if x.get("dec") is not None]
    if ras:
        ra = float(np.median(ras))
    if decs:
        dec = float(np.median(decs))
    return {
        "model": model,
        "n_epochs": n,
        "ra": ra,
        "dec": dec,
        "mu_arcsec_yr": round(float(mu_arcsec_yr), 3),
        "pa_deg": pa_deg,
        "mu_significance": round(float(mu_sig), 2),
    }


def _caveats(track: dict, total_epochs: int) -> list[str]:
    nodes = track["nodes"]
    n = len(nodes)
    out = []
    if n < total_epochs:
        out.append(f"missing {total_epochs - n} epoch(s)")
    if n <= 4:
        out.append("4 epochs only; tracklet association is ambiguous")
    if n < 3:
        out.append("insufficient_epochs")
    if all(str(x.get("stage")) == "residual" for x in nodes):
        out.append("residual-only track")
    return out


def _base_level(track: dict) -> str:
    nodes = track["nodes"]
    if any(any(q in ARTIFACT_QUALITY for q in node.get("quality", [])) for node in nodes):
        return "D0"
    if track["model"] in ("linear", "curved"):
        return "D3"
    return "D4"


def _make_track(track: dict, idx: int, total_epochs: int) -> dict:
    model = _model_and_motion(track, total_epochs)
    level = _base_level({**track, **model})
    nodes = []
    for n in track["nodes"]:
        nodes.append(
            {
                "source_id": n["source_id"],
                "epoch_id": n["epoch_id"],
                "mjd": n["mjd"],
                "stage": n["stage"],
                "x": n["x"],
                "y": n["y"],
                "common_x": n.get("common_x"),
                "common_y": n.get("common_y"),
                "ra": n["ra"],
                "dec": n["dec"],
                "snr": n["snr"],
                "flux": n["flux"],
                "flux_err": n["flux_err"],
                "quality": n["quality"],
                "parent": n.get("parent"),
                "masked_by": n.get("masked_by"),
                "reason": n.get("reason"),
            }
        )
    confidence = 0.2 + 0.12 * len(nodes)
    if level == "D0":
        confidence = 0.1
    elif level == "D1":
        confidence = 0.85
    return {
        "track_id": f"trk-{idx:04d}",
        "level": level,
        "model": model["model"],
        "n_epochs": model["n_epochs"],
        "ra": model["ra"],
        "dec": model["dec"],
        "mu_arcsec_yr": model["mu_arcsec_yr"],
        "pa_deg": model["pa_deg"],
        "mu_significance": model["mu_significance"],
        "catalog": "none",
        "catalog_id": None,
        "catalog_status": "not_checked",
        "confidence": round(min(confidence, 0.85), 3),
        "caveats": _caveats(track, total_epochs),
        "nodes": nodes,
        "discovery_claim": False,
    }


def tracklets(
    epochs: list[dict],
    *,
    max_sep_px: float = DEFAULT_MAX_SEP_PX,
    max_gap: int = DEFAULT_MAX_GAP,
    catalog_lookup=None,
    max_catalog_checks: int = DEFAULT_MAX_CATALOG_CHECKS,
) -> list[dict]:
    """把每历元 L1/residual 节点关联成 tracklet，并给 D0–D4 初始层级。

    ``epochs``: ``[{"epoch_id":..., "mjd":..., "nodes":[...]}]``
    ``catalog_lookup``: 可选回调 ``(track) -> {"status": "known|unavailable|partial|ambiguous|none", ...}``
    """
    if not epochs:
        return []
    eps = []
    for ep in epochs:
        if not isinstance(ep, dict):
            continue
        nodes = [n for n in (ep.get("nodes") or []) if isinstance(n, dict) and _valid_node(n)]
        # 空历元要保留：它代表“这一期没有节点”，而不是时间轴不存在。
        eps.append({"epoch_id": ep.get("epoch_id"), "mjd": ep.get("mjd"), "nodes": nodes, "pixel_scale_arcsec": ep.get("pixel_scale_arcsec")})
    if not eps:
        return []
    eps.sort(key=lambda e: (_finite(e.get("mjd"), float("inf")), str(e.get("epoch_id"))))
    total_epochs = len(eps)
    raw = _associate(eps, float(max_sep_px), int(max_gap))
    tracks = [_make_track(t, i + 1, total_epochs) for i, t in enumerate(raw)]

    # D1：只对非 D0、至少 2 期的 track 做有限次星表查询。
    checked = 0
    for tr in sorted(tracks, key=lambda x: (-x["n_epochs"], -x["confidence"])):
        if tr["level"] == "D0" or tr["n_epochs"] < 2:
            continue
        if checked >= max(0, int(max_catalog_checks)):
            tr["catalog_status"] = "lookup_limit"
            tr["caveats"].append("catalog lookup limit; not checked")
            continue
        if catalog_lookup is None:
            continue
        checked += 1
        try:
            info = catalog_lookup(tr) or {}
        except Exception as exc:  # noqa: BLE001 - 星表失败不能拖垮轨迹
            info = {"status": "unavailable", "detail": str(exc)}
        status = str(info.get("status") or "none")
        if status == "known":
            tr["level"] = "D1"
            tr["catalog"] = str(info.get("catalog") or info.get("release") or "gaia-dr3")
            tr["catalog_id"] = str(info.get("source_id") or info.get("catalog_id") or "")
            tr["catalog_status"] = "ok"
            tr["confidence"] = 0.85
            if info.get("caveat"):
                tr["caveats"].append(str(info["caveat"]))
        elif status == "unavailable":
            tr["catalog"] = "unavailable"
            tr["catalog_status"] = "unavailable"
            tr["caveats"].append("catalog unavailable; no known/unknown conclusion")
        elif status == "partial":
            tr["catalog"] = "partial"
            tr["catalog_status"] = "partial"
            tr["caveats"].append("catalog partial; D1 not assigned")
        elif status == "ambiguous":
            tr["catalog"] = "ambiguous"
            tr["catalog_status"] = "ambiguous"
            tr["caveats"].append("multiple catalog candidates; no hard assignment")
        elif status == "none":
            tr["catalog"] = "none"
            tr["catalog_status"] = "ok"
        if status != "known" and tr["level"] == "D0":
            tr["level"] = "D0"

    tracks.sort(key=lambda t: (LEVELS.index(t["level"]), -t["n_epochs"], t["track_id"]))
    return tracks


def counts(tracks: list[dict]) -> dict:
    out = {level: 0 for level in LEVELS}
    for tr in tracks or []:
        lv = tr.get("level")
        if lv in out:
            out[lv] += 1
    return out
