"""D 阶段 MVP：星表客户端（B 服务）与目标源 known/unknown 分类。

设计依据：
- ``项目主控/架构/07_已知未知识别与数据比对设计.md`` §3–§5（D 语义）
- ``系统/数据库工具/docs/CATALOG.md`` §5.6–§5.8（B API 契约）

边界（与设计一致）：
- 只回答“分析目标这个检测源能否被官方星表解释”，不做行星确认，
  不输出“新恒星/新行星/发现”。
- B 不可达、超时、429/5xx、503 一律 ``catalog_unavailable``；
  绝不把失败降级成“未匹配/未收录”。
- PM 传播交给 B：请求里带检测 ``mjd``/``epoch`` 与位置误差；
  app 不重复做星表 PM 传播，只用 B 返回的传播后位置/角距。

状态枚举（MVP，目标源级别）::

    known_star / ambiguous / uncatalogued_candidate /
    catalog_unavailable / insufficient_data

其中 ``catalog_unavailable`` 是 MVP 把设计里的
``insufficient_data + reason=CATALOG_UNAVAILABLE`` 收拢成的显式状态，
方便 UI 与验收直接断言“星表不可用 ≠ 未匹配”。
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request

DEFAULT_CATALOG_BASE_URL = "http://127.0.0.1:8799"
DEFAULT_TIMEOUT_S = 2.5
DEFAULT_INGEST_TIMEOUT_S = 60.0
CATALOG = "gaia"
IDENTIFICATION_SCHEMA_VERSION = "1.0"
IDENTIFICATION_ENGINE_VERSION = "d-identify/0.1"
MAX_CANDIDATES = 5
# 一个 SPHEREx 像素（6.15″/px）。目标-星表偏移超过 1 px 时，
# 位置匹配不足以支撑 known/ambiguous；这是通用门限，不针对目标名。
CATALOG_OFFSET_LIMIT_ARCSEC = 6.15

IDENTIFICATION_STATUSES = (
    "known_star",
    "ambiguous",
    "uncatalogued_candidate",
    "catalog_unavailable",
    "insufficient_data",
)

# UI/API 统一文案：不出现“新恒星/新行星/发现”等正向断言。
LABELS_ZH = {
    "known_star": "Gaia DR3 已知",
    "ambiguous": "多候选 ambiguous",
    "uncatalogued_candidate": "未收录候选（未经确认）",
    "catalog_unavailable": "星表不可用",
    "insufficient_data": "数据不足",
}

DISCLAIMER = "未匹配不等于新天体；本工具只做已知/未知比对，不做发现声明。"

_CATALOG_STATUSES = ("ok", "empty", "partial", "stale", "unavailable")


def catalog_base_url() -> str:
    """B 服务地址；测试/部署可用 ``CATALOG_URL`` 覆盖（兼容旧变量名）。"""
    return (
        os.environ.get("CATALOG_URL")
        or os.environ.get("MOVESCOPE_CATALOG_URL")
        or os.environ.get("CATALOG_BASE_URL")
        or DEFAULT_CATALOG_BASE_URL
    ).strip().rstrip("/")


def catalog_timeout_s() -> float:
    """B 请求超时（秒）。星表是增强信息，不能长时间阻塞原分析。"""
    raw = os.environ.get("MOVESCOPE_CATALOG_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return value if math.isfinite(value) and value > 0 else DEFAULT_TIMEOUT_S


def ingest_timeout_s() -> float:
    """B ingest 的超时；TAP 可能较慢，默认 60s。"""
    raw = os.environ.get("MOVESCOPE_CATALOG_INGEST_TIMEOUT")
    if raw is None:
        return DEFAULT_INGEST_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_INGEST_TIMEOUT_S
    return value if math.isfinite(value) and value > 0 else DEFAULT_INGEST_TIMEOUT_S


def auto_ingest_enabled() -> bool:
    """是否允许因 field 未缓存自动触发 ingest；默认关闭。"""
    return str(os.environ.get("CATALOG_AUTO_INGEST", "")).strip().lower() in ("1", "true", "yes", "on")


def _finite(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _round(value, digits=4):
    out = _finite(value)
    return round(out, digits) if out is not None else None


def _candidate(raw: dict) -> dict | None:
    """把 B 的候选行收敛成 app 需要的稳定子集；catalog_id 永远是字符串。"""
    if not isinstance(raw, dict):
        return None
    catalog_id = raw.get("catalog_id")
    if catalog_id is None:
        # B 契约里 source_id 形如 ``gaia-dr3:447...``；只作为兜底，不编造。
        source_id = str(raw.get("source_id") or "")
        catalog_id = source_id.split(":", 1)[1] if ":" in source_id else (source_id or None)
    if catalog_id is not None:
        catalog_id = str(catalog_id)
    return {
        "catalog": str(raw.get("catalog") or CATALOG),
        "catalog_id": catalog_id,
        "source_id": catalog_id,
        "release": raw.get("release"),
        "separation_arcsec": _round(raw.get("sep_arcsec")),
        "ra": _round(raw.get("ra"), 9),
        "dec": _round(raw.get("dec"), 9),
        "ra_pred": _round(raw.get("ra_propagated"), 9),
        "dec_pred": _round(raw.get("dec_propagated"), 9),
        "pmra": _round(raw.get("pmra")),
        "pmdec": _round(raw.get("pmdec")),
        "pmra_error": _round(raw.get("pmra_error")),
        "pmdec_error": _round(raw.get("pmdec_error")),
        "parallax": _round(raw.get("parallax")),
        "parallax_error": _round(raw.get("parallax_error"), 6),
        "ruwe": _round(raw.get("ruwe"), 6),
        "astrometric_excess_noise": _round(raw.get("astrometric_excess_noise"), 6),
        "phot_variable_flag": raw.get("phot_variable_flag"),
        "gmag": _round(raw.get("gmag"), 6),
        "bp_rp": _round(raw.get("bp_rp"), 6),
        "jmag": _round(raw.get("jmag"), 6),
        "hmag": _round(raw.get("hmag"), 6),
        "kmag": _round(raw.get("kmag"), 6),
        "w1": _round(raw.get("w1"), 6),
        "w2": _round(raw.get("w2"), 6),
        "twomass_id": raw.get("twomass_id") or raw.get("2mass"),
        "wise_id": raw.get("wise_id") or raw.get("wise"),
        "object_type": raw.get("object_type"),
        "confidence": _round(raw.get("confidence"), 6),
        "pm_residual": _round(raw.get("pm_residual"), 3),
        "pa_residual": _round(raw.get("pa_residual"), 3),
        "ambiguous": bool(raw.get("ambiguous")),
        "n_within_ambiguity_arcsec": int(raw.get("n_within_ambiguity_arcsec") or 0),
    }


def _photometry(candidate: dict) -> dict | None:
    """只输出真实存在的 2MASS/WISE 数值/交叉证认，缺失不补 0/不猜测。"""
    keys = ("jmag", "hmag", "kmag", "w1", "w2")
    values = {k: candidate.get(k) for k in keys if candidate.get(k) is not None}
    twomass_id = candidate.get("twomass_id")
    wise_id = candidate.get("wise_id")
    if not values and not twomass_id and not wise_id:
        return None
    colors = {}
    if values.get("jmag") is not None and values.get("hmag") is not None:
        colors["j_h"] = round(float(values["jmag"]) - float(values["hmag"]), 3)
    if values.get("hmag") is not None and values.get("kmag") is not None:
        colors["h_k"] = round(float(values["hmag"]) - float(values["kmag"]), 3)
    if values.get("w1") is not None and values.get("w2") is not None:
        colors["w1_w2"] = round(float(values["w1"]) - float(values["w2"]), 3)
    out = {**values, "colors": colors}
    if twomass_id:
        out["twomass_id"] = str(twomass_id)
    if wise_id:
        out["wise_id"] = str(wise_id)
    return out


def _unavailable(reason: str, *, detail: str | None = None, http_status=None) -> dict:
    return {
        "ok": False,
        "catalog_status": "unavailable",
        "catalog": CATALOG,
        "release": None,
        "n_candidates": 0,
        "ambiguous": False,
        "candidates": [],
        "reason": "CATALOG_UNAVAILABLE",
        "catalog_reason": reason,
        "detail": detail,
        "http_status": http_status,
        "coverage": None,
        "catalog_schema_version": None,
        "persisted": False,
        "persisted_rows": 0,
    }


def normalize_match(raw: dict) -> dict:
    """把 B ``/api/v1/catalog/match`` 的响应收敛成客户端内部结果。

    关键不变量：**查询失败 ≠ 查询成功但 0 候选**。
    未知/缺字段的响应按 ``CATALOG_UNAVAILABLE`` 处理（保守优先）。
    """
    if not isinstance(raw, dict):
        return _unavailable("MALFORMED_RESPONSE", detail="响应不是 JSON 对象")
    if raw.get("unavailable"):
        return _unavailable(
            str(raw.get("reason") or raw.get("catalog_reason") or "CATALOG_UNAVAILABLE"),
            detail=raw.get("message"),
        )

    matches = raw.get("matches")
    if not isinstance(matches, list):
        return _unavailable("MALFORMED_RESPONSE", detail="响应缺少 matches 数组")

    match = matches[0] if matches else {}
    if not isinstance(match, dict):
        return _unavailable("MALFORMED_RESPONSE", detail="matches[0] 不是对象")

    catalog_status = match.get("catalog_status") or raw.get("catalog_status")
    if catalog_status not in _CATALOG_STATUSES:
        catalog_status = "partial"

    coverage = match.get("coverage") if isinstance(match.get("coverage"), dict) else {}
    # B 的 /match 在“查询点不在任何已缓存场覆盖内”时当前返回 partial+0 候选，
    # 不会返回 503。这里按 B 自己的 CATALOG.md 契约把它显式归为不可用，
    # 否则缓存缺失会被误读成“星表里没有”。
    if coverage and all(
        isinstance(cov, dict) and cov.get("field_id") is None for cov in coverage.values()
    ):
        return _unavailable(
            "NO_CACHED_FIELD",
            detail="查询点不在任何已缓存星表场内",
        )

    if catalog_status == "unavailable":
        return _unavailable(
            "CATALOG_UNAVAILABLE",
            detail=raw.get("message") or match.get("note"),
        )

    candidates = []
    for item in match.get("candidates") or []:
        cand = _candidate(item)
        if cand is not None:
            candidates.append(cand)

    release = match.get("release") or raw.get("release")
    if not release and isinstance(raw.get("releases"), list):
        for item in raw["releases"]:
            if isinstance(item, dict) and item.get("release"):
                release = item["release"]
                break

    persisted_rows = int(raw.get("persisted") or 0)
    return {
        "ok": True,
        "catalog_status": catalog_status,
        "catalog": CATALOG,
        "release": release,
        "catalog_schema_version": raw.get("star_schema_version") or raw.get("db_schema_version"),
        "n_candidates": len(candidates),
        "ambiguous": bool(match.get("ambiguous")) or len(candidates) >= 2,
        "candidates": candidates[:MAX_CANDIDATES],
        "reason": None,
        "catalog_reason": None,
        "detail": None,
        "http_status": None,
        "coverage": coverage or None,
        "match_status": match.get("status"),
        "persisted": bool(persisted_rows > 0),
        "persisted_rows": persisted_rows,
    }


def _should_auto_ingest(result: dict) -> bool:
    if not auto_ingest_enabled():
        return False
    status = str((result or {}).get("catalog_status") or "")
    if status not in ("partial", "unavailable"):
        return False
    reason = str((result or {}).get("catalog_reason") or (result or {}).get("reason") or "").lower()
    # normalize_match 把 B 的 no_cached_field 归为 NO_CACHED_FIELD；
    # partial 状态本身也允许尝试一次补场。
    return status == "partial" or reason in ("no_cached_field", "catalog_partial", "partial")


class CatalogClient:
    """B 星表服务的最小 stdlib HTTP 客户端；任何异常都转成 unavailable。"""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        opener=None,
        ingest_timeout: float | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/") or None
        self.timeout = float(timeout) if timeout is not None else None
        self.ingest_timeout = float(ingest_timeout) if ingest_timeout is not None else None
        self._opener = opener

    def _resolved_base_url(self) -> str:
        return self.base_url or catalog_base_url()

    def _resolved_timeout(self) -> float:
        return self.timeout if self.timeout is not None else catalog_timeout_s()

    def _resolved_ingest_timeout(self) -> float:
        return self.ingest_timeout if self.ingest_timeout is not None else ingest_timeout_s()

    def _post_json(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        url = self._resolved_base_url() + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        opener = self._opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=(timeout if timeout is not None else self._resolved_timeout())) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))

    def ingest(
        self,
        *,
        ra: float,
        dec: float,
        radius_deg: float = 0.25,
        mag_limit: float = 20.0,
        field_id: str | None = None,
        include_photometry: bool = False,
    ) -> dict:
        """请求 B 从 Gaia TAP 接收一个场并写 SQL。

        任何失败都返回结构化结果，不抛异常、不伪造场已写入：
        ok=False + catalog_status=ingest_failed + reason/http_status/detail。
        """
        ra = _finite(ra)
        dec = _finite(dec)
        radius = _finite(radius_deg)
        mag = _finite(mag_limit)
        if ra is None or dec is None or radius is None or mag is None:
            return {
                "ok": False,
                "catalog": CATALOG,
                "catalog_status": "ingest_failed",
                "reason": "INVALID_INGEST_REQUEST",
                "detail": "ra/dec/radius_deg/mag_limit 必须是有限数字。",
                "http_status": None,
                "field_id": field_id,
            }
        payload = {
            "catalog": CATALOG,
            "ra": float(ra),
            "dec": float(dec),
            "radius_deg": float(radius),
            "mag_limit": float(mag),
            "include_photometry": bool(include_photometry),
            "replace_field": True,
        }
        if field_id:
            payload["field_id"] = str(field_id)
        try:
            raw = self._post_json("/api/v1/catalog/ingest", payload, timeout=self._resolved_ingest_timeout())
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            body = {}
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                body = {}
            if not isinstance(body, dict):
                body = {}
            err = body.get("error")
            return {
                "ok": False,
                "catalog": CATALOG,
                "catalog_status": "ingest_failed",
                "reason": str(body.get("reason") or body.get("code") or "INGEST_HTTP_ERROR"),
                "detail": body.get("message") or (err.get("message") if isinstance(err, dict) else None),
                "http_status": status,
                "field_id": field_id,
                "response": body,
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {
                "ok": False,
                "catalog": CATALOG,
                "catalog_status": "ingest_failed",
                "reason": "INGEST_UNREACHABLE",
                "detail": str(exc),
                "http_status": None,
                "field_id": field_id,
            }
        except ValueError as exc:
            return {
                "ok": False,
                "catalog": CATALOG,
                "catalog_status": "ingest_failed",
                "reason": "INGEST_MALFORMED_RESPONSE",
                "detail": str(exc),
                "http_status": None,
                "field_id": field_id,
            }
        except Exception as exc:  # noqa: BLE001 - 分析不能被 ingest 拖垮
            return {
                "ok": False,
                "catalog": CATALOG,
                "catalog_status": "ingest_failed",
                "reason": "INGEST_CLIENT_ERROR",
                "detail": str(exc),
                "http_status": None,
                "field_id": field_id,
            }
        if not isinstance(raw, dict) or not raw.get("ok"):
            reason = "INGEST_REJECTED"
            detail = None
            if isinstance(raw, dict):
                reason = str(raw.get("reason") or raw.get("code") or reason)
                detail = raw.get("message") or raw.get("detail")
            return {
                "ok": False,
                "catalog": CATALOG,
                "catalog_status": "ingest_failed",
                "reason": reason,
                "detail": detail,
                "http_status": 200,
                "field_id": field_id,
                "response": raw if isinstance(raw, dict) else None,
            }
        return {
            "ok": True,
            "catalog": CATALOG,
            "catalog_status": "ok",
            "reason": None,
            "detail": None,
            "http_status": 200,
            "field_id": raw.get("field_id") or field_id,
            "release": raw.get("release"),
            "counts": raw.get("counts") or {},
            "provenance": raw.get("provenance"),
            "complete": raw.get("complete"),
            "warnings": raw.get("warnings") or [],
        }

    def match(
        self,
        *,
        ra: float,
        dec: float,
        mjd: float,
        sigma_arcsec: float = 1.0,
        radius_arcsec: float | None = None,
        catalogs=("gaia",),
        detection_id: str = "target",
        analysis_id: str | None = None,
        persist: bool = False,
        pmra_mas_yr: float | None = None,
        pmdec_mas_yr: float | None = None,
        _allow_auto: bool = True,
    ) -> dict:
        """按目标检测坐标/MJD/误差查询 B；PM 传播由 B 按 ``epoch`` 完成。

        ``_allow_auto`` 仅内部用于重试，防止 ingest 后再次触发。
        """
        sigma = _finite(sigma_arcsec) or 1.0
        sigma = max(sigma, 0.01)
        radius = _finite(radius_arcsec)
        if radius is None:
            # B 内部用 max(radius, 3σ) 作为有效半径；这里给 3σ 且限制在
            # 3–10″，既覆盖质心误差，也不把整个像元外的源都算成候选。
            radius = min(10.0, max(3.0, 3.0 * sigma))
        detection = {
            "detection_id": str(detection_id),
            "ra": float(ra),
            "dec": float(dec),
            "mjd": float(mjd),
            "sigma_arcsec": float(sigma),
        }
        # PM 是可选的观测先验：交给 B 算 PM 残差，但缺省不伪造。
        if pmra_mas_yr is not None and pmdec_mas_yr is not None:
            detection["pmra_mas_yr"] = float(pmra_mas_yr)
            detection["pmdec_mas_yr"] = float(pmdec_mas_yr)
        payload = {
            "detections": [detection],
            "catalogs": list(catalogs),
            "radius_arcsec": float(radius),
            "epoch": float(mjd),
            "persist": bool(persist),
        }
        if analysis_id:
            payload["analysis_id"] = str(analysis_id)
        if persist and not analysis_id:
            return _unavailable(
                "PERSIST_ANALYSIS_ID_MISSING",
                detail="persist=true 需要稳定 analysis_id。",
                http_status=None,
            )

        try:
            raw = self._post_json("/api/v1/catalog/match", payload)
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            payload = {}
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001 - 错误体只用于诊断，不能影响主流程
                payload = {}
            reason = str(payload.get("reason") or payload.get("code") or "HTTP_ERROR")
            detail = payload.get("message") or payload.get("error", {}).get("message")
            return _unavailable(reason, detail=detail, http_status=status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _unavailable("CATALOG_UNREACHABLE", detail=str(exc))
        except ValueError as exc:
            return _unavailable("MALFORMED_RESPONSE", detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - 星表是增强信息，不允许拖垮分析
            return _unavailable("CATALOG_CLIENT_ERROR", detail=str(exc))

        result = normalize_match(raw)
        if not result.get("release") and raw.get("release"):
            result["release"] = raw.get("release")
        if _allow_auto and _should_auto_ingest(result):
            ingested = self.ingest(
                ra=float(ra),
                dec=float(dec),
                radius_deg=0.25,
                mag_limit=20.0,
                include_photometry=False,
            )
            if ingested.get("ok"):
                retry = self.match(
                    ra=ra,
                    dec=dec,
                    mjd=mjd,
                    sigma_arcsec=sigma_arcsec,
                    radius_arcsec=radius_arcsec,
                    catalogs=catalogs,
                    detection_id=detection_id,
                    analysis_id=analysis_id,
                    persist=persist,
                    pmra_mas_yr=pmra_mas_yr,
                    pmdec_mas_yr=pmdec_mas_yr,
                    _allow_auto=False,
                )
                retry["auto_ingest"] = {
                    "attempted": True,
                    "ok": True,
                    "field_id": ingested.get("field_id"),
                    "reason": None,
                    "http_status": ingested.get("http_status"),
                }
                return retry
            # ingest 失败/超时：保留原 match 状态，绝不伪造
            result["auto_ingest"] = {
                "attempted": True,
                "ok": False,
                "reason": ingested.get("reason"),
                "detail": ingested.get("detail"),
                "http_status": ingested.get("http_status"),
            }
        return result


# ------------------------------------------------------------------ 分类


def _motion_pm(motion: dict | None) -> tuple[float, float, float] | None:
    """把 app 的 μ/PA 转成 (pmra*, pmdec, σ) [mas/yr]；不可测时返回 None。"""
    if not isinstance(motion, dict) or not motion.get("moving"):
        return None
    mu = _finite(motion.get("mu_mas_yr"))
    mu_err = _finite(motion.get("mu_err_mas_yr"))
    pa = _finite(motion.get("pa_deg"))
    if mu is None or mu <= 0 or mu_err is None or mu_err <= 0 or pa is None:
        return None
    # 只有观测 PM 本身显著（>3σ）时，才用它否决 known；否则按“不可判”处理。
    if mu / mu_err < 3.0:
        return None
    rad = math.radians(pa)
    return mu * math.sin(rad), mu * math.cos(rad), mu_err


def pm_consistency(motion: dict | None, candidate: dict) -> tuple[bool | None, dict | None]:
    """目标实测 PM 与星表 PM 的 3σ 一致性；数据不足返回 (None, None)。

    这不是星表位置传播（那由 B 做），只是把 app 已有的运动测量与 B 返回的
    PM 数值做一次独立核对。单期 / PM 不可测时不硬判。
    """
    pm = _motion_pm(motion)
    if pm is None:
        return None, None
    mu_ra, mu_dec, mu_err = pm
    cat_ra = _finite(candidate.get("pmra"))
    cat_dec = _finite(candidate.get("pmdec"))
    if cat_ra is None or cat_dec is None:
        return None, None
    cat_ra_err = _finite(candidate.get("pmra_error")) or 0.0
    cat_dec_err = _finite(candidate.get("pmdec_error")) or 0.0
    sigma = math.hypot(mu_err, math.hypot(cat_ra_err, cat_dec_err))
    if sigma <= 0:
        sigma = max(mu_err, 1.0)
    residual = math.hypot(mu_ra - cat_ra, mu_dec - cat_dec)
    metrics = {
        "pm_residual_mas_yr": round(residual, 3),
        "pm_residual_sigma": round(residual / sigma, 3),
    }
    return residual <= 3.0 * sigma, metrics


def _base(catalog: dict | None) -> dict:
    return {
        "schema_version": IDENTIFICATION_SCHEMA_VERSION,
        "engine_version": IDENTIFICATION_ENGINE_VERSION,
        "discovery_claim": False,
        "disclaimer": DISCLAIMER,
        "catalog": (catalog or {}).get("catalog") or CATALOG,
        "catalog_schema_version": (catalog or {}).get("catalog_schema_version"),
        "catalog_id": None,
        "source_id": None,
        "release": (catalog or {}).get("release"),
        "catalog_release": (catalog or {}).get("release"),
        "separation_arcsec": None,
        "sep_arcsec": None,
        "pm_consistent": None,
        "n_candidates": 0,
        "candidates": [],
        "quality": [],
        "caveats": [],
        "reason": None,
        "persisted": bool((catalog or {}).get("persisted")),
        "persisted_rows": int((catalog or {}).get("persisted_rows") or 0),
        "persist_requested": False,
    }


def _finish(out: dict) -> dict:
    """补齐 API 契约要求的别名字段，保证所有状态同形。

    - ``catalog_release``：与 ``release`` 同值（任务/设计要求的字段名）；
    - ``source_id``：仅 known_star 时有值，且始终是字符串（Gaia 64 位 ID）；
    - ``sep_arcsec``：与 ``separation_arcsec`` 同值；
    - ``caveats``：人可读的降级/边界说明；即使为空也返回数组。
    """
    out["catalog_release"] = out.get("release")
    if out.get("status") == "known_star" and out.get("catalog_id") is not None:
        out["source_id"] = str(out["catalog_id"])
    else:
        out["source_id"] = None
    out["sep_arcsec"] = out.get("separation_arcsec")
    out["caveats"] = [str(c) for c in (out.get("caveats") or []) if c]
    out["persisted"] = bool(out.get("persisted"))
    out["persisted_rows"] = int(out.get("persisted_rows") or 0)
    out["persist_requested"] = bool(out.get("persist_requested"))
    return out


def _label(status: str, catalog: dict | None) -> str:
    if status == "known_star":
        release = (catalog or {}).get("release")
        return "Gaia DR3 已知" if release == "gaia-dr3" else LABELS_ZH[status]
    return LABELS_ZH[status]


def _insufficient_caveats(reason: str) -> list[str]:
    text = {
        "TARGET_NOT_DETECTED": "目标没有被探测到，无法做星表比对。",
        "FETCH_FAILED": "真实切片取数失败，无法确认目标位置。",
        "CATALOG_PARTIAL": "星表覆盖不完整（partial），0 候选不能当作“未收录”。",
        "CATALOG_UNAVAILABLE": "星表服务不可用，无法判断已知/未知。",
        "CATALOG_ID_MISSING": "星表候选缺少稳定 ID，不能安全地给 known。",
        "SINGLE_EPOCH": "仅单期观测，无法核对自行/运动一致性。",
        "LOW_SNR": "目标 S/N 不足，位置匹配不可靠。",
    }
    return [text.get(reason, f"识别条件不足：{reason}。")]


def insufficient(reason: str, *, catalog: dict | None = None, detail: str | None = None) -> dict:
    out = _base(catalog)
    out.update(
        {
            "status": "insufficient_data",
            "label_zh": LABELS_ZH["insufficient_data"],
            "confidence": 0.0,
            "catalog_status": (catalog or {}).get("catalog_status"),
            "reason": str(reason or "INSUFFICIENT_DATA"),
            "detail": detail,
            "quality": [str(reason or "INSUFFICIENT_DATA")],
            "caveats": _insufficient_caveats(str(reason or "INSUFFICIENT_DATA")),
        }
    )
    return _finish(out)


def catalog_unavailable(
    catalog: dict | None = None,
    *,
    reason: str = "CATALOG_UNAVAILABLE",
    detail: str | None = None,
) -> dict:
    out = _base(catalog)
    out.update(
        {
            "status": "catalog_unavailable",
            "label_zh": LABELS_ZH["catalog_unavailable"],
            "confidence": 0.0,
            "catalog_status": "unavailable",
            "reason": "CATALOG_UNAVAILABLE",
            "catalog_reason": reason,
            "detail": detail,
            "n_candidates": 0,
            "quality": ["CATALOG_UNAVAILABLE"],
            "caveats": [
                "星表服务不可用，无法判断已知/未知；没有把空结果当成“未收录”。",
            ],
        }
    )
    return _finish(out)


def _identify(
    catalog: dict | None,
    *,
    target: dict | None = None,
    motion: dict | None = None,
) -> dict:
    """根据 B 的候选结果给目标源一个 known/ambiguous/... 状态。

    ``target`` 至少含 ``ra``/``dec``/``mjd``/``sigma_arcsec``（分析目标的
    最新实测位置）；为 None 表示目标没有被探测到，不查询星表。
    """
    if target is None:
        return insufficient("TARGET_NOT_DETECTED", catalog=catalog)
    if not isinstance(catalog, dict) or not catalog.get("ok"):
        reason = (catalog or {}).get("catalog_reason") or "CATALOG_UNAVAILABLE"
        return catalog_unavailable(
            catalog,
            reason=reason,
            detail=(catalog or {}).get("detail"),
        )

    catalog = dict(catalog)
    if target.get("ra") is not None and target.get("dec") is not None:
        catalog.setdefault("query", target)
    candidates = list(catalog.get("candidates") or [])
    n_candidates = len(candidates)
    base = _base(catalog)
    base["catalog_status"] = catalog.get("catalog_status")
    base["n_candidates"] = n_candidates

    if n_candidates >= 2 or catalog.get("ambiguous"):
        best = min(
            (c for c in candidates if c.get("separation_arcsec") is not None),
            key=lambda c: c["separation_arcsec"],
            default=None,
        )
        caveats = ["匹配半径内有多个星表候选；系统不自动选择最亮或最近的一个。"]
        if catalog.get("catalog_status") == "partial":
            caveats.append("星表覆盖不完整（partial），候选列表可能缺源。")
        return {
            **base,
            "status": "ambiguous",
            "label_zh": LABELS_ZH["ambiguous"],
            "confidence": 0.55,
            "catalog_id": None,
            "separation_arcsec": (best or {}).get("separation_arcsec"),
            "candidates": candidates,
            "reason": "MULTIPLE_CANDIDATES",
            "quality": ["MULTIPLE_CANDIDATES"],
            "caveats": caveats,
            "auto_status": "ambiguous",
            "manual_override": False,
        }

    if n_candidates == 1:
        candidate = candidates[0]
        cid = candidate.get("catalog_id")
        if not cid:
            return insufficient("CATALOG_ID_MISSING", catalog=catalog)
        sep = _finite(candidate.get("separation_arcsec"))
        if sep is not None and sep > CATALOG_OFFSET_LIMIT_ARCSEC:
            # 单候选但偏移超过 1 个 SPHEREx 像素：这是偶然对齐，不能 known。
            # 保留 candidates（含 candidate id）与 separation 供审计；
            # 顶层 catalog_id/source_id 明确置空，避免被当成已确认身份。
            out = {
                **base,
                "status": "insufficient_data",
                "label_zh": LABELS_ZH["insufficient_data"],
                "confidence": 0.0,
                "catalog_id": None,
                "separation_arcsec": sep,
                "candidates": candidates,
                "reason": "CATALOG_OFFSET_TOO_LARGE",
                "quality": ["CATALOG_OFFSET_TOO_LARGE"],
                "caveats": [
                    f"星表候选偏离 {sep:.2f}″，超过 {CATALOG_OFFSET_LIMIT_ARCSEC:.2f}″ 门限，不能据此给 known。",
                ],
            }
            return out
        release = candidate.get("release") or catalog.get("release")
        base["release"] = release
        ruwe = _finite(candidate.get("ruwe"))
        if ruwe is None or ruwe <= 0:
            return {
                **base,
                "status": "ambiguous",
                "label_zh": LABELS_ZH["ambiguous"],
                "confidence": 0.35,
                "catalog_id": None,
                "separation_arcsec": candidate.get("separation_arcsec"),
                "candidates": candidates,
                "reason": "RUWE_UNAVAILABLE",
                "quality": ["RUWE_UNAVAILABLE"],
                "ruwe": None,
                "astrometric_excess_noise": candidate.get("astrometric_excess_noise"),
                "phot_variable_flag": candidate.get("phot_variable_flag"),
                "caveats": [
                    "B 未提供可用的 RUWE/天体测量质量；无法确认 Gaia 解可靠，known 降级为 ambiguous。",
                ],
            }
        if ruwe > 2.0:
            return {
                **base,
                "status": "ambiguous",
                "label_zh": LABELS_ZH["ambiguous"],
                "confidence": 0.3,
                "catalog_id": None,
                "separation_arcsec": candidate.get("separation_arcsec"),
                "candidates": candidates,
                "reason": "RUWE_HIGH",
                "quality": ["HIGH_RUWE"],
                "ruwe": ruwe,
                "astrometric_excess_noise": candidate.get("astrometric_excess_noise"),
                "phot_variable_flag": candidate.get("phot_variable_flag"),
                "caveats": [
                    f"Gaia RUWE={ruwe:.2f} > 2.0（项目阈值）；5 参数解可能受伴星/污染影响，known 降级为 ambiguous。",
                ],
            }
        pm_ok, pm_metrics = pm_consistency(motion, candidate)
        base["pm_consistent"] = pm_ok
        if pm_metrics:
            base["metrics"] = pm_metrics
        quality = []
        if catalog.get("catalog_status") == "partial":
            quality.append("CATALOG_PARTIAL")
        if pm_ok is False:
            # 位置对上、运动对不上：可能的混合/错误对应体，不能硬给 known。
            caveats = [
                "位置匹配但实测自行与星表 PM 不一致（>3σ）；可能是混合、未解析伴星或错误对应体。",
            ]
            if catalog.get("catalog_status") == "partial":
                caveats.append("星表覆盖不完整（partial），结论可能受缺源影响。")
            return {
                **base,
                "status": "ambiguous",
                "label_zh": LABELS_ZH["ambiguous"],
                "confidence": 0.4,
                "catalog_id": None,
                "separation_arcsec": candidate.get("separation_arcsec"),
                "candidates": candidates,
                "reason": "PM_INCONSISTENT",
                "quality": [*quality, "PM_INCONSISTENT"],
                "caveats": caveats,
            }
        n_epochs = int((motion or {}).get("n_epochs") or (motion or {}).get("detected_epochs") or 0)
        if n_epochs < 2:
            quality.append("SINGLE_EPOCH")
        mu = _finite((motion or {}).get("mu_mas_yr"))
        if mu is not None and abs(mu) >= 1000.0:
            quality.append("HIGH_PM")
        caveats = []
        confidence = 0.9
        if pm_ok is None:
            # 没有可用的实测 PM（单期/未提供/不显著）：位置匹配仍然有价值，
            # 但不能声称“运动一致”，必须降置信度并显式写 caveat。
            quality.append("NO_PM_CHECK")
            caveats.append("未提供或未测到可用的自行（PM）；known 仅基于位置匹配，未核对运动一致性。")
            confidence = min(confidence, 0.75)
        if "SINGLE_EPOCH" in quality:
            caveats.append("仅单期观测，无法核对自行；known 置信度降级。")
            confidence = min(confidence, 0.8)
        if "CATALOG_PARTIAL" in quality:
            caveats.append("星表覆盖不完整（partial），候选可能缺失；结论按降级处理。")
            confidence = min(confidence, 0.75)
        if catalog.get("catalog_status") == "stale":
            caveats.append("使用过期的星表缓存；结果可复现但可能不是最新 release。")
            confidence = min(confidence, 0.9)
        if "HIGH_PM" in quality:
            caveats.append("高自行目标：已按星表 PM 传播到观测历元；残余偏差仍可能来自星表解。")
        photometry = _photometry(candidate)
        excess = _finite(candidate.get("astrometric_excess_noise"))
        if excess is not None and excess > 2.0:
            caveats.append(
                f"Gaia astrometric_excess_noise={excess:.2f} mas > 2；天体测量质量需谨慎，未据此自动升级置信度。"
            )
        phot_flag = candidate.get("phot_variable_flag")
        if phot_flag and str(phot_flag).upper() not in ("NOT_AVAILABLE", "CONSTANT", "UNKNOWN", "NONE"):
            caveats.append(
                f"Gaia phot_variable_flag={phot_flag}；仅作目录备注，不构成 D 的变星结论。"
            )
        if photometry:
            caveats.append("2MASS/WISE 红外数值仅作颜色/交叉证认；不单独据此做天体分类。")
        return {
            **base,
            "status": "known_star",
            "label_zh": _label("known_star", catalog),
            "confidence": round(confidence, 3),
            "catalog_id": str(cid),
            "separation_arcsec": candidate.get("separation_arcsec"),
            "pm_consistent": pm_ok,
            "candidates": candidates,
            "reason": "SINGLE_CATALOG_MATCH",
            "quality": quality,
            "caveats": caveats,
            "object_type": candidate.get("object_type"),
            "ruwe": ruwe,
            "astrometric_excess_noise": candidate.get("astrometric_excess_noise"),
            "phot_variable_flag": phot_flag,
            "parallax_error": candidate.get("parallax_error"),
            "photometry": photometry,
        }

    # 0 候选：只有“查询成功且覆盖完整”才允许进入未收录候选流程。
    catalog_status = catalog.get("catalog_status")
    if catalog_status in ("ok", "empty", "stale"):
        n_epochs = int((motion or {}).get("n_epochs") or (motion or {}).get("detected_epochs") or 0)
        caveats = [
            "星表查询成功但没有候选；未匹配不等于新天体，只是“未收录候选”。",
            "可能是星表遗漏、星系/QSO、伪源或 6.15″/px 下的混合；需要更高分辨率/更深数据复核。",
        ]
        if catalog_status == "stale":
            caveats.append("使用过期星表缓存；候选缺失可能来自缓存版本。")
        if n_epochs < 2:
            caveats.append("少于 2 期观测，未核对自行/多历元一致性；候选置信度降级。")
        return {
            **base,
            "status": "uncatalogued_candidate",
            "label_zh": LABELS_ZH["uncatalogued_candidate"],
            "confidence": 0.5 if catalog_status != "stale" else 0.4,
            "catalog_id": None,
            "separation_arcsec": None,
            "reason": "NO_CATALOG_MATCH",
            "quality": ["NO_CATALOG_MATCH"],
            "caveats": caveats,
        }
    # partial：查询不完整，0 候选不能当“未收录”（D-08）。
    return insufficient("CATALOG_PARTIAL", catalog=catalog)


def identify(
    catalog: dict | None,
    *,
    target: dict | None = None,
    motion: dict | None = None,
) -> dict:
    """公开入口：分类并补齐 ``source_id``/``sep_arcsec``/``catalog_release``/``caveats``。"""
    return _finish(_identify(catalog, target=target, motion=motion))
