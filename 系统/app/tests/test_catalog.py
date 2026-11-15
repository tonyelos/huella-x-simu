"""星表客户端与 D 阶段识别分类的单元测试（不联网）。

覆盖：
- B 不可达 / 503 / 坏 JSON → catalog_unavailable，绝不变成 uncatalogued；
- known / ambiguous / empty(ok) / empty(partial) / 无目标；
- catalog_id 始终字符串；B 返回 ambiguous 时不硬选亮者；
- app 只把 MJD/误差传给 B，不自己传播星表 PM。
"""
import io
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

from core import catalog as cat  # noqa: E402

TARGET = {"ra": 269.446165, "dec": 4.769536, "mjd": 61210.0, "sigma_arcsec": 1.0}
MOTION = {
    "moving": True,
    "mu_mas_yr": 10393.4,
    "mu_err_mas_yr": 60.0,
    "pa_deg": 355.6,
    "n_epochs": 4,
    "detected_epochs": 4,
}


def _candidate(cid="4472832130942575872", sep=0.12, **overrides):
    out = {
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
        "parallax_error": 0.0401,
        "ruwe": 1.0848505,
        "astrometric_excess_noise": 0.2048,
        "phot_variable_flag": "NOT_AVAILABLE",
        "gmag": 8.19397,
        "bp_rp": 2.8337,
        "jmag": 5.24,
        "hmag": 4.63,
        "kmag": 4.39,
        "w1": 4.10,
        "w2": 3.90,
        "2mass": "2MASS J17574849+0441405",
        "wise": "WISEA J175748.51+044132.9",
        "object_type": "star",
        "confidence": 0.99,
    }
    out.update(overrides)
    return out


def _response(candidates=None, *, catalog_status="ok", field_id="barnard", match_status=None):
    candidates = list(candidates or [])
    if match_status is None:
        match_status = "ambiguous" if len(candidates) > 1 else ("single_candidate" if candidates else "no_match")
    return {
        "schema_version": "1.0",
        "star_schema_version": 2,
        "service": "star-catalog",
        "release": "gaia-dr3",
        "version": "dr3",
        "releases": [{"release": "gaia-dr3"}],
        "unavailable": False,
        "catalog_status": catalog_status,
        "query": {"catalogs": ["gaia"], "radius_arcsec": 3.0, "epoch": 61210.0, "n_detections": 1},
        "matches": [
            {
                "index": 0,
                "detection_id": "target",
                "ra": TARGET["ra"],
                "dec": TARGET["dec"],
                "epoch_mjd": TARGET["mjd"],
                "sigma_arcsec": 1.0,
                "match_radius_arcsec": 3.0,
                "coverage": {
                    "gaia": {
                        "complete": catalog_status == "ok",
                        "field_id": field_id,
                        "n_cached_sources": 9603,
                    }
                },
                "catalog_status": catalog_status,
                "n_candidates": len(candidates),
                "ambiguous": len(candidates) > 1,
                "status": match_status,
                "candidates": candidates,
            }
        ],
        "n_detections": 1,
        "n_matched": 0 if not candidates else 1,
        "n_ambiguous": 1 if len(candidates) > 1 else 0,
        "n_unmatched": 1 if not candidates else 0,
    }


class _FakeResponse:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.last_request = None
        self.last_timeout = None

    def open(self, request, timeout=None):
        self.last_request = request
        self.last_timeout = timeout
        if self.exc is not None:
            raise self.exc
        return _FakeResponse(self.payload)


