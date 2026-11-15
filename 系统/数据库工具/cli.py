#!/usr/bin/env python3
"""数据库工具统一命令行入口。

最常用：

    python3 cli.py init
    python3 cli.py ingest all                 # 从 ../app 与 ../视图工具 导入
    python3 cli.py stats
    python3 cli.py query cone --ra 269.452 --dec 4.693 --radius 0.2
    python3 cli.py serve --port 8797

所有子命令都支持 --db / --blobs 覆盖默认路径。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from db.blobs import BlobStore
from db.catalog import Catalog
from db.config import COVERAGE_RADIUS_DEG, DEFAULT_BLOBS, DEFAULT_DB, DEFAULT_EXPORTS, TOOL_ROOT
from db.connection import connect, db_disk_bytes
from db.export import EXPORTERS, export_all, export_one, write_json
from db.ingest import Ingestor, init_db
from db.server import serve

WORKSPACE = TOOL_ROOT.parent


def _ensure_db(db_path: str, blob_root: str) -> Path:
    p = Path(db_path).expanduser().resolve()
    if not p.exists():
        print(f"数据库不存在，先初始化: {p}", file=sys.stderr)
        init_db(p, blob_root)
    return p


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _add_common(p: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    def default(value):
        return argparse.SUPPRESS if suppress else value

    p.add_argument("--db", default=default(str(DEFAULT_DB)), help="SQLite 数据库路径")
    p.add_argument("--blobs", default=default(str(DEFAULT_BLOBS)), help="Blob 仓库目录")
    p.add_argument("--quiet", action="store_true", default=default(False), help="少打印进度")


def cmd_init(args) -> int:
    p = init_db(args.db, args.blobs)
    print(f"已初始化: {p}")
    print(f"Blob 仓库: {Path(args.blobs).expanduser().resolve()}")
    return 0


def cmd_ingest(args) -> int:
    _ensure_db(args.db, args.blobs)
    with Ingestor(
        args.db, args.blobs, store_blobs=not getattr(args, "no_blobs", False), quiet=args.quiet
    ) as ing:
        if args.source == "all":
            root = Path(args.root).expanduser().resolve()
            out = ing.import_all(
                root,
                skip_headers=args.skip_headers,
                with_fields=not args.no_fields,
                with_samples=args.with_samples,
                limit=args.limit,
            )
        elif args.source == "headers":
            out = ing.ingest_headers(
                args.dir or WORKSPACE / "app" / "data" / "header_cache",
                limit=args.limit,
                pattern=args.pattern,
            )
        elif args.source == "weeks":
            out = ing.ingest_week_index(args.file or WORKSPACE / "app" / "data" / "week_index.json")
        elif args.source == "targets":
            out = ing.ingest_targets(args.file or WORKSPACE / "app" / "data" / "targets.json")
        elif args.source == "epochs":
            out = ing.ingest_epoch_catalog(args.file or WORKSPACE / "app" / "data" / "epoch_catalog.json")
        elif args.source == "cutouts":
            out = ing.ingest_cutouts(
                args.dir or WORKSPACE / "app" / "data" / "epochcache", limit=args.limit
            )
        elif args.source == "fields":
            out = ing.ingest_fields_json(args.file or WORKSPACE / "视图工具" / "data" / "fields.json")
        elif args.source == "samples":
            out = ing.ingest_cutouts(args.dir or WORKSPACE / "参考 资料" / "muestras")
        else:
            raise SystemExit(f"未知来源: {args.source}")
        _print(out)
    return 0


def _catalog(args) -> Catalog:
    _ensure_db(args.db, args.blobs)
    return Catalog(args.db, readonly=True, blob_root=args.blobs)


def cmd_query(args) -> int:
    with _catalog(args) as cat:
        if args.q == "cone":
            out = cat.cone(
                args.ra,
                args.dec,
                args.radius,
                limit=args.limit,
                offset=args.offset,
                order=args.order,
                detector=args.detector,
                release=args.release,
                mjd_min=args.mjd_min,
                mjd_max=args.mjd_max,
                size_deg=args.size,
                include_cutouts=args.cutouts,
            )
        elif args.q == "coverage":
            out = cat.coverage(
                args.ra, args.dec, args.radius,
                mjd_min=args.mjd_min, mjd_max=args.mjd_max,
            )
        elif args.q == "epochs":
            radius = args.radius if args.radius is not None else (
                COVERAGE_RADIUS_DEG if args.v1 else 0.2
            )
            if args.v1:
                # 与 HTTP `/api/v1/epochs` 完全同一条代码路径（含去重、目标感知、时间窗）。
                out = cat.epochs_v1(
                    target_id=args.target,
                    ra=args.ra,
                    dec=args.dec,
                    fov=args.fov,
                    radius=radius,
                    limit=args.limit,
                    mjd_min=args.mjd_min,
                    mjd_max=args.mjd_max,
                )
            elif args.target:
                out = {"target_id": args.target, "epochs": cat.epochs_for_target(args.target)}
            elif args.ra is not None and args.dec is not None:
                out = {
                    "epochs": cat.cone(
                        args.ra, args.dec, radius, limit=args.limit, order="mjd",
                        mjd_min=args.mjd_min, mjd_max=args.mjd_max,
                    )["frames"]
                }
            else:
                raise SystemExit("query epochs 需要 --target 或 --ra/--dec")
        elif args.q == "target":
            out = cat.target(args.target)
            if out is None:
                raise SystemExit(f"目标不存在: {args.target}")
        elif args.q == "frame":
            out = cat.frame(args.obs_id, args.detector)
            if out is None:
                raise SystemExit(f"帧不存在: {args.obs_id} D{args.detector}")
        elif args.q == "weeks":
            out = {"weeks": cat.weeks(args.release)}
        elif args.q == "cutouts":
            out = {"cutouts": cat.cutouts(obs_id=args.obs_id, frame_id=args.frame_id)}
        else:
            raise SystemExit(f"未知查询: {args.q}")
        _print(out)
    return 0


def cmd_stats(args) -> int:
    with _catalog(args) as cat:
        _print(cat.stats())
    return 0


def cmd_verify(args) -> int:
    with _catalog(args) as cat:
        out = cat.integrity_check(deep=args.deep)
        _print(out)
        bad = out.get("quick_check") != "ok" or out.get("foreign_key_violations", 0) > 0
        bad = bad or out.get("missing_blobs", 0) > 0 or out.get("corrupt_blobs", 0) > 0
        return 1 if bad else 0


def cmd_vacuum(args) -> int:
    path = _ensure_db(args.db, args.blobs)
    before = db_disk_bytes(path)
    conn = connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()
    after = db_disk_bytes(path)
    _print({"db": str(path), "bytes_before": before, "bytes_after": after, "saved": before - after})
    return 0


def cmd_gc(args) -> int:
    path = _ensure_db(args.db, args.blobs)
    conn = connect(path)
    try:
        store = BlobStore(args.blobs)
        out = store.gc(conn, dry_run=args.dry_run)
        _print(out)
    finally:
        conn.close()
    return 0


def cmd_export(args) -> int:
    with _catalog(args) as cat:
        out = Path(args.out) if args.out else DEFAULT_EXPORTS
        if args.what == "all":
            result = export_all(cat.conn, out)
        else:
            filename = {
                "fields": "fields.json",
                "epochs": "epoch_catalog.json",
                "targets": "targets.json",
                "weeks": "week_index.json",
            }[args.what]
            result = str(export_one(cat.conn, args.what, out / filename))
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_backup(args) -> int:
    src_path = _ensure_db(args.db, args.blobs)
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    src = connect(src_path)
    dst = sqlite3.connect(str(out))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _print({"source": str(src_path), "backup": str(out), "bytes": out.stat().st_size})
    return 0


def cmd_serve(args) -> int:
    serve(args.db, host=args.host, port=args.port, blob_root=args.blobs)
    return 0


def cmd_bench(args) -> int:
    from db.bench import run_bench

    with _catalog(args) as cat:
        out = run_bench(cat, runs=args.runs, out_path=args.out)
        _print(out)
    return 0


# ---------------------------------------------------------------------- 参数


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="数据库工具",
        description="SPHEREx 元数据目录 + 内容寻址 Blob 仓库（MoveScope / 视图工具共用数据层）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="初始化数据库与 Blob 目录")
    _add_common(s)
    s.set_defaults(func=cmd_init)

    ing = sub.add_parser("ingest", help="导入数据")
    _add_common(ing)
    isub = ing.add_subparsers(dest="source", required=True)
    allp = isub.add_parser("all", help="一键导入 app/ 与 视图工具/ 的现有数据")
    _add_common(allp, suppress=True)
    allp.add_argument("--root", default=str(WORKSPACE), help="工作区根目录")
    allp.add_argument("--limit", type=int, default=None)
    allp.add_argument("--skip-headers", action="store_true")
    allp.add_argument("--no-fields", action="store_true")
    allp.add_argument("--with-samples", action="store_true")
    allp.add_argument("--no-blobs", action="store_true")
    allp.set_defaults(func=cmd_ingest)
    for name, help_text in (
        ("headers", "导入 FITS 头目录"),
        ("weeks", "导入 week_index.json"),
        ("targets", "导入 targets.json"),
        ("epochs", "导入 epoch_catalog.json"),
        ("cutouts", "导入 NPZ/FITS 切割缓存"),
        ("fields", "导入视图工具的 fields.json"),
        ("samples", "导入参考样本 FITS"),
    ):
        sp = isub.add_parser(name, help=help_text)
        _add_common(sp, suppress=True)
        sp.add_argument("--limit", type=int, default=None)
        sp.add_argument("--no-blobs", action="store_true")
        if name in ("headers", "cutouts", "samples"):
            sp.add_argument("--dir", default=None)
        if name == "headers":
            sp.add_argument("--pattern", default="*.hdr")
        else:
            sp.add_argument("--file", default=None)
        sp.set_defaults(func=cmd_ingest)

    q = sub.add_parser("query", help="查询")
    _add_common(q)
    qsub = q.add_subparsers(dest="q", required=True)
    cone = qsub.add_parser("cone", help="锥搜索")
    _add_common(cone, suppress=True)
    cone.add_argument("--ra", type=float, required=True)
    cone.add_argument("--dec", type=float, required=True)
    cone.add_argument("--radius", type=float, default=0.2)
    cone.add_argument("--limit", type=int, default=50)
    cone.add_argument("--offset", type=int, default=0)
    cone.add_argument("--order", choices=["sep", "mjd"], default="sep")
    cone.add_argument("--detector", type=int, default=None)
    cone.add_argument("--release", default=None)
    cone.add_argument("--mjd-min", type=float, default=None)
    cone.add_argument("--mjd-max", type=float, default=None)
    cone.add_argument("--size", type=float, default=0.2)
    cone.add_argument("--cutouts", action="store_true")
    cone.set_defaults(func=cmd_query)
    cov = qsub.add_parser("coverage", help="覆盖机会 C0–C3（readiness 已 deprecated）")
    _add_common(cov, suppress=True)
    cov.add_argument("--ra", type=float, required=True)
    cov.add_argument("--dec", type=float, required=True)
    cov.add_argument("--radius", type=float, default=COVERAGE_RADIUS_DEG)
    cov.add_argument("--mjd-min", type=float, default=None, help="可选 MJD 窗口下界（含）")
    cov.add_argument("--mjd-max", type=float, default=None, help="可选 MJD 窗口上界（含）")
    cov.set_defaults(func=cmd_query)
    ep = qsub.add_parser("epochs", help="历元；--v1 走契约（两入口同形）")
    _add_common(ep, suppress=True)
    ep.add_argument("--target", default=None)
    ep.add_argument("--ra", type=float, default=None)
    ep.add_argument("--dec", type=float, default=None)
    ep.add_argument("--radius", type=float, default=None, help="覆盖半径；--v1 默认 2.47，旧形状默认 0.2")
    ep.add_argument("--fov", type=float, default=0.25, help="仅 --v1：slice_url 的 size")
    ep.add_argument("--mjd-min", type=float, default=None, help="可选 MJD 窗口下界（含）")
    ep.add_argument("--mjd-max", type=float, default=None, help="可选 MJD 窗口上界（含）")
    ep.add_argument("--limit", type=int, default=500)
    ep.add_argument("--v1", action="store_true", help="输出契约 v1 响应（去重 + 目标感知）")
    ep.set_defaults(func=cmd_query)
    tg = qsub.add_parser("target", help="单目标")
    _add_common(tg, suppress=True)
    tg.add_argument("target")
    tg.set_defaults(func=cmd_query)
    fr = qsub.add_parser("frame", help="单帧")
    _add_common(fr, suppress=True)
    fr.add_argument("obs_id")
    fr.add_argument("detector", type=int)
    fr.set_defaults(func=cmd_query)
    wk = qsub.add_parser("weeks", help="周列表")
    _add_common(wk, suppress=True)
    wk.add_argument("--release", default=None)
    wk.set_defaults(func=cmd_query)
    cu = qsub.add_parser("cutouts", help="切割产品")
    _add_common(cu, suppress=True)
    cu.add_argument("--obs-id", default=None)
    cu.add_argument("--frame-id", type=int, default=None)
    cu.set_defaults(func=cmd_query)

    for name, fn in (("stats", cmd_stats), ("verify", cmd_verify), ("vacuum", cmd_vacuum), ("gc", cmd_gc)):
        sp = sub.add_parser(name, help=name)
        _add_common(sp)
        if name == "verify":
            sp.add_argument("--deep", action="store_true")
        if name == "gc":
            sp.add_argument("--dry-run", action="store_true")
        sp.set_defaults(func=fn)

    ex = sub.add_parser("export", help="导出兼容 JSON")
    _add_common(ex)
    ex.add_argument("what", choices=list(EXPORTERS) + ["all"])
    ex.add_argument("--out", default=None, help="输出目录（默认 var/exports）")
    ex.set_defaults(func=cmd_export)

    bk = sub.add_parser("backup", help="数据库一致性快照（不含 Blob 文件）")
    _add_common(bk)
    bk.add_argument("--out", required=True, help="输出 .sqlite 路径")
    bk.set_defaults(func=cmd_backup)

    sv = sub.add_parser("serve", help="启动只读 HTTP API")
    _add_common(sv)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8797)
    sv.set_defaults(func=cmd_serve)

    b = sub.add_parser("bench", help="查询性能基准")
    _add_common(b)
    b.add_argument("--runs", type=int, default=200)
    b.add_argument("--out", default=str(TOOL_ROOT / "benchmarks" / "report.json"))
    b.set_defaults(func=cmd_bench)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BrokenPipeError:
        return 0
    except FileNotFoundError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
