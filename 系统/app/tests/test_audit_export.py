"""服务端批量审计导出：attachment CSV（候选展开）/JSON（嵌套 candidates）。"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core import audit_export  # noqa: E402


def _source(sid, **over):
    out = {"source_id": sid, "epoch_id": "e0", "frame": 0, "ra": 269.4, "dec": 4.7,
           "mjd": 60916.6, "sigma_arcsec": 0.5}
    out.update(over)
    return out


def _result(sid, status, candidates=None, **over):
    cands = candidates or []
    out = {
        "source_id": sid,
        "detection_id": sid,
        "status": status,
        "label_zh": "Gaia DR3 已知" if status == "known_star" else (
            "未收录候选（未经确认）" if status == "uncatalogued_candidate" else "多候选 ambiguous"
        ),
        "catalog_id": "111" if status == "known_star" else None,
        "release": "gaia-dr3",
        "separation_arcsec": 0.5,
        "ruwe": 1.08 if status == "known_star" else None,
        "n_candidates": len(cands),
        "candidates": cands,
        "reason": "SINGLE_CATALOG_MATCH" if status == "known_star" else ("MULTIPLE_CANDIDATES" if status == "ambiguous" else "NO_CATALOG_MATCH"),
        "quality": ["Q"],
        "caveats": ["c"],
        "input": {"ra": 269.4, "dec": 4.7, "mjd": 60916.6, "sigma_arcsec": 0.5},
        "discovery_claim": False,
    }
    out.update(over)
    return out


def _cand(cid, sep, **over):
    out = {"catalog": "gaia", "catalog_id": cid, "release": "gaia-dr3",
           "separation_arcsec": sep, "ruwe": 1.1, "jmag": 5.2, "w1": 4.1}
    out.update(over)
    return out


class AuditPureTest(unittest.TestCase):
    def test_rows_expand_ambiguous(self):
        sources = [_source("a"), _source("b"), _source("c")]
        results = [
            _result("a", "known_star", [_cand("111", 0.5)], photometry={"jmag": 5.2}),
            _result("b", "ambiguous", [_cand("222", 0.4), _cand("333", 0.8)]),
            _result("c", "uncatalogued_candidate", []),
        ]
        rows = audit_export.build_rows(sources, results)
        self.assertEqual(len(rows), 4)  # known 1 + ambiguous 2 + no_match 1
        self.assertEqual([r["candidate_index"] for r in rows], [0, 0, 1, None])
        self.assertIsNone(rows[1]["catalog_id"])  # ambiguous source-level ID empty
        self.assertEqual(rows[1]["candidate_id"], "222")
        self.assertEqual(rows[2]["candidate_id"], "333")
        csv_text = audit_export.to_csv(sources, results)
        self.assertEqual(csv_text.charCodeAt(0) if hasattr(csv_text, "charCodeAt") else ord(csv_text[0]), 0xFEFF)
        self.assertIn("candidate_index,candidate_catalog", csv_text)
        self.assertIn("candidate_id", csv_text)
        payload = audit_export.json_payload(sources, results, kind="A")
        self.assertEqual(payload["byId"]["b"]["status"], "ambiguous")
        self.assertEqual(len(payload["byId"]["b"]["candidates"]), 2)
        self.assertEqual(payload["n_rows"], 4)


class AuditHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(timeout=5)

    def _post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _batch(self):
        sources = [_source("a"), _source("b"), _source("c")]
        counts = {"known_star": 1, "ambiguous": 1, "uncatalogued_candidate": 0,
                  "insufficient_data": 0, "catalog_unavailable": 0, "no_match": 1}
        return {
            "n": 3, "counts": counts,
            "catalog_status": "ok", "discovery_claim": False,
            "disclaimer": "候选不是新天体",
            "results": [
                _result("a", "known_star", [_cand("111", 0.5)], photometry={"jmag": 5.2}),
                _result("b", "ambiguous", [_cand("222", 0.4), _cand("333", 0.8)]),
                _result("c", "uncatalogued_candidate", []),
            ],
        }

    def test_csv_attachment_expands_candidates(self):
        with mock.patch.object(server, "run_identify_batch", return_value=self._batch()):
            status, headers, body = self._post(
                "/api/identify/batch/export?format=csv&kind=A&target=Barnard", {"sources": [_source("a"), _source("b"), _source("c")]}
            )
        self.assertEqual(status, 200, body)
        self.assertIn("text/csv", headers.get("Content-Type", ""))
        self.assertIn("attachment;", headers.get("Content-Disposition", ""))
        text = body.decode("utf-8")
        self.assertTrue(text.startswith("\ufeff"))
        self.assertIn("candidate_index", text)
        self.assertEqual(len(text.replace("\ufeff", "").strip().split("\n")), 5)  # header + 4 rows
        self.assertIn("222", text)
        self.assertIn("333", text)

    def test_json_attachment_has_sources_candidates_byid(self):
        with mock.patch.object(server, "run_identify_batch", return_value=self._batch()):
            status, headers, body = self._post(
                "/api/identify/batch/export?format=json&kind=C&target=field", {"sources": [_source("a"), _source("b"), _source("c")]}
            )
        self.assertEqual(status, 200, body)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIn("attachment;", headers.get("Content-Disposition", ""))
        d = json.loads(body)
        self.assertEqual(d["kind"], "C")
        self.assertEqual(d["n_sources"], 3)
        self.assertEqual(d["n_rows"], 4)
        self.assertIn("byId", d)
        self.assertEqual(len(d["byId"]["b"]["candidates"]), 2)
        self.assertEqual(len(d["sources"]), 3)

    def test_invalid_format_400(self):
        status, headers, body = self._post(
            "/api/identify/batch/export?format=txt", {"sources": [_source("a")]}
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "INVALID_FORMAT")

    def test_b_unavailable_attachment_still_has_rows(self):
        unavailable = {
            "n": 1,
            "counts": {"known_star": 0, "ambiguous": 0, "uncatalogued_candidate": 0,
                       "insufficient_data": 0, "catalog_unavailable": 1, "no_match": 0},
            "catalog_status": "unavailable",
            "disclaimer": "星表不可用",
            "results": [_result("a", "catalog_unavailable", [], label_zh="星表不可用")],
        }
        with mock.patch.object(server, "run_identify_batch", return_value=unavailable):
            status, _headers, body = self._post(
                "/api/identify/batch/export?format=json&kind=A", {"sources": [_source("a")]}
            )
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["catalog_status"], "unavailable")
        self.assertEqual(d["n_rows"], 1)
        self.assertEqual(d["byId"]["a"]["status"], "catalog_unavailable")


if __name__ == "__main__":
    unittest.main()
