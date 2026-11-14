"""HTTP API：给 MoveScope、视图工具和任何脚本一个只读数据入口。

只用标准库（http.server + sqlite3）。每个线程一条只读 SQLite 连接；WAL 允许
导入进程与 API 进程同时工作。

启动：
    python3 cli.py serve --port 8797
"""
from __future__ import annotations

import gzip
import html
import json
import math
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .catalog import Catalog
from .config import API_SCHEMA_VERSION, COVERAGE_RADIUS_DEG, DEFAULT_BLOBS, DEFAULT_DB, TOOL_VERSION
from .export import EXPORTERS
from .timeutil import mjd_to_iso
from .connection import utcnow

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_local = threading.local()

ENDPOINTS = [
    ("GET", "/api/v1/health", "契约 v1 健康检查"),
    ("GET", "/api/v1/epochs", "规范 EpochRef（target_id 或 ra/dec，两入口同形）"),
    ("GET", "/api/v1/coverage", "覆盖机会 C0–C3（无 L0–L3）"),
    ("GET", "/api/health", "服务状态与计数（兼容）"),
    ("GET", "/api/stats", "完整统计：数量、时间范围、release、存储"),
    ("GET", "/api/targets", "目标列表（含 truth）"),
    ("GET", "/api/targets/<id>/epochs", "某目标的候选历元"),
    ("GET", "/api/epochs?target_id=|ra=&dec=&fov=", "旧历元接口（deprecated）"),
    ("GET", "/api/cone?ra=&dec=&radius=", "锥搜索（精确大圆距离）"),
    ("GET", "/api/coverage?ra=&dec=&radius=", "旧覆盖接口（含 deprecated readiness）"),
    ("GET", "/api/weeks?release=", "观测周与处理版本"),
    ("GET", "/api/frames/<obsid>/<detector>", "单帧元数据 + 切割产品"),
    ("GET", "/api/cutouts?obs_id=", "切割产品列表"),
    ("GET", "/api/blob/<sha256>", "原始头/切割 Blob（immutable 缓存）"),
    ("GET", "/api/export/<fields|epochs|targets|weeks>.json", "兼容 JSON 导出"),
    ("GET", "/api/docs", "README"),
]


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "replace")
    return str(o)


def _sanitize(o):
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    return o


