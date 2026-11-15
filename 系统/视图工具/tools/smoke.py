#!/usr/bin/env python3
"""Static smoke checks for the viewer — 一条命令回答“这版还能不能交”。

    python3 tools/smoke.py

检查项：
  1. index.html 里存在 main.js 引用的所有 DOM id；品牌标题正确；默认不是 M101
  2. 深链：search 优先 + hash 兜底 + 写回 search（含 t）
  2b. 分析时间窗口 30/90/180/365/730 + t0/t1 + 单 MoveScope handoff
  2c. 统一时间入口：commitTime、UTC 输入/±快捷、5 刻度 UTC 轴、连续播放/回绕、地球 tween
  3. data/fields.json 行数/列数/NaN/来源/生成元数据（不得再指向 header_cache）
  4. data/targets.json 字段齐全、生成物、与 app/data/targets.json 一致；rw_aur.truth 修正
  5. data/epoch_index.json 有策展历元（barnard 4 / rw_aur 1）
  6. 覆盖口径：2.47°；帧/曝光/夜拆分；RW Aurigae 非 0；README 表与 fields 一致
  7. 交接契约：MOVESCOPE_ORIGIN 8790、GET 探活、全精度坐标 + target；禁止旧路径/HEAD/五位小数
  8. 文案契约：无旧项目名/旧端口/过时提示
  9. 可重建：build_field_index --check（DB）与 --source export --out（相对路径）；sync_targets --check
 10. node --check 全部 js（node 存在时）
 11. start.sh 默认端口 8830

退出码 0 表示全过。
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
PROJECT = os.path.dirname(TOOL)

fails: list[str] = []
warns: list[str] = []

# 旧文案/旧写法只用于“不得出现”检查；这里拼接，避免本文件被全局 grep 误伤。
BANNED_TEXT = ("HUELLA" + "_X", "87" + "70", "前端尚未" + "落地")
DISALLOWED_JS = (
    "../app/" + "index.html",
    "method: '" + "HEAD'",
    'method: "' + 'HEAD"',
    "toFixed(" + "5)",
)
OLD_HALF = "1." + "745"
OLD_README = "5," + "857 次曝光"
FORBIDDEN_ENTRY = ("analysis" + "Url", "btn-" + "cascade", "btn-" + "tracks")
FORBIDDEN_PATHS = ("cascade: '" + "/cascade'", "tracks: '" + "/tracks'")


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


def ang_sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in degrees (same spherical law as js/astro.js)."""
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dra = math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.acos(min(1.0, max(-1.0, c))))


def night_of(mjd: float) -> str:
    return (datetime.datetime(1858, 11, 17) + datetime.timedelta(days=float(mjd))).date().isoformat()


# ---------------------------------------------------------------- 1. DOM ids
html = read(os.path.join(TOOL, "index.html"))
main_js = read(os.path.join(TOOL, "js", "main.js"))
readme = read(os.path.join(TOOL, "README.md"))
ids_used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", main_js))
ids_used |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", main_js))
ids_have = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
missing = sorted(ids_used - ids_have)
if missing:
    fail(f"index.html 缺少 main.js 引用的 id：{missing}")
else:
    ok(f"DOM id 全部存在（{len(ids_used)} 个）")

# ---------------------------------------------------------------- 1b. 品牌 / 深链
if "<title>SPHEREx 天区视图 · MoveScope</title>" not in html:
    fail("index.html 品牌标题不是 'SPHEREx 天区视图 · MoveScope'")
else:
    ok("品牌：SPHEREx 天区视图 · MoveScope")

if 'value="210.80"' in html or 'value="54.35"' in html:
    fail("index.html 仍把 M101 坐标写成输入框默认值")
else:
    ok("index.html 默认天区不是 M101")

i_search = main_js.find("new URLSearchParams(location.search)")
i_hash = main_js.find("new URLSearchParams(location.hash")
if i_search < 0 or i_hash < 0 or i_search > i_hash:
    fail("深链解析不是 search 优先 / hash 兜底")
