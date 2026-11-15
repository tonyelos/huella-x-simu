"""SIMBAD 名称 enrichment 的最小实现。

边界（与 D-02 一致）：

- SIMBAD 只用于名称/ID/otype 提示，**不做位置匹配/3σ 锚点**；
- 本轮只做 7 个 preset target 的名称 enrichment；不建 SIMBAD 场目录；
- SIMBAD TAP 当前不可用（404/超时）时明确写 `simbad=unavailable`；
  名称仍来自项目 preset 元数据（provenance=preset_target_names），不伪称在线查询；
- 若未来 TAP 可用，本模块只标记 `available_names_only`；场锥搜索尚未接入。

参考：
- SIMBAD/CDS: https://simbad.cds.unistra.fr/
- Wenger et al. 2000, A&AS 143, 9, DOI 10.1051/aas:2000332
- 许可：ODbL v1.0
"""
from __future__ import annotations

import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .connection import utcnow
from .star_catalog import StarCatalog
from .star_schema import apply_star_schema

SIMBAD_TAP_SYNC = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
SIMBAD_LICENSE = "ODbL v1.0 (SIMBAD/CDS)"
SIMBAD_CITATION = "Wenger et al. 2000, A&AS 143, 9, DOI 10.1051/aas:2000332"
USER_AGENT = "huellaX-star-catalog/1.0 (SIMBAD name enrichment)"


class SIMBADError(RuntimeError):
    pass


def _probe_url() -> str:
    query = "SELECT TOP 1 main_id FROM basic"
    params = {
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "csv",
        "QUERY": query,
    }
    return SIMBAD_TAP_SYNC + "?" + urllib.parse.urlencode(params)


def probe_simbad(timeout: float = 8.0) -> tuple[bool, str]:
    """轻量探测 SIMBAD TAP；不解析对象、不用于名称。"""
    req = urllib.request.Request(_probe_url(), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        return False, "empty response"
    try:
        reader = csv.DictReader(io.StringIO(text))
        if "main_id" not in (reader.fieldnames or []):
            return False, f"unexpected TAP response: {text[:160]!r}"
    except (csv.Error, TypeError) as exc:
        return False, f"invalid TAP response: {exc}"
    return True, "SIMBAD TAP reachable"


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO star_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def set_status(db_path: str | Path, status: str, note: str) -> dict:
    """写入 simbad_status/simbad_note；只改元数据，不写 catalog_source。"""
    if status not in ("unavailable", "available_names_only"):
        raise ValueError(f"未知 SIMBAD 状态: {status}")
    with StarCatalog(db_path, readonly=False) as cat:
        apply_star_schema(cat.conn)
        _set_meta(cat.conn, "simbad_status", status)
        _set_meta(cat.conn, "simbad_note", note)
        _set_meta(cat.conn, "simbad_checked_at", utcnow())
        cat.conn.commit()
        return cat.target_names()


def run(
    db_path: str | Path,
    *,
    probe: bool = True,
    timeout: float = 8.0,
) -> dict:
    """种子 preset 名称 + 探测 SIMBAD TAP；失败不编造在线结果。"""
    if probe:
        ok, detail = probe_simbad(timeout=timeout)
        status = "available_names_only" if ok else "unavailable"
        note = (
            "SIMBAD TAP reachable; field cone search not implemented this round; "
            "names remain preset metadata (preset_target_names)."
            if ok
            else f"SIMBAD TAP unavailable: {detail}. Names are preset metadata, not a SIMBAD catalog."
        )
        time.sleep(0)  # 保留重试/限流钩子；不额外请求
    else:
        status = "unavailable"
        note = "SIMBAD probe disabled; names are preset metadata, not a SIMBAD catalog."
    return set_status(db_path, status, note)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="SIMBAD 名称 enrichment（不做位置匹配）")
    parser.add_argument("--db", required=True)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--offline", action="store_true", help="不探测网络，直接标 unavailable")
    args = parser.parse_args(argv)
    payload = run(args.db, probe=not args.offline, timeout=args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
