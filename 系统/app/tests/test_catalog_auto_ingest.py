"""B 星表 ingest 客户端能力：fake B server，不碰真实 DB/TAP。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core.catalog import CatalogClient  # noqa: E402


class _FakeB(BaseHTTPRequestHandler):
    mode = "ok"
    match_calls = 0
    ingest_calls = 0
    last_ingest = None

    def log_message(self, fmt, *args):
        return

    def _send(self, status, body):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if self.path == "/api/v1/catalog/match":
            type(self).match_calls += 1
            if type(self).mode == "ingest_fail":
                return self._send(200, {"matches": [{"catalog_status": "partial", "candidates": []}]})
            if type(self).match_calls == 1:
                if type(self).mode == "no_cached_field":
                    return self._send(
                        200,
                        {
                            "matches": [
                                {
                                    "catalog_status": "partial",
                                    "candidates": [],
                                    "coverage": {"gaia-dr3": {"field_id": None}},
                                }
                            ]
                        },
                    )
                return self._send(200, {"matches": [{"catalog_status": "partial", "candidates": []}]})
            return self._send(
                200,
                {
                    "matches": [
                        {
                            "catalog_status": "ok",
                            "release": "gaia-dr3",
                            "candidates": [
                                {
                                    "catalog_id": "4472832130942575872",
                                    "sep_arcsec": 0.42,
                                    "ruwe": 1.08,
                                    "pmra": -801.5,
                                    "pmdec": 10362.4,
                                    "pmra_error": 0.03,
                                    "pmdec_error": 0.04,
                                }
                            ],
                        }
                    ],
                    "star_schema_version": "test",
                },
            )
        if self.path == "/api/v1/catalog/ingest":
            type(self).ingest_calls += 1
            type(self).last_ingest = payload
            if type(self).mode == "ingest_fail":
                return self._send(502, {"ok": False, "reason": "UPSTREAM_TAP_ERROR", "message": "TAP down"})
            if type(self).mode == "ingest_timeout":
                time.sleep(0.3)
            return self._send(
                200,
                {
                    "schema_version": "1.0",
                    "ok": True,
                    "catalog": "gaia",
                    "release": "gaia-dr3",
                    "field_id": "auto-test-field",
                    "counts": {"n_sources": 10, "n_new": 10},
                    "provenance": "tap",
                    "complete": True,
                    "warnings": [],
                },
            )
        return self._send(404, {"error": "not found"})


class CatalogAutoIngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeB)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)

    def setUp(self):
        _FakeB.mode = "ok"
        _FakeB.match_calls = 0
        _FakeB.ingest_calls = 0
        _FakeB.last_ingest = None

    def _client(self):
        return CatalogClient(base_url=self.base, timeout=2.0, ingest_timeout=2.0)

    def test_auto_ingest_partial_then_match_ok(self):
        with mock.patch.dict(os.environ, {"CATALOG_AUTO_INGEST": "1", "MOVESCOPE_CATALOG_INGEST_TIMEOUT": "5"}):
            result = self._client().match(ra=269.45, dec=4.69, mjd=60916.6)
        self.assertEqual(result["catalog_status"], "ok")
        self.assertEqual(result["n_candidates"], 1)
        self.assertEqual(_FakeB.match_calls, 2)
        self.assertEqual(_FakeB.ingest_calls, 1)
        self.assertTrue(result["auto_ingest"]["ok"])
        self.assertEqual(result["auto_ingest"]["field_id"], "auto-test-field")
        self.assertEqual(_FakeB.last_ingest["radius_deg"], 0.25)
        self.assertEqual(_FakeB.last_ingest["catalog"], "gaia")

    def test_no_cached_field_also_triggers_ingest(self):
        _FakeB.mode = "no_cached_field"
        with mock.patch.dict(os.environ, {"CATALOG_AUTO_INGEST": "1"}):
            result = self._client().match(ra=269.45, dec=4.69, mjd=60916.6)
        self.assertEqual(result["catalog_status"], "ok")
        self.assertEqual(_FakeB.match_calls, 2)
        self.assertEqual(_FakeB.ingest_calls, 1)

    def test_auto_off_only_calls_match_once(self):
        with mock.patch.dict(os.environ, {"CATALOG_AUTO_INGEST": "0"}):
            result = self._client().match(ra=269.45, dec=4.69, mjd=60916.6)
        self.assertEqual(result["catalog_status"], "partial")
        self.assertEqual(_FakeB.match_calls, 1)
        self.assertEqual(_FakeB.ingest_calls, 0)
        self.assertNotIn("auto_ingest", result)

    def test_ingest_failure_keeps_original_status(self):
        _FakeB.mode = "ingest_fail"
        with mock.patch.dict(os.environ, {"CATALOG_AUTO_INGEST": "1"}):
            result = self._client().match(ra=269.45, dec=4.69, mjd=60916.6)
        self.assertEqual(result["catalog_status"], "partial")
        self.assertEqual(result["n_candidates"], 0)
        self.assertEqual(_FakeB.match_calls, 1)
        self.assertEqual(_FakeB.ingest_calls, 1)
        self.assertFalse(result["auto_ingest"]["ok"])
        self.assertEqual(result["auto_ingest"]["reason"], "UPSTREAM_TAP_ERROR")

    def test_ingest_timeout_keeps_original_status(self):
        _FakeB.mode = "ingest_timeout"
        with mock.patch.dict(os.environ, {"CATALOG_AUTO_INGEST": "1"}):
            result = CatalogClient(base_url=self.base, timeout=2.0, ingest_timeout=0.05).match(
                ra=269.45, dec=4.69, mjd=60916.6
            )
        self.assertEqual(result["catalog_status"], "partial")
        self.assertEqual(_FakeB.match_calls, 1)
        self.assertEqual(_FakeB.ingest_calls, 1)
        self.assertFalse(result["auto_ingest"]["ok"])
        self.assertEqual(result["auto_ingest"]["reason"], "INGEST_UNREACHABLE")

    def test_ingest_structured_http_failure(self):
        _FakeB.mode = "ingest_fail"
        with mock.patch.dict(os.environ, {"CATALOG_AUTO_INGEST": "1"}):
            out = self._client().ingest(ra=10.0, dec=20.0)
        self.assertFalse(out["ok"])
        self.assertEqual(out["catalog_status"], "ingest_failed")
        self.assertEqual(out["http_status"], 502)
        self.assertEqual(out["reason"], "UPSTREAM_TAP_ERROR")


if __name__ == "__main__":
    unittest.main()