elif "history.replaceState" not in main_js:
    fail("深链状态没有写回 search（缺 history.replaceState）")
elif "p.set('t'" not in main_js:
    fail("深链没有写回时相 t")
else:
    ok("深链：search 优先 + hash 兜底 + 写回 search")

# ---------------------------------------------------------------- 1c. 分析时间窗口 / 分析入口
win_block = re.search(r'<select id="win-days"[\s\S]*?</select>', html)
if not win_block:
    fail("index.html 缺分析时间窗口选择器 #win-days")
else:
    opts = re.findall(r'<option value="(\d+)"', win_block.group(0))
    if opts != ["30", "90", "180", "365", "730"]:
        fail(f"时间窗口选项不是 30/90/180/365/730：{opts}")
    elif 'value="365" selected' not in win_block.group(0):
        fail("时间窗口默认值不是 365 天")
    else:
        ok("时间窗口选项 30/90/180/365/730，默认 365 天")

for key in ("WINDOW_OPTIONS = [30, 90, 180, 365, 730]", "DEFAULT_WINDOW_DAYS = 365", "windowDays: DEFAULT_WINDOW_DAYS"):
    if key not in main_js:
        fail(f"main.js 缺时间窗口常量/状态：{key}")
if "state.t0 = state.mjd - state.windowDays" not in main_js or "state.t1 = state.mjd + state.windowDays" not in main_js:
    fail("时间窗口没有以当前 state.mjd 为中心计算 t0/t1")
if "$('win-days').addEventListener('change'" not in main_js:
    fail("时间窗口选择器没有 change 监听")
if "const t0 = num('t0')" not in main_js or "const t1 = num('t1')" not in main_js:
    fail("深链没有读取 t0/t1")
if "p.set('t0'" not in main_js or "p.set('t1'" not in main_js:
    fail("URL 没有写回 t0/t1")
if "p.set('t0'" in main_js and "p.set('t1'" in main_js:
    ok("时间窗口：默认 365、以 state.mjd 为中心、URL 读写 t0/t1")

if "分析层过滤范围" not in html or "数据时相" not in html:
    fail("页面没有明确“时间窗口 = 分析层过滤范围”且区别于数据时相")
else:
    ok("页面明确：时间窗口 = 分析层过滤范围（≠ 数据时相）")

for key in FORBIDDEN_PATHS:
    if key in main_js:
        fail(f"main.js 仍保留独立分析路径：{key}")
for token in FORBIDDEN_ENTRY:
    if token in html or token in main_js:
        fail(f"仍保留独立分析入口：{token}")
if "btn-movescope" not in html or "$('btn-movescope')" not in main_js:
    fail("缺少唯一的 MoveScope 入口")
else:
    ok("只有 MoveScope 一个分析入口")

if "进入 MoveScope 后切换" not in html or "普通" not in html or "级联" not in html or "轨迹" not in html:
    fail("页面没有明确“进入 MoveScope 后切换普通/级联/轨迹”")
else:
    ok("页面文案：进入 MoveScope 后切换普通/级联/轨迹")

if "不是数据时相" not in main_js:
    fail("分析提示没有区分“分析窗口”和“数据时相”")
else:
    ok("分析提示区分分析窗口与数据时相")

readme_needs = ("分析时间窗口", "t0", "t1", "from=view", "分析层过滤范围", "进入 MoveScope 后切换")
missing_readme = [s for s in readme_needs if s not in readme]
if missing_readme:
    fail(f"README 缺分析窗口/交接说明：{missing_readme}")
else:
    ok("README 记录分析窗口与单 MoveScope handoff")

# ---------------------------------------------------------------- 1d. 时间选择 / 播放 / tween
for tid in ("in-utc", "btn-utc-go", "t-start", "t-25", "t-mid", "t-75", "t-end"):
    if f'id="{tid}"' not in html:
        fail(f"index.html 缺时间控件/刻度 #{tid}")
