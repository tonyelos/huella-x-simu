"""批量审计导出：CSV/JSON 纯函数与 UI 静态断言。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")


class BatchExportJsTest(unittest.TestCase):
    @unittest.skipUnless(NODE, "node 不在 PATH")
    def test_csv_escape_ambiguous_no_match_and_counts(self):
        js = os.path.join(APP, "web", "batch_export.js")
        script = r"""
const be = require(process.argv[1]);
const aSources = [];
for (let i = 0; i < 215; i++) {
  aSources.push({epoch_id: "e" + (i % 4), frame: i % 4, x: i + 1, y: i + 2,
                 ra: 10 + i, dec: 20, sigma_x: 0.03, sigma_y: 0.04});
}
const byA = {};
for (let i = 0; i < 215; i++) {
  byA["e" + (i % 4) + ":" + (i + 1) + ":" + (i + 2)] = {
    source_id: "e" + (i % 4) + ":" + (i + 1) + ":" + (i + 2),
    status: i === 0 ? "known_star" : "uncatalogued_candidate",
    label_zh: i === 0 ? "Gaia DR3 已知" : "未收录候选（未经确认）",
    catalog_id: i === 0 ? "123" : null,
    release: "gaia-dr3",
    separation_arcsec: 0.5,
    ruwe: i === 0 ? 1.08 : null,
    n_candidates: i === 0 ? 1 : 0,
    photometry: i === 0 ? {jmag: 5.2, w1: 4.1} : {},
    quality: ["Q1", "Q2"],
    caveats: ["a, b", 'quote "x"', "line\nbreak"],
  };
}
const aRows = be.buildRows(aSources, byA, "a", [{mjd: 100}, {mjd: 200}, {mjd: 300}, {mjd: 400}]);
const aCsv = be.toCsv(aRows);
const cSources = [];
for (let i = 0; i < 111; i++) cSources.push({source_id: "c" + i, epoch_id: "e0", mjd: 100, ra: 1, dec: 2});
const cRows = be.buildRows(cSources, {}, "c", []);
const known = aRows[0];
const missing = aRows[1];
const out = {
  columns: be.COLUMNS,
  aCount: aRows.length,
  cCount: cRows.length,
  aLines: aCsv.replace(/^\ufeff/, "").trimEnd().split("\n").length,
  bom: aCsv.charCodeAt(0),
  known: {status: known.status, label: known.label_zh, cid: known.catalog_id,
          jmag: known.jmag, w1: known.w1, missH: known.hmag},
  noMatch: {status: missing.status, label: missing.label_zh, cid: missing.catalog_id, n: missing.n_candidates},
  quote: aCsv.includes('""x""'),
  comma: aCsv.includes('"a, b'),
  newline: aCsv.includes('line\nbreak'),
  jsonLen: JSON.parse(be.toJson(aRows)).length,
  name: be.downloadName("A", "Barnard X", "csv", new Date("2026-11-15T08:30:00Z")),
};
console.log(JSON.stringify(out));
"""
        proc = subprocess.run([NODE, "-e", script, js], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["columns"], ["source_id", "epoch_id", "frame", "ra", "dec", "mjd", "status", "label_zh", "catalog_id", "release", "separation_arcsec", "ruwe", "n_candidates", "jmag", "hmag", "kmag", "w1", "w2", "reason", "quality", "caveats"])
        self.assertEqual(out["aCount"], 215)
        self.assertEqual(out["cCount"], 111)
        self.assertGreaterEqual(out["aLines"], 216)
        self.assertEqual(out["bom"], 0xFEFF)
        self.assertEqual(out["known"]["status"], "known_star")
        self.assertEqual(out["known"]["cid"], "123")
        self.assertEqual(out["known"]["jmag"], 5.2)
        self.assertEqual(out["known"]["w1"], 4.1)
        self.assertIsNone(out["known"]["missH"])
        self.assertEqual(out["noMatch"]["status"], "uncatalogued_candidate")
        self.assertEqual(out["noMatch"]["label"], "未收录候选（未经确认）")
        self.assertIsNone(out["noMatch"]["cid"])
        self.assertEqual(out["noMatch"]["n"], 0)
        self.assertTrue(out["quote"])
        self.assertTrue(out["comma"])
        self.assertTrue(out["newline"])
        self.assertEqual(out["jsonLen"], 215)
        self.assertTrue(out["name"].startswith("movescope_A_batch_Barnard_X_20261115T083000Z.csv"))

    @unittest.skipUnless(NODE, "node 不在 PATH")
    def test_ambiguous_catalog_id_and_n_candidates(self):
        js = os.path.join(APP, "web", "batch_export.js")
        script = r"""
const be = require(process.argv[1]);
const rows = be.buildRows([{source_id: "c1", epoch_id: "e", mjd: 1}],
  {"c1": {status: "ambiguous", catalog_id: "SHOULD_NOT_USE", n_candidates: 2,
          candidates: [{release: "gaia-dr3"}, {release: "gaia-dr3"}]}}, "c", []);
console.log(JSON.stringify(rows[0]));
"""
        proc = subprocess.run([NODE, "-e", script, js], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        row = json.loads(proc.stdout)
        self.assertEqual(row["status"], "ambiguous")
        self.assertIsNone(row["catalog_id"])
        self.assertEqual(row["n_candidates"], 2)
        self.assertIsNone(row["ruwe"])


class BatchExportUiStaticTest(unittest.TestCase):
    def test_export_buttons_and_module(self):
        with open(os.path.join(APP, "web", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(APP, "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("exportASourcesBtn", html)
        self.assertIn("exportCSourcesBtn", html)
        self.assertIn("exportAJsonBtn", html)
        self.assertIn("exportCJsonBtn", html)
        self.assertIn("导出 CSV", html)
        self.assertIn("导出 JSON", html)
        self.assertIn("batch_export.js", html)
        self.assertIn("exportABatchCsv", js)
        self.assertIn("exportCBatchCsv", js)
        self.assertIn("toCsv", js)
        self.assertIn("/api/identify/batch/export", js)
        self.assertIn("exportBatchServer", js)
        self.assertIn("导出失败", js)
        self.assertIn("导出不改变表格/badge", js)
        self.assertNotIn("新恒星", html + js)
        self.assertNotIn("新行星", html + js)


if __name__ == "__main__":
    unittest.main()
