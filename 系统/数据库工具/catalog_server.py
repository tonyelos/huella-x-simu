#!/usr/bin/env python3
"""星表 HTTP 服务（独立端口 8799，不占 SPHEREx 的 8797）。

    python3 catalog_server.py                 # 127.0.0.1:8799
    python3 catalog_server.py --port 8799 --db var/star_catalog.sqlite

接口：
    GET  /api/v1/catalog/cone?ra=&dec=&radius_deg=&epoch=&catalog=gaia
    GET  /api/v1/catalog/releases
    GET  /api/v1/catalog/fields
    GET  /api/v1/catalog/stats
    GET  /api/v1/health
    POST /api/v1/catalog/match
    POST /api/v1/catalog/ingest      # Gaia TAP 场级接收（写 SQL）

失败语义：星表未导入/数据库不可读/查询天区未缓存 → HTTP 503，
`code=CATALOG_UNAVAILABLE`。不会把空结果说成“新星”或“无此源”。
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import math
import re
import sqlite3
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from db.connection import utcnow
from db.gaia_tap import (
    GAIA_DR3_CITATION,
    GAIA_DR3_LICENSE,
    GAIA_DR3_VERSION,
    GAIA_TAP_SYNC,
    GaiaTAPError,
    fetch_gaia_cone,
)
from db.irsa_gator import enrich_gaia_rows, fetch_field_photometry, photometry_summary
from db.star_catalog import (
    CatalogError,
    CatalogUnavailable,
    CatalogValidationError,
    StarCatalog,
)
from db.star_config import (
    CATALOG_API_VERSION,
    CATALOG_DOC,
    CATALOG_PORT,
    DEFAULT_STAR_DB,
    INGEST_MAX_FIELD_ID_LEN,
    INGEST_MAX_MAG_LIMIT,
    INGEST_MAX_RADIUS_DEG,
    INGEST_MIN_MAG_LIMIT,
    INGEST_MIN_RADIUS_DEG,
    INGEST_TAP_RETRIES,
    INGEST_TAP_TIMEOUT_SECONDS,
    MAX_CONE_LIMIT,
    MAX_MATCH_DETECTIONS,
    SERVICE_NAME,
)
from db.star_schema import check_star_schema

_local = threading.local()

ENDPOINTS = [
    ("GET", "/api/v1/catalog/cone?ra=&dec=&radius_deg=&epoch=&catalog=gaia", "星表锥搜索（带 release/version/许可）"),
    ("POST", "/api/v1/catalog/match", "检测源位置/误差 → 星表候选 + ambiguous"),
    ("POST", "/api/v1/catalog/ingest", "Gaia TAP 按场接收并缓存（写 SQL）"),
    ("GET", "/api/v1/catalog/releases", "已导入星表版本、许可、来源"),
    ("GET", "/api/v1/catalog/fields", "已缓存的预设场与半径"),
    ("GET", "/api/v1/catalog/stats", "计数/存储/schema"),
    ("GET", "/api/v1/health", "健康检查"),
    ("GET", "/api/docs", "CATALOG.md"),
]


def _sanitize(o):
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    return o


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "replace")
    return str(o)


class CatalogHandler(BaseHTTPRequestHandler):
    server_version = f"StarCatalog/{CATALOG_API_VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ 基础设施
    def log_message(self, fmt: str, *args) -> None:
        if any(code in (fmt % args) for code in (" 500 ", " 503 ", " 404 ")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _catalog(self) -> StarCatalog:
        cat = getattr(_local, "catalog", None)
        if cat is None:
            cat = StarCatalog(self.server.db_path, readonly=True)  # type: ignore[attr-defined]
            _local.catalog = cat
        return cat

    def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        headers = {
            "Content-Type": ctype,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, If-None-Match",
        }
        if extra:
            headers.update(extra)
        if len(body) >= 1024 and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, 6)
            headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(body))
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(_sanitize(obj), ensure_ascii=False, default=_json_default).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, err_code: str, message: str, **extra) -> None:
        payload = {
            "schema_version": CATALOG_API_VERSION,
            "service": SERVICE_NAME,
            "code": err_code,
            "status": code,
            "message": message,
            "error": {"code": err_code, "message": message},
            "generated_at": utcnow(),
        }
        payload.update(extra)
        self._json(payload, code=code)

    def _param(self, q: dict[str, list[str]], name: str, default=None):
        vals = q.get(name)
        return vals[0] if vals else default

    def _float(self, q: dict[str, list[str]], name: str, default: float | None = None) -> float | None:
        v = self._param(q, name)
        if v is None or v == "":
            return default
        try:
            value = float(v)
        except ValueError as exc:
            raise CatalogValidationError(f"{name} 必须是数字") from exc
        if not math.isfinite(value):
            raise CatalogValidationError(f"{name} 必须是有限数字")
        return value

    def _int(self, q: dict[str, list[str]], name: str, default: int = 0) -> int:
        v = self._param(q, name)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except ValueError as exc:
            raise CatalogValidationError(f"{name} 必须是整数") from exc

    # ------------------------------------------------------------------ 路由
    def do_OPTIONS(self) -> None:
        self._send(204, b"", "text/plain")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        q = parse_qs(u.query)
        try:
            if path in ("/", "/index.html"):
                return self._landing()
            if path in ("/api/health", "/api/v1/health", "/api/v1/catalog/health"):
                return self._json(self._catalog().health())
            if path == "/api/v1/catalog/releases":
                releases = self._catalog().releases()
                return self._json(
                    {
                        "schema_version": CATALOG_API_VERSION,
                        "service": SERVICE_NAME,
                        "releases": releases,
                        "count": len(releases),
                        "generated_at": utcnow(),
                    }
                )
            if path == "/api/v1/catalog/fields":
                catalog = self._param(q, "catalog", "gaia")
                version = self._param(q, "version") or None
                release = self._catalog().get_release(catalog, version)
                fields = self._catalog().fields(release["release_id"])
                return self._json(
                    {
                        "schema_version": CATALOG_API_VERSION,
                        "release": release["release"],
                        "version": release["version"],
                        "fields": fields,
                        "count": len(fields),
                        "generated_at": utcnow(),
                    }
                )
            if path == "/api/v1/catalog/stats":
                return self._json(self._catalog().stats())
            if path == "/api/v1/catalog/cone":
                return self._api_cone(q)
            if path == "/api/v1/catalog/match":
                return self._error(405, "METHOD_NOT_ALLOWED", "match 只接受 POST JSON")
            if path == "/api/v1/catalog/ingest":
                return self._error(405, "METHOD_NOT_ALLOWED", "ingest 只接受 POST JSON")
            if path == "/api/docs":
                doc = Path(self.server.docs_path)  # type: ignore[attr-defined]
                if doc.exists():
                    return self._send(200, doc.read_bytes(), "text/markdown; charset=utf-8")
                return self._error(404, "NOT_FOUND", "CATALOG.md 不存在")
            return self._error(404, "NOT_FOUND", f"未知路径: {path}")
        except CatalogUnavailable as exc:
            return self._error(
                503,
                "CATALOG_UNAVAILABLE",
                exc.message,
                catalog=exc.catalog,
                version=exc.version,
                reason=exc.reason,
            )
        except CatalogValidationError as exc:
            return self._error(400, "BAD_REQUEST", str(exc))
        except CatalogError as exc:
            return self._error(400, "CATALOG_ERROR", str(exc))
        except sqlite3.Error as exc:
            return self._error(503, "CATALOG_UNAVAILABLE", f"星表数据库错误: {exc}")
        except FileNotFoundError as exc:
            return self._error(503, "CATALOG_UNAVAILABLE", f"星表数据库不存在: {exc}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._error(500, "INTERNAL", f"内部错误: {exc}")

    def do_POST(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        try:
            if path == "/api/v1/catalog/match":
                return self._api_match()
            if path == "/api/v1/catalog/ingest":
                return self._api_ingest()
            return self._error(404, "NOT_FOUND", f"未知路径: {path}")
        except CatalogUnavailable as exc:
            return self._error(
                503,
                "CATALOG_UNAVAILABLE",
                exc.message,
                catalog=exc.catalog,
                version=exc.version,
                reason=exc.reason,
            )
        except CatalogValidationError as exc:
            return self._error(400, "BAD_REQUEST", str(exc))
        except CatalogError as exc:
            return self._error(400, "CATALOG_ERROR", str(exc))
        except sqlite3.Error as exc:
            return self._error(503, "CATALOG_UNAVAILABLE", f"星表数据库错误: {exc}")
        except FileNotFoundError as exc:
            return self._error(503, "CATALOG_UNAVAILABLE", f"星表数据库不存在: {exc}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._error(500, "INTERNAL", f"内部错误: {exc}")

    # ------------------------------------------------------------------ 实现
    def _api_cone(self, q: dict[str, list[str]]) -> None:
        ra = self._float(q, "ra")
        dec = self._float(q, "dec")
        if ra is None or dec is None:
            raise CatalogValidationError("需要 ra 与 dec")
        radius = self._float(q, "radius_deg")
        if radius is None:
            radius = self._float(q, "radius")
        if radius is None:
            radius = 0.1
        catalog = self._param(q, "catalog", "gaia") or "gaia"
        version = self._param(q, "version") or None
        epoch = self._float(q, "epoch")
        limit = min(max(1, self._int(q, "limit", 500)), MAX_CONE_LIMIT)
        offset = max(0, self._int(q, "offset", 0))
        ambiguity = self._float(q, "ambiguity_arcsec")
        if ambiguity is None:
            ambiguity = 6.15
        out = self._catalog().cone(
            ra,
            dec,
            radius,
            epoch=epoch,
            catalog=catalog,
            version=version,
            limit=limit,
            offset=offset,
            ambiguity_arcsec=ambiguity,
        )
        self._json(out)

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise CatalogValidationError("Content-Length 非法") from exc
        if length <= 0:
            raise CatalogValidationError("POST 需要 JSON body")
        if length > 2_000_000:
            raise CatalogValidationError("请求体过大（>2 MB）")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CatalogValidationError(f"JSON 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise CatalogValidationError("请求体必须是 JSON 对象")
        return data

    def _api_match(self) -> None:
        data = self._read_json_body()
        detections = data.get("detections")
        if detections is None and "ra" in data and "dec" in data:
            detections = [data]
        if not isinstance(detections, list):
            raise CatalogValidationError("detections 必须是数组")
        if len(detections) > MAX_MATCH_DETECTIONS:
            raise CatalogValidationError(f"detections 最多 {MAX_MATCH_DETECTIONS} 个")
        catalogs = data.get("catalogs")
        if catalogs is None:
            catalogs = [data.get("catalog", "gaia")]
        if isinstance(catalogs, str):
            catalogs = [catalogs]
        if not isinstance(catalogs, list) or not all(isinstance(c, str) and c for c in catalogs):
            raise CatalogValidationError("catalogs 必须是字符串数组")
        versions = data.get("versions")
        if versions is not None and not isinstance(versions, dict):
            raise CatalogValidationError("versions 必须是对象")
        radius = data.get("radius_arcsec", 3.0)
        epoch = data.get("epoch")
        persist = bool(data.get("persist", False))
        analysis_id = data.get("analysis_id")
        out = self._catalog().match(
            detections,
            catalogs=catalogs,
            radius_arcsec=radius,
            epoch=epoch,
            versions=versions or {},
            persist=False,
            analysis_id=analysis_id,
        )
        if persist:
            writer = StarCatalog(self.server.db_path, readonly=False)  # type: ignore[attr-defined]
            try:
                out["persisted"] = writer.record_matches(
                    analysis_id or "", out["matches"], detections=detections
                )
            finally:
                writer.close()
        else:
            out["persisted"] = 0
        self._json(out)

    # ------------------------------------------------------------------ 场级接收
    @staticmethod
    def _body_float(data: dict, name: str, *, required: bool = False, default=None):
        if name not in data or data[name] is None or data[name] == "":
            if required:
                raise CatalogValidationError(f"需要 {name}")
            return default
        try:
            value = float(data[name])
        except (TypeError, ValueError) as exc:
            raise CatalogValidationError(f"{name} 必须是数字") from exc
        if not math.isfinite(value):
            raise CatalogValidationError(f"{name} 必须是有限数字")
        return value

    @staticmethod
    def _body_bool(data: dict, name: str, default: bool = False) -> bool:
        if name not in data or data[name] is None:
            return default
        value = data[name]
        if not isinstance(value, bool):
            raise CatalogValidationError(f"{name} 必须是布尔值")
        return value

    def _api_ingest(self) -> None:
        data = self._read_json_body()
        catalog = str(data.get("catalog") or "gaia").strip().lower()
        if catalog != "gaia":
            raise CatalogValidationError("MVP 只支持 catalog=gaia")
        ra = self._body_float(data, "ra", required=True)
        dec = self._body_float(data, "dec", required=True)
        if not (-90.0 <= dec <= 90.0):
            raise CatalogValidationError("dec 必须在 [-90, 90]")
        radius = self._body_float(data, "radius_deg", default=0.25)
        mag_limit = self._body_float(data, "mag_limit", default=20.0)
        if not (INGEST_MIN_RADIUS_DEG <= radius <= INGEST_MAX_RADIUS_DEG):
            raise CatalogValidationError(
                f"radius_deg 必须在 [{INGEST_MIN_RADIUS_DEG:g}, {INGEST_MAX_RADIUS_DEG:g}]"
            )
        if not (INGEST_MIN_MAG_LIMIT <= mag_limit <= INGEST_MAX_MAG_LIMIT):
            raise CatalogValidationError(
                f"mag_limit 必须在 [{INGEST_MIN_MAG_LIMIT:g}, {INGEST_MAX_MAG_LIMIT:g}]"
            )

        raw_field_id = data.get("field_id")
        if raw_field_id is None or raw_field_id == "":
            field_id = f"auto-{ra:.4f}-{dec:.4f}-r{radius:g}"
        elif isinstance(raw_field_id, str):
            field_id = raw_field_id.strip()
        else:
            raise CatalogValidationError("field_id 必须是字符串")
        if not field_id or len(field_id) > INGEST_MAX_FIELD_ID_LEN:
            raise CatalogValidationError(
                f"field_id 长度必须在 1..{INGEST_MAX_FIELD_ID_LEN}"
            )
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", field_id):
            raise CatalogValidationError("field_id 只允许字母/数字/._:-")
        field_name = data.get("field_name")
        if field_name is not None and not isinstance(field_name, str):
            raise CatalogValidationError("field_name 必须是字符串")
        if field_name:
            field_name = field_name.strip()[:200]
        else:
            field_name = f"auto field RA {ra:.4f} Dec {dec:.4f}"

        include_photometry = self._body_bool(data, "include_photometry", False)
        replace_field = self._body_bool(data, "replace_field", True)
        warnings: list[str] = []

        try:
            rows, _raw_csv, _query = fetch_gaia_cone(
                ra,
                dec,
                radius,
                mag_limit=mag_limit,
                timeout=INGEST_TAP_TIMEOUT_SECONDS,
                retries=INGEST_TAP_RETRIES,
            )
        except GaiaTAPError as exc:
            return self._error(
                502,
                "UPSTREAM_TAP_ERROR",
                f"Gaia TAP 失败，未写入任何数据: {exc}",
                ok=False,
                catalog="gaia",
                field_id=field_id,
            )
        for row in rows:
            row["field_id"] = field_id
            row["provenance"] = "tap"

        photometry = None
        if include_photometry:
            try:
                phot = fetch_field_photometry(
                    ra,
                    dec,
                    radius,
                    timeout=INGEST_TAP_TIMEOUT_SECONDS,
                    retries=INGEST_TAP_RETRIES,
                )
                stats = enrich_gaia_rows(rows, phot)
                photometry = photometry_summary(phot, stats)
                if phot.get("errors"):
                    warnings.append(
                        "2MASS/WISE partial: " + "; ".join(
                            f"{k}: {v}" for k, v in phot["errors"].items()
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"2MASS/WISE unavailable: {exc}; columns stay null")
                photometry = {
                    "status": "unavailable",
                    "source": "IRSA Gator 2MASS PSC + AllWISE",
                    "error": str(exc),
                }

        writer = StarCatalog(self.server.db_path, readonly=False)  # type: ignore[attr-defined]
        try:
            lock = getattr(self.server, "write_lock", None) or threading.Lock()
            with lock:
                result = writer.upsert_field(
                    catalog="gaia",
                    version=GAIA_DR3_VERSION,
                    field_id=field_id,
                    field_name=field_name,
                    ra=ra,
                    dec=dec,
                    radius_deg=radius,
                    mag_limit=mag_limit,
                    sources=rows,
                    provenance="tap",
                    license=GAIA_DR3_LICENSE,
                    citation=GAIA_DR3_CITATION,
                    source_url=GAIA_TAP_SYNC,
                    replace_field=replace_field,
                    photometry=photometry,
                    run_mode="tap",
                )
        finally:
            writer.close()

        warnings.extend(result.get("warnings") or [])
        out = {
            "schema_version": CATALOG_API_VERSION,
            "service": SERVICE_NAME,
            "ok": True,
            "catalog": "gaia",
            "release": result["release"],
            "version": result["version"],
            "field_id": result["field_id"],
            "field_name": result.get("field_name"),
            "counts": {
                "n_sources": result["n_sources"],
                "n_new": result["n_new"],
                "n_fields": result["n_fields"],
            },
            "provenance": "tap",
            "release_provenance": result.get("release_provenance"),
            "complete": bool(result.get("complete")),
            "radius_deg": radius,
            "mag_limit": mag_limit,
            "fetched_at": result["fetched_at"],
            "license": GAIA_DR3_LICENSE,
            "citation": GAIA_DR3_CITATION,
            "source_url": GAIA_TAP_SYNC,
            "n_release_sources": result.get("n_release_sources"),
            "replace_field": replace_field,
            "include_photometry": include_photometry,
            "photometry": photometry,
            "warnings": warnings,
        }
        self._json(out)

    # ------------------------------------------------------------------ 首页
    def _landing(self) -> None:
        schema_version = "?"
        try:
            stats = self._catalog().stats()
            schema_version = stats["db"]["schema_version"]
            releases = stats["releases"]
            counts = stats["counts"]
            release_rows = "".join(
                f"<tr><td><code>{html.escape(str(r['release']))}</code></td>"
                f"<td>{html.escape(str(r['version']))}</td>"
                f"<td>{html.escape(str(r['epoch_ref']))}</td>"
                f"<td>{r['n_sources_actual']:,}</td>"
                f"<td>{html.escape(str(r['provenance']))}</td>"
                f"<td>{html.escape(str(r['license']))}</td></tr>"
                for r in releases
            )
            n_sources = counts["sources"]
        except Exception:  # noqa: BLE001
            releases = []
            release_rows = ""
            n_sources = 0
        rows = "\n".join(
            f"<tr><td><code>{html.escape(m)}</code></td><td><code>{html.escape(p)}</code></td><td>{html.escape(d)}</td></tr>"
            for m, p, d in ENDPOINTS
        )
        page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>星表服务 · Gaia DR3</title>
<style>
:root{{color-scheme:dark}} body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
margin:0;background:#0b0f17;color:#d9e2ec}}
main{{max-width:1000px;margin:0 auto;padding:48px 24px 80px}}
h1{{font-size:30px;margin:0 0 6px}} .sub{{color:#8fa3b8;margin-bottom:28px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:24px 0 34px}}
.card{{background:#121a26;border:1px solid #1f2c3d;border-radius:12px;padding:16px}}
.card b{{display:block;font-size:24px;color:#7dd3fc}} .card span{{color:#8fa3b8;font-size:12px}}
table{{width:100%;border-collapse:collapse;background:#121a26;border-radius:12px;overflow:hidden;margin-bottom:26px}}
th,td{{text-align:left;padding:10px 14px;border-bottom:1px solid #1f2c3d;font-size:13px}}
th{{color:#8fa3b8;font-weight:600}} code{{color:#bae6fd}}
a{{color:#7dd3fc}} .foot{{color:#64748b;font-size:12px;margin-top:28px}}
</style></head><body><main>
<h1>星表服务 · Gaia DR3</h1>
<div class="sub">阶段 B/D1：星表锥搜索、候选匹配与 Gaia TAP 场级接收。独立端口 8799，不占 SPHEREx 的 8797。</div>
<div class="grid">
<div class="card"><b>{n_sources:,}</b><span>缓存星表源</span></div>
<div class="card"><b>{len(releases)}</b><span>release</span></div>
<div class="card"><b>8799</b><span>服务端口</span></div>
</div>
<table><thead><tr><th>release</th><th>version</th><th>epoch_ref</th><th>sources</th><th>provenance</th><th>license</th></tr></thead>
<tbody>{release_rows or '<tr><td colspan="6">尚未导入；运行 python3 tools/import_gaia.py --rebuild</td></tr>'}</tbody></table>
<table><thead><tr><th>方法</th><th>路径</th><th>作用</th></tr></thead><tbody>{rows}</tbody></table>
<div class="foot">schema v{schema_version} ·
<a href="/api/v1/health">health</a> · <a href="/api/v1/catalog/releases">releases</a> ·
<a href="/api/docs">CATALOG.md</a></div>
</main></body></html>"""
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")


