"""EN/ES i18n layer: static wiring, key completeness and runtime localisation.

No browser is required: the page files are checked statically and ``web/i18n.js``
is exercised with Node (the same UMD entry point the browser uses).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(APP, "web")
NODE = shutil.which("node")

PAGE_FILES = ("index.html", "cascade.html", "tracks.html")
UI_JS_FILES = (
    "app.js",
    "cascade.js",
    "tracks.js",
    "source_pick.js",
    "lock_note.js",
    "candidate_directory.js",
    "a_source_batch.js",
)


def _read(name: str) -> str:
    with open(os.path.join(WEB, name), encoding="utf-8") as fh:
        return fh.read()


def _strip_js_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


class I18nStaticTest(unittest.TestCase):
    def test_all_pages_are_english_primary_with_selector(self):
        for name in PAGE_FILES:
            html = _read(name)
            self.assertIn('<html lang="en">', html, name)
            self.assertIn('src="/static/i18n.js"', html, name)
            self.assertIn("data-lang-select", html, name)
            self.assertIn('id="langSelect"', html, name)
            self.assertIn('value="en"', html, name)
            self.assertIn('value="es"', html, name)
            self.assertIn("data-i18n", html, name)

    def test_html_has_no_chinese_ui_strings(self):
        for name in PAGE_FILES:
            html = _strip_html_comments(_read(name))
            self.assertFalse(_has_cjk(html), f"{name} still contains Chinese UI text")

    def test_ui_js_has_no_chinese_ui_strings(self):
        # i18n.js intentionally contains Chinese source text as translation input.
        # batch_export.js keeps the API's label_zh column for audit CSV/JSON data.
        for name in UI_JS_FILES:
            js = _strip_js_comments(_read(name))
            self.assertFalse(_has_cjk(js), f"{name} still contains Chinese UI literals")

    def test_css_has_no_chinese_content(self):
        css = _strip_js_comments(_read("style.css"))
        self.assertFalse(_has_cjk(css), "style.css still contains Chinese UI text")

    def test_i18n_layer_has_language_contract(self):
        js = _read("i18n.js")
        self.assertIn('const SUPPORTED_LANGS = ["en", "es"]', js)
        self.assertIn('const DEFAULT_LANG = "en"', js)
        self.assertIn('const STORAGE_KEY = "movescope.lang"', js)
        self.assertIn("URLSearchParams", js)
        self.assertIn('localStorage.getItem', js)
        self.assertIn('localStorage.setItem', js)
        self.assertIn('document.documentElement.lang', js)
        self.assertIn("data-i18n", js)

    def test_no_positive_discovery_semantics_in_pages(self):
        for name in PAGE_FILES:
            html = _read(name).lower()
            for phrase in ("new planet", "nuevo planeta", "confirmed discovery", "descubrimiento confirmado"):
                self.assertNotIn(phrase, html, f"{name}: {phrase}")


@unittest.skipUnless(NODE, "node not in PATH")
class I18nRuntimeTest(unittest.TestCase):
    def _run_node(self, script: str) -> dict:
        proc = subprocess.run(
            [NODE, "-e", script, os.path.join(WEB, "i18n.js"), WEB],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_all_keys_and_runtime_behaviour(self):
        script = r"""
const fs = require("fs");
const path = require("path");
const I = require(process.argv[1]);
const web = process.argv[2];

const missing = [];
const empty = [];
for (const [key, pair] of Object.entries(I.STRINGS)) {
  if (!Array.isArray(pair) || pair.length !== 2) { missing.push(key + " (shape)"); continue; }
  if (!String(pair[0] || "").trim() || !String(pair[1] || "").trim()) empty.push(key);
}

