"""T2 数据桥接（Epoch Resolver）的单元测试。

不联网：DB 用本地 fake HTTP server；取数用 mock；不写 app/data 缓存。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core import data as data_layer  # noqa: E402
from core import epoch_source  # noqa: E402

_MISSING = object()

BARNARD_RA = 269.452077
BARNARD_DEC = 4.693365


# --------------------------------------------------------------------------- fixtures


def epoch_dict(obsid, detector, mjd, sep=0.2, date_obs=_MISSING, key=None, provenance="real", **over):
    key = key if key is not None else f"dr1/level2/2025W01_1A/l2b-v27-2026-231/{detector}/level2_{obsid}D{detector}.fits"
    out = {
        "obsid": obsid,
        "detector": detector,
        "release": "dr1",
        "week": "2025W01_1A",
        "version": "l2b-v27-2026-231",
        "mjd": float(mjd),
        "date_obs": epoch_source.mjd_to_iso(mjd) if date_obs is _MISSING else date_obs,
        "sep_deg": sep,
        "key": key,
        "slice_url": "",
        "provenance": provenance,
        "frame_id": 1,
    }
    out.update(over)
    return out


def barnard_epochs():
    return [
        epoch_dict("2025W35_2A_0221_1", 2, 60916.656062, 0.229, "2025-08-29T15:44:43.747"),
        epoch_dict("2025W35_2A_0233_1", 1, 60916.721970, 1.189, "2025-08-29T17:19:38.192"),
        epoch_dict("2025W36_2B_0123_1", 1, 60923.107203, 0.880, "2025-09-05T02:34:22.352"),
        epoch_dict("2026W12_2A_0324_1", 3, 61120.278599, 1.851, "2026-03-21T06:41:10.918"),
    ]


def legacy_row(obsid, detector, mjd, sep=0.2, date_obs=None):
    return {
        "id": 1,
        "target_id": "barnard",
        "frame_id": 1,
        "release": "dr1",
        "week": "2025W01_1A",
        "obs_id": obsid,
        "detector": detector,
        "mjd": float(mjd),
        "date_obs": date_obs if date_obs is not None else epoch_source.mjd_to_iso(mjd),
        "sep_deg": sep,
        "rank": 0,
        "fits_key": f"dr1/level2/2025W01_1A/l2b-v27-2026-231/{detector}/level2_{obsid}D{detector}.fits",
        "slice_url": "https://example.invalid/?size=0.05",
        "version": "l2b-v27-2026-231",
    }


def catalog_epochs(n, start=60000.0, span=183.0):
    out = []
    for i in range(n):
        mjd = start + (span * i / (n - 1) if n > 1 else 0.0)
        out.append(
            {
                "obsid": f"2025W01_1A_{i:04d}_1",
                "detector": 2,
                "release": "dr1",
                "week": "2025W01_1A",
                "version": "v-test",
                "mjd": float(mjd),
                "date_obs": epoch_source.mjd_to_iso(mjd),
                "sep_deg": 0.1,
                "key": f"dr1/level2/2025W01_1A/v-test/2/f{i}.fits",
                "slice_url": "",
                "provenance": "real",
            }
        )
    return out


def fake_epoch(mjd, provenance="real"):
    return SimpleNamespace(mjd=float(mjd), provenance=provenance, band="D2")


def fake_sim(target, times, fov_deg=0.2, seed=7):
    epochs = [fake_epoch(t, provenance="synthetic") for t in times]
    return epochs, {"mu_mas_yr": 1000.0, "pa_deg": 90.0}


# --------------------------------------------------------------------------- fake DB


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003 - silence
        pass

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        status, body, delay = self.server.route(parsed.path, query)  # type: ignore[attr-defined]
        if delay:
            time.sleep(delay)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


class FakeDB:
    def __init__(self, route):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.route = route  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def v1_route(epochs, coverage=None, matched="barnard", delay=0.0):
    def route(path, query):
        if path == "/api/v1/epochs":
            return 200, {
                "schema_version": "1.0",
                "epochs": epochs,
                "coverage": coverage or {"n_epochs": len(epochs), "baseline_days": 203.62, "coverage_level": "C2"},
                "matched_target_id": matched,
                "count": len(epochs),
            }, delay
        return 404, {"error": "not found"}, 0.0

    return route


def legacy_route(legacy_target_rows, legacy_radec_rows=None):
    legacy_radec_rows = legacy_radec_rows or []

    def route(path, query):
        if path == "/api/v1/epochs":
            return 404, {"error": "not found"}, 0.0
        if path == "/api/epochs":
            if query.get("target_id"):
                return 200, {"target_id": query["target_id"][0], "count": len(legacy_target_rows), "epochs": legacy_target_rows}, 0.0
            return 200, {"query": {}, "count": len(legacy_radec_rows), "epochs": legacy_radec_rows}, 0.0
        return 404, {"error": "not found"}, 0.0

    return route


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------- resolver tests


class ResolverTest(unittest.TestCase):
    def test_v1_barnard_four_epochs(self):
        with FakeDB(v1_route(barnard_epochs())) as db:
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.source, "db")
        self.assertEqual(res.transport, "v1")
        self.assertIsNone(res.fallback_reason)
        self.assertEqual(res.matched_target_id, "barnard")
        self.assertEqual(res.coverage["n_epochs"], 4)
        self.assertEqual([e["mjd"] for e in res.epochs], sorted(e["mjd"] for e in res.epochs))
        self.assertTrue(all(e["date_obs"] for e in res.epochs))
        self.assertIn("size=0.25", res.epochs[0]["slice_url"])
        self.assertEqual(res.warnings, [])

    def test_empty_response_is_no_local_coverage(self):
        with FakeDB(v1_route([], coverage={"n_epochs": 0, "coverage_level": "C0"})) as db:
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.fallback_reason, "NO_LOCAL_COVERAGE")
        self.assertEqual(res.source, "db")
        # DB 不可用时才允许本地 JSON 回退；这里 DB 正常返回空，不能拿本地 catalog 冒充
        self.assertEqual(res.matched_target_id, "barnard")

    def test_http_500_is_db_unavailable(self):
        def route(path, query):
            return 500, {"error": "boom"}, 0.0

        with FakeDB(route) as db:
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertEqual(res.source, "none")
        self.assertNotEqual(res.source, "local_json")

    def test_timeout_is_db_unavailable(self):
        with FakeDB(v1_route(barnard_epochs(), delay=0.5)) as db:
            started = time.time()
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url, timeout=0.12, allow_legacy=False)
            elapsed = time.time() - started
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertLess(elapsed, 2.0)

    def test_bad_json_is_db_unavailable(self):
        def route(path, query):
            return 200, b"this is not json", 0.0

        with FakeDB(route) as db:
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")

    def test_missing_key_is_dropped_with_warning(self):
        rows = barnard_epochs()
        rows[1]["key"] = ""
        with FakeDB(v1_route(rows, matched=None)) as db:
            res = epoch_source.resolve_epochs(10.0, 20.0, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(len(res.epochs), 3)
        self.assertTrue(any("key" in w for w in res.warnings), res.warnings)
        self.assertIsNone(res.fallback_reason)

    def test_date_obs_backfilled_from_mjd(self):
        rows = [epoch_dict("2025W01_1A_0001_1", 2, 60000.5, date_obs=None)]
        with FakeDB(v1_route(rows, matched=None)) as db:
            res = epoch_source.resolve_epochs(10.0, 20.0, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(len(res.epochs), 1)
        self.assertTrue(res.epochs[0]["date_obs"])
        self.assertTrue(res.epochs[0]["date_obs"].startswith("2023-"))
        self.assertTrue(any("date_obs" in w for w in res.warnings), res.warnings)

    def test_dedupe_and_sort(self):
        rows = [
            epoch_dict("A", 2, 60000.0, sep=0.5),
            epoch_dict("A", 2, 60000.0, sep=0.1),
            epoch_dict("B", 1, 60000.005, sep=0.3),
            epoch_dict("C", 1, 60001.0, sep=0.4),
        ]
        with FakeDB(v1_route(rows, matched=None)) as db:
            res = epoch_source.resolve_epochs(10.0, 20.0, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(len(res.epochs), 2)
        self.assertEqual(res.epochs[0]["obsid"], "A")
        self.assertEqual(res.epochs[0]["sep_deg"], 0.1)
        self.assertEqual(res.epochs[1]["obsid"], "C")
        self.assertEqual([e["mjd"] for e in res.epochs], sorted(e["mjd"] for e in res.epochs))

    def test_curated_target_beats_more_frames(self):
        def route(path, query):
            if path != "/api/v1/epochs":
                return 404, {}, 0.0
            if query.get("target_id"):
                return 200, {"epochs": barnard_epochs(), "count": 4, "matched_target_id": "barnard"}, 0.0
            # 模拟旧端点：ra/dec 锥搜索更多 frames，但可能漏掉长基线 curated 历元
            return 200, {"epochs": barnard_epochs()[:3] + [epoch_dict("EXTRA", 1, 60930.0)], "count": 4}, 0.0

        with FakeDB(route) as db:
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(len(res.epochs), 4)
        self.assertNotIn("EXTRA", [e["obsid"] for e in res.epochs])

    def test_legacy_target_id_normalized(self):
        rows = [
            legacy_row("2025W35_2A_0221_1", 2, 60916.656062, 0.229),
            legacy_row("2025W35_2A_0221_1", 5, 60916.656062, 0.238),
            legacy_row("2025W35_2A_0233_1", 1, 60916.721970, 1.189),
            legacy_row("2025W36_2B_0123_1", 1, 60923.107203, 0.880),
            legacy_row("2026W12_2A_0324_1", 3, 61120.278599, 1.851),
        ]
        with FakeDB(legacy_route(rows)) as db:
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url)
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.source, "db_legacy")
        self.assertEqual(res.transport, "legacy")
        self.assertIsNone(res.fallback_reason)
        self.assertEqual(res.epochs[0]["obsid"], "2025W35_2A_0221_1")
        self.assertIn("size=0.25", res.epochs[0]["slice_url"])

    def test_legacy_radec_unknown_sky(self):
        rows = [epoch_dict("2025W01_1A_0001_1", 2, 60000.0)]
        with FakeDB(legacy_route([], rows)) as db:
            res = epoch_source.resolve_epochs(10.0, 20.0, 0.25, base_url=db.url)
        self.assertEqual(len(res.epochs), 1)
        self.assertEqual(res.source, "db_legacy")
        self.assertEqual(res.matched_target_id, None)

    def test_db_unavailable_custom_does_not_fall_back_to_local_catalog(self):
        def route(path, query):
            return 500, {"error": "down"}, 0.0

        with FakeDB(route) as db:
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=db.url, allow_legacy=False)
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertNotEqual(res.source, "local_json")
        self.assertEqual(res.matched_target_id, "barnard")

    def test_explicit_target_id_can_fall_back_to_local_json(self):
        def route(path, query):
            return 500, {"error": "down"}, 0.0

        with FakeDB(route) as db:
            res = epoch_source.resolve_epochs(
                BARNARD_RA, BARNARD_DEC, 0.25, target_id="barnard", base_url=db.url, allow_legacy=False
            )
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.source, "local_json")
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertEqual(res.matched_target_id, "barnard")

    def test_connection_refused_is_db_unavailable(self):
        url = f"http://127.0.0.1:{free_port()}"
        res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=url, timeout=0.2, allow_legacy=False)
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertEqual(res.epochs, [])

    def test_env_base_url(self):
        with FakeDB(v1_route(barnard_epochs(), matched=None)) as db, mock.patch.dict(
            os.environ, {epoch_source.DB_URL_ENV: db.url}
        ):
            res = epoch_source.resolve_epochs(10.0, 20.0, 0.25, allow_legacy=False)
        self.assertEqual(len(res.epochs), 4)

    def test_match_known_target_covers_wise_ambiguity(self):
        target = epoch_source.match_known_target(133.795129 + 0.027, -7.245147)
        self.assertIsNotNone(target)
        self.assertEqual(target["id"], "wise0855")
        self.assertIsNone(epoch_source.match_known_target(133.795129 + 0.08, -7.245147))

    def test_invalid_query_raises_value_error(self):
        with self.assertRaises(ValueError):
            epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.0)
        with self.assertRaises(ValueError):
            epoch_source.resolve_epochs(BARNARD_RA, 100.0, 0.25)
        with self.assertRaises(ValueError):
            epoch_source.resolve_epochs(float("nan"), 0.0, 0.25)

    def test_coverage_levels(self):
        self.assertEqual(epoch_source.coverage_for_epochs([])["coverage_level"], "C0")
        self.assertEqual(epoch_source.coverage_for_epochs([{"mjd": 1.0, "detector": 1}])["coverage_level"], "C0")
        self.assertEqual(
            epoch_source.coverage_for_epochs([{"mjd": 1.0, "detector": 1}, {"mjd": 2.0, "detector": 1}])["coverage_level"],
            "C1",
        )
        self.assertEqual(
            epoch_source.coverage_for_epochs([{"mjd": float(i), "detector": 1} for i in range(4)])["coverage_level"],
            "C2",
        )
        six = [{"mjd": float(i) * 40, "detector": 1} for i in range(6)]
        self.assertEqual(epoch_source.coverage_for_epochs(six)["coverage_level"], "C3")


# --------------------------------------------------------------------------- build_dataset tests


class BuildDatasetTest(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(data_layer, "generate_epochs", side_effect=fake_sim),
        ]
        for patcher in self._patches:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _barnard_catalog(self, n=8, span=183.0):
        return {"barnard": catalog_epochs(n, span=span)}

    def test_preset_fetch_all_failed_is_unavailable_not_baseline(self):
        with mock.patch.object(data_layer, "load_epoch_catalog", return_value=self._barnard_catalog(8, 183)), mock.patch.object(
            data_layer, "fetch_epoch", return_value=None
        ):
            target, epochs, truth, meta = data_layer.build_dataset("barnard", mode="auto", size_deg=0.25)
        self.assertEqual(meta["mode"], "unavailable")
        self.assertEqual(meta["fallback_reason"], "FETCH_FAILED")
        self.assertEqual(meta["real_epochs"], 0)
        self.assertNotIn("基线仅 183 天", meta["note"])
        self.assertIn("取数全部失败", meta["note"])
        self.assertEqual(len(epochs), 8)
        self.assertIsNone(truth)

    def test_preset_fetch_one_success_is_insufficient(self):
        cat = catalog_epochs(8, span=183)
        first_mjd = min(e["mjd"] for e in cat)

        def fake_fetch(ep, ra, dec, size):
            return fake_epoch(ep["mjd"]) if ep["mjd"] == first_mjd else None

        with mock.patch.object(data_layer, "load_epoch_catalog", return_value={"barnard": cat}), mock.patch.object(
            data_layer, "fetch_epoch", side_effect=fake_fetch
        ):
            target, epochs, truth, meta = data_layer.build_dataset("barnard", mode="auto", size_deg=0.25)
        self.assertEqual(meta["mode"], "hybrid")
        self.assertEqual(meta["fallback_reason"], "INSUFFICIENT_EPOCHS")
        self.assertEqual(meta["real_epochs"], 1)
        self.assertEqual(len(epochs), 8)
        self.assertIn("1 期", meta["note"])

    def test_preset_no_catalog_is_no_local_coverage(self):
        with mock.patch.object(data_layer, "load_epoch_catalog", return_value={"barnard": []}):
            target, epochs, truth, meta = data_layer.build_dataset("barnard", mode="auto", size_deg=0.25)
        self.assertEqual(meta["mode"], "synthetic")
        self.assertEqual(meta["fallback_reason"], "NO_LOCAL_COVERAGE")
        self.assertEqual(meta["real_epochs"], 0)
        self.assertEqual(len(epochs), 8)

    def test_preset_real_path_unchanged(self):
        cat = catalog_epochs(4, span=203)
        fetched = [fake_epoch(e["mjd"]) for e in cat]
        with mock.patch.object(data_layer, "load_epoch_catalog", return_value={"barnard": cat}), mock.patch.object(
            data_layer, "fetch_epoch", side_effect=fetched
        ):
            target, epochs, truth, meta = data_layer.build_dataset("barnard", mode="auto", size_deg=0.25)
        self.assertEqual(meta["mode"], "real")
        self.assertIsNone(meta["fallback_reason"])
        self.assertEqual(meta["real_epochs"], 4)
        self.assertEqual(len(epochs), 4)
        self.assertIn("4/4", meta["note"])

    def test_preset_short_baseline_is_hybrid(self):
        with mock.patch.object(data_layer, "load_epoch_catalog", return_value=self._barnard_catalog(3, 1.2)), mock.patch.object(
            data_layer, "fetch_epoch"
        ) as fetch:
            target, epochs, truth, meta = data_layer.build_dataset("barnard", mode="auto", size_deg=0.25)
        fetch.assert_not_called()
        self.assertEqual(meta["mode"], "hybrid")
        self.assertEqual(meta["fallback_reason"], "BASELINE_TOO_SHORT")
        self.assertEqual(len(epochs), 8)

    def test_preset_explicit_synthetic_has_null_reason(self):
        with mock.patch.object(data_layer, "load_epoch_catalog", return_value=self._barnard_catalog(4, 203)), mock.patch.object(
            data_layer, "fetch_epoch"
        ) as fetch:
            target, epochs, truth, meta = data_layer.build_dataset("barnard", mode="synthetic", size_deg=0.25)
        fetch.assert_not_called()
        self.assertEqual(meta["mode"], "synthetic")
        self.assertIsNone(meta["fallback_reason"])

    # ---- custom

    def _resolution(self, epochs, matched="barnard", reason=None, source="db"):
        return epoch_source.EpochResolution(
            epochs=epochs,
            coverage=epoch_source.coverage_for_epochs(epochs),
            matched_target_id=matched,
            fallback_reason=reason,
            source=source,
            transport="v1" if source == "db" else None,
        )

    def test_custom_matched_barnard_real(self):
        eps = barnard_epochs()
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=self._resolution(eps)), mock.patch.object(
            data_layer, "fetch_epoch", side_effect=[fake_epoch(e["mjd"]) for e in eps]
        ):
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="auto", size_deg=0.25, ra=BARNARD_RA, dec=BARNARD_DEC
            )
        self.assertEqual(meta["mode"], "real")
        self.assertIsNone(meta["fallback_reason"])
        self.assertEqual(meta["real_epochs"], 4)
        self.assertEqual(len(epochs), 4)
        self.assertEqual(meta["matched_target_id"], "barnard")
        self.assertEqual(target["ra"], BARNARD_RA)
        self.assertAlmostEqual(target["coord_unc_arcsec"], 3.0)
        self.assertEqual(target["truth"]["pmra_mas_yr"], -801.551)

    def test_custom_db_unavailable_is_synthetic(self):
        res = self._resolution([], matched="barnard", reason="DB_UNAVAILABLE", source="none")
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=res):
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="auto", size_deg=0.25, ra=BARNARD_RA, dec=BARNARD_DEC
            )
        self.assertEqual(meta["mode"], "synthetic")
        self.assertEqual(meta["fallback_reason"], "DB_UNAVAILABLE")
        self.assertEqual(meta["real_epochs"], 0)
        self.assertEqual(len(epochs), 8)
        self.assertIn("DB_UNAVAILABLE", meta["note"])
        self.assertEqual(target["truth"], {})
        self.assertEqual(meta["matched_target_id"], "barnard")

    def test_custom_uncovered_is_synthetic(self):
        res = self._resolution([], matched=None, reason="NO_LOCAL_COVERAGE", source="db")
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=res):
            target, epochs, truth, meta = data_layer.build_dataset("custom", mode="auto", size_deg=0.25, ra=0.0, dec=0.0)
        self.assertEqual(meta["mode"], "synthetic")
        self.assertEqual(meta["fallback_reason"], "NO_LOCAL_COVERAGE")
        self.assertEqual(len(epochs), 8)

    def test_custom_fetch_all_failed_is_unavailable(self):
        eps = barnard_epochs()
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=self._resolution(eps)), mock.patch.object(
            data_layer, "fetch_epoch", return_value=None
        ):
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="auto", size_deg=0.25, ra=BARNARD_RA, dec=BARNARD_DEC
            )
        self.assertEqual(meta["mode"], "unavailable")
        self.assertEqual(meta["fallback_reason"], "FETCH_FAILED")
        self.assertEqual(meta["real_epochs"], 0)
        self.assertIn("取数全部失败", meta["note"])
        self.assertNotIn("基线仅", meta["note"])
        self.assertEqual(len(epochs), 8)
        self.assertIsNone(truth)

    def test_custom_one_real_is_hybrid_insufficient(self):
        eps = [epoch_dict("2026W37_2A_0230_1", 5, 61294.601216, 1.843)]
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=self._resolution(eps, matched="rw_aur")), mock.patch.object(
            data_layer, "fetch_epoch", return_value=fake_epoch(61294.601216)
        ):
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="auto", size_deg=0.25, ra=76.956526, dec=30.401438
            )
        self.assertEqual(meta["mode"], "hybrid")
        self.assertEqual(meta["fallback_reason"], "INSUFFICIENT_EPOCHS")
        self.assertEqual(meta["real_epochs"], 1)
        self.assertEqual(len(epochs), 8)
        self.assertEqual(sum(1 for e in epochs if e.provenance == "real"), 1)

    def test_custom_short_baseline_is_hybrid(self):
        eps = [
            epoch_dict("R1", 2, 60000.0),
            epoch_dict("R2", 2, 60001.0),
        ]
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=self._resolution(eps, matched="r_dra")), mock.patch.object(
            data_layer, "fetch_epoch", side_effect=[fake_epoch(60000.0), fake_epoch(60001.0)]
        ):
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="auto", size_deg=0.25, ra=248.167611, dec=66.754979
            )
        self.assertEqual(meta["mode"], "hybrid")
        self.assertEqual(meta["fallback_reason"], "BASELINE_TOO_SHORT")
        self.assertEqual(meta["real_epochs"], 2)
        self.assertEqual(len(epochs), 8)

    def test_custom_unknown_sky_real(self):
        eps = [epoch_dict("U1", 2, 60000.0), epoch_dict("U2", 2, 60100.0)]
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=self._resolution(eps, matched=None)), mock.patch.object(
            data_layer, "fetch_epoch", side_effect=[fake_epoch(60000.0), fake_epoch(60100.0)]
        ):
            target, epochs, truth, meta = data_layer.build_dataset("custom", mode="auto", size_deg=0.25, ra=10.0, dec=20.0)
        self.assertEqual(meta["mode"], "real")
        self.assertEqual(meta["real_epochs"], 2)
        self.assertEqual(len(epochs), 2)
        self.assertEqual(target["truth"], {})
        self.assertIsNone(meta["matched_target_id"])

    def test_custom_explicit_synthetic_skips_db(self):
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs") as resolve:
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="synthetic", size_deg=0.25, ra=10.0, dec=20.0
            )
        resolve.assert_not_called()
        self.assertEqual(meta["mode"], "synthetic")
        self.assertIsNone(meta["fallback_reason"])
        self.assertEqual(len(epochs), 8)

    def test_custom_missing_coordinates_raises(self):
        with self.assertRaises(ValueError):
            data_layer.build_dataset("custom", mode="auto", size_deg=0.25)


if __name__ == "__main__":
    unittest.main()