def make_server(
    db_path: str | Path = DEFAULT_STAR_DB,
    *,
    host: str = "127.0.0.1",
    port: int = CATALOG_PORT,
    docs_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    """创建（但不启动）星表 HTTP server；测试可用 port=0 拿随机端口。"""
    db = Path(db_path).expanduser().resolve()
    if not db.exists():
        raise FileNotFoundError(
            f"星表数据库不存在: {db}（先运行 python3 tools/import_gaia.py --rebuild）"
        )
    docs = Path(docs_path) if docs_path else CATALOG_DOC
    httpd = ThreadingHTTPServer((host, port), CatalogHandler)
    httpd.db_path = str(db)  # type: ignore[attr-defined]
    httpd.docs_path = str(docs)  # type: ignore[attr-defined]
    httpd.write_lock = threading.Lock()  # 场级 ingest 写入串行化
    return httpd


def serve(
    db_path: str | Path = DEFAULT_STAR_DB,
    *,
    host: str = "127.0.0.1",
    port: int = CATALOG_PORT,
    docs_path: str | Path | None = None,
) -> None:
    httpd = make_server(db_path, host=host, port=port, docs_path=docs_path)
    print(f"星表 API → http://{host}:{port}/", flush=True)
    print(f"  db={Path(db_path).expanduser().resolve()}", flush=True)
    print(f"  docs={CATALOG_DOC}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="星表服务（Gaia DR3 锥搜索/匹配，端口 8799）")
    parser.add_argument("--db", default=str(DEFAULT_STAR_DB))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=CATALOG_PORT)
    parser.add_argument("--docs", default=None)
    args = parser.parse_args(argv)
    try:
        serve(args.db, host=args.host, port=args.port, docs_path=args.docs)
    except FileNotFoundError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
