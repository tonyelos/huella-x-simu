"""D-C P0：AnyField 覆盖握手客户端 + /api/field 代理测试（不联网）。

覆盖：
- fake C HTTP 200 / 422 / 503 / 超时 / 连接失败；
- 200 的 n_epochs/footprint_method、422 的强断言、503 的“索引不完整”；
- app `/api/field` 代理保留状态，且不需要 C 在线；
- UI 文案静态检查：503 不说“无覆盖”，不出现 SIM/新天体。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core import anyfield as af  # noqa: E402

RA = 269.452077
DEC = 4.693365


def _body_200():
    return {
        "schema_version": "1.0",
        "index_snapshot": "local-test",
        "index_completeness": "partial",
        "footprint_method": "wcs_header",
        "source": "target_epochs",
        "coverage": {
            "state": "covered",
            "coverage_method": "wcs_header",
            "footprint_method": "wcs_header",
            "n_epochs": 4,
            "coverage_level": "C2",
            "index_completeness": "partial",
        },
        "epochs": [{"obsid": "e1", "mjd": 60916.6, "footprint_method": "wcs_header"}],
        "count": 4,
    }


def _body_422():
    return {
        "error": {
            "code": "NO_LOCAL_COVERAGE",
            "retryable": False,
            "details": {"strong_assertion": True, "no_local_coverage": True},
        },
        "coverage": {"state": "none", "index_completeness": "complete", "n_epochs": 0},
    }


def _body_503():
    return {
        "error": {
            "code": "INDEX_INCOMPLETE",
            "retryable": True,
            "details": {"strong_assertion": False, "no_local_coverage": False},
        },
        "coverage": {"state": "none", "index_completeness": "partial", "n_epochs": 0},
    }


def _body_sources():
    return {
        "schema_version": "1.0",
        "service_version": "0.3.0",
        "index_snapshot": "local-test",
        "index_completeness": "partial",
        "query": {"ra": RA, "dec": DEC, "fov": 0.2, "radius_deg": 0.1, "epoch": None, "limit": 200},
        "source_origin": "ingested",
        "provenance": "real",
        "count": 2,
        "sources": [
            {
                "source_id": "meas:2025W35_2A_0221_1:0",
                "epoch_id": "2025W35_2A_0221_1",
                "obsid": "2025W35_2A_0221_1",
                "detector": 2,
                "release": "dr1",
                "version": "local-test",
                "mjd": 60916.656,
                "source_index": 0,
                "x": 62.016,
                "y": 8.682,
                "ra": 269.45476104,
                "dec": 4.8516613,
                "flux": 128.28,
                "flux_err": 1.46,
                "snr": 161.68,
                "saturated": False,
                "quality": [],
                "source_origin": "ingested",
                "provenance": "real",
                "sep_deg": 0.09,
            },
            {
                "source_id": "meas:2025W35_2A_0221_1:1",
                "epoch_id": "2025W35_2A_0221_1",
                "obsid": "2025W35_2A_0221_1",
                "detector": 2,
                "release": "dr1",
                "version": "local-test",
                "mjd": 60916.656,
                "source_index": 1,
                "x": 80.0,
                "y": 90.0,
                "ra": 269.40,
                "dec": 4.70,
                "flux": 20.0,
                "flux_err": 2.0,
                "snr": 10.0,
                "saturated": False,
                "quality": ["EDGE"],
                "source_origin": "ingested",
                "provenance": "real",
                "sep_deg": 0.2,
            },
        ],
        "sources_truncated": False,
        "generated_at": "2026-11-15T00:00:00Z",
    }


class _FakeC(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/sources"):
            mode = getattr(self.server, "source_mode", "ok")
            if mode == "timeout":
                time.sleep(0.6)
                return
            if mode == "empty":
                payload = {**_body_sources(), "count": 0, "sources": []}
                status = 200
            else:
                status, payload = {
                    "ok": (200, _body_sources()),
                    "503": (503, _body_503()),
                }.get(mode, (200, _body_sources()))
        else:
            mode = getattr(self.server, "mode", "ok")
            if mode == "timeout":
                time.sleep(0.6)
                return
            status, payload = {
                "ok": (200, _body_200()),
                "422": (422, _body_422()),
                "503": (503, _body_503()),
            }.get(mode, (200, _body_200()))
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        return


class FakeCServerMixin:
    @classmethod
    def setUpClass(cls):
        cls.c_httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeC)
        cls.c_httpd.daemon_threads = True
        cls.c_thread = threading.Thread(target=cls.c_httpd.serve_forever, daemon=True)
        cls.c_thread.start()
        cls.c_base = f"http://127.0.0.1:{cls.c_httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.c_httpd.shutdown()
        cls.c_httpd.server_close()
        cls.c_thread.join(timeout=5)


class AnyFieldClientTest(FakeCServerMixin, unittest.TestCase):
    def _client(self, timeout=1.0):
        return af.AnyFieldClient(base_url=self.c_base, timeout=timeout)

    def test_200_has_n_epochs_and_footprint(self):
        self.c_httpd.mode = "ok"
        out = self._client().field(RA, DEC, 0.2)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["n_epochs"], 4)
        self.assertEqual(out["footprint_method"], "wcs_header")
        self.assertEqual(out["http_status"], 200)
        self.assertEqual(len(out["epochs"]), 1)

    def test_422_is_strong_no_coverage_not_incomplete(self):
        self.c_httpd.mode = "422"
        out = self._client().field(RA, DEC, 0.2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "no_coverage")
        self.assertEqual(out["http_status"], 422)
        self.assertFalse(out["retryable"])
        self.assertEqual(out["error_code"], "NO_LOCAL_COVERAGE")

    def test_503_is_index_incomplete_not_no_coverage(self):
        self.c_httpd.mode = "503"
        out = self._client().field(RA, DEC, 0.2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "index_incomplete")
        self.assertEqual(out["http_status"], 503)
        self.assertTrue(out["retryable"])
        self.assertEqual(out["error_code"], "INDEX_INCOMPLETE")

    def test_timeout_is_unavailable(self):
        self.c_httpd.mode = "timeout"
        out = af.AnyFieldClient(base_url=self.c_base, timeout=0.1).field(RA, DEC, 0.2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unavailable")
        self.assertIn(out["error_code"], {"ANYFIELD_TIMEOUT", "ANYFIELD_UNREACHABLE"})

    def test_sources_200_parses_string_ids_and_fields(self):
        self.c_httpd.source_mode = "ok"
        out = self._client().sources(RA, DEC, 0.2, limit=50)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["n_sources"], 2)
        self.assertEqual(len(out["sources"]), 2)
        first = out["sources"][0]
        self.assertIsInstance(first["source_id"], str)
        self.assertEqual(first["source_id"], "meas:2025W35_2A_0221_1:0")
        self.assertEqual(first["snr"], 161.68)
        self.assertEqual(first["source_origin"], "ingested")
        self.assertEqual(first["quality"], [])

    def test_sources_empty_is_200_not_503(self):
        self.c_httpd.source_mode = "empty"
        out = self._client().sources(RA, DEC, 0.2)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["n_sources"], 0)
        self.assertEqual(out["sources"], [])

    def test_sources_503_is_index_incomplete(self):
        self.c_httpd.source_mode = "503"
        out = self._client().sources(RA, DEC, 0.2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "index_incomplete")
        self.assertNotEqual(out["status"], "no_coverage")

    def test_sources_timeout_is_unavailable(self):
        self.c_httpd.source_mode = "timeout"
        out = af.AnyFieldClient(base_url=self.c_base, timeout=0.1).sources(RA, DEC, 0.2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unavailable")

    def test_connection_refused_is_unavailable(self):
        out = af.AnyFieldClient(base_url="http://127.0.0.1:1", timeout=0.3).field(RA, DEC, 0.2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unavailable")

    def test_bad_json_is_unavailable(self):
        class BadOpener:
            def open(self, request, timeout=None):
                class R:
                    status = 200

                    def read(self):
                        return b"{not json"

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                return R()

        out = af.AnyFieldClient(base_url=self.c_base, opener=BadOpener()).field(RA, DEC)
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["error_code"], "MALFORMED_RESPONSE")

    @mock.patch.dict(os.environ, {"ANYFIELD_URL": "http://example.test:8798/"}, clear=False)
    def test_anyfield_url_env(self):
        self.assertEqual(af.anyfield_base_url(), "http://example.test:8798")


class CSourceToIdentifyTest(FakeCServerMixin, unittest.TestCase):
    def test_c_source_coordinates_can_be_identified(self):
        self.c_httpd.source_mode = "ok"
        result = af.AnyFieldClient(base_url=self.c_base, timeout=1.0).sources(RA, DEC, 0.2)
        src = result["sources"][0]

        class FakeB:
            def match(self, **kwargs):
                return {
                    "ok": True,
                    "catalog_status": "ok",
                    "catalog": "gaia",
                    "release": "gaia-dr3",
                    "catalog_schema_version": 2,
                    "n_candidates": 1,
                    "ambiguous": False,
                    "candidates": [
                        {
                            "catalog": "gaia",
                            "catalog_id": "4472832130942575872",
                            "source_id": "4472832130942575872",
                            "release": "gaia-dr3",
                            "separation_arcsec": 0.1,
                            "pmra": -801.55,
                            "pmdec": 10362.39,
                            "pmra_error": 0.03,
                            "pmdec_error": 0.04,
                            "ruwe": 1.08485,
                        }
                    ],
                    "persisted": False,
                    "persisted_rows": 0,
                }

        with mock.patch.object(server, "CATALOG_CLIENT", FakeB()):
            out = server.run_identify(
                {
                    "ra": src["ra"],
                    "dec": src["dec"],
                    "mjd": src["mjd"],
                    "sigma_arcsec": 0.5,
                    "detection_id": f"c:{src['source_id']}",
                    "n_epochs": 1,
                }
            )
        self.assertEqual(out["status"], "known_star", out)
        self.assertEqual(out["source_id"], "4472832130942575872")
        self.assertEqual(out["detection_id"], f"c:{src['source_id']}")
        self.assertFalse(out["discovery_claim"])


class FieldProxyTest(FakeCServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        super().tearDownClass()

    def _get(self, query):
        req = urllib.request.Request(self.base + "/api/field?" + query)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _get_sources(self, query):
        req = urllib.request.Request(self.base + "/api/sources?" + query)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_proxy_200(self):
        self.c_httpd.mode = "ok"
        with mock.patch.object(server, "ANYFIELD_CLIENT", af.AnyFieldClient(base_url=self.c_base, timeout=1.0)):
            status, body = self._get(f"ra={RA}&dec={DEC}&fov=0.2")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["n_epochs"], 4)
        self.assertEqual(body["footprint_method"], "wcs_header")

    def test_proxy_422(self):
        self.c_httpd.mode = "422"
        with mock.patch.object(server, "ANYFIELD_CLIENT", af.AnyFieldClient(base_url=self.c_base, timeout=1.0)):
            status, body = self._get(f"ra={RA}&dec={DEC}&fov=0.2")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["status"], "no_coverage")
        self.assertFalse(body["retryable"])

    def test_proxy_503(self):
        self.c_httpd.mode = "503"
        with mock.patch.object(server, "ANYFIELD_CLIENT", af.AnyFieldClient(base_url=self.c_base, timeout=1.0)):
            status, body = self._get(f"ra={RA}&dec={DEC}&fov=0.2")
        self.assertEqual(status, 503, body)
        self.assertEqual(body["status"], "index_incomplete")
        self.assertNotEqual(body["status"], "no_coverage")

    def test_proxy_c_down_is_503_unavailable(self):
        with mock.patch.object(server, "ANYFIELD_CLIENT", af.AnyFieldClient(base_url="http://127.0.0.1:1", timeout=0.2)):
            status, body = self._get(f"ra={RA}&dec={DEC}&fov=0.2")
        self.assertEqual(status, 503, body)
        self.assertEqual(body["status"], "unavailable")

    def test_proxy_sources_200_real(self):
        self.c_httpd.source_mode = "ok"
        with mock.patch.object(server, "ANYFIELD_CLIENT", af.AnyFieldClient(base_url=self.c_base, timeout=1.0)):
            status, body = self._get_sources(f"ra={RA}&dec={DEC}&fov=0.2&limit=50")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["n_sources"], 2)
        self.assertIsInstance(body["sources"][0]["source_id"], str)

    def test_proxy_sources_empty(self):
        self.c_httpd.source_mode = "empty"
        with mock.patch.object(server, "ANYFIELD_CLIENT", af.AnyFieldClient(base_url=self.c_base, timeout=1.0)):
            status, body = self._get_sources(f"ra={RA}&dec={DEC}&fov=0.2")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["sources"], [])

    def test_proxy_sources_503(self):
        self.c_httpd.source_mode = "503"
        with mock.patch.object(server, "ANYFIELD_CLIENT", af.AnyFieldClient(base_url=self.c_base, timeout=1.0)):
            status, body = self._get_sources(f"ra={RA}&dec={DEC}&fov=0.2")
        self.assertEqual(status, 503, body)
        self.assertEqual(body["status"], "index_incomplete")

    def test_proxy_sources_invalid_query_is_400(self):
        status, body = self._get_sources("ra=999&dec=0")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_RA")

    def test_proxy_invalid_query_is_400(self):
        status, body = self._get("ra=999&dec=0")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_RA")


class AnyFieldUiCopyTest(unittest.TestCase):
    def test_ui_copy_has_required_c_status_text(self):
        web = os.path.join(APP, "web")
        with open(os.path.join(web, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(web, "i18n.js"), encoding="utf-8") as fh:
            i18n = fh.read()
        with open(os.path.join(web, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        ui = js + i18n
        self.assertIn("anyfieldCard", html)
        self.assertIn("C index is incomplete", i18n)
        self.assertIn("existing local data is used", i18n)
        self.assertIn("C confirms no coverage", i18n)
        self.assertIn("C service is unavailable", i18n)
        self.assertIn("footprint_method", js)
        self.assertIn("n_epochs", js)
        self.assertIn("loadCSourcesBtn", html)
        self.assertIn("anyfieldSources", html)
        self.assertIn("Load C source list", html)
        self.assertIn("C source list", i18n)
        self.assertIn("/api/sources", js)
        self.assertIn("lockCSource", js)
        self.assertIn("identifyLockedSource", js)
        # 503 文案不能把 C 缺口说成“确认无覆盖”，也不能出现 SIM。
        self.assertNotIn("SIM", i18n[i18n.find("C index is incomplete") - 200 : i18n.find("C index is incomplete") + 200])
        self.assertIn("existing local data and analysis conclusions are unchanged", i18n)


NODE = shutil.which("node")


class LockNoteJsTest(unittest.TestCase):
    """锁定源 note 的纯函数：成功/失败/超时都不能停留在“正在查询”。"""

    @unittest.skipUnless(NODE, "node 不在 PATH，跳过锁 note JS 测试")
    def test_lock_note_success_failure_timeout(self):
        js_path = os.path.join(APP, "web", "lock_note.js")
        script = r"""