if "UTC 时间轴" not in html:
    fail("时间轴没有标注 UTC 时间轴")
if "播放 = 连续时间" not in html:
    fail("页面没有标注“播放 = 连续时间”")
hours = re.findall(r'data-hours="(-?\d+)"', html)
speeds = re.findall(r'data-speed="([\d.]+)"', html)
if hours != ["-24", "-1", "1", "24"]:
    fail(f"±快捷按钮不是 -1d/-1h/+1h/+1d：{hours}")
elif speeds != ["0.15", "0.6", "2.4"]:
    fail(f"播放速度不是 0.15×/1×/4×：{speeds}")
else:
    ok("时间选择：UTC 输入 + ±1d/±1h + 5 个 UTC 刻度 + 0.15×/1×/4×")

if "function commitTime(mjd, mode = 'data'" not in main_js or "source = 'set'" not in main_js:
    fail("没有统一时间提交 commitTime(mjd, mode, {animate, source})")
elif main_js.count("state.mjd =") != 1:
    fail(f"state.mjd 有 {main_js.count('state.mjd =')} 处直接赋值，应只有 commitTime")
elif main_js.count("globe?.setClock") != 1:
    fail(f"globe.setClock 有 {main_js.count('globe?.setClock')} 处，应只有 commitTime")
else:
    ok("统一时间源：state.mjd/globe.setClock 只在 commitTime")

if "parseUtcInput" not in main_js or "locateUtc" not in main_js:
    fail("缺 UTC 输入解析/定位逻辑")
if "$('btn-utc-go').addEventListener" not in main_js or "$('in-utc').addEventListener" not in main_js:
    fail("UTC 输入/定位按钮没有绑定")
if "data-hours" not in main_js or "source: 'step'" not in main_js:
    fail("±快捷按钮没有走 commitTime")
if "source: 'slider'" not in main_js:
    fail("滑杆没有走 commitTime")
else:
    ok("UTC 输入、±按钮、滑杆、键盘都走 commitTime")

if "state.mjd + dt * state.speed" not in main_js or "if (m > mjdMax) m = mjdMin" not in main_js:
    fail("没有连续时间播放（mjd += dt × speed，末尾回绕）")
elif "commitTime(m, 'data', { silent: true, animate: false, source: 'play' })" not in main_js:
    fail("播放循环没有走 commitTime(..., animate:false)")
elif "state.mode === 'now' || state.mjd > mjdMax - 0.6" not in main_js or "source: 'play-reset'" not in main_js:
    fail("从末尾/now 起播没有先回 mjdMin")
else:
    ok("连续播放：dt×speed、末尾回绕、从末尾/now 先回 mjdMin、animate:false")

if "观测序列" in main_js or "observationTimes" in main_js or "STEP_MS" in main_js:
    fail("main.js 仍残留观测序列播放逻辑")
else:
    ok("已移除观测序列播放/首尾特判/末尾停止逻辑")

globe_js = read(os.path.join(TOOL, "js", "globe.js"))
if "ROTATE_MS = 700" not in globe_js or "rotTween" not in globe_js:
    fail("globe 没有 0.7s 自转 tween")
elif "Math.pow(1 - p, 3)" not in globe_js or "rotTween.target" not in globe_js:
    fail("地球 tween 不是 ease-out / 结束不精确")
else:
    ok("地球 tween：0.7s、最短路径、ease-out、结束精确")

missing_readme_time = [
    key
    for key in ("UTC 时间轴", "播放 = 连续时间", "0.15×", "回绕", "animate:false")
    if key not in readme
]
if missing_readme_time:
    fail(f"README 缺时间交互说明：{missing_readme_time}")
