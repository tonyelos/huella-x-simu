#!/usr/bin/env python3
"""在 SPHEREx S3 公开桶里为目标坐标寻找真实历元。

策略（不依赖 TAP/SIA/DataLink）：
  1. 用 app/data/week_index.json 的周指向（黄经）筛出候选周；
  2. 列出每周 D1 的场号，对场号做"粗扫 + 细扫"头部 Range 读取（每个 ~40 KB）；
  3. 头部 CRVAL1/2 是该探测器视场中心，取与目标角距最小者；
  4. 再对 D1–D6 取头，选角距 < 1.8° 的探测器；
  5. 输出 epoch_catalog.json（含切片 URL、MJD、观测 ID）。

用法：
    python3 tools/find_epochs.py --target barnard --weeks 2025W35_2A 2026W12_2A
    python3 tools/find_epochs.py --all --max-weeks 6
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S3 = "https://nasa-irsa-spherex.s3.amazonaws.com"
CACHE = os.path.join(ROOT, "app", "data", "header_cache")
HDR = 45000
R_EARTH = 6371.0


def angsep(ra1, dec1, ra2, dec2) -> float:
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def eq2ecl(ra, dec):
    eps = math.radians(23.4392911)
    ra, dec = math.radians(ra), math.radians(dec)
    x = math.cos(dec) * math.cos(ra)
    y = math.cos(dec) * math.sin(ra)
    z = math.sin(dec)
    ye = y * math.cos(eps) + z * math.sin(eps)
    ze = -y * math.sin(eps) + z * math.cos(eps)
    return math.degrees(math.atan2(ye, x)) % 360, math.degrees(math.asin(ze))


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MoveScope/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _range(key: str) -> bytes:
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest() + ".hdr")
    if os.path.exists(fn) and os.path.getsize(fn) > 30000:
        return open(fn, "rb").read()
    req = urllib.request.Request(
        f"{S3}/{key}", headers={"Range": f"bytes=0-{HDR - 1}", "User-Agent": "MoveScope/0.1"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        blob = r.read()
    with open(fn, "wb") as fh:
        fh.write(blob)
    return blob


def parse_hdus(blob: bytes) -> list[dict]:
    hdus: list[dict] = []
    start = 0
    while start + 2880 <= len(blob):
        h, blk = {}, 0
        done = False
        while not done:
            chunk = blob[start + blk * 2880 : start + (blk + 1) * 2880]
            if len(chunk) < 2880:
                return hdus
            text = chunk.decode("latin-1")
            for j in range(0, 2880, 80):
                card = text[j : j + 80]
                key = card[:8].strip()
                if key == "END":
                    done = True
                    break
                if key and card[8:10] == "= ":
                    h[key] = card[10:80].split("/")[0].strip()
            blk += 1
        hdus.append(h)
        start += blk * 2880
        naxis = int(h.get("NAXIS", 0))
        if h.get("XTENSION", "").strip() == "BINTABLE":
            nb = int(h.get("NAXIS1", 0)) * int(h.get("NAXIS2", 0))
        else:
            n = 0
            if naxis > 0:
                n = 1
                for d in range(1, naxis + 1):
                    n *= int(h.get(f"NAXIS{d}", 0))
            nb = abs(int(h.get("BITPIX", 8))) // 8 * n
        start += ((nb + 2879) // 2880) * 2880 if nb else 0
        if len(hdus) > 3:
            break
    return hdus


def image_header(key: str) -> dict | None:
    try:
        hdus = parse_hdus(_range(key))
        return hdus[1] if len(hdus) > 1 else None
    except Exception:
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def list_week(release: str, week: str, version: str) -> list[str]:
    keys: list[str] = []
    for det in range(1, 7):
        pre = f"{release}/level2/{week}/{version}/{det}/"
        try:
            xml = _get(f"{S3}/?list-type=2&prefix={pre}&max-keys=1000").decode()
        except Exception:
            continue
        keys.extend(k for k in re.findall(r"<Key>([^<]+)</Key>", xml) if k.endswith(".fits"))
    return keys


def field_no(key: str) -> int | None:
    m = re.search(r"_(\d{4})_\d+D(\d)_spx_", key)
    return int(m.group(1)) if m else None


def key_for(fields: dict[int, dict], fno: int, det: int) -> str | None:
    for k, v in fields.items():
        if v["field"] == fno and v["det"] == det:
            return k
    return None


def probe_week(release: str, week: str, version: str, target, prefer_det=1):
    keys = list_week(release, week, version)
    if not keys:
        return None
    fmap = {}
    for k in keys:
        m = re.search(r"_(\d{4})_(\d)D(\d)_spx_", k)
        if m:
            fmap[k] = {"field": int(m.group(1)), "rep": int(m.group(2)), "det": int(m.group(3))}
    det_keys = [(k, v) for k, v in fmap.items() if v["det"] == prefer_det]
    if not det_keys:
        return None
    fields = sorted({v["field"] for _, v in det_keys})

    def score(f):
        k = key_for(fmap, f, prefer_det)
        if not k:
            return (f, None, None, None)
        h = image_header(k)
        if not h:
            return (f, None, None, None)
        ra, dec = _f(h.get("CRVAL1")), _f(h.get("CRVAL2"))
        if ra is None or dec is None:
            return (f, None, None, None)
        return (f, ra, dec, angsep(ra, dec, target[0], target[1]))

    # 视轴场中心可能离目标 5–7°（焦平面 6 个探测器并联），先扫全部场
    with ThreadPoolExecutor(max_workers=16) as ex:
        coarse_res = list(ex.map(score, fields))
    coarse_res = [r for r in coarse_res if r[3] is not None]
    if not coarse_res:
        return None
    coarse_res.sort(key=lambda r: r[3])
    cands = [r for r in coarse_res if r[3] < 7.5][:6]
    out = []
    for f, ra, dec, sep in cands:
        for det in range(1, 7):
            k = key_for(fmap, f, det)
            if not k:
                continue
            h = image_header(k)
            if not h:
                continue
            cra, cdec = _f(h.get("CRVAL1")), _f(h.get("CRVAL2"))
            if cra is None:
                continue
            d = angsep(cra, cdec, target[0], target[1])
            if d < 1.9:
                mjd = _f(h.get("MJD-OBS"))
                out.append(
                    {
                        "release": release,
                        "week": week,
                        "version": version,
                        "field": f,
                        "detector": det,
                        "key": k,
                        "obsid": h.get("OBSID", "").strip("' "),
                        "mjd": mjd,
                        "date_obs": h.get("DATE-OBS", "").strip("' "),
                        "sep_deg": round(d, 3),
                    }
                )
    return out or None


def slice_url(ep: dict, ra: float, dec: float, size: float, release_override: str | None = None) -> str:
    rel = release_override or ep["release"]
    relpath = ep["key"].split("/", 1)[1] if ep["key"].startswith(ep["release"] + "/") else ep["key"]
    return (
        f"https://irsa.ipac.caltech.edu/ibe/data/spherex/{rel}/{relpath}"
        f"?center={ra:.6f},{dec:.6f}d&size={size}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--weeks", nargs="*", default=None)
    ap.add_argument("--max-weeks", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(ROOT, "app", "data", "epoch_catalog.json"))
    args = ap.parse_args()

    catalog = json.load(open(os.path.join(ROOT, "app", "data", "targets.json")))
    idx = json.load(open(os.path.join(ROOT, "app", "data", "week_index.json")))["weeks"]
    targets = catalog["targets"]
    if args.target:
        targets = [t for t in targets if t["id"] == args.target]
    elif not args.all:
        targets = [t for t in targets if t.get("official")]

    epochs: dict[str, list] = {}
    for t in targets:
        ra, dec = t["ra"], t["dec"]
        lam, beta = eq2ecl(ra, dec)
        cands = [w for w in idx if abs((w["elon"] - lam + 180) % 360 - 180) < 28]
        if args.weeks:
            cands = [w for w in idx if w["week"] in args.weeks]
        cands.sort(key=lambda w: abs((w["elon"] - lam + 180) % 360 - 180))
        print(f"\n== {t['name']}  λ={lam:.1f} β={beta:.1f}  候选 {len(cands)} 周", flush=True)
        found = []
        for w in cands[: args.max_weeks]:
            res = probe_week(w["release"], w["week"], w["version"], (ra, dec))
            if res:
                print(f"   {w['week']}: {len(res)} 个探测器覆盖", flush=True)
                found.extend(res)
            else:
                print(f"   {w['week']}: 未覆盖（或探测失败）", flush=True)
        for ep in found:
            ep["target_id"] = t["id"]
            ep["slice_url_002"] = slice_url(ep, ra, dec, 0.05)
        found.sort(key=lambda e: e["mjd"] or 0)
        epochs[t["id"]] = found
        print(f"   → 共 {len(found)} 个真实历元", flush=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump({"generated_by": "tools/find_epochs.py", "epochs": epochs}, open(args.out, "w"), indent=1)
    print("写入", args.out)


if __name__ == "__main__":
    main()
