#!/usr/bin/env python3
"""Static smoke checks for the viewer — one command answers "is this shippable?".

    python3 tools/smoke.py

Checks:
  1. Every DOM id referenced by main.js exists; English-default brand; default region is not M101
  2. Deep links: search first + hash fallback + write-back to search (including t)
  2b. Analysis time window 30/90/180/365/730 + t0/t1 + a single MoveScope handoff
  2c. Single time entry point: commitTime, UTC input/steps, 5 UTC ticks, continuous playback, Earth tween
  2d. i18n: English default, header EN/ES selector, ?lang=en|es + localStorage, complete EN/ES dictionaries,
      no Chinese UI strings, caveat semantics in both languages, lang excluded from the handoff
  3. data/fields.json rows/columns/NaN/source/generator metadata (must not point at header_cache)
  4. data/targets.json fields/generator/single-source consistency; rw_aur.truth fix
  5. data/epoch_index.json curated epochs (barnard 4 / rw_aur 1)
  6. Coverage definition: 2.47°; frames/exposures/nights split; RW Aurigae non-zero; README table matches fields
  7. Handoff contract: MOVESCOPE_ORIGIN 8790, GET health check, full-precision coordinates + t0/t1/from=view;
     no old path/HEAD/five-decimal rounding, no mode/target/lang
  8. Copy contract: no old project name/port/stale copy; no novelty/first-detection claims in UI/README/docs
  9. Rebuildable: build_field_index --check (DB) and --source export --out (relative path); sync_targets --check
 10. node --check all js (also imports js/i18n.js and compares dictionaries when node exists)
 11. start.sh default port 8830

Exit code 0 means everything passed.
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
OLD_README = "5," + "857 exposures"
FORBIDDEN_ENTRY = ("analysis" + "Url", "btn-" + "cascade", "btn-" + "tracks")
FORBIDDEN_PATHS = ("cascade: '" + "/cascade'", "tracks: '" + "/tracks'")
LANG_KEY = "spherex.view.lang"
NOVELTY_RE = re.compile(r"\bdis" + r"cover", re.I)


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
i18n_js = read(os.path.join(TOOL, "js", "i18n.js"))
readme = read(os.path.join(TOOL, "README.md"))
i18n_doc_path = os.path.join(TOOL, "docs", "i18n-en-es.md")
i18n_doc = read(i18n_doc_path) if os.path.exists(i18n_doc_path) else ""
ids_used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", main_js))
ids_used |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", main_js))
ids_have = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
missing = sorted(ids_used - ids_have)
if missing:
    fail(f"index.html 缺少 main.js 引用的 id：{missing}")
else:
    ok(f"DOM id 全部存在（{len(ids_used)} 个）")

# ---------------------------------------------------------------- 1b. Brand / deep links
if '<html lang="en">' not in html:
    fail("index.html 不是英文默认（缺 <html lang=\"en\">）")
elif '<title data-i18n="doc.title">SPHEREx Sky View · MoveScope</title>' not in html:
    fail("index.html 英文默认标题不是 'SPHEREx Sky View · MoveScope'")
else:
    ok("English default: <html lang=\"en\"> + 'SPHEREx Sky View · MoveScope'")

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
        fail("时间窗口默认值不是 365 days")
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

if "analysis-layer filter range" not in i18n_js or "not the data epoch" not in i18n_js:
    fail("EN 文案没有明确“时间窗口 = analysis-layer filter range”且区别于 data epoch")
elif "rango de filtrado de la capa de análisis" not in i18n_js or "época de datos" not in i18n_js:
    fail("ES 文案没有明确“时间窗口 = rango de filtrado de la capa de análisis”且区别于 época de datos")
else:
    ok("EN/ES: 时间窗口 = analysis-layer filter range（≠ data epoch）")

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

if "Normal / Cascade-residual / Track" not in i18n_js:
    fail("EN 文案没有明确进入 MoveScope 后切换 Normal / Cascade-residual / Track")
elif "Normal / Cascada-residual / Trayectoria" not in i18n_js:
    fail("ES 文案没有明确进入 MoveScope 后切换 Normal / Cascada-residual / Trayectoria")
else:
    ok("EN/ES: 进入 MoveScope 后切换 Normal/Cascade-residual/Track")

if "not the data epoch" not in i18n_js or "no la época de datos" not in i18n_js:
    fail("EN/ES 分析提示没有区分分析窗口与 data epoch")
else:
    ok("EN/ES 分析提示区分分析窗口与 data epoch")

readme_needs = ("Analysis time window", "t0", "t1", "from=view", "analysis-layer filter range", "Normal / Cascade-residual / Track")
missing_readme = [s for s in readme_needs if s not in readme]
if missing_readme:
    fail(f"README 缺分析窗口/交接说明：{missing_readme}")
else:
    ok("README 记录分析窗口与单 MoveScope handoff")

# ---------------------------------------------------------------- 1d. 时间选择 / 播放 / tween
for tid in ("in-utc", "btn-utc-go", "t-start", "t-25", "t-mid", "t-75", "t-end"):
    if f'id="{tid}"' not in html:
        fail(f"index.html 缺时间控件/刻度 #{tid}")
if "UTC timeline" not in html or "Línea de tiempo UTC" not in i18n_js:
    fail("时间轴没有标注 UTC timeline (EN/ES)")
if "Playback = continuous time" not in html or "Reproducción = tiempo continuo" not in i18n_js:
    fail("页面没有标注 Playback = continuous time (EN/ES)")
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
    for key in ("UTC timeline", "Playback = continuous time", "0.15×", "wrap", "animate:false")
    if key not in readme
]
if missing_readme_time:
    fail(f"README 缺时间交互说明：{missing_readme_time}")
else:
    ok("README 同步 UTC 时间轴与连续播放")

# ---------------------------------------------------------------- 1e. i18n / EN-ES
lang_block = re.search(r'<div class="lang-switch"[\s\S]*?</div>', html)
if not lang_block:
    fail("index.html 缺 header EN/ES 选择器 #lang-switch")
else:
    lang_order = re.findall(r'data-lang="([a-z]{2})"', lang_block.group(0))
    if lang_order != ["en", "es"]:
        fail(f"header 语言按钮不是 EN/ES：{lang_order}")
    elif 'aria-pressed="true"' not in lang_block.group(0) or 'aria-pressed="false"' not in lang_block.group(0):
        fail("header 语言按钮缺 aria-pressed 状态")
    else:
        ok("header EN/ES selector (data-lang=en|es + aria-pressed)")

i18n_needles = {
    "default English": "export const DEFAULT_LANG = 'en'",
    "supported languages": "export const LANGS = ['en', 'es']",
    "storage key": "export const LANG_KEY = 'spherex.view.lang'",
    "URL precedence": "search.get('lang') ?? hash.get('lang')",
    "localStorage read": "localStorage.getItem(LANG_KEY)",
    "localStorage write": "localStorage.setItem(LANG_KEY, value)",
    "html lang": "document.documentElement.lang = lang",
}
missing_i18n = [name for name, needle in i18n_needles.items() if needle not in i18n_js]
if missing_i18n:
    fail(f"i18n.js 缺默认语言/URL/localStorage 逻辑：{missing_i18n}")
else:
    ok("i18n: default en + ?lang search→hash + localStorage spherex.view.lang")

if "p.set('lang', getLang())" not in main_js:
    fail("View URL 没有写回当前 lang")
else:
    ok("View URL writes lang (per-link language)")

if "document.querySelectorAll('.lang-btn[data-lang]')" not in main_js:
    fail("main.js 没有绑定 header EN/ES 按钮")
else:
    ok("main.js 绑定 header EN/ES 按钮")

switch_block = re.search(r"function switchLang\([\s\S]*?\n\}", main_js)
if not switch_block:
    fail("找不到 switchLang()")
else:
    switch_body = switch_block.group(0)
    forbidden = [x for x in ("commitTime(", "setRegion(", "state.mjd =", "state.playing =") if x in switch_body]
    if forbidden:
        fail(f"switchLang 修改了区域/时间/播放状态：{forbidden}")
    elif "writeUrl()" not in switch_body:
        fail("switchLang 没有写回 URL lang")
    else:
        ok("语言切换不改变区域/时相/播放，只重渲染并写回 lang")

# Compare EN/ES dictionaries through Node (pure module import; no network).
node_bin = subprocess.run(["bash", "-lc", "command -v node"], capture_output=True, text=True).stdout.strip()
if not node_bin:
    warn("node 不可用，跳过 EN/ES 字典完整性检查")
else:
    i18n_path = os.path.join(TOOL, "js", "i18n.js")
    from pathlib import Path as _Path

    node_script = """
