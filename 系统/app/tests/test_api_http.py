"""HTTP 层回归：sample / 非法 FOV / 目标冲突 / 404 / 非对象 JSON。

不联网：只请求本地临时端口上的 Handler；sample 模式读仓库自带的两份 FITS。
"""
import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from PIL import Image  # noqa: E402

import server  # noqa: E402


class ApiHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)

    def _get(self, path):
        try:
            with self.opener.open(self.base + path, timeout=30) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()

    def _post(self, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/api/analyze", data=raw, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    # ---------------------------------------------------------------- tests

    def test_health(self):
        status, ctype, body = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["results_cached"], int)
        self.assertIsInstance(data["results_bytes"], int)

    def test_sample_mode_is_200_and_serves_frames(self):
        status, body = self._post({"target_id": "barnard", "mode": "sample", "fov": 0.25})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["schema_version"], "1.0")
        self.assertEqual(body["data"]["mode"], "sample")
        self.assertEqual(body["result"]["class"], "U")
        self.assertEqual(body["result"]["readiness"], "L0")
        self.assertEqual(body["result"]["readiness_scope"], "analysis")
        self.assertIn("real_epochs", body["data"])
        self.assertEqual(body["data"]["fallback_reason"], "SAMPLE_MODE")
        self.assertIn("SAMPLE_MODE_NO_TARGET_MEASUREMENT", body["result"]["quality"])
        self.assertEqual(len(body["epochs"]), 2)

        status, ctype, png = self._get(body["epochs"][0]["main_url"])
        self.assertEqual(status, 200)
        self.assertIn("image/png", ctype)
        self.assertTrue(png.startswith(b"\x89PNG"))
        im = Image.open(io.BytesIO(png))
        self.assertEqual(im.format, "PNG")
        status, _, _ = self._get(body["stack_url"])
        self.assertEqual(status, 200)

    def test_invalid_fov_is_400_with_code(self):
        for bad, code in (
            (-0.2, "INVALID_FOV"),
            (0, "INVALID_FOV"),
            (0.019, "INVALID_FOV"),
            ("nan", "INVALID_FOV"),
            (3.1, "FOV_TOO_LARGE"),
            (3.5, "FOV_TOO_LARGE"),
            (99, "FOV_TOO_LARGE"),
        ):
            status, body = self._post({"target_id": "barnard", "mode": "sample", "fov": bad})
            self.assertEqual(status, 400, (bad, body))
            self.assertEqual(body["error"]["code"], code, (bad, body))

    def test_target_coordinate_conflict_is_400(self):
        status, body = self._post({"target_id": "barnard", "ra": 10, "dec": 10, "mode": "sample", "fov": 0.25})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "TARGET_COORD_CONFLICT")

    def test_partial_coordinates_are_400(self):
        status, body = self._post({"target_id": "barnard", "ra": 269.452077, "mode": "sample", "fov": 0.25})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "MISSING_COORDINATE")

    def test_unknown_mode_and_non_object_json_are_400(self):
        status, body = self._post({"target_id": "barnard", "mode": "bogus", "fov": 0.25})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_MODE")

        status, body = self._post(b"[]")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "BAD_JSON")

        status, body = self._post(b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "BAD_JSON")

    def test_unknown_frame_is_404(self):
        status, _, _ = self._get("/api/frame/deadbeef/0/main.png")
        self.assertEqual(status, 404)
        status, _, _ = self._get("/api/frame/deadbeef/stack.png")
        self.assertEqual(status, 404)


class PngSizeCapTest(unittest.TestCase):
    def test_large_input_is_not_upscaled_past_cap(self):
        import numpy as np

        rgb = np.zeros((700, 700, 3), np.uint8)
        rgb[::2, ::2] = 255
        png = server.png_bytes(rgb, scale=4)
        with Image.open(io.BytesIO(png)) as im:
            self.assertLessEqual(max(im.size), server.MAX_PNG_PX)
            expected = 700 * max(1, min(4, server.MAX_PNG_PX // 700))
            self.assertEqual(im.size, (expected, expected))

    def test_small_input_keeps_4x(self):
        import numpy as np

        rgb = np.zeros((100, 100, 3), np.uint8)
        png = server.png_bytes(rgb, scale=4)
        with Image.open(io.BytesIO(png)) as im:
            self.assertEqual(im.size, (400, 400))


if __name__ == "__main__":
    unittest.main()
