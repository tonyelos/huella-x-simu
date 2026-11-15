"""A 源表批量识别：纯函数 + HTML/JS 静态断言。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")


class ASourceBatchJsTest(unittest.TestCase):
    @unittest.skipUnless(NODE, "node 不在 PATH")
    def test_build_chunks_keys_sigma_merge_badges(self):
        js = os.path.join(APP, "web", "a_source_batch.js")
        script = r"""
const ab = require(process.argv[1]);
const sources = [];
for (let i = 0; i < 215; i++) {
  sources.push({epoch_id: "e" + (i % 4), frame: i % 4, x: i + 1, y: i + 2,
                ra: 10 + i, dec: 20, sigma_x: 0.03, sigma_y: 0.04, quality: []});
}
const epochs = [{mjd: 100}, {mjd: 200}, {mjd: 300}, {mjd: 400}];
const built = ab.buildBatchSources(sources, epochs);
const batches = ab.chunks(built, 200);
const first = built[0];
const out = {
  n: built.length,
  batchSizes: batches.map(b => b.length),
  key: first.source_id,
  mjd: first.mjd,
  sigma: first.sigma_arcsec,
  merged: ab.mergeCounts({known_star: 30, no_match: 10}, {known_star: 1, ambiguous: 6, no_match: 1}),
  badges: {
    known: ab.badgeStatus({status: "known_star"}),
    ambiguous: ab.badgeStatus({status: "ambiguous"}),
    no: ab.badgeStatus({status: "uncatalogued_candidate"}),
    insuff: ab.badgeStatus({status: "insufficient_data"}),
    unavail: ab.badgeStatus({status: "catalog_unavailable"}),
    noLabel: ab.badgeLabel("no_match"),
  },
};
console.log(JSON.stringify(out));
"""
        proc = subprocess.run([NODE, "-e", script, js], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["n"], 215)
        self.assertEqual(out["batchSizes"], [200, 15])
        self.assertEqual(out["key"], "e0:1:2")
        self.assertEqual(out["mjd"], 100)
        self.assertAlmostEqual(out["sigma"], 0.3075, places=4)
        self.assertEqual(out["merged"]["known_star"], 31)
        self.assertEqual(out["merged"]["ambiguous"], 6)
        self.assertEqual(out["merged"]["no_match"], 11)
        self.assertEqual(out["badges"]["known"], "known")
        self.assertEqual(out["badges"]["ambiguous"], "ambiguous")
        self.assertEqual(out["badges"]["no"], "no_match")
        self.assertEqual(out["badges"]["insuff"], "insufficient")
        self.assertEqual(out["badges"]["unavail"], "unavailable")
        self.assertEqual(out["badges"]["noLabel"], "no_match")


class ASourceBatchUiStaticTest(unittest.TestCase):
    def test_panel_and_batch_wiring(self):
        with open(os.path.join(APP, "web", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(APP, "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("aSourcePanel", html)
        self.assertIn("A 检测源（本次分析）", html)
        self.assertIn("batchASourcesBtn", html)
        self.assertIn("批量识别 A 源", html)
        self.assertIn("a_source_batch.js", html)
        self.assertIn("/api/identify/batch", js)
        self.assertIn("buildBatchSources", js)
        self.assertIn("chunks(items, 200)", js)
        self.assertIn("A 源表和已有 badge 保持不变", js)
        self.assertIn("no_match = 未收录候选", html)
        self.assertIn("<th>epoch</th><th>S/N</th><th>RA / Dec</th><th>catalog</th>", html)
        self.assertIn("a-coord", js)
        self.assertIn('</summary>\n        <div class="a-source-actions">', html)
        self.assertIn('id="anyfieldBatchActions"', html)
        with open(os.path.join(APP, "web", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".a-source-table { width: 100%; max-width: 100%; table-layout: fixed;", css)
        self.assertIn(".a-source-actions { display: flex; flex-wrap: wrap;", css)
        self.assertIn(".anyfield-actions { margin-top: 6px; display: flex; flex-wrap: wrap;", css)
        self.assertIn("grid-template-columns: 46px minmax(0, 1fr) 70px", css)
        self.assertIn("width: 31%", css)
        self.assertNotIn("新恒星", html + js)
        self.assertNotIn("新行星", html + js)


if __name__ == "__main__":
    unittest.main()
