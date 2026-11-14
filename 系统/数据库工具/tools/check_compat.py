#!/usr/bin/env python3
"""检查导出的兼容 JSON 是否仍与两个项目的现有文件一致。

    python3 tools/check_compat.py --root .. --exports var/exports

检查项：
- fields.json：5,857 行数组逐行一致（顺序也一致）
- epoch_catalog.json：每个目标、每条历元一致
- targets.json：字段一致（包括 extra_json 保留的 coord_unc_arcsec）
- week_index.json：release/week/version/first_key/obsid/日期/黄道坐标一致；
  detector 允许 int/str 差异，mjd 允许 1e-6 差异
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--exports", default=str(Path(__file__).resolve().parent.parent / "var" / "exports"))
    args = ap.parse_args()
    root = Path(args.root).resolve()
    exports = Path(args.exports).resolve()
    problems: list[str] = []

    # fields
    src = root / "视图工具" / "data" / "fields.json"
    if src.exists() and (exports / "fields.json").exists():
        a, b = load(src)["fields"], load(exports / "fields.json")["fields"]
        if a != b:
            problems.append(f"fields.json 不一致: {len(a)} vs {len(b)} 行")
        else:
            print(f"fields.json      OK  ({len(a)} 行)")
    else:
        problems.append("fields.json 缺失")

    # epochs
    src = root / "app" / "data" / "epoch_catalog.json"
    if src.exists() and (exports / "epoch_catalog.json").exists():
        a, b = load(src)["epochs"], load(exports / "epoch_catalog.json")["epochs"]
        if a != b:
            problems.append("epoch_catalog.json 不一致")
        else:
            print(f"epoch_catalog    OK  ({sum(len(v) for v in a.values())} 条)")
    else:
        problems.append("epoch_catalog.json 缺失")

    # targets
    src = root / "app" / "data" / "targets.json"
    if src.exists() and (exports / "targets.json").exists():
        a = {t["id"]: t for t in load(src)["targets"]}
        b = {t["id"]: t for t in load(exports / "targets.json")["targets"]}
        if a != b:
            problems.append("targets.json 不一致")
        else:
            print(f"targets.json     OK  ({len(a)} 个)")
    else:
        problems.append("targets.json 缺失")

    # weeks（宽松比较：detector 类型 + mjd 精度）
    src = root / "app" / "data" / "week_index.json"
    if src.exists() and (exports / "week_index.json").exists():
        a = {w["week"]: w for w in load(src)["weeks"]}
        b = {w["week"]: w for w in load(exports / "week_index.json")["weeks"]}
        bad = 0
        for week, x in a.items():
            y = b.get(week)
            if not y:
                bad += 1
                continue
            for key in ("release", "version", "first_key", "obsid", "date_obs", "elon", "elat", "ra", "dec"):
                if x.get(key) != y.get(key):
                    bad += 1
                    break
            else:
                if abs(float(x.get("mjd_obs") or 0) - float(y.get("mjd_obs") or 0)) > 1e-6:
                    bad += 1
                elif str(x.get("detector")) != str(y.get("detector")):
                    bad += 1
        if bad:
            problems.append(f"week_index.json 有 {bad} 个周不一致")
        else:
            print(f"week_index       OK  ({len(a)} 个周)")
    else:
        problems.append("week_index.json 缺失")

    if problems:
        print("\nFAIL")
        for p in problems:
            print(" -", p)
        return 1
    print("\n全部兼容检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
