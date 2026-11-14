#!/usr/bin/env python3
"""Static smoke checks for the viewer — 一条命令回答“这版还能不能交”。

    python3 tools/smoke.py

检查项：
  1. index.html 里存在 main.js 引用的所有 DOM id
  2. data/fields.json 行数/列数/NaN/来源（不得再指向已删除的 header_cache）
  3. data/targets.json 字段齐全，且与 app/data/targets.json 一致
  4. data/epoch_index.json 有策展历元（barnard 4 / rw_aur 1）
  5. 交接契约：MoveScope 地址、GET 探活、全精度坐标；禁止旧路径/HEAD/toFixed(5)
  6. 文案契约：无 HUELLA_X、无 8770、无“前端尚未落地”
  7. 可重建：build_field_index.py --check 与 fields.json 一致（DB 存在时）
  8. node --check 全部 js（node 存在时）
  9. start.sh 默认端口 8830

退出码 0 表示全过。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
PROJECT = os.path.dirname(TOOL)

fails: list[str] = []
warns: list[str] = []


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    fails.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg: str) -> None:
    warns.append(msg)
    print(f"  WARN  {msg}")


def read(path: str) -> str:
    return open(path, encoding="utf-8").read()


# ---------------------------------------------------------------- 1. DOM ids
html = read(os.path.join(TOOL, "index.html"))
main_js = read(os.path.join(TOOL, "js", "main.js"))
ids_used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", main_js))
ids_used |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", main_js))
ids_have = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
missing = sorted(ids_used - ids_have)
if missing:
    fail(f"index.html 缺少 main.js 引用的 id：{missing}")
else:
    ok(f"DOM id 全部存在（{len(ids_used)} 个）")

# ---------------------------------------------------------------- 2. fields.json
fields_doc = json.loads(read(os.path.join(TOOL, "data", "fields.json")))
rows = fields_doc.get("fields") or []
if len(rows) != fields_doc.get("count"):
    fail(f"fields.json count={fields_doc.get('count')} 与实际 {len(rows)} 行不符")
else:
    ok(f"fields.json {len(rows)} 帧")
if any(len(r) != 10 for r in rows[:50]):
    fail("fields.json 行宽不是 10")
bad = [i for i, r in enumerate(rows) if any(isinstance(v, float) and v != v for v in r)]
if bad:
    fail(f"fields.json 有 {len(bad)} 行含 NaN")
src = str(fields_doc.get("source", ""))
if "header_cache" in src:
    fail(f"fields.json 的 source 仍指向 header_cache：{src}")
elif not src:
    fail("fields.json 缺 source")
else:
    ok(f"fields.json 来源 {src}")
n_obs = len({r[0] for r in rows})
db_path = os.path.join(PROJECT, "数据库工具", "var", "catalog.sqlite")
if os.path.exists(db_path):
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db_frames, db_obs = con.execute("select count(*), count(distinct obs_id) from frames").fetchone()
    con.close()
    if len(rows) != db_frames:
        fail(f"fields.json {len(rows)} 帧 ≠ 数据库 {db_frames} 帧（源变了，重跑 build_field_index.py --force）")
    elif n_obs != db_obs:
        fail(f"曝光数 {n_obs} ≠ 数据库 {db_obs}")
    else:
        ok(f"{len(rows)} 帧 / {n_obs} 曝光，与数据库一致")
else:
    warn("数据库不存在，跳过帧数比对")

# ---------------------------------------------------------------- 3. targets
tdoc = json.loads(read(os.path.join(TOOL, "data", "targets.json")))
targets = tdoc.get("targets") or []
req = ("id", "name", "ra", "dec", "official", "expected", "truth", "coord_unc_arcsec")
missing_f = [(t.get("id"), [k for k in req if k not in t]) for t in targets]
missing_f = [m for m in missing_f if m[1]]
if len(targets) != 7:
    fail(f"targets.json 有 {len(targets)} 个目标，应为 7")
elif missing_f:
    fail(f"targets 缺字段：{missing_f}")
else:
    ok("targets.json 7 个目标，字段齐全")

app_t = os.path.join(PROJECT, "app", "data", "targets.json")
if os.path.exists(app_t):
    app_targets = json.loads(read(app_t)).get("targets") or []
    by_id = {t["id"]: t for t in app_targets}
    drift = []
    for t in targets:
        a = by_id.get(t["id"])
        if not a:
            drift.append(f"{t['id']}: 不在 app/data/targets.json")
            continue
        for k in ("ra", "dec", "official", "expected", "truth", "coord_unc_arcsec"):
            if a.get(k) != t.get(k):
                drift.append(f"{t['id']}.{k}")
    if drift:
        fail(f"targets 与 app/data/targets.json 不一致：{drift}")
    else:
        ok("targets 与 app/data/targets.json 逐字段一致")
else:
    warn("app/data/targets.json 不存在，跳过单一源比对")

rw = next((t for t in targets if t["id"] == "rw_aur"), None)
if rw and (rw["truth"].get("period_d") != 12.0 or rw["truth"].get("amp_mag") != 0.85):
    fail(f"rw_aur.truth 未修正：{rw['truth']}")
elif rw:
    ok("rw_aur.truth = 12.0 d / 0.85 mag")

# ---------------------------------------------------------------- 4. epoch index
ei = json.loads(read(os.path.join(TOOL, "data", "epoch_index.json"))).get("targets") or {}
for tid, want in (("barnard", 4), ("rw_aur", 1)):
    got = len(ei.get(tid, []))
    if got != want:
        fail(f"epoch_index[{tid}] = {got}，应为 {want}")
    else:
        ok(f"epoch_index[{tid}] = {got}")

# ---------------------------------------------------------------- 5. 交接契约
for pat in ("../app/index.html", "method: 'HEAD'", 'method: "HEAD"', "toFixed(5)"):
    hits = [f for f in ("main.js", "globe.js", "skymap.js") if pat in read(os.path.join(TOOL, "js", f))]
    if hits:
        fail(f"js/ 仍含禁用写法 {pat!r}：{hits}")
    else:
        ok(f"js/ 无 {pat!r}")
if "http://127.0.0.1:8790" not in main_js:
    fail("main.js 缺 MOVESCOPE_ORIGIN 8790")
elif "mode: 'no-cors'" not in main_js:
    fail("main.js 的探活不是 GET/no-cors")
else:
    ok("MoveScope 交接：8790 + GET 探活")

# 传给 MoveScope 的坐标必须全精度（moveScopeUrl 里不得出现舍入）
m = re.search(r"function moveScopeUrl\(\)[\s\S]*?\n\}", main_js)
if not m:
    fail("找不到 moveScopeUrl()")
elif "toFixed" in m.group(0):
    fail("moveScopeUrl 里仍有 toFixed 舍入")
elif "String(t.ra)" not in m.group(0) or "String(state.ra)" not in m.group(0):
    fail("moveScopeUrl 未使用全精度坐标")
else:
    ok("MoveScope URL 使用全精度坐标")

# ---------------------------------------------------------------- 6. 文案
all_text = html + main_js + read(os.path.join(TOOL, "README.md"))
for bad_s in ("HUELLA_X", "8770", "前端尚未落地"):
    if bad_s in all_text:
        fail(f"文案仍含 {bad_s!r}")
    else:
        ok(f"文案无 {bad_s!r}")

# ---------------------------------------------------------------- 7. 可重建
bscript = os.path.join(TOOL, "tools", "build_field_index.py")
if os.path.exists(os.path.join(PROJECT, "数据库工具", "var", "catalog.sqlite")):
    r = subprocess.run([sys.executable, bscript, "--check"], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"build_field_index --check 失败：{r.stderr.strip() or r.stdout.strip()}")
    else:
        ok("fields.json 可由数据库逐行重建")
else:
    warn("数据库不存在，跳过后端重建校验")

# ---------------------------------------------------------------- 8. node --check
node = subprocess.run(["bash", "-lc", "command -v node"], capture_output=True, text=True).stdout.strip()
if node:
    for f in ("main.js", "globe.js", "skymap.js", "astro.js"):
        r = subprocess.run([node, "--check", os.path.join(TOOL, "js", f)], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"node --check {f} 失败：{r.stderr.strip().splitlines()[-1] if r.stderr else ''}")
    if not any("node --check" in f for f in fails):
        ok("node --check 4/4")
else:
    warn("node 不可用，跳过语法检查")

# ---------------------------------------------------------------- 9. start.sh
start = read(os.path.join(TOOL, "start.sh"))
if "8830" not in start:
    fail("start.sh 默认端口不是 8830")
elif re.search(r"range\(8[0-9]{3}", start):
    fail("start.sh 仍在扫描端口")
else:
    ok("start.sh 默认 8830，不扫端口")

# ---------------------------------------------------------------- 汇总
print()
if fails:
    print(f"SMOKE FAIL：{len(fails)} 项失败，{len(warns)} 项警告")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"SMOKE OK：全部通过（{len(warns)} 项警告）")
for w in warns:
    print(f"  ~ {w}")