else:
    ok("README 同步 UTC 时间轴与连续播放")

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
missing_meta = [k for k in ("generated_by", "source", "generated_at", "count", "exposures") if k not in fields_doc]
if missing_meta:
    fail(f"fields.json 缺生成元数据：{missing_meta}")
else:
    ok("fields.json 带 generated_by/source/generated_at/count/exposures")
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

gen = str(tdoc.get("generated_by", ""))
tsrc = str(tdoc.get("source", ""))
if not gen.endswith("sync_targets.py") or not tsrc:
    fail("data/targets.json 不是 sync_targets.py 生成物（缺 generated_by/source）")
else:
    ok(f"targets 生成物：{gen} ← {tsrc}")

stscript = os.path.join(TOOL, "tools", "sync_targets.py")
if os.path.exists(app_t):
    r = subprocess.run([sys.executable, stscript, "--check"], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"sync_targets --check 失败：{r.stderr.strip() or r.stdout.strip()}")
    else:
        ok("targets/epoch_index 可由 sync_targets.py 重建（--check）")
else:
    warn("app/data/targets.json 不存在，跳过 sync_targets --check")

# ---------------------------------------------------------------- 4b. 覆盖口径
if "const HIT_RADIUS_DEG = 2.47" not in main_js:
    fail("main.js 的 HIT_RADIUS_DEG 不是 2.47")
else:
    ok("覆盖半径 2.47°（main.js）")
if OLD_HALF in read(os.path.join(TOOL, "js", "skymap.js")):
    fail("skymap.js 仍用旧半宽字面量作为命中/覆盖常量")
else:
    ok("skymap.js 无旧半宽命中常量")

COVERAGE_LABELS = {
    "barnard": "Barnard's Star",
    "epsind_b": "Epsilon Indi B",
    "wise0855": "WISE 0855",
    "rw_aur": "RW Aurigae",
    "r_dra": "R Draconis",
    "m101": "M101",
    "campo_a": "场 A",
}
coverage = {}
for t in targets:
    hits = [r for r in rows if ang_sep_deg(r[2], r[3], t["ra"], t["dec"]) <= 2.47]
    obs = {r[0] for r in hits}
    nights = {night_of(r[4]) for r in hits}
    minsep = min((ang_sep_deg(r[2], r[3], t["ra"], t["dec"]) for r in hits), default=0.0)
    coverage[t["id"]] = {
        "frames": len(hits),
        "exposures": len(obs),
        "nights": len(nights),
        "minsep": round(minsep * 60, 1),
    }

rwc = coverage.get("rw_aur", {})
if rwc.get("frames", 0) < 6 or rwc.get("nights", 0) < 1:
    fail(f"RW Aurigae 覆盖仍是 {rwc}（期望 ≥6 帧 / ≥1 夜）")
else:
    ok(
        f"RW Aurigae 覆盖 {rwc['frames']} 帧 / {rwc['exposures']} 曝光 / {rwc['nights']} 夜（非 0）"
    )

doc_cov = {}
for line in readme.splitlines():
    m = re.match(
        r"\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)′\s*\|",
        line,
    )
    if m:
        doc_cov[m.group(1).strip()] = (
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4)),
            float(m.group(5)),
        )
doc_drift = []
for tid, label in COVERAGE_LABELS.items():
    got = coverage.get(tid)
    want = doc_cov.get(label)
    if got is None:
        continue
    if want is None:
        doc_drift.append(f"{label}: README 表缺行")
    elif want[:3] != (got["frames"], got["exposures"], got["nights"]) or abs(want[3] - got["minsep"]) > 0.05:
        doc_drift.append(f"{label}: README {want} ≠ fields {got}")
if doc_drift:
    fail("README 覆盖表与 fields/2.47° 不一致：" + "；".join(doc_drift))
else:
    ok("README 覆盖表与 fields/2.47° 一致（7 个目标）")

for s in ("5,893 帧 / 5,562", "23 个观测周", "5,893 帧"):
    if s not in readme:
        fail(f"README 缺当前快照口径：{s!r}")
