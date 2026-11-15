#!/usr/bin/env python3
"""AnyField P1A HTTP service (port 8798 by default).

Endpoints:
  GET  /api/v1/health
  GET  /api/v1/field?ra=&dec=&fov=
  GET  /api/v1/epochs?ra=&dec=&fov=   (1.0-compatible)
  GET  /api/v1/sources?ra=&dec=&fov=  (local ingested sources)
  POST /api/v1/ingest                 (local JSON manifest or local NPZ/FITS path)
  GET  /api/v1/ingest/{job_id}
  POST /api/v1/ingest/{job_id}/rollback

The catalog DB is read-only. The sidecar is the only writable store. There is
no outbound network access, no pixel download, no detection/reprojection, and
no SIM generation. P1A adds local NPZ/FITS pixel ingest + source detection; it
still has no IRSA/S3, no reprojection/common grid, and no series recomputation.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse  # query parsing only; no outbound network

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # package import
    from .anyfield_core import (  # type: ignore[import-not-found]
        ApiError,
        COVERAGE_MODE,
        DEFAULT_SIDECAR,
        INDEX_COMPLETENESS,
        SCHEMA_VERSION,
        SERVICE_NAME,
        SERVICE_VERSION,
        Store,
        error_payload,
    )
except ImportError:  # script/direct test import
    from anyfield_core import (  # type: ignore[no-redef]
        ApiError,
        COVERAGE_MODE,
        DEFAULT_SIDECAR,
        INDEX_COMPLETENESS,
        SCHEMA_VERSION,
        SERVICE_NAME,
        SERVICE_VERSION,
        Store,
        error_payload,
    )

DEFAULT_DB = HERE.parent / "数据库工具" / "var" / "catalog.sqlite"
DEFAULT_BLOB_ROOT = HERE.parent / "数据库工具" / "var" / "blobs"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8798
MAX_INGEST_BYTES = 1_000_000


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class AnyFieldHandler(BaseHTTPRequestHandler):
    server_version = "AnyFieldP0/0.2"
    sys_version = ""

    # ------------------------------------------------------------------ helpers
    def _request_id(self) -> str:
        return "req_" + uuid.uuid4().hex[:12]

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error_body(self, exc: ApiError, *, request_id: str) -> None:
        snapshot = None
        try:
            snapshot = self.server.store.index_snapshot()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - error response must not fail
            snapshot = "local-unavailable"
        self._send_json(
            exc.status,
            error_payload(exc, index_snapshot=snapshot, request_id=request_id),
        )

    @staticmethod
    def _param(query: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
        values = query.get(name)
        if not values:
            return default
        return values[0]

    def _resolve_query(self) -> tuple[float, float, float]:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        ra = self._param(query, "ra")
        dec = self._param(query, "dec")
        fov = self._param(query, "fov", "0.2")
        if ra is None or dec is None:
            raise ApiError(400, "INVALID_QUERY", "需要同时提供 ra、dec（度）")
        return ra, dec, fov  # type: ignore[return-value]

    def _read_json_body(self) -> Any:
        content_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type not in ("application/json", "text/json", ""):
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "POST /ingest 需要 application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(400, "BAD_JSON", "Content-Length 非法") from exc
        if length <= 0:
            raise ApiError(400, "BAD_JSON", "请求体为空")
        if length > MAX_INGEST_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", f"manifest 最大 {MAX_INGEST_BYTES} 字节")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(400, "BAD_JSON", "请求体不是合法 JSON") from exc

    # ------------------------------------------------------------------ routes
    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        request_id = self._request_id()
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/v1/health":
                body = self.server.store.health()  # type: ignore[attr-defined]
                status = 200 if body.get("status") == "ok" else 503
                body["request_id"] = request_id
                self._send_json(status, body)
                return
            if path == "/api/v1/field":
                ra, dec, fov = self._resolve_query()
                body = self.server.store.resolve_field(ra, dec, fov)  # type: ignore[attr-defined]
                body["request_id"] = request_id
                self._send_json(200, body)
                return
            if path == "/api/v1/epochs":
                ra, dec, fov = self._resolve_query()
                body = self.server.store.resolve_epochs(ra, dec, fov)  # type: ignore[attr-defined]
                body["request_id"] = request_id
                self._send_json(200, body)
                return
            if path == "/api/v1/sources":
                query = parse_qs(parsed.query, keep_blank_values=True)
                ra = self._param(query, "ra")
                dec = self._param(query, "dec")
                if ra is None or dec is None:
                    raise ApiError(400, "INVALID_QUERY", "需要同时提供 ra、dec（度）")
                fov = self._param(query, "fov", "0.2")
                epoch = self._param(query, "epoch")
                limit_raw = self._param(query, "limit", "1000")
                try:
                    limit = int(limit_raw)
                except (TypeError, ValueError) as exc:
                    raise ApiError(400, "INVALID_QUERY", "limit 必须是整数") from exc
                body = self.server.store.resolve_sources(  # type: ignore[attr-defined]
                    ra, dec, fov, epoch=epoch, limit=limit
                )
                body["request_id"] = request_id
                self._send_json(200, body)
                return
            if path.startswith("/api/v1/ingest/"):
                parts = [p for p in path.split("/") if p]
                if len(parts) == 4 and parts[:3] == ["api", "v1", "ingest"]:
                    body = self.server.store.job_status(parts[3])  # type: ignore[attr-defined]
                    body["request_id"] = request_id
                    self._send_json(200, body)
                    return
            if path == "/api/v1/capabilities":
                self._send_json(
                    200,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "service": SERVICE_NAME,
                        "service_version": SERVICE_VERSION,
                        "coverage_mode": COVERAGE_MODE,
                        "index_completeness": INDEX_COMPLETENESS,
                        "endpoints": [
                            "/api/v1/health",
                            "/api/v1/field",
                            "/api/v1/epochs",
                            "/api/v1/sources",
                            "/api/v1/ingest",
                            "/api/v1/ingest/{job_id}",
                            "/api/v1/ingest/{job_id}/rollback",
                        ],
                        "network": "none",
                        "catalog_writes": "none",
                        "sidecar_writes": "enabled",
                        "ingest": "metadata_or_local_pixels",
                        "pixel_ingest": True,
                        "pixel_kinds": ["npz", "fits"],
                        "pixel_detection": True,
                        "sim": False,
                    },
                )
                return
            raise ApiError(404, "NOT_FOUND", f"未知路径：{parsed.path}")
        except ApiError as exc:
            self._send_error_body(exc, request_id=request_id)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            traceback.print_exc()
            self._send_error_body(
                ApiError(500, "INTERNAL", f"服务内部错误：{exc}", retryable=True),
                request_id=request_id,
            )

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        request_id = self._request_id()
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/v1/ingest":
                payload = self._read_json_body()
                body = self.server.store.submit_ingest(payload)  # type: ignore[attr-defined]
                body["request_id"] = request_id
                self._send_json(202, body)
                return
            if path.startswith("/api/v1/ingest/"):
                parts = [p for p in path.split("/") if p]
                if len(parts) == 5 and parts[:3] == ["api", "v1", "ingest"] and parts[4] == "rollback":
                    body = self.server.store.rollback_job(parts[3])  # type: ignore[attr-defined]
                    body["request_id"] = request_id
                    self._send_json(200, body)
                    return
            raise ApiError(404, "NOT_FOUND", f"未知路径：{parsed.path}")
        except ApiError as exc:
            self._send_error_body(exc, request_id=request_id)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            traceback.print_exc()
            self._send_error_body(
                ApiError(500, "INTERNAL", f"服务内部错误：{exc}", retryable=True),
                request_id=request_id,
            )

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib API
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), fmt % args)
        )


class AnyFieldHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: Store) -> None:
        self.store = store
        self.quiet = False
        super().__init__(address, AnyFieldHandler)


def build_server(
    db_path: str | Path = DEFAULT_DB,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    sidecar_path: str | Path | None = None,
    blob_root: str | Path | None = None,
    ingest_roots: list[str | Path] | None = None,
    build_footprints: bool = True,
) -> AnyFieldHTTPServer:
    store = Store(
        db_path,
        sidecar_path=sidecar_path,
        blob_root=blob_root,
        ingest_roots=ingest_roots,
        build_footprints=build_footprints,
    )
    return AnyFieldHTTPServer((host, int(port)), store)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AnyField P0 local footprint service")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="catalog.sqlite（只读）")
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR), help="anyfield.sqlite（可写）")
    parser.add_argument("--blob-root", default=str(DEFAULT_BLOB_ROOT), help="fits-header blob 根目录")
    parser.add_argument(
        "--ingest-root",
        action="append",
        default=None,
        help="允许的本地像素 root（可重复；默认 系统/app/data + 系统/参考 资料/muestras）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-build-footprints", action="store_true", help="不重建 header 足迹")
    args = parser.parse_args(argv)

    store = Store(
        args.db,
        sidecar_path=args.sidecar,
        blob_root=args.blob_root,
        ingest_roots=args.ingest_root,
        build_footprints=not args.no_build_footprints,
    )
    server = AnyFieldHTTPServer((args.host, args.port), store)
    host, port = server.server_address[:2]
    print(f"{SERVICE_NAME} {SERVICE_VERSION} listening on http://{host}:{port}", flush=True)
    print(
        f"db={store.db_path} mode=read-only sidecar={store.sidecar_path} "
        f"blob_root={store.blob_root} network=none",
        flush=True,
    )
    print(f"ingest_roots={[str(p) for p in store.ingest_roots]}", flush=True)
    print(f"footprints={store.footprint_stats}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