class Handler(BaseHTTPRequestHandler):
    server_version = f"SpherexDB/{TOOL_VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ 基础设施
    def log_message(self, fmt: str, *args) -> None:
        if " 500 " in (fmt % args) or " 404 " in (fmt % args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _catalog(self) -> Catalog:
        cat = getattr(_local, "catalog", None)
        if cat is None:
            cat = Catalog(self.server.db_path, readonly=True, blob_root=self.server.blob_root)  # type: ignore[attr-defined]
            _local.catalog = cat
        return cat

    def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        headers = {
            "Content-Type": ctype,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
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

    def _json(self, obj, code: int = 200, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(_sanitize(obj), ensure_ascii=False, default=_json_default).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", extra)

    def _error(self, code: int, msg: str) -> None:
        self._json({"error": msg, "status": code}, code=code)

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
            raise ValueError(f"{name} 必须是数字") from exc
        if not math.isfinite(value):
            # nan/inf 会让 SQL/比较静默失效，甚至把坐标错配到别的目标；一律 400。
            raise ValueError(f"{name} 必须是有限数字")
        return value

    def _int(self, q: dict[str, list[str]], name: str, default: int = 0) -> int:
        v = self._param(q, name)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是整数") from exc

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
            if path in ("/api/health", "/api/v1/health"):
                return self._json(self._catalog().health())
            if path == "/api/v1/epochs":
                return self._api_v1_epochs(q)
            if path == "/api/v1/coverage":
                return self._api_v1_coverage(q)
            if path == "/api/stats":
                return self._json(self._catalog().stats())
            if path == "/api/targets":
                include_truth = self._param(q, "include_truth", "0") in ("1", "true", "yes")
                targets = self._catalog().targets()
                if not include_truth:
                    for t in targets:
                        t.pop("truth", None)
                return self._json({"targets": targets, "include_truth": include_truth})
            if path.startswith("/api/targets/"):
                parts = path.strip("/").split("/")
                tid = parts[2]
                if len(parts) >= 4 and parts[3] == "epochs":
                    limit = min(self._int(q, "limit", 500), 2000)
                    epochs = self._catalog().epochs_for_target(tid, limit=limit)
                    return self._json({"target_id": tid, "count": len(epochs), "epochs": epochs})
                t = self._catalog().target(tid)
                if not t:
                    return self._error(404, f"目标不存在: {tid}")
                if self._param(q, "include_truth", "0") not in ("1", "true", "yes"):
                    t.pop("truth", None)
                return self._json(t)
            if path == "/api/epochs":
                return self._api_epochs(q)
            if path == "/api/cone":
                return self._api_cone(q)
            if path == "/api/coverage":
                ra = self._float(q, "ra")
                dec = self._float(q, "dec")
                if ra is None or dec is None:
                    raise ValueError("需要 ra 与 dec")
                radius = self._float(q, "radius", COVERAGE_RADIUS_DEG) or COVERAGE_RADIUS_DEG
                return self._json(self._catalog().coverage(ra, dec, radius))
            if path == "/api/weeks":
                return self._json({"weeks": self._catalog().weeks(self._param(q, "release"))})
            if path.startswith("/api/frames/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    raise ValueError("路径应为 /api/frames/<obsid>/<detector>")
                obs_id, det = parts[2], parts[3]
                d = self._catalog().frame(obs_id, int(det))
                return self._json(d) if d else self._error(404, "帧不存在")
            if path == "/api/cutouts":
                obs_id = self._param(q, "obs_id")
                frame_id = self._int(q, "frame_id", -1)
                return self._json(
                    {"cutouts": self._catalog().cutouts(obs_id=obs_id, frame_id=None if frame_id < 0 else frame_id)}
                )
            if path.startswith("/api/blob/"):
                return self._blob(path.rsplit("/", 1)[-1])
            if path.startswith("/api/export/"):
                name = path.rsplit("/", 1)[-1]
                if name.endswith(".json"):
                    name = name[:-5]
                if name not in EXPORTERS:
                    return self._error(404, f"未知导出: {name}")
                payload = EXPORTERS[name](self._catalog().conn)
                return self._json(payload)
            if path == "/api/docs":
                doc = Path(self.server.docs_path)  # type: ignore[attr-defined]
                if doc.exists():
                    return self._send(200, doc.read_bytes(), "text/markdown; charset=utf-8")
                return self._error(404, "README 不存在")
            return self._error(404, f"未知路径: {path}")
        except KeyError as exc:
            return self._error(404, f"目标不存在: {exc.args[0] if exc.args else exc}")
        except ValueError as exc:
            return self._error(400, str(exc))
        except sqlite3.Error as exc:
            return self._error(500, f"数据库错误: {exc}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._error(500, f"内部错误: {exc}")

    # ------------------------------------------------------------------ API 实现
    def _api_cone(self, q: dict[str, list[str]]) -> None:
        ra = self._float(q, "ra")
        dec = self._float(q, "dec")
        if ra is None or dec is None:
            raise ValueError("需要 ra 与 dec")
        radius = self._float(q, "radius", 0.2) or 0.2
        limit = min(self._int(q, "limit", 100), 2000)
        offset = max(0, self._int(q, "offset", 0))
        detector = self._param(q, "detector")
        release = self._param(q, "release")
        size = self._float(q, "size", 0.2) or 0.2
        include_cutouts = self._param(q, "cutouts", "0") in ("1", "true", "yes")
        det: int | list[int] | None = None
        if detector:
            det = [int(x) for x in detector.split(",") if x.strip()]
        out = self._catalog().cone(
            ra,
            dec,
            radius,
            limit=limit,
            offset=offset,
            order=self._param(q, "order", "sep"),
            detector=det,
            release=release,
            mjd_min=self._float(q, "mjd_min"),
            mjd_max=self._float(q, "mjd_max"),
            size_deg=size,
            include_cutouts=include_cutouts,
        )
        self._json(out)

    def _epoch_args(self, q: dict[str, list[str]]) -> dict:
        target_id = self._param(q, "target_id")
        ra = self._float(q, "ra")
        dec = self._float(q, "dec")
        if not target_id and (ra is None or dec is None):
            raise ValueError("需要 target_id，或 ra+dec")
        if (ra is None) != (dec is None):
            raise ValueError("ra 与 dec 必须同时给")
        if dec is not None and not (-90.0 <= dec <= 90.0):
            raise ValueError("dec 必须在 [-90, 90]")
        fov = self._float(q, "fov", 0.25)
        if fov is None or not (0.02 <= fov <= 3.5):
            raise ValueError("fov 必须在 [0.02, 3.5]")
        radius = self._float(q, "radius", COVERAGE_RADIUS_DEG)
        if radius is None or not (0.2 <= radius <= 5.0):
            raise ValueError("radius 必须在 [0.2, 5]")
        limit = self._int(q, "limit", 500)
        limit = max(1, min(limit, 2000))
        offset = max(0, self._int(q, "offset", 0))
        release = self._param(q, "release") or None
        if release not in (None, "dr1", "qr3"):
            raise ValueError("release 只能是 dr1 或 qr3")
        return {
            "target_id": target_id,
            "ra": ra,
            "dec": dec,
            "fov": fov,
            "radius": radius,
            "release": release,
            "limit": limit,
            "offset": offset,
        }

    def _epochs_v1_payload(self, q: dict[str, list[str]]) -> dict:
        return self._catalog().epochs_v1(**self._epoch_args(q))

    def _legacy_epochs_payload(self, resp: dict, requested_target_id: str | None) -> dict:
        epochs = []
        for e in resp["epochs"]:
            epochs.append(
                {
                    **e,
                    "obs_id": e["obsid"],
                    "fits_key": e["key"],
                    "frame_present": e["frame_id"] is not None,
                }
            )
        body: dict = {
            "target_id": resp.get("matched_target_id") or requested_target_id,
            "count": len(epochs),
            "epochs": epochs,
            "coverage": resp.get("coverage"),
            "deprecated": True,
            "schema_version": API_SCHEMA_VERSION,
        }
        if not requested_target_id:
            body["query"] = resp.get("query")
        return body

    def _api_epochs(self, q: dict[str, list[str]]) -> None:
        """旧接口：保留形状，但委托到 v1 规范化逻辑，并补 key/obsid/date_obs。"""
        resp = self._epochs_v1_payload(q)
        self._json(self._legacy_epochs_payload(resp, self._param(q, "target_id")))

    def _api_v1_epochs(self, q: dict[str, list[str]]) -> None:
        resp = self._epochs_v1_payload(q)
        fmt = self._param(q, "format", "v1")
        if fmt == "movescope":
            return self._json(self._legacy_epochs_payload(resp, self._param(q, "target_id")))
        if fmt != "v1":
            raise ValueError("format 只能是 v1 或 movescope")
        self._json(resp)

    def _api_v1_coverage(self, q: dict[str, list[str]]) -> None:
        ra = self._float(q, "ra")
        dec = self._float(q, "dec")
        if ra is None or dec is None:
            raise ValueError("需要 ra 与 dec")
        if not (-90.0 <= dec <= 90.0):
            raise ValueError("dec 必须在 [-90, 90]")
        radius = self._float(q, "radius", COVERAGE_RADIUS_DEG)
        if radius is None or not (0.2 <= radius <= 5.0):
            raise ValueError("radius 必须在 [0.2, 5]")
        out = self._catalog().coverage(ra, dec, radius)
        out.pop("readiness", None)
        out.pop("nearest", None)
        out["query"] = {"ra": round(float(ra) % 360.0, 6), "dec": float(dec), "radius": float(radius)}
        out["schema_version"] = API_SCHEMA_VERSION
        out["generated_at"] = utcnow()
        self._json(out)

    def _blob(self, sha: str) -> None:
        if not _SHA_RE.match(sha):
            return self._error(400, "sha256 格式不对")
        row = self._catalog().conn.execute(
            "SELECT codec, kind, raw_bytes FROM blobs WHERE sha256=?", (sha,)
        ).fetchone()
        if not row:
            return self._error(404, "blob 不存在")
        if self.headers.get("If-None-Match") == f'"{sha}"':
            return self._send(304, b"", "text/plain", {"ETag": f'"{sha}"'})
        data = self._catalog().blobs.get_bytes(sha, row["codec"])
        ctype = {
            "fits-header": "application/fits",
            "fits-cutout": "application/fits",
            "npz-cutout": "application/x-npz",
        }.get(row["kind"], "application/octet-stream")
        self._send(
            200,
            data,
            ctype,
            {
                "ETag": f'"{sha}"',
                "Cache-Control": "public, max-age=31536000, immutable",
                "Content-Disposition": f'inline; filename="{sha[:16]}{".fits" if "fits" in row["kind"] else ".npz"}"',
            },
        )

    # ------------------------------------------------------------------ 首页
    def _landing(self) -> None:
        s = self._catalog().stats()
        c = s["counts"]
        rows = "\n".join(
            f"<tr><td><code>{html.escape(m)}</code></td><td><code>{html.escape(p)}</code></td><td>{html.escape(d)}</td></tr>"
            for m, p, d in ENDPOINTS
        )
        page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPHEREx 数据库工具</title>
<style>
:root{{color-scheme:dark}} body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
margin:0;background:#0b0f17;color:#d9e2ec}}
main{{max-width:980px;margin:0 auto;padding:48px 24px 80px}}
h1{{font-size:30px;margin:0 0 6px}} .sub{{color:#8fa3b8;margin-bottom:32px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0 36px}}
.card{{background:#121a26;border:1px solid #1f2c3d;border-radius:12px;padding:16px}}
.card b{{display:block;font-size:24px;color:#7dd3fc}} .card span{{color:#8fa3b8;font-size:12px}}
table{{width:100%;border-collapse:collapse;background:#121a26;border-radius:12px;overflow:hidden}}
th,td{{text-align:left;padding:10px 14px;border-bottom:1px solid #1f2c3d;font-size:13px}}
th{{color:#8fa3b8;font-weight:600}} code{{color:#bae6fd}}
a{{color:#7dd3fc}} .foot{{color:#64748b;font-size:12px;margin-top:28px}}
</style></head><body><main>
<h1>SPHEREx 数据库工具</h1>
<div class="sub">元数据在 SQLite，原始头与切割产品在内容寻址 Blob 仓库。只读 API：<code>/api/*</code></div>
<div class="grid">
<div class="card"><b>{c['frames']:,}</b><span>探测器帧</span></div>
<div class="card"><b>{c['observations']:,}</b><span>曝光</span></div>
<div class="card"><b>{c['weeks']:,}</b><span>观测周</span></div>
<div class="card"><b>{c['cutouts']:,}</b><span>切割产品</span></div>
</div>
<table><thead><tr><th>方法</th><th>路径</th><th>作用</th></tr></thead><tbody>{rows}</tbody></table>
<div class="foot">SQLite {html.escape(s['db']['sqlite_version'])} · schema v{s['db']['schema_version']} ·
MJD {s['time']['mjd_min']} … {s['time']['mjd_max']} · <a href="/api/health">health</a> ·
<a href="/api/stats">stats</a> · <a href="/api/docs">README</a></div>
</main></body></html>"""
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")


def serve(
    db_path: str | os.PathLike[str] = DEFAULT_DB,
    *,
    host: str = "127.0.0.1",
    port: int = 8797,
    blob_root: str | os.PathLike[str] | None = None,
    docs_path: str | os.PathLike[str] | None = None,
) -> None:
    db = Path(db_path).expanduser().resolve()
    if not db.exists():
        raise FileNotFoundError(f"数据库不存在: {db}（先运行 python3 cli.py init）")
    docs = Path(docs_path) if docs_path else db.parent.parent / "README.md"
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.db_path = str(db)  # type: ignore[attr-defined]
    httpd.blob_root = str(Path(blob_root or DEFAULT_BLOBS).expanduser().resolve())  # type: ignore[attr-defined]
    httpd.docs_path = str(docs)  # type: ignore[attr-defined]
    print(f"SPHEREx 数据库 API → http://{host}:{port}/", flush=True)
    print(f"  db={db}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        httpd.server_close()
