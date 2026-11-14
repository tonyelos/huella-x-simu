#!/usr/bin/env python3
"""Generate the viewer's target table and curated-epoch index from the project's single sources.

Single sources
--------------
  targets      app/data/targets.json                (MoveScope 自己读的那一份)
  epochs       数据库工具/var/exports/epoch_catalog.json（DB 导出的策展历元）

Outputs
-------
  data/targets.json       带 generated_by / source / generated_at 的副本
  data/epoch_index.json   {targets: {id: [{obsid, mjd, sep_deg, frames}, ...]}}

Usage
-----
    python3 tools/sync_targets.py            # 重新生成
    python3 tools/sync_targets.py --check    # 只比对，不一致时退出码 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
PROJECT = os.path.dirname(TOOL)

TARGETS_SRC = os.path.join(PROJECT, "app", "data", "targets.json")
TARGETS_DB_EXPORT = os.path.join(PROJECT, "数据库工具", "var", "exports", "targets.json")
EPOCH_SRC = os.path.join(PROJECT, "数据库工具", "var", "exports", "epoch_catalog.json")
EPOCH_FALLBACK = os.path.join(PROJECT, "app", "data", "epoch_catalog.json")

OUT_TARGETS = os.path.join(TOOL, "data", "targets.json")
OUT_EPOCHS = os.path.join(TOOL, "data", "epoch_index.json")

REQUIRED = ("id", "name", "ra", "dec", "official", "expected", "truth", "coord_unc_arcsec")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def load_targets() -> tuple[list[dict], str]:
    if not os.path.exists(TARGETS_SRC):
        raise SystemExit(f"找不到目标源：{TARGETS_SRC}")
    with open(TARGETS_SRC, encoding="utf-8") as fh:
        doc = json.load(fh)
    targets = doc.get("targets") or []
    if not targets:
        raise SystemExit(f"{TARGETS_SRC} 里没有 targets")
    for t in targets:
        missing = [k for k in REQUIRED if k not in t]
        if missing:
            raise SystemExit(f"{t.get('id', '?')} 缺字段：{', '.join(missing)}")
    return targets, TARGETS_SRC


def load_epochs() -> tuple[dict, str]:
    src = EPOCH_SRC if os.path.exists(EPOCH_SRC) else EPOCH_FALLBACK
    if not os.path.exists(src):
        print(f"警告：找不到历元目录（{EPOCH_SRC}），epoch_index 将为空", file=sys.stderr)
        return {}, ""
    with open(src, encoding="utf-8") as fh:
        doc = json.load(fh)
    raw = doc.get("epochs") or {}
    out: dict[str, list[dict]] = {}
    for tid, rows in raw.items():
        by_obs: dict[str, dict] = {}
        for r in rows:
            obs = r.get("obsid") or r.get("obs_id")
            if not obs:
                continue
            cur = by_obs.get(obs)
            if cur is None:
                by_obs[obs] = {
                    "obsid": obs,
                    "mjd": round(float(r["mjd"]), 6),
                    "sep_deg": round(float(r.get("sep_deg", 0.0)), 4),
                    "frames": 1,
                }
            else:
                cur["frames"] += 1
                cur["sep_deg"] = min(cur["sep_deg"], round(float(r.get("sep_deg", 0.0)), 4))
                cur["mjd"] = min(cur["mjd"], round(float(r["mjd"]), 6))
        out[tid] = sorted(by_obs.values(), key=lambda e: e["mjd"])
    return out, src


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    targets, tsrc = load_targets()
    epochs, esrc = load_epochs()

    # 与 DB 导出交叉核对（存在时）
    if os.path.exists(TARGETS_DB_EXPORT):
        with open(TARGETS_DB_EXPORT, encoding="utf-8") as fh:
            db_targets = json.load(fh).get("targets") or []
        by_id = {t["id"]: t for t in db_targets}
        for t in targets:
            d = by_id.get(t["id"])
            if not d:
                continue
            for k in ("ra", "dec", "official", "expected", "coord_unc_arcsec"):
                if d.get(k) != t.get(k):
                    print(f"警告：{t['id']}.{k} 与 DB 导出不同（{t.get(k)} vs {d.get(k)}）", file=sys.stderr)

    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    t_doc = {
        "schema": "movescope/targets/v1",
        "generated_by": "视图工具/tools/sync_targets.py",
        "source": os.path.relpath(tsrc, PROJECT),
        "source_sha256": sha256_of(tsrc),
        "generated_at": now,
        "count": len(targets),
        "targets": targets,
    }
    e_doc = {
        "generated_by": "视图工具/tools/sync_targets.py",
        "source": os.path.relpath(esrc, PROJECT) if esrc else None,
        "generated_at": now,
        "counts": {k: len(v) for k, v in epochs.items()},
        "targets": epochs,
    }

    if args.check:
        ok = True
        for path, doc, label in ((OUT_TARGETS, t_doc, "targets"), (OUT_EPOCHS, e_doc, "epoch_index")):
            if not os.path.exists(path):
                print(f"CHECK FAIL: {path} 不存在", file=sys.stderr)
                ok = False
                continue
            with open(path, encoding="utf-8") as fh:
                cur = json.load(fh)
            a = cur.get("targets")
            b = doc.get("targets")
            if a != b:
                print(f"CHECK FAIL: {label} 与源不一致", file=sys.stderr)
                ok = False
        if ok:
            print(f"CHECK OK: targets（{len(targets)} 个）与 epoch_index（{sum(len(v) for v in epochs.values())} 个历元）")
        return 0 if ok else 1

    os.makedirs(os.path.dirname(OUT_TARGETS), exist_ok=True)
    with open(OUT_TARGETS, "w", encoding="utf-8") as fh:
        json.dump(t_doc, fh, ensure_ascii=False, indent=1)
    with open(OUT_EPOCHS, "w", encoding="utf-8") as fh:
        json.dump(e_doc, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"写入 {OUT_TARGETS}（{len(targets)} 个目标）")
    print(f"写入 {OUT_EPOCHS}（{sum(len(v) for v in epochs.values())} 个策展历元）")
    for k, v in e_doc["counts"].items():
        print(f"  {k:10s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
