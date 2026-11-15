#!/usr/bin/env python3
"""C-MVP AnyField HTTP service (port 8798 by default).

Read-only local coverage skeleton:
  GET /api/v1/health
  GET /api/v1/field?ra=&dec=&fov=
  GET /api/v1/epochs?ra=&dec=&fov=   (1.0-compatible, optional convenience)

No network access, no database writes, no ingest, no pixel download, no SIM.
Unknown / not-yet-indexed fields return 503 INDEX_INCOMPLETE; this MVP never
returns 422 NO_LOCAL_COVERAGE.
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
    from .core import (  # type: ignore[import-not-found]
        ApiError,
        COVERAGE_MODE,
        INDEX_COMPLETENESS,
        SCHEMA_VERSION,
        SERVICE_NAME,
        SERVICE_VERSION,
        Store,
        error_payload,
    )
except ImportError:  # script/direct test import
    from core import (  # type: ignore[no-redef]
        ApiError,
        COVERAGE_MODE,
        INDEX_COMPLETENESS,
        SCHEMA_VERSION,
        SERVICE_NAME,
        SERVICE_VERSION,
        Store,
        error_payload,
    )

DEFAULT_DB = HERE.parent / "数据库工具" / "var" / "catalog.sqlite"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8798


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class AnyFieldHandler(BaseHTTPRequestHandler):
    server_version = "AnyFieldMVP/0.1"
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
            if path == "/api/v1/capabilities":
                self._send_json(
                    200,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "service": SERVICE_NAME,
                        "service_version": SERVICE_VERSION,
                        "coverage_mode": COVERAGE_MODE,
                        "index_completeness": INDEX_COMPLETENESS,
                        "endpoints": ["/api/v1/health", "/api/v1/field", "/api/v1/epochs"],
                        "network": "none",
                        "writes": "none",
                        "ingest": False,
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

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib API
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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
) -> AnyFieldHTTPServer:
    return AnyFieldHTTPServer((host, int(port)), Store(db_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C-MVP AnyField read-only service")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="本地 catalog.sqlite（只读）")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    store = Store(args.db)
    server = AnyFieldHTTPServer((args.host, args.port), store)
    host, port = server.server_address[:2]
    print(f"{SERVICE_NAME} {SERVICE_VERSION} listening on http://{host}:{port}", flush=True)
    print(f"db={store.db_path} mode=read-only network=none coverage_mode={COVERAGE_MODE}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
