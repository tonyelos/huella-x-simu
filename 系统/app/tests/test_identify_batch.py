"""批量星表识别：CatalogClient.match_batch + /api/identify/batch + UI 静态断言。"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core.catalog import CatalogClient  # noqa: E402


def _cand(cid, sep):
    return {
        "catalog_id": cid,
        "sep_arcsec": sep,
        "ruwe": 1.08,
        "pmra": -801.55,
        "pmdec": 10362.39,
        "pmra_error": 0.03,
        "pmdec_error": 0.04,
    }


class _FakeB(BaseHTTPRequestHandler):
    mode = "ok"
    calls = []

    def log_message(self, fmt, *args):
        return

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        type(self).calls.append(payload)
        if self.path != "/api/v1/catalog/match":
            return self._send(404, {"error": "not found"})
        if type(self).mode == "unavailable":
            return self._send(503, {"reason": "CATALOG_UNAVAILABLE", "message": "down"})
        matches = [
            {"index": 0, "detection_id": "c1", "status": "single_candidate", "catalog_status": "ok",
             "candidates": [_cand("111", 1.0)]},
            {"index": 1, "detection_id": "c2", "status": "ambiguous", "catalog_status": "ok",
             "candidates": [_cand("222", 0.5), _cand("333", 0.8)]},
            {"index": 2, "detection_id": "c3", "status": "no_match", "catalog_status": "ok", "candidates": []},
            {"index": 3, "detection_id": "c4", "status": "single_candidate", "catalog_status": "ok",
             "candidates": [_cand("444", 20.16)]},
        ]
        return self._send(200, {"schema_version": "1.0", "catalog_status": "ok", "release": "gaia-dr3",
                                "matches": matches, "n_detections": 4})


_SOURCES = [
    {"source_id": "c1", "ra": 269.45, "dec": 4.69, "mjd": 60916.6, "sigma_arcsec": 0.5},
    {"source_id": "c2", "ra": 269.46, "dec": 4.70, "mjd": 60916.6, "sigma_arcsec": 0.5},
    {"source_id": "c3", "ra": 269.47, "dec": 4.71, "mjd": 60916.6, "sigma_arcsec": 0.5},
    {"source_id": "c4", "ra": 269.48, "dec": 4.72, "mjd": 60916.6, "sigma_arcsec": 0.5},
]


class _AppServer:
    @classmethod
    def setUpClass(cls):
        cls.b = ThreadingHTTPServer(("127.0.0.1", 0), _FakeB)
        cls.b_thread = threading.Thread(target=cls.b.serve_forever, daemon=True)
        cls.b_thread.start()
        cls.client = CatalogClient(base_url=f"http://127.0.0.1:{cls.b.server_address[1]}", timeout=2.0)
        cls.app = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.app.daemon_threads = True
        cls.app_thread = threading.Thread(target=cls.app.serve_forever, daemon=True)
        cls.app_thread.start()
        cls.base = f"http://127.0.0.1:{cls.app.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown(); cls.app.server_close(); cls.app_thread.join(timeout=5)
        cls.b.shutdown(); cls.b.server_close(); cls.b_thread.join(timeout=5)

    def setUp(self):
        _FakeB.mode = "ok"
        _FakeB.calls = []
        self.patcher = mock.patch.object(server, "CATALOG_CLIENT", self.client)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _post(self, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.base + "/api/identify/batch", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


class IdentifyBatchClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeB)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(timeout=5)

    def test_batch_client_maps_single_ambiguous_no_match(self):
        _FakeB.mode = "ok"
        out = CatalogClient(base_url=self.base, timeout=2.0).match_batch(_SOURCES)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n"], 4)
        self.assertEqual([r["match_status"] for r in out["results"]],
                         ["single_candidate", "ambiguous", "no_match", "single_candidate"])
        self.assertEqual(len(out["results"][0]["catalog"]["candidates"]), 1)
        self.assertEqual(len(out["results"][1]["catalog"]["candidates"]), 2)
        self.assertEqual(out["results"][2]["catalog"]["n_candidates"], 0)

    def test_batch_client_b_unavailable_is_structured(self):
        _FakeB.mode = "unavailable"
        out = CatalogClient(base_url=self.base, timeout=2.0).match_batch(_SOURCES)
        self.assertFalse(out["ok"])
        self.assertEqual(out["catalog_status"], "unavailable")
        self.assertEqual(out["n"], 4)
        self.assertTrue(all(r["catalog"]["catalog_status"] == "unavailable" for r in out["results"]))


class IdentifyBatchApiTest(_AppServer, unittest.TestCase):
    def test_batch_api_counts(self):
        status, body = self._post({"sources": _SOURCES, "catalogs": ["gaia"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["n"], 4)
        self.assertEqual(body["counts"]["known_star"], 1)
        self.assertEqual(body["counts"]["ambiguous"], 1)
        self.assertEqual(body["counts"]["no_match"], 1)
        self.assertEqual(body["counts"]["insufficient_data"], 1)  # 20.16" candidate
        self.assertEqual(body["counts"]["catalog_unavailable"], 0)
        self.assertFalse(body["discovery_claim"])
        self.assertEqual(body["results"][0]["status"], "known_star")
        self.assertEqual(body["results"][2]["status"], "uncatalogued_candidate")
        self.assertEqual(body["results"][3]["status"], "insufficient_data")
        self.assertEqual(body["results"][3]["reason"], "CATALOG_OFFSET_TOO_LARGE")
        self.assertEqual(body["results"][0]["source_id"], "c1")
        self.assertEqual(_FakeB.calls[0]["detections"][0]["detection_id"], "c1")

    def test_batch_api_b_unavailable_200(self):
        _FakeB.mode = "unavailable"
        status, body = self._post({"sources": _SOURCES[:2], "catalogs": ["gaia"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["counts"]["catalog_unavailable"], 2)
        self.assertTrue(all(r["status"] == "catalog_unavailable" for r in body["results"]))

    def test_batch_api_max_200_400(self):
        sources = [{"source_id": f"s{i}", "ra": 269.4, "dec": 4.7, "mjd": 60916.6,
                    "sigma_arcsec": 0.5} for i in range(201)]
        status, body = self._post({"sources": sources})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "TOO_MANY_SOURCES")
        self.assertEqual(_FakeB.calls, [])

    def test_batch_api_empty_sources(self):
        status, body = self._post({"sources": []})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["n"], 0)
        self.assertEqual(body["counts"]["no_match"], 0)


class BatchUiStaticTest(unittest.TestCase):
    def test_batch_ui_static(self):
        with open(os.path.join(APP, "web", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(APP, "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(APP, "web", "i18n.js"), encoding="utf-8") as fh:
            i18n = fh.read()
        self.assertIn("batchCSourcesBtn", html)
        self.assertIn("Batch identify C sources", html)
        self.assertIn("/api/identify/batch", js)
        self.assertIn("batchSummary", js)
        self.assertIn("no_match", js)
        self.assertIn("Uncatalogued candidate", i18n)
        self.assertIn("C-source list and existing results remain unchanged", i18n)
        for text in (html, js, i18n):
            self.assertNotIn("新恒星", text)
            self.assertNotIn("新行星", text)
            self.assertNotIn("new planet", text.lower())
            self.assertNotIn("confirmed discovery", text.lower())


if __name__ == "__main__":
    unittest.main()