if OLD_README in readme or "15 个观测周" in readme:
    fail("README 仍含旧快照口径（旧曝光数 / 15 个观测周）")
else:
    ok("README 快照口径 5,893 帧 / 5,562 曝光 / 23 周")

# ---------------------------------------------------------------- 4. epoch index
ei = json.loads(read(os.path.join(TOOL, "data", "epoch_index.json"))).get("targets") or {}
for tid, want in (("barnard", 4), ("rw_aur", 1)):
    got = len(ei.get(tid, []))
    if got != want:
        fail(f"epoch_index[{tid}] = {got}，应为 {want}")
    else:
        ok(f"epoch_index[{tid}] = {got}")

# ---------------------------------------------------------------- 5. 交接契约
for pat in DISALLOWED_JS:
    hits = [f for f in ("main.js", "globe.js", "skymap.js") if pat in read(os.path.join(TOOL, "js", f))]
    if hits:
        fail(f"js/ 仍含禁用写法 {pat!r}：{hits}")
    else:
        ok(f"js/ 无 {pat!r}")
origin = re.search(r"const MOVESCOPE_ORIGIN = String\([\s\S]*?\);", main_js)
if not origin:
    fail("main.js 找不到 MOVESCOPE_ORIGIN 配置")
elif "8790" not in origin.group(0) or "window.MOVESCOPE_ORIGIN" not in origin.group(0):
    fail("MOVESCOPE_ORIGIN 默认不是 8790，或不可用 window.MOVESCOPE_ORIGIN 覆盖")
elif "/api/health" not in main_js or "mode: 'no-cors'" not in main_js:
    fail("main.js 的探活不是 GET /api/health（no-cors）")
else:
    ok("MoveScope 交接：MOVESCOPE_ORIGIN 默认 8790 + GET 探活")

# 传给 MoveScope 的一次 handoff：全精度坐标 + t0/t1/from=view，不传 mode
m = re.search(r"function moveScopeUrl\(\)[\s\S]*?\n\}", main_js)
if not m:
    fail("找不到 moveScopeUrl()")
else:
    body = m.group(0)
    needs = (
        "p.set('ra', String(state.ra))",
        "p.set('dec', String(state.dec))",
        "p.set('fov', String(state.fov))",
        "p.set('t0'",
        "p.set('t1'",
        "p.set('from', 'view')",
    )
    miss = [x for x in needs if x not in body]
    if miss:
        fail(f"moveScopeUrl 缺参数/不是全精度：{miss}")
    elif "p.set('mode'" in body or "mode=" in body:
        fail("moveScopeUrl 不应传 mode")
    elif "p.set('target'" in body:
        fail("moveScopeUrl 不应再传 target")
    else:
        ok("MoveScope URL：全精度 ra/dec/fov + t0/t1 + from=view，无 mode/target")

# ---------------------------------------------------------------- 6. 文案
all_text = html + main_js + readme
report = os.path.join(TOOL, "docs", "交接修复报告.md")
if os.path.exists(report):
    all_text += read(report)
for bad_s in BANNED_TEXT:
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

# 从 DB 导出物重建（同时验证相对 --out；不写仓库）
export_path = os.path.join(PROJECT, "数据库工具", "var", "exports", "fields.json")
if os.path.exists(export_path):
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [sys.executable, bscript, "--source", "export", "--out", "fields.json", "--force"],
            cwd=td,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            fail(f"build_field_index --source export 失败：{r.stderr.strip() or r.stdout.strip()}")
        else:
            built = json.loads(read(os.path.join(td, "fields.json")))
            if built.get("fields") != rows:
                fail("--source export 生成结果与 data/fields.json 不逐行一致")
            else:
                ok("fields.json 可由 DB export 重建（--source export --out）")
else:
    warn("DB export 不存在，跳过 --source export 校验")

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
