#!/usr/bin/env python3
"""T4 头文件采集器 / Header Harvester。

用 S3 Range 只读每个 level2 文件的前 64 KB 头，落成 `.hdr` + manifest，
再 `ingest headers` 回填数据库的 date_obs / 像素尺度 / CRPIX / PSF / DQA。

Phase 1（必须）：DB 的 74 条 target_epochs。
Phase 2（可选）：按 week_index.json 的周/探测器列出 S3 key 后采集。

只用标准库；不下载整幅 65 MB FITS。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field as dc_field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.catalog import build_fits_key  # noqa: E402
from db.config import DEFAULT_BLOBS, DEFAULT_DB  # noqa: E402

HDR_BYTES = 65536
S3_BASE = "https://nasa-irsa-spherex.s3.amazonaws.com"
USER_AGENT = "spherex-header-harvester/1.0"
_KEY_RE = re.compile(
    r"^(?P<release>[^/]+)/level2/(?P<week>[^/]+)/(?P<version>[^/]+)/(?P<detdir>\d+)/"
    r"level2_(?P<w2>.+)_(?P<field>\d+)_(?P<rep>\d+)D(?P<det>\d+)_spx_.*\.fits$"
)
_FILE_RE = re.compile(r"level2_(?P<week>.+)_(?P<field>\d+)_(?P<rep>\d+)D(?P<det>\d+)_spx_")


@dataclass
class HarvestItem:
    key: str
    url: str
    release: str | None = None
    week: str | None = None
    version: str | None = None
    obsid: str = ""
    detector: int | None = None
    field: int | None = None
    rep: int | None = None
    file: Path | None = None
    sha256: str | None = None
    bytes: int = 0
    status: str = "planned"
    error: str | None = None
    fetched_at: str | None = None
    targets: list[str] = dc_field(default_factory=list)


# ---------------------------------------------------------------------- 解析/命名
def parse_key(key: str) -> dict | None:
    key = (key or "").strip()
    if key.startswith("s3://"):
        # s3://bucket/release/...
        parts = key.split("/", 3)
        if len(parts) < 4:
            return None
        key = parts[3]
    key = key.lstrip("/")
    m = _KEY_RE.match(key)
    if not m:
        return None
    if m.group("w2") != m.group("week"):
        # 路径周与文件名周不一致：不猜，交给上层报错。
        return None
    return {
        "release": m.group("release"),
        "week": m.group("week"),
        "version": m.group("version"),
        "detector": int(m.group("det")),
        "field": int(m.group("field")),
        "rep": int(m.group("rep")),
        "obsid": f"{m.group('w2')}_{m.group('field')}_{m.group('rep')}",
    }


def planned_from_key(key: str, base_url: str = S3_BASE) -> HarvestItem | None:
    parsed = parse_key(key)
    if not parsed:
        return None
    key = key.strip().lstrip("/")
    if key.startswith("s3://"):
        key = key.split("/", 3)[3].lstrip("/")
    return HarvestItem(
        key=key,
        url=f"{base_url.rstrip('/')}/{key}",
        release=parsed["release"],
        week=parsed["week"],
        version=parsed["version"],
        obsid=parsed["obsid"],
        detector=parsed["detector"],
        field=parsed["field"],
        rep=parsed["rep"],
    )


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def readable_name(item: HarvestItem) -> str:
    return _safe(
        f"{item.release or 'na'}_{item.week or 'na'}_{item.obsid or 'na'}_D{item.detector or 'x'}_{item.version or 'na'}.hdr"
    )[:180]


def assign_files(items: list[HarvestItem], out_dir: str | os.PathLike[str]) -> None:
    """给每个 item 分配可读文件名；同名（不同 key）用 sha1(key)[:8] 消歧。"""
    out = Path(out_dir)
    used: set[str] = set()
    for item in items:
        if item.file is not None:
            used.add(item.file.name)
            continue
        name = readable_name(item)
        if name in used:
            stem, ext = os.path.splitext(name)
            suffix = hashlib.sha1(item.key.encode()).hexdigest()[:8]
            name = f"{stem}_{suffix}{ext}"
        while name in used:
            stem, ext = os.path.splitext(name)
            name = f"{stem}_x{ext}"
        used.add(name)
        item.file = out / name


# ---------------------------------------------------------------------- S3 列表
def _s3_list(base_url: str, prefix: str, *, delimiter: str | None = None, timeout: int = 60) -> tuple[list[str], list[str]]:
    url = f"{base_url.rstrip('/')}/?list-type=2&prefix={urllib.parse.quote(prefix)}&max-keys=1000"
    if delimiter:
        url += f"&delimiter={urllib.parse.quote(delimiter)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    root = ET.fromstring(body)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    keys = [e.text or "" for e in root.findall(".//s3:Contents/s3:Key", ns) if e.text]
    prefixes = [e.text or "" for e in root.findall(".//s3:CommonPrefixes/s3:Prefix", ns) if e.text]
    return keys, prefixes


# ---------------------------------------------------------------------- 计划
def plan_epochs(db_path: str | os.PathLike[str], base_url: str = S3_BASE) -> tuple[list[HarvestItem], dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT e.target_id, e.release, e.week, e.obs_id, e.detector, e.fits_key,
                      w.version
               FROM target_epochs e
               LEFT JOIN weeks w ON w.week=e.week AND w.release=e.release
               ORDER BY e.id"""
        ).fetchall()
    finally:
        conn.close()

    items: list[HarvestItem] = []
    seen: set[str] = set()
    for r in rows:
        key = r["fits_key"]
        if not key:
            key = build_fits_key(r["release"], r["week"], r["version"], r["obs_id"], r["detector"])
        if not key:
            item = HarvestItem(
                key="",
                url="",
                release=r["release"],
                week=r["week"],
                version=r["version"],
                obsid=str(r["obs_id"]),
                detector=int(r["detector"]) if r["detector"] is not None else None,
                status="failed",
                error="fits_key 缺失，且无法由 release/week/version 拼出路径",
            )
            items.append(item)
            continue
        if key in seen:
            continue
        seen.add(key)
        item = planned_from_key(key, base_url)
        if item is None:
            item = HarvestItem(
                key=key,
                url="",
                release=r["release"],
                week=r["week"],
                version=r["version"],
                obsid=str(r["obs_id"]),
                detector=int(r["detector"]) if r["detector"] is not None else None,
                status="failed",
                error="无法解析 fits_key 路径",
            )
        else:
            item.targets = [r["target_id"]]
        items.append(item)
    return items, {"target_epochs": len(rows), "unique_keys": len(seen), "planned": len(items)}