class ClientTransportTest(unittest.TestCase):
    def _client(self, payload=None, exc=None):
        opener = _FakeOpener(payload=payload, exc=exc)
        return cat.CatalogClient(base_url="http://127.0.0.1:8799", timeout=1.5, opener=opener), opener

    def test_passes_only_mjd_sigma_and_epoch_no_pm(self):
        client, opener = self._client(payload=_response([_candidate()]))
        out = client.match(ra=TARGET["ra"], dec=TARGET["dec"], mjd=TARGET["mjd"], sigma_arcsec=1.0)
        self.assertTrue(out["ok"])
        payload = json.loads(opener.last_request.data.decode("utf-8"))
        det = payload["detections"][0]
        self.assertEqual(det["mjd"], TARGET["mjd"])
        self.assertEqual(payload["epoch"], TARGET["mjd"])
        self.assertEqual(det["sigma_arcsec"], 1.0)
        self.assertNotIn("pmra_mas_yr", det)
        self.assertNotIn("pmdec_mas_yr", det)
        self.assertFalse(payload["persist"])

    def test_passes_optional_pm_to_b(self):
        client, opener = self._client(payload=_response([_candidate()]))
        out = client.match(
            ra=TARGET["ra"],
            dec=TARGET["dec"],
            mjd=TARGET["mjd"],
            sigma_arcsec=1.0,
            pmra_mas_yr=-801.55,
            pmdec_mas_yr=10362.39,
        )
        self.assertTrue(out["ok"])
        det = json.loads(opener.last_request.data.decode("utf-8"))["detections"][0]
        self.assertEqual(det["pmra_mas_yr"], -801.55)
        self.assertEqual(det["pmdec_mas_yr"], 10362.39)

    def test_persist_true_passes_stable_analysis_id(self):
        client, opener = self._client(payload=_response([_candidate()]))
        out = client.match(
            ra=TARGET["ra"], dec=TARGET["dec"], mjd=TARGET["mjd"],
            analysis_id="d-identify:abc", persist=True,
        )
        self.assertTrue(out["ok"])
        payload = json.loads(opener.last_request.data.decode("utf-8"))
        self.assertTrue(payload["persist"])
        self.assertEqual(payload["analysis_id"], "d-identify:abc")

    def test_persist_without_analysis_id_is_unavailable(self):
        client, _ = self._client(payload=_response([_candidate()]))
        out = client.match(ra=TARGET["ra"], dec=TARGET["dec"], mjd=TARGET["mjd"], persist=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["catalog_reason"], "PERSIST_ANALYSIS_ID_MISSING")

    def test_http_503_is_catalog_unavailable_not_empty(self):
        body = json.dumps(
            {
                "code": "CATALOG_UNAVAILABLE",
                "message": "查询中心不在任何已缓存场覆盖范围内",
                "reason": "no_cached_field",
            }
        ).encode("utf-8")
        exc = urllib.error.HTTPError(
            "http://127.0.0.1:8799/api/v1/catalog/match",
            503,
            "Service Unavailable",
            None,
            io.BytesIO(body),
        )
        client, _ = self._client(exc=exc)
        out = client.match(ra=TARGET["ra"], dec=TARGET["dec"], mjd=TARGET["mjd"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["catalog_status"], "unavailable")
        self.assertEqual(out["catalog_reason"], "no_cached_field")
        self.assertEqual(out["n_candidates"], 0)
        ident = cat.identify(out, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "catalog_unavailable")
        self.assertNotEqual(ident["status"], "uncatalogued_candidate")

    def test_connection_refused_is_catalog_unavailable(self):
        client, _ = self._client(exc=urllib.error.URLError(ConnectionRefusedError("refused")))
        out = client.match(ra=TARGET["ra"], dec=TARGET["dec"], mjd=TARGET["mjd"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["catalog_status"], "unavailable")
        ident = cat.identify(out, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "catalog_unavailable")
        self.assertEqual(ident["reason"], "CATALOG_UNAVAILABLE")

    def test_bad_json_is_unavailable(self):
        client, _ = self._client(payload=b"{not json")
        out = client.match(ra=TARGET["ra"], dec=TARGET["dec"], mjd=TARGET["mjd"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["catalog_reason"], "MALFORMED_RESPONSE")

    def test_no_cached_field_is_unavailable(self):
        result = cat.normalize_match(_response([], catalog_status="partial", field_id=None))
        self.assertFalse(result["ok"])
        self.assertEqual(result["catalog_reason"], "NO_CACHED_FIELD")
        self.assertEqual(
            cat.identify(result, target=TARGET, motion=MOTION)["status"], "catalog_unavailable"
        )


class ClassificationTest(unittest.TestCase):
    def test_known_star_has_string_id_and_release(self):
        result = cat.normalize_match(_response([_candidate()]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "known_star")
        self.assertIsInstance(ident["catalog_id"], str)
        self.assertEqual(ident["catalog_id"], "4472832130942575872")
        self.assertEqual(ident["release"], "gaia-dr3")
        self.assertEqual(ident["n_candidates"], 1)
        self.assertEqual(ident["pm_consistent"], True)
        self.assertAlmostEqual(ident["separation_arcsec"], 0.12)
        self.assertFalse(ident["discovery_claim"])
        self.assertNotIn("新恒星", ident["disclaimer"])

    def test_partial_single_candidate_is_known_but_degraded(self):
        # B 的 fixture 缓存永远 coverage.complete=false；Barnard 验收需要在
        # fixture 可用时仍能 known，因此 partial + 唯一候选降级 known。
        result = cat.normalize_match(_response([_candidate()], catalog_status="partial"))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "known_star")
        self.assertIn("CATALOG_PARTIAL", ident["quality"])
        self.assertLessEqual(ident["confidence"], 0.8)

    def test_ambiguous_does_not_pick_a_candidate(self):
        cands = [_candidate("156430822114424576", sep=0.0), _candidate("156430817820015232", sep=0.2163)]
        result = cat.normalize_match(_response(cands))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "ambiguous")
        self.assertIsNone(ident["catalog_id"])
        self.assertEqual(ident["n_candidates"], 2)
        self.assertEqual(len(ident["candidates"]), 2)
        self.assertIn("MULTIPLE_CANDIDATES", ident["quality"])

    def test_pm_inconsistent_is_ambiguous(self):
        result = cat.normalize_match(_response([_candidate(pmra=5000.0, pmdec=-5000.0)]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "ambiguous")
        self.assertEqual(ident["reason"], "PM_INCONSISTENT")
        self.assertIs(ident["pm_consistent"], False)

    def test_empty_ok_is_uncatalogued_candidate(self):
        result = cat.normalize_match(_response([], catalog_status="ok"))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "uncatalogued_candidate")
        self.assertEqual(ident["reason"], "NO_CATALOG_MATCH")
        self.assertIsNone(ident["catalog_id"])
        self.assertNotIn("新恒星", ident["disclaimer"])

    def test_empty_partial_is_insufficient_not_uncatalogued(self):
        result = cat.normalize_match(_response([], catalog_status="partial"))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "insufficient_data")
        self.assertEqual(ident["reason"], "CATALOG_PARTIAL")

    def test_no_target_is_insufficient(self):
        ident = cat.identify(None, target=None, motion=MOTION)
        self.assertEqual(ident["status"], "insufficient_data")
        self.assertEqual(ident["reason"], "TARGET_NOT_DETECTED")

    def test_catalog_id_is_coerced_to_string(self):
        result = cat.normalize_match(_response([_candidate(cid=4472832130942575872)]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertIsInstance(ident["catalog_id"], str)

    def test_high_ruwe_is_ambiguous_not_high_confidence_known(self):
        result = cat.normalize_match(_response([_candidate(ruwe=4.29554)]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "ambiguous")
        self.assertEqual(ident["reason"], "RUWE_HIGH")
        self.assertLessEqual(ident["confidence"], 0.4)
        self.assertIn("HIGH_RUWE", ident["quality"])
        self.assertEqual(ident["candidates"][0]["ruwe"], 4.29554)

    def test_missing_ruwe_downgrades_known(self):
        cand = _candidate()
        cand.pop("ruwe", None)
        result = cat.normalize_match(_response([cand]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "ambiguous")
        self.assertEqual(ident["reason"], "RUWE_UNAVAILABLE")
        self.assertLessEqual(ident["confidence"], 0.4)

    def test_infrared_photometry_passthrough(self):
        result = cat.normalize_match(_response([_candidate()]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "known_star")
        photo = ident["photometry"]
        self.assertEqual(photo["jmag"], 5.24)
        self.assertEqual(photo["hmag"], 4.63)
        self.assertEqual(photo["w1"], 4.10)
        self.assertEqual(photo["w2"], 3.90)
        self.assertAlmostEqual(photo["colors"]["j_h"], 0.61, places=3)
        self.assertAlmostEqual(photo["colors"]["w1_w2"], 0.20, places=3)
        self.assertTrue(photo["twomass_id"].startswith("2MASS"))
        self.assertTrue(photo["wise_id"].startswith("WISEA"))
        self.assertEqual(ident["catalog_schema_version"], 2)

    def test_null_photometry_is_not_fabricated(self):
        cand = _candidate(
            jmag=None, hmag=None, kmag=None, w1=None, w2=None,
            **{"2mass": None, "wise": None},
        )
        result = cat.normalize_match(_response([cand]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "known_star")
        self.assertIsNone(ident["photometry"])
        self.assertIsNone(ident["candidates"][0]["jmag"])

    def test_status_enum_has_no_discovery_values(self):
        statuses = set(cat.IDENTIFICATION_STATUSES)
        self.assertEqual(
            statuses,
            {
                "known_star",
                "ambiguous",
                "uncatalogued_candidate",
                "catalog_unavailable",
                "insufficient_data",
            },
        )
        self.assertNotIn("new_star", statuses)
        self.assertNotIn("new_planet", statuses)
        self.assertNotIn("discovery", statuses)

    def test_pm_consistency_not_claimed_for_single_epoch(self):
        result = cat.normalize_match(_response([_candidate()]))
        flat = dict(MOTION, moving=False)
        ident = cat.identify(result, target=TARGET, motion=flat)
        self.assertEqual(ident["status"], "known_star")
        self.assertIsNone(ident["pm_consistent"])


class OutputContractTest(unittest.TestCase):
    """D-MVP 任务要求的输出字段：所有状态同形，失败不伪装成未匹配。"""

    def _known(self):
        return cat.identify(cat.normalize_match(_response([_candidate()])), target=TARGET, motion=MOTION)

    def test_known_has_required_alias_fields(self):
        ident = self._known()
        self.assertEqual(ident["status"], "known_star")
        self.assertEqual(ident["catalog_release"], "gaia-dr3")
        self.assertEqual(ident["source_id"], "4472832130942575872")
        self.assertIsInstance(ident["source_id"], str)
        self.assertEqual(ident["sep_arcsec"], 0.12)
        self.assertIsInstance(ident["caveats"], list)
        self.assertFalse(ident["discovery_claim"])

    def test_all_statuses_have_alias_fields(self):
        cases = [
            self._known(),
            cat.identify(cat.normalize_match(_response([_candidate(), _candidate("2")])), target=TARGET, motion=MOTION),
            cat.identify(cat.normalize_match(_response([])), target=TARGET, motion=MOTION),
            cat.identify(cat.normalize_match({"unavailable": True}), target=TARGET, motion=MOTION),
            cat.identify(None, target=None, motion=None),
        ]
        for ident in cases:
            for key in ("status", "catalog_release", "source_id", "sep_arcsec", "confidence", "caveats", "discovery_claim"):
                self.assertIn(key, ident, ident)
            self.assertFalse(ident["discovery_claim"])
            self.assertIsInstance(ident["caveats"], list)

    def test_no_pm_downgrades_confidence_and_adds_caveat(self):
        flat = dict(MOTION, moving=False, mu_mas_yr=0.0, mu_err_mas_yr=0.0)
        ident = cat.identify(cat.normalize_match(_response([_candidate()])), target=TARGET, motion=flat)
        self.assertEqual(ident["status"], "known_star")
        self.assertLess(ident["confidence"], 0.9)
        self.assertTrue(any("自行" in c or "PM" in c for c in ident["caveats"]))

    @mock.patch.dict(os.environ, {"CATALOG_URL": "http://example.test:1234/"}, clear=False)
    def test_catalog_url_env_override(self):
        self.assertEqual(cat.catalog_base_url(), "http://example.test:1234")


class _FakeCatalogHandler(BaseHTTPRequestHandler):
    """本地 fake B 服务：只实现 POST /api/v1/catalog/match。"""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps(self.server.payload, ensure_ascii=False).encode("utf-8")
        self.send_response(self.server.status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class FakeCatalogHTTPServerTest(unittest.TestCase):
    """真实本地 HTTP 假服务，覆盖 known/ambiguous/no_match/503/partial。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCatalogHandler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)

    def _match(self, payload, status_code=200):
        self.httpd.status_code = status_code
        self.httpd.payload = payload
        return cat.CatalogClient(base_url=self.base, timeout=5.0).match(
            ra=TARGET["ra"], dec=TARGET["dec"], mjd=TARGET["mjd"], sigma_arcsec=1.0
        )

    def test_http_known_and_string_id(self):
        result = self._match(_response([_candidate()]))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "known_star")
        self.assertEqual(ident["source_id"], "4472832130942575872")
        self.assertIsInstance(ident["source_id"], str)

    def test_http_ambiguous(self):
        cands = [_candidate("156430822114424576", 0.0), _candidate("156430817820015232", 0.2163)]
        result = self._match(_response(cands))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "ambiguous")
        self.assertEqual(len(ident["candidates"]), 2)

    def test_http_no_match_ok_is_uncatalogued(self):
        result = self._match(_response([], catalog_status="ok"))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "uncatalogued_candidate")

    def test_http_partial_zero_is_insufficient(self):
        result = self._match(_response([], catalog_status="partial"))
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "insufficient_data")
        self.assertEqual(ident["reason"], "CATALOG_PARTIAL")

    def test_http_503_is_catalog_unavailable(self):
        result = self._match({"code": "CATALOG_UNAVAILABLE", "reason": "no_cached_field"}, status_code=503)
        ident = cat.identify(result, target=TARGET, motion=MOTION)
        self.assertEqual(ident["status"], "catalog_unavailable")
        self.assertEqual(ident["n_candidates"], 0)
        self.assertNotEqual(ident["status"], "uncatalogued_candidate")


if __name__ == "__main__":
    unittest.main()
