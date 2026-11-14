#!/usr/bin/env python3
"""Build the viewer's exposure index (data/fields.json) from the single source of truth.

Sources
-------
  db      (default)  数据库工具/var/catalog.sqlite → table `frames`
  export             数据库工具/var/exports/fields.json   (DB 导出物)
  headers            legacy：解析 FITS 头缓存（该目录已删除，仅作历史兼容）

Usage
-----
    python3 tools/build_field_index.py                 # db → data/fields.json（已存在则拒绝）
    python3 tools/build_field_index.py --force         # 覆盖
    python3 tools/build_field_index.py --check         # 只校验，不写
    python3 tools/build_field_index.py --source export --out /tmp/fields.json --force

Row schema (10 columns, 与 DB export 一致):
    [obsid, detector, ra, dec, mjd, lat, lon, elon, elat, xposure]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
PROJECT = os.path.dirname(TOOL)

DB_PATH = os.path.join(PROJECT, "数据库工具", "var", "catalog.sqlite")
EXPORT_PATH = os.path.join(PROJECT, "数据库工具", "var", "exports", "fields.json")
HEADER_DIR = os.path.join(PROJECT, "app", "data", "header_cache")
DEFAULT_OUT = os.path.join(TOOL, "data", "fields.json")
KEYS = ["obsid", "detector", "ra", "dec", "mjd", "lat", "lon", "elon", "elat", "xposure"]

# 与历史文件保持一致的舍入，避免“重建后数值漂移”
ROUND = {"ra": 5, "dec": 5, "mjd": 6, "lat": 4, "lon": 4, "elon": 3, "elat": 3, "xposure": 1}


def rows_from_db(path: str) -> list[list]:
    if not os.path.exists(path):
        raise SystemExit(f"找不到数据库：{path}\n先运行 数据库工具/tools/import_all.sh")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.execute(
            "SELECT obs_id, detector, ra, dec, mjd, sgt_lat, sgt_lon, elon, elat, xposure "
            "FROM frames WHERE ra IS NOT NULL AND dec IS NOT NULL AND mjd IS NOT NULL "
            "ORDER BY frame_id"
        )
        rows = [list(r) for r in cur]
    finally:
        con.close()
    return normalize(rows)


def rows_from_export(path: str) -> list[list]:
    if not os.path.exists(path):
        raise SystemExit(f"找不到导出文件：{path}\n先运行 数据库工具/cli.py export fields")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    order = doc.get("keys") or KEYS
    idx = [order.index(k) for k in KEYS]
    rows = [[r[i] for i in idx] for r in doc.get("fields", [])]
    return normalize(rows)


def rows_from_headers(path: str) -> list[list]:
    import re

    if not os.path.isdir(path):
        raise SystemExit(
            f"header_cache 不存在：{path}\n"
            "该缓存是旧版取数器的临时目录，已从仓库移除；请改用 --source db 或 export。"
        )
    pats = {
        "obsid": (r"OBSID\s*=\s*'([^']*)'", str),
        "detector": (r"DETECTOR\s*=\s*([0-9]+)", int),
        "ra": (r"CRVAL1\s*=\s*([-+0-9.eE]+)", float),
        "dec": (r"CRVAL2\s*=\s*([-+0-9.eE]+)", float),
        "mjd": (r"MJD-OBS\s*=\s*([-+0-9.eE]+)", float),
        "lat": (r"SGT_LAT_MIDPT\s*=\s*([-+0-9.eE]+)", float),
        "lon": (r"SGT_LON_MIDPT\s*=\s*([-+0-9.eE]+)", float),
        "elon": (r"SPS_ELON\s*=\s*([-+0-9.eE]+)", float),
        "elat": (r"SPS_ELAT\s*=\s*([-+0-9.eE]+)", float),
        "xposure": (r"XPOSURE\s*=\s*([-+0-9.eE]+)", float),
    }
    rows, seen, bad = [], set(), 0
    for name in sorted(os.listdir(path)):
        if not name.endswith(".hdr"):
            continue
        raw = open(os.path.join(path, name), "rb").read().decode("latin-1", "replace")
        rec = {}
        for k, (pat, typ) in pats.items():
            m = re.search(pat, raw)
            if not m:
                bad += 1
                rec = None
                break
            rec[k] = typ(m.group(1))
        if not rec:
            continue
        key = f'{rec["obsid"]}/{rec["detector"]}'
        if key in seen:
            continue
        seen.add(key)
        rows.append([rec[k] for k in KEYS])
    print(f"  header 解析：{len(rows)} 行，{bad} 个文件缺字段", file=sys.stderr)
    rows.sort(key=lambda r: (r[4], r[1], r[0]))
    return normalize(rows)


def normalize(rows: list[list]) -> list[list]:
    out = []
    for r in rows:
        if len(r) != len(KEYS):
            raise SystemExit(f"行宽不是 {len(KEYS)}：{r!r}")
        obsid, det, ra, dec, mjd, lat, lon, elon, elat, xp = r
        out.append([
            str(obsid), int(det),
            round(float(ra), ROUND["ra"]), round(float(dec), ROUND["dec"]),
            round(float(mjd), ROUND["mjd"]),
            round(float(lat), ROUND["lat"]), round(float(lon), ROUND["lon"]),
            round(float(elon), ROUND["elon"]), round(float(elat), ROUND["elat"]),
            round(float(xp), ROUND["xposure"]),
        ])
    return out


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_current(path: str) -> list[list] | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("fields")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["db", "export", "headers"], default="db")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--db", default=DB_PATH, help="sqlite 路径（--source db）")
    ap.add_argument("--export", default=EXPORT_PATH, help="导出 JSON 路径（--source export）")
    ap.add_argument("--headers", default=HEADER_DIR, help="旧头缓存目录（--source headers）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的输出文件")
    ap.add_argument("--check", action="store_true", help="只与现有文件比对，不写")
    args = ap.parse_args()

    src_path = {"db": args.db, "export": args.export, "headers": args.headers}[args.source]
    print(f"build_field_index: source={args.source} ({src_path})")
    if args.source == "db":
        rows = rows_from_db(src_path)
    elif args.source == "export":
        rows = rows_from_export(src_path)
    else:
        rows = rows_from_headers(src_path)

    if not rows:
        raise SystemExit("没有解析到任何曝光行")
    n_obs = len({r[0] for r in rows})
    mjds = [r[4] for r in rows]
    print(f"  {len(rows)} 帧 / {n_obs} 次曝光 / MJD {min(mjds):.3f} → {max(mjds):.3f}")

    if args.check:
        cur = load_current(args.out)
        if cur is None:
            print(f"CHECK FAIL: {args.out} 不存在", file=sys.stderr)
            return 1
        if cur == rows:
            print(f"CHECK OK: 与 {args.out} 逐行一致")
            return 0
        print(f"CHECK FAIL: 与 {args.out} 不一致（当前 {len(cur)} 行）", file=sys.stderr)
        return 1

    if os.path.exists(args.out) and not args.force:
        raise SystemExit(f"{args.out} 已存在；加 --force 覆盖（或用 --check 只比对）")

    payload = {
        "generated_by": "视图工具/tools/build_field_index.py",
        "source": f"{args.source}:{os.path.relpath(src_path, PROJECT)}",
        "source_sha256": sha256_of(src_path) if os.path.isfile(src_path) else None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(rows),
        "exposures": n_obs,
        "keys": KEYS,
        "fields": rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  写入 {args.out}（{os.path.getsize(args.out) / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
