"""C-MVP AnyField tests: read-only, offline, temp SQLite only."""
from __future__ import annotations

import hashlib
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parent
for path in (str(SERVICE), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import (  # noqa: E402
    COVERAGE_MODE,
    SCHEMA_VERSION,
    ApiError,
    Store,
    angular_sep_deg,
)
from server import build_server  # noqa: E402
from fixtures import (  # noqa: E402
    BARNARD_DEC,
    BARNARD_RA,
    LOCAL_FRAME_DEC,
    LOCAL_FRAME_RA,
    UNKNOWN_DEC,
    UNKNOWN_RA,
    build_fixture,
)


class AnyFieldHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="anyfield-test-")
        cls.db = build_fixture(Path(cls.tmp.name) / "catalog.sqlite")
        cls.server = build_server(db_path=cls.db, host="127.0.0.1", port=0)
        cls.server.quiet = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urlopen(Request(self.base + path), timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # ------------------------------------------------------------------ health
    def test_health_ok(self) -> None:
        status, body = self._get("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["schema_version"], "1.0")
        self.assertEqual(body["coverage_mode"], "center_approx")
        self.assertEqual(body["network"], "none")
        self.assertEqual(body["writes"], "none")
        self.assertTrue(body["db"]["read_only"])
        self.assertTrue(body["index_snapshot"].startswith("local-"))
        self.assertIn("request_id", body)

    def test_capabilities_declares_no_network_or_ingest(self) -> None:
        status, body = self._get("/api/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertFalse(body["ingest"])
        self.assertFalse(body["sim"])
        self.assertEqual(body["network"], "none")

    # ------------------------------------------------------------------ field
    def test_barnard_field_returns_real_epochs(self) -> None:
        status, body = self._get(
            f"/api/v1/field?ra={BARNARD_RA}&dec={BARNARD_DEC}&fov=0.1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["schema_version"], "1.0")
        self.assertEqual(body["coverage_mode"], "center_approx")
        self.assertEqual(body["coverage"]["coverage_mode"], "center_approx")
        self.assertEqual(body["coverage"]["state"], "covered")
        self.assertEqual(body["coverage"]["coverage_level"], "C1")
        self.assertEqual(body["coverage"]["target_id"], "barnard")
        self.assertEqual(body["coverage"]["n_frames"], 2)
        self.assertEqual(body["coverage"]["n_epochs"], 2)
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["epochs"]), 2)
        self.assertTrue(body["index_snapshot"].startswith("local-"))
        self.assertEqual(body["query"]["fov"], 0.1)

        required = {
            "obsid", "detector", "release", "week", "version", "mjd",
            "date_obs", "sep_deg", "key", "slice_url", "provenance", "frame_id",
        }
        for epoch in body["epochs"]:
            self.assertTrue(required.issubset(epoch.keys()))
            self.assertEqual(epoch["provenance"], "real")
            self.assertTrue(epoch["key"].startswith("dr1/level2/"))
            self.assertIn("center=269.452077", epoch["slice_url"])
            self.assertIsInstance(epoch["detector"], int)
        self.assertNotIn("synthetic", json.dumps(body).lower())
        self.assertNotIn("simulated", json.dumps(body).lower())

    def test_barnard_coverage_is_not_a_footprint(self) -> None:
        _, body = self._get(
            f"/api/v1/field?ra={BARNARD_RA}&dec={BARNARD_DEC}&fov=0.1"
        )
        coverage = body["coverage"]
        self.assertEqual(coverage["method"], "center_radius_2.47")
        self.assertEqual(coverage["radius_deg"], 2.47)
        self.assertAlmostEqual(coverage["search_radius_deg"], 2.47)
        self.assertGreaterEqual(coverage["baseline_days"], 200.0)
        self.assertTrue(any("CENTER_APPROX_COVERAGE" in w for w in body["warnings"]))

    def test_known_local_frame_without_preset_target(self) -> None:
        status, body = self._get(
            f"/api/v1/field?ra={LOCAL_FRAME_RA}&dec={LOCAL_FRAME_DEC}&fov=0.1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "frames")
        self.assertIsNone(body["coverage"]["target_id"])
        self.assertEqual(body["coverage"]["n_frames"], 1)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["epochs"][0]["detector"], 1)
        self.assertTrue(any("NO_PRESET_TARGET_MATCH" in w for w in body["warnings"]))

    def test_unknown_field_is_503_index_incomplete_not_422(self) -> None:
        status, body = self._get(
            f"/api/v1/field?ra={UNKNOWN_RA}&dec={UNKNOWN_DEC}&fov=0.1"
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "INDEX_INCOMPLETE")
        self.assertTrue(body["error"]["retryable"])
        self.assertNotEqual(body["error"]["code"], "NO_LOCAL_COVERAGE")
        self.assertFalse(body["error"]["details"]["strong_assertion"])
        self.assertFalse(body["error"]["details"]["no_local_coverage"])
        self.assertEqual(body["coverage_mode"], "center_approx")
        self.assertEqual(body["coverage"]["state"], "unknown")
        self.assertEqual(body["coverage"]["coverage_mode"], "center_approx")
        self.assertEqual(body["error"]["details"]["coverage_mode"], "center_approx")
        self.assertTrue(body["index_snapshot"].startswith("local-"))

    def test_invalid_queries_are_400(self) -> None:
        cases = [
            "/api/v1/field?ra=1",
            "/api/v1/field?ra=1&dec=2&fov=0.001",
            "/api/v1/field?ra=1&dec=2&fov=3.5",
            "/api/v1/field?ra=1&dec=100&fov=0.1",
            "/api/v1/field?ra=nan&dec=2&fov=0.1",
        ]
        for path in cases:
            with self.subTest(path=path):
                status, body = self._get(path)
                self.assertEqual(status, 400)
                self.assertEqual(body["error"]["code"], "INVALID_QUERY")

    # ------------------------------------------------------------------ epochs
    def test_epochs_compatible_view(self) -> None:
        status, body = self._get(
            f"/api/v1/epochs?ra={BARNARD_RA}&dec={BARNARD_DEC}&fov=0.1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["schema_version"], "1.0")
        self.assertEqual(body["coverage"]["radius_deg"], 2.47)
        self.assertEqual(body["coverage"]["coverage_level"], "C1")
        self.assertIn("baseline_days", body["coverage"])
        self.assertNotIn("readiness", body["coverage"])
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["epochs"]), 2)
        self.assertIsNone(body["fallback_reason"])

    def test_epochs_unknown_is_503(self) -> None:
        status, body = self._get(
            f"/api/v1/epochs?ra={UNKNOWN_RA}&dec={UNKNOWN_DEC}&fov=0.1"
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "INDEX_INCOMPLETE")

    # ------------------------------------------------------------------ read-only
    def test_read_only_database_is_not_modified(self) -> None:
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self._get("/api/v1/health")
        self._get(f"/api/v1/field?ra={BARNARD_RA}&dec={BARNARD_DEC}&fov=0.1")
        self._get(f"/api/v1/epochs?ra={BARNARD_RA}&dec={BARNARD_DEC}&fov=0.1")
        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertFalse(Path(str(self.db) + "-wal").exists())

    def test_unknown_route_is_404(self) -> None:
        status, body = self._get("/api/v1/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")


class AnyFieldOfflineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="anyfield-offline-")
        self.db = build_fixture(Path(self.tmp.name) / "catalog.sqlite")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolver_never_opens_a_network_connection(self) -> None:
        store = Store(self.db)
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                body = store.resolve_field(BARNARD_RA, BARNARD_DEC, 0.1)
        self.assertEqual(body["coverage"]["state"], "covered")
        self.assertEqual(body["count"], 2)

    def test_store_does_not_require_running_db_service(self) -> None:
        # No HTTP client is constructed for the resolver; this is a direct
        # read-only SQLite call, so the permanent 8797 service is unnecessary.
        store = Store(self.db)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("8797/network used")):
            self.assertTrue(store.health()["db"]["readable"])
            body = store.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.1)
        self.assertEqual(body["count"], 2)

    def test_unknown_still_503_when_called_offline(self) -> None:
        store = Store(self.db)
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            with self.assertRaises(ApiError) as ctx:
                store.resolve_field(UNKNOWN_RA, UNKNOWN_DEC, 0.1)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, "INDEX_INCOMPLETE")


class AnyFieldMissingDBTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="anyfield-missing-")
        cls.db = Path(cls.tmp.name) / "does-not-exist.sqlite"
        cls.server = build_server(db_path=cls.db, host="127.0.0.1", port=0)
        cls.server.quiet = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urlopen(Request(self.base + path), timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_missing_db_health_is_503(self) -> None:
        status, body = self._get("/api/v1/health")
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["error"]["code"], "DB_UNAVAILABLE")
        self.assertFalse(body["db"]["readable"])
        self.assertEqual(body["index_snapshot"], "local-unavailable")

    def test_missing_db_field_is_503(self) -> None:
        status, body = self._get(
            f"/api/v1/field?ra={BARNARD_RA}&dec={BARNARD_DEC}&fov=0.1"
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "DB_UNAVAILABLE")
        self.assertNotEqual(body["error"]["code"], "NO_LOCAL_COVERAGE")
        self.assertFalse(self.db.exists())


class GeometryHelpersTest(unittest.TestCase):
    def test_angular_sep(self) -> None:
        self.assertAlmostEqual(angular_sep_deg(0, 0, 0, 0), 0.0)
        self.assertAlmostEqual(angular_sep_deg(0, 0, 90, 0), 90.0, places=6)
        self.assertTrue(angular_sep_deg(float("nan"), 0, 0, 0) != angular_sep_deg(0, 0, 0, 0))

    def test_schema_constant(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.0")
        self.assertEqual(COVERAGE_MODE, "center_approx")


if __name__ == "__main__":
    unittest.main()
