"""级联/残差层：纯函数、HTTP 端点与页面静态断言（不联网）。"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer
from unittest import mock

import numpy as np

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core import pipeline  # noqa: E402
from core.simulator import Epoch  # noqa: E402
from core.wcs import WCS  # noqa: E402


def _epoch() -> Epoch:
    """两个亮峰（L1）+ 一个 4.5σ 级暗峰（residual）；固定种子。"""
    n = 48
    yy, xx = np.mgrid[0:n, 0:n]
    image = np.zeros((n, n), float)
    for x, y, amp, sigma in ((12, 12, 20.0, 1.2), (32, 16, 20.0, 1.2), (24, 34, 0.4, 1.2)):
        image += amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma * sigma))
    rng = np.random.default_rng(7)
    image += rng.normal(0, 0.05, image.shape)
    variance = np.full_like(image, 0.05 ** 2)
    flags = np.zeros((n, n), np.int32)
    wcs = WCS(
        crval1=10.0,
        crval2=20.0,
        crpix1=n / 2 + 0.5,
        crpix2=n / 2 + 0.5,
        cd=np.array([[-0.0017, 0.0], [0.0, 0.0017]]),
        naxis1=n,
        naxis2=n,
    )
    return Epoch(
        id="syn-epoch",
        mjd=60000.0,
        image=image,
        variance=variance,
        flags=flags,
        wcs=wcs,
        provenance="real",
        band="D2",
        obsid="SYN",
        date_obs="2026-01-01T00:00:00",
    )


class CascadeLayersTest(unittest.TestCase):
    def test_l1_mask_and_residual(self):
        out = pipeline.cascade_layers(_epoch())
        self.assertGreaterEqual(out["counts"]["l1"], 2)
        self.assertEqual(out["counts"]["masked"], out["counts"]["l1"])
        self.assertGreaterEqual(out["counts"]["residual"], 1)
        self.assertEqual(len(out["l1"]), out["counts"]["l1"])
        self.assertEqual(len(out["masked"]), out["counts"]["masked"])
        self.assertEqual(len(out["residual"]), out["counts"]["residual"])
        self.assertLessEqual(out["counts"]["l1"], 80)
        self.assertLessEqual(out["counts"]["residual"], 80)
        self.assertEqual(out["residual_threshold"], 5.0)

        l1_ids = {s["source_id"] for s in out["l1"]}
        for s in out["l1"]:
            self.assertEqual(s["stage"], "l1")
            self.assertIsInstance(s["source_id"], str)
            self.assertIsNotNone(s["ra"])
            self.assertIsNotNone(s["dec"])
        for s in out["masked"]:
            self.assertEqual(s["stage"], "masked")
            self.assertEqual(s["reason"], "already_detected")
            self.assertIn(s["parent"], l1_ids)
            self.assertIn(s["masked_by"], l1_ids)
            self.assertIn("MASKED", s["quality"])
        for s in out["residual"]:
            self.assertEqual(s["stage"], "residual")
            self.assertIn("RESIDUAL_CANDIDATE", s["quality"])
            # 残差必须与 L1 中心保持 >2px 距离
            for l1 in out["l1"]:
                self.assertGreaterEqual(
                    ((s["x"] - l1["x"]) ** 2 + (s["y"] - l1["y"]) ** 2) ** 0.5,
                    2.0 - 1e-9,
                )

    def test_detect_sources_mask_is_honored(self):
        ep = _epoch()
        mask = np.zeros(ep.image.shape, dtype=bool)
        mask[10:15, 10:15] = True
        masked = pipeline.detect_sources(ep, threshold=4.5, mask=mask)
        # 被 mask 的亮源不应再成为候选
        self.assertFalse(any(10 <= s.x <= 15 and 10 <= s.y <= 15 for s in masked))

    def test_empty_epoch_is_safe(self):
        out = pipeline.cascade_layers(None)
        self.assertEqual(out["counts"], {"l1": 0, "masked": 0, "residual": 0})


class CascadeHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.target = server.data_layer.get_target("barnard")
        cls.epoch = _epoch()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)

    def _get(self, query):
        try:
            with urllib.request.urlopen(self.base + "/api/cascade?" + query, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _query(self, **extra):
        return "target_id=barnard&fov=0.2"

    def test_cascade_200(self):
        meta = {"mode": "real", "fallback_reason": None, "note": "test"}
        with mock.patch.object(
            server.data_layer,
            "build_dataset",
            return_value=(self.target, [self.epoch], None, meta),
        ):
            status, body = self._get(self._query())
        self.assertEqual(status, 200, body)
        self.assertEqual(body["schema_version"], "1.0")
        self.assertEqual(body["mode"], "real")
        self.assertEqual(body["counts"]["n_epochs"], 1)
        self.assertGreaterEqual(body["counts"]["l1"], 2)
        self.assertEqual(body["counts"]["masked"], body["counts"]["l1"])
        self.assertGreaterEqual(body["counts"]["residual"], 1)
        self.assertEqual(body["epochs"][0]["counts"]["l1"], body["counts"]["l1"])
        self.assertEqual(body["limits"]["l1_max"], 80)
        self.assertEqual(body["limits"]["residual_max"], 80)
        self.assertEqual(body["limits"]["residual_threshold"], 5.0)
        self.assertIn("残差候选不是新天体", body["disclaimer"])
        self.assertIsInstance(body["epochs"][0]["l1"][0]["source_id"], str)

    def _four_epochs(self):
        return [replace(self.epoch, id=f"e{i}", mjd=60916.0 + i * 10.0) for i in range(4)]

    def test_time_window_two_epochs(self):
        meta = {"mode": "real", "fallback_reason": None, "note": "test"}
        with mock.patch.object(
            server.data_layer, "build_dataset", return_value=(self.target, self._four_epochs(), None, meta)
        ):
            status, body = self._get("target_id=barnard&fov=0.2&t0=60916&t1=60926")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["time_filter"]["filtered"])
        self.assertEqual(body["time_filter"]["requested"], 4)
        self.assertEqual(body["time_filter"]["kept"], 2)
        self.assertEqual(body["counts"]["n_epochs"], 2)
        self.assertEqual(len(body["epochs"]), 2)

    def test_time_window_zero_epochs_is_200_empty(self):
        meta = {"mode": "real", "fallback_reason": None, "note": "test"}
        with mock.patch.object(
            server.data_layer, "build_dataset", return_value=(self.target, self._four_epochs(), None, meta)
        ):
            status, body = self._get("target_id=barnard&fov=0.2&t0=70000&t1=70001")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["time_filter"]["kept"], 0)
        self.assertEqual(body["counts"]["n_epochs"], 0)
        self.assertEqual(body["epochs"], [])
        self.assertIn("没有历元", body["note"])

    def test_time_window_one_epoch_is_200(self):
        meta = {"mode": "real", "fallback_reason": None, "note": "test"}
        with mock.patch.object(
            server.data_layer, "build_dataset", return_value=(self.target, self._four_epochs(), None, meta)
        ):
            status, body = self._get("target_id=barnard&fov=0.2&t0=60916&t1=60916")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["time_filter"]["kept"], 1)
        self.assertEqual(body["counts"]["n_epochs"], 1)

    def test_time_window_alias_and_invalid(self):
        meta = {"mode": "real", "fallback_reason": None, "note": "test"}
        with mock.patch.object(
            server.data_layer, "build_dataset", return_value=(self.target, self._four_epochs(), None, meta)
        ):
            status, body = self._get("target_id=barnard&fov=0.2&mjd_min=60916&mjd_max=60926")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["time_filter"]["kept"], 2)
        status, body = self._get("target_id=barnard&fov=0.2&t0=60930&t1=60916")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_TIME_WINDOW")

    def test_no_time_window_keeps_old_behavior(self):
        meta = {"mode": "real", "fallback_reason": None, "note": "test"}
        with mock.patch.object(
            server.data_layer, "build_dataset", return_value=(self.target, self._four_epochs(), None, meta)
        ):
            status, body = self._get("target_id=barnard&fov=0.2")
        self.assertEqual(status, 200, body)
        self.assertFalse(body["time_filter"]["filtered"])
        self.assertEqual(body["time_filter"]["requested"], 4)
        self.assertEqual(body["time_filter"]["kept"], 4)
        self.assertEqual(body["counts"]["n_epochs"], 4)

    def test_cascade_db_unavailable_503(self):
        meta = {"mode": "synthetic", "fallback_reason": "DB_UNAVAILABLE", "note": "db down"}
        with mock.patch.object(
            server.data_layer,
            "build_dataset",
            return_value=(self.target, [self.epoch], None, meta),
        ):
            status, body = self._get(self._query())
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["code"], "DB_UNAVAILABLE")

    def test_cascade_no_epochs_503(self):
        meta = {"mode": "synthetic", "fallback_reason": "NO_LOCAL_COVERAGE", "note": "none"}
        with mock.patch.object(
            server.data_layer,
            "build_dataset",
            return_value=(self.target, [], None, meta),
        ):
            status, body = self._get(self._query())
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["code"], "NO_EPOCHS")

    def test_cascade_invalid_fov_400(self):
        status, body = self._get("target_id=barnard&fov=0.001")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_FOV")

    def test_cascade_missing_coordinate_400(self):
        status, body = self._get("fov=0.2")
        self.assertEqual(status, 400, body)
        self.assertIn(body["error"]["code"], {"MISSING_COORDINATE", "INVALID_QUERY"})


class CascadePageStaticTest(unittest.TestCase):
    def test_page_and_js_boundaries(self):
        web = os.path.join(APP, "web")
        with open(os.path.join(web, "cascade.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(web, "cascade.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(web, "index.html"), encoding="utf-8") as fh:
            index = fh.read()
        self.assertIn("Residual candidates", html)
        self.assertIn("not new objects", html)
        self.assertIn("/api/cascade", js)
        self.assertIn("/api/identify", js)
        self.assertIn("catalog_unavailable", js)
        self.assertIn("cascade.layer.residual", js)
        self.assertIn("/cascade", index)
        self.assertIn('id="t0"', html)
        self.assertIn('id="t1"', html)
        self.assertIn('id="viewLink"', html)
        self.assertIn("time_filter", js)
        self.assertIn("timeFilterText", js)
        self.assertIn("viewLink", js)
        for text in (html, js):
            self.assertNotIn("新恒星", text)
            self.assertNotIn("新行星", text)
            self.assertNotIn("发现新", text)
            self.assertNotIn("new planet", text.lower())
            self.assertNotIn("confirmed discovery", text.lower())


if __name__ == "__main__":
    unittest.main()
