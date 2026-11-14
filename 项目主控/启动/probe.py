#!/usr/bin/env python3
"""统一启动器的只读探针（Python 标准库，不写任何文件）。

子命令：
  wait   轮询 HTTP 健康检查，成功/超时后退出。
  schema 只读读取 SQLite 的 meta.schema_version（DB 未运行时也可用）。
  latest 递归找目录内最新修改的文件（给 status 显示 app/data 快照）。

由 lib.sh 调用；也可以单独运行，便于演示排查：
  python3 probe.py wait --kind db --url http://127.0.0.1:8797/api/health \
      --expect-service spherex-db --timeout 15 --interval 0.2
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

TIMEOUT_PER_REQUEST = 2.0
MAX_BODY = 64 * 1024
# 健康检查永远走本机直连，不受调用方 shell 的代理环境变量影响。
_OPENER = build_opener(ProxyHandler({}))


def _iso(ts: float) -> str:
    """本地时区的 ISO 时间，精确到秒。"""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _short(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def http_get(url: str) -> Tuple[int, bytes]:
    """返回 (status, body)；连接失败抛 RuntimeError，HTTP 4xx/5xx 正常返回。"""
    req = Request(
        url,
        headers={
            "User-Agent": "huellaX-launcher/1.0 (+probe)",
            "Connection": "close",
        },
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT_PER_REQUEST) as resp:
            return resp.status, resp.read(MAX_BODY)
    except HTTPError as exc:  # 503 等启动期响应也当作一次失败尝试
        try:
            body = exc.read(MAX_BODY)
        except Exception:  # noqa: BLE001 - 探针不应因读响应失败而崩溃
            body = b""
        return exc.code, body
    except (URLError, OSError) as exc:
        raise RuntimeError(_short(f"{type(exc).__name__}: {exc}")) from exc


def _check_once(kind: str, url: str, expect_service: str) -> Optional[str]:
    """成功返回 None，失败返回可读原因。"""
    try:
        code, body = http_get(url)
    except RuntimeError as exc:
        return str(exc)
    if code != 200:
        return f"HTTP {code}（期望 200）: {_short(body.decode('utf-8', 'replace'), 120)!r}"
    if kind == "view":
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"HTTP 200 但响应不是 JSON: {_short(str(exc), 100)}"
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return f"HTTP 200 但 ok != true: {_short(json.dumps(payload, ensure_ascii=False), 120)}"
    if expect_service and payload.get("service") != expect_service:
        return (
            "HTTP 200 但 service 不是期望值: "
            f"{payload.get('service')!r} != {expect_service!r}"
        )
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    last = "尚未尝试"
    attempts = 0
    while True:
        attempts += 1
        if args.pid and not _pid_alive(args.pid):
            print(f"进程 {args.pid} 已退出（健康检查中止）")
            return 1
        last = _check_once(args.kind, args.url, args.expect_service) or ""
        if not last:
            detail = ""
            try:
                code, body = http_get(args.url)
                if code == 200 and args.kind != "view":
                    payload = json.loads(body.decode("utf-8"))
                    if args.kind == "db":
                        # DB 工具演进出 api schema 后，health 同时有 schema_version（API）
                        # 与 db_schema_version（SQLite）。优先报告后者，避免把 1.0 当库版本。
                        if "db_schema_version" in payload:
                            detail = f" schema={payload['db_schema_version']}"
                        elif "schema_version" in payload:
                            detail = f" schema={payload['schema_version']}"
                    if args.kind == "app" and "version" in payload:
                        detail = f" version={payload['version']}"
            except Exception:  # noqa: BLE001 - 成功结果不因附加信息失败而改写
                pass
            print(f"ok{detail}（{attempts} 次探测）")
            return 0
        if time.monotonic() >= deadline:
            print(f"超时 {args.timeout:g}s（{attempts} 次探测）；最后一次：{last}")
            return 1
        time.sleep(args.interval)


def _read_schema(db_path: Path) -> str:
    if not db_path.is_file():
        return "unavailable（数据库文件不存在）"
    uri = "file:" + quote(str(db_path)) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=3.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"unavailable（{_short(str(exc), 120)}）"
    return str(row[0]) if row else "unknown（meta 无 schema_version）"


def cmd_schema(args: argparse.Namespace) -> int:
    value = _read_schema(Path(args.db).expanduser().resolve())
    print(value)
    return 0 if value.isdigit() else 1


def cmd_latest(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"unavailable（目录不存在: {root}）")
        return 1
    best: Optional[Tuple[float, str, int]] = None
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if best is None or st.st_mtime > best[0]:
                best = (st.st_mtime, full, st.st_size)
    if best is None:
        print(f"unavailable（目录为空: {root}）")
        return 1
    mtime, full, size = best
    print(f"{_iso(mtime)}\t{os.path.relpath(full, root)}\t{size} bytes")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一启动器只读探针")
    sub = parser.add_subparsers(dest="command", required=True)

    wait = sub.add_parser("wait", help="轮询 HTTP 健康检查")
    wait.add_argument("--kind", choices=("db", "app", "view"), required=True)
    wait.add_argument("--url", required=True)
    wait.add_argument("--expect-service", default="")
    wait.add_argument("--pid", type=int, default=0, help="健康检查期间若该进程退出则立即失败")
    wait.add_argument("--timeout", type=float, default=15.0)
    wait.add_argument("--interval", type=float, default=0.2)
    wait.set_defaults(func=cmd_wait)

    schema = sub.add_parser("schema", help="只读读取 DB schema_version")
    schema.add_argument("--db", required=True)
    schema.set_defaults(func=cmd_schema)

    latest = sub.add_parser("latest", help="目录内最新修改文件")
    latest.add_argument("--path", required=True)
    latest.set_defaults(func=cmd_latest)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
