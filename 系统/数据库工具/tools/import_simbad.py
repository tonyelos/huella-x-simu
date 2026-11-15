#!/usr/bin/env python3
"""SIMBAD 名称 enrichment（不做位置匹配、不建场目录）。

    python3 tools/import_simbad.py --db var/star_catalog.sqlite
    python3 tools/import_simbad.py --db var/star_catalog.sqlite --offline

只更新 `target_name`/`star_meta`；不写 catalog_source，不生成 `simbad` release。
SIMBAD TAP 不可用时明确标 `simbad=unavailable`，名称仍是 preset 元数据。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.simbad import run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SIMBAD 名称 enrichment")
    parser.add_argument("--db", required=True)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    payload = run(args.db, probe=not args.offline, timeout=args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
