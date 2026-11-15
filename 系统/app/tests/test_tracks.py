"""跨历元 tracklet：纯函数、/api/tracks、页面静态断言（不联网）。"""
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
from core import tracks  # noqa: E402


def _nodes(i: int):
    nodes = [
        {"source_id": f"s{i}", "stage": "l1", "x": 10.0, "y": 10.0, "ra": 10.0, "dec": 20.0,
         "snr": 20.0, "flux": 10.0, "flux_err": 1.0, "quality": []},
        {"source_id": f"l{i}", "stage": "l1", "x": 20.0 + 2.0 * i, "y": 20.0, "ra": 10.1, "dec": 20.0,
         "snr": 20.0, "flux": 10.0, "flux_err": 1.0, "quality": []},
        {"source_id": f"v{i}", "stage": "residual", "x": 30.0, "y": 30.0, "ra": 10.2, "dec": 20.0,
         "snr": 10.0, "flux": [10.0, 20.0, 10.0, 20.0][i], "flux_err": 1.0, "quality": []},
    ]
    if i == 0:
        nodes.append({"source_id": "a0", "stage": "l1", "x": 40.0, "y": 40.0, "ra": 10.3, "dec": 20.0,
                      "snr": 5.0, "flux": 1.0, "flux_err": 0.2, "quality": ["EDGE"]})
    return nodes


def _epochs():
    return [
        {"epoch_id": f"e{i}", "mjd": 60000.0 + 30.0 * i, "pixel_scale_arcsec": 6.15, "nodes": _nodes(i)}
        for i in range(4)
    ]


class TrackletsTest(unittest.TestCase):
    def test_association_levels_and_no_duplicate_nodes(self):
        ts = tracks.tracklets(_epochs())
        self.assertEqual(len(ts), 4)
        self.assertEqual(tracks.counts(ts), {"D0": 1, "D1": 0, "D2": 0, "D3": 1, "D4": 2})
        levels = {t["level"] for t in ts}
        self.assertEqual(levels, {"D0", "D3", "D4"})
        models = {t["model"] for t in ts}
        self.assertIn("linear", models)
        self.assertIn("stationary", models)
        self.assertIn("variable", models)
        self.assertIn("transient", models)
        seen = []
        for t in ts:
            self.assertFalse(t["discovery_claim"])
            self.assertIn("4 epochs only", " ".join(t["caveats"]))
            for n in t["nodes"]:
                seen.append((n["epoch_id"], n["source_id"]))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertGreaterEqual(len(seen), 13)
        linear = next(t for t in ts if t["model"] == "linear")
        self.assertGreater(linear["mu_arcsec_yr"], 0)
        self.assertIsNotNone(linear["pa_deg"])
        stationary = next(t for t in ts if t["model"] == "stationary")
        self.assertEqual(stationary["mu_arcsec_yr"], 0.0)
        self.assertIsNone(stationary["pa_deg"])

    def test_catalog_lookup_can_assign_d1(self):
        def lookup(track):
            if track["model"] == "stationary":
                return {"status": "known", "catalog": "gaia-dr3", "source_id": "4472832130942575872"}
            return {"status": "none"}

        ts = tracks.tracklets(_epochs(), catalog_lookup=lookup)
        counts = tracks.counts(ts)
        self.assertEqual(counts["D1"], 1)
        d1 = next(t for t in ts if t["level"] == "D1")
        self.assertEqual(d1["catalog"], "gaia-dr3")
        self.assertEqual(d1["catalog_id"], "4472832130942575872")

    def test_catalog_unavailable_never_known(self):
        ts = tracks.tracklets(_epochs(), catalog_lookup=lambda track: {"status": "unavailable"})
        self.assertEqual(tracks.counts(ts)["D1"], 0)
        self.assertTrue(all(t["catalog"] in ("none", "unavailable") for t in ts))
        self.assertTrue(any("catalog unavailable" in " ".join(t["caveats"]) for t in ts))

    def test_empty_and_single_epoch(self):
        self.assertEqual(tracks.counts(tracks.tracklets([])), {"D0": 0, "D1": 0, "D2": 0, "D3": 0, "D4": 0})
        one = tracks.tracklets([_epochs()[0]])
        self.assertEqual(len(one), len(_epochs()[0]["nodes"]))
        self.assertTrue(all(t["n_epochs"] == 1 for t in one))
        self.assertTrue(all(t["model"] == "transient" for t in one))

    def test_one_gap_is_allowed(self):
        # 只有 e0/e2/e3 的线性移动源；允许中间缺 1 期
        eps = [
            {"epoch_id": "e0", "mjd": 60000.0, "pixel_scale_arcsec": 6.15,
             "nodes": [{"source_id": "x0", "stage": "l1", "x": 20.0, "y": 20.0, "ra": 10.0, "dec": 20.0,
                        "snr": 20.0, "flux": 10.0, "flux_err": 1.0, "quality": []}]},
            {"epoch_id": "e1", "mjd": 60030.0, "pixel_scale_arcsec": 6.15, "nodes": []},
            {"epoch_id": "e2", "mjd": 60060.0, "pixel_scale_arcsec": 6.15,
             "nodes": [{"source_id": "x2", "stage": "l1", "x": 24.0, "y": 20.0, "ra": 10.0, "dec": 20.0,
                        "snr": 20.0, "flux": 10.0, "flux_err": 1.0, "quality": []}]},
            {"epoch_id": "e3", "mjd": 60090.0, "pixel_scale_arcsec": 6.15,
             "nodes": [{"source_id": "x3", "stage": "l1", "x": 26.0, "y": 20.0, "ra": 10.0, "dec": 20.0,
                        "snr": 20.0, "flux": 10.0, "flux_err": 1.0, "quality": []}]},
        ]
        ts = tracks.tracklets(eps, max_sep_px=3.0, max_gap=1)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0]["n_epochs"], 3)
        self.assertEqual([n["source_id"] for n in ts[0]["nodes"]], ["x0", "x2", "x3"])


