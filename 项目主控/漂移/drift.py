#!/usr/bin/env python3
"""T6 跨工具漂移检查器（只读）。

检查 app / 数据库工具 / 视图工具 三处的当前数据、常量、就绪度口径与文档数字
是否与 ``expect.json`` 基线一致。默认直接只读打开 ``catalog.sqlite``，不启动、
不依赖数据库 HTTP 服务；数据库文件缺失时 DB 类检查标 SKIP。

退出码：
  0  全部通过（允许 WARN）
  1  发现漂移（至少一个 ERROR）
  2  环境缺失（工作区/核心数据文件/报告目录不可用）

本工具只读源码与数据；唯一写入是 ``report.json`` / ``report.md``。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

VERSION = "1.0.0"

DEFAULT_DOCS = [
    "README.md",
    "系统/数据库工具/README.md",
    "系统/数据库工具/benchmarks/README.md",
    "系统/数据库工具/docs/INTEGRATION.md",
    "系统/视图工具/README.md",
    "系统/app/data/header_cache/README.md",
]

DEFAULT_EXPECT: dict[str, Any] = {
    "targets_total": 7,
    "targets_official": 5,
    "epochs_total": 74,
    "fields_total": 5893,
    "exposures_total": 5562,
    "db_weeks_total": 151,
    "local_weeks_total": 23,
    "headers_total": 74,
    "cutouts_total": 101,
    "coverage_radius_deg": 2.47,
    "frame_half_width_deg": 1.745,
    "dedupe_tol_days": 0.02,
    "l3_min_epochs": 6,
    "l3_min_baseline_years": 0.6,
    "coverage_c3_min_days": 180.0,
    "app_port": 8790,
    "db_port": 8797,
    "view_port": 8830,
    "schema_version": 6,
    "api_schema_version": "1.0",
    "app_tests": 52,
    "db_tests": 54,
    "fields_float_tol": 1e-05,
    "epoch_mjd_tol": 1e-06,
    "date_obs_tol_days": 0.001,
    "docs": DEFAULT_DOCS,
}

CORE_DATA = [
    "系统/app/data/targets.json",
    "系统/app/data/epoch_catalog.json",
    "系统/视图工具/data/targets.json",
    "系统/视图工具/data/fields.json",
]

HISTORICAL_MARKERS = (
    "旧",
    "此前",
    "从 ",
    "扩展",
    "增长",
    "审计",
    "基线",
    "快照",
    "历史",
    "回滚",
    "曾",
    "早期",
    "old",
    "previous",
    "grew",
    "from ",
)

FRAME_LINE_SKIP = re.compile(r"最多|至少|上限|批|合成|synthetic|规模|scale|10\^", re.I)
TARGET_LINE_SKIP = re.compile(r"预设目标|官方目标|样本目标|其中|至少|最多")
COUNT_LINE_SKIP = re.compile(r"最多|至少|上限|批")
ROW_LINE_SKIP = re.compile(r"最多|至少|上限|批|合成|synthetic|规模|scale|10\^")

EXPECTED_FIELD_KEYS = [
    "obsid",
    "detector",
    "ra",
    "dec",
    "mjd",
    "lat",
    "lon",
    "elon",
    "elat",
    "xposure",
]

# --------------------------------------------------------------------------- 数据结构


@dataclass
class Finding:
    check: str
    severity: str  # error | warning
    message: str
    file: str | None = None
    line: int | None = None
    evidence: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
        }
        if self.file:
            out["file"] = self.file
        if self.line:
            out["line"] = self.line
        if self.evidence is not None:
            out["evidence"] = self.evidence
        return out


@dataclass
class CheckResult:
    id: str
    title: str
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    md: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str | None = None

    def error(
        self,
        message: str,
        *,
        file: str | None = None,
        line: int | None = None,
        evidence: Any = None,
    ) -> None:
        self.findings.append(Finding(self.id, "error", message, file, line, evidence))

    def warn(
        self,
        message: str,
        *,
        file: str | None = None,
        line: int | None = None,
        evidence: Any = None,
    ) -> None:
        self.findings.append(Finding(self.id, "warning", message, file, line, evidence))

    def note(self, message: str) -> None:
        self.notes.append(message)

    def skip(self, reason: str) -> None:
        self.skipped = True
        self.skip_reason = reason
        self.note(reason)

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def status(self) -> str:
        if self.errors:
            return "FAIL"
        if self.skipped:
            return "SKIP"
        if self.warnings:
            return "WARN"
        return "PASS"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
            "skipped": self.skipped,
            "findings": [f.to_dict() for f in self.findings],
            "notes": self.notes,
            "stats": self.stats,
        }
        if self.skip_reason:
            out["skip_reason"] = self.skip_reason
        return out


# --------------------------------------------------------------------------- 上下文


class DbReader:
    """只读 SQLite 文件读取；打开失败只让 DB 检查 SKIP，不影响常量/文档检查。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.available = False
        self.reason = ""
        self.immutable = False
        self.conn: sqlite3.Connection | None = None
        if not self.path.is_file():
            self.reason = "sqlite 文件不存在"
            return
        try:
            # 静态检查优先用 immutable 快照，避免 mode=ro 仍生成 -shm/-wal；
            # 若存在非空 WAL（DB 正在写入），退回 mode=ro 读取最新内容。
            wal_path = Path(str(self.path) + "-wal")
            self.immutable = not (wal_path.exists() and wal_path.stat().st_size > 0)
            suffix = "?mode=ro&immutable=1" if self.immutable else "?mode=ro"
            uri = self.path.resolve().as_uri() + suffix
            self.conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA query_only = ON")
            self.conn.execute("SELECT 1").fetchone()
            self.available = True
        except Exception as exc:  # noqa: BLE001 - 只读探测失败应降级为 SKIP
            self.reason = f"无法只读打开: {exc}"
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn = None

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if not self.available or self.conn is None:
            raise RuntimeError(self.reason or "DB 不可用")
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        if not self.available or self.conn is None:
            raise RuntimeError(self.reason or "DB 不可用")
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None