def plan_keys(keys: list[str], base_url: str = S3_BASE) -> tuple[list[HarvestItem], dict]:
    items = []
    seen = set()
    for raw in keys:
        raw = raw.strip()
        if not raw:
            continue
        item = planned_from_key(raw, base_url)
        if item is None:
            items.append(HarvestItem(key=raw, url="", status="failed", error="无法解析 key"))
            continue
        if item.key in seen:
            continue
        seen.add(item.key)
        items.append(item)
    return items, {"keys": len(keys), "unique_keys": len(seen), "planned": len(items)}


def plan_weeks(
    week_index_path: str | os.PathLike[str],
    *,
    weeks: list[str] | None = None,
    detectors: list[int] | None = None,
    base_url: str = S3_BASE,
    limit: int = 0,
    timeout: int = 60,
) -> tuple[list[HarvestItem], dict]:
    with open(week_index_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    wanted_weeks = set(weeks or [])
    wanted_dets = detectors or [1, 2, 3, 4, 5, 6]
    items: list[HarvestItem] = []
    seen: set[str] = set()
    for w in payload.get("weeks", []):
        week = w.get("week")
        if wanted_weeks and week not in wanted_weeks:
            continue
        release = w.get("release")
        version = w.get("version")
        if not (release and week):
            continue
        if not version:
            # 从 S3 前缀发现版本目录；只发一次 delimiter=/ 的 list。
            _, prefixes = _s3_list(base_url, f"{release}/level2/{week}/", delimiter="/", timeout=timeout)
            candidates = sorted(
                p.rstrip("/").split("/")[-1] for p in prefixes if p.rstrip("/").split("/")[-1]
            )
            version = candidates[0] if candidates else None
        if not version:
            items.append(HarvestItem(key="", url="", release=release, week=week, status="failed",
                                     error="周索引缺 version，且 S3 前缀发现失败"))
            continue
        for det in wanted_dets:
            keys, _ = _s3_list(
                base_url, f"{release}/level2/{week}/{version}/{det}/", timeout=timeout
            )
            for key in sorted(keys):
                if not key.endswith(".fits") or key in seen:
                    continue
                item = planned_from_key(key, base_url)
                if item is None:
                    continue
                seen.add(key)
                items.append(item)
                if limit and len(items) >= limit:
                    return items, {"weeks": len(wanted_weeks), "unique_keys": len(seen), "planned": len(items)}
    return items, {"weeks": len(wanted_weeks), "unique_keys": len(seen), "planned": len(items)}


# ---------------------------------------------------------------------- 下载
def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fetch_one(item: HarvestItem, out_dir: Path, *, retries: int, backoff: float, timeout: float) -> HarvestItem:
    if item.url == "":
        return item
    if item.file is None:
        assign_files([item], out_dir)
    path = out_dir / item.file.name
    attempts = retries + 1
    last = ""
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                item.url, headers={"Range": "bytes=0-65535", "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = getattr(r, "status", 200)
                blob = r.read()
            if status == 200:
                last = "HTTP 200（服务器忽略 Range，拒绝整文件下载）"
                break
            if status != 206:
                last = f"HTTP {status}"
                break
            if len(blob) < 1000 or not blob.startswith(b"SIMPLE"):
                last = "响应不是 FITS 头（缺少 SIMPLE）"
                break
            sha = hashlib.sha256(blob).hexdigest()
            if path.exists() and _sha_file(path) == sha:
                item.sha256 = sha
                item.bytes = len(blob)
                item.status = "cached"
                item.fetched_at = _now()
                item.error = None
                return item
            out_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".hdr-", dir=out_dir)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(blob)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            item.sha256 = sha
            item.bytes = len(blob)
            item.status = "ok"
            item.fetched_at = _now()
            item.error = None
            return item
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code == 404:
                break
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt + 1 < attempts:
                    time.sleep(backoff * (2**attempt))
                    continue
                last = f"{last}（尝试 {attempts} 次）"
                break
            break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = str(exc)
            if attempt + 1 < attempts:
                time.sleep(backoff * (2**attempt))
                continue
            last = f"{last}（尝试 {attempts} 次）"
            break
    item.status = "failed"
    item.error = last
    item.fetched_at = _now()
    return item


# ---------------------------------------------------------------------- manifest
def _entry(item: HarvestItem) -> dict:
    return {
        "key": item.key,
        "url": item.url,
        "release": item.release,
        "week": item.week,
        "version": item.version,
        "obsid": item.obsid,
        "detector": item.detector,
        "file": str(item.file) if item.file else None,
        "sha256": item.sha256,
        "bytes": item.bytes,
        "status": item.status,
        "error": item.error,
        "fetched_at": item.fetched_at,
        "targets": item.targets,
    }


def _load_entries(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {e["key"]: e for e in payload.get("entries", []) if e.get("key")}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_manifest(path: Path, entries: dict[str, dict], *, summary: dict, last_run: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_by": "数据库工具/tools/harvest_headers.py",
        "generated_at": _now(),
        "summary": summary,
        "last_run": last_run,
        "entries": list(entries.values()),
    }
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_failures(path: Path, items: list[HarvestItem]) -> None:
    failed = [_entry(x) for x in items if x.status == "failed"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": len(failed), "entries": failed}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------- ingest
def ingest_headers(db: str | os.PathLike[str], blobs: str | os.PathLike[str], out_dir: str | os.PathLike[str]) -> dict:
    from db.ingest import Ingestor

    with Ingestor(db, blobs, quiet=True) as ing:
        h = ing.ingest_headers(out_dir)
        ing.relink_epochs()
        cutouts = ing.relink_cutouts()
        ing.backfill_releases()
        ing.refresh_week_counts()
        linked = ing.conn.execute(
            "SELECT COUNT(*) FROM target_epochs WHERE frame_id IS NOT NULL"
        ).fetchone()[0]
    return {
        "headers": h,
        "epochs_linked": int(linked),
        "cutouts_linked": cutouts.get("rows_updated", 0),
    }


# ---------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SPHEREx 头文件采集器（Range 64 KB）")
    ap.add_argument("--source", choices=["epochs", "weeks", "keys"], default="epochs")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--blobs", default=str(DEFAULT_BLOBS))
    ap.add_argument("--out", default=str(ROOT.parent / "app" / "data" / "header_cache"))
    ap.add_argument("--manifest", default=str(ROOT.parent / "app" / "data" / "header_manifest.json"))
    ap.add_argument("--week-index", default=str(ROOT.parent / "app" / "data" / "week_index.json"))
    ap.add_argument("--keys-file", default=None)
    ap.add_argument("--weeks", nargs="*", default=None)
    ap.add_argument("--detectors", default="1,2,3,4,5,6")
    ap.add_argument("--s3-base", default=S3_BASE)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--backoff", type=float, default=1.5)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--no-ingest", action="store_true")
    ap.add_argument("--failures", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.source == "epochs":
        items, stats = plan_epochs(args.db, args.s3_base)
    elif args.source == "keys":
        if not args.keys_file:
            ap.error("--source keys 需要 --keys-file")
        keys = [x.strip() for x in Path(args.keys_file).read_text(encoding="utf-8").splitlines() if x.strip()]
        items, stats = plan_keys(keys, args.s3_base)
    else:
        dets = [int(x) for x in args.detectors.split(",") if x.strip()]
        items, stats = plan_weeks(
            args.week_index, weeks=args.weeks, detectors=dets, base_url=args.s3_base,
            limit=args.limit, timeout=args.timeout,
        )
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("没有可采集的条目（检查 DB/输入）", file=sys.stderr)
        return 1

    print(f"计划 {len(items)} 条  source={args.source}  out={args.out}")
    for i, item in enumerate(items, start=1):
        target = item.file.name if item.file else readable_name(item)
        print(f"[{i}/{len(items)}] {item.key or '?'} -> {target}")
    if args.dry_run:
        print("dry-run：未下载、未写 manifest、未 ingest")
        return 0

    out = Path(args.out).expanduser().resolve()
    manifest = Path(args.manifest).expanduser().resolve()
    assign_files(items, out)
    previous = _load_entries(manifest)
    now = _now()
    pending: list[HarvestItem] = []
    for item in items:
        prev = previous.get(item.key)
        if args.resume and prev and prev.get("sha256") and item.file and item.file.exists():
            try:
                if _sha_file(item.file) == prev["sha256"]:
                    item.sha256 = prev["sha256"]
                    item.bytes = int(prev.get("bytes") or item.file.stat().st_size)
                    item.status = "cached"
                    item.fetched_at = now
                    continue
            except OSError:
                pass
        pending.append(item)
    if args.resume:
        print(f"下载 0 条（缓存命中 {len(items) - len(pending)} 条）" if not pending else f"待下载 {len(pending)} 条（缓存命中 {len(items) - len(pending)} 条）")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {
            ex.submit(fetch_one, item, out, retries=args.retries, backoff=args.backoff, timeout=args.timeout): item
            for item in pending
        }
        for i, fut in enumerate(as_completed(futs), start=1):
            item = futs[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                item.status = "failed"
                item.error = str(exc)
            if not args.quiet and (i % 10 == 0 or i == len(pending)):
                ok = sum(1 for x in items if x.status in ("ok", "cached"))
                print(f"  {i}/{len(pending)} ok/cached={ok} failed={sum(1 for x in items if x.status == 'failed')}")

    entries = dict(previous)
    for item in items:
        entries[item.key] = _entry(item)
    current = items
    ok = sum(1 for x in current if x.status == "ok")
    cached = sum(1 for x in current if x.status == "cached")
    failed = [x for x in current if x.status == "failed"]
    summary = {
        "entries": len(entries),
        "ok": ok,
        "cached": cached,
        "cached_this_run": cached if args.resume else 0,
        "failed": len(failed),
        "bytes_total": sum(int(e.get("bytes") or 0) for e in entries.values() if e.get("status") in ("ok", "cached")),
    }
    last_run = {"planned": len(items), "fetched": ok, "cached": cached, "failed": len(failed)}
    _write_manifest(manifest, entries, summary=summary, last_run=last_run)
    if args.failures:
        _write_failures(Path(args.failures).expanduser().resolve(), failed)
    print(f"采集完成: ok={ok} cached={cached} failed={len(failed)} manifest={manifest}")

    if failed:
        return 1
    if not args.no_ingest:
        result = ingest_headers(args.db, args.blobs, out)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
