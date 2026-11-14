#!/usr/bin/env python3
"""T1 · 验收与契约工具（Acceptance & Contract Harness）。

一条命令验证“三个工具是否仍然满足官方要求与跨工具契约”：
- A 契约：Target / EpochRef / Coverage / AnalysisResult
- B 导出对账：cli.py export all + tools/check_compat.py
- C 5 目标 HTTP：5 × POST /api/analyze
- D 任意坐标：R1 核心（自定义 Barnard / RW Aur / 未覆盖天区 / 可选 DB 停机）
- E 错误路径：sample / FOV / target+坐标冲突 / 非法 JSON / 缺字段
- F 离线：黑洞代理下启动 8810 子进程，5 目标从缓存跑通
- G DB 一致性：/api/v1/epochs 两入口同形、计数、字段、coverage、header_frames
- H 视图工具：静态资源 + 源码静态检查（可选/手动，不阻塞 P0）

只用 Python stdlib，不依赖 pytest/jsonschema/playwright。只读三个工具，
所有写入都在 report 目录与 /tmp。

退出码：0 无 P0 失败；1 有 P0 失败；2 环境错误（服务不可达/子进程起不来）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from contracts import analysis, coverage, epoch_ref
from contracts import target as target_contract
from contracts import worst as worst_severity

ACCEPT_DIR = Path(__file__).resolve().parent
WORKSPACE = ACCEPT_DIR.parents[1] / "系统"
DB_TOOL_DIR = WORKSPACE / "数据库工具"
APP_SERVER = WORKSPACE / "app" / "server.py"
VIEW_MAIN_JS = WORKSPACE / "视图工具" / "js" / "main.js"
VIEW_TARGETS = WORKSPACE / "视图工具" / "data" / "targets.json"
DB_SQLITE = DB_TOOL_DIR / "var" / "catalog.sqlite"

OFFICIAL_TARGETS = ("barnard", "epsind_b", "wise0855", "rw_aur", "r_dra")
GROUP_NAMES = {
    "A": "契约（Target / EpochRef / Coverage / AnalysisResult）",
    "B": "导出对账（export + check_compat）",
    "C": "5 目标 HTTP",
    "D": "任意坐标（R1 核心）",
    "E": "错误路径",
    "F": "离线（黑洞代理）",
    "G": "DB 一致性（v1）",
    "H": "视图工具（可选）",
    "ENV": "环境/源码漂移",
}
GROUP_ORDER = ("A", "B", "C", "D", "E", "F", "G", "H", "ENV")
SOURCE_FILES = (
    "app/server.py",
    "app/core/data.py",
    "app/core/pipeline.py",
    "数据库工具/db/server.py",
    "数据库工具/db/catalog.py",
    "数据库工具/db/config.py",
    "视图工具/js/main.js",
)
DATA_FILES = (
    "app/data/targets.json",
    "app/data/epoch_catalog.json",
    "app/data/week_index.json",
    "视图工具/data/fields.json",
    "视图工具/data/targets.json",
    "数据库工具/var/catalog.sqlite",
)


# --------------------------------------------------------------------------- 基础工具
def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def tail_text(path: Path, limit: int = 60) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-limit:])


def find_lines(text: str, needle: str, limit: int = 8) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            out.append(f"{i}: {line.strip()}")
            if len(out) >= limit:
                break
    return out


def curl_repro(url: str, payload=None, method: str = "GET") -> str:
    if payload is None:
        return f"curl -s {shlex.quote(url)}"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        f"curl -s -X {method} {shlex.quote(url)} "
        f"-H 'Content-Type: application/json' -d {shlex.quote(body)}"
    )


def mu_matches(actual, expected, sigma=None, pct: float = 5.0):
    """μ 是否在 ±pct% 或 ±1σ 内。expected=None 表示黄金样本没有 μ。"""
    if expected is None:
        return True, "黄金 μ 为 null，跳过"
    if actual is None:
        return False, "实际 μ 缺失"
    tol_pct = abs(float(expected)) * pct / 100.0
    tol_sigma = abs(float(sigma)) if sigma is not None else 0.0
    tol = max(tol_pct, tol_sigma)
    diff = abs(float(actual) - float(expected))
    return diff <= tol, f"Δ={diff:.1f}，容差={tol:.1f}（±{pct:g}% 或 ±1σ）"


def _fmt_mu(value) -> str:
    if value is None:
        return "null"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return repr(value)


class HttpResponse:
    def __init__(self, url, method, status, headers, body, error, elapsed_ms):
        self.url = url
        self.method = method
        self.status = status
        self.headers = headers or {}
        self.body = body or b""
        self.error = error
        self.elapsed_ms = elapsed_ms
        self.json_data = None
        if self.body:
            try:
                self.json_data = json.loads(self.body.decode("utf-8"))
            except Exception:
                self.json_data = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def snippet(self, limit: int = 240) -> str:
        if self.error:
            return self.error
        if self.json_data is not None:
            return json.dumps(self.json_data, ensure_ascii=False)[:limit]
        return self.text[:limit]


class HttpClient:
    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
        # 验收自身永远直连 127.0.0.1，不受 --offline 的黑洞代理影响。
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> HttpResponse:
        started = time.perf_counter()
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        status = None
        resp_headers: dict = {}
        resp_body = b""
        error = None
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                resp_headers = dict(resp.headers.items())
                resp_body = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers = dict(exc.headers.items()) if exc.headers else {}
            try:
                resp_body = exc.read()
            except Exception:
                resp_body = b""
            error = f"HTTP {exc.code}"
        except Exception as exc:  # URLError / RemoteDisconnected / timeout
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return HttpResponse(url, method, status, resp_headers, resp_body, error, elapsed_ms)

    def get(self, url: str, headers: dict | None = None) -> HttpResponse:
        return self.request("GET", url, headers=headers)

    def post(self, url: str, body: bytes, headers: dict | None = None) -> HttpResponse:
        return self.request("POST", url, body=body, headers=headers)

    def post_json(self, url: str, payload, headers: dict | None = None) -> HttpResponse:
        h = {"Content-Type": "application/json; charset=utf-8"}
        h.update(headers or {})
        return self.post(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"), h)


class Context:
    def __init__(self, args, report_dir: Path):
        self.args = args
        self.report_dir = report_dir
        self.artifacts_dir = report_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.http = HttpClient(args.http_timeout)
        self.quick_http = HttpClient(min(2.0, args.http_timeout))
        self.base_url = args.base_url.rstrip("/")
        self.db_url = args.db_url.rstrip("/")
        self.view_url = args.view_url.rstrip("/")
        self.cases: list[dict] = []
        self.not_run: list[dict] = []
        self.notes: list[str] = []
        self.services: dict = {}
        self.data_snapshot: dict = {}
        self.source_before: dict = {}
        self.source_after: dict = {}
        self.source_drift: list[str] = []
        self.git: dict = {}
        self.golden: dict = {}
        self.golden_custom: dict = {}
        self.online: dict[str, dict] = {}
        self.env_error: str | None = None
        self.children: list[dict] = []
        self.started_at = iso_now()
        self.started_perf = time.perf_counter()
        self.invocation = " ".join(shlex.quote(x) for x in ["./run.sh", *sys.argv[1:]])
        self.view_available = False
        self.db_available = False
        self.db_status = "unchecked"
        self.db_block_reason: str | None = None
        self.export_tmp: str | None = None

    @property
    def elapsed_s(self) -> float:
        return round(time.perf_counter() - self.started_perf, 2)


# --------------------------------------------------------------------------- 报告
def save_artifact(ctx: Context, name: str, content) -> str:
    path = ctx.artifacts_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    elif isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(str(content), encoding="utf-8")
    return f"artifacts/{name}"


def save_response(ctx: Context, name: str, resp: HttpResponse) -> str:
    if resp.json_data is not None:
        return save_artifact(ctx, name, resp.json_data)
    if resp.body:
        return save_artifact(ctx, name, resp.body)
    return save_artifact(ctx, name, {"url": resp.url, "status": resp.status, "error": resp.error})


def add_case(
    ctx: Context,
    cid: str,
    group: str,
    severity: str,
    status: str,
    expected: str,
    actual: str,
    evidence: str | None = None,
    repro: str | None = None,
    details: list[dict] | None = None,
    duration_ms: float | None = None,
) -> None:
    case = {
        "id": cid,
        "group": group,
        "severity": severity,
        "status": status,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "repro": repro,
        "duration_ms": duration_ms,
    }
    if details:
        case["details"] = details
    ctx.cases.append(case)
    line = f"[{status}] {cid} ({severity}) {actual}"
    print(line[:260], flush=True)


def add_blocked(
    ctx: Context,
    cid: str,
    group: str,
    severity: str,
    expected: str,
    actual: str,
    reason: str,
    evidence: str | None = None,
    repro: str | None = None,
) -> None:
    """依赖服务/数据不可用：不算 PASS，也不算 P0 FAIL，单列 BLOCKED。"""
    case = {
        "id": cid,
        "group": group,
        "severity": severity,
        "status": "BLOCKED",
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "repro": repro,
        "duration_ms": None,
        "blocked_reason": reason,
    }
    ctx.cases.append(case)
    line = f"[BLOCKED] {cid} ({severity}) {actual}"
    print(line[:260], flush=True)


def problems_text(problems: list[dict], limit: int = 4) -> str:
    if not problems:
        return ""
    text = "；".join(p["message"] for p in problems[:limit])
    if len(problems) > limit:
        text += f" …（共 {len(problems)} 项）"
    return text


def case_from_problems(
    ctx: Context,
    cid: str,
    group: str,
    nominal: str,
    expected: str,
    problems: list[dict],
    actual_pass: str,
    evidence: str | None = None,
    repro: str | None = None,
    duration_ms: float | None = None,
) -> None:
    severity = worst_severity(problems) or nominal
    if problems:
        add_case(
            ctx,
            cid,
            group,
            severity,
            "FAIL",
            expected,
            problems_text(problems),
            evidence,
            repro,
            [dict(p) for p in problems],
            duration_ms,
        )
    else:
        add_case(ctx, cid, group, nominal, "PASS", expected, actual_pass, evidence, repro, None, duration_ms)


# --------------------------------------------------------------------------- 环境探针
def probe_services(ctx: Context) -> list[str]:
    missing: list[str] = []
    app = ctx.http.get(ctx.base_url + "/api/health")
    save_response(ctx, "env/app_health.json", app)
    app_ok = app.status == 200 and isinstance(app.json_data, dict) and app.json_data.get("ok") is True
    ctx.services["app"] = {
        "url": ctx.base_url,
        "http": app.status,
        "ok": app_ok,
        "version": (app.json_data or {}).get("version") if isinstance(app.json_data, dict) else None,
        "health": app.json_data if isinstance(app.json_data, dict) else app.snippet(200),
    }
    if not app_ok:
        missing.append(f"app {ctx.base_url}/api/health 不可用（HTTP {app.status}，{app.snippet(120)}）")

    # DB 不再作为全局环境错误：DB 停止/旧 schema 只让 DB 相关用例 BLOCKED，
    # app-only 用例（C/E/F/B/H 等）继续跑，便于回答“红在哪、哪些是 DB 停止导致”。
    db = ctx.http.get(ctx.db_url + "/api/health")
    save_response(ctx, "env/db_health.json", db)
    db_ok = db.status == 200 and isinstance(db.json_data, dict) and db.json_data.get("ok") is True
    v1_url = ctx.db_url + "/api/v1/epochs?" + urllib.parse.urlencode({"target_id": "barnard"})
    v1 = ctx.http.get(v1_url)
    save_response(ctx, "env/db_v1_probe.json", v1)
    v1_ok = (
        v1.status == 200
        and isinstance(v1.json_data, dict)
        and isinstance(v1.json_data.get("epochs"), list)
    )
    ctx.db_available = bool(db_ok and v1_ok)
    if not db_ok:
        ctx.db_status = "unreachable"
        ctx.db_block_reason = (
            f"DB 服务不可达（{ctx.db_url}/api/health HTTP {db.status}，{db.snippet(100)}）；"
            "DB 相关用例 BLOCKED，未改用 legacy 端口通过"
        )
    elif not v1_ok:
        ctx.db_status = "legacy-only"
        ctx.db_block_reason = (
            f"DB 服务可达但 /api/v1/epochs HTTP {v1.status}"
            f"（schema_version={(db.json_data or {}).get('schema_version')!r}；"
            f"{v1.snippet(100)}）；契约不可验证，DB 相关用例 BLOCKED"
        )
    else:
        ctx.db_status = "v1-ok"
        ctx.db_block_reason = None
    ctx.services["db"] = {
        "url": ctx.db_url,
        "http": db.status,
        "ok": db_ok,
        "contract_ok": ctx.db_available,
        "status": ctx.db_status,
        "block_reason": ctx.db_block_reason,
        "version": (db.json_data or {}).get("version") if isinstance(db.json_data, dict) else None,
        "health": db.json_data if isinstance(db.json_data, dict) else db.snippet(200),
        "v1_probe": {
            "url": v1_url,
            "http": v1.status,
            "ok": v1_ok,
            "body": v1.json_data if v1.json_data is not None else v1.snippet(200),
        },
    }

    view = ctx.http.get(ctx.view_url + "/")
    save_response(ctx, "env/view_root.txt", view)
    view_ok = view.status == 200
    ctx.view_available = view_ok
    ctx.services["view"] = {
        "url": ctx.view_url,
        "http": view.status,
        "ok": view_ok,
        "version": "static",
        "health": "index.html" if view_ok else view.snippet(160),
    }
    # view 是可选伴侣，不进入环境错误。
    return missing


def snapshot_sources() -> dict:
    out = {}
    for rel in SOURCE_FILES:
        path = WORKSPACE / rel
        if path.exists():
            out[rel] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return out


def snapshot_data(ctx: Context) -> dict:
    files = {}
    for rel in DATA_FILES:
        path = WORKSPACE / rel
        if not path.exists():
            files[rel] = {"missing": True}
            continue
        stat = path.stat()
        files[rel] = {
            "sha256": sha256_file(path),
            "bytes": stat.st_size,
            "mtime": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(timespec="seconds"),
        }

    counts = {}
    targets = read_json_file(WORKSPACE / "app/data/targets.json")
    if isinstance(targets, dict) and isinstance(targets.get("targets"), list):
        counts["app_targets"] = len(targets["targets"])
    epochs = read_json_file(WORKSPACE / "app/data/epoch_catalog.json")
    if isinstance(epochs, dict) and isinstance(epochs.get("epochs"), dict):
        counts["app_epochs"] = sum(len(v) for v in epochs["epochs"].values() if isinstance(v, list))
    fields = read_json_file(WORKSPACE / "视图工具/data/fields.json")
    if isinstance(fields, dict) and isinstance(fields.get("fields"), list):
        counts["view_fields"] = len(fields["fields"])
    cache = WORKSPACE / "app/data/epochcache"
    if cache.is_dir():
        npz = list(cache.glob("*.npz"))
        counts["epochcache_npz"] = len(npz)
        counts["epochcache_bytes"] = sum(p.stat().st_size for p in npz)
    db_service = (ctx.services.get("db") or {}).get("health") or {}
    if isinstance(db_service, dict):
        counts["db"] = db_service.get("counts")
    return {"files": files, "counts": counts}


def git_info() -> dict:
    def run(args: list[str]):
        try:
            return subprocess.run(
                ["git", "-C", str(WORKSPACE), *args],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            return None

    head = run(["rev-parse", "--short", "HEAD"])
    if head is None or head.returncode != 0:
        return {"status": "no-git", "detail": "工作区不是 git 仓库"}
    tag = run(["describe", "--tags", "--always"])
    return {
        "status": "git",
        "commit": head.stdout.strip(),
        "describe": tag.stdout.strip() if tag is not None and tag.returncode == 0 else "no-tag",
    }


def load_golden(ctx: Context) -> None:
    path = ACCEPT_DIR / "golden" / "five_targets.json"
    custom = ACCEPT_DIR / "golden" / "custom_barnard.json"
    ctx.golden = json.loads(path.read_text(encoding="utf-8"))
    ctx.golden_custom = json.loads(custom.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- A. 契约
def _target_source(ctx: Context, key: str) -> dict:
    if key == "app":
        resp = ctx.http.get(ctx.base_url + "/api/targets")
        return {
            "label": "app /api/targets",
            "url": ctx.base_url + "/api/targets",
            "resp": resp,
            "payload": resp.json_data,
            "evidence": save_response(ctx, "A.targets_app.json", resp),
        }
    if key == "db":
        url = ctx.db_url + "/api/v1/targets"
        if not ctx.db_available:
            # 只做 legacy 诊断留证，不把 legacy 当 PASS。
            legacy_url = ctx.db_url + "/api/targets"
            legacy = ctx.http.get(legacy_url)
            evidence = save_response(ctx, "A.targets_db_legacy_probe.json", legacy)
            return {
                "label": "db /api/v1/targets",
                "url": url,
                "resp": None,
                "payload": None,
                "evidence": evidence,
                "blocked": ctx.db_block_reason,
                "legacy": {
                    "url": legacy_url,
                    "http": legacy.status,
                    "count": len(legacy.json_data.get("targets", []))
                    if isinstance(legacy.json_data, dict) and isinstance(legacy.json_data.get("targets"), list)
                    else None,
                },
            }
        resp = ctx.http.get(url)
        used = "/api/v1/targets"
        if resp.status == 404:
            url = ctx.db_url + "/api/targets"
            resp = ctx.http.get(url)
            used = "/api/targets"
        return {
            "label": f"db {used}",
            "url": url,
            "resp": resp,
            "payload": resp.json_data,
            "evidence": save_response(ctx, "A.targets_db.json", resp),
        }
    # view：优先 HTTP，失败再用本地文件（view 是可选伴侣）
    resp = ctx.http.get(ctx.view_url + "/data/targets.json")
    payload = resp.json_data
    used = ctx.view_url + "/data/targets.json"
    evidence = save_response(ctx, "A.targets_view.json", resp)
    if payload is None:
        payload = read_json_file(VIEW_TARGETS)
        used = str(VIEW_TARGETS)
        evidence = save_artifact(ctx, "A.targets_view_local.json", payload or {})
    return {
        "label": "view data/targets.json",
        "url": used,
        "resp": resp,
        "payload": payload,
        "evidence": evidence,
    }


def run_a(ctx: Context) -> None:
    sources = {key: _target_source(ctx, key) for key in ("app", "db", "view")}
    validated = {}
    for key, nominal in (("app", "P0"), ("db", "P0"), ("view", "P1")):
        source = sources[key]
        result = target_contract.validate_payload(source["payload"], source["label"])
        validated[key] = result
        actual_pass = f"{source['label']}：{result['count']} 条；字段齐全；coord_unc_arcsec 非空"
        case_from_problems(
            ctx,
            f"A.targets.{key}",
            "A",
            nominal,
            "7 条目标；8 个契约字段齐全；coord_unc_arcsec 非空",
            result["problems"],
            actual_pass,
            source["evidence"],
            curl_repro(source["url"]),
        )

    parity_db = target_contract.compare(
        validated["app"]["by_id"], validated["db"]["by_id"], "app", "db", severity="P0"
    )
    case_from_problems(
        ctx,
        "A.targets.app_db",
        "A",
        "P0",
        "app 与 DB 的 7 条目标逐字段一致（含 coord_unc_arcsec / truth）",
        parity_db,
        "7 条目标全部一致",
        f"{sources['app']['evidence']} , {sources['db']['evidence']}",
        "见 A.targets.app / A.targets.db 的复现命令",
    )

    parity_view = target_contract.compare(
        validated["app"]["by_id"], validated["view"]["by_id"], "app", "view", severity="P1"
    )
    case_from_problems(
        ctx,
        "A.targets.view_parity",
        "A",
        "P1",
        "view 的 targets 与 app 逐字段一致（视图工具为可选伴侣，偏差按 P1）",
        parity_view,
        "view 与 app 目标字段一致",
        sources["view"]["evidence"],
        curl_repro(sources["view"]["url"]),
    )

    # ---------------------------------------------------------------- EpochRef
    def v1_epochs(params: dict, artifact: str) -> tuple[str, HttpResponse]:
        url = ctx.db_url + "/api/v1/epochs?" + urllib.parse.urlencode(params)
        resp = ctx.http.get(url)
        save_response(ctx, artifact, resp)
        return url, resp

    barnard_target_url, barnard_target = v1_epochs({"target_id": "barnard"}, "A.epochs_barnard_target.json")
    barnard_coord_url, barnard_coord = v1_epochs(
        {"ra": 269.452077, "dec": 4.693365, "fov": 0.25}, "A.epochs_barnard_coord.json"
    )
    rw_target_url, rw_target = v1_epochs({"target_id": "rw_aur"}, "A.epochs_rw_target.json")
    rw_coord_url, rw_coord = v1_epochs(
        {"ra": 76.956526, "dec": 30.401438, "fov": 0.25}, "A.epochs_rw_coord.json"
    )

    def epoch_case(cid, expected_count, pairs, label, evidence_files):
        problems: list[dict] = []
        summaries = []
        for name, url, resp in pairs:
            if resp.status != 200 or not isinstance(resp.json_data, dict):
                problems.append(
                    {
                        "code": "EPOCH_HTTP",
                        "severity": "P0",
                        "message": f"{name}: HTTP {resp.status}（{resp.snippet(120)}）",
                    }
                )
                continue
            payload = resp.json_data
            epochs = payload.get("epochs")
            summaries.append(f"{name} {epoch_ref.summary(epochs)}")
            problems.extend(epoch_ref.validate_epochs(epochs, name))
            if isinstance(epochs, list) and len(epochs) != expected_count:
                problems.append(
                    {
                        "code": "EPOCH_COUNT",
                        "severity": "P0",
                        "message": f"{name}: {len(epochs)} 条，期望 {expected_count} 条",
                    }
                )
            if payload.get("schema_version") != "1.0":
                problems.append(
                    {
                        "code": "EPOCH_SCHEMA",
                        "severity": "P0",
                        "message": f"{name}: schema_version={payload.get('schema_version')!r}，期望 '1.0'",
                    }
                )
            radius = ((payload.get("query") or {}).get("radius")) if isinstance(payload.get("query"), dict) else None
            if radius is not None and abs(float(radius) - 2.47) > 1e-9:
                problems.append(
                    {
                        "code": "EPOCH_RADIUS",
                        "severity": "P0",
                        "message": f"{name}: query.radius={radius}，期望 2.47",
                    }
                )

        groups = {name: (resp.json_data or {}).get("epochs", []) for name, _, resp in pairs}
        labels = {name: name for name, _, _ in pairs}
        problems.extend(epoch_ref.compare(groups, labels))
        evidence = " , ".join(f"artifacts/{artifact}" for artifact in evidence_files)
        repro = " ; ".join(curl_repro(url) for _, url, _ in pairs)
        case_from_problems(
            ctx,
            cid,
            "A",
            "P0",
            f"{label}：两入口各 {expected_count} 条，十字段非空、date_obs 非空、无重复、逐字段一致",
            problems,
            "；".join(summaries) + "；两入口逐字段一致",
            evidence,
            repro,
        )

    epoch_case(
        "A.epoch_ref.barnard",
        4,
        [("target_id=barnard", barnard_target_url, barnard_target), ("ra/dec", barnard_coord_url, barnard_coord)],
        "Barnard",
        ["A.epochs_barnard_target.json", "A.epochs_barnard_coord.json"],
    )
    epoch_case(
        "A.epoch_ref.rw_aur",
        1,
        [("target_id=rw_aur", rw_target_url, rw_target), ("ra/dec", rw_coord_url, rw_coord)],
        "RW Aur",
        ["A.epochs_rw_target.json", "A.epochs_rw_coord.json"],
    )

    # ---------------------------------------------------------------- Coverage
    cov_url = ctx.db_url + "/api/v1/coverage?ra=269.452077&dec=4.693365"
    cov = ctx.http.get(cov_url)
    cov_evidence = save_response(ctx, "A.coverage_barnard.json", cov)
    cov_problems = []
    if cov.status != 200 or not isinstance(cov.json_data, dict):
        cov_problems.append(
            {"code": "COVERAGE_HTTP", "severity": "P0", "message": f"HTTP {cov.status}（{cov.snippet(120)}）"}
        )
    else:
        cov_problems.extend(coverage.validate_coverage(cov.json_data, "db /api/v1/coverage"))
    case_from_problems(
        ctx,
        "A.coverage",
        "A",
        "P0",
        "Coverage：radius_deg 默认 2.47；coverage_level 以 C 开头；baseline_days 数值；不返回 readiness",
        cov_problems,
        coverage.summary(cov.json_data) if isinstance(cov.json_data, dict) else "",
        cov_evidence,
        curl_repro(cov_url),
    )

    # ---------------------------------------------------------------- AnalysisResult
    analysis_url = ctx.base_url + "/api/analyze"
    payload = {"target_id": "barnard", "mode": "auto", "fov": 0.25}
    resp = ctx.http.post_json(analysis_url, payload)
    evidence = save_response(ctx, "A.analysis_barnard.json", resp)
    contract_problems: list[dict] = []
    real_epochs_problems: list[dict] = []
    actual = f"HTTP {resp.status}（{resp.snippet(120)}）"
    if resp.status != 200 or not isinstance(resp.json_data, dict):
        contract_problems.append(
            {"code": "ANALYSIS_HTTP", "severity": "P0", "message": f"HTTP {resp.status}（{resp.snippet(120)}）"}
        )
    else:
        doc = resp.json_data
        contract_problems.extend(analysis.contract_problems(doc, "app /api/analyze"))
        real_epochs_problems = analysis.real_epochs_problems(doc, "app /api/analyze")
        summary = analysis.summary(doc)
        actual = (
            f"HTTP 200；mode={summary.get('mode')}；schema_version={summary.get('schema_version')!r}；"
            f"readiness_scope={summary.get('readiness_scope')!r}；real_epochs={summary.get('real_epochs')!r}"
        )
    case_from_problems(
        ctx,
        "A.analysis_schema",
        "A",
        "P0",
        "AnalysisResult：schema_version=1.0、data.mode/provenance/fallback_reason、result.class/readiness/readiness_scope 齐全",
        contract_problems,
        actual,
        evidence,
        curl_repro(analysis_url, payload, "POST"),
    )
    case_from_problems(
        ctx,
        "A.analysis_real_epochs",
        "A",
        "P1",
        "data.real_epochs 存在且为 int（T2 后必须）",
        real_epochs_problems,
        "data.real_epochs 存在且合法",
        evidence,
        curl_repro(analysis_url, payload, "POST"),
    )


# --------------------------------------------------------------------------- B. 导出对账
def _run(argv: list[str], cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def run_b(ctx: Context) -> None:
    tmp = tempfile.mkdtemp(prefix="t1_export_", dir="/tmp")
    ctx.export_tmp = tmp
    export_argv = [sys.executable, "cli.py", "export", "all", "--out", tmp]
    export = _run(export_argv, DB_TOOL_DIR)
    export_log = save_artifact(
        ctx,
        "B.export.log",
        f"$ {' '.join(shlex.quote(x) for x in export_argv)}\n"
        f"exit={export.returncode}\n--- stdout ---\n{export.stdout}\n--- stderr ---\n{export.stderr}\n",
    )
    expected_files = ("fields.json", "epoch_catalog.json", "targets.json", "week_index.json")
    missing_files = [name for name in expected_files if not (Path(tmp) / name).exists()]
    export_problems = []
    if export.returncode != 0:
        export_problems.append(
            {
                "code": "EXPORT_FAILED",
                "severity": "P0",
                "message": f"cli.py export all 退出码 {export.returncode}: {export.stderr.strip()[:160]}",
            }
        )
    if missing_files:
        export_problems.append(
            {
                "code": "EXPORT_FILES",
                "severity": "P0",
                "message": f"缺导出文件 {missing_files}",
            }
        )
    counts = {}
    for name in expected_files:
        payload = read_json_file(Path(tmp) / name)
        if isinstance(payload, dict):
            if name == "fields.json" and isinstance(payload.get("fields"), list):
                counts[name] = len(payload["fields"])
            elif name == "epoch_catalog.json" and isinstance(payload.get("epochs"), dict):
                counts[name] = sum(len(v) for v in payload["epochs"].values() if isinstance(v, list))
            elif name == "targets.json" and isinstance(payload.get("targets"), list):
                counts[name] = len(payload["targets"])
            elif name == "week_index.json" and isinstance(payload.get("weeks"), list):
                counts[name] = len(payload["weeks"])
    case_from_problems(
        ctx,
        "B.export",
        "B",
        "P0",
        "cli.py export all --out <tmp> 退出码 0，生成 4 个导出 JSON",
        export_problems,
        f"退出码 0；4/4 文件；计数 {counts}",
        export_log,
        f"cd 数据库工具 && {shlex.quote(sys.executable)} cli.py export all --out {shlex.quote(tmp)}",
    )

    compat_argv = [sys.executable, "tools/check_compat.py", "--root", "..", "--exports", tmp]
    compat = _run(compat_argv, DB_TOOL_DIR)
    compat_log = save_artifact(
        ctx,
        "B.check_compat.log",
        f"$ {' '.join(shlex.quote(x) for x in compat_argv)}\n"
        f"exit={compat.returncode}\n--- stdout ---\n{compat.stdout}\n--- stderr ---\n{compat.stderr}\n",
    )
    ok_lines = re.findall(r"(?m)^(\S+)\s+OK\b", compat.stdout)
    compat_problems = []
    if compat.returncode != 0:
        compat_problems.append(
            {
                "code": "COMPAT_FAILED",
                "severity": "P0",
                "message": f"check_compat.py 退出码 {compat.returncode}: {compat.stderr.strip()[:160]}",
            }
        )
    if len(ok_lines) != 4:
        compat_problems.append(
            {
                "code": "COMPAT_OK_COUNT",
                "severity": "P0",
                "message": f"只解析到 {len(ok_lines)}/4 项 OK：{ok_lines}",
            }
        )
    if "全部兼容检查通过" not in compat.stdout:
        compat_problems.append(
            {"code": "COMPAT_SUMMARY", "severity": "P0", "message": "输出没有‘全部兼容检查通过’"}
        )
    for line in compat.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            compat_problems.append(
                {"code": "COMPAT_ITEM", "severity": "P0", "message": stripped[2:].strip()}
            )
    case_from_problems(
        ctx,
        "B.check_compat",
        "B",
        "P0",
        "check_compat.py 四项 OK（fields / epoch_catalog / targets / week_index）",
        compat_problems,
        f"四项 OK：{ok_lines}；全部兼容检查通过",
        compat_log,
        f"cd 数据库工具 && {shlex.quote(sys.executable)} tools/check_compat.py --root .. --exports {shlex.quote(tmp)}",
    )
    ctx.notes.append(f"B 组导出目录（临时）：{tmp}；未写入 数据库工具/var/exports")


# --------------------------------------------------------------------------- C. 5 目标
def post_analyze(ctx: Context, payload: dict, artifact: str) -> HttpResponse:
    resp = ctx.http.post_json(ctx.base_url + "/api/analyze", payload)
    save_response(ctx, artifact, resp)
    return resp


def golden_target_problems(tid: str, doc: dict, elapsed_ms: float, golden: dict) -> list[dict]:
    problems: list[dict] = []
    g = golden["targets"][tid]
    summary = analysis.summary(doc)
    mode = summary.get("mode")
    if mode != g["mode"]:
        problems.append(
            {
                "code": "GOLDEN_MODE",
                "severity": "P0",
                "message": f"mode={mode!r}，黄金={g['mode']!r}",
            }
        )
    if summary.get("readiness") != g["readiness"]:
        problems.append(
            {
                "code": "GOLDEN_READINESS",
                "severity": "P0",
                "message": f"readiness={summary.get('readiness')!r}，黄金={g['readiness']!r}",
            }
        )
    if summary.get("class") != g["class"]:
        problems.append(
            {
                "code": "GOLDEN_CLASS",
                "severity": "P0",
                "message": f"class={summary.get('class')!r}，黄金={g['class']!r}",
            }
        )
    if summary.get("n_epochs") != g["n_epochs"]:
        problems.append(
            {
                "code": "GOLDEN_N_EPOCHS",
                "severity": "P1",
                "message": f"n_epochs={summary.get('n_epochs')!r}，黄金={g['n_epochs']!r}",
            }
        )
    ok, detail = mu_matches(
        summary.get("mu_mas_yr"),
        g.get("mu_mas_yr"),
        g.get("mu_sigma_mas_yr"),
        golden.get("tolerance", {}).get("mu_pct", 5.0),
    )
    if not ok:
        problems.append({"code": "GOLDEN_MU", "severity": "P0", "message": f"μ {detail}"})
    limit_ms = float(golden.get("tolerance", {}).get("duration_s", 30.0)) * 1000.0
    if elapsed_ms > limit_ms:
        problems.append(
            {
                "code": "DURATION",
                "severity": "P1",
                "message": f"耗时 {elapsed_ms/1000:.2f}s > {limit_ms/1000:.0f}s",
            }
        )
    return problems


def run_c(ctx: Context) -> None:
    for tid in OFFICIAL_TARGETS:
        payload = {"target_id": tid, "mode": "auto", "fov": 0.25}
        resp = post_analyze(ctx, payload, f"C.{tid}.json")
        evidence = f"artifacts/C.{tid}.json"
        problems: list[dict] = []
        actual = f"HTTP {resp.status}（{resp.snippet(140)}）"
        doc = resp.json_data if isinstance(resp.json_data, dict) else None
        summary = {}
        if resp.status != 200 or doc is None:
            problems.append(
                {
                    "code": "ANALYZE_HTTP",
                    "severity": "P0",
                    "message": f"HTTP {resp.status}（{resp.snippet(160)}）",
                }
            )
        else:
            summary = analysis.summary(doc)
            problems.extend(analysis.shape_problems(doc, f"C.{tid}"))
            problems.extend(golden_target_problems(tid, doc, resp.elapsed_ms, ctx.golden))
            actual = (
                f"HTTP 200；mode={summary.get('mode')}；n={summary.get('n_epochs')}；"
                f"{summary.get('readiness')}；{summary.get('class')}；μ={_fmt_mu(summary.get('mu_mas_yr'))}；"
                f"{resp.elapsed_ms/1000:.2f}s"
            )
        ctx.online[tid] = {
            "http": resp.status,
            "summary": summary,
            "elapsed_ms": resp.elapsed_ms,
            "evidence": evidence,
        }
        g = ctx.golden["targets"][tid]
        case_from_problems(
            ctx,
            f"C.{tid}",
            "C",
            "P0",
            f"HTTP 200；mode={g['mode']}；n={g['n_epochs']}；{g['readiness']}；{g['class']}；"
            + ("μ 无黄金值；" if g.get("mu_mas_yr") is None else f"μ≈{_fmt_mu(g.get('mu_mas_yr'))}（±5% 或 ±1σ）；")
            + "<30s",
            problems,
            actual,
            evidence,
            curl_repro(ctx.base_url + "/api/analyze", payload, "POST"),
            resp.elapsed_ms,
        )


# --------------------------------------------------------------------------- D. 任意坐标
def _custom_payload(ra: float, dec: float, fov: float = 0.25) -> dict:
    return {"ra": ra, "dec": dec, "fov": fov}


def run_d(ctx: Context) -> None:
    # D1：Barnard 精确坐标
    payload = _custom_payload(269.452077, 4.693365)
    resp = post_analyze(ctx, payload, "D.custom_barnard.json")
    evidence = "artifacts/D.custom_barnard.json"
    problems: list[dict] = []
    actual = f"HTTP {resp.status}（{resp.snippet(140)}）"
    if resp.status != 200 or not isinstance(resp.json_data, dict):
        problems.append(
            {"code": "CUSTOM_HTTP", "severity": "P0", "message": f"HTTP {resp.status}（{resp.snippet(160)}）"}
        )
    else:
        doc = resp.json_data
        summary = analysis.summary(doc)
        expected = ctx.golden_custom["expected"]
        problems.extend(analysis.shape_problems(doc, "D.custom_barnard"))
        if summary.get("mode") != expected["mode"]:
            problems.append(
                {
                    "code": "CUSTOM_MODE",
                    "severity": "P0",
                    "message": f"mode={summary.get('mode')!r}，期望 {expected['mode']!r}",
                }
            )
        if summary.get("n_epochs") != expected["n_epochs"]:
            problems.append(
                {
                    "code": "CUSTOM_N",
                    "severity": "P0",
                    "message": f"n_epochs={summary.get('n_epochs')!r}，期望 {expected['n_epochs']}",
                }
            )
        if summary.get("readiness") != expected["readiness"]:
            problems.append(
                {
                    "code": "CUSTOM_READINESS",
                    "severity": "P0",
                    "message": f"readiness={summary.get('readiness')!r}，期望 {expected['readiness']!r}",
                }
            )
        if summary.get("class") != expected["class"]:
            problems.append(
                {
                    "code": "CUSTOM_CLASS",
                    "severity": "P0",
                    "message": f"class={summary.get('class')!r}，期望 {expected['class']!r}",
                }
            )
        if summary.get("fallback_reason") is not None:
            problems.append(
                {
                    "code": "CUSTOM_FALLBACK",
                    "severity": "P0",
                    "message": f"fallback_reason={summary.get('fallback_reason')!r}，期望 null",
                }
            )
        real_epochs = summary.get("real_epochs")
        if not isinstance(real_epochs, int) or real_epochs < expected["real_epochs_min"]:
            problems.append(
                {
                    "code": "CUSTOM_REAL_EPOCHS",
                    "severity": "P0",
                    "message": f"real_epochs={real_epochs!r}，期望 ≥{expected['real_epochs_min']}",
                }
            )
        ok, detail = mu_matches(
            summary.get("mu_mas_yr"),
            expected.get("mu_mas_yr"),
            None,
            expected.get("mu_tol_pct", 1.0),
        )
        if not ok:
            problems.append({"code": "CUSTOM_MU", "severity": "P0", "message": f"μ {detail}"})
        actual = (
            f"HTTP 200；mode={summary.get('mode')}；n={summary.get('n_epochs')}；"
            f"real_epochs={summary.get('real_epochs')}；{summary.get('readiness')}；{summary.get('class')}；"
            f"fallback={summary.get('fallback_reason')}；μ={_fmt_mu(summary.get('mu_mas_yr'))}"
        )
    case_from_problems(
        ctx,
        "D.custom_barnard",
        "D",
        "P0",
        "无 target_id 的 Barnard 精确坐标：mode=real、4 期、real_epochs≥4、L2、S、fallback=null，与预设 μ 差 ≤1%",
        problems,
        actual,
        evidence,
        curl_repro(ctx.base_url + "/api/analyze", payload, "POST"),
        resp.elapsed_ms,
    )

    # D2：RW Aur 坐标至少 1 条真实历元
    payload = _custom_payload(76.956526, 30.401438)
    resp = post_analyze(ctx, payload, "D.custom_rw_aur.json")
    evidence = "artifacts/D.custom_rw_aur.json"
    problems = []
    actual = f"HTTP {resp.status}（{resp.snippet(140)}）"
    if resp.status != 200 or not isinstance(resp.json_data, dict):
        problems.append(
            {"code": "CUSTOM_RW_HTTP", "severity": "P0", "message": f"HTTP {resp.status}（{resp.snippet(160)}）"}
        )
    else:
        summary = analysis.summary(resp.json_data)
        real_epochs = summary.get("real_epochs")
        if summary.get("mode") not in ("real", "hybrid"):
            problems.append(
                {
                    "code": "CUSTOM_RW_MODE",
                    "severity": "P0",
                    "message": f"mode={summary.get('mode')!r}，期望 real 或 hybrid",
                }
            )
        if not isinstance(real_epochs, int) or real_epochs < 1:
            problems.append(
                {
                    "code": "CUSTOM_RW_REAL_EPOCHS",
                    "severity": "P0",
                    "message": f"real_epochs={real_epochs!r}，期望 ≥1",
                }
            )
        if summary.get("fallback_reason") == "NO_LOCAL_COVERAGE":
            problems.append(
                {
                    "code": "CUSTOM_RW_COVERAGE",
                    "severity": "P0",
                    "message": "fallback_reason=NO_LOCAL_COVERAGE，但 RW Aur 有 1 条真实历元",
                }
            )
        if not isinstance(summary.get("n_epochs"), int) or summary.get("n_epochs") < 1:
            problems.append(
                {
                    "code": "CUSTOM_RW_N",
                    "severity": "P0",
                    "message": f"n_epochs={summary.get('n_epochs')!r}，期望 ≥1",
                }
            )
        actual = (
            f"HTTP 200；mode={summary.get('mode')}；n={summary.get('n_epochs')}；"
            f"real_epochs={summary.get('real_epochs')}；fallback={summary.get('fallback_reason')}"
        )
    case_from_problems(
        ctx,
        "D.custom_rw_aur",
        "D",
        "P0",
        "RW Aur 坐标：HTTP 200、mode=real|hybrid、real_epochs≥1、不得 NO_LOCAL_COVERAGE/0",
        problems,
        actual,
        evidence,
        curl_repro(ctx.base_url + "/api/analyze", payload, "POST"),
        resp.elapsed_ms,
    )

    # D3：未覆盖天区
    payload = _custom_payload(0.0, 0.0)
    db_url = ctx.db_url + "/api/v1/epochs?" + urllib.parse.urlencode({"ra": 0.0, "dec": 0.0, "fov": 0.25})
    db_resp = ctx.http.get(db_url)
    save_response(ctx, "D.uncovered_db.json", db_resp)
    resp = post_analyze(ctx, payload, "D.uncovered.json")
    evidence = "artifacts/D.uncovered.json"
    problems = []
    actual = f"HTTP {resp.status}（{resp.snippet(140)}）"
    if db_resp.status != 200 or not isinstance(db_resp.json_data, dict):
        problems.append(
            {
                "code": "UNCOVERED_DB",
                "severity": "P0",
                "message": f"DB v1 查询失败 HTTP {db_resp.status}（{db_resp.snippet(120)}）",
            }
        )
    elif db_resp.json_data.get("count") != 0:
        problems.append(
            {
                "code": "UNCOVERED_DB_COUNT",
                "severity": "P0",
                "message": f"DB 返回 {db_resp.json_data.get('count')} 条，期望 0",
            }
        )
    if resp.status != 200 or not isinstance(resp.json_data, dict):
        problems.append(
            {"code": "UNCOVERED_HTTP", "severity": "P0", "message": f"HTTP {resp.status}（{resp.snippet(160)}）"}
        )
    else:
        summary = analysis.summary(resp.json_data)
        if summary.get("mode") != "synthetic":
            problems.append(
                {
                    "code": "UNCOVERED_MODE",
                    "severity": "P0",
                    "message": f"mode={summary.get('mode')!r}，期望 synthetic",
                }
            )
        if summary.get("fallback_reason") != "NO_LOCAL_COVERAGE":
            problems.append(
                {
                    "code": "UNCOVERED_FALLBACK",
                    "severity": "P0",
                    "message": f"fallback_reason={summary.get('fallback_reason')!r}，期望 NO_LOCAL_COVERAGE",
                }
            )
        if summary.get("real_epochs") not in (0, None):
            problems.append(
                {
                    "code": "UNCOVERED_REAL_EPOCHS",
                    "severity": "P0",
                    "message": f"real_epochs={summary.get('real_epochs')!r}，期望 0",
                }
            )
        note = (resp.json_data.get("data") or {}).get("note") or ""
        if not note.strip():
            problems.append({"code": "UNCOVERED_NOTE", "severity": "P0", "message": "data.note 为空"})
        elif not any(word in note for word in ("模拟", "SIM", "覆盖", "无")):
            problems.append(
                {
                    "code": "UNCOVERED_NOTE_EXPLAIN",
                    "severity": "P0",
                    "message": f"note 未明确说明降级原因：{note[:80]}",
                }
            )
        actual = (
            f"HTTP 200；mode={summary.get('mode')}；fallback={summary.get('fallback_reason')}；"
            f"real_epochs={summary.get('real_epochs')}；note={note[:80]}"
        )
    case_from_problems(
        ctx,
        "D.uncovered",
        "D",
        "P0",
        "RA0/Dec0 本地无覆盖：DB count=0；mode=synthetic、fallback_reason=NO_LOCAL_COVERAGE、real_epochs=0、note 明确",
        problems,
        actual,
        evidence,
        curl_repro(ctx.base_url + "/api/analyze", payload, "POST"),
        resp.elapsed_ms,
    )

    # D4（可选）：DB 停机
    if not ctx.args.db_down:
        ctx.not_run.append({"case": "D.db_down", "reason": "未指定 --db-down（T5.4 为可选用例）"})
        return
    if ctx.args.no_start:
        add_case(
            ctx,
            "D.db_down",
            "D",
            "P0",
            "WARN",
            "DB 停机：预设 Barnard 仍 real 4；自定义 Barnard 显式 DB_UNAVAILABLE/SIM，不 500",
            "未执行：--no-start 与 --db-down 冲突",
        )
        return

    env = {
        "SPHEREX_DB_URL": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    base, err, log_path = start_isolated_app(ctx, ctx.args.offline_port, env, "D.db_down_app.log")
    if err:
        add_case(
            ctx,
            "D.db_down",
            "D",
            "P0",
            "FAIL",
            "DB 停机：预设 Barnard 仍 real 4；自定义 Barnard 显式 DB_UNAVAILABLE/SIM，不 500",
            f"子进程启动失败：{err}",
            "artifacts/D.db_down_app.log",
        )
        ctx.env_error = f"D.db_down 子进程启动失败：{err}"
        return
    try:
        preset = HttpClient(ctx.args.http_timeout).post_json(
            base + "/api/analyze", {"target_id": "barnard", "mode": "auto", "fov": 0.25}
        )
        save_response(ctx, "D.db_down_preset.json", preset)
        preset_problems = []
        if preset.status != 200 or not isinstance(preset.json_data, dict):
            preset_problems.append(
                {"code": "DB_DOWN_PRESET_HTTP", "severity": "P0", "message": f"HTTP {preset.status}"}
            )
        else:
            summary = analysis.summary(preset.json_data)
            if summary.get("mode") != "real" or summary.get("n_epochs") != 4:
                preset_problems.append(
                    {
                        "code": "DB_DOWN_PRESET",
                        "severity": "P0",
                        "message": f"mode={summary.get('mode')!r} n={summary.get('n_epochs')!r}，期望 real/4",
                    }
                )
        case_from_problems(
            ctx,
            "D.db_down.preset",
            "D",
            "P0",
            "DB 停机时预设 Barnard 仍从本地 JSON 回退：real 4 期",
            preset_problems,
            "real 4 期",
            "artifacts/D.db_down_preset.json",
            f"SPHEREX_DB_URL=http://127.0.0.1:9 {curl_repro(base + '/api/analyze', {'target_id': 'barnard', 'mode': 'auto', 'fov': 0.25}, 'POST')}",
        )

        custom = HttpClient(ctx.args.http_timeout).post_json(
            base + "/api/analyze", {"ra": 269.452077, "dec": 4.693365, "fov": 0.25}
        )
        save_response(ctx, "D.db_down_custom.json", custom)
        custom_problems = []
        if custom.status != 200 or not isinstance(custom.json_data, dict):
            custom_problems.append(
                {"code": "DB_DOWN_CUSTOM_HTTP", "severity": "P0", "message": f"HTTP {custom.status}"}
            )
        else:
            summary = analysis.summary(custom.json_data)
            if summary.get("fallback_reason") != "DB_UNAVAILABLE" and summary.get("mode") not in (
                "synthetic",
                "unavailable",
            ):
                custom_problems.append(
                    {
                        "code": "DB_DOWN_CUSTOM",
                        "severity": "P0",
                        "message": f"fallback={summary.get('fallback_reason')!r} mode={summary.get('mode')!r}，"
                        "期望 DB_UNAVAILABLE 或显式 SIM/UNAVAILABLE",
                    }
                )
        case_from_problems(
            ctx,
            "D.db_down.custom",
            "D",
            "P0",
            "DB 停机时自定义 Barnard 不 500，显式 DB_UNAVAILABLE/SIM",
            custom_problems,
            "显式降级，无 500",
            "artifacts/D.db_down_custom.json",
            f"SPHEREX_DB_URL=http://127.0.0.1:9 {curl_repro(base + '/api/analyze', {'ra': 269.452077, 'dec': 4.693365, 'fov': 0.25}, 'POST')}",
        )
    finally:
        stop_isolated_apps(ctx)
        if log_path:
            ctx.notes.append(f"D.db_down 子进程日志：{log_path.name}")


# --------------------------------------------------------------------------- E. 错误路径
def run_e(ctx: Context) -> None:
    url = ctx.base_url + "/api/analyze"

    def raw_post(body: bytes, artifact: str) -> HttpResponse:
        resp = ctx.http.post(url, body, {"Content-Type": "application/json; charset=utf-8"})
        if resp.json_data is not None:
            save_artifact(ctx, artifact, resp.json_data)
        else:
            save_artifact(ctx, artifact, resp.text or (resp.error or ""))
        return resp

    def error_code(resp: HttpResponse):
        if isinstance(resp.json_data, dict):
            err = resp.json_data.get("error")
            if isinstance(err, dict):
                return err.get("code"), err.get("message")
            if isinstance(err, str):
                return None, err
        return None, resp.snippet(160)

    # sample：200 或 422，不能 500
    resp = raw_post(json.dumps({"target_id": "barnard", "mode": "sample", "fov": 0.25}).encode(), "E.sample.json")
    problems = []
    if resp.status == 500:
        problems.append({"code": "SAMPLE_500", "severity": "P0", "message": f"HTTP 500（{resp.snippet(160)}）"})
    elif resp.status not in (200, 422):
        problems.append(
            {"code": "SAMPLE_STATUS", "severity": "P0", "message": f"HTTP {resp.status}，期望 200/422"}
        )
    elif resp.status == 200:
        mode = analysis.summary(resp.json_data).get("mode")
        if mode != "sample":
            problems.append(
                {"code": "SAMPLE_MODE", "severity": "P1", "message": f"mode={mode!r}，期望 sample"}
            )
    actual = f"HTTP {resp.status}"
    if isinstance(resp.json_data, dict) and resp.json_data.get("data"):
        actual += f"；mode={resp.json_data['data'].get('mode')}"
    case_from_problems(
        ctx,
        "E.sample",
        "E",
        "P0",
        "mode=sample 返回 200 或 422，不是 500",
        problems,
        actual,
        "artifacts/E.sample.json",
        curl_repro(url, {"target_id": "barnard", "mode": "sample", "fov": 0.25}, "POST"),
        resp.elapsed_ms,
    )

    for tag, fov in (("negative", -0.2), ("zero", 0), ("large", 999)):
        resp = raw_post(
            json.dumps({"target_id": "barnard", "mode": "auto", "fov": fov}).encode(), f"E.fov_{tag}.json"
        )
        problems = []
        if resp.status != 400:
            severity = "P0"
            if resp.status == 500:
                msg = f"HTTP 500（{resp.snippet(160)}）"
            else:
                msg = f"HTTP {resp.status}，期望 400"
            problems.append({"code": f"FOV_{tag.upper()}", "severity": severity, "message": msg})
        case_from_problems(
            ctx,
            f"E.fov.{tag}",
            "E",
            "P0",
            f"fov={fov} 返回 400（不是 500）",
            problems,
            f"HTTP {resp.status}",
            f"artifacts/E.fov_{tag}.json",
            curl_repro(url, {"target_id": "barnard", "mode": "auto", "fov": fov}, "POST"),
            resp.elapsed_ms,
        )

    # target_id + 冲突坐标
    payload = {"target_id": "barnard", "ra": 10, "dec": 10, "fov": 0.25}
    resp = raw_post(json.dumps(payload).encode(), "E.target_conflict.json")
    code, msg = error_code(resp)
    problems = []
    if resp.status == 400 and code == "TARGET_COORD_CONFLICT":
        pass
    elif resp.status == 200 and isinstance(resp.json_data, dict):
        tgt = resp.json_data.get("target") or {}
        try:
            overridden = abs(float(tgt.get("ra")) - 10) <= 1e-6 and abs(float(tgt.get("dec")) - 10) <= 1e-6
        except (TypeError, ValueError):
            overridden = False
        if overridden:
            pass  # 明确 override
        else:
            problems.append(
                {
                    "code": "CONFLICT_SILENT",
                    "severity": "P1",
                    "message": f"200 且 target={tgt.get('ra')},{tgt.get('dec')}，静默返回预设",
                }
            )
    else:
        problems.append(
            {
                "code": "CONFLICT_STATUS",
                "severity": "P1",
                "message": f"HTTP {resp.status} code={code}，期望 400 TARGET_COORD_CONFLICT 或明确 override",
            }
        )
    case_from_problems(
        ctx,
        "E.target_coord_conflict",
        "E",
        "P1",
        "target_id=barnard + ra=10&dec=10：400 TARGET_COORD_CONFLICT，或明确 override；不得静默返回 Barnard",
        problems,
        f"HTTP {resp.status} code={code}",
        "artifacts/E.target_conflict.json",
        curl_repro(url, payload, "POST"),
        resp.elapsed_ms,
    )

    # 非法 JSON
    resp = raw_post(b'{"ra": 1,', "E.bad_json.json")
    problems = []
    if resp.status != 400:
        problems.append(
            {
                "code": "BAD_JSON_STATUS",
                "severity": "P1",
                "message": f"HTTP {resp.status}，期望 400（不是 500）",
            }
        )
    case_from_problems(
        ctx,
        "E.bad_json",
        "E",
        "P1",
        "非法 JSON 返回 400，不是 500",
        problems,
        f"HTTP {resp.status}",
        "artifacts/E.bad_json.json",
        "curl -s -X POST " + shlex.quote(url) + " -H 'Content-Type: application/json' -d '{\"ra\": 1,'",
        resp.elapsed_ms,
    )

    # 缺字段
    resp = raw_post(b"{}", "E.missing.json")
    problems = []
    if resp.status != 400:
        problems.append(
            {
                "code": "MISSING_STATUS",
                "severity": "P1",
                "message": f"HTTP {resp.status}，期望 400（不是 500）",
            }
        )
    case_from_problems(
        ctx,
        "E.missing",
        "E",
        "P1",
        "缺 ra/dec/target_id 返回 400，不是 500",
        problems,
        f"HTTP {resp.status}",
        "artifacts/E.missing.json",
        "curl -s -X POST " + shlex.quote(url) + " -H 'Content-Type: application/json' -d '{}'",
        resp.elapsed_ms,
    )


# --------------------------------------------------------------------------- 子进程管理
def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def start_isolated_app(ctx: Context, port: int, env_extra: dict, log_name: str):
    """启动一个只属于验收的 app 子进程；绝不动用户已在跑的服务。"""
    if _port_in_use("127.0.0.1", port):
        return None, f"端口 {port} 已被占用（验收不会杀别人的进程）", None
    log_path = ctx.artifacts_dir / log_name
    log_fh = open(log_path, "wb")
    env = os.environ.copy()
    env.update({k: str(v) for k, v in env_extra.items()})
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [sys.executable, "-u", str(APP_SERVER), "--port", str(port), "--host", "127.0.0.1"]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(WORKSPACE),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        log_fh.close()
        return None, f"Popen 失败：{exc}", log_path
    ctx.children.append({"proc": proc, "log": log_fh, "log_path": log_path})
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 40
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            last = f"子进程提前退出（code={proc.returncode}）"
            break
        probe = ctx.quick_http.get(base + "/api/health")
        if probe.status == 200 and isinstance(probe.json_data, dict) and probe.json_data.get("ok") is True:
            return base, None, log_path
        last = probe.error or f"HTTP {probe.status}"
        time.sleep(0.4)
    stop_isolated_apps(ctx)
    tail = tail_text(log_path, 40)
    return None, f"健康检查超时（{last}）\n{tail}", log_path


def stop_isolated_apps(ctx: Context) -> None:
    for child in ctx.children:
        proc = child.get("proc")
        log_fh = child.get("log")
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        if proc is not None:
            try:
                proc.wait(timeout=6)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        if log_fh is not None and not log_fh.closed:
            try:
                log_fh.close()
            except Exception:
                pass
    ctx.children.clear()


# --------------------------------------------------------------------------- F. 离线
def run_f(ctx: Context) -> None:
    if not ctx.args.offline:
        ctx.not_run.append({"case": "F", "reason": "未指定 --offline（T7 需要黑洞代理子进程）"})
        return
    if ctx.args.no_start:
        add_case(
            ctx,
            "F.offline",
            "F",
            "P0",
            "WARN",
            "黑洞代理下 8810 子进程跑 5 目标，5/5 与在线一致",
            "未执行：--no-start 禁止启动隔离子进程",
        )
        return

    proxy = "http://127.0.0.1:9"
    env = {
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "https_proxy": proxy,
        "http_proxy": proxy,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    base, err, log_path = start_isolated_app(ctx, ctx.args.offline_port, env, "F.offline_app.log")
    if err:
        add_case(
            ctx,
            "F.offline.startup",
            "F",
            "P0",
            "FAIL",
            "在 8810 启动 app 子进程（黑洞代理）并健康检查通过",
            f"启动失败：{err}",
            "artifacts/F.offline_app.log",
        )
        ctx.env_error = f"F 离线子进程启动失败：{err}"
        return

    client = HttpClient(ctx.args.http_timeout)
    passed = 0
    try:
        for tid in OFFICIAL_TARGETS:
            payload = {"target_id": tid, "mode": "auto", "fov": 0.25}
            resp = client.post_json(base + "/api/analyze", payload)
            save_response(ctx, f"F.{tid}.json", resp)
            problems = []
            actual = f"HTTP {resp.status}（{resp.snippet(140)}）"
            if resp.status != 200 or not isinstance(resp.json_data, dict):
                problems.append(
                    {
                        "code": "OFFLINE_HTTP",
                        "severity": "P0",
                        "message": f"HTTP {resp.status}（{resp.snippet(160)}）",
                    }
                )
            else:
                doc = resp.json_data
                summary = analysis.summary(doc)
                problems.extend(analysis.shape_problems(doc, f"F.{tid}"))
                problems.extend(golden_target_problems(tid, doc, resp.elapsed_ms, ctx.golden))
                on = (ctx.online.get(tid) or {}).get("summary") or {}
                for key in ("mode", "readiness", "class"):
                    if on.get(key) is not None and summary.get(key) != on.get(key):
                        problems.append(
                            {
                                "code": f"OFFLINE_ONLINE_{key.upper()}",
                                "severity": "P0",
                                "message": f"{key}: 离线={summary.get(key)!r} vs 在线={on.get(key)!r}",
                            }
                        )
                if on.get("mu_mas_yr") is not None and summary.get("mu_mas_yr") is not None:
                    ok, detail = mu_matches(summary.get("mu_mas_yr"), on.get("mu_mas_yr"), None, 5.0)
                    if not ok:
                        problems.append(
                            {"code": "OFFLINE_ONLINE_MU", "severity": "P0", "message": f"μ 离线/在线 {detail}"}
                        )
                if summary.get("fallback_reason") == "FETCH_FAILED":
                    problems.append(
                        {
                            "code": "OFFLINE_FETCH_FAILED",
                            "severity": "P0",
                            "message": f"FETCH_FAILED（缓存缺失；见 artifacts/F.offline_app.log）",
                        }
                    )
                actual = (
                    f"HTTP 200；mode={summary.get('mode')}；n={summary.get('n_epochs')}；"
                    f"{summary.get('readiness')}；{summary.get('class')}；μ={_fmt_mu(summary.get('mu_mas_yr'))}；"
                    f"{resp.elapsed_ms/1000:.2f}s"
                )
                if not problems:
                    passed += 1
            g = ctx.golden["targets"][tid]
            case_from_problems(
                ctx,
                f"F.offline.{tid}",
                "F",
                "P0",
                f"离线 HTTP 200；mode={g['mode']}；{g['readiness']}；{g['class']}；μ 与在线差 ≤5%",
                problems,
                actual,
                f"artifacts/F.{tid}.json",
                "HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost "
                + curl_repro(f"http://127.0.0.1:{ctx.args.offline_port}/api/analyze", payload, "POST"),
                resp.elapsed_ms,
            )
    finally:
        stop_isolated_apps(ctx)

    add_case(
        ctx,
        "F.offline.5of5",
        "F",
        "P0",
        "PASS" if passed == len(OFFICIAL_TARGETS) else "FAIL",
        "离线 5/5 目标成功且与在线一致",
        f"{passed}/{len(OFFICIAL_TARGETS)} 成功",
        "artifacts/F.offline_app.log",
        "见 F.offline.<target> 用例",
    )
    if log_path:
        ctx.notes.append(f"F 离线子进程日志：{log_path.name}")


# --------------------------------------------------------------------------- G. DB 一致性
EXPECTED_DB_COUNTS = {"epsind_b": 10, "wise0855": 10, "r_dra": 3}


def run_g(ctx: Context) -> None:
    def v1(params: dict, artifact: str) -> tuple[str, HttpResponse]:
        url = ctx.db_url + "/api/v1/epochs?" + urllib.parse.urlencode(params)
        resp = ctx.http.get(url)
        save_response(ctx, artifact, resp)
        return url, resp

    pairs: dict[str, dict] = {}
    for tid, ra, dec in (
        ("barnard", 269.452077, 4.693365),
        ("rw_aur", 76.956526, 30.401438),
        ("epsind_b", 331.044147, -56.782844),
        ("wise0855", 133.795129, -7.245147),
        ("r_dra", 248.167611, 66.754979),
    ):
        t_url, t_resp = v1({"target_id": tid}, f"G.{tid}.target.json")
        c_url, c_resp = v1({"ra": ra, "dec": dec, "fov": 0.25}, f"G.{tid}.coord.json")
        pairs[tid] = {
            "target": (t_url, t_resp),
            "coord": (c_url, c_resp),
            "ra": ra,
            "dec": dec,
        }

    def parity_case(cid: str, tid: str, expected_count: int) -> None:
        target_url, target_resp = pairs[tid]["target"]
        coord_url, coord_resp = pairs[tid]["coord"]
        problems: list[dict] = []
        summaries = []
        for name, resp in (("target_id", target_resp), ("ra/dec", coord_resp)):
            if resp.status != 200 or not isinstance(resp.json_data, dict):
                problems.append(
                    {
                        "code": "V1_HTTP",
                        "severity": "P0",
                        "message": f"{tid}/{name}: HTTP {resp.status}（{resp.snippet(120)}）",
                    }
                )
                continue
            payload = resp.json_data
            epochs = payload.get("epochs")
            summaries.append(f"{name} {len(epochs) if isinstance(epochs, list) else '?'} 条")
            if isinstance(epochs, list) and len(epochs) != expected_count:
                problems.append(
                    {
                        "code": "V1_COUNT",
                        "severity": "P0",
                        "message": f"{tid}/{name}: {len(epochs)} 条，期望 {expected_count}",
                    }
                )
            problems.extend(epoch_ref.validate_epochs(epochs, f"{tid}/{name}"))
            if payload.get("schema_version") != "1.0":
                problems.append(
                    {
                        "code": "V1_SCHEMA",
                        "severity": "P0",
                        "message": f"{tid}/{name}: schema_version={payload.get('schema_version')!r}",
                    }
                )
            query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
            if query.get("radius") is not None and abs(float(query["radius"]) - 2.47) > 1e-9:
                problems.append(
                    {
                        "code": "V1_RADIUS",
                        "severity": "P0",
                        "message": f"{tid}/{name}: query.radius={query.get('radius')}，期望 2.47",
                    }
                )
        groups = {
            "target_id": (target_resp.json_data or {}).get("epochs", []),
            "ra/dec": (coord_resp.json_data or {}).get("epochs", []),
        }
        problems.extend(epoch_ref.compare(groups, {"target_id": "target_id", "ra/dec": "ra/dec"}))
        case_from_problems(
            ctx,
            cid,
            "G",
            "P0",
            f"{tid}：两入口各 {expected_count} 条、逐字段一致、date_obs/key 非空、无重复 (obsid,detector)",
            problems,
            "；".join(summaries) + "；逐字段一致",
            f"artifacts/G.{tid}.target.json , artifacts/G.{tid}.coord.json",
            f"{curl_repro(target_url)} ; {curl_repro(coord_url)}",
        )

    parity_case("G.v1_epochs.barnard", "barnard", 4)
    parity_case("G.v1_epochs.rw_aur", "rw_aur", 1)

    # 计数 10/10/3（T8 的明确期望）
    count_problems: list[dict] = []
    actual_counts = {}
    for tid, expected_count in EXPECTED_DB_COUNTS.items():
        target = pairs[tid]["target"][1]
        coord = pairs[tid]["coord"][1]
        for name, resp in (("target_id", target), ("ra/dec", coord)):
            payload = resp.json_data if isinstance(resp.json_data, dict) else {}
            actual = payload.get("count")
            actual_counts[f"{tid}.{name}"] = actual
            if actual != expected_count:
                count_problems.append(
                    {
                        "code": "V1_COUNT_EXPECTED",
                        "severity": "P0",
                        "message": f"{tid}/{name}: count={actual}，期望 {expected_count}",
                    }
                )
    case_from_problems(
        ctx,
        "G.v1_epochs.counts",
        "G",
        "P0",
        "Eps/WISE/R Dra 两入口条数一致且为 10/10/3",
        count_problems,
        f"实际 {actual_counts}",
        "artifacts/G.*.target.json / artifacts/G.*.coord.json",
        "见 G.v1_epochs.barnard 的 curl 形式",
    )

    # 全量字段
    field_problems: list[dict] = []
    total = 0
    for tid, entry in pairs.items():
        for name in ("target", "coord"):
            resp = entry[name][1]
            payload = resp.json_data if isinstance(resp.json_data, dict) else {}
            epochs = payload.get("epochs")
            total += len(epochs) if isinstance(epochs, list) else 0
            field_problems.extend(epoch_ref.validate_epochs(epochs, f"{tid}/{name}"))
            if isinstance(epochs, list):
                for idx, e in enumerate(epochs):
                    if not isinstance(e, dict):
                        continue
                    for field in ("obsid", "detector", "release", "week", "version", "mjd", "date_obs", "sep_deg", "key", "slice_url", "provenance", "frame_id"):
                        if field not in e:
                            field_problems.append(
                                {
                                    "code": "V1_FIELD_MISSING",
                                    "severity": "P0",
                                    "message": f"{tid}/{name}[{idx}] 缺 {field}",
                                }
                            )
    case_from_problems(
        ctx,
        "G.v1_epochs.fields",
        "G",
        "P0",
        "全部 v1 历元：12 字段齐全、date_obs/key/slice_url 非空、无重复 (obsid,detector)",
        field_problems,
        f"5 目标 × 2 入口，共 {total} 条；字段检查通过",
        "artifacts/G.*.json",
        "见 G.v1_epochs.barnard 的 curl 形式",
    )

    # schema
    schema_problems = []
    for tid in ("barnard", "rw_aur"):
        for name in ("target", "coord"):
            resp = pairs[tid][name][1]
            payload = resp.json_data if isinstance(resp.json_data, dict) else {}
            if payload.get("schema_version") != "1.0":
                schema_problems.append(
                    {
                        "code": "V1_SCHEMA",
                        "severity": "P0",
                        "message": f"{tid}/{name}: schema_version={payload.get('schema_version')!r}",
                    }
                )
            query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
            if query.get("radius") != 2.47:
                schema_problems.append(
                    {
                        "code": "V1_QUERY_RADIUS",
                        "severity": "P0",
                        "message": f"{tid}/{name}: query.radius={query.get('radius')!r}，期望 2.47",
                    }
                )
            if payload.get("count") != len(payload.get("epochs") or []):
                schema_problems.append(
                    {
                        "code": "V1_COUNT_MISMATCH",
                        "severity": "P0",
                        "message": f"{tid}/{name}: count != len(epochs)",
                    }
                )
    case_from_problems(
        ctx,
        "G.v1_schema",
        "G",
        "P0",
        "v1 响应：schema_version=1.0、query.radius=2.47、count=len(epochs)",
        schema_problems,
        "schema 检查通过",
        "artifacts/G.*.json",
        "见 G.v1_epochs.barnard 的 curl 形式",
    )

    # coverage
    cov_url = ctx.db_url + "/api/v1/coverage?ra=269.452077&dec=4.693365"
    cov = ctx.http.get(cov_url)
    cov_evidence = save_response(ctx, "G.coverage_barnard.json", cov)
    cov_problems = []
    if cov.status != 200 or not isinstance(cov.json_data, dict):
        cov_problems.append(
            {"code": "V1_COVERAGE_HTTP", "severity": "P0", "message": f"HTTP {cov.status}（{cov.snippet(120)}）"}
        )
    else:
        cov_problems.extend(coverage.validate_coverage(cov.json_data, "db /api/v1/coverage"))
    case_from_problems(
        ctx,
        "G.v1_coverage",
        "G",
        "P0",
        "coverage 默认 radius=2.47、coverage_level 以 C 开头、baseline_days 数值",
        cov_problems,
        coverage.summary(cov.json_data) if isinstance(cov.json_data, dict) else "",
        cov_evidence,
        curl_repro(cov_url),
    )

    # header_frames 与实际一致
    health = ctx.http.get(ctx.db_url + "/api/v1/health")
    if health.status != 200:
        health = ctx.http.get(ctx.db_url + "/api/health")
    health_evidence = save_response(ctx, "G.health.json", health)
    header_problems = []
    actual_header = None
    truth_header = None
    if health.status != 200 or not isinstance(health.json_data, dict):
        header_problems.append(
            {"code": "G_HEALTH", "severity": "P0", "message": f"健康检查 HTTP {health.status}"}
        )
    else:
        counts = health.json_data.get("counts") or {}
        actual_header = counts.get("header_frames")
        if DB_SQLITE.exists():
            try:
                conn = sqlite3.connect(f"file:{DB_SQLITE}?mode=ro", uri=True)
                try:
                    truth_header = conn.execute(
                        "SELECT COUNT(*) FROM frames WHERE header_sha256 IS NOT NULL"
                    ).fetchone()[0]
                    truth_frames = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
                    truth_epochs = conn.execute("SELECT COUNT(*) FROM target_epochs").fetchone()[0]
                finally:
                    conn.close()
                if actual_header != truth_header:
                    header_problems.append(
                        {
                            "code": "G_HEADER_FRAMES",
                            "severity": "P0",
                            "message": f"health.header_frames={actual_header}，DB 实际 {truth_header}",
                        }
                    )
                if counts.get("frames") != truth_frames:
                    header_problems.append(
                        {
                            "code": "G_FRAMES",
                            "severity": "P0",
                            "message": f"health.frames={counts.get('frames')}，DB 实际 {truth_frames}",
                        }
                    )
                if counts.get("epochs") != truth_epochs:
                    header_problems.append(
                        {
                            "code": "G_EPOCHS",
                            "severity": "P0",
                            "message": f"health.epochs={counts.get('epochs')}，DB 实际 {truth_epochs}",
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                header_problems.append(
                    {
                        "code": "G_SQLITE",
                        "severity": "P1",
                        "message": f"无法只读核对 sqlite：{exc}",
                    }
                )
        else:
            header_problems.append(
                {
                    "code": "G_SQLITE",
                    "severity": "P1",
                    "message": f"本地 DB 文件不存在：{DB_SQLITE}",
                }
            )
    case_from_problems(
        ctx,
        "G.header_frames",
        "G",
        "P0",
        "health.header_frames 与 DB 实际一致（不要求 >0，但不能虚报）",
        header_problems,
        f"header_frames={actual_header}，DB 实际={truth_header}",
        health_evidence,
        curl_repro(ctx.db_url + "/api/v1/health"),
    )


# --------------------------------------------------------------------------- H. 视图工具
def run_h(ctx: Context) -> None:
    # 静态资源
    query_url = (
        ctx.view_url
        + "/?"
        + urllib.parse.urlencode(
            {"ra": 269.452077, "dec": 4.693365, "fov": 0.5, "target": "barnard"}
        )
    )
    resp = ctx.http.get(query_url)
    view_evidence = save_response(ctx, "H.view.html", resp)
    problems = []
    if not ctx.view_available:
        add_case(
            ctx,
            "H.view_http",
            "H",
            "P1",
            "WARN",
            "GET view/?ra=&dec=&fov=&target= 返回 200 静态 HTML",
            f"view 服务不可达（{ctx.view_url}）；按可选组件记 WARN",
        )
    else:
        ctype = ""
        for header_name, header_value in resp.headers.items():
            if str(header_name).lower() == "content-type":
                ctype = header_value
                break
        if resp.status != 200:
            problems.append(
                {"code": "VIEW_HTTP", "severity": "P1", "message": f"HTTP {resp.status}"}
            )
        elif "text/html" not in ctype.lower() and "<html" not in resp.text.lower():
            problems.append(
                {"code": "VIEW_CONTENT", "severity": "P1", "message": "不是 HTML"}
            )
        case_from_problems(
            ctx,
            "H.view_http",
            "H",
            "P1",
            "GET view/?ra=&dec=&fov=&target= 返回 200 静态 HTML",
            problems,
            f"HTTP {resp.status}；{ctype}",
            view_evidence,
            curl_repro(query_url),
        )

    try:
        js = VIEW_MAIN_JS.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        js = ""
        ctx.notes.append(f"H：无法读取 {VIEW_MAIN_JS}：{exc}")

    scan = {
        "file": str(VIEW_MAIN_JS),
        "app_index_hits": find_lines(js, "../app/index.html"),
        "head_hits": find_lines(js, "method: 'HEAD'") + find_lines(js, 'method: "HEAD"'),
        "tofixed5_hits": find_lines(js, "toFixed(5)"),
        "movescope_origin": find_lines(js, "MOVESCOPE_ORIGIN", 3),
    }
    scan_evidence = save_artifact(ctx, "H.static_scan.json", scan)

    static_cases = (
        (
            "H.static_no_app_link",
            "../app/index.html",
            scan["app_index_hits"],
            "main.js 不含已断的 ../app/index.html 链接",
        ),
        (
            "H.static_no_head",
            "method: 'HEAD'",
            scan["head_hits"],
            "main.js 不用 HEAD 探活（app 只实现 GET）",
        ),
        (
            "H.static_precision",
            "toFixed(5)",
            scan["tofixed5_hits"],
            "传给 MoveScope 的坐标不做 5 位截断（不得用 toFixed(5)）",
        ),
    )
    for cid, needle, hits, expected in static_cases:
        problems = []
        if not js:
            problems.append(
                {"code": "VIEW_JS_MISSING", "severity": "P1", "message": f"读不到 {VIEW_MAIN_JS}"}
            )
        elif hits:
            problems.append(
                {
                    "code": "VIEW_STATIC",
                    "severity": "P1",
                    "message": f"发现 {needle}: {hits}",
                }
            )
        case_from_problems(
            ctx,
            cid,
            "H",
            "P1",
            expected,
            problems,
            "未发现",
            scan_evidence,
            f"grep -n {shlex.quote(needle)} {shlex.quote(str(VIEW_MAIN_JS))}",
        )

    add_case(
        ctx,
        "H.screenshot",
        "H",
        "P1",
        "WARN",
        "可选：headless 浏览器截图并检查选中目标",
        "WARN: manual（本工具不引入 Playwright；视图工具不进入提交硬门槛）",
        scan_evidence,
    )


# --------------------------------------------------------------------------- golden 更新
def maybe_update_golden(ctx: Context) -> None:
    if not ctx.args.update_golden:
        return
    c_cases = [c for c in ctx.cases if c["group"] == "C"]
    c_green = bool(c_cases) and all(c["status"] == "PASS" for c in c_cases)
    if not c_green and not ctx.args.confirm:
        ctx.notes.append("--update-golden 未执行：C 组未全过；如确需覆盖，加 --confirm 人工确认")
        return
    if not ctx.args.confirm:
        if not sys.stdin.isatty():
            ctx.notes.append("--update-golden 未执行：非交互 shell，无法人工确认；如确认请加 --confirm")
            return
        answer = input("将用本次 C 组实测值覆盖 golden/five_targets.json，输入 yes 确认: ")
        if answer.strip().lower() != "yes":
            ctx.notes.append("--update-golden 已取消（未输入 yes）")
            return
    updated = json.loads(json.dumps(ctx.golden))
    changed = []
    for tid in OFFICIAL_TARGETS:
        summary = (ctx.online.get(tid) or {}).get("summary") or {}
        g = updated["targets"].get(tid)
        if not g or summary.get("mode") is None:
            ctx.notes.append(f"--update-golden 跳过 {tid}：无有效实测")
            continue
        g["mode"] = summary.get("mode")
        g["n_epochs"] = summary.get("n_epochs")
        g["readiness"] = summary.get("readiness")
        g["class"] = summary.get("class")
        g["mu_mas_yr"] = summary.get("mu_mas_yr")
        changed.append(tid)
    if changed:
        path = ACCEPT_DIR / "golden" / "five_targets.json"
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ctx.notes.append(f"--update-golden 已更新 {path}（目标：{changed}）")


# --------------------------------------------------------------------------- 报告写盘
def summarize(cases: list[dict]) -> dict:
    out = {"pass": 0, "fail": 0, "warn": 0, "p0_fail": 0, "p1_fail": 0}
    for case in cases:
        if case["status"] == "PASS":
            out["pass"] += 1
        elif case["status"] == "FAIL":
            out["fail"] += 1
            if case["severity"] == "P0":
                out["p0_fail"] += 1
            else:
                out["p1_fail"] += 1
        elif case["status"] == "WARN":
            out["warn"] += 1
    return out


def group_stats(cases: list[dict]) -> dict:
    stats = {}
    for group in GROUP_ORDER:
        rows = [c for c in cases if c["group"] == group]
        stats[group] = {
            "total": len(rows),
            "pass": sum(1 for c in rows if c["status"] == "PASS"),
            "fail": sum(1 for c in rows if c["status"] == "FAIL"),
            "warn": sum(1 for c in rows if c["status"] == "WARN"),
        }
    return stats


def split_evidence(value) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"\s*,\s*", str(value)) if part.strip()]


def evidence_md(value) -> str:
    parts = split_evidence(value)
    if not parts:
        return "—"
    if len(parts) == 1:
        return f"[证据]({parts[0]})"
    return " ".join(f"[证据{i + 1}]({part})" for i, part in enumerate(parts))


def md_cell(value, limit: int = 220) -> str:
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", "<br>")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def status_badge(status: str) -> str:
    return {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN"}.get(status, status)


def render_markdown(ctx: Context, exit_code: int, summary: dict, env_error: str | None) -> str:
    stats = group_stats(ctx.cases)
    if env_error:
        conclusion = f"环境错误（退出码 2）：{env_error}"
    elif summary["p0_fail"] > 0:
        conclusion = (
            f"**红**：P0 失败 {summary['p0_fail']} 条，P1 失败 {summary['p1_fail']} 条，"
            f"WARN {summary['warn']} 条，退出码 {exit_code}。"
        )
    else:
        conclusion = (
            f"**绿（无 P0 失败）**：PASS {summary['pass']} 条，P1 失败 {summary['p1_fail']} 条，"
            f"WARN {summary['warn']} 条，退出码 {exit_code}。"
        )

    app = ctx.services.get("app") or {}
    db = ctx.services.get("db") or {}
    view = ctx.services.get("view") or {}
    lines: list[str] = []
    lines.append("# T1 验收报告 · Acceptance & Contract Harness")
    lines.append("")
    lines.append(f"> 生成时间：{ctx.started_at} → {iso_now()}（耗时 {ctx.elapsed_s:.1f}s）")
    lines.append(f"> 结论：{conclusion}")
    lines.append("")
    lines.append("## 0. 复现命令")
    lines.append("")
    lines.append("```bash")
    lines.append("cd 项目主控/验收")
    lines.append(ctx.invocation)
    lines.append("echo $?")
    lines.append("```")
    lines.append("")
    lines.append("## 1. 环境")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| app | `{ctx.base_url}` · HTTP {app.get('http')} · version {app.get('version')} |")
    lines.append(f"| DB | `{ctx.db_url}` · HTTP {db.get('http')} · version {db.get('version')} |")
    lines.append(f"| view（可选） | `{ctx.view_url}` · HTTP {view.get('http')} |")
    lines.append(f"| Python | {platform.python_version()} · `{sys.executable}` |")
    lines.append(f"| 平台 | {platform.platform()} |")
    git = ctx.git or {}
    git_text = "no-git" if git.get("status") != "git" else f"{git.get('commit')} ({git.get('describe')})"
    lines.append(f"| git | {git_text} |")
    lines.append("| 黄金样本 | `golden/five_targets.json`（2026-11-14 快照） + `golden/custom_barnard.json` |")
    counts = (ctx.data_snapshot or {}).get("counts") or {}
    lines.append(f"| 数据快照 | {md_cell(json.dumps(counts, ensure_ascii=False))} |")
    drift = "有变化：" + ", ".join(ctx.source_drift) if ctx.source_drift else "运行期间源文件未变"
    lines.append(f"| 源码快照 | {drift} |")
    lines.append("")
    lines.append("### 1.1 数据文件快照")
    lines.append("")
    lines.append("| 文件 | 大小 | sha256 |")
    lines.append("|---|---|---|")
    for rel, info in ((ctx.data_snapshot or {}).get("files") or {}).items():
        if info.get("missing"):
            lines.append(f"| `{rel}` | 缺失 | — |")
        else:
            lines.append(
                f"| `{rel}` | {info.get('bytes', 0):,} | `{str(info.get('sha256', ''))[:16]}…` |"
            )
    lines.append("")
    lines.append("## 2. 汇总")
    lines.append("")
    lines.append("| 组 | 名称 | 用例 | PASS | FAIL | WARN |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for group in GROUP_ORDER:
        s = stats[group]
        if s["total"] == 0:
            continue
        lines.append(
            f"| {group} | {GROUP_NAMES[group]} | {s['total']} | {s['pass']} | {s['fail']} | {s['warn']} |"
        )
    lines.append(
        f"| **合计** |  | **{summary['pass'] + summary['fail'] + summary['warn']}** | "
        f"**{summary['pass']}** | **{summary['fail']}** | **{summary['warn']}** |"
    )
    lines.append("")

    lines.append("## 3. 用例结果")
    for group in GROUP_ORDER:
        rows = [c for c in ctx.cases if c["group"] == group]
        if not rows:
            continue
        lines.append("")
        lines.append(f"### {group}. {GROUP_NAMES[group]}")
        lines.append("")
        lines.append("| 用例 | 级别 | 结果 | 期望 | 实际 | 证据 |")
        lines.append("|---|---|---|---|---|---|")
        for case in rows:
            ev_text = evidence_md(case.get("evidence"))
            lines.append(
                f"| `{case['id']}` | {case['severity']} | {status_badge(case['status'])} | "
                f"{md_cell(case['expected'])} | {md_cell(case['actual'])} | {ev_text} |"
            )

    failures = [c for c in ctx.cases if c["status"] == "FAIL"]
    if failures:
        lines.append("")
        lines.append("## 4. 失败详情")
        for case in failures:
            lines.append("")
            lines.append(f"### `{case['id']}` · {case['severity']}")
            lines.append("")
            lines.append(f"- 期望：{case['expected']}")
            lines.append(f"- 实际：{case['actual']}")
            if case.get("repro"):
                lines.append("- 复现：")
                lines.append("")
                lines.append("```bash")
                lines.append(case["repro"])
                lines.append("```")
            for idx, part in enumerate(split_evidence(case.get("evidence")), 1):
                label = "证据" if len(split_evidence(case.get("evidence"))) == 1 else f"证据{idx}"
                lines.append(f"- {label}：[{part}]({part})")
            if case.get("details"):
                lines.append("- 明细：")
                for item in case["details"]:
                    lines.append(f"  - `{item.get('code')}`：{item.get('message')}")

    warnings = [c for c in ctx.cases if c["status"] == "WARN"]
    if warnings:
        lines.append("")
        lines.append("## 5. WARN / 手动项")
        for case in warnings:
            lines.append(f"- `{case['id']}`（{case['severity']}）：{case['actual']}")

    if ctx.not_run:
        lines.append("")
        lines.append("## 6. 未执行")
        for item in ctx.not_run:
            lines.append(f"- `{item['case']}`：{item['reason']}")

    if ctx.notes:
        lines.append("")
        lines.append("## 7. 备注")
        for note in ctx.notes:
            lines.append(f"- {note}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "说明：本工具只读 `app/`、`数据库工具/`、`视图工具/`；所有写入都在本报告目录与 `/tmp`。"
        "P0 失败退出码 1；环境错误退出码 2；P1（视图工具/非阻塞契约）失败不改变退出码。"
    )
    lines.append("")
    return "\n".join(lines)


def write_report(ctx: Context, exit_code: int, env_error: str | None) -> None:
    summary = summarize(ctx.cases)
    stats = group_stats(ctx.cases)
    report = {
        "schema_version": "1.0",
        "tool": "T1 Acceptance & Contract Harness",
        "started_at": ctx.started_at,
        "finished_at": iso_now(),
        "duration_s": ctx.elapsed_s,
        "base_url": ctx.base_url,
        "db_url": ctx.db_url,
        "view_url": ctx.view_url,
        "offline": bool(ctx.args.offline),
        "exit_code": exit_code,
        "environment_error": env_error,
        "environment": {
            "python": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "workspace": str(WORKSPACE),
            "git": ctx.git,
            "services": ctx.services,
            "data_snapshot": ctx.data_snapshot,
            "source_hashes": ctx.source_before,
            "source_drift": ctx.source_drift,
            "golden": {
                "five_targets": "golden/five_targets.json",
                "custom_barnard": "golden/custom_barnard.json",
                "source": ctx.golden.get("source") if ctx.golden else None,
            },
        },
        "cases": ctx.cases,
        "summary": summary,
        "groups": stats,
        "not_run": ctx.not_run,
        "notes": ctx.notes,
        "repro": ctx.invocation,
    }
    report_json = ctx.report_dir / "report.json"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md = ctx.report_dir / "report.md"
    report_md.write_text(render_markdown(ctx, exit_code, summary, env_error), encoding="utf-8")


# --------------------------------------------------------------------------- 选择器
def parse_selectors(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def group_enabled(args, group: str) -> bool:
    only = parse_selectors(args.only)
    skip = parse_selectors(args.skip)
    if any(sel.upper() == group for sel in skip):
        return False
    if only:
        return any(sel.upper() == group or sel.upper().startswith(group + ".") for sel in only)
    return True


def case_enabled(args, group: str, cid: str) -> bool:
    only = parse_selectors(args.only)
    skip = parse_selectors(args.skip)
    for sel in skip:
        if sel.upper() == group or cid == sel or cid.startswith(sel + "."):
            return False
    if only:
        return any(
            sel.upper() == group or cid == sel or cid.startswith(sel + ".") for sel in only
        )
    return True


# --------------------------------------------------------------------------- 主流程
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="T1 验收与契约工具：一条命令验证三工具契约、5 目标、R1、错误路径、离线与 DB 一致性。"
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:8790", help="app 地址（默认 8790）")
    ap.add_argument("--db-url", default="http://127.0.0.1:8797", help="DB 地址（默认 8797）")
    ap.add_argument("--view-url", default="http://127.0.0.1:8830", help="视图工具地址（可选，默认 8830）")
    ap.add_argument("--report-dir", default=None, help="报告目录（默认 report/<timestamp>）")
    ap.add_argument("--offline", action="store_true", help="启动 8810 子进程并设置黑洞代理，验证离线缓存")
    ap.add_argument("--update-golden", action="store_true", help="修复后更新黄金样本（需人工确认）")
    ap.add_argument("--only", default="", help="只跑某些组/用例，如 C 或 C,D 或 C.barnard")
    ap.add_argument("--skip", default="", help="跳过某些组/用例，如 H 或 E.sample")
    ap.add_argument("--no-start", action="store_true", help="只测已运行服务；不启动任何子进程")
    ap.add_argument("--db-down", action="store_true", help="可选：DB 停机降级用例（D4）")
    ap.add_argument("--confirm", action="store_true", help="配合 --update-golden 的人工确认")
    ap.add_argument("--offline-port", type=int, default=8810, help="离线/D4 子进程端口（默认 8810）")
    ap.add_argument("--http-timeout", type=float, default=60.0, help="单次 HTTP 超时秒（默认 60）")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.report_dir:
        report_dir = Path(args.report_dir).expanduser()
    else:
        report_dir = Path("report") / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = report_dir.resolve()
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"无法创建报告目录 {report_dir}: {exc}", file=sys.stderr)
        return 2

    ctx = Context(args, report_dir)
    print(f"T1 验收开始：app={ctx.base_url} db={ctx.db_url} view={ctx.view_url}", flush=True)
    print(f"报告目录：{report_dir}", flush=True)

    try:
        load_golden(ctx)
    except Exception as exc:  # noqa: BLE001
        ctx.env_error = f"黄金样本读取失败：{exc}"
        write_report(ctx, 2, ctx.env_error)
        print(f"[ENV] {ctx.env_error}", file=sys.stderr)
        return 2

    ctx.git = git_info()
    ctx.source_before = snapshot_sources()

    missing = probe_services(ctx)
    if missing:
        ctx.env_error = "；".join(missing)
        write_report(ctx, 2, ctx.env_error)
        print(f"[ENV] {ctx.env_error}", file=sys.stderr)
        print(f"报告：{report_dir / 'report.md'}", flush=True)
        return 2

    ctx.data_snapshot = snapshot_data(ctx)

    for group in GROUP_ORDER:
        if group == "ENV":
            continue
        if not group_enabled(args, group):
            ctx.not_run.append({"case": group, "reason": "被 --only / --skip 过滤"})
            continue
        print(f"\n--- {group}. {GROUP_NAMES[group]} ---", flush=True)
        try:
            if group == "A":
                run_a(ctx)
            elif group == "B":
                run_b(ctx)
            elif group == "C":
                run_c(ctx)
            elif group == "D":
                run_d(ctx)
            elif group == "E":
                run_e(ctx)
            elif group == "F":
                run_f(ctx)
            elif group == "G":
                run_g(ctx)
            elif group == "H":
                run_h(ctx)
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            add_case(
                ctx,
                f"ENV.{group}",
                group,
                "P0",
                "FAIL",
                f"{group} 组执行完成",
                f"组异常：{type(exc).__name__}: {exc}",
            )

    stop_isolated_apps(ctx)

    ctx.source_after = snapshot_sources()
    for rel, before in ctx.source_before.items():
        after = ctx.source_after.get(rel) or {}
        if before.get("sha256") != after.get("sha256"):
            ctx.source_drift.append(rel)
    if ctx.source_drift:
        add_case(
            ctx,
            "ENV.source_drift",
            "ENV",
            "P1",
            "WARN",
            "验收期间三工具源码不变",
            f"运行期间源码变化：{ctx.source_drift}（结果只绑定本次快照）",
        )

    # 用例过滤（支持 --only C.barnard 这类精确用例）
    ctx.cases = [c for c in ctx.cases if case_enabled(args, c["group"], c["id"])]

    maybe_update_golden(ctx)

    summary = summarize(ctx.cases)
    if ctx.env_error:
        exit_code = 2
    elif summary["p0_fail"] > 0:
        exit_code = 1
    else:
        exit_code = 0
    write_report(ctx, exit_code, ctx.env_error)

    print("\n=== T1 汇总 ===")
    print(f"PASS={summary['pass']} FAIL={summary['fail']} WARN={summary['warn']} "
          f"P0_FAIL={summary['p0_fail']} P1_FAIL={summary['p1_fail']}")
    if ctx.env_error:
        print(f"环境错误：{ctx.env_error}")
    print(f"耗时：{ctx.elapsed_s:.1f}s")
    print(f"报告：{report_dir / 'report.json'}")
    print(f"      {report_dir / 'report.md'}")
    print(f"退出码：{exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
