#!/usr/bin/env python3
"""导入 Gaia DR3 预设场到独立的 `var/star_catalog.sqlite`。

用法：

    # 真实 TAP（7 个预设场；网络失败自动回退到真实 Gaia 行 fixture）
    python3 tools/import_gaia.py --rebuild

    # 完全离线：只用提交在 tests/fixtures/ 的小样本
    python3 tools/import_gaia.py --rebuild --offline

    # 只导入一个场，或导出可提交的小样本
    python3 tools/import_gaia.py --fields barnard --rebuild
    python3 tools/import_gaia.py --export-sample tests/fixtures/gaia_dr3_sample.csv

设计边界：
- 不写 `var/catalog.sqlite`；
- 不提交 Gaia 全量；每个预设场只缓存 `--radius-deg`（默认 0.25°）内
  G <= `--mag-limit`（默认 20）的源；
- fixture 是真实 Gaia 行的子集，provenance 明确标 `fixture`；联网 TAP 标 `tap`；
  两者混用时先按场记录 provenance，release 标 `mixed`。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.catalog import angsep_deg  # noqa: E402
from db.irsa_gator import (  # noqa: E402
    CATALOGS as IRSA_CATALOGS,
    enrich_gaia_rows,
    fetch_field_photometry,
    photometry_summary,
)
from db.gaia_tap import (  # noqa: E402
    GAIA_ARCHIVE_URL,
    GAIA_DR3_CITATION,
    GAIA_DR3_LICENSE,
    GAIA_DR3_RELEASE,
    GAIA_DR3_VERSION,
    GAIA_EPOCH_MJD,
    GAIA_EPOCH_REF,
    GAIA_MAG_LIMIT,
    GAIA_TAP_SYNC,
    GaiaTAPError,
    adql_cone_query,
    fetch_gaia_cone,
    fixture_meta,
    preset_field_specs,
    read_fixture_rows,
    write_fixture,
)
from db.star_catalog import CatalogError, StarCatalog  # noqa: E402
from db.star_config import (  # noqa: E402
    DEFAULT_GAIA_FIXTURE,
    DEFAULT_STAR_DB,
    FIELD_CACHE_RADIUS_DEG,
)
from db.star_schema import init_star_db  # noqa: E402


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_fields(text: str | None) -> list[str] | None:
    if not text:
        return None
    return [x.strip() for x in text.split(",") if x.strip()]


def _write_csv(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _nearest_pair(rows: list[dict]) -> tuple[dict, dict] | None:
    """找样本里最近的一对源；用于 fixture 的 ambiguous 回归。"""
    best: tuple[float, dict, dict] | None = None
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            sep = angsep_deg(a["ra"], a["dec"], b["ra"], b["dec"]) * 3600.0
            if best is None or sep < best[0]:
                best = (sep, a, b)
    if best is None or best[0] > 8.0:
        return None
    return best[1], best[2]


def _fixture_photometry_stats(rows: list[dict]) -> dict:
    """fixture/离线模式：只统计 CSV 里已有的真实 2MASS/WISE 值。"""
    tm_rows = [r for r in rows if r.get("twomass_id")]
    wise_rows = [r for r in rows if r.get("wise_id")]
    tm_matched = [
        r for r in rows
        if r.get("twomass_id") and any(r.get(k) is not None for k in ("jmag", "hmag", "kmag"))
    ]
    wise_matched = [
        r for r in rows
        if r.get("wise_id") and any(r.get(k) is not None for k in ("w1", "w2"))
    ]
    return {
        "status": "fixture_sample",
        "source": "fixture rows only (Gaia real rows; 2MASS/WISE values where the fixture has them)",
        "source_url": "tests/fixtures/gaia_dr3_sample.csv",
        "license": {k: IRSA_CATALOGS[k]["license"] for k in ("2mass", "wise")},
        "2mass": {
            "rows": len(tm_rows),
            "matched": len(tm_matched),
            "j_available": sum(1 for r in rows if r.get("jmag") is not None),
            "h_available": sum(1 for r in rows if r.get("hmag") is not None),
            "k_available": sum(1 for r in rows if r.get("kmag") is not None),
        },
        "wise": {
            "rows": len(wise_rows),
            "matched": len(wise_matched),
            "w1_available": sum(1 for r in rows if r.get("w1") is not None),
            "w2_available": sum(1 for r in rows if r.get("w2") is not None),
        },
        "errors": {},
    }


def make_sample(sources: list[dict], *, per_field: int = 20) -> list[dict]:
    """从真实导入行里挑小而可复现的 fixture：近场心/最亮/高自行/近对。"""
    by_field: dict[str, list[dict]] = {}
    for s in sources:
        by_field.setdefault(str(s.get("field_id") or ""), []).append(s)
    chosen: dict[str, dict] = {}

    def add(row: dict) -> None:
        key = row["catalog_id"]
        chosen.setdefault(key, row)

    for fid, rows in sorted(by_field.items()):
        if not fid:
            continue
        center = next(
            (f for f in preset_field_specs([fid]) if f["field_id"] == fid), None
        )
        if center:
            near = sorted(
                rows,
                key=lambda r: angsep_deg(center["ra"], center["dec"], r["ra"], r["dec"]),
            )[:per_field]
        else:
            near = rows[:per_field]
        for r in near:
            add(r)
        for r in sorted(rows, key=lambda r: r.get("gmag") if r.get("gmag") is not None else 99)[:5]:
            add(r)
        for r in sorted(
            rows,
            key=lambda r: -(abs(r.get("pmra") or 0.0) + abs(r.get("pmdec") or 0.0)),
        )[:5]:
            add(r)
        pair = _nearest_pair(rows)
        if pair:
            add(pair[0])
            add(pair[1])
        # 至少保留几条有 2MASS/WISE 交叉证认/真实星等的行，供 API 列检查。
        for r in [r for r in rows if r.get("twomass_id") or r.get("wise_id")][:3]:
            add(r)
        for r in [
            r for r in rows
            if any(r.get(k) is not None for k in ("jmag", "hmag", "kmag", "w1", "w2"))
        ][:5]:
            add(r)
        # 每个场至少留一条，fixture 7 场齐全。
        add(rows[0])
    return list(chosen.values())


def _sample_meta(
    *,
    fields: list[dict],
    sources: list[dict],
    fixture_rows: int,
    mode: str,
    fetched_at: str,
    query_template: str,
    fallback: list[str],
) -> dict:
    counts = {}
    for s in sources:
        counts[str(s.get("field_id") or "")] = counts.get(str(s.get("field_id") or ""), 0) + 1
    return {
        "catalog": "gaia",
        "version": GAIA_DR3_VERSION,
        "release": GAIA_DR3_RELEASE,
        "epoch_ref": GAIA_EPOCH_REF,
        "epoch_mjd": GAIA_EPOCH_MJD,
        "license": GAIA_DR3_LICENSE,
        "citation": GAIA_DR3_CITATION,
        "source": "ESA Gaia Archive TAP cone search",
        "tap_url": GAIA_TAP_SYNC,
        "archive_url": GAIA_ARCHIVE_URL,
        "query_template": query_template,
        "fetched_at": fetched_at,
        "mode": mode,
        "usage": "tests_only",
        "complete": False,
        "note": (
            "这是真实 Gaia DR3 TAP 结果的小样本，不是合成坐标；"
            "field_id 是导入时按预设场加的，source_id/ra/dec/pm 等保留 Gaia 原值。"
            "样本不完整（仅用于离线测试/回归，不得当作演示星表、全天星表或全量 Gaia），"
            "不能据此声称 Gaia 全量或 OSI 许可。field_counts 是当时 7 个场各 0.25°、"
            "G<=20 的真实 TAP 行数，不是本 fixture 的行数。"
        ),
        "field_counts_note": (
            "field_counts 是 TAP 缓存场的完整计数；fixture 只含每场抽样后的行，不完整。"
        ),
        "n_fixture_rows": fixture_rows,
        "field_counts": counts,
        "fields": fields,
        "photometry": {
            "source": "IRSA Gator: 2MASS PSC fp_psc + AllWISE allwise_p3as_psd",
            "source_url": "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query",
            "license": {k: IRSA_CATALOGS[k]["license"] for k in ("2mass", "wise")},
            "note": (
                "fixture 的 J/H/K/W1/W2 是真实 Gator 值（若该行有交叉证认且目录有值）；"
                "null 表示无匹配/无质量值，不是 0。每场覆盖率见 fields[].photometry。"
            ),
            "fields": {
                f["field_id"]: f.get("photometry") for f in fields
            },
        },
        "fallback_fields": fallback,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入 Gaia DR3 星表缓存（独立于生产 catalog.sqlite）")
    parser.add_argument("--db", default=str(DEFAULT_STAR_DB), help="星表 SQLite 路径")
    parser.add_argument("--fields", default=None, help="逗号分隔的预设场；默认全部 7 个")
    parser.add_argument("--radius-deg", type=float, default=FIELD_CACHE_RADIUS_DEG)
    parser.add_argument("--mag-limit", type=float, default=GAIA_MAG_LIMIT)
    parser.add_argument("--fixture", default=str(DEFAULT_GAIA_FIXTURE), help="离线 fixture CSV")
    parser.add_argument("--offline", action="store_true", help="只用 fixture，不访问网络")
    parser.add_argument("--rebuild", action="store_true", help="删除并重建星表库")
    parser.add_argument("--cache-dir", default=None, help="可选：保存 TAP 原始 CSV 的目录")
    parser.add_argument("--no-photometry", action="store_true", help="跳过 IRSA Gator 2MASS/WISE 补充")
    parser.add_argument("--photometry-timeout", type=float, default=90.0)
    parser.add_argument("--photometry-retries", type=int, default=2)
    parser.add_argument("--export-sample", default=None, help="把真实导入行导出为可提交小样本")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    field_names = _parse_fields(args.fields)
    try:
        specs = preset_field_specs(field_names)
    except KeyError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    if not (0 < args.radius_deg <= 2.0):
        print("错误: --radius-deg 必须在 (0, 2]", file=sys.stderr)
        return 2
    if not (0 < args.mag_limit <= 25):
        print("错误: --mag-limit 必须在 (0, 25]", file=sys.stderr)
        return 2

    fixture_path = Path(args.fixture).expanduser().resolve()
    fixture_all: list[dict] = []
    if args.offline or args.export_sample or fixture_path.exists():
        try:
            fixture_all = read_fixture_rows(fixture_path)
        except (FileNotFoundError, GaiaTAPError) as exc:
            if args.offline:
                print(f"错误: 离线模式需要可读 fixture: {exc}", file=sys.stderr)
                return 2
            fixture_all = []
    fixture_by_field: dict[str, list[dict]] = {}
    for row in fixture_all:
        fixture_by_field.setdefault(str(row.get("field_id") or ""), []).append(row)

    field_meta: list[dict] = []
    sources: list[dict] = []
    fallback_fields: list[str] = []
    mode = "fixture" if args.offline else "tap"
    fetched_at = utcnow()

    for spec in specs:
        fid = spec["field_id"]
        rows: list[dict] = []
        provenance = "fixture" if args.offline else "tap"
        if not args.offline:
            try:
                rows, raw, query = fetch_gaia_cone(
                    float(spec["ra"]),
                    float(spec["dec"]),
                    args.radius_deg,
                    mag_limit=args.mag_limit,
                    timeout=args.timeout,
                    retries=args.retries,
                )
                for r in rows:
                    r["field_id"] = fid
                if args.cache_dir:
                    _write_csv(Path(args.cache_dir) / f"{fid}.csv", raw)
                print(
                    f"[tap] {fid}: {len(rows)} sources  ({args.radius_deg}°, G<={args.mag_limit:g})",
                    file=sys.stderr,
                )
            except GaiaTAPError as exc:
                fallback = fixture_by_field.get(fid, [])
                if not fallback:
                    print(
                        f"错误: {fid} TAP 失败且 fixture 没有该场数据: {exc}",
                        file=sys.stderr,
                    )
                    return 2
                rows = [dict(r) for r in fallback]
                provenance = "fixture"
                fallback_fields.append(fid)
                print(f"[fixture] {fid}: TAP 失败，回退到 {len(rows)} 行 fixture（{exc}）", file=sys.stderr)
        else:
            rows = [dict(r) for r in fixture_by_field.get(fid, [])]
            provenance = "fixture"
            if not rows:
                print(f"警告: fixture 没有 {fid} 场，跳过", file=sys.stderr)
                continue
            print(f"[fixture] {fid}: {len(rows)} sources", file=sys.stderr)

        photometry = None
        if provenance == "tap" and not args.no_photometry:
            phot = fetch_field_photometry(
                float(spec["ra"]),
                float(spec["dec"]),
                args.radius_deg,
                timeout=args.photometry_timeout,
                retries=args.photometry_retries,
            )
            stats = enrich_gaia_rows(rows, phot)
            photometry = photometry_summary(phot, stats)
            if phot.get("errors"):
                print(
                    f"[photometry] {fid}: 部分失败 {phot['errors']}；缺失列保持 null",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[photometry] {fid}: 2MASS matched={stats['2mass']['matched']}, "
                    f"WISE matched={stats['wise']['matched']}",
                    file=sys.stderr,
                )
        elif args.no_photometry and provenance == "tap":
            photometry = {
                "status": "disabled",
                "source": "IRSA Gator disabled by --no-photometry",
                "2mass": None,
                "wise": None,
                "errors": {},
            }
        else:
            photometry = _fixture_photometry_stats(rows)

        for r in rows:
            r["field_id"] = fid
            r["provenance"] = provenance
            sources.append(r)
        field_meta.append(
            {
                "field_id": fid,
                "field_name": spec.get("name"),
                "ra": float(spec["ra"]),
                "dec": float(spec["dec"]),
                "radius_deg": args.radius_deg,
                "n_sources": len(rows),
                "provenance": provenance,
                "photometry": photometry,
            }
        )

    if not sources:
        print("错误: 没有任何星表源可导入", file=sys.stderr)
        return 2
    if any(f["provenance"] != "tap" for f in field_meta):
        mode = "fixture" if all(f["provenance"] == "fixture" for f in field_meta) else "mixed"
    query_template = adql_cone_query(0.0, 0.0, args.radius_deg, mag_limit=args.mag_limit)
    notes = (
        f"preset fields={','.join(f['field_id'] for f in field_meta)}; "
        f"cache_radius={args.radius_deg:g} deg; mag_limit={args.mag_limit:g}; "
        f"mode={mode}; fixture={fixture_path if args.offline or fallback_fields else 'none'}; "
        f"imported_from={GAIA_TAP_SYNC}; license={GAIA_DR3_LICENSE}; "
        f"2mass_wise_source={IRSA_CATALOGS['2mass']['source_url']} + {IRSA_CATALOGS['wise']['source_url']}. "
        f"Not a full-sky catalog; per-field cache only."
    )
    print(
        f"汇总: {len(field_meta)} fields / {len(sources)} sources / mode={mode}"
        + (f" / fallback={fallback_fields}" if fallback_fields else ""),
        file=sys.stderr,
    )

    if args.export_sample:
        sample = make_sample(sources)
        meta = _sample_meta(
            fields=field_meta,
            sources=sources,
            fixture_rows=len(sample),
            mode=mode,
            fetched_at=fetched_at,
            query_template=query_template,
            fallback=fallback_fields,
        )
        if args.dry_run:
            print(f"[dry-run] 将写样本 {len(sample)} 行 -> {args.export_sample}", file=sys.stderr)
        else:
            out = write_fixture(args.export_sample, sample, meta=meta)
            print(f"写出 fixture: {out} ({len(sample)} rows)", file=sys.stderr)
        if not args.rebuild and not args.dry_run:
            # 导出样本时仍允许继续建库（默认行为）。
            pass

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "mode": mode,
                    "fields": field_meta,
                    "n_sources": len(sources),
                    "db": args.db,
                    "fallback_fields": fallback_fields,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    db_path = Path(args.db).expanduser().resolve()
    if db_path.exists() and not args.rebuild:
        # 在现有库上替换 release；DB schema 缺失时补建。
        cat = StarCatalog(db_path, readonly=False)
        try:
            cat.init()
            release = cat.import_release(
                catalog="gaia",
                version=GAIA_DR3_VERSION,
                epoch_ref=GAIA_EPOCH_REF,
                epoch_mjd=GAIA_EPOCH_MJD,
                license=GAIA_DR3_LICENSE,
                citation=GAIA_DR3_CITATION,
                source_url=GAIA_TAP_SYNC,
                provenance=mode,
                sources=sources,
                fields=field_meta,
                release_key=GAIA_DR3_RELEASE,
                cache_radius_deg=args.radius_deg,
                mag_limit=args.mag_limit,
                notes=notes,
                replace=True,
                run_mode=mode,
            )
        finally:
            cat.close()
    else:
        init_star_db(db_path, replace=args.rebuild)
        cat = StarCatalog(db_path, readonly=False)
        try:
            release = cat.import_release(
                catalog="gaia",
                version=GAIA_DR3_VERSION,
                epoch_ref=GAIA_EPOCH_REF,
                epoch_mjd=GAIA_EPOCH_MJD,
                license=GAIA_DR3_LICENSE,
                citation=GAIA_DR3_CITATION,
                source_url=GAIA_TAP_SYNC,
                provenance=mode,
                sources=sources,
                fields=field_meta,
                release_key=GAIA_DR3_RELEASE,
                cache_radius_deg=args.radius_deg,
                mag_limit=args.mag_limit,
                notes=notes,
                replace=False,
                run_mode=mode,
            )
        finally:
            cat.close()

    print(
        json.dumps(
            {
                "db": str(db_path),
                "release": release["release"],
                "version": release["version"],
                "provenance": release["provenance"],
                "n_sources": release["n_sources_actual"],
                "n_fields": release["n_fields"],
                "license": release["license"],
                "epoch_ref": release["epoch_ref"],
                "field_provenance": {f["field_id"]: f["provenance"] for f in field_meta},
                "photometry": {
                    f["field_id"]: {
                        "status": (f.get("photometry") or {}).get("status"),
                        "2mass_matched": ((f.get("photometry") or {}).get("2mass") or {}).get("matched"),
                        "wise_matched": ((f.get("photometry") or {}).get("wise") or {}).get("matched"),
                    }
                    for f in field_meta
                },
                "fallback_fields": fallback_fields,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CatalogError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(2)
