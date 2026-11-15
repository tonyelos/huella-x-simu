#!/usr/bin/env python3
"""用 IRSA Gator 导入 2MASS / AllWISE 实际场目录（不伪造）。

    python3 tools/import_irsa_catalogs.py --db var/star_catalog.sqlite --rebuild
    python3 tools/import_irsa_catalogs.py --db /tmp/star_catalog.sqlite

- 对 7 个预设场各做一次 0.25° Gator cone；
- 建 `2mass` / `wise` release（version=irsa-gator），坐标用 Gator 原始 RA/Dec，
  epoch_ref=J2000.0，不写 PM；
- 2MASS 源保存 J/H/K + quality，WISE 源保存 W1/W2 + quality；
- `--rebuild` 只删这两个实际目录 release，不碰 gaia，也不删 derived release；
- 某场某目录失败时 skip 并明确写 errors，不写伪数据；不存在“全 7 场完整”的假声明。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.irsa_gator import CATALOGS, fetch_field_photometry  # noqa: E402
from db.star_catalog import StarCatalog  # noqa: E402
from db.star_config import DEFAULT_STAR_DB, FIELD_CACHE_RADIUS_DEG, PRESET_FIELDS  # noqa: E402

VERSION = "irsa-gator"
EPOCH_REF = "J2000.0"
EPOCH_MJD = 51544.5

CATALOG_META = {
    "2mass": {
        "license": "Public domain (2MASS/IPAC/Caltech/UMass)",
        "citation": "2MASS PSC (Skrutskie et al. 2006, AJ 131, 1163)",
        "source_url": "https://irsa.ipac.caltech.edu/Missions/2mass.html",
    },
    "wise": {
        "license": "Public domain (NASA/JPL-Caltech)",
        "citation": (
            "AllWISE Source Catalog (Wright et al. 2010, AJ 140, 1868; "
            "Cutri et al. 2021, VizieR II/328)"
        ),
        "source_url": "https://irsa.ipac.caltech.edu/Missions/wise.html",
    },
}


def _fields_arg(text: str | None) -> list[str]:
    if not text:
        return list(PRESET_FIELDS)
    return [x.strip() for x in text.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IRSA Gator 2MASS/WISE 实际场目录导入")
    parser.add_argument("--db", default=str(DEFAULT_STAR_DB))
    parser.add_argument("--fields", default=None, help="逗号分隔预设场；默认 7 个")
    parser.add_argument("--radius-deg", type=float, default=FIELD_CACHE_RADIUS_DEG)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--rebuild", action="store_true", help="只删除并重建 2mass/wise 实际 release")
    args = parser.parse_args(argv)

    if not (0.0 < args.radius_deg <= 1.0):
        print("错误: --radius-deg 必须在 (0, 1]", file=sys.stderr)
        return 2
    fields = _fields_arg(args.fields)
    unknown = [f for f in fields if f not in PRESET_FIELDS]
    if unknown:
        print(f"错误: 未知预设场: {unknown}", file=sys.stderr)
        return 2

    db = Path(args.db).expanduser().resolve()
    report: dict = {
        "db": str(db),
        "radius_deg": args.radius_deg,
        "catalogs": {
            c: {
                "release": f"{c}-{VERSION}",
                "version": VERSION,
                "epoch_ref": EPOCH_REF,
                "epoch_mjd": EPOCH_MJD,
                "fields": {},
                "errors": {},
            }
            for c in ("2mass", "wise")
        },
    }

    with StarCatalog(db, readonly=False) as cat:
        if args.rebuild:
            for c in ("2mass", "wise"):
                cat.delete_release(c, VERSION)
        for fid in fields:
            spec = PRESET_FIELDS[fid]
            phot = fetch_field_photometry(
                float(spec["ra"]),
                float(spec["dec"]),
                args.radius_deg,
                timeout=args.timeout,
                retries=args.retries,
            )
            for catalog in ("2mass", "wise"):
                error = (phot.get("errors") or {}).get(catalog)
                if error:
                    report["catalogs"][catalog]["errors"][fid] = error
                    continue
                rows = (phot.get("catalogs") or {}).get(catalog, [])
                sources = []
                for r in rows:
                    if r.get("ra") is None or r.get("dec") is None:
                        continue
                    src = {
                        "catalog_id": r["catalog_id"],
                        "ra": r["ra"],
                        "dec": r["dec"],
                        "quality": r.get("quality"),
                        "field_id": fid,
                        "provenance": "irsa_gator",
                        "epoch_ref": EPOCH_MJD,
                    }
                    if catalog == "2mass":
                        src.update(
                            {
                                "jmag": r.get("jmag"),
                                "hmag": r.get("hmag"),
                                "kmag": r.get("kmag"),
                            }
                        )
                    else:
                        src.update({"w1": r.get("w1"), "w2": r.get("w2")})
                    sources.append(src)
                meta = CATALOG_META[catalog]
                result = cat.upsert_field(
                    catalog=catalog,
                    version=VERSION,
                    field_id=fid,
                    field_name=spec.get("name"),
                    ra=float(spec["ra"]),
                    dec=float(spec["dec"]),
                    radius_deg=args.radius_deg,
                    mag_limit=None,
                    sources=sources,
                    provenance="irsa_gator",
                    license=meta["license"],
                    citation=meta["citation"],
                    source_url=meta["source_url"],
                    epoch_ref=EPOCH_REF,
                    epoch_mjd=EPOCH_MJD,
                    photometry={
                        "status": "ok",
                        "source": "IRSA Gator " + CATALOGS[catalog]["label"],
                        "n_sources": len(sources),
                        "note": "actual Gator field catalog; coords are Gator RA/Dec at J2000.0",
                    },
                )
                report["catalogs"][catalog]["fields"][fid] = {
                    "n_sources": result["n_sources"],
                    "n_new": result["n_new"],
                }

        for catalog in ("2mass", "wise"):
            try:
                rel = cat.get_release(catalog, VERSION)
            except Exception as exc:  # noqa: BLE001
                report["catalogs"][catalog]["error"] = str(exc)
                continue
            report["catalogs"][catalog].update(
                {
                    "n_sources": rel["n_sources_actual"],
                    "n_fields": rel["n_fields"],
                    "provenance": rel["provenance"],
                    "license": rel["license"],
                    "citation": rel["citation"],
                    "source_url": rel["source_url"],
                }
            )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
