"""T3 离线演示冻结器 · 共享工具。

只使用 Python 标准库（numpy 由调用方按需引入）。
本文件不读写 app/data/epochcache，只做通用路径/哈希/清单校验。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

# 项目根：项目主控/冻结/common.py -> huellaX
ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "系统"
APP = SYSTEM / "app"

TOOL_ID = "t3-offline-freezer"
SCHEMA_VERSION = "1.0"

EPOCH_STATUSES = {"ok", "missing", "fetch_failed", "no_coverage", "hybrid_no_pixels"}
DONE_STATUSES = {"ok", "hybrid_no_pixels"}

ABS_WIN_RE = re.compile(r"^[A-Za-z]:[\\/]")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class SnapshotError(RuntimeError):
    """快照结构/文件错误：调用方应以退出码 2 结束。"""


# ------------------------------------------------------------------ 基础


def setup_app_path() -> None:
    """把 app/ 放进 sys.path，之后可 `import core.data`（不启动服务）。"""
    import sys

    app = str(APP)
    if app not in sys.path:
        sys.path.insert(0, app)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_today() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False, default=_json_default)


def _json_default(obj):
    try:
        import numpy as np

        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except Exception:  # noqa: BLE001
        pass
    raise TypeError(f"not JSON serializable: {type(obj)!r}")


def read_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json_dumps(obj) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def fmt_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GiB"


# ------------------------------------------------------------------ 路径安全


def is_safe_rel_path(value: str) -> bool:
    """manifest 中的文件路径必须是相对 POSIX 路径，且不含 .. / 盘符 / 反斜杠。"""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or value.startswith("~"):
        return False
    if ABS_WIN_RE.match(value):
        return False
    if "\\" in value:
        return False
    p = PurePosixPath(value)
    if p.is_absolute():
        return False
    parts = [x for x in value.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        return False
    if p.name != parts[-1]:
        return False
    return True


def scan_manifest_paths(obj, prefix="$") -> list[str]:
    """递归检查 manifest 中所有字符串：绝对路径、Windows 路径、`..` 路径段。

    URL（http/https）允许；其 path 段也接受同一规则（本项目 URL 不含 ..）。
    """
    problems: list[str] = []

    def walk(node, here):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{here}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{here}[{i}]")
        elif isinstance(node, str):
            s = node
            if s.startswith("http://") or s.startswith("https://"):
                tail = s.split("://", 1)[1]
                path_part = tail.split("/", 1)[1] if "/" in tail else ""
                if any(seg == ".." for seg in path_part.split("?")[0].split("/")):
                    problems.append(f"{here}: URL 含 .. 路径段: {s}")
                return
            if s.startswith("/") or ABS_WIN_RE.match(s) or "\\" in s:
                problems.append(f"{here}: 绝对路径或反斜杠: {s}")
                return
            if any(seg == ".." for seg in s.split("/")):
                problems.append(f"{here}: 含 .. 路径段: {s}")
        # 数字/布尔/None 不检查

    walk(obj, prefix)
    return problems


def resolve_in_snapshot(snapshot: Path, rel: str) -> Path:
    if not is_safe_rel_path(rel):
        raise SnapshotError(f"非法相对路径: {rel!r}")
    target = (snapshot / rel).resolve()
    root = snapshot.resolve()
    if target != root and root not in target.parents:
        raise SnapshotError(f"路径越出快照根: {rel!r}")
    return target


# ------------------------------------------------------------------ manifest 文件集合


def manifest_epoch_entries(manifest: dict) -> list[tuple[str, dict]]:
    """返回 [(owner_kind, entry)]；owner_kind 为 target:<id> / coord:<ra>,<dec>。"""
    out: list[tuple[str, dict]] = []
    for t in manifest.get("targets", []) or []:
        tid = str(t.get("id", "?"))
        for e in t.get("epochs", []) or []:
            out.append((f"target:{tid}", e))
    for c in manifest.get("coords", []) or []:
        cid = f"coord:{c.get('ra')},{c.get('dec')}"
        for e in c.get("epochs", []) or []:
            out.append((cid, e))
    return out


def expected_files(manifest: dict) -> dict[str, dict]:
    """从 manifest 汇总所有应随快照分发的文件（epochs + support + results）。

    返回 rel -> {sha256, bytes, role}；同一路径出现多次时要求 sha/bytes 一致。
    """
    files: dict[str, dict] = {}

    def add(rel, sha, size, role):
        if rel is None:
            return
        if not is_safe_rel_path(str(rel)):
            raise SnapshotError(f"manifest 含非法文件路径: {rel!r}")
        item = {"sha256": str(sha or ""), "bytes": int(size) if size is not None else None, "role": role}
        if rel in files:
            old = files[rel]
            if old["sha256"] and item["sha256"] and old["sha256"] != item["sha256"]:
                raise SnapshotError(f"同一文件在 manifest 中有不同 sha256: {rel}")
            if old["bytes"] is not None and item["bytes"] is not None and old["bytes"] != item["bytes"]:
                raise SnapshotError(f"同一文件在 manifest 中有不同 bytes: {rel}")
            return
        files[rel] = item

    for _owner, e in manifest_epoch_entries(manifest):
        if e.get("file"):
            add(e["file"], e.get("sha256"), e.get("bytes"), "epoch")
    for s in manifest.get("support", []) or []:
        if s.get("file"):
            add(s["file"], s.get("sha256"), s.get("bytes"), "support")
    for t in manifest.get("targets", []) or []:
        if t.get("results_file"):
            add(t["results_file"], t.get("results_sha256"), t.get("results_bytes"), "results")
    for c in manifest.get("coords", []) or []:
        if c.get("results_file"):
            add(c["results_file"], c.get("results_sha256"), c.get("results_bytes"), "results")
    return files


def check_snapshot_files(snapshot: Path, manifest: dict) -> dict:
    """逐个文件检查存在性/大小/sha256/符号链接；返回结构化结果。"""
    files = expected_files(manifest)
    errors: list[str] = []
    warnings: list[str] = []
    checked: dict[str, dict] = {}
    for rel, meta in sorted(files.items()):
        path = resolve_in_snapshot(snapshot, rel)
        item = dict(meta)
        item["file"] = rel
        if path.is_symlink():
            errors.append(f"不允许符号链接: {rel}")
            item["status"] = "error"
        elif not path.is_file():
            errors.append(f"缺文件: {rel}")
            item["status"] = "missing"
        else:
            size = path.stat().st_size
            item["actual_bytes"] = size
            if meta["bytes"] is not None and size != meta["bytes"]:
                errors.append(f"大小不符: {rel}（manifest {meta['bytes']}，实际 {size}）")
                item["status"] = "mismatch"
            else:
                digest = sha256_file(path)
                item["actual_sha256"] = digest
                if meta["sha256"] and digest != meta["sha256"]:
                    errors.append(f"sha256 不符: {rel}")
                    item["status"] = "mismatch"
                else:
                    item["status"] = "ok"
        checked[rel] = item
    return {
        "files": checked,
        "errors": errors,
        "warnings": warnings,
        "total_files": len(files),
        "total_bytes": sum((v.get("bytes") or 0) for v in files.values()),
    }


# ------------------------------------------------------------------ checksums


def write_checksums(snapshot: Path, files: dict[str, dict]) -> None:
    lines = []
    for rel in sorted(files):
        meta = files[rel]
        if not meta.get("sha256"):
            continue
        lines.append(f"{meta['sha256']}  {rel}\n")
    write_text_atomic(snapshot / "checksums.txt", "".join(lines))


def parse_checksums(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not path.exists():
        raise SnapshotError("缺少 checksums.txt")
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        m = re.match(r"^([0-9a-f]{64}) [ *](.+)$", line)
        if not m:
            raise SnapshotError(f"checksums.txt 第 {lineno} 行格式错误（应为 'sha256  relative/path'）")
        digest, rel = m.group(1), m.group(2)
        if not is_safe_rel_path(rel):
            raise SnapshotError(f"checksums.txt 第 {lineno} 行路径非法: {rel!r}")
        out.append((digest, rel))
    return out


def list_managed_files(snapshot: Path, subdirs=("epochs", "support", "results")) -> set[str]:
    """列出应受管的实际文件（忽略隐藏文件与目录）。"""
    found: set[str] = set()
    for sub in subdirs:
        root = snapshot / sub
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            if any(part.startswith(".") for part in path.relative_to(snapshot).parts):
                continue
            found.add(path.relative_to(snapshot).as_posix())
    return found


def app_fingerprint(paths: list[Path]) -> dict:
    """计算 app 关键源码/数据指纹：无 git 时用于标记“冻结时的 app 版本”。"""
    files = []
    for p in paths:
        if p.exists():
            files.append({"path": p.relative_to(ROOT).as_posix(), "sha256": sha256_file(p)})
    joined = "\n".join(sorted(f"{f['sha256']}  {f['path']}" for f in files))
    return {"sha256": sha256_text(joined), "files": files}


def app_source_paths() -> list[Path]:
    """参与 app 指纹的文件（影响离线取数/模拟/流水线的部分）。"""
    return [
        APP / "core" / "data.py",
        APP / "core" / "epoch_source.py",
        APP / "core" / "simulator.py",
        APP / "core" / "pipeline.py",
        APP / "core" / "motion.py",
        APP / "core" / "photometry.py",
        APP / "core" / "wcs.py",
        APP / "core" / "fitsio.py",
        APP / "server.py",
        APP / "data" / "targets.json",
        APP / "data" / "epoch_catalog.json",
        APP / "data" / "psf_d2.npy",
    ]
