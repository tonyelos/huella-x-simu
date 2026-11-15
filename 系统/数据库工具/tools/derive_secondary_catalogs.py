#!/usr/bin/env python3
"""从 Gaia 缓存派生 2MASS/WISE 交叉证认子集（不联网）。

    python3 tools/derive_secondary_catalogs.py --db var/star_catalog.sqlite

只读 Gaia release，写独立 `2mass`/`wise` release；不下载、不提交缓存。
这是交叉证认子集，不是完整 2MASS/WISE 目录；缺失 JHK/W1W2 的源不会进入。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.star_catalog import StarCatalog  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="派生 2MASS/WISE 子集")
    parser.add_argument("--db", required=True)
    parser.add_argument("--gaia-version", default="dr3")
    args = parser.parse_args(argv)
    with StarCatalog(args.db, readonly=False) as cat:
        result = cat.derive_secondary_catalogs(gaia_version=args.gaia_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
