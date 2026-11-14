"""T2 数据桥接（Epoch Resolver）的单元测试。

不联网：DB 用本地 fake HTTP server（随机端口）；取数用 mock；不写 app/data 缓存。
测试只覆盖 app 侧适配层与现有管线 seam，不改 DB/app 其它文件。
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
RW_AUR_RA = 76.956526
RW_AUR_DEC = 30.401438


# --------------------------------------------------------------------------- fixtures


def epoch_dict(obsid, detector, mjd, sep=0.2, date_obs=_MISSING, key=None, provenance="real", week="2025W01_1A", **over):
    key = key if key is not None else f"dr1/level2/{week}/l2b-v27-2026-231/{detector}/level2_{obsid}D{detector}.fits"
    out = {
        "obsid": obsid,
        "detector": detector,
        "release": "dr1",
        "week": week,
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
    """与 DB v1 黄金样本一致的 Barnard 4 条历元（真实 obsid/MJD/date_obs）。"""
    return [
        epoch_dict("2025W35_2A_0221_1", 2, 60916.656062, 0.229, "2025-08-29T15:44:43.747", week="2025W35_2A"),
        epoch_dict("2025W35_2A_0233_1", 1, 60916.721970, 1.189, "2025-08-29T17:19:38.192", week="2025W35_2A"),
        epoch_dict("2025W36_2B_0123_1", 1, 60923.107203, 0.880, "2025-09-05T02:34:22.352", week="2025W36_2B"),
        epoch_dict("2026W12_2A_0324_1", 3, 61120.278599, 1.851, "2026-03-21T06:41:10.918", week="2026W12_2A"),
    ]


def rw_aur_epochs():
    """RW Aur 的真实 fixture：只有 1 条历元。"""
    return [
        epoch_dict("2026W37_2A_0230_1", 5, 61294.601216, 1.843, "2026-09-11T14:25:45.106"),
    ]


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

    def log_message(self, fmt, *args):  # noqa: A003 - silence test server
        pass

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.requests.append((parsed.path, query))  # type: ignore[attr-defined]
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
    """本地 fake v1 DB；记录每次请求，便于断言“没有走旧端点/没有重复查询”。"""

    def __init__(self, route):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.route = route  # type: ignore[attr-defined]
        self.requests: list[tuple[str, dict]] = []
        self.httpd.requests = self.requests  # type: ignore[attr-defined]
        self.httpd.lock = threading.Lock()  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True)

    @property
    def url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def paths(self):
        return [path for path, _ in self.requests]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def v1_payload(epochs, coverage=_MISSING, matched=_MISSING):
    body = {"schema_version": "1.0", "epochs": epochs, "count": len(epochs)}
    if coverage is not _MISSING:
        body["coverage"] = coverage
    if matched is not _MISSING:
        body["matched_target_id"] = matched
    return body


def v1_route(
    epochs,
    *,
    coverage=_MISSING,
    matched=_MISSING,
    delay=0.0,
    target_epochs=_MISSING,
    target_matched=_MISSING,
):
    def route(path, query):
        if path != "/api/v1/epochs":
            return 404, {"error": "not found"}, 0.0
        if query.get("target_id"):
            rows = epochs if target_epochs is _MISSING else target_epochs
            m = matched if target_matched is _MISSING else target_matched
            return 200, v1_payload(rows, coverage=coverage, matched=m), delay
        return 200, v1_payload(epochs, coverage=coverage, matched=matched), delay

    return route


def error_route(status=500, body=None):
    def route(path, query):
        return status, body if body is not None else {"error": "boom"}, 0.0

    return route


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def resolve(db, *args, **kwargs):
    return epoch_source.resolve_epochs(*args, base_url=db.url, **kwargs)


# --------------------------------------------------------------------------- resolver tests


class ResolverTest(unittest.TestCase):
    def test_v1_barnard_four_epochs_complete_dto(self):
        coverage = {"n_epochs": 4, "baseline_days": 203.62, "coverage_level": "C2"}
        with FakeDB(v1_route(barnard_epochs(), coverage=coverage, matched="barnard")) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
            self.assertEqual(db.paths, ["/api/v1/epochs"])
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.source, "db")
        self.assertEqual(res.transport, "v1")
        self.assertIsNone(res.fallback_reason)
        self.assertEqual(res.matched_target_id, "barnard")
        self.assertEqual(res.coverage, coverage)
        self.assertEqual([e["mjd"] for e in res.epochs], sorted(e["mjd"] for e in res.epochs))
        self.assertIn("size=0.25", res.epochs[0]["slice_url"])
        self.assertEqual(res.warnings, [])
        for epoch in res.epochs:
            for name in epoch_source.EPOCH_REF_FIELDS:
                self.assertIn(name, epoch)
                self.assertNotIn(epoch[name], (None, ""), f"{name} 不可为空")
            self.assertEqual(epoch["provenance"], "real")

    def test_v1_rw_aur_single_epoch_fixture(self):
        coverage = {"n_epochs": 1, "baseline_days": 0.0, "coverage_level": "C0"}
        with FakeDB(v1_route(rw_aur_epochs(), coverage=coverage, matched="rw_aur")) as db:
            res = resolve(db, RW_AUR_RA, RW_AUR_DEC, 0.5)
        self.assertEqual(len(res.epochs), 1)
        self.assertEqual(res.epochs[0]["obsid"], "2026W37_2A_0230_1")
        self.assertEqual(res.epochs[0]["detector"], 5)
        self.assertEqual(res.matched_target_id, "rw_aur")
        self.assertIsNone(res.fallback_reason)
        self.assertEqual(res.source, "db")

    def test_empty_response_is_no_local_coverage(self):
        with FakeDB(v1_route([], coverage={"n_epochs": 0, "coverage_level": "C0"}, matched="barnard")) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.fallback_reason, "NO_LOCAL_COVERAGE")
        self.assertEqual(res.source, "db")
        # DB 正常返回空时不能拿本地 catalog 冒充
        self.assertEqual(res.matched_target_id, "barnard")

    def test_http_500_is_db_unavailable(self):
        with FakeDB(error_route(500)) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertEqual(res.source, "none")
        self.assertIsNone(res.transport)
        self.assertEqual(res.matched_target_id, "barnard")
        self.assertTrue(any("HTTP 500" in w for w in res.warnings), res.warnings)

    def test_timeout_is_db_unavailable(self):
        with FakeDB(v1_route(barnard_epochs(), matched="barnard", delay=0.5)) as db:
            started = time.time()
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25, timeout=0.12)
            elapsed = time.time() - started
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertLess(elapsed, 2.0)

    def test_bad_json_is_db_unavailable(self):
        with FakeDB(error_route(200, b"this is not json")) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")

    def test_bad_shape_is_db_unavailable(self):
        with FakeDB(error_route(200, {"error": "not a contract payload"})) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
            self.assertEqual(len(db.requests), 1)
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")

    def test_epochs_not_a_list_is_db_unavailable(self):
        with FakeDB(error_route(200, {"schema_version": "1.0", "epochs": {"bad": True}})) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")

    def test_http_400_is_db_unavailable(self):
        with FakeDB(error_route(400, {"error": "bad fov"})) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")

    def test_v1_404_does_not_call_legacy_endpoint(self):
        def route(path, query):
            if path == "/api/epochs":  # 旧端点即使能返回数据也不许用
                return 200, {"epochs": barnard_epochs()}, 0.0
            return 404, {"error": "v1 not found"}, 0.0

        with FakeDB(route) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
            self.assertEqual(db.paths, ["/api/v1/epochs"])
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertEqual(res.source, "none")
        self.assertEqual(res.matched_target_id, "barnard")

    def test_v1_404_explicit_target_can_fall_back_to_local_json(self):
        with FakeDB(error_route(404, {"error": "v1 not deployed"})) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25, target_id="barnard")
            self.assertEqual(db.paths, ["/api/v1/epochs"])
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.source, "local_json")
        self.assertEqual(res.transport, "local_json")
        self.assertEqual(res.matched_target_id, "barnard")
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")

    def test_http_500_explicit_target_can_fall_back_to_local_json(self):
        with FakeDB(error_route(500)) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25, target_id="barnard")
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.source, "local_json")
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")

    def test_db_unavailable_custom_does_not_fall_back_to_local_catalog(self):
        # 坐标在 Barnard 0.05° 内，但自定义入口不允许用本地 catalog 冒充真实历元
        with FakeDB(error_route(500)) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.source, "none")
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertNotEqual(res.source, "local_json")
        self.assertEqual(res.matched_target_id, "barnard")

    def test_explicit_target_db_empty_does_not_use_local_json(self):
        with FakeDB(v1_route([], matched="barnard")) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25, target_id="barnard")
        self.assertEqual(res.epochs, [])
        self.assertEqual(res.source, "db")
        self.assertNotEqual(res.source, "local_json")
        self.assertEqual(res.fallback_reason, "NO_LOCAL_COVERAGE")

    def test_dto_validation_drops_invalid_rows_with_warnings(self):
        no_mjd = epoch_dict("NO_MJD", 2, 60930.2)
        no_mjd["mjd"] = None
        rows = barnard_epochs() + [
            epoch_dict("", 2, 60930.0),
            epoch_dict("NO_KEY", 2, 60930.1, key=""),
            no_mjd,
            epoch_dict("BAD_DET", 7, 60930.3),
            epoch_dict("NO_SEP", 2, 60930.4, sep=None),
        ]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 4)
        for needle in ("obsid", "key", "mjd", "detector", "sep_deg"):
            self.assertTrue(any(needle in w for w in res.warnings), (needle, res.warnings))

    def test_missing_key_is_dropped_with_warning(self):
        rows = barnard_epochs()
        rows[1]["key"] = ""
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 3)
        self.assertTrue(any("key" in w for w in res.warnings), res.warnings)
        self.assertIsNone(res.fallback_reason)

    def test_missing_obsid_is_dropped_with_warning(self):
        rows = barnard_epochs()
        rows[0]["obsid"] = ""
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 3)
        self.assertTrue(any("obsid" in w for w in res.warnings), res.warnings)

    def test_missing_mjd_is_dropped_with_warning(self):
        rows = barnard_epochs()
        rows[2]["mjd"] = None
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 3)
        self.assertTrue(any("mjd" in w for w in res.warnings), res.warnings)

    def test_date_obs_backfilled_from_mjd(self):
        rows = [epoch_dict("2025W01_1A_0001_1", 2, 60000.5, date_obs=None)]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 1)
        self.assertTrue(res.epochs[0]["date_obs"].startswith("2023-"))
        self.assertTrue(any("date_obs" in w for w in res.warnings), res.warnings)

    def test_date_obs_z_suffix_stripped(self):
        rows = [epoch_dict("2025W01_1A_0002_1", 2, 60000.5, date_obs="2023-02-25T00:00:00.000Z")]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(res.epochs[0]["date_obs"], "2023-02-25T00:00:00.000")

    def test_invalid_date_obs_is_backfilled_with_warning(self):
        rows = [epoch_dict("2025W01_1A_0003_1", 2, 60000.5, date_obs="yesterday")]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertTrue(res.epochs[0]["date_obs"].startswith("2023-"))
        self.assertTrue(any("格式非法" in w for w in res.warnings), res.warnings)

    def test_dedupe_same_obsid_detector_keeps_closest(self):
        rows = [
            epoch_dict("A", 2, 60000.0, sep=0.5),
            epoch_dict("A", 2, 60000.0, sep=0.1),
            epoch_dict("B", 1, 60000.005, sep=0.3),
            epoch_dict("C", 1, 60001.0, sep=0.4),
        ]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 2)
        self.assertEqual(res.epochs[0]["obsid"], "A")
        self.assertEqual(res.epochs[0]["sep_deg"], 0.1)
        self.assertEqual(res.epochs[1]["obsid"], "C")
        self.assertEqual([e["mjd"] for e in res.epochs], sorted(e["mjd"] for e in res.epochs))

    def test_same_mjd_across_detectors_keeps_nearest(self):
        rows = [
            epoch_dict("A", 2, 60000.000, sep=0.5),
            epoch_dict("B", 1, 60000.005, sep=0.1),
            epoch_dict("C", 3, 60000.010, sep=0.3),
            epoch_dict("D", 4, 60001.000, sep=0.2),
        ]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual([e["obsid"] for e in res.epochs], ["B", "D"])
        self.assertEqual([e["detector"] for e in res.epochs], [1, 4])

    def test_sep_deg_computed_from_frame_coordinates(self):
        rows = [epoch_dict("2025W01_1A_0004_1", 2, 60000.0, sep=None, frame_ra=10.0, frame_dec=20.0)]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 1)
        self.assertAlmostEqual(res.epochs[0]["sep_deg"], 0.0, places=6)
        self.assertTrue(any("sep_deg" in w for w in res.warnings), res.warnings)

    def test_coverage_computed_when_db_omits_it(self):
        with FakeDB(v1_route(barnard_epochs(), matched="barnard")) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
        self.assertEqual(res.coverage["n_epochs"], 4)
        self.assertEqual(res.coverage["coverage_level"], "C2")
        self.assertAlmostEqual(res.coverage["baseline_days"], 203.62, places=2)
        self.assertEqual(res.coverage["n_weeks"], 3)

    def test_coverage_from_db_is_preserved(self):
        coverage = {
            "radius_deg": 2.47,
            "n_epochs": 4,
            "baseline_days": 203.62,
            "coverage_level": "C2",
            "header_available": True,
        }
        with FakeDB(v1_route(barnard_epochs(), coverage=coverage, matched="barnard")) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
        self.assertEqual(res.coverage, coverage)

    def test_invalid_provenance_becomes_unknown(self):
        rows = [epoch_dict("2025W01_1A_0005_1", 2, 60000.0, provenance="fabricated")]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(res.epochs[0]["provenance"], "unknown")
        self.assertTrue(any("provenance" in w for w in res.warnings), res.warnings)

    def test_non_object_epoch_entry_is_dropped(self):
        rows = barnard_epochs() + ["not-an-epoch"]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(len(res.epochs), 4)
        self.assertTrue(any("非对象" in w for w in res.warnings), res.warnings)

    def test_retries_once_after_500(self):
        calls = {"n": 0}

        def route(path, query):
            calls["n"] += 1
            if calls["n"] == 1:
                return 500, {"error": "transient"}, 0.0
            return 200, v1_payload([epoch_dict("2025W01_1A_0006_1", 2, 60000.0)], matched=None), 0.0

        with FakeDB(route) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(res.epochs), 1)
        self.assertEqual(res.source, "db")

    def test_no_cross_request_cache(self):
        with FakeDB(v1_route([epoch_dict("2025W01_1A_0007_1", 2, 60000.0)], matched=None)) as db:
            first = resolve(db, 10.0, 20.0, 0.25)
            second = resolve(db, 10.0, 20.0, 0.25)
            self.assertEqual(len(db.requests), 2)
        self.assertEqual(len(first.epochs), 1)
        self.assertEqual(len(second.epochs), 1)

    def test_connection_refused_is_db_unavailable(self):
        url = f"http://127.0.0.1:{free_port()}"
        res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25, base_url=url, timeout=0.2)
        self.assertEqual(res.fallback_reason, "DB_UNAVAILABLE")
        self.assertEqual(res.epochs, [])

    def test_env_base_url(self):
        with FakeDB(v1_route(barnard_epochs(), matched="barnard")) as db, mock.patch.dict(
            os.environ, {epoch_source.DB_URL_ENV: db.url}
        ):
            res = epoch_source.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.25)
        self.assertEqual(len(res.epochs), 4)

    def test_db_base_url_default(self):
        with mock.patch.dict(os.environ, {epoch_source.DB_URL_ENV: ""}):
            self.assertEqual(epoch_source.db_base_url(), epoch_source.DEFAULT_DB_URL)
        self.assertEqual(epoch_source.db_base_url("http://example.test:1/"), "http://example.test:1")

    def test_explicit_target_id_queries_target_endpoint_only(self):
        with FakeDB(v1_route([], target_epochs=barnard_epochs(), target_matched="barnard")) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25, target_id="barnard")
            query = db.requests[0][1]
        self.assertEqual(query.get("target_id"), ["barnard"])
        self.assertNotIn("ra", query)
        self.assertEqual(len(res.epochs), 4)
        self.assertEqual(res.matched_target_id, "barnard")

    def test_unknown_target_id_uses_radec_endpoint(self):
        rows = [epoch_dict("2025W01_1A_0008_1", 2, 60000.0)]
        with FakeDB(v1_route(rows, matched=None)) as db:
            res = resolve(db, 10.0, 20.0, 0.25, target_id="no-such-target")
            query = db.requests[0][1]
        self.assertEqual(query.get("ra"), ["10.000000"])
        self.assertNotIn("target_id", query)
        self.assertEqual(len(res.epochs), 1)

    def test_db_matched_target_id_has_priority(self):
        with FakeDB(v1_route(barnard_epochs(), matched="barnard")) as db:
            res = resolve(db, 10.0, 20.0, 0.25)
            # DB 已给 matched_target_id，不需要再发 local target_id 查询
            self.assertEqual(len(db.requests), 1)
        self.assertEqual(res.matched_target_id, "barnard")

    def test_local_match_queries_target_when_db_omits_matched(self):
        frames = barnard_epochs()[:3] + [epoch_dict("EXTRA", 1, 60930.0)]
        with FakeDB(
            v1_route(
                frames,
                matched=None,
                target_epochs=barnard_epochs(),
                target_matched="barnard",
            )
        ) as db:
            res = resolve(db, BARNARD_RA, BARNARD_DEC, 0.25)
            self.assertEqual(len(db.requests), 2)
            self.assertEqual(db.requests[1][1].get("target_id"), ["barnard"])
        self.assertEqual(len(res.epochs), 4)
        self.assertNotIn("EXTRA", [e["obsid"] for e in res.epochs])
        self.assertEqual(res.matched_target_id, "barnard")

    def test_slice_url_uses_query_fov_and_center(self):
        rows = [epoch_dict("2025W01_1A_0009_1", 2, 60000.0)]
        with FakeDB(v1_route(rows, matched="nope")) as db:
            res = resolve(db, 10.0, 20.0, 0.5)
        self.assertIn("center=10.000000,20.000000d", res.epochs[0]["slice_url"])
        self.assertIn("size=0.5", res.epochs[0]["slice_url"])

    def test_match_known_target_barnard(self):
        target = epoch_source.match_known_target(BARNARD_RA, BARNARD_DEC)
        self.assertIsNotNone(target)
        self.assertEqual(target["id"], "barnard")
        self.assertEqual(target["coord_unc_arcsec"], 3.0)
        self.assertIsNotNone(target["truth"])

    def test_match_known_target_tolerance_boundary(self):
        self.assertIsNotNone(epoch_source.match_known_target(BARNARD_RA, BARNARD_DEC + 0.049))
        self.assertIsNone(epoch_source.match_known_target(BARNARD_RA, BARNARD_DEC + 0.06))

    def test_match_known_target_covers_wise_ambiguity(self):
        # 赛道坐标与 SIMBAD 差 1.6′；0.05° 阈值必须覆盖
        target = epoch_source.match_known_target(133.795129 + 0.027, -7.245147)
        self.assertIsNotNone(target)
        self.assertEqual(target["id"], "wise0855")
        self.assertIsNone(epoch_source.match_known_target(133.795129 + 0.08, -7.245147))

    def test_invalid_query_raises_value_error(self):
        for args in (
            (BARNARD_RA, BARNARD_DEC, 0.0),
            (BARNARD_RA, BARNARD_DEC, 0.001),
            (BARNARD_RA, BARNARD_DEC, 4.0),
            (BARNARD_RA, 100.0, 0.25),
            (float("nan"), 0.0, 0.25),
            (float("inf"), 0.0, 0.25),
            ("not-a-number", 0.0, 0.25),
        ):
            with self.assertRaises(ValueError, msg=str(args)):
                epoch_source.resolve_epochs(*args)

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
        eps = rw_aur_epochs()
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs", return_value=self._resolution(eps, matched="rw_aur")), mock.patch.object(
            data_layer, "fetch_epoch", return_value=fake_epoch(61294.601216)
        ):
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="auto", size_deg=0.25, ra=RW_AUR_RA, dec=RW_AUR_DEC
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
        with mock.patch.object(data_layer.epoch_source, "resolve_epochs") as resolve_mock:
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="synthetic", size_deg=0.25, ra=10.0, dec=20.0
            )
        resolve_mock.assert_not_called()
        self.assertEqual(meta["mode"], "synthetic")
        self.assertIsNone(meta["fallback_reason"])
        self.assertEqual(len(epochs), 8)

    def test_custom_missing_coordinates_raises(self):
        with self.assertRaises(ValueError):
            data_layer.build_dataset("custom", mode="auto", size_deg=0.25)


class BuildDatasetSeamTest(unittest.TestCase):
    """真实 seam：FakeDB v1 → resolve_epochs → build_dataset(custom) → fetch_epoch(mock)。"""

    def test_custom_v1_flow_end_to_end(self):
        def fake_fetch(ep, ra, dec, size):
            return fake_epoch(ep["mjd"])

        with FakeDB(v1_route(barnard_epochs(), coverage={"n_epochs": 4, "coverage_level": "C2"}, matched="barnard")) as db, mock.patch.dict(
            os.environ, {epoch_source.DB_URL_ENV: db.url}
        ), mock.patch.object(data_layer, "fetch_epoch", side_effect=fake_fetch):
            target, epochs, truth, meta = data_layer.build_dataset(
                "custom", mode="auto", size_deg=0.25, ra=BARNARD_RA, dec=BARNARD_DEC
            )
        self.assertEqual(meta["mode"], "real")
        self.assertEqual(meta["source"], "db")
        self.assertEqual(meta["real_epochs"], 4)
        self.assertEqual(meta["matched_target_id"], "barnard")
        self.assertEqual(len(epochs), 4)
        self.assertIsNone(meta["fallback_reason"])
        self.assertEqual(target["ra"], BARNARD_RA)
        self.assertEqual(target["truth"]["pmra_mas_yr"], -801.551)


if __name__ == "__main__":
    unittest.main()