class Context:
    def __init__(self, root: Path, expect: dict[str, Any], expect_path: Path, db_path: Path):
        self.root = root
        self.expect = expect
        self.expect_path = expect_path
        self.db_path = db_path
        self.db = DbReader(db_path)
        self._json_cache: dict[str, Any] = {}
        self._text_cache: dict[str, str] = {}
        self.missing_core: list[str] = []

        for rel in CORE_DATA:
            if not self.path(rel).is_file():
                self.missing_core.append(rel)

    def path(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    def rel(self, path: str | Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except Exception:
            return str(path)

    def read_text(self, rel: str | Path) -> str:
        path = self.path(rel)
        key = str(path)
        if key not in self._text_cache:
            self._text_cache[key] = path.read_text(encoding="utf-8")
        return self._text_cache[key]

    def load_json(self, rel: str | Path) -> Any:
        path = self.path(rel)
        key = str(path)
        if key not in self._json_cache:
            with open(path, encoding="utf-8") as fh:
                self._json_cache[key] = json.load(fh)
        return self._json_cache[key]

    def safe_scalar(self, sql: str) -> Any:
        if not self.db.available:
            return None
        try:
            return self.db.scalar(sql)
        except Exception:
            return None

    def safe_rows(self, sql: str) -> list[dict[str, Any]] | None:
        if not self.db.available:
            return None
        try:
            return self.db.rows(sql)
        except Exception:
            return None


# --------------------------------------------------------------------------- 小工具


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return bool(value)


def _as_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_eq(a: Any, b: Any, tol: float = 1e-9) -> bool:
    fa, fb = _as_float(a), _as_float(b)
    if fa is None or fb is None:
        return a == b
    return abs(fa - fb) <= tol


def _parse_py(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise LookupError(f"Python 语法错误: {exc}") from exc


def ast_assign(path: Path, name: str) -> Any:
    tree = _parse_py(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise LookupError(f"未找到赋值 {name}")


def ast_func_default(path: Path, func_name: str, arg_name: str) -> Any:
    tree = _parse_py(path)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        args = node.args
        pos = list(args.args)
        defaults = list(args.defaults)
        offset = len(pos) - len(defaults)
        for index, arg in enumerate(pos):
            if arg.arg != arg_name:
                continue
            if index < offset:
                break
            return ast.literal_eval(defaults[index - offset])
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if arg.arg == arg_name and default is not None:
                return ast.literal_eval(default)
    raise LookupError(f"未找到函数 {func_name} 的参数 {arg_name} 默认值")


def ast_argparse_default(path: Path, option: str) -> Any:
    tree = _parse_py(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or first.value != option:
            continue
        for kw in node.keywords:
            if kw.arg == "default":
                return ast.literal_eval(kw.value)
    raise LookupError(f"未找到 add_argument({option!r}) 的 default")


def func_source(path: Path, func_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = _parse_py(path)
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise LookupError(f"未找到函数 {func_name}")


def literal_number_in_func(path: Path, func_name: str, value: float) -> float:
    src = func_source(path, func_name)
    token = f"{value:g}"
    if not re.search(r"(?<![\d.])" + re.escape(token) + r"(?![\d])", src):
        raise LookupError(f"函数 {func_name} 中未找到字面量 {token}")
    return float(value)


def _js_declaration_rhs(path: Path, name: str) -> str:
    """取 ``const/let/var NAME =`` 或 ``window.NAME =`` 到第一个分号之间的表达式。

    允许跨行；MOVESCOPE_ORIGIN 这类 ``String(window.X || 'http://...')`` 也能取到默认 URL。
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"(?:const|let|var)\s+" + re.escape(name) + r"\s*=",
        text,
    )
    if not match:
        match = re.search(r"window\." + re.escape(name) + r"\s*=", text)
    if not match:
        raise LookupError(f"未找到 JS 声明 {name}")
    tail = text[match.end() :]
    end = tail.find(";")
    block = tail if end < 0 else tail[:end]
    return block.strip()


def _safe_js_arith(expr: str) -> float:
    """只允许数字、括号和 + - * / 的 JS 简单算式（如 ``3.49 / 2``）。"""
    expr = expr.strip()
    if not re.fullmatch(r"[0-9.+\-*/() \t\n\r]+", expr):
        raise LookupError(f"JS 数值表达式不受支持: {expr!r}")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise LookupError(f"JS 数值表达式无法解析: {expr!r}") from exc
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.USub,
        ast.UAdd,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise LookupError(f"JS 数值表达式含不允许的节点: {type(node).__name__}")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return value
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise LookupError(f"JS 数值表达式节点不支持: {type(node).__name__}")

    return evaluate(tree)


def js_number(path: Path, name: str) -> float:
    block = _js_declaration_rhs(path, name)
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    block = re.sub(r"//[^\n]*", "", block).strip()
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", block):
        return float(block)
    return _safe_js_arith(block)


def js_port(path: Path, name: str) -> int:
    block = _js_declaration_rhs(path, name)
    match = re.search(r"(?:https?://)?127\.0\.0\.1:(\d+)", block)
    if not match:
        raise LookupError(f"未找到 JS 地址常量 {name} 中的 127.0.0.1 端口")
    return int(match.group(1))


def sh_default_port(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\$\{1:-(\d+)\}", text)
    if not match:
        raise LookupError("未找到 PORT=${1:-...} 默认端口")
    return int(match.group(1))


def sh_var_port(path: Path, var_name: str, env_name: str) -> int:
    text = path.read_text(encoding="utf-8")
    pattern = re.escape(var_name) + r'="\$\{' + re.escape(env_name) + r":-(\d+)\}"
    match = re.search(pattern, text)
    if not match:
        raise LookupError(f"未找到 {var_name}=${{{env_name}:-...}}")
    return int(match.group(1))


def label_contains(path: Path, needle: str) -> bool:
    if needle not in path.read_text(encoding="utf-8"):
        raise LookupError(f"未找到文案 {needle!r}")
    return True


def l3_pair(path: Path) -> tuple[int, float]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"n\s*>=\s*(\d+)\s+and\s+baseline_years\s*>=\s*([0-9.]+)\s*:",
        text,
    )
    if not match:
        raise LookupError("未找到 L3 阈值 (n >= N and baseline_years >= Y)")
    return int(match.group(1)), float(match.group(2))


def count_test_defs(directory: Path) -> int | None:
    if not directory.is_dir():
        return None
    count = 0
    for path in sorted(directory.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        count += len(re.findall(r"^\s*def\s+test_\w+\s*\(", text, re.M))
    return count


def week_from_obsid(obsid: Any) -> str | None:
    if not obsid:
        return None
    parts = str(obsid).split("_")
    token = "_".join(parts[:2]).strip() if len(parts) >= 2 else parts[0].strip()
    return token or None


def mjd_to_iso(mjd: float) -> str:
    dt = datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=float(mjd))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def iso_to_mjd(value: str) -> float | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - datetime(1858, 11, 17, tzinfo=timezone.utc)
    return delta.total_seconds() / 86400.0


def dedupe_mjd_rows(rows: list[dict[str, Any]], tol_days: float = 0.02) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda r: (_as_float(r.get("mjd")) or 0.0, _as_float(r.get("sep_deg")) or 9.0),
    )
    out: list[dict[str, Any]] = []
    for row in ordered:
        mjd = _as_float(row.get("mjd"))
        if mjd is None:
            continue
        if out:
            last = _as_float(out[-1].get("mjd"))
            if last is not None and abs(mjd - last) <= tol_days:
                if (_as_float(row.get("sep_deg")) or 9.0) < (_as_float(out[-1].get("sep_deg")) or 9.0):
                    out[-1] = row
                continue
        out.append(row)
    return out


def coverage_level(n_epochs: int, baseline_days: float) -> str:
    if n_epochs <= 1:
        return "C0"
    if n_epochs == 2:
        return "C1"
    if n_epochs < 6:
        return "C2"
    return "C3" if baseline_days >= 180.0 else "C2"


def readiness_level(n_epochs: int, baseline_days: float, l3_days: float) -> str:
    if n_epochs <= 1:
        return "L0"
    if n_epochs == 2:
        return "L1"
    if n_epochs < 6:
        return "L2"
    return "L3" if baseline_days >= l3_days else "L2"


def md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_field(raw: Any, label: str, r: CheckResult, rel: str) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (ValueError, TypeError) as exc:
        r.error(f"{label} JSON 无法解析: {exc}", file=rel)
        return None


def _load_json_or_report(
    ctx: Context,
    r: CheckResult,
    rel: str,
    label: str,
    *,
    required: bool = True,
) -> Any:
    try:
        return ctx.load_json(rel)
    except FileNotFoundError:
        if required:
            r.error(f"{label} 缺失", file=rel)
        else:
            r.warn(f"{label} 缺失，跳过对应比较", file=rel)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        r.error(f"{label} 无法读取/解析: {exc}", file=rel)
        return None


# --------------------------------------------------------------------------- 1. targets


def check_targets(ctx: Context) -> CheckResult:
    r = CheckResult("targets", "目标字段三处一致（app / DB / view，含 coord_unc_arcsec）")
    expect = ctx.expect
    app_doc = _load_json_or_report(ctx, r, "系统/app/data/targets.json", "app targets.json")
    view_doc = _load_json_or_report(ctx, r, "系统/视图工具/data/targets.json", "view targets.json")
    if app_doc is None or view_doc is None:
        return r

    app_list = app_doc.get("targets") if isinstance(app_doc, dict) else None
    view_list = view_doc.get("targets") if isinstance(view_doc, dict) else None
    if not isinstance(app_list, list):
        r.error("app targets.json 缺少 targets 数组", file="系统/app/data/targets.json")
        app_list = []
    if not isinstance(view_list, list):
        r.error("view targets.json 缺少 targets 数组", file="系统/视图工具/data/targets.json")
        view_list = []

    def index(items: list[Any], source: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                r.error(f"{source}: 目标条目不是对象")
                continue
            tid = item.get("id")
            if not tid:
                r.error(f"{source}: 目标缺少 id")
                continue
            if tid in out:
                r.error(f"{source}: 目标 id 重复: {tid}")
                continue
            out[str(tid)] = item
        return out

    app = index(app_list, "app")
    view = index(view_list, "view")

    for label, items in (("app", app), ("view", view)):
        if len(items) != int(expect["targets_total"]):
            r.error(f"{label} 目标数为 {len(items)}，期望 {expect['targets_total']}")
        official = sum(1 for t in items.values() if _as_bool(t.get("official")))
        if official != int(expect["targets_official"]):
            r.error(f"{label} official 目标数为 {official}，期望 {expect['targets_official']}")

    for tid in sorted(set(app) | set(view)):
        if tid not in app:
            r.error(f"目标 {tid} 只存在于 view，app 缺失")
            continue
        if tid not in view:
            r.error(f"目标 {tid} 只存在于 app，view 缺失")
            continue
        a, v = app[tid], view[tid]
        for fld in ("name", "name_zh", "type", "expected", "note"):
            if a.get(fld) != v.get(fld):
                r.error(
                    f"目标 {tid}: {fld} 不一致（app={a.get(fld)!r}，view={v.get(fld)!r}）",
                    file="系统/视图工具/data/targets.json",
                )
        if not _float_eq(a.get("ra"), v.get("ra"), 1e-9):
            r.error(f"目标 {tid}: ra 不一致（app={a.get('ra')!r}，view={v.get('ra')!r}）")
        if not _float_eq(a.get("dec"), v.get("dec"), 1e-9):
            r.error(f"目标 {tid}: dec 不一致（app={a.get('dec')!r}，view={v.get('dec')!r}）")
        if _as_bool(a.get("official")) != _as_bool(v.get("official")):
            r.error(f"目标 {tid}: official 不一致")
        if not isinstance(a.get("truth"), dict):
            r.error(f"目标 {tid}: app 缺少 truth 对象")
        elif a.get("truth") != v.get("truth"):
            r.error(f"目标 {tid}: truth 不一致（app/view）")
        for source, item in (("app", a), ("view", v)):
            if "coord_unc_arcsec" not in item or item.get("coord_unc_arcsec") is None:
                r.error(f"目标 {tid}: {source} 缺少必填 coord_unc_arcsec")
            elif _as_float(item.get("coord_unc_arcsec")) is not None and float(item["coord_unc_arcsec"]) <= 0:
                r.warn(f"目标 {tid}: {source} coord_unc_arcsec <= 0，疑似用 0 代替缺失")

    source_sha = view_doc.get("source_sha256") if isinstance(view_doc, dict) else None
    if source_sha:
        actual_sha = sha256_file(ctx.path("系统/app/data/targets.json"))
        if actual_sha != source_sha:
            r.error(
                "view targets.json 的 source_sha256 与 app targets.json 当前哈希不一致",
                file="系统/视图工具/data/targets.json",
                evidence={"expected": source_sha, "actual": actual_sha},
            )
        else:
            r.note("view targets.json source_sha256 与 app 当前哈希一致")
    else:
        r.warn("view targets.json 缺少 source_sha256，无法确认生成来源")

    r.stats["counts"] = {"app": len(app), "view": len(view)}
    r.md.append("| 来源 | 目标数 | official |")
    r.md.append("|---|---:|---:|")
    r.md.append(
        f"| app `系统/app/data/targets.json` | {len(app)} | "
        f"{sum(1 for t in app.values() if _as_bool(t.get('official')))} |"
    )
    r.md.append(
        f"| view `系统/视图工具/data/targets.json` | {len(view)} | "
        f"{sum(1 for t in view.values() if _as_bool(t.get('official')))} |"
    )

    if not ctx.db.available:
        r.skip(f"DB 不可用（{ctx.db.reason}），仅完成 app/view 两处比较")
    else:
        db_rows = ctx.safe_rows("SELECT * FROM targets")
        if db_rows is None:
            r.warn("DB targets 表不可读，跳过 DB 比较")
        else:
            db: dict[str, dict[str, Any]] = {}
            for row in db_rows:
                tid = row.get("id")
                if tid in db:
                    r.error(f"DB targets id 重复: {tid}")
                    continue
                db[str(tid)] = row
            if len(db) != int(expect["targets_total"]):
                r.error(f"DB targets 数为 {len(db)}，期望 {expect['targets_total']}")
            db_official = sum(1 for t in db.values() if _as_bool(t.get("official")))
            if db_official != int(expect["targets_official"]):
                r.error(f"DB official 目标数为 {db_official}，期望 {expect['targets_official']}")
            for tid in sorted(set(app) | set(db)):
                if tid not in app:
                    r.error(f"目标 {tid} 只存在于 DB，app 缺失")
                    continue
                if tid not in db:
                    r.error(f"目标 {tid} 只存在于 app，DB 缺失")
                    continue
                a, d = app[tid], db[tid]
                for fld in ("name", "name_zh", "type", "expected", "note"):
                    if a.get(fld) != d.get(fld):
                        r.error(
                            f"目标 {tid}: {fld} 不一致（app={a.get(fld)!r}，DB={d.get(fld)!r}）",
                            file="系统/数据库工具/var/catalog.sqlite",
                        )
                if not _float_eq(a.get("ra"), d.get("ra"), 1e-9):
                    r.error(f"目标 {tid}: ra 不一致（app={a.get('ra')!r}，DB={d.get('ra')!r}）")
                if not _float_eq(a.get("dec"), d.get("dec"), 1e-9):
                    r.error(f"目标 {tid}: dec 不一致（app={a.get('dec')!r}，DB={d.get('dec')!r}）")
                if _as_bool(a.get("official")) != _as_bool(d.get("official")):
                    r.error(f"目标 {tid}: official 不一致（app/DB）")
                db_truth = _json_field(
                    d.get("truth_json"), f"目标 {tid} DB truth_json", r, "系统/数据库工具/var/catalog.sqlite"
                )
                if db_truth is None:
                    r.error(f"目标 {tid}: DB 缺少 truth_json")
                elif a.get("truth") != db_truth:
                    r.error(f"目标 {tid}: truth 不一致（app/DB）")
                db_extra = _json_field(
                    d.get("extra_json"), f"目标 {tid} DB extra_json", r, "系统/数据库工具/var/catalog.sqlite"
                )
                if not isinstance(db_extra, dict) or "coord_unc_arcsec" not in db_extra:
                    r.error(f"目标 {tid}: DB extra_json 缺少 coord_unc_arcsec")
                else:
                    if not _float_eq(a.get("coord_unc_arcsec"), db_extra.get("coord_unc_arcsec"), 1e-9):
                        r.error(
                            f"目标 {tid}: coord_unc_arcsec 不一致（app={a.get('coord_unc_arcsec')!r}，"
                            f"DB={db_extra.get('coord_unc_arcsec')!r}）"
                        )
            r.md.append(f"| DB `targets` | {len(db)} | {db_official} |")
            r.note(f"DB targets 表：{len(db)} 个目标，coord_unc_arcsec 在 extra_json 中逐项对账")

    r.md.append("")
    r.md.append(
        "- 逐字段：`id/name/name_zh/official/ra/dec/type/expected/coord_unc_arcsec/truth/note`。"
    )
    return r


# --------------------------------------------------------------------------- 2. epochs


def check_epochs(ctx: Context) -> CheckResult:
    r = CheckResult("epochs", "历元 74 条（app JSON / DB target_epochs / 导出）")
    expect = ctx.expect
    app_doc = _load_json_or_report(ctx, r, "系统/app/data/epoch_catalog.json", "app epoch_catalog.json")
    export_doc = _load_json_or_report(
        ctx,
        r,
        "系统/数据库工具/var/exports/epoch_catalog.json",
        "DB export epoch_catalog.json",
        required=False,
    )
    if app_doc is None:
        return r

    def index_json(doc: Any, source: str) -> tuple[dict[tuple[str, str, int], dict[str, Any]], dict[str, int]]:
        out: dict[tuple[str, str, int], dict[str, Any]] = {}
        counts: dict[str, int] = {}
        raw = doc.get("epochs") if isinstance(doc, dict) else None
        if not isinstance(raw, dict):
            r.error(f"{source}: 缺少 epochs 对象")
            return out, counts
        for tid, items in raw.items():
            if not isinstance(items, list):
                r.error(f"{source}: 目标 {tid} 的历元不是数组")
                continue
            counts[str(tid)] = len(items)
            for item in items:
                if not isinstance(item, dict):
                    r.error(f"{source}: 目标 {tid} 存在非对象历元")
                    continue
                detector = item.get("detector")
                try:
                    detector_int = int(detector)
                except (TypeError, ValueError):
                    r.error(f"{source}: 目标 {tid} 历元 detector 非整数: {detector!r}")
                    continue
                obsid = item.get("obsid")
                if not obsid:
                    r.error(f"{source}: 目标 {tid} 历元缺少 obsid")
                    continue
                mjd = _as_float(item.get("mjd"))
                if mjd is None:
                    r.error(f"{source}: 历元 {obsid}/D{detector_int} 缺少数值 mjd")
                    continue
                key = (str(tid), str(obsid), detector_int)
                if key in out:
                    r.error(f"{source}: 历元 (obsid,detector) 重复: {obsid}/D{detector_int}")
                    continue
                out[key] = {
                    "target_id": str(tid),
                    "obsid": str(obsid),
                    "detector": detector_int,
                    "release": item.get("release"),
                    "week": item.get("week"),
                    "version": item.get("version"),
                    "mjd": mjd,
                    "date_obs": item.get("date_obs"),
                    "sep_deg": _as_float(item.get("sep_deg")),
                    "key": item.get("key"),
                }
        return out, counts

    def index_db(rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, int], dict[str, Any]], dict[str, int]]:
        out: dict[tuple[str, str, int], dict[str, Any]] = {}
        counts: dict[str, int] = {}
        for row in rows:
            tid = str(row.get("target_id"))
            obsid = row.get("obs_id")
            detector = row.get("detector")
            try:
                detector_int = int(detector)
            except (TypeError, ValueError):
                r.error(f"DB: target_epochs detector 非整数: {detector!r}")
                continue
            if not obsid:
                r.error(f"DB: target_epochs 缺少 obs_id（id={row.get('id')}）")
                continue
            key = (tid, str(obsid), detector_int)
            if key in out:
                r.error(f"DB: target_epochs (obsid,detector) 重复: {obsid}/D{detector_int}")
                continue
            out[key] = {
                "target_id": tid,
                "obsid": str(obsid),
                "detector": detector_int,
                "release": row.get("release"),
                "week": row.get("week"),
                "version": None,
                "mjd": _as_float(row.get("mjd")),
                "date_obs": row.get("date_obs"),
                "sep_deg": _as_float(row.get("sep_deg")),
                "key": row.get("fits_key"),
                "frame_id": row.get("frame_id"),
            }
            counts[tid] = counts.get(tid, 0) + 1
        return out, counts

    app, app_counts = index_json(app_doc, "app")
    export = None
    export_counts: dict[str, int] = {}
    if export_doc is not None:
        export, export_counts = index_json(export_doc, "export")

    def compare_maps(
        left: dict[tuple[str, str, int], dict[str, Any]],
        right: dict[tuple[str, str, int], dict[str, Any]],
        left_label: str,
        right_label: str,
        fields: list[str],
        float_fields: set[str],
        tol: float,
    ) -> int:
        diffs = 0
        only_left = sorted(set(left) - set(right))
        only_right = sorted(set(right) - set(left))
        for key in only_left[:10]:
            r.error(f"{left_label} 有、{right_label} 缺失: {key}")
            diffs += 1
        if len(only_left) > 10:
            r.error(f"{left_label} 有、{right_label} 缺失共 {len(only_left)} 条")
        for key in only_right[:10]:
            r.error(f"{right_label} 有、{left_label} 缺失: {key}")
            diffs += 1
        if len(only_right) > 10:
            r.error(f"{right_label} 有、{left_label} 缺失共 {len(only_right)} 条")
        field_diffs = 0
        for key in sorted(set(left) & set(right)):
            lrow, rrow = left[key], right[key]
            for fld in fields:
                lv, rv = lrow.get(fld), rrow.get(fld)
                if fld in float_fields:
                    same = _float_eq(lv, rv, tol)
                else:
                    same = lv == rv
                if not same:
                    field_diffs += 1
                    diffs += 1
                    if field_diffs <= 10:
                        r.error(
                            f"历元 {key[1]}/D{key[2]}（{key[0]}）字段 {fld} 不一致："
                            f"{left_label}={lv!r}，{right_label}={rv!r}"
                        )
        if field_diffs > 10:
            r.error(f"{left_label} vs {right_label} 字段差异共 {field_diffs} 处（只列前 10）")
        return diffs

    json_fields = ["release", "week", "version", "mjd", "date_obs", "sep_deg", "key", "obsid", "detector"]
    if export is not None:
        compare_maps(app, export, "app", "export", json_fields, {"mjd", "sep_deg"}, 1e-9)
    db = None
    db_counts: dict[str, int] = {}
    if ctx.db.available:
        db_rows = ctx.safe_rows(
            "SELECT id, target_id, frame_id, release, week, obs_id, detector, mjd, date_obs, "
            "sep_deg, fits_key FROM target_epochs ORDER BY target_id, mjd, detector"
        )
        if db_rows is None:
            r.warn("DB target_epochs 表不可读，跳过 DB 比较")
        else:
            db, db_counts = index_db(db_rows)
            compare_maps(
                app,
                db,
                "app",
                "DB",
                ["release", "week", "mjd", "date_obs", "sep_deg", "key", "obsid", "detector"],
                {"mjd", "sep_deg"},
                1e-9,
            )
    else:
        r.skip(f"DB 不可用（{ctx.db.reason}），app/export 比较仍执行")

    if len(app) != int(expect["epochs_total"]):
        r.error(f"app 历元总数为 {len(app)}，期望 {expect['epochs_total']}")
    if export is not None and len(export) != int(expect["epochs_total"]):
        r.error(f"export 历元总数为 {len(export)}，期望 {expect['epochs_total']}")
    if db is not None and len(db) != int(expect["epochs_total"]):
        r.error(f"DB target_epochs 总数为 {len(db)}，期望 {expect['epochs_total']}")

    all_targets = sorted(set(app_counts) | set(export_counts) | set(db_counts))
    for tid in all_targets:
        counts = {
            "app": app_counts.get(tid, 0),
            "export": export_counts.get(tid, 0),
            "db": db_counts.get(tid, 0),
        }
        present = [v for k, v in counts.items() if (k != "export" or export is not None) and (k != "db" or db is not None)]
        if present and len(set(present)) > 1:
            r.error(
                f"目标 {tid} 历元数三处不一致: app={counts['app']}，"
                f"export={counts['export']}，DB={counts['db']}"
            )

    date_warnings = 0
    for key, epoch in sorted(app.items()):
        date_obs = epoch.get("date_obs")
        if not date_obs:
            r.error(f"历元 {key[1]}/D{key[2]} 的 date_obs 为空（契约禁止 null）")
            continue
        mjd = _as_float(epoch.get("mjd"))
        parsed_mjd = iso_to_mjd(str(date_obs))
        if mjd is not None and parsed_mjd is not None:
            diff = abs(mjd - parsed_mjd)
            if diff > float(expect["date_obs_tol_days"]) and date_warnings < 5:
                date_warnings += 1
                r.warn(
                    f"历元 {key[1]}/D{key[2]} date_obs 与 mjd 不一致：差 {diff:.6f} 天",
                    evidence={"date_obs": date_obs, "mjd": mjd, "mjd_iso": mjd_to_iso(mjd)},
                )
        if not epoch.get("key"):
            r.error(f"历元 {key[1]}/D{key[2]} 缺少 key/fits_key")
        if not epoch.get("release") or not epoch.get("week"):
            r.error(f"历元 {key[1]}/D{key[2]} 缺少 release/week")

    app_epoch_buckets = app_doc.get("epochs") if isinstance(app_doc, dict) else None
    if not isinstance(app_epoch_buckets, dict):
        app_epoch_buckets = {}
    for tid, items in app_epoch_buckets.items():
        if not isinstance(items, list):
            continue
        order = []
        for item in items:
            if isinstance(item, dict):
                mjd = _as_float(item.get("mjd"))
                detector = item.get("detector")
                try:
                    det = int(detector)
                except (TypeError, ValueError):
                    det = 0
                if mjd is not None:
                    order.append((mjd, det))
        if order and order != sorted(order):
            r.warn(f"目标 {tid} 的 app 历元未按 (mjd, detector) 升序排列")

    header_note = None
    try:
        manifest = ctx.load_json("系统/app/data/header_manifest.json")
        entries = manifest.get("entries") if isinstance(manifest, dict) else None
        if isinstance(entries, list):
            header_note = len(entries)
    except (FileNotFoundError, json.JSONDecodeError):
        header_note = None
    if header_note is not None and header_note != len(app):
        r.warn(f"app header_manifest.json 条目数 {header_note} 与历元数 {len(app)} 不一致")

    frame_links = None
    if ctx.db.available:
        frame_links = ctx.safe_scalar(
            "SELECT SUM(CASE WHEN frame_id IS NULL THEN 1 ELSE 0 END) FROM target_epochs"
        )
        if frame_links is None:
            frame_links = 0
        if int(frame_links) > 0:
            r.warn(f"DB target_epochs 中有 {int(frame_links)} 条未链接 frame_id")

    if export_doc is not None:
        r.note("export epoch_catalog.json 与 app 逐条对账（含 version）")
    else:
        r.note("export epoch_catalog.json 缺失，仅 app/DB 两处对账")
    if db is not None:
        r.note("DB target_epochs 无 version 列；按 release/week/mjd/date_obs/sep_deg/fits_key 比较")

    r.stats["counts"] = {
        "app": len(app),
        "export": len(export) if export is not None else None,
        "db": len(db) if db is not None else None,
        "per_target_app": app_counts,
        "per_target_export": export_counts,
        "per_target_db": db_counts,
        "frame_links_null": frame_links,
    }
    r.md.append("| 目标 | app | export | DB target_epochs |")
    r.md.append("|---|---:|---:|---:|")
    for tid in all_targets:
        export_cell = str(export_counts.get(tid, 0)) if export is not None else "—"
        db_cell = str(db_counts.get(tid, 0)) if db is not None else "—"
        r.md.append(f"| {tid} | {app_counts.get(tid, 0)} | {export_cell} | {db_cell} |")
    r.md.append("")
    r.md.append(
        f"- 总数：app **{len(app)}** / export **{len(export) if export is not None else '—'}** / "
        f"DB **{len(db) if db is not None else '—'}**，期望 **{expect['epochs_total']}**。"
    )
    if frame_links is not None:
        r.md.append(f"- DB `target_epochs.frame_id` 空值：{int(frame_links)} 条。")
    return r


# --------------------------------------------------------------------------- 3. fields


def check_fields(ctx: Context) -> CheckResult:
    r = CheckResult("fields", "星场 5,893 行（view fields.json / DB frames / fields_snapshot / export）")
    expect = ctx.expect
    view_doc = _load_json_or_report(ctx, r, "系统/视图工具/data/fields.json", "view fields.json")
    if view_doc is None:
        return r
    rows = view_doc.get("fields") if isinstance(view_doc, dict) else None
    if not isinstance(rows, list):
        r.error("view fields.json 缺少 fields 数组", file="系统/视图工具/data/fields.json")
        return r
    valid_rows: list[list[Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(EXPECTED_FIELD_KEYS):
            r.error(f"view fields.json 第 {index} 行不是 10 列数组")
            continue
        valid_rows.append(row)
    rows = valid_rows

    if len(rows) != int(expect["fields_total"]):
        r.error(f"view fields.json 行数为 {len(rows)}，期望 {expect['fields_total']}")
    keys = view_doc.get("keys") if isinstance(view_doc, dict) else None
    if keys != EXPECTED_FIELD_KEYS:
        r.error(f"view fields.json keys 与契约不一致：{keys!r}")

    def view_index() -> tuple[dict[tuple[str, int], list[Any]], list[tuple[str, int]], list[str]]:
        out: dict[tuple[str, int], list[Any]] = {}
        order: list[tuple[str, int]] = []
        duplicates: list[str] = []
        for row in rows:
            obsid, detector = str(row[0]), row[1]
            try:
                det = int(detector)
            except (TypeError, ValueError):
                r.error(f"view fields.json detector 非整数: {detector!r}")
                continue
            key = (obsid, det)
            if key in out:
                duplicates.append(f"{obsid}/D{det}")
                continue
            out[key] = row
            order.append(key)
        return out, order, duplicates

    v_index, v_order, v_dups = view_index()
    for dup in v_dups[:10]:
        r.error(f"view fields.json (obsid,detector) 重复: {dup}")

    local_weeks = {week_from_obsid(row[0]) for row in rows if week_from_obsid(row[0])}
    if len(local_weeks) != int(expect["local_weeks_total"]):
        r.error(
            f"view fields.json 覆盖 {len(local_weeks)} 个观测周，期望 {expect['local_weeks_total']}"
        )

    float_tol = float(expect["fields_float_tol"])
    db_frames = None
    db_index: dict[tuple[str, int], list[Any]] | None = None
    db_order: list[tuple[str, int]] = []
    tolerated = 0
    field_diffs = 0

    if not ctx.db.available:
        r.skip(f"DB 不可用（{ctx.db.reason}），仅完成 view/export 比较")
    else:
        db_frames = ctx.safe_rows(
            "SELECT obs_id, detector, ra, dec, mjd, sgt_lat, sgt_lon, elon, elat, xposure "
            "FROM frames ORDER BY frame_id"
        )
        snapshot = ctx.safe_rows(
            "SELECT obsid, detector, ra, dec, mjd, lat, lon, elon, elat, xposure "
            "FROM fields_snapshot ORDER BY seq"
        )
        if db_frames is None:
            r.warn("DB frames 表不可读，跳过 DB 比较")
        else:
            if len(db_frames) != int(expect["fields_total"]):
                r.error(f"DB frames 行数为 {len(db_frames)}，期望 {expect['fields_total']}")
            if snapshot is None:
                r.warn("DB fields_snapshot 表不可读，跳过冻结快照比较")
            else:
                if len(snapshot) != len(db_frames):
                    r.error(f"DB fields_snapshot {len(snapshot)} 行 != frames {len(db_frames)} 行")
                snapshot_rows = [
                    [
                        row["obsid"],
                        row["detector"],
                        row["ra"],
                        row["dec"],
                        row["mjd"],
                        row["lat"],
                        row["lon"],
                        row["elon"],
                        row["elat"],
                        row["xposure"],
                    ]
                    for row in snapshot
                ]
                if snapshot_rows != rows:
                    for i, (a, b) in enumerate(zip(snapshot_rows, rows)):
                        if a != b:
                            r.error(
                                f"DB fields_snapshot 与 view fields.json 第 {i} 行不一致",
                                evidence={"snapshot": a, "view": b},
                            )
                            break
                else:
                    r.note("DB fields_snapshot 与 view fields.json 逐行一致（含顺序）")

            db_index = {}
            for row in db_frames:
                try:
                    det = int(row["detector"])
                except (TypeError, ValueError):
                    r.error(f"DB frames detector 非整数: {row['detector']!r}")
                    continue
                key = (str(row["obs_id"]), det)
                if key in db_index:
                    r.error(f"DB frames (obs_id,detector) 重复: {key[0]}/D{key[1]}")
                    continue
                db_index[key] = [
                    row["obs_id"],
                    row["detector"],
                    row["ra"],
                    row["dec"],
                    row["mjd"],
                    row["sgt_lat"],
                    row["sgt_lon"],
                    row["elon"],
                    row["elat"],
                    row["xposure"],
                ]
                db_order.append(key)

            for key in sorted(set(v_index) - set(db_index))[:10]:
                r.error(f"view fields.json 有、DB frames 缺失: {key[0]}/D{key[1]}")
            for key in sorted(set(db_index) - set(v_index))[:10]:
                r.error(f"DB frames 有、view fields.json 缺失: {key[0]}/D{key[1]}")

            for i, key in enumerate(v_order):
                if key not in db_index:
                    continue
                a, b = v_index[key], db_index[key]
                for col in range(10):
                    av, bv = a[col], b[col]
                    if col in (0, 1):
                        same = str(av) == str(bv)
                    else:
                        fa, fb = _as_float(av), _as_float(bv)
                        if fa is None or fb is None:
                            same = av == bv
                        else:
                            diff = abs(fa - fb)
                            same = diff <= float_tol
                            if same and diff > 0:
                                tolerated += 1
                    if not same:
                        field_diffs += 1
                        if field_diffs <= 10:
                            r.error(
                                f"view fields.json 与 DB frames 字段不一致 "
                                f"（第 {i} 行，列 {EXPECTED_FIELD_KEYS[col]}）：view={av!r}，DB={bv!r}"
                            )
            if field_diffs > 10:
                r.error(f"view/DB 字段差异共 {field_diffs} 处（只列前 10）")

            if v_order and db_order and set(v_order) == set(db_order) and v_order != db_order:
                r.warn("view fields.json 与 DB frames 行顺序不一致（按 frame_id 排序）")

            exposures = ctx.safe_scalar("SELECT COUNT(*) FROM observations")
            view_exposures = view_doc.get("exposures") if isinstance(view_doc, dict) else None
            if view_exposures is not None and exposures is not None and int(view_exposures) != int(exposures):
                r.error(
                    f"view fields.json exposures={view_exposures}，DB observations={exposures}"
                )
            if int(expect["exposures_total"]) != int(exposures or 0):
                r.error(f"DB observations 数为 {exposures}，期望 {expect['exposures_total']}")
    view_exposures = view_doc.get("exposures") if isinstance(view_doc, dict) else None
    if view_exposures is not None and int(view_exposures) != int(expect["exposures_total"]):
        r.error(
            f"view fields.json exposures={view_exposures}，期望 {expect['exposures_total']}"
        )

    export_doc = _load_json_or_report(
        ctx,
        r,
        "系统/数据库工具/var/exports/fields.json",
        "DB export fields.json",
        required=False,
    )
    if export_doc is not None:
        export_rows = export_doc.get("fields") if isinstance(export_doc, dict) else None
        if not isinstance(export_rows, list):
            r.error("export fields.json 缺少 fields 数组")
        else:
            if len(export_rows) != int(expect["fields_total"]):
                r.error(f"export fields.json 行数为 {len(export_rows)}，期望 {expect['fields_total']}")
            if export_rows != rows:
                for i, (a, b) in enumerate(zip(export_rows, rows)):
                    if a != b:
                        r.error(f"export fields.json 与 view fields.json 第 {i} 行不一致")
                        break
            else:
                r.note("export fields.json 与 view fields.json 逐行一致")

    count_meta = view_doc.get("count") if isinstance(view_doc, dict) else None
    if count_meta is not None and int(count_meta) != len(rows):
        r.error(f"view fields.json count={count_meta}，实际行数 {len(rows)}")

    r.stats["counts"] = {
        "view": len(rows),
        "db_frames": len(db_frames) if db_frames is not None else None,
        "export": len(export_rows) if export_doc is not None and isinstance(export_rows, list) else None,
        "local_weeks": len(local_weeks),
        "tolerated_rounding_cells": tolerated,
        "field_diffs": field_diffs,
    }
    r.md.append("| 来源 | 行数 | 说明 |")
    r.md.append("|---|---:|---|")
    r.md.append(f"| view `fields.json` | {len(rows)} | 10 列紧凑数组 |")
    if db_frames is not None:
        r.md.append(f"| DB `frames` | {len(db_frames)} | 按 frame_id 顺序 |")
    if export_doc is not None and isinstance(export_rows, list):
        r.md.append(f"| DB export `fields.json` | {len(export_rows)} | 冻结导出 |")
    r.md.append("")
    r.md.append(f"- 本地观测周：**{len(local_weeks)}**（由 obsid 前缀去重）。")
    if db_frames is not None:
        r.md.append(
            f"- 浮点比较容差 {float_tol:g}；仅因 view 保留 5 位小数产生的差异单元格：{tolerated}。"
        )
    return r


# --------------------------------------------------------------------------- 4. constants


@dataclass
class Probe:
    label: str
    rel: str
    getter: Callable[[Path], Any]


def _probe_group(
    ctx: Context,
    r: CheckResult,
    label: str,
    expected: Any,
    kind: str,
    probes: list[Probe],
) -> None:
    values: list[dict[str, Any]] = []
    for probe in probes:
        path = ctx.path(probe.rel)
        if not path.is_file():
            r.warn(f"{label}: 源文件缺失，跳过 {probe.label}", file=probe.rel)
            continue
        try:
            actual = probe.getter(path)
        except LookupError as exc:
            r.error(f"{label}: {probe.label} 无法定位（{exc}）", file=probe.rel)
            continue
        except Exception as exc:  # noqa: BLE001 - 解析失败应记录为漂移证据
            r.error(f"{label}: {probe.label} 解析失败（{exc}）", file=probe.rel)
            continue
        values.append({"label": probe.label, "file": probe.rel, "actual": actual})

    if not values:
        r.note(f"{label}: 无可用探针（源文件均缺失）")
        return

    def same(actual: Any) -> bool:
        try:
            if kind == "float":
                return _float_eq(actual, expected, 1e-9)
            if kind == "int":
                return int(actual) == int(expected)
            if kind == "str":
                return str(actual) == str(expected)
            if kind == "tuple":
                return tuple(actual) == tuple(expected)
            if kind == "bool":
                return bool(actual) == bool(expected)
        except (TypeError, ValueError):
            return False
        return actual == expected

    bad = [item for item in values if not same(item["actual"])]
    if bad:
        detail = "；".join(f"{item['label']}={item['actual']!r}" for item in bad)
        r.error(f"{label} 与基线不一致（期望 {expected!r}）：{detail}")
    else:
        r.note(f"{label} = {expected!r}（{len(values)} 处一致）")

    display = "；".join(f"{item['label']}={item['actual']!r}" for item in values)
    r.stats.setdefault("constants", []).append(
        {
            "label": label,
            "expected": expected,
            "status": "FAIL" if bad else "PASS",
            "values": values,
        }
    )
    r.md.append(
        f"| {md_escape(label)} | {md_escape(repr(expected))} | {md_escape(display)} | "
        f"{'FAIL' if bad else 'PASS'} |"
    )


def check_constants(ctx: Context) -> CheckResult:
    r = CheckResult("constants", "覆盖半径 / 去重容差 / L3 阈值 / 端口常量")
    expect = ctx.expect
    r.md.append("| 常量 | 期望 | 实测来源 | 结果 |")
    r.md.append("|---|---|---|---|")

    _probe_group(
        ctx,
        r,
        "覆盖半径",
        expect["coverage_radius_deg"],
        "float",
        [
            Probe(
                "数据库 db/config.py COVERAGE_RADIUS_DEG",
                "系统/数据库工具/db/config.py",
                lambda p: ast_assign(p, "COVERAGE_RADIUS_DEG"),
            ),
            Probe(
                "数据库 client.py epochs_v1.radius",
                "系统/数据库工具/client.py",
                lambda p: ast_func_default(p, "epochs_v1", "radius"),
            ),
            Probe(
                "数据库 client.py coverage_v1.radius",
                "系统/数据库工具/client.py",
                lambda p: ast_func_default(p, "coverage_v1", "radius"),
            ),
            Probe(
                "视图 main.js HIT_RADIUS_DEG",
                "系统/视图工具/js/main.js",
                lambda p: js_number(p, "HIT_RADIUS_DEG"),
            ),
            Probe(
                "视图 skymap.js lim",
                "系统/视图工具/js/skymap.js",
                lambda p: js_number(p, "lim"),
            ),
        ],
    )
    _probe_group(
        ctx,
        r,
        "单片半宽",
        expect["frame_half_width_deg"],
        "float",
        [
            Probe(
                "数据库 db/config.py FRAME_HALF_WIDTH_DEG",
                "系统/数据库工具/db/config.py",
                lambda p: ast_assign(p, "FRAME_HALF_WIDTH_DEG"),
            ),
            Probe(
                "视图 skymap.js half",
                "系统/视图工具/js/skymap.js",
                lambda p: js_number(p, "half"),
            ),
        ],
    )
    _probe_group(
        ctx,
        r,
        "去重容差（天）",
        expect["dedupe_tol_days"],
        "float",
        [
            Probe(
                "app epoch_source._dedupe_and_sort tol_days",
                "系统/app/core/epoch_source.py",
                lambda p: ast_func_default(p, "_dedupe_and_sort", "tol_days"),
            ),
            Probe(
                "app data.dedupe_epochs tol_days",
                "系统/app/core/data.py",
                lambda p: ast_func_default(p, "dedupe_epochs", "tol_days"),
            ),
            Probe(
                "DB catalog.dedupe_epoch_refs tol",
                "系统/数据库工具/db/catalog.py",
                lambda p: ast_func_default(p, "dedupe_epoch_refs", "tol"),
            ),
            Probe(
                "DB catalog.coverage 内 0.02 天",
                "系统/数据库工具/db/catalog.py",
                lambda p, tol=expect["dedupe_tol_days"]: literal_number_in_func(p, "coverage", tol),
            ),
        ],
    )
    _probe_group(
        ctx,
        r,
        "L3 最少期数",
        expect["l3_min_epochs"],
        "int",
        [
            Probe(
                "app motion.py L3 条件 n >= N",
                "系统/app/core/motion.py",
                lambda p: l3_pair(p)[0],
            )
        ],
    )
    _probe_group(
        ctx,
        r,
        "L3 最少基线（年）",
        expect["l3_min_baseline_years"],
        "float",
        [
            Probe(
                "app motion.py L3 条件 baseline_years >= Y",
                "系统/app/core/motion.py",
                lambda p: l3_pair(p)[1],
            )
        ],
    )
    _probe_group(
        ctx,
        r,
        "L3 文案（≥6 期且跨 6 个月）",
        True,
        "bool",
        [
            Probe(
                "app server.py THEORY_BLOCK L3",
                "系统/app/server.py",
                lambda p: label_contains(p, "≥6 期且跨 6 个月"),
            ),
            Probe(
                "数据库 db/config.py READINESS_LABELS L3",
                "系统/数据库工具/db/config.py",
                lambda p: label_contains(p, "≥6 期且跨 6 个月"),
            ),
        ],
    )
    _probe_group(
        ctx,
        r,
        "MoveScope 端口",
        expect["app_port"],
        "int",
        [
            Probe(
                "app server.py --port 默认",
                "系统/app/server.py",
                lambda p: ast_argparse_default(p, "--port"),
            ),
            Probe(
                "视图 main.js MOVESCOPE_ORIGIN",
                "系统/视图工具/js/main.js",
                lambda p: js_port(p, "MOVESCOPE_ORIGIN"),
            ),
            Probe(
                "启动器 lib.sh APP_PORT",
                "项目主控/启动/lib.sh",
                lambda p: sh_var_port(p, "APP_PORT", "LAUNCH_APP_PORT"),
            ),
        ],
    )
    _probe_group(
        ctx,
        r,
        "数据库端口",
        expect["db_port"],
        "int",
        [
            Probe(
                "数据库 start.sh 默认端口",
                "系统/数据库工具/start.sh",
                sh_default_port,
            ),
            Probe(
                "数据库 cli.py serve --port 默认",
                "系统/数据库工具/cli.py",
                lambda p: ast_argparse_default(p, "--port"),
            ),
            Probe(
                "数据库 db/server.py serve port 默认",
                "系统/数据库工具/db/server.py",
                lambda p: ast_func_default(p, "serve", "port"),
            ),
            Probe(
                "启动器 lib.sh DB_PORT",
                "项目主控/启动/lib.sh",
                lambda p: sh_var_port(p, "DB_PORT", "LAUNCH_DB_PORT"),
            ),
        ],
    )
    _probe_group(
        ctx,
        r,
        "视图端口",
        expect["view_port"],
        "int",
        [
            Probe(
                "视图 start.sh 默认端口",
                "系统/视图工具/start.sh",
                sh_default_port,
            ),
            Probe(
                "启动器 lib.sh VIEW_PORT",
                "项目主控/启动/lib.sh",
                lambda p: sh_var_port(p, "VIEW_PORT", "LAUNCH_VIEW_PORT"),
            ),
        ],
    )
    if not r.stats.get("constants"):
        r.skip("未找到可检查的常量源文件")
    return r


# --------------------------------------------------------------------------- 5. readiness


def check_readiness(ctx: Context) -> CheckResult:
    r = CheckResult("readiness", "coverage_level 与 analysis_readiness 语义分离")
    expect = ctx.expect
    app_epoch_source = "系统/app/core/epoch_source.py"
    app_motion = "系统/app/core/motion.py"
    db_catalog = "系统/数据库工具/db/catalog.py"
    db_server = "系统/数据库工具/db/server.py"

    coverage_src = None
    if not ctx.path(app_epoch_source).is_file():
        r.warn(f"{app_epoch_source} 缺失，跳过 app coverage 检查", file=app_epoch_source)
    else:
        try:
            coverage_src = func_source(ctx.path(app_epoch_source), "coverage_for_epochs")
            if "coverage_level" not in coverage_src:
                r.error("app coverage_for_epochs 未返回 coverage_level", file=app_epoch_source)
            if re.search(r"[\"']readiness[\"']", coverage_src):
                r.error("app coverage_for_epochs 同时返回 readiness（C/L 混用）", file=app_epoch_source)
            else:
                r.note("app coverage_for_epochs 只返回 coverage_level（C0–C3）")
        except LookupError as exc:
            r.error(f"app coverage_for_epochs 检查失败: {exc}", file=app_epoch_source)

    if not ctx.path(app_motion).is_file():
        r.warn(f"{app_motion} 缺失，跳过 app motion 检查", file=app_motion)
    else:
        text = ctx.read_text(app_motion)
        if "readiness" not in text:
            r.error("app motion.py 未返回 readiness（L0–L3）", file=app_motion)
        elif "coverage_level" in text:
            r.error("app motion.py 同时出现 coverage_level（C/L 混用）", file=app_motion)
        else:
            r.note("app motion.py 只返回 readiness（L0–L3）")

    if not ctx.path(db_server).is_file():
        r.warn(f"{db_server} 缺失，跳过 v1 coverage 剥离检查", file=db_server)
    else:
        try:
            v1_src = func_source(ctx.path(db_server), "_api_v1_coverage")
            if not re.search(r"pop\(\s*[\"']readiness[\"']", v1_src):
                r.error(
                    "DB /api/v1/coverage 未剥离 readiness（会把 L 级混进覆盖响应）",
                    file=db_server,
                )
            else:
                r.note("DB /api/v1/coverage 显式 pop('readiness')")
        except LookupError as exc:
            r.error(f"DB _api_v1_coverage 检查失败: {exc}", file=db_server)

    legacy_threshold = None
    coverage_c3_days = None
    app_c3_days = None
    if not ctx.path(db_catalog).is_file():
        r.warn(f"{db_catalog} 缺失，跳过 DB 旧 readiness/coverage 阈值检查", file=db_catalog)
    else:
        try:
            cov_src = func_source(ctx.path(db_catalog), "coverage")
            if not re.search(r"[\"']deprecated[\"']\s*:\s*True", cov_src):
                r.error("DB 旧 coverage() 的 readiness 未标 deprecated", file=db_catalog)
            if "analysis_readiness" not in cov_src:
                r.warn("DB 旧 coverage() 未提示 analysis_readiness 由 app 管线给出", file=db_catalog)
            else:
                r.note("DB 旧 coverage() 的 readiness 标 deprecated，并提示 analysis_readiness 由 app 给出")
        except LookupError as exc:
            r.error(f"DB coverage() 检查失败: {exc}", file=db_catalog)

        try:
            legacy_src = func_source(ctx.path(db_catalog), "readiness_level")
            match = re.search(r"baseline_days\s*>=\s*([0-9.]+)", legacy_src)
            if match:
                legacy_threshold = float(match.group(1))
        except LookupError:
            legacy_threshold = None

        try:
            cov_level_src = func_source(ctx.path(db_catalog), "coverage_level")
            match = re.search(r"baseline_days\s*>=\s*([0-9.]+)", cov_level_src)
            if match:
                coverage_c3_days = float(match.group(1))
        except LookupError:
            coverage_c3_days = None

    if coverage_src is not None:
        match = re.search(r"baseline\s*>=\s*([0-9.]+)", coverage_src)
        if match:
            app_c3_days = float(match.group(1))

    if coverage_c3_days is not None and app_c3_days is not None:
        if abs(coverage_c3_days - app_c3_days) > 1e-9:
            r.error(
                f"C3 覆盖阈值不一致：app={app_c3_days:g} 天，DB={coverage_c3_days:g} 天",
                file=db_catalog,
            )
        else:
            r.note(f"C3 覆盖阈值 app/DB 一致：≥{app_c3_days:g} 天")

    l3_days = float(expect["l3_min_baseline_years"]) * 365.25
    if legacy_threshold is not None:
        if abs(legacy_threshold - l3_days) > 0.01:
            r.warn(
                f"DB 旧 readiness L3 阈值 {legacy_threshold:g} 天与 app L3 "
                f"{expect['l3_min_baseline_years']:g} 年（{l3_days:.2f} 天）不同；"
                "旧字段已标 deprecated，不得当作 analysis_readiness 使用。",
                file=db_catalog,
            )
        else:
            r.note("DB 旧 readiness L3 阈值与 app 一致")

    view_candidates = [
        ctx.rel(path) for path in sorted(ctx.path("系统/视图工具/js").glob("*.js"))
    ] if ctx.path("系统/视图工具/js").is_dir() else []
    view_candidates.append("系统/视图工具/index.html")
    for rel in view_candidates:
        if not ctx.path(rel).is_file():
            continue
        text = ctx.read_text(rel)
        if re.search(r"analysis_readiness|\breadiness\b", text):
            r.error("视图工具出现 readiness/analysis_readiness（应由 app 管线负责）", file=rel)

    misuse_pattern = re.compile(
        r"(?:[\"']?coverage_level[\"']?\s*[:=]\s*[\"']?L[0-3])|"
        r"(?:[\"']?readiness[\"']?\s*[:=]\s*[\"']?C[0-3])"
    )
    for rel in [
        app_epoch_source,
        app_motion,
        "系统/app/server.py",
        db_catalog,
        db_server,
        *view_candidates,
    ]:
        if not ctx.path(rel).is_file():
            continue
        text = ctx.read_text(rel)
        match = misuse_pattern.search(text)
        if match:
            line = text[: match.start()].count("\n") + 1
            r.error(f"出现 C/L 值混用：{match.group(0)!r}", file=rel, line=line)

    examples: list[dict[str, Any]] = []
    if ctx.db.available:
        rows = ctx.safe_rows("SELECT target_id, mjd, sep_deg FROM target_epochs ORDER BY target_id, mjd")
        if rows:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(str(row["target_id"]), []).append(row)
            for tid in sorted(grouped):
                deduped = dedupe_mjd_rows(grouped[tid], float(expect["dedupe_tol_days"]))
                mjds = [_as_float(row.get("mjd")) for row in deduped]
                mjds = [m for m in mjds if m is not None]
                baseline = (max(mjds) - min(mjds)) if len(mjds) >= 2 else 0.0
                examples.append(
                    {
                        "target": tid,
                        "n_epochs": len(deduped),
                        "baseline_days": round(baseline, 2),
                        "coverage_level": coverage_level(len(deduped), baseline),
                        "analysis_readiness": readiness_level(len(deduped), baseline, l3_days),
                    }
                )

    r.stats["semantics"] = {
        "app_coverage_returns": "coverage_level (C0–C3)",
        "app_analysis_returns": "readiness (L0–L3)",
        "db_v1_coverage_strips_readiness": True,
        "db_legacy_readiness_deprecated": True,
        "db_legacy_l3_days": legacy_threshold,
        "app_l3_days": round(l3_days, 2),
        "coverage_c3_days": {"app": app_c3_days, "db": coverage_c3_days},
        "examples": examples,
    }
    r.md.append("| 目标 | 去重后历元 | 基线（天） | coverage_level | analysis_readiness |")
    r.md.append("|---|---:|---:|---|---|")
    for row in examples:
        r.md.append(
            f"| {row['target']} | {row['n_epochs']} | {row['baseline_days']} | "
            f"{row['coverage_level']} | {row['analysis_readiness']} |"
        )
    if not examples:
        r.md.append("| — | — | — | — | — |")
    r.md.append("")
    r.md.append(
        "- 覆盖等级只描述“拍过几次、跨多久”（C0–C3）；分析就绪度只描述“测到几期、够不够判”（L0–L3）。"
    )
    return r


# --------------------------------------------------------------------------- 6. docs


@dataclass
class DocClaim:
    key: str
    label: str
    pattern: re.Pattern[str]
    actual_key: str
    path_contains: str | None = None
    path_equals: str | None = None
    line_contains: str | None = None
    line_require: re.Pattern[str] | None = None
    line_skip: re.Pattern[str] | None = None
    max_value: int | None = None


def check_docs(ctx: Context) -> CheckResult:
    r = CheckResult("docs", "README / INTEGRATION 数字与实测一致")
    expect = ctx.expect

    def measured_int(sql: str) -> int | None:
        value = ctx.safe_scalar(sql)
        return int(value) if value is not None else None

    frames = measured_int("SELECT COUNT(*) FROM frames")
    exposures = measured_int("SELECT COUNT(*) FROM observations")
    epochs = measured_int("SELECT COUNT(*) FROM target_epochs")
    targets = measured_int("SELECT COUNT(*) FROM targets")
    db_weeks = measured_int("SELECT COUNT(*) FROM weeks")
    cutouts = measured_int("SELECT COUNT(*) FROM cutouts")
    headers = measured_int("SELECT COUNT(*) FROM target_epochs WHERE frame_id IS NOT NULL")

    view_rows: list[list[Any]] = []
    view_doc: Any = None
    try:
        view_doc = ctx.load_json("系统/视图工具/data/fields.json")
        if isinstance(view_doc, dict) and isinstance(view_doc.get("fields"), list):
            view_rows = [row for row in view_doc["fields"] if isinstance(row, list) and len(row) == 10]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if frames is None and view_rows:
        frames = len(view_rows)
    if exposures is None and isinstance(view_doc, dict) and view_doc.get("exposures") is not None:
        exposures = int(view_doc["exposures"])
    if epochs is None:
        try:
            app_doc = ctx.load_json("系统/app/data/epoch_catalog.json")
            raw = app_doc.get("epochs") if isinstance(app_doc, dict) else None
            if isinstance(raw, dict):
                epochs = sum(len(items) for items in raw.values() if isinstance(items, list))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    if targets is None:
        try:
            app_doc = ctx.load_json("系统/app/data/targets.json")
            raw = app_doc.get("targets") if isinstance(app_doc, dict) else None
            if isinstance(raw, list):
                targets = len(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    if headers is None:
        try:
            manifest = ctx.load_json("系统/app/data/header_manifest.json")
            entries = manifest.get("entries") if isinstance(manifest, dict) else None
            if isinstance(entries, list):
                headers = len(entries)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    local_weeks = len({week_from_obsid(row[0]) for row in view_rows if week_from_obsid(row[0])})
    if not local_weeks and view_rows:
        local_weeks = None

    schema_version = None
    try:
        schema_version = int(ast_assign(ctx.path("系统/数据库工具/db/config.py"), "SCHEMA_VERSION"))
    except (FileNotFoundError, LookupError, TypeError, ValueError):
        pass
    db_schema = ctx.safe_scalar("SELECT value FROM meta WHERE key='schema_version'")
    if db_schema is not None:
        try:
            schema_version = int(db_schema)
        except (TypeError, ValueError):
            pass

    app_tests = count_test_defs(ctx.path("系统/app/tests"))
    db_tests = count_test_defs(ctx.path("系统/数据库工具/tests"))

    actuals: dict[str, Any] = {
        "frames": frames,
        "fields_rows": frames,
        "exposures": exposures,
        "epochs": epochs,
        "targets": targets,
        "db_weeks": db_weeks,
        "local_weeks": local_weeks,
        "headers": headers,
        "cutouts": cutouts,
        "schema": schema_version,
        "app_tests": app_tests,
        "db_tests": db_tests,
    }
    fallback = {
        "frames": expect["fields_total"],
        "fields_rows": expect["fields_total"],
        "exposures": expect["exposures_total"],
        "epochs": expect["epochs_total"],
        "targets": expect["targets_total"],
        "db_weeks": expect["db_weeks_total"],
        "local_weeks": expect["local_weeks_total"],
        "headers": expect["headers_total"],
        "cutouts": expect["cutouts_total"],
        "schema": expect["schema_version"],
        "app_tests": expect["app_tests"],
        "db_tests": expect["db_tests"],
    }
    for key, value in list(actuals.items()):
        if value is None:
            actuals[key] = fallback[key]

    claims = [
        DocClaim(
            "frames",
            "帧数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*帧(?!数)"),
            "frames",
            line_skip=FRAME_LINE_SKIP,
            max_value=100_000,
        ),
        DocClaim(
            "fields_rows",
            "fields 行数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*行(?!数)"),
            "fields_rows",
            line_skip=ROW_LINE_SKIP,
            max_value=100_000,
        ),        DocClaim(
            "exposures",
            "曝光数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*次[^，。；\n]{0,12}曝光"),
            "exposures",
            line_skip=COUNT_LINE_SKIP,
        ),
        DocClaim(
            "epochs",
            "历元数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*条(?:目标|候选)?历元"),
            "epochs",
            line_skip=COUNT_LINE_SKIP,
        ),
        DocClaim(
            "targets",
            "目标数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*个目标"),
            "targets",
            line_skip=TARGET_LINE_SKIP,
        ),
        DocClaim(
            "headers",
            "FITS 头数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*份"),
            "headers",
            line_require=re.compile(r"FITS|头|header", re.I),
            line_skip=COUNT_LINE_SKIP,
        ),
        DocClaim(
            "cutouts",
            "cutout 数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*个切割产品"),
            "cutouts",
            line_skip=COUNT_LINE_SKIP,
        ),
        DocClaim(
            "db_weeks",
            "数据库观测周数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*个周"),
            "db_weeks",
            path_contains="系统/数据库工具",
            line_skip=COUNT_LINE_SKIP,
        ),
        DocClaim(
            "local_weeks",
            "视图本地观测周数",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*个观测周"),
            "local_weeks",
            path_contains="系统/视图工具/README.md",
        ),
        DocClaim(
            "schema",
            "SQLite schema 版本",
            re.compile(r"schema\s+v(\d+)", re.I),
            "schema",
        ),
        DocClaim(
            "app_tests",
            "app 单测数",
            re.compile(r"(\d+)\s*(?:项|tests?)\b", re.I),
            "app_tests",
            path_equals="README.md",
            line_require=re.compile(r"单测|测试|tests?\b", re.I),
        ),
        DocClaim(
            "db_tests",
            "DB 单测数",
            re.compile(r"(\d+)\s*(?:个单元/接口测试|tests?)\b", re.I),
            "db_tests",
            path_contains="系统/数据库工具",
            line_require=re.compile(r"测试|tests?\b", re.I),
        ),
        DocClaim(
            "exposures_en",
            "曝光数（英文）",
            re.compile(r"(?<![\w.,\-–])(\d{1,3}(?:,\d{3})+|\d+)\s*exposures?\b", re.I),
            "exposures",
            line_skip=COUNT_LINE_SKIP,
        ),
    ]

    docs = [str(item) for item in (expect.get("docs") or DEFAULT_DOCS)]
    checked_by_claim: dict[str, int] = {claim.key: 0 for claim in claims}
    mismatches_by_claim: dict[str, int] = {claim.key: 0 for claim in claims}
    docs_found = 0
    missing_docs: list[str] = []

    for rel in docs:
        path = ctx.path(rel)
        if not path.is_file():
            missing_docs.append(rel)
            continue
        docs_found += 1
        text = ctx.read_text(rel)
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(marker in line for marker in HISTORICAL_MARKERS):
                continue
            for claim in claims:
                if claim.path_contains and claim.path_contains not in rel:
                    continue
                if claim.path_equals and rel != claim.path_equals:
                    continue
                if claim.line_contains and claim.line_contains not in line:
                    continue
                if claim.line_require and not claim.line_require.search(line):
                    continue
                if claim.line_skip and claim.line_skip.search(line):
                    continue
                for match in claim.pattern.finditer(line):
                    raw_value = next((group for group in match.groups() if group is not None), None)
                    if raw_value is None:
                        continue
                    value = int(raw_value.replace(",", ""))
                    if claim.max_value is not None and value >= claim.max_value:
                        continue
                    prefix = line[max(0, match.start() - 8) : match.start()]
                    if any(token in prefix for token in ("第", "每", "^")):
                        continue
                    expected = actuals.get(claim.actual_key)
                    if expected is None:
                        continue
                    checked_by_claim[claim.key] += 1
                    if value != int(expected):
                        mismatches_by_claim[claim.key] += 1
                        r.error(
                            f"{claim.label}写成 {value}，实测 {expected}（{rel}:{lineno}）",
                            file=rel,
                            line=lineno,
                            evidence={"text": line.strip(), "expected": int(expected), "actual": value},
                        )

    if missing_docs:
        r.warn("部分文档不存在：" + "、".join(missing_docs))
    if docs_found == 0:
        r.skip("文档列表中的文件均不存在")

    total_checked = sum(checked_by_claim.values())
    total_mismatch = sum(mismatches_by_claim.values())
    r.note(f"共检查 {total_checked} 条数字声明，{total_mismatch} 条不一致；文档 {docs_found}/{len(docs)} 个可读")
    r.stats["actuals"] = actuals
    r.stats["claims"] = {
        "checked": checked_by_claim,
        "mismatches": mismatches_by_claim,
        "docs_found": docs_found,
        "docs_missing": missing_docs,
    }
    r.md.append("| 数字项 | 期望（实测） | 已检查声明 | 不一致 |")
    r.md.append("|---|---:|---:|---:|")
    for claim in claims:
        r.md.append(
            f"| {claim.label} | {actuals.get(claim.actual_key)} | "
            f"{checked_by_claim[claim.key]} | {mismatches_by_claim[claim.key]} |"
        )
    r.md.append("")
    r.md.append(
        f"- 共检查 {total_checked} 条数字声明，{total_mismatch} 条不一致；"
        f"文档 {docs_found}/{len(docs)} 个可读。"
    )
    return r


# --------------------------------------------------------------------------- 7. schema


def check_schema(ctx: Context) -> CheckResult:
    r = CheckResult("schema", "DB schema/API 版本与表完整性")
    expect = ctx.expect
    config = "系统/数据库工具/db/config.py"

    source_schema = None
    source_api = None
    if not ctx.path(config).is_file():
        r.warn(f"{config} 缺失，跳过源码 schema 版本检查", file=config)
    else:
        try:
            source_schema = int(ast_assign(ctx.path(config), "SCHEMA_VERSION"))
            if source_schema != int(expect["schema_version"]):
                r.error(f"源码 SCHEMA_VERSION={source_schema}，期望 {expect['schema_version']}", file=config)
            else:
                r.note(f"源码 SCHEMA_VERSION={source_schema}")
        except (LookupError, TypeError, ValueError) as exc:
            r.error(f"无法读取源码 SCHEMA_VERSION: {exc}", file=config)
        try:
            source_api = str(ast_assign(ctx.path(config), "API_SCHEMA_VERSION"))
            if source_api != str(expect["api_schema_version"]):
                r.error(
                    f"源码 API_SCHEMA_VERSION={source_api!r}，期望 {expect['api_schema_version']!r}",
                    file=config,
                )
            else:
                r.note(f"源码 API_SCHEMA_VERSION={source_api}")
        except (LookupError, TypeError, ValueError) as exc:
            r.error(f"无法读取源码 API_SCHEMA_VERSION: {exc}", file=config)

    db_meta = None
    tables: list[str] = []
    quick_check = None
    if not ctx.db.available:
        r.skip(f"DB 不可用（{ctx.db.reason}），仅完成源码版本检查")
    else:
        db_meta = ctx.safe_scalar("SELECT value FROM meta WHERE key='schema_version'")
        if db_meta is None:
            r.error("DB meta 缺少 schema_version", file="系统/数据库工具/var/catalog.sqlite")
        else:
            try:
                if int(db_meta) != int(expect["schema_version"]):
                    r.error(f"DB meta schema_version={db_meta}，期望 {expect['schema_version']}")
                else:
                    r.note(f"DB meta schema_version={db_meta}")
            except (TypeError, ValueError):
                r.error(f"DB meta schema_version 非整数: {db_meta!r}")
        rows = ctx.safe_rows("SELECT name FROM sqlite_master WHERE type='table'")
        if rows is not None:
            tables = sorted(str(row["name"]) for row in rows)
        required = {"targets", "target_epochs", "frames", "fields_snapshot", "observations", "weeks", "cutouts", "meta"}
        missing = sorted(required - set(tables))
        if missing:
            r.error("DB 缺少表：" + "、".join(missing))
        else:
            r.note("DB 必需表齐全：" + "、".join(sorted(required)))
        try:
            assert ctx.db.conn is not None
            quick_check = str(ctx.db.conn.execute("PRAGMA quick_check").fetchone()[0])
            if quick_check != "ok":
                r.error(f"DB PRAGMA quick_check={quick_check!r}")
            else:
                r.note("DB PRAGMA quick_check=ok")
        except Exception as exc:  # noqa: BLE001
            r.warn(f"DB quick_check 失败: {exc}")

    r.stats["schema"] = {
        "source": source_schema,
        "db_meta": db_meta,
        "api": source_api,
        "tables": tables,
        "quick_check": quick_check,
    }
    r.md.append("| 项 | 值 |")
    r.md.append("|---|---|")
    r.md.append(f"| 源码 SCHEMA_VERSION | {source_schema} |")
    r.md.append(f"| DB meta schema_version | {db_meta} |")
    r.md.append(f"| 源码 API_SCHEMA_VERSION | {source_api} |")
    r.md.append(f"| DB 表总数（含内部表） | {len(tables)}（{', '.join(tables) if tables else '—'}） |")
    r.md.append(f"| PRAGMA quick_check | {quick_check or '—'} |")
    return r


# --------------------------------------------------------------------------- 报告与入口


CHECKS: list[Callable[[Context], CheckResult]] = [
    check_targets,
    check_epochs,
    check_fields,
    check_constants,
    check_readiness,
    check_docs,
    check_schema,
]


def _summary(results: list[CheckResult]) -> dict[str, Any]:
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    errors = sum(result.errors for result in results)
    warnings = sum(result.warnings for result in results)
    return {
        "checks_total": len(results),
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "warn": counts["WARN"],
        "skip": counts["SKIP"],
        "errors": errors,
        "warnings": warnings,
        "drift": errors > 0,
    }


def _build_report(ctx: Context, results: list[CheckResult], exit_code: int) -> dict[str, Any]:
    summary = _summary(results)
    findings = []
    for result in results:
        findings.extend(f.to_dict() for f in result.findings)
    return {
        "tool": "t6-drift",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(ctx.root),
        "expect": ctx.rel(ctx.expect_path),
        "database": {
            "path": ctx.rel(ctx.db_path),
            "present": ctx.db_path.is_file(),
            "available": ctx.db.available,
            "mode": "sqlite-direct-readonly",
            "immutable": ctx.db.immutable,
            "reason": ctx.db.reason or None,
        },
        "environment": {
            "missing_core": list(ctx.missing_core),
        },
        "exit_code": exit_code,
        "summary": summary,
        "checks": [result.to_dict() for result in results],
        "findings": findings,
    }


def _render_markdown(report: dict[str, Any], results: list[CheckResult]) -> str:
    summary = report["summary"]
    lines: list[str] = []
    lines.append("# T6 跨工具漂移报告")
    lines.append("")
    lines.append("> 只读检查：不启动、不依赖 DB 服务，直接只读 `catalog.sqlite`；工具本身不修改任何源码。")
    lines.append("")
    lines.append(f"- 生成时间：`{report['generated_at']}`")
    lines.append(f"- 工作区：`{report['root']}`")
    lines.append(f"- 期望基线：`{report['expect']}`")
    db = report["database"]
    db_line = f"`{db['path']}`"
    if not db["present"]:
        db_line += "（文件不存在，DB 类检查 SKIP）"
    elif not db["available"]:
        db_line += f"（不可读：{db['reason']}，DB 类检查 SKIP）"
    else:
        db_line += "（只读直读，未使用 DB HTTP 服务）"
    lines.append(f"- 数据库：{db_line}")
    if report["environment"]["missing_core"]:
        lines.append("- 环境缺失：" + "、".join(f"`{p}`" for p in report["environment"]["missing_core"]))
    lines.append(
        f"- 退出码：**{report['exit_code']}**"
        + ("（发现漂移）" if report["exit_code"] == 1 else "（全过）" if report["exit_code"] == 0 else "（环境缺失）")
    )
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 检查 | 状态 | 错误 | 警告 | 说明 |")
    lines.append("|---|---|---:|---:|---|")
    for result in results:
        note = result.skip_reason or (result.notes[-1] if result.notes else "")
        lines.append(
            f"| `{result.id}` | **{result.status}** | {result.errors} | {result.warnings} | "
            f"{md_escape(note)[:100]} |"
        )
    lines.append(
        f"| **合计** | — | **{summary['errors']}** | **{summary['warnings']}** | "
        f"{summary['checks_total']} 类检查："
        f"{summary['pass']} PASS / {summary['fail']} FAIL / {summary['warn']} WARN / {summary['skip']} SKIP |"
    )
    lines.append("")

    error_findings = [f for result in results for f in result.findings if f.severity == "error"]
    warning_findings = [f for result in results for f in result.findings if f.severity == "warning"]
    if error_findings:
        lines.append("## 漂移项")
        lines.append("")
        for index, finding in enumerate(error_findings, 1):
            location = ""
            if finding.file:
                location = f" `{finding.file}`" + (f":{finding.line}" if finding.line else "")
            lines.append(f"{index}. **`{finding.check}`** · {location} — {finding.message}")
        lines.append("")
    else:
        lines.append("## 漂移项")
        lines.append("")
        lines.append("未发现 ERROR 级漂移。")
        lines.append("")
    if warning_findings:
        lines.append("## 警告")
        lines.append("")
        for index, finding in enumerate(warning_findings, 1):
            location = ""
            if finding.file:
                location = f" `{finding.file}`" + (f":{finding.line}" if finding.line else "")
            lines.append(f"{index}. **`{finding.check}`** · {location} — {finding.message}")
        lines.append("")

    lines.append("## 检查详情")
    lines.append("")
    for result in results:
        lines.append(f"### `{result.id}` · {result.title}")
        lines.append("")
        lines.append(f"- 状态：**{result.status}**（错误 {result.errors} / 警告 {result.warnings}）")
        if result.skip_reason:
            lines.append(f"- SKIP：{result.skip_reason}")
        for note in result.notes:
            lines.append(f"- {note}")
        lines.append("")
        if result.md:
            lines.extend(result.md)
            lines.append("")
        if result.findings:
            lines.append("**明细**")
            lines.append("")
            for finding in result.findings:
                location = ""
                if finding.file:
                    location = f" `{finding.file}`" + (f":{finding.line}" if finding.line else "")
                lines.append(f"- `{finding.severity}`{location} — {finding.message}")
            lines.append("")
        lines.append("")

    lines.append("## 说明")
    lines.append("")
    lines.append("- 退出码：0 = 全过；1 = 发现漂移（ERROR）；2 = 环境缺失（核心 JSON 或报告目录不可用）。")
    lines.append("- DB 服务未启动不影响检查：本工具直接只读 `catalog.sqlite`；文件缺失/不可读时 DB 类检查标 SKIP。")
    lines.append("- 本工具只读源码与数据，只写本目录下的 `report.json` / `report.md`。")
    lines.append("")
    return "\n".join(lines)


def _write_reports(report: dict[str, Any], markdown: str, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")


def _print_stdout(report: dict[str, Any], results: list[CheckResult], json_path: Path, md_path: Path, quiet: bool) -> None:
    db = report["database"]
    print("T6 漂移检查 · 只读")
    print(f"工作区: {report['root']}")
    if db["available"]:
        print(f"数据库: {db['path']}（直读 sqlite，未使用 DB 服务）")
    elif db["present"]:
        print(f"数据库: {db['path']}（不可读，DB 类检查 SKIP：{db['reason']}）")
    else:
        print(f"数据库: {db['path']}（不存在，DB 类检查 SKIP）")
    if not quiet:
        print("")
        for result in results:
            print(f"[{result.status:<4}] {result.id:<10} {result.title}")
        print("")
    findings = [f for result in results for f in result.findings]
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    if errors:
        print(f"发现 {len(errors)} 项漂移：")
        for finding in errors[:20]:
            location = ""
            if finding.file:
                location = f" ({finding.file}" + (f":{finding.line}" if finding.line else "") + ")"
            print(f" - [{finding.check}] {finding.message}{location}")
        if len(errors) > 20:
            print(f" ... 另有 {len(errors) - 20} 项，见报告")
    else:
        print("未发现 ERROR 级漂移。")
    if warnings:
        print(f"\n警告 {len(warnings)} 项（不影响退出码）：")
        for finding in warnings[:10]:
            location = ""
            if finding.file:
                location = f" ({finding.file}" + (f":{finding.line}" if finding.line else "") + ")"
            print(f" - [{finding.check}] {finding.message}{location}")
        if len(warnings) > 10:
            print(f" ... 另有 {len(warnings) - 10} 项，见报告")
    print("")
    print(f"报告: {json_path}")
    print(f"      {md_path}")
    print(f"退出码: {report['exit_code']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="T6 只读跨工具漂移检查器（app / 数据库工具 / 视图工具）",
    )
    parser.add_argument("--root", default=None, help="工作区根目录（默认仓库根）")
    parser.add_argument("--db", default=None, help="catalog.sqlite 路径（默认 <root>/系统/数据库工具/var/catalog.sqlite）")
    parser.add_argument("--expect", default=None, help="基线 JSON（默认本目录 expect.json）")
    parser.add_argument("--out-dir", default=None, help="报告输出目录（默认本目录）")
    parser.add_argument("--json-out", default=None, help="report.json 路径（覆盖 --out-dir）")
    parser.add_argument("--md-out", default=None, help="report.md 路径（覆盖 --out-dir）")
    parser.add_argument("--quiet", action="store_true", help="不打印逐项状态，只打印漂移与汇总")
    parser.add_argument("--version", action="version", version=f"t6-drift {VERSION}")
    args = parser.parse_args(argv)

    tool_dir = Path(__file__).resolve().parent
    root = Path(args.root).expanduser().resolve() if args.root else tool_dir.parents[1]
    if not root.is_dir():
        print(f"环境缺失：工作区不存在: {root}", file=sys.stderr)
        return 2

    if args.expect:
        expect_path = Path(args.expect).expanduser().resolve()
        if not expect_path.is_file():
            print(f"环境缺失：expect 文件不存在: {expect_path}", file=sys.stderr)
            return 2
    else:
        expect_path = tool_dir / "expect.json"

    expect: dict[str, Any] = dict(DEFAULT_EXPECT)
    if expect_path.is_file():
        try:
            loaded = json.loads(expect_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"环境缺失：无法读取 expect 文件 {expect_path}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(loaded, dict):
            print(f"环境缺失：expect 文件不是对象: {expect_path}", file=sys.stderr)
            return 2
        expect.update(loaded)
    else:
        print(f"提示：未找到 {expect_path}，使用内置基线", file=sys.stderr)

    db_path = (
        Path(args.db).expanduser().resolve()
        if args.db
        else root / "系统" / "数据库工具" / "var" / "catalog.sqlite"
    )
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else tool_dir
    json_path = Path(args.json_out).expanduser().resolve() if args.json_out else out_dir / "report.json"
    md_path = Path(args.md_out).expanduser().resolve() if args.md_out else out_dir / "report.md"

    ctx = Context(root=root, expect=expect, expect_path=expect_path, db_path=db_path)
    results: list[CheckResult] = []
    try:
        for check in CHECKS:
            try:
                result = check(ctx)
            except Exception as exc:  # noqa: BLE001 - 单个检查异常不应中断整份报告
                result = CheckResult(check.__name__, "检查器内部异常")
                result.error(f"{type(exc).__name__}: {exc}")
            results.append(result)
    finally:
        ctx.db.close()

    errors = sum(result.errors for result in results)
    exit_code = 2 if ctx.missing_core else (1 if errors else 0)
    report = _build_report(ctx, results, exit_code)
    markdown = _render_markdown(report, results)
    try:
        _write_reports(report, markdown, json_path, md_path)
    except OSError as exc:
        print(f"环境缺失：无法写报告 {json_path} / {md_path}: {exc}", file=sys.stderr)
        return 2
    _print_stdout(report, results, json_path, md_path, args.quiet)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
