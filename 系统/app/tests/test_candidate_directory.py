"""统一候选目录：A/C 去重 + track 行 + counts/导出 + UI 静态。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")


class CandidateDirectoryJsTest(unittest.TestCase):
    @unittest.skipUnless(NODE, "node 不在 PATH")
    def test_merge_conflict_nomatch_unknown_tracks_and_exports(self):
        js = os.path.join(APP, "web", "candidate_directory.js")
        script = r"""
const cd = require(process.argv[1]);
const epochs = [{mjd: 100}, {mjd: 200}];
const a = [
  {epoch_id: "e0", frame: 0, x: 1, y: 2, ra: 10.1234564, dec: 20.0000004},
  {epoch_id: "e0", frame: 0, x: 9, y: 9, ra: 11.0, dec: 21.0},
  {epoch_id: "e1", frame: 1, x: 3, y: 4, ra: 12.0, dec: 22.0},
  {epoch_id: "e1", frame: 1, x: 5, y: 6, ra: 13.0, dec: 23.0},
];
const c = [
  {source_id: "c1", epoch_id: "e0", mjd: 100, ra: 10.1234564, dec: 20.0000004},
  {source_id: "c2", epoch_id: "e1", mjd: 200, ra: 12.0, dec: 22.0},
];
const aById = {
  "e0:1:2": {status: "known_star", catalog_id: "gaia-1", catalog: "gaia", caveats: ["same"]},
  "e0:9:9": {status: "ambiguous", catalog: "gaia", caveats: ["two"]},
  "e1:3:4": {status: "known_star", catalog_id: "gaia-3", catalog: "gaia"},
  // e1:5:6 no result -> unknown
};
const cById = {
  c1: {status: "known_star", catalog_id: "gaia-1", catalog: "gaia"},
  c2: {status: "uncatalogued_candidate", label_zh: "未收录候选（未经确认）", catalog: "gaia"},
};
const tracks = [
  {track_id: "trk-1", level: "D1", model: "linear", n_epochs: 3, ra: 10.1, dec: 20.0,
   catalog: "gaia", catalog_id: "gaia-1", caveats: ["cand"], nodes: [{}, {}, {}]},
];
const dir = cd.build({aSources: a, aById, cSources: c, cById, tracks, epochs});
const out = {
  counts: dir.counts,
  rows: dir.rows,
  csv: cd.toCsv(dir.rows),
  json: JSON.parse(cd.toJson(dir)),
  name: cd.downloadName("csv"),
};
console.log(JSON.stringify(out));
"""
        proc = subprocess.run([NODE, "-e", script, js], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        c = out["counts"]
        self.assertEqual(c["unique_sources"], 4)
        self.assertEqual(c["tracks"], 1)
        self.assertEqual(c["rows"], 5)
        self.assertEqual(c["known"], 1)
        self.assertEqual(c["ambiguous"], 1)
        self.assertEqual(c["no_match"], 0)
        self.assertEqual(c["unknown"], 1)
        self.assertEqual(c["conflict"], 1)
        # same mjd+rounded position A+C merged; track is one row with no node rows
        merged = next(r for r in out["rows"] if r["origins"] == "A;C")
        self.assertEqual(merged["origin"], "A")
        self.assertEqual(merged["source_id"], "e0:1:2")
        self.assertEqual(merged["status"], "known")
        track = next(r for r in out["rows"] if r["origin"] == "track")
        self.assertEqual(track["source_id"], "trk-1")
        self.assertEqual(track["n_epochs"], 3)
        self.assertEqual(track["status"], "D1")
        self.assertEqual(len([r for r in out["rows"] if r["origin"] == "track"]), 1)
        # conflict row has both statuses in caveat, no silent status
        conflict = next(r for r in out["rows"] if r["status"] == "conflict")
        self.assertIn("known", conflict["caveats"])
        self.assertIn("no_match", conflict["caveats"])
        self.assertIn("origin", out["csv"].split("\n")[0])
        self.assertTrue(out["csv"].startswith("\ufeff"))
        self.assertEqual(out["json"]["counts"]["rows"], 5)
        self.assertEqual(len(out["json"]["rows"]), 5)

    @unittest.skipUnless(NODE, "node 不在 PATH")
    def test_no_match_match_status_survives_partial_insufficient(self):
        js = os.path.join(APP, "web", "candidate_directory.js")
        script = r"""
const cd = require(process.argv[1]);
const src=[{source_id:"c1",epoch_id:"e",mjd:1,ra:1,dec:2},{source_id:"c2",epoch_id:"e",mjd:1,ra:3,dec:4}];
const dir=cd.build({cSources:src,cById:{
  c1:{status:"insufficient_data",match_status:"no_match",label_zh:"数据不足"},
  c2:{status:"insufficient_data",match_status:"single_candidate",label_zh:"数据不足"}
}});
console.log(JSON.stringify({counts:dir.counts,rows:dir.rows.map(r=>({id:r.source_id,status:r.status}))}));
"""
        proc = subprocess.run([NODE, "-e", script, js], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["counts"]["no_match"], 1)
        self.assertEqual(out["counts"]["insufficient"], 1)
        self.assertEqual(out["rows"][0]["status"], "no_match")
        self.assertEqual(out["rows"][1]["status"], "insufficient")

    @unittest.skipUnless(NODE, "node 不在 PATH")
    def test_no_match_and_unknown_have_distinct_status(self):
        js = os.path.join(APP, "web", "candidate_directory.js")
        script = r"""
const cd = require(process.argv[1]);
const src=[{source_id:"c1",epoch_id:"e",mjd:1,ra:1,dec:2},{source_id:"c2",epoch_id:"e",mjd:1,ra:3,dec:4}];
const dir=cd.build({cSources:src,cById:{c1:{status:"uncatalogued_candidate",label_zh:"未收录候选（未经确认）"}}});
console.log(JSON.stringify({counts:dir.counts,rows:dir.rows.map(r=>({id:r.source_id,status:r.status,label:r.status}))}));
"""
        proc = subprocess.run([NODE, "-e", script, js], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["counts"]["no_match"], 1)
        self.assertEqual(out["counts"]["unknown"], 1)
        self.assertNotEqual(out["rows"][0]["status"], out["rows"][1]["status"])


class CandidateDirectoryUiStaticTest(unittest.TestCase):
    def test_directory_ui_wiring(self):
        web = os.path.join(APP, "web")
        with open(os.path.join(web, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(web, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("candidateDirectoryBtn", html)
        self.assertIn("统一候选目录", html)
        self.assertIn("candidateDirectoryPanel", html)
        self.assertIn("exportDirectoryCsvBtn", html)
        self.assertIn("exportDirectoryJsonBtn", html)
        self.assertIn("候选目录不是发现目录", html)
        self.assertIn("track 行不是新源", html)
        self.assertIn("candidate_directory.js", html)
        self.assertIn("/api/tracks", js)
        self.assertIn("openCandidateDirectory", js)
        self.assertIn("CandidateDirectory.build", js)
        self.assertNotIn("新恒星", html + js)
        self.assertNotIn("新行星", html + js)


if __name__ == "__main__":
    unittest.main()