import { DICTS, TARGET_NAMES, LANGS, DEFAULT_LANG } from %r;
process.stdout.write(JSON.stringify({ dicts: DICTS, targets: TARGET_NAMES, langs: LANGS, default: DEFAULT_LANG }));
""" % _Path(i18n_path).as_uri()
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
        fh.write(node_script)
        node_script_path = fh.name
    try:
        nr = subprocess.run([node_bin, node_script_path], capture_output=True, text=True, encoding="utf-8")
    finally:
        os.unlink(node_script_path)
    if nr.returncode != 0:
        fail(f"js/i18n.js 无法由 Node 导入：{nr.stderr.strip().splitlines()[-1] if nr.stderr else ''}")
    else:
        try:
            i18n_data = json.loads(nr.stdout)
        except json.JSONDecodeError:
            fail("js/i18n.js Node 输出不是 JSON")
        else:
            langs = i18n_data.get("langs")
            if langs != ["en", "es"] or i18n_data.get("default") != "en":
                fail(f"i18n 语言不是默认 EN + ES：langs={langs} default={i18n_data.get('default')}")
            else:
                ok("i18n languages: EN default + ES")

            en = i18n_data["dicts"]["en"]
            es = i18n_data["dicts"]["es"]
            en_keys, es_keys = set(en), set(es)
            if en_keys != es_keys:
                fail(f"EN/ES 字典键不一致：EN 缺 {sorted(es_keys - en_keys)}，ES 缺 {sorted(en_keys - es_keys)}")
            elif len(en_keys) < 70:
                fail(f"EN/ES 字典只有 {len(en_keys)} 个键，覆盖不足")
            else:
                ok(f"EN/ES dictionary key parity ({len(en_keys)} keys)")

            empty_keys = sorted(k for k, v in list(en.items()) + list(es.items()) if not isinstance(v, str) or not v.strip())
            if empty_keys:
                fail(f"EN/ES 字典有空值：{empty_keys[:8]}")
            else:
                ok("EN/ES dictionary values non-empty")

            cjk = re.compile(r"[\u4e00-\u9fff]")
            cjk_keys = sorted(k for k, v in list(en.items()) + list(es.items()) if cjk.search(v))
            if cjk_keys:
                fail(f"EN/ES UI 字符串仍含中文：{cjk_keys[:8]}")
            else:
                ok("no Chinese characters in EN/ES UI strings")

            ph = re.compile(r"\{(\w+)\}")
            ph_bad = sorted(k for k in en_keys if set(ph.findall(en[k])) != set(ph.findall(es[k])))
            if ph_bad:
                fail(f"EN/ES 占位符不一致：{ph_bad[:8]}")
            else:
                ok("EN/ES interpolation placeholders match")

            html_keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html))
            js_keys = set(re.findall(r"\btr\('([^']+)'", main_js))
            used_keys = html_keys | js_keys
            missing_keys = sorted(used_keys - en_keys)
            if missing_keys:
                fail(f"页面/JS 使用了字典中不存在的键：{missing_keys[:8]}")
            else:
                ok(f"all {len(used_keys)} data-i18n/tr keys exist in EN+ES")

            required_keys = {
                "field.ra", "field.dec", "field.fov", "unit.deg",
                "window.title", "window.days", "window.note", "time.axis", "time.utc.aria", "time.go",
                "time.playnote", "speed", "play.aria", "pause.aria", "tracknote.default",
                "panel.left.title", "panel.right.title", "kv.fov", "kv.site", "kv.lst",
                "hits.head", "hits.count", "hits.empty", "curated",
                "open.movescope", "copy.json", "copy.link", "movescope.target", "movescope.custom",
                "movescope.unreachable", "utc.invalid", "utc.clamped", "fatal.load", "fatal.webgl",
                "data.note", "loader",
            }
            missing_required = sorted(required_keys - en_keys)
            if missing_required:
                fail(f"覆盖不完整，缺关键 UI 文案：{missing_required}")
            else:
                ok("UI coverage: RA/Dec/FOV, window, UTC, playback, timeline, region, coverage, handoff/copy/errors, notes")

            exact = (
                en.get("panel.right.title") == "Selected region"
                and es.get("panel.right.title") == "Región seleccionada"
                and en.get("window.title") == "Analysis time window"
                and es.get("window.title") == "Ventana de tiempo de análisis"
                and en.get("time.axis") == "UTC timeline"
                and es.get("time.axis") == "Línea de tiempo UTC"
                and en.get("open.movescope") == "Open in MoveScope"
                and es.get("open.movescope") == "Abrir en MoveScope"
                and en.get("copy.link") == "Copy link"
                and es.get("copy.link") == "Copiar enlace"
                and en.get("utc.invalid") == "UTC time must use YYYY-MM-DD HH:MM (UTC)."
                and es.get("utc.invalid") == "La hora UTC debe usar el formato YYYY-MM-DD HH:MM (UTC)."
            )
            if not exact:
                fail("EN/ES 核心文案不完整或回退到英文")
            else:
                ok("EN/ES core labels exact (window, timeline, region, handoff, copy, UTC error)")

            targets_en = i18n_data["targets"]["en"]
            targets_es = i18n_data["targets"]["es"]
            target_ids = {"barnard", "epsind_b", "wise0855", "rw_aur", "r_dra", "m101", "campo_a"}
            if set(targets_en) != target_ids or set(targets_es) != target_ids:
                fail("target display-name dictionaries 不是 7 个目标")
            elif targets_en.get("campo_a") != "Field A (DR1)" or targets_es.get("campo_a") != "Campo A (DR1)":
                fail("target display name 没有本地化（campo_a / M101）")
            elif "视场" in " ".join(targets_en.values()) or "视场" in " ".join(targets_es.values()):
                fail("target display names 仍含中文")
            else:
                ok("7 target display names localized (Field A / Campo A)")

            caveat_en = "This does not mean it was never observed"
            caveat_es = "Esto no significa que nunca se haya observado"
            if caveat_en not in en.get("hits.empty", "") or caveat_es not in es.get("hits.empty", ""):
                fail("英文/西语没有保留“无命中≠从未观测”的 caveat")
            elif "observing weeks" not in en.get("hits.empty", "") or "semanas de observación" not in es.get("hits.empty", ""):
                fail("caveat 没有说明本地索引只是观测周子集")
            else:
                ok("caveat semantics preserved EN/ES (subset of 23 weeks; not never observed)")

            all_ui = "\n".join(en.values()) + "\n" + "\n".join(es.values())
            if NOVELTY_RE.search(all_ui) or NOVELTY_RE.search(readme):
                fail("EN/ES UI 或 README 出现 novelty/first-detection 语言")
            else:
                ok("no novelty/first-detection language in EN/ES UI or README")

lang_doc_needs = ("Language / Idioma", "?lang=en|es", LANG_KEY, "English by default", "Spanish")
missing_lang_doc = [x for x in lang_doc_needs if x not in readme]
if missing_lang_doc:
    fail(f"README 缺 EN/ES 语言说明：{missing_lang_doc}")
else:
    ok("README documents English default + ?lang=en|es + localStorage")

if not i18n_doc:
    fail("缺 docs/i18n-en-es.md 本地化报告")
else:
    doc_needs = (
        "English is the default and the primary language",
        "?lang=en|es",
        LANG_KEY,
        "This does not mean it was never observed",
        "Esto no significa que nunca se haya observado",
        "Normal / Cascade-residual / Track",
        "Normal / Cascada-residual / Trayectoria",
        "Field A (DR1)",
        "Campo A (DR1)",
    )
    missing_doc = [x for x in doc_needs if x not in i18n_doc]
    if missing_doc:
        fail(f"docs/i18n-en-es.md 缺 EN/ES 断言/证据：{missing_doc}")
    else:
        ok("docs/i18n-en-es.md covers EN/ES strings, modes and caveat semantics")

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
    "campo_a": "Field A",
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

for s in ("5,893 frames / 5,562 exposures", "23 observing weeks", "5,893 frames"):
    if s not in readme:
        fail(f"README 缺当前快照口径：{s!r}")
if OLD_README in readme or "15 observing weeks" in readme:
    fail("README 仍含旧快照口径（旧曝光数 / 15 observing weeks）")
else:
    ok("README snapshot 5,893 frames / 5,562 exposures / 23 observing weeks")

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
    elif "lang" in body:
        fail("moveScopeUrl 不应传 lang（语言只属于 View）")
    else:
        ok("MoveScope URL：全精度 ra/dec/fov + t0/t1 + from=view，无 mode/target/lang")

# ---------------------------------------------------------------- 6. 文案
all_text = html + main_js + i18n_js + readme + i18n_doc
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
    for f in ("main.js", "globe.js", "skymap.js", "astro.js", "i18n.js"):
        r = subprocess.run([node, "--check", os.path.join(TOOL, "js", f)], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"node --check {f} 失败：{r.stderr.strip().splitlines()[-1] if r.stderr else ''}")
    if not any("node --check" in f for f in fails):
        ok("node --check 5/5")
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