def _cascade_payload():
    return {
        "query": {"target_id": "barnard", "fov": 0.2},
        "target": {"id": "barnard", "name": "Barnard's Star", "ra": 269.45, "dec": 4.69},
        "mode": "real",
        "fallback_reason": None,
        "note": "test",
        "counts": {"n_epochs": 4, "l1": 4, "masked": 4, "residual": 4},
        "epochs": [
            {
                "epoch_id": e["epoch_id"], "mjd": e["mjd"], "pixel_scale_arcsec": 6.15,
                "l1": [n for n in e["nodes"] if n["stage"] == "l1"],
                "residual": [n for n in e["nodes"] if n["stage"] == "residual"],
            }
            for e in _epochs()
        ],
    }


class TracksHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)

    def _get(self, query):
        try:
            with urllib.request.urlopen(self.base + "/api/tracks?" + query, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_tracks_time_filter_zero_is_200_empty(self):
        empty = _cascade_payload()
        empty["counts"] = {"n_epochs": 0, "l1": 0, "masked": 0, "residual": 0}
        empty["epochs"] = []
        empty["time_filter"] = {"t0": 70000.0, "t1": 70001.0, "requested": 4, "kept": 0, "filtered": True}
        empty["note"] = "时间窗口内没有历元"
        with mock.patch.object(server, "run_cascade", return_value=empty):
            status, body = self._get("target_id=barnard&fov=0.2&t0=70000&t1=70001")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["counts"]["D0"] + body["counts"]["D1"] + body["counts"]["D2"] + body["counts"]["D3"] + body["counts"]["D4"], 0)
        self.assertEqual(body["tracks"], [])
        self.assertTrue(body["insufficient"])
        self.assertEqual(body["time_filter"]["kept"], 0)

    def test_tracks_one_epoch_is_insufficient(self):
        one = _cascade_payload()
        one["epochs"] = one["epochs"][:1]
        one["counts"] = {"n_epochs": 1, "l1": 4, "masked": 4, "residual": 4}
        one["time_filter"] = {"t0": 60916.0, "t1": 60916.0, "requested": 4, "kept": 1, "filtered": True}
        one["note"] = "时间窗口保留 1/4 期"
        with mock.patch.object(server, "run_cascade", return_value=one):
            status, body = self._get("target_id=barnard&fov=0.2&t0=60916&t1=60916")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["insufficient"])
        self.assertEqual(body["time_filter"]["kept"], 1)
        self.assertTrue(body["tracks"])
        self.assertTrue(all(t["n_epochs"] == 1 for t in body["tracks"]))
        self.assertTrue(all("insufficient_epochs" in t["caveats"] for t in body["tracks"]))

    def test_tracks_200(self):
        class DownCatalog:
            def match(self, **kwargs):
                return {"ok": False, "catalog_status": "unavailable", "catalog_reason": "down"}

        with (
            mock.patch.object(server, "run_cascade", return_value=_cascade_payload()),
            mock.patch.object(server, "CATALOG_CLIENT", DownCatalog()),
        ):
            status, body = self._get("target_id=barnard&fov=0.2")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["counts"]["D0"] + body["counts"]["D1"] + body["counts"]["D2"] + body["counts"]["D3"] + body["counts"]["D4"], 4)
        self.assertEqual(body["counts"]["D1"], 0)
        self.assertIn("discovery_claim", body)
        self.assertFalse(body["discovery_claim"])
        self.assertIn("轨迹候选不是新天体", body["disclaimer"])
        self.assertEqual(body["limits"]["max_sep_px"], 3.0)
        self.assertEqual(body["limits"]["max_catalog_checks"], 20)

    def test_tracks_catalog_known_can_assign_d1(self):
        class KnownCatalog:
            def match(self, **kwargs):
                return {
                    "ok": True,
                    "catalog_status": "ok",
                    "release": "gaia-dr3",
                    "candidates": [
                        {"catalog_id": "4472832130942575872", "release": "gaia-dr3", "ruwe": 1.08485}
                    ],
                }

        with (
            mock.patch.object(server, "run_cascade", return_value=_cascade_payload()),
            mock.patch.object(server, "CATALOG_CLIENT", KnownCatalog()),
        ):
            status, body = self._get("target_id=barnard&fov=0.2")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["counts"]["D1"], 3)
        d1 = [t for t in body["tracks"] if t["level"] == "D1"]
        self.assertTrue(all(t["catalog"] == "gaia-dr3" for t in d1))
        self.assertTrue(all(t["catalog_id"] == "4472832130942575872" for t in d1))

    def test_tracks_db_unavailable_503(self):
        with mock.patch.object(server, "run_cascade", side_effect=server.ApiError(503, "DB_UNAVAILABLE", "db down")):
            status, body = self._get("target_id=barnard&fov=0.2")
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["code"], "DB_UNAVAILABLE")

    def test_tracks_invalid_fov_400(self):
        status, body = self._get("target_id=barnard&fov=0.001")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_FOV")

    def test_tracks_missing_coordinate_400(self):
        status, body = self._get("fov=0.2")
        self.assertEqual(status, 400, body)
        self.assertIn(body["error"]["code"], {"MISSING_COORDINATE", "INVALID_QUERY"})


class TracksPageStaticTest(unittest.TestCase):
    def test_page_and_js_boundaries(self):
        web = os.path.join(APP, "web")
        with open(os.path.join(web, "tracks.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(web, "tracks.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("D0", html)
        self.assertIn("D4", html)
        self.assertIn("/api/tracks", js)
        self.assertIn("/api/identify", js)
        self.assertIn("轨迹候选不是新天体", html + js)
        self.assertIn("D5", html)
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


if __name__ == "__main__":
    unittest.main()