const pageFiles = ["index.html", "cascade.html", "tracks.html"];
const jsFiles = ["app.js", "cascade.js", "tracks.js"];
const used = new Set();
for (const file of [...pageFiles, ...jsFiles]) {
  const text = fs.readFileSync(path.join(web, file), "utf8");
  for (const m of text.matchAll(/data-i18n(?:-html|-title|-placeholder|-aria-label)?="([^"]+)"/g)) used.add(m[1]);
  for (const m of text.matchAll(/(?:tr|I18n\.t)\(["'`]([a-zA-Z0-9_.]+)["'`]/g)) used.add(m[1]);
}
const dynamicFamilies = {
  "badge.mode.": ["real", "hybrid", "synthetic", "sample"],
  "layer.kind.": ["l1", "masked", "residual", "track", "node"],
  "anyfield.status.": ["ok", "no_coverage", "index_incomplete", "unavailable", "unknown"],
  "catalog.badge.": ["known_star", "knownGaia", "ambiguous", "uncatalogued_candidate", "catalog_unavailable", "insufficient_data", "unknown"],
  "tracks.level.": ["D0", "D1", "D2", "D3", "D4"],
  "qualityFlag.": ["SATURATED_SOURCE", "FEW_EPOCHS", "SHORT_BASELINE", "LARGE_CENTROID_ERROR", "CATALOG_PRIOR_MOTION", "MEASUREMENT_NOT_SIGNIFICANT", "IMPLAUSIBLE_PM", "TIME_WINDOW_EMPTY", "FETCH_FAILED", "SAMPLE_MODE_NO_TARGET_MEASUREMENT", "TARGET_NOT_DETECTED", "TARGET_OUT_OF_FRAME", "MULTI_BAND"],
};
for (const [prefix, values] of Object.entries(dynamicFamilies)) {
  for (const value of values) used.add(prefix + value);
}
for (const key of used) if (!I.hasKey(key)) missing.push(key);

const resolve = {
  urlEs: I.resolveLang("?lang=es", "en"),
  urlEn: I.resolveLang("?lang=en", "es"),
  storedEs: I.resolveLang("", "es"),
  defaultEn: I.resolveLang("", ""),
  invalidFallsBackToStored: I.resolveLang("?lang=fr", "es"),
};
I.setLang("es", { persist: false, updateUrl: false });
const es = {
  lang: I.lang,
  label: I.t("header.expert.label"),
  title: I.t("page.home.title"),
  fallback: I.t("catalog.disclaimer"),
};
const spanishCjk = /[\u4e00-\u9fff]/.test(JSON.stringify(es));

const caveatFixtures = [
  "星表服务不可用，无法判断已知/未知。",
  "目标没有被探测到，无法做星表比对。",
  "匹配半径内有多个星表候选；系统不自动选择最亮或最近的一个。",
  "最近星表候选偏离 0.50″，超过 1.00″ 门限，不能据此给 known/ambiguous。",
  "Gaia RUWE=2.50 > 2.0（项目阈值）；5 参数解可能受伴星/污染影响，known 降级为 ambiguous。",
  "位置序列与流量相关（Δχ²=4.2）：可能有邻星混叠，运动结论保留，但质心可能受影响。",
  "拟合自行 40.0″/yr 超过恒星先验上限（30″/yr），极可能是跨历元关联错误，结论降级。",
  "持久化未完成（星表不可用）；本次识别结果仍可用，persisted=false。",
  "目录有 4 期真实历元，但切片取数全部失败（0/4，可能网络或 IRSA 不可用）；未做真实判定，以下画面仅为 SIM 演示，请恢复网络后重试。",
];
const caveatOut = caveatFixtures.map((x) => I.serverText(x));
const caveatCjk = caveatOut.filter((x) => /[\u4e00-\u9fff]/.test(x));

const resultFixtures = [
  {class: "L", moving: true, detected_epochs: 4, quality: [], fingerprint: {n_epochs: 4, baseline_days: 100, displacement_arcsec: 1.2, displacement_err_arcsec: 0.1, pa_disp_deg: 45, mu_mas_yr: 5000, mu_px_yr: 0.8, amp_mag: 0}, evidence: [], caveats: []},
  {class: "V", moving: false, variable: true, detected_epochs: 3, quality: [], fingerprint: {n_epochs: 3, baseline_days: 30, displacement_arcsec: 0.1, displacement_err_arcsec: 0.2, mu_mas_yr: 1, mu_px_yr: 0.1, amp_mag: 0.5, period_d: 5}, evidence: [], caveats: []},
  {class: "S", moving: false, detected_epochs: 4, quality: [], fingerprint: {n_epochs: 4, baseline_days: 100, displacement_arcsec: 0.7, displacement_err_arcsec: 0.5, mu_mas_yr: 1, mu_px_yr: 0.1, amp_mag: 0}, evidence: [], caveats: []},
  {class: "A", moving: true, detected_epochs: 5, quality: [], fingerprint: {n_epochs: 5, baseline_days: 100, displacement_arcsec: 2, displacement_err_arcsec: 0.1, pa_disp_deg: 30, mu_mas_yr: 5000, mu_px_yr: 0.8, amp_mag: 0, accel_sig: 4}, evidence: [], caveats: []},
  {class: "U", moving: false, detected_epochs: 0, quality: ["TARGET_NOT_DETECTED"], fingerprint: {n_epochs: 0, baseline_days: 0}, evidence: ["在预测位置附近没有达到 3σ 的探测"], caveats: []},
  {class: "U", moving: false, detected_epochs: 2, quality: [], fingerprint: {n_epochs: 2, baseline_days: 5}, evidence: [], caveats: []},
  {class: "U", moving: false, detected_epochs: 0, quality: ["TIME_WINDOW_EMPTY"], fingerprint: {n_epochs: 0, baseline_days: 0}, evidence: [], caveats: ["时间窗口为空；不把空结果当成“没有天体”。"]},
  {class: "U", moving: false, detected_epochs: 0, quality: ["SAMPLE_MODE_NO_TARGET_MEASUREMENT"], fingerprint: {n_epochs: 0, baseline_days: 0}, evidence: [], caveats: ["样本模式不产生运动结论。"]},
];
const resultCjk = [];
for (const lang of ["en", "es"]) {
  I.setLang(lang, { persist: false, updateUrl: false });
  for (const fixture of resultFixtures) {
    const view = I.resultView(fixture);
    if (/[\u4e00-\u9fff]/.test(JSON.stringify(view))) resultCjk.push(lang + ":" + fixture.class);
  }
  if (/[\u4e00-\u9fff]/.test(I.modeNote({mode: "synthetic"}))) resultCjk.push(lang + ":modeNote");
  if (/[\u4e00-\u9fff]/.test(I.errorText({code: "DB_UNAVAILABLE", message: "数据库不可用"}))) resultCjk.push(lang + ":errorText");
}

I.setLang("en", { persist: false, updateUrl: false });
const all = JSON.stringify(I.STRINGS).toLowerCase();
const banned = ["new planet", "nuevo planeta", "confirmed discovery", "descubrimiento confirmado", "first discovery", "primer descubrimiento"];
const positiveCjk = banned.filter((x) => all.includes(x));

console.log(JSON.stringify({
  missing,
  empty,
  resolve,
  es,
  spanishCjk,
  caveatCjk,
  resultCjk,
  banned: positiveCjk,
  keys: Object.keys(I.STRINGS).length,
}));
"""
        out = self._run_node(script)
        self.assertEqual(out["missing"], [])
        self.assertEqual(out["empty"], [])
        self.assertEqual(out["resolve"], {
            "urlEs": "es",
            "urlEn": "en",
            "storedEs": "es",
            "defaultEn": "en",
            "invalidFallsBackToStored": "es",
        })
        self.assertEqual(out["es"]["lang"], "es")
        self.assertEqual(out["es"]["label"], "Modo investigador")
        self.assertFalse(out["spanishCjk"])
        self.assertEqual(out["caveatCjk"], [])
        self.assertEqual(out["resultCjk"], [])
        self.assertEqual(out["banned"], [])
        self.assertGreater(out["keys"], 300)


if __name__ == "__main__":
    unittest.main()
