"""星表先验 motion 后处理（通用规则，不针对目标名）。

当 SPHEREx 的短基线测量无法显著测出运动（class=S），但 Gaia 给出显著、
与实测 μ 相容的高自行时，把结果标注为 **L(catalog prior)**：
- 不修改实测 μ / μ 误差 / moving / μ 显著性；
- 只增加 catalog_motion 与明确的 prior 文案；
- 不输出“发现/确认”。
"""
from __future__ import annotations

import math

APPLY_QUALITY = ("CATALOG_PRIOR_MOTION", "MEASUREMENT_NOT_SIGNIFICANT")
PRIOR_LABEL = "L · Gaia 先验（SPHEREx 未独立测出）"
PRIOR_TEXT = (
    "SPHEREx 本次未独立测出该目标的运动；采用 Gaia PM 先验判为 L 候选。"
    "这是星表先验，不是 SPHEREx 实测自行，也不是发现/确认。"
)
PRIOR_CAVEAT = (
    "class=L 来自 Gaia PM 先验；SPHEREx 的 μ 测量不显著，未把星表 PM 伪装成实测运动。"
)


def _finite(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _is_gaia(candidate: dict) -> bool:
    text = f"{candidate.get('catalog', '')} {candidate.get('release', '')}".lower()
    return "gaia" in text


def _catalog_motion(result: dict, identification: dict | None) -> dict:
    """从 identification 候选中取 Gaia PM；不修改 result。"""
    motion = {
        "pm_mas_yr": None,
        "pm_err_mas_yr": None,
        "basis": "none",
        "consistent_with_measurement": None,
        "measurement_significance": None,
        "catalog_id": None,
    }
    measured = _finite((result or {}).get("mu_mas_yr"))
    measured_err = _finite((result or {}).get("mu_err_mas_yr"))
    sig = _finite(((result or {}).get("fingerprint") or {}).get("mu_signif"))
    if sig is None and measured is not None and measured_err and measured_err > 0:
        sig = abs(measured) / measured_err
    motion["measurement_significance"] = sig

    if not identification or identification.get("status") not in ("known_star", "ambiguous"):
        return motion
    candidates = [
        c for c in (identification.get("candidates") or [])
        if isinstance(c, dict) and _is_gaia(c)
    ]
    if not candidates:
        return motion
    # ambiguous 时取角距最小候选仅用于先验注记；不把它写成 confirmed identity。
    candidates.sort(key=lambda c: (_finite(c.get("separation_arcsec")) is None,
                                   _finite(c.get("separation_arcsec")) or float("inf")))
    cand = candidates[0]
    pmra = _finite(cand.get("pmra"))
    pmdec = _finite(cand.get("pmdec"))
    if pmra is None or pmdec is None:
        return motion
    pm = math.hypot(pmra, pmdec)
    pmra_err = _finite(cand.get("pmra_error"))
    pmdec_err = _finite(cand.get("pmdec_error"))
    pm_err = None
    if pmra_err is not None and pmdec_err is not None and pmra_err >= 0 and pmdec_err >= 0:
        pm_err = math.hypot(pmra_err, pmdec_err)
    motion.update(
        {
            "pm_mas_yr": pm,
            "pm_err_mas_yr": pm_err,
            "catalog_id": cand.get("catalog_id"),
        }
    )
    if measured is None or measured_err is None or measured_err <= 0 or pm_err is None or pm_err <= 0:
        return motion
    sigma = math.hypot(measured_err, pm_err)
    consistent = abs(measured - pm) <= 3.0 * sigma
    motion["consistent_with_measurement"] = bool(consistent)
    return motion


def apply_catalog_prior(result: dict, identification: dict | None) -> dict:
    """为 result 增加 catalog_motion；满足通用条件时把 S 标为 L(prior)。

    必须保留 result 原有 μ、μ_err、moving、fingerprint.mu_signif，
    以区分“星表先验”与“SPHEREx 实测运动”。
    """
    if not isinstance(result, dict):
        return {}
    motion = _catalog_motion(result, identification)
    result["catalog_motion"] = motion

    if result.get("class") != "S":
        return motion
    pm = motion.get("pm_mas_yr")
    pm_err = motion.get("pm_err_mas_yr")
    if pm is None or pm_err is None or pm <= 1000.0 or pm_err <= 0:
        return motion
    # 星表 PM 本身必须显著，避免把误差很大的 PM 当先验。
    if pm / pm_err < 5.0:
        return motion
    if motion.get("consistent_with_measurement") is not True:
        return motion

    result["class"] = "L"
    quality = list(result.get("quality") or [])
    for flag in APPLY_QUALITY:
        if flag not in quality:
            quality.append(flag)
    result["quality"] = quality
    result["confidence"] = min(_finite(result.get("confidence")) or 0.0, 0.6)
    result["label_zh"] = PRIOR_LABEL
    result["public_text"] = PRIOR_TEXT
    caveats = list(result.get("caveats") or [])
    if PRIOR_CAVEAT not in caveats:
        caveats.append(PRIOR_CAVEAT)
    result["caveats"] = caveats
    motion["basis"] = "catalog_prior"
    # 明确保留：moving=False 与实测 μ/显著性不变
    return motion
