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


class _FakeC(BaseHTTPRequestHandler):
    def do_GET(self):
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

    def test_proxy_invalid_query_is_400(self):
        status, body = self._get("ra=999&dec=0")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_RA")


class AnyFieldUiCopyTest(unittest.TestCase):
    def test_ui_copy_has_required_c_status_text(self):
        web = os.path.join(APP, "web")
        with open(os.path.join(web, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(web, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("anyfieldCard", html)
        self.assertIn("C 索引不完整", js)
        self.assertIn("使用现有本地数据", js)
        self.assertIn("C 确认无覆盖", js)
        self.assertIn("C 服务不可用", js)
        self.assertIn("footprint_method", js)
        self.assertIn("n_epochs", js)
        # 503 文案不能把 C 缺口说成“确认无覆盖”，也不能出现 SIM。
        self.assertNotIn("SIM", js[js.find("C 索引不完整") - 200 : js.find("C 索引不完整") + 200])
        self.assertIn("不改变现有本地数据/分析结论", js)


if __name__ == "__main__":
    unittest.main()
