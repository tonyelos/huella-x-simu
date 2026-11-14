#!/usr/bin/env python3
"""扫描 SPHEREx 公开 S3 桶，建立"周 → 指向"索引。

背景：IRSA 的 TAP / SIA / DataLink 在采集时段不可用（502），而 S3 静态桶可用。
导航头里带 SPS_ELON / SPS_ELAT（计划视轴黄经/黄纬），因此只要对每周第一个
level2 文件做一次 HTTP Range 读取（只取头部约 60 KB），就能知道这一周扫了天球
的哪一条带。索引用来把"用户坐标"缩小到"候选周 + 候选场号"，避免盲取 65 MB 大文件。

用法：
    python3 tools/scan_week_index.py --out app/data/week_index.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

S3 = "https://nasa-irsa-spherex.s3.amazonaws.com"
REL = ("qr3", "dr1")
HDR_BYTES = 65536


def get(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MoveScope/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def list_prefix(prefix: str) -> list[str]:
    xml = get(f"{S3}/?list-type=2&prefix={prefix}&max-keys=1000").decode("utf-8", "replace")
    return re.findall(r"<Key>([^<]+)</Key>", xml)


def list_dirs(prefix: str) -> list[str]:
    xml = get(f"{S3}/?list-type=2&prefix={prefix}&delimiter=/&max-keys=1000").decode("utf-8", "replace")
    found = re.findall(r"<Prefix>([^<]+)</Prefix>", xml)
    # S3 回显请求前缀本身，排除它，只保留真正的子目录
    return [p for p in found if p != prefix and p.startswith(prefix)]


def parse_header_cards(blob: bytes, start: int = 0) -> tuple[dict, int]:
    """返回 (cards, header_bytes)，只解析第一个头部（PRIMARY）。"""
    h: dict[str, str] = {}
    blk = 0
    while True:
        chunk = blob[start + blk * 2880 : start + (blk + 1) * 2880]
        if len(chunk) < 2880:
            break
        text = chunk.decode("latin-1")
        for j in range(0, 2880, 80):
            card = text[j : j + 80]
            key = card[:8].strip()
            if key == "END":
                return h, (blk + 1) * 2880
            if key and card[8:10] == "= ":
                h[key] = card[10:80].split("/")[0].strip()
        blk += 1
    return h, blk * 2880


def first_image_cards(blob: bytes) -> dict:
    """PRIMARY 之后第一个 IMAGE 扩展的头（含 SPS_ELON / MJD-OBS）。"""
    _, primary_bytes = parse_header_cards(blob, 0)
    cards, _ = parse_header_cards(blob, primary_bytes)
    return cards


def scan_week(rel: str, week: str) -> dict | None:
    try:
        vers = list_dirs(f"{rel}/level2/{week}/")
        if not vers:
            return None
        ver = vers[0].rstrip("/").split("/")[-1]
        keys = list_prefix(f"{rel}/level2/{week}/{ver}/1/")
        if not keys:
            # 某些周没有 D1，退回扫描所有探测器目录
            for det in range(1, 7):
                keys = list_prefix(f"{rel}/level2/{week}/{ver}/{det}/")
                if keys:
                    break
        if not keys:
            return None
        key = keys[0]
        blob = _range(key)
        h = first_image_cards(blob)
        return {
            "release": rel,
            "week": week,
            "version": ver,
            "first_key": key,
            "obsid": h.get("OBSID", "").strip("' "),
            "detector": h.get("DETECTOR", "").strip("' "),
            "date_obs": h.get("DATE-OBS", "").strip("' "),
            "mjd_obs": _f(h.get("MJD-OBS")),
            "elon": _f(h.get("SPS_ELON")),
            "elat": _f(h.get("SPS_ELAT")),
            "ra": _f(h.get("CRVAL1")),
            "dec": _f(h.get("CRVAL2")),
        }
    except Exception as exc:  # noqa: BLE001 - 索引允许单周失败
        return {"release": rel, "week": week, "error": str(exc)}


def _range(key: str) -> bytes:
    req = urllib.request.Request(
        f"{S3}/{key}",
        headers={"Range": f"bytes=0-{HDR_BYTES - 1}", "User-Agent": "MoveScope/0.1"},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read()


def _f(v: str | None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="app/data/week_index.json")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    weeks: list[tuple[str, str]] = []
    for rel in REL:
        xml = get(f"{S3}/?list-type=2&prefix={rel}/level2/&delimiter=/&max-keys=1000").decode()
        for w in re.findall(r"<Prefix>" + rel + r"/level2/([^<]+)/</Prefix>", xml):
            weeks.append((rel, w))
    print(f"发现 {len(weeks)} 个周目录，开始扫描（{args.workers} 并发）…", flush=True)

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(scan_week, rel, w): (rel, w) for rel, w in weeks}
        for i, fut in enumerate(as_completed(futs), 1):
            rel, w = futs[fut]
            res = fut.result()
            if res:
                out.append(res)
            if i % 20 == 0 or i == len(weeks):
                print(f"  {i}/{len(weeks)}", flush=True)

    ok = [r for r in out if r.get("elon") is not None]
    ok.sort(key=lambda r: (r["release"], r["week"]))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"generated_by": "tools/scan_week_index.py", "weeks": ok}, fh, ensure_ascii=False, indent=1)
    print(f"写入 {args.out}：{len(ok)} 周有指向，{len(out) - len(ok)} 周失败/无数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
