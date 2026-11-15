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
        cls.sidecar = Path(cls.tmp.name) / "anyfield.sqlite"
        cls.blobs = Path(cls.tmp.name) / "blobs"
        cls.blobs.mkdir(parents=True, exist_ok=True)
        cls.server = build_server(
            db_path=cls.db,
            host="127.0.0.1",
            port=0,
            sidecar_path=cls.sidecar,
            blob_root=cls.blobs,
        )
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

    def _post(self, path: str, payload: dict | str) -> tuple[int, dict]:
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        request = Request(self.base + path, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=3) as response:
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
        self.assertEqual(body["ingest"], "metadata_only")
        self.assertFalse(body["pixel_ingest"])
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
        self.assertEqual(body["epochs"][0]["coverage_method"], "center_approx")
        self.assertEqual(body["epochs"][0]["footprint_method"], "center_approx")
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
        self.sidecar = Path(self.tmp.name) / "anyfield.sqlite"
        self.blobs = Path(self.tmp.name) / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolver_never_opens_a_network_connection(self) -> None:
        store = Store(self.db, sidecar_path=self.sidecar, blob_root=self.blobs)
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                body = store.resolve_field(BARNARD_RA, BARNARD_DEC, 0.1)
        self.assertEqual(body["coverage"]["state"], "covered")
        self.assertEqual(body["count"], 2)

    def test_store_does_not_require_running_db_service(self) -> None:
        # No HTTP client is constructed for the resolver; this is a direct
        # read-only SQLite call, so the permanent 8797 service is unnecessary.
        store = Store(self.db, sidecar_path=self.sidecar, blob_root=self.blobs)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("8797/network used")):
            self.assertTrue(store.health()["db"]["readable"])
            body = store.resolve_epochs(BARNARD_RA, BARNARD_DEC, 0.1)
        self.assertEqual(body["count"], 2)

    def test_unknown_still_503_when_called_offline(self) -> None:
        store = Store(self.db, sidecar_path=self.sidecar, blob_root=self.blobs)
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
        cls.sidecar = Path(cls.tmp.name) / "anyfield.sqlite"
        cls.blobs = Path(cls.tmp.name) / "blobs"
        cls.blobs.mkdir(parents=True, exist_ok=True)
        cls.server = build_server(
            db_path=cls.db,
            host="127.0.0.1",
            port=0,
            sidecar_path=cls.sidecar,
            blob_root=cls.blobs,
        )
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


# --------------------------------------------------------------------------- P0
from wcs_footprint import FOOTPRINT_METHOD_WCS, point_in_polygon  # noqa: E402


PROD_DB = SERVICE.parent / "数据库工具" / "var" / "catalog.sqlite"
PROD_BLOBS = SERVICE.parent / "数据库工具" / "var" / "blobs"


class RealHeaderFootprintTest(unittest.TestCase):
    """Integration test on the real local DB; skipped if fixtures are absent."""

    def setUp(self) -> None:
        if not PROD_DB.is_file() or not PROD_BLOBS.is_dir():
            self.skipTest("production catalog.sqlite/blobs not available")
        self.tmp = tempfile.TemporaryDirectory(prefix="anyfield-prod-")
        self.sidecar = Path(self.tmp.name) / "anyfield.sqlite"
        self.store = Store(
            PROD_DB,
            sidecar_path=self.sidecar,
            blob_root=PROD_BLOBS,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_74_linked_headers_build_real_footprints(self) -> None:
        stats = self.store.footprint_stats
        self.assertEqual(stats["headers"], 74)
        self.assertEqual(stats["built"], 74)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(self.store.sidecar.counts()["footprints"], 74)
        self.assertEqual(self.store.sidecar.counts()["complete_regions"], 0)
        import sqlite3

        with sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True) as conn:
            frames = int(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0])
        self.assertEqual(frames, 5893)
        self.assertEqual(frames - 74, 5819)

    def test_barnard_uses_wcs_header_footprints(self) -> None:
        body = self.store.resolve_field(BARNARD_RA, BARNARD_DEC, 0.1)
        self.assertEqual(body["coverage"]["footprint_method"], FOOTPRINT_METHOD_WCS)
        self.assertEqual(body["coverage"]["coverage_method"], FOOTPRINT_METHOD_WCS)
        self.assertGreaterEqual(body["coverage"]["n_wcs_header"], 1)
        self.assertEqual(body["coverage"]["n_center_approx"], 0)
        self.assertTrue(body["epochs"])
        for epoch in body["epochs"]:
            self.assertEqual(epoch["coverage_method"], FOOTPRINT_METHOD_WCS)
            self.assertEqual(epoch["footprint_method"], FOOTPRINT_METHOD_WCS)
        self.assertEqual(body["footprint_method"], FOOTPRINT_METHOD_WCS)

    def test_production_unknown_is_503_not_422(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.store.resolve_field(0.0, 0.0, 0.1)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, "INDEX_INCOMPLETE")
        self.assertFalse(ctx.exception.details["strong_assertion"])

    def test_point_in_polygon(self) -> None:
        square = [[9.0, 9.0], [11.0, 9.0], [11.0, 11.0], [9.0, 11.0]]
        self.assertTrue(point_in_polygon(10.0, 10.0, square))
        self.assertTrue(point_in_polygon(9.5, 10.0, square))
        self.assertFalse(point_in_polygon(12.0, 10.0, square))
        self.assertFalse(point_in_polygon(10.0, 8.0, square))
        wrap = [[359.0, 0.0], [1.0, 0.0], [1.0, 2.0], [359.0, 2.0]]
        self.assertTrue(point_in_polygon(0.0, 1.0, wrap))


class AnyFieldCompleteRegionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="anyfield-complete-")
        cls.db = build_fixture(Path(cls.tmp.name) / "catalog.sqlite")
        cls.sidecar = Path(cls.tmp.name) / "anyfield.sqlite"
        cls.blobs = Path(cls.tmp.name) / "blobs"
        cls.blobs.mkdir(parents=True, exist_ok=True)
        cls.server = build_server(
            db_path=cls.db,
            host="127.0.0.1",
            port=0,
            sidecar_path=cls.sidecar,
            blob_root=cls.blobs,
        )
        cls.server.quiet = True
        cls.server.store.sidecar.insert_complete_region(
            "blank-0-0", 0.0, 0.0, 0.5, note="test-only complete blank region"
        )
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

    def test_complete_blank_is_422(self) -> None:
        status, body = self._get("/api/v1/field?ra=0&dec=0&fov=0.1")
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "NO_LOCAL_COVERAGE")
        self.assertEqual(body["coverage"]["state"], "none")
        self.assertEqual(body["coverage"]["index_completeness"], "complete")
        self.assertTrue(body["error"]["details"]["strong_assertion"])
        self.assertTrue(body["error"]["details"]["no_local_coverage"])
        self.assertTrue(body["error"]["details"]["sim_allowed"])

    def test_outside_complete_region_is_503(self) -> None:
        status, body = self._get("/api/v1/field?ra=50&dec=50&fov=0.1")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "INDEX_INCOMPLETE")
        self.assertFalse(body["error"]["details"]["strong_assertion"])

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urlopen(Request(self.base + path), timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class AnyFieldIngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="anyfield-ingest-")
        cls.db = build_fixture(Path(cls.tmp.name) / "catalog.sqlite")
        cls.sidecar = Path(cls.tmp.name) / "anyfield.sqlite"
        cls.blobs = Path(cls.tmp.name) / "blobs"
        cls.blobs.mkdir(parents=True, exist_ok=True)
        cls.server = build_server(
            db_path=cls.db,
            host="127.0.0.1",
            port=0,
            sidecar_path=cls.sidecar,
            blob_root=cls.blobs,
        )
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

    def setUp(self) -> None:
        # Each ingest test starts from an empty sidecar; the catalog fixture is
        # immutable and shared.
        import sqlite3

        conn = sqlite3.connect(str(self.sidecar))
        try:
            conn.execute("DELETE FROM measurement")
            conn.execute("DELETE FROM epoch_extra")
            conn.execute("DELETE FROM ingest_job")
            conn.execute("DELETE FROM coverage_region")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _manifest(ra: float = 120.0, dec: float = 20.0, **overrides) -> dict:
        manifest = {
            "source": {"kind": "local_manifest", "name": "unit-test"},
            "epoch": {
                "obsid": "2026W50_1A_0001_1",
                "detector": 1,
                "release": "dr1",
                "version": "l2b-v27-2026-999",
                "week": "2026W50_1A",
                "mjd": 61340.0,
                "ra": ra,
                "dec": dec,
                "frame_id": None,
                "footprint": {
                    "method": "wcs_header",
                    "corners": [[ra - 1, dec - 1], [ra + 1, dec - 1], [ra + 1, dec + 1], [ra - 1, dec + 1]],
                },
            },
            "measurements": [
                {
                    "target_key": "unit-test-target",
                    "ra": ra,
                    "dec": dec,
                    "flux": 1.2,
                    "flux_err": 0.1,
                    "snr": 12.0,
                    "quality": ["TEST"],
                }
            ],
        }
        epoch_over = overrides.pop("epoch", {})
        manifest["epoch"].update(epoch_over)
        manifest.update(overrides)
        return manifest

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urlopen(Request(self.base + path), timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _post(self, path: str, payload: dict | str) -> tuple[int, dict]:
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        request = Request(self.base + path, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _wait(self, job_id: str, timeout: float = 3.0) -> dict:
        import time

        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            status, last = self._get(f"/api/v1/ingest/{job_id}")
            self.assertEqual(status, 200)
            if last.get("state") in ("committed", "failed", "retracted"):
                return last
            time.sleep(0.01)
        self.fail(f"job did not finish: {last}")

    def test_ingest_success_and_field_visibility(self) -> None:
        status, created = self._post("/api/v1/ingest", self._manifest())
        self.assertEqual(status, 202)
        self.assertEqual(created["state"], "queued")
        job = self._wait(created["job_id"])
        self.assertEqual(job["state"], "committed")
        self.assertEqual(job["measurements_added"], 1)

        status, field = self._get("/api/v1/field?ra=120&dec=20&fov=0.1")
        self.assertEqual(status, 200)
        self.assertEqual(field["source"], "ingest")
        self.assertEqual(field["coverage"]["coverage_method"], FOOTPRINT_METHOD_WCS)
        self.assertEqual(field["coverage"]["n_wcs_header"], 1)
        self.assertTrue(any(e["coverage_method"] == FOOTPRINT_METHOD_WCS for e in field["epochs"]))

    def test_ingest_is_idempotent_by_epoch_id(self) -> None:
        manifest = self._manifest(ra=121.0, dec=21.0, epoch={"obsid": "2026W50_1A_0002_1"})
        status, first = self._post("/api/v1/ingest", manifest)
        self.assertEqual(status, 202)
        first_job = self._wait(first["job_id"])
        self.assertEqual(first_job["state"], "committed")

        status, second = self._post("/api/v1/ingest", manifest)
        self.assertEqual(status, 202)
        self.assertTrue(second["replayed"])
        second_job = self._wait(second["job_id"])
        self.assertEqual(second_job["state"], "committed")
        self.assertEqual(second_job["measurements_added"], 0)
        self.assertEqual(self.server.store.sidecar.counts()["epoch_extra"], 1)

    def test_ingest_rollback_is_soft(self) -> None:
        manifest = self._manifest(ra=122.0, dec=22.0, epoch={"obsid": "2026W50_1A_0003_1"})
        status, created = self._post("/api/v1/ingest", manifest)
        self.assertEqual(status, 202)
        job = self._wait(created["job_id"])
        self.assertEqual(job["state"], "committed")
        status, rolled = self._post(f"/api/v1/ingest/{created['job_id']}/rollback", {})
        self.assertEqual(status, 200)
        self.assertEqual(rolled["state"], "retracted")
        status, field = self._get("/api/v1/field?ra=122&dec=22&fov=0.1")
        self.assertEqual(status, 503)
        self.assertEqual(field["error"]["code"], "INDEX_INCOMPLETE")

    def test_ingest_bad_input(self) -> None:
        status, body = self._post("/api/v1/ingest", {})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_MANIFEST")

        bad_url = self._manifest()
        bad_url["source"] = {"kind": "local_manifest", "url": "https://example.com/x.fits"}
        status, body = self._post("/api/v1/ingest", bad_url)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "NETWORK_SOURCE_NOT_ALLOWED")

        status, body = self._post("/api/v1/ingest", "{not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "BAD_JSON")

    def test_unknown_job_is_404(self) -> None:
        status, body = self._get("/api/v1/ingest/ing_does_not_exist")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "JOB_NOT_FOUND")