const ln = require(process.argv[1]);
const src = {ra: 269.446165, dec: 4.769536, c_source: true};
const cases = {
  known: {status: "known_star", label_zh: "known"},
  ambiguous: {status: "ambiguous", label_zh: "ambiguous"},
  insufficient: {status: "insufficient_data", label_zh: "insufficient"},
  failure: {status: "catalog_unavailable", label_zh: "unavailable"},
  timeout: {status: "catalog_unavailable", label_zh: "unavailable"},
  empty: null,
};
const out = {};
for (const [k, ident] of Object.entries(cases)) out[k] = ln.sourceNote(src, ident);
console.log(JSON.stringify(out));
"""
        proc = subprocess.run([NODE, "-e", script, js_path], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("Known in catalog", out["known"])
        self.assertIn("known_star", out["known"])
        self.assertIn("ambiguous", out["ambiguous"])
        self.assertIn("Insufficient data", out["insufficient"])
        self.assertIn("insufficient_data", out["insufficient"])
        self.assertIn("Catalog unavailable", out["failure"])
        self.assertIn("catalog_unavailable", out["failure"])
        self.assertIn("Catalog unavailable", out["timeout"])
        self.assertIn("Catalog unavailable", out["empty"])
        self.assertIn("269.446165", out["known"])
        self.assertIn("4.769536", out["known"])
        for text in out.values():
            self.assertNotIn("正在查询", text)
            self.assertNotIn("未匹配", text)
            self.assertNotIn("新天体", text)
            self.assertNotIn("行星", text)

    @unittest.skipUnless(NODE, "node 不在 PATH，跳过锁 note JS 测试")
    def test_app_updates_note_in_identify_finally_for_c_sources_only(self):
        with open(os.path.join(APP, "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("function updateLockedSourceNote", js)
        self.assertIn("if (!src || !src.c_source) return;", js)
        self.assertIn("updateLockedSourceNote(src, state.lockedIdentification)", js)
        self.assertNotIn("正在查询 Gaia DR3…", js)
        with open(os.path.join(APP, "web", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("lock_note.js", html)


if __name__ == "__main__":
    unittest.main()
