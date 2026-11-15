"""`/api/analyze` 的 identification 契约（D 阶段 MVP）。

- 直接调用 ``server.run_analysis``（mock 取数/渲染）检查字段与状态；
- 起一个临时 HTTP server，用真实管线 + 假星表客户端检查
  ``POST /api/analyze`` 不 500、字段可读、星表停摆时不阻塞原分析。
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import numpy as np

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core import catalog as cat  # noqa: E402

BARNARD_ID = "4472832130942575872"


def _candidate(cid=BARNARD_ID, sep=0.12):
    return {
        "source_id": f"gaia-dr3:{cid}",
        "catalog": "gaia",
        "catalog_id": cid,
        "release": "gaia-dr3",
        "ra": 269.44850252543836,
        "dec": 4.739420051112412,
        "ra_propagated": 269.446164982,
        "dec_propagated": 4.769536326,
        "sep_arcsec": sep,
        "pmra": -801.550978,
        "pmdec": 10362.394207,
        "pmra_error": 0.03182,
        "pmdec_error": 0.03607,
        "parallax": 546.97594,
        "gmag": 8.19397,
        "bp_rp": 2.8337,
        "object_type": "star",
        "confidence": 0.99,
    }


def _b_response(candidates=None, *, catalog_status="ok"):
    candidates = list(candidates or [])
    return {
        "schema_version": "1.0",
        "release": "gaia-dr3",
        "version": "dr3",
        "releases": [{"release": "gaia-dr3"}],
        "unavailable": False,
        "catalog_status": catalog_status,
        "matches": [
            {
                "index": 0,
                "detection_id": "barnard",
                "ra": 269.446165,
                "dec": 4.769536,
                "epoch_mjd": 61210.0,
                "sigma_arcsec": 1.0,
                "match_radius_arcsec": 3.0,
                "coverage": {
                    "gaia": {
                        "complete": catalog_status == "ok",
                        "field_id": "barnard",
                        "n_cached_sources": 9603,
                    }
                },
                "catalog_status": catalog_status,
                "n_candidates": len(candidates),
                "ambiguous": len(candidates) > 1,
                "status": "ambiguous" if len(candidates) > 1 else ("single_candidate" if candidates else "no_match"),
                "candidates": candidates,
            }
        ],
        "n_detections": 1,
    }


class FakeCatalogClient:
    def __init__(self, normalized):
        self.normalized = normalized
        self.calls = []

    def match(self, **kwargs):
        self.calls.append(kwargs)
        return self.normalized


class _FakeWCS:
    crval1 = 269.452077
    crval2 = 4.693365
    naxis1 = 64
    naxis2 = 64

    def pixel_scale_arcsec(self):
        return 6.15


class _FakeEpoch:
    def __init__(self, i):
        self.id = f"fake-{i}"
        self.mjd = 60916.656 + i * 100.0
        self.image = np.zeros((8, 8), float)
        self.psf = None
        self.provenance = "real"
        self.band = "D2"
        self.obsid = f"OBS{i}"
        self.date_obs = "2026-01-0%dT00:00:00" % (i + 1)
        self.source = "fake"
        self.wcs = _FakeWCS()

    def json(self):
        return {
            "id": self.id,
            "mjd": self.mjd,
            "date_obs": self.date_obs,
            "band": self.band,
            "obsid": self.obsid,
            "provenance": self.provenance,
            "source": self.source,
            "shape": [8, 8],
            "wcs": {},
        }


def _pipeline_result(*, measurements=None, quality=None):
    return {
        "class": "L",
        "confidence": 0.9,
        "readiness": "L2",
        "readiness_scope": "analysis",
        "quality": list(quality or []),
        "mu_mas_yr": 10393.4,
        "mu_err_mas_yr": 60.0,
        "pa_deg": 355.6,
        "moving": True,
        "variable": False,
        "detected_epochs": 2,
        "baseline_days": 204.0,
        "sources": [],
        "measurements": measurements
        if measurements is not None
        else [
            {
                "epoch_id": "fake-1",
                "mjd": 61210.0,
                "xi_arcsec": -7.0,
                "eta_arcsec": 263.0,
                "sigma_arcsec": 1.0,
                "flux": 100.0,
                "flux_err": 2.0,
                "saturated": False,
            }
        ],
    }


def run_with_catalog(normalized, *, measurements=None, result_quality=None):
    target = server.data_layer.get_target("barnard")
    epochs = [_FakeEpoch(0), _FakeEpoch(1)]
    meta = {
        "mode": "synthetic",
        "note": "identification test",
        "real_epochs": 2,
        "fetch_attempted": 2,
        "fetch_succeeded": 2,
    }
    fake = FakeCatalogClient(normalized)
    with (
        mock.patch.object(server.data_layer, "build_dataset", return_value=(target, epochs, None, meta)),
        mock.patch.object(
            server.pipeline,
            "run_pipeline",
            return_value=_pipeline_result(measurements=measurements, quality=result_quality),
        ),
        mock.patch.object(
            server.pipeline,
            "reproject_to_common",
            return_value=[np.zeros((4, 4)), np.zeros((4, 4))],
        ),
        mock.patch.object(server, "prepare_frames", return_value={"frames": [], "stack": b"x"}),
        mock.patch.object(server, "CATALOG_CLIENT", fake),
    ):
        out = server.run_analysis({"target_id": "barnard", "mode": "auto", "fov": 0.25})
    return out, fake


class AnalyzeIdentificationTest(unittest.TestCase):
    def test_known_star_fields(self):
        out, fake = run_with_catalog(cat.normalize_match(_b_response([_candidate()])))
        ident = out["identification"]
        self.assertEqual(ident["status"], "known_star")
        self.assertEqual(ident["catalog"], "gaia")
        self.assertIsInstance(ident["catalog_id"], str)
        self.assertEqual(ident["catalog_id"], BARNARD_ID)
        self.assertEqual(ident["source_id"], BARNARD_ID)
        self.assertEqual(ident["release"], "gaia-dr3")
        self.assertEqual(ident["catalog_release"], "gaia-dr3")
        self.assertEqual(ident["n_candidates"], 1)
        self.assertAlmostEqual(ident["separation_arcsec"], 0.12)
        self.assertAlmostEqual(ident["sep_arcsec"], 0.12)
        self.assertIsInstance(ident["caveats"], list)
        self.assertIs(ident["pm_consistent"], True)
        self.assertIn(ident["reason"], {"SINGLE_CATALOG_MATCH"})
        self.assertFalse(ident["discovery_claim"])
        self.assertNotIn("新恒星", json.dumps(out, ensure_ascii=False))
        # 原分析结果未被星表改写
        self.assertEqual(out["result"]["class"], "L")
        self.assertEqual(len(out["sources"]), 0)
        self.assertTrue(fake.calls)
        call = fake.calls[0]
        self.assertEqual(call["mjd"], 61210.0)
        self.assertNotIn("pmra_mas_yr", call)
        self.assertNotIn("pmdec_mas_yr", call)

    def test_ambiguous_keeps_both_candidates(self):
        cands = [_candidate("156430822114424576", 0.0), _candidate("156430817820015232", 0.2163)]
        out, _ = run_with_catalog(cat.normalize_match(_b_response(cands)))
        ident = out["identification"]
        self.assertEqual(ident["status"], "ambiguous")
        self.assertIsNone(ident["catalog_id"])
        self.assertEqual(ident["n_candidates"], 2)
        self.assertEqual(len(ident["candidates"]), 2)
        self.assertNotIn("新恒星", json.dumps(out, ensure_ascii=False))

    def test_empty_ok_is_uncatalogued(self):
        out, _ = run_with_catalog(cat.normalize_match(_b_response([], catalog_status="ok")))
        ident = out["identification"]
        self.assertEqual(ident["status"], "uncatalogued_candidate")
        self.assertEqual(ident["label_zh"], "未收录候选（未经确认）")

    def test_empty_partial_is_insufficient(self):
        out, _ = run_with_catalog(cat.normalize_match(_b_response([], catalog_status="partial")))
        ident = out["identification"]
        self.assertEqual(ident["status"], "insufficient_data")
        self.assertEqual(ident["reason"], "CATALOG_PARTIAL")

    def test_unavailable_does_not_block_analysis(self):
        out, _ = run_with_catalog(cat.normalize_match({"unavailable": True}))
        ident = out["identification"]
        self.assertEqual(ident["status"], "catalog_unavailable")
        self.assertEqual(ident["catalog_status"], "unavailable")
        self.assertEqual(ident["n_candidates"], 0)
        self.assertEqual(out["result"]["class"], "L")
        self.assertNotIn("new_star", json.dumps(out, ensure_ascii=False))

    def test_target_not_detected_skips_catalog(self):
        out, fake = run_with_catalog(cat.normalize_match(_b_response([_candidate()])), measurements=[])
        ident = out["identification"]
        self.assertEqual(ident["status"], "insufficient_data")
        self.assertEqual(ident["reason"], "TARGET_NOT_DETECTED")
        self.assertEqual(fake.calls, [])

    def test_identification_failure_never_500(self):
        class Boom:
            def match(self, **kwargs):
                raise RuntimeError("boom")

        target = server.data_layer.get_target("barnard")
        epochs = [_FakeEpoch(0), _FakeEpoch(1)]
        meta = {"mode": "synthetic", "note": "x", "real_epochs": 2}
        with (
            mock.patch.object(server.data_layer, "build_dataset", return_value=(target, epochs, None, meta)),
            mock.patch.object(server.pipeline, "run_pipeline", return_value=_pipeline_result()),
            mock.patch.object(server.pipeline, "reproject_to_common", return_value=[np.zeros((4, 4))] * 2),
            mock.patch.object(server, "prepare_frames", return_value={"frames": [], "stack": b"x"}),
            mock.patch.object(server, "CATALOG_CLIENT", Boom()),
        ):
            out = server.run_analysis({"target_id": "barnard", "mode": "auto", "fov": 0.25})
        self.assertEqual(out["identification"]["status"], "catalog_unavailable")
        self.assertEqual(out["identification"]["catalog_reason"], "CATALOG_CLIENT_ERROR")


class AnalyzeHTTPIdentificationTest(unittest.TestCase):
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

    def _post(self, payload):
        req = urllib.request.Request(
            self.base + "/api/analyze",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _post_identify(self, payload):
        req = urllib.request.Request(
            self.base + "/api/identify",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_identify_known_contract(self):
        fake = FakeCatalogClient(cat.normalize_match(_b_response([_candidate()])))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post_identify(
                {"ra": 269.446165, "dec": 4.769536, "mjd": 61210.0, "sigma_arcsec": 1.0}
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "known_star")
        self.assertEqual(body["catalog_release"], "gaia-dr3")
        self.assertEqual(body["source_id"], BARNARD_ID)
        self.assertIsInstance(body["source_id"], str)
        self.assertEqual(body["sep_arcsec"], 0.12)
        self.assertIsInstance(body["caveats"], list)
        self.assertFalse(body["discovery_claim"])
        self.assertEqual(fake.calls[0]["mjd"], 61210.0)

    def test_identify_ambiguous(self):
        cands = [_candidate("156430822114424576", 0.0), _candidate("156430817820015232", 0.2163)]
        fake = FakeCatalogClient(cat.normalize_match(_b_response(cands)))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post_identify({"ra": 76.956, "dec": 30.401, "mjd": 61210.0})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ambiguous")
        self.assertIsNone(body["source_id"])
        self.assertEqual(len(body["candidates"]), 2)
        self.assertFalse(body["discovery_claim"])

    def test_identify_pm_inconsistent_is_ambiguous(self):
        cand = _candidate()
        cand["pmra"] = 5000.0
        cand["pmdec"] = -5000.0
        fake = FakeCatalogClient(cat.normalize_match(_b_response([cand])))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post_identify(
                {
                    "ra": 269.446165,
                    "dec": 4.769536,
                    "mjd": 61210.0,
                    "pmra_mas_yr": -801.55,
                    "pmdec_mas_yr": 10362.39,
                    "pm_err_mas_yr": 50.0,
                    "n_epochs": 4,
                }
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ambiguous")
        self.assertEqual(body["reason"], "PM_INCONSISTENT")
        self.assertIsNone(body["source_id"])

    def test_identify_no_match_ok_is_uncatalogued(self):
        fake = FakeCatalogClient(cat.normalize_match(_b_response([], catalog_status="ok")))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post_identify({"ra": 10.0, "dec": 20.0, "mjd": 61210.0})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "uncatalogued_candidate")
        self.assertIsNone(body["source_id"])
        self.assertIn("未收录", body["label_zh"])

    def test_identify_partial_zero_is_insufficient(self):
        fake = FakeCatalogClient(cat.normalize_match(_b_response([], catalog_status="partial")))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post_identify({"ra": 10.0, "dec": 20.0, "mjd": 61210.0})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "insufficient_data")
        self.assertEqual(body["reason"], "CATALOG_PARTIAL")

    def test_identify_503_is_catalog_unavailable(self):
        fake = FakeCatalogClient(cat.normalize_match({"unavailable": True, "reason": "no_cached_field"}))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post_identify({"ra": 10.0, "dec": 20.0, "mjd": 61210.0})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "catalog_unavailable")
        self.assertEqual(body["catalog_status"], "unavailable")
        self.assertIsNone(body["source_id"])
        self.assertFalse(body["discovery_claim"])
        self.assertTrue(any("星表" in c for c in body["caveats"]))

    def test_identify_no_pm_downgrades_confidence(self):
        fake = FakeCatalogClient(cat.normalize_match(_b_response([_candidate()])))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post_identify(
                {"ra": 269.446165, "dec": 4.769536, "mjd": 61210.0, "sigma_arcsec": 0.5}
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "known_star")
        self.assertLess(body["confidence"], 0.9)
        self.assertTrue(any("自行" in c or "PM" in c for c in body["caveats"]))

    def test_identify_invalid_input_is_400(self):
        with mock.patch.object(server, "CATALOG_CLIENT", FakeCatalogClient(cat.normalize_match(_b_response([_candidate()])))):
            status, body = self._post_identify({"ra": 999.0, "dec": 20.0, "mjd": 61210.0})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "INVALID_RA")

    def test_ui_copy_has_no_positive_discovery_claims(self):
        web = os.path.join(APP, "web")
        with open(os.path.join(web, "app.js"), encoding="utf-8") as fh:
            app_js = fh.read()
        with open(os.path.join(web, "index.html"), encoding="utf-8") as fh:
            index = fh.read()
        for text in (app_js, index):
            for phrase in ("新恒星", "新行星", "首次发现", "发现新"):
                self.assertNotIn(phrase, text)
        self.assertIn("未匹配不等于新天体", app_js)
        for status in ("known_star", "ambiguous", "uncatalogued_candidate", "catalog_unavailable"):
            self.assertIn(status, app_js)

    def test_http_analyze_has_identification_and_200(self):
        fake = FakeCatalogClient(cat.normalize_match(_b_response([_candidate()])))
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            status, body = self._post({"target_id": "barnard", "mode": "synthetic", "fov": 0.2})
        self.assertEqual(status, 200, body)
        ident = body["identification"]
        self.assertEqual(ident["status"], "known_star")
        self.assertEqual(ident["catalog_id"], BARNARD_ID)
        self.assertIn("schema_version", body)
        self.assertIn("sources", body)

    def test_http_analyze_when_catalog_down_is_still_200(self):
        class Down:
            def match(self, **kwargs):
                return cat.normalize_match({"unavailable": True, "reason": "catalog_not_imported"})

        with mock.patch.object(server, "CATALOG_CLIENT", Down()):
            status, body = self._post({"target_id": "barnard", "mode": "synthetic", "fov": 0.2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["identification"]["status"], "catalog_unavailable")
        self.assertEqual(body["identification"]["catalog_reason"], "catalog_not_imported")
        self.assertEqual(body["result"]["class"], "L")


if __name__ == "__main__":
    unittest.main()
