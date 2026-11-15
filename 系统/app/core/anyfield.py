"""D-C 集成 P0：AnyField（C，8798）只读客户端。

范围：
- 只做 ``GET /api/v1/field?ra=&dec=&fov=`` 的覆盖握手；
- **不**拉像素、**不**做 C-native 源列表、**不**生成 SIM；
- C 是可选增强：超时/503/连接失败一律返回结构化结果，绝不抛未处理异常，
  也绝不把“C 索引不完整”解释成“确认无覆盖”。

C 状态语义（见 ``系统/anyfield/README.md``）：
- 200  ``ok``：有本地 epochs / 覆盖；返回 n_epochs、footprint_method、epochs；
- 422  ``no_coverage``：C 在 complete region 内的强断言，确认无覆盖；
- 503  ``index_incomplete``：索引不完整/DB 不可用，不能断言无覆盖；
- 连接失败/超时 ``unavailable``：C 服务不可用，继续现有本地路径。
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ANYFIELD_URL = "http://127.0.0.1:8798"
DEFAULT_TIMEOUT_S = 1.5
ANYFIELD_SCHEMA_VERSION = "1.0"
ANYFIELD_SERVICE = "anyfield-c-p0"


def anyfield_base_url() -> str:
    """C 服务地址；默认 8798，``ANYFIELD_URL`` 可覆盖。"""
    return (
        os.environ.get("ANYFIELD_URL")
        or DEFAULT_ANYFIELD_URL
    ).strip().rstrip("/")


def anyfield_timeout_s() -> float:
    raw = os.environ.get("ANYFIELD_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return value if math.isfinite(value) and value > 0 else DEFAULT_TIMEOUT_S


def _finite(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _int(value) -> int | None:
    out = _finite(value)
    return int(out) if out is not None else None


def _error_result(
    *,
    status: str,
    code: str,
    detail: str | None = None,
    http_status: int | None = None,
    retryable: bool = True,
    body: dict | None = None,
) -> dict:
    body = body or {}
    coverage = body.get("coverage") if isinstance(body.get("coverage"), dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    return {
        "schema_version": ANYFIELD_SCHEMA_VERSION,
        "service": ANYFIELD_SERVICE,
        "ok": False,
        "status": status,
        "http_status": http_status,
        "n_epochs": 0,
        "count": 0,
        "footprint_method": body.get("footprint_method") or coverage.get("footprint_method"),
        "coverage_method": body.get("coverage_method") or coverage.get("coverage_method"),
        "coverage": coverage or None,
        "epochs": [],
        "index_completeness": body.get("index_completeness") or coverage.get("index_completeness"),
        "index_snapshot": body.get("index_snapshot"),
        "data_source": body.get("source"),
        "error_code": code or error.get("code"),
        "error": error or None,
        "retryable": bool(body.get("retryable", error.get("retryable", retryable))),
        "detail": detail,
    }


def _ok_result(body: dict, http_status: int = 200) -> dict:
    coverage = body.get("coverage") if isinstance(body.get("coverage"), dict) else {}
    epochs = body.get("epochs") if isinstance(body.get("epochs"), list) else []
    n_epochs = _int(coverage.get("n_epochs"))
    if n_epochs is None:
        n_epochs = _int(body.get("count"))
    if n_epochs is None:
        n_epochs = len(epochs)
    return {
        "schema_version": ANYFIELD_SCHEMA_VERSION,
        "service": ANYFIELD_SERVICE,
        "ok": True,
        "status": "ok",
        "http_status": http_status,
        "n_epochs": int(n_epochs),
        "count": int(_int(body.get("count")) if _int(body.get("count")) is not None else len(epochs)),
        "footprint_method": body.get("footprint_method") or coverage.get("footprint_method"),
        "coverage_method": body.get("coverage_method") or coverage.get("coverage_method"),
        "coverage": coverage or None,
        "epochs": epochs,
        "index_completeness": body.get("index_completeness") or coverage.get("index_completeness"),
        "index_snapshot": body.get("index_snapshot"),
        "data_source": body.get("source"),
        "error_code": None,
        "error": None,
        "retryable": False,
        "detail": None,
    }


def normalize_field_response(status: int, body: dict | None) -> dict:
    """把 C 的 HTTP 状态/错误体收敛为稳定的 P0 结果。"""
    body = body if isinstance(body, dict) else {}
    if status == 200:
        return _ok_result(body, http_status=200)
    if status == 422:
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        return _error_result(
            status="no_coverage",
            code=str(error.get("code") or "NO_LOCAL_COVERAGE"),
            detail=str(error.get("message") or "C 在 complete region 内确认无覆盖。"),
            http_status=422,
            retryable=False,
            body=body,
        )
    if status == 503:
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        return _error_result(
            status="index_incomplete",
            code=str(error.get("code") or "INDEX_INCOMPLETE"),
            detail=str(error.get("message") or "C 索引不完整或数据库不可用。"),
            http_status=503,
            retryable=True,
            body=body,
        )
    return _error_result(
        status="unavailable",
        code=f"HTTP_{status}",
        detail=f"C 返回未预期的 HTTP {status}。",
        http_status=status,
        retryable=status >= 500,
        body=body,
    )


class AnyFieldClient:
    """C 的 stdlib HTTP 客户端；所有失败都转成结构化结果，不抛出。"""

    def __init__(self, base_url: str | None = None, timeout: float | None = None, opener=None):
        self.base_url = (base_url or "").rstrip("/") or None
        self.timeout = float(timeout) if timeout is not None else None
        self._opener = opener

    def _resolved_base_url(self) -> str:
        return self.base_url or anyfield_base_url()

    def _resolved_timeout(self) -> float:
        return self.timeout if self.timeout is not None else anyfield_timeout_s()

    def field(self, ra: float, dec: float, fov: float = 0.2) -> dict:
        ra = _finite(ra)
        dec = _finite(dec)
        fov = _finite(fov)
        if ra is None or dec is None or fov is None:
            return _error_result(
                status="unavailable",
                code="INVALID_QUERY",
                detail="ra/dec/fov 必须是有限数字。",
                retryable=False,
            )
        if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0 and 0.02 <= fov <= 3.0):
            return _error_result(
                status="unavailable",
                code="INVALID_QUERY",
                detail="ra/dec/fov 超出 C 查询范围。",
                retryable=False,
            )
        query = urllib.parse.urlencode(
            {"ra": f"{ra:.8f}", "dec": f"{dec:.8f}", "fov": f"{fov:.6f}"}
        )
        url = self._resolved_base_url() + "/api/v1/field?" + query
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        opener = self._opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=self._resolved_timeout()) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            try:
                raw = exc.read()
            except Exception:  # noqa: BLE001 - 错误体只用于诊断
                raw = b""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # 连接拒绝、超时、DNS 等：C 不可用，不是“无覆盖”。
            code = "ANYFIELD_TIMEOUT" if "timed out" in str(exc).lower() else "ANYFIELD_UNREACHABLE"
            return _error_result(
                status="unavailable",
                code=code,
                detail=str(exc),
                retryable=True,
            )
        except Exception as exc:  # noqa: BLE001 - C 是可选增强，绝不拖垮 app
            return _error_result(
                status="unavailable",
                code="ANYFIELD_CLIENT_ERROR",
                detail=str(exc),
                retryable=True,
            )

        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError) as exc:
            return _error_result(
                status="unavailable",
                code="MALFORMED_RESPONSE",
                detail=f"C 响应不是合法 JSON：{exc}",
                http_status=status,
                retryable=True,
            )
        if not isinstance(body, dict):
            return _error_result(
                status="unavailable",
                code="MALFORMED_RESPONSE",
                detail="C 响应不是 JSON 对象。",
                http_status=status,
                retryable=True,
            )
        return normalize_field_response(status, body)
