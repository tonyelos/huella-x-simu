"""主界面模式化：/api/analyze 时间窗口 + 模式 UI 静态断言。"""
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
from core.simulator import Epoch  # noqa: E402
from core.wcs import WCS  # noqa: E402


def _epoch(mjd=60916.0, eid="e0"):
    n = 24
    image = np.ones((n, n), float) * 0.5
    image[10:12, 10:12] += 5.0
    image[16:18, 16:18] += 3.0
    variance = np.full((n, n), 0.01)
    flags = np.zeros((n, n), np.int32)
    wcs = WCS(
        crval1=269.45, crval2=4.69,
        crpix1=n / 2 + 0.5, crpix2=n / 2 + 0.5,
        cd=np.array([[-0.0017, 0.0], [0.0, 0.0017]]),
        naxis1=n, naxis2=n,
    )
    return Epoch(id=eid, mjd=mjd, image=image, variance=variance, flags=flags, wcs=wcs,
                 provenance="real", band="D2", obsid=eid, date_obs="2026-01-01T00:00:00")


class AnalyzeTimeWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.target = server.data_layer.get_target("barnard")
        cls.epochs = [replace(_epoch(), id=f"e{i}", mjd=60916.0 + 10.0 * i) for i in range(4)]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)

    def _post(self, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.base + "/api/analyze", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.status, json.loads(r.read())

    def _mock_analysis(self):
        meta = {"mode": "real", "fallback_reason": None, "note": "test"}
        result = server._empty_result("U", "test", "L0")
        result["sources"] = []
        return (
            mock.patch.object(server.data_layer, "build_dataset", return_value=(self.target, self.epochs, None, meta)),
            mock.patch.object(server.pipeline, "run_pipeline", return_value=result),
        )

    def test_analyze_time_window_two_epochs(self):
        p1, p2 = self._mock_analysis()
        with p1, p2:
            status, body = self._post({"target_id": "barnard", "fov": 0.2, "mode": "auto", "t0": 60916, "t1": 60926})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["time_filter"], {"t0": 60916.0, "t1": 60926.0, "requested": 4, "kept": 2, "filtered": True})
        self.assertEqual(body["data"]["time_filter"]["kept"], 2)
        self.assertEqual(len(body["epochs"]), 2)

    def test_analyze_time_window_one_epoch(self):
        p1, p2 = self._mock_analysis()
        with p1, p2:
            status, body = self._post({"target_id": "barnard", "fov": 0.2, "mode": "auto", "t0": 60916, "t1": 60916})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["time_filter"]["kept"], 1)
        self.assertEqual(len(body["epochs"]), 1)

    def test_analyze_time_window_zero_is_200_empty(self):
        p1, p2 = self._mock_analysis()
        with p1, p2:
            status, body = self._post({"target_id": "barnard", "fov": 0.2, "mode": "auto", "t0": 70000, "t1": 70001})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["epochs"], [])
        self.assertEqual(body["data"]["n_epochs"], 0)
        self.assertEqual(body["result"]["class"], "U")
        self.assertIn("没有历元", body["data"]["note"])

    def test_analyze_no_window_keeps_old_behavior(self):
        p1, p2 = self._mock_analysis()
        with p1, p2:
            status, body = self._post({"target_id": "barnard", "fov": 0.2, "mode": "auto"})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["time_filter"]["filtered"])
        self.assertEqual(body["time_filter"]["requested"], 4)
        self.assertEqual(body["time_filter"]["kept"], 4)

    def test_cascade_reuses_analysis_epochs_without_refetch(self):
        p1, p2 = self._mock_analysis()
        with p1, p2:
            status, body = self._post({"target_id": "barnard", "fov": 0.2, "mode": "auto", "t0": 60916, "t1": 60926})
        self.assertEqual(status, 200, body)
        aid = body["analysis_id"]
        with mock.patch.object(server.data_layer, "build_dataset", side_effect=AssertionError("must not refetch")):
            status, out = self._get(
                f"/api/cascade?analysis_id={aid}&ra=269.45&dec=4.69&fov=0.2&t0=60916&t1=60926"
            )
        self.assertEqual(status, 200, out)
        self.assertEqual(out["counts"]["n_epochs"], 2)
        self.assertEqual(out["time_filter"]["kept"], 2)

    def test_analyze_invalid_window_400(self):
        status, body = self._post({"target_id": "barnard", "fov": 0.2, "mode": "auto", "t0": 60930, "t1": 60916})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_TIME_WINDOW")


class MainModeStaticTest(unittest.TestCase):
    def test_main_ui_has_mode_bar_and_canvas_overlays(self):
        web = os.path.join(APP, "web")
        with open(os.path.join(web, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(web, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        for text in ('data-layer-mode="browse"', 'data-layer-mode="cascade"', 'data-layer-mode="tracks"'):
            self.assertIn(text, html)
        self.assertIn('id="t0"', html)
        self.assertIn('id="t1"', html)
        self.assertIn('id="timeBadge"', html)
        self.assertIn('id="layerPanel"', html)
        for text in ("drawCascadeOverlay", "drawTracksOverlay", "/api/cascade", "/api/tracks", "setLayerMode"):
            self.assertIn(text, js)
        self.assertIn("mode", js)
        self.assertNotIn("新恒星", html + js)
        self.assertNotIn("新行星", html + js)
        self.assertNotIn("发现新", html + js)


if __name__ == "__main__":
    unittest.main()
