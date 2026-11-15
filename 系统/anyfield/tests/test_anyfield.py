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

from anyfield_core import (  # noqa: E402
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
    build_fits_fixture,
    build_fixture,
    build_npz_fixture,
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
        self.assertEqual(body["ingest"], "metadata_local_pixels_or_s3_cutout")
        self.assertTrue(body["pixel_ingest"])
        self.assertEqual(body["discovery"], "week_index+s3_header")
        self.assertEqual(body["fetch"], "allowlisted_irsa_cutout")
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

    def test_ingest_root_env_is_configurable(self) -> None:
        import os

        with mock.patch.dict(os.environ, {"ANYFIELD_INGEST_ROOT": str(self.tmp.name)}):
            store = Store(self.db, sidecar_path=self.sidecar, blob_root=self.blobs)
        self.assertEqual([p.resolve() for p in store.ingest_roots], [Path(self.tmp.name).resolve()])

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


class AnyFieldFileIngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="anyfield-file-")
        cls.root = Path(cls.tmp.name)
        cls.db = build_fixture(cls.root / "catalog.sqlite")
        cls.sidecar = cls.root / "anyfield.sqlite"
        cls.blobs = cls.root / "blobs"
        cls.blobs.mkdir(parents=True, exist_ok=True)
        cls.npz = build_npz_fixture(cls.root / "synthetic_sources.npz")
        cls.fits = build_fits_fixture(cls.root / "synthetic_sources.fits")
        cls.bad = cls.root / "bad.npz"
        cls.bad.write_bytes(b"not a real npz")
        cls.server = build_server(
            db_path=cls.db,
            host="127.0.0.1",
            port=0,
            sidecar_path=cls.sidecar,
            blob_root=cls.blobs,
            ingest_roots=[
                cls.root,
                SERVICE.parent / "app" / "data",
                SERVICE.parent / "参考 资料" / "muestras",
            ],
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

    def _wait(self, job_id: str, timeout: float = 5.0) -> dict:
        import time

        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            status, last = self._get(f"/api/v1/ingest/{job_id}")
            self.assertEqual(status, 200)
            if last.get("state") in ("committed", "failed", "retracted"):
                return last
            time.sleep(0.02)
        self.fail(f"pixel ingest job did not finish: {last}")

    def _ingest(self, path: Path) -> dict:
        status, created = self._post("/api/v1/ingest", {"input": {"path": str(path)}})
        self.assertEqual(status, 202, created)
        return self._wait(created["job_id"])

    def test_npz_ingest_sources_and_field(self) -> None:
        job = self._ingest(self.npz)
        self.assertEqual(job["state"], "committed")
        self.assertEqual(job["input_kind"], "npz")
        self.assertGreaterEqual(job["measurements_added"], 2)

        status, sources = self._get(
            "/api/v1/sources?ra=120&dec=20&fov=0.1&limit=100"
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(sources["count"], 2)
        self.assertEqual(sources["source_origin"], "ingested")
        for src in sources["sources"]:
            self.assertEqual(src["source_origin"], "ingested")
            self.assertEqual(src["provenance"], "real")
            self.assertIn("source_index", src)
            for field in ("x", "y", "ra", "dec", "flux", "flux_err", "snr", "saturated", "quality"):
                self.assertIn(field, src)

        status, field = self._get("/api/v1/field?ra=120&dec=20&fov=0.1")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(field["source_count"], 2)
        self.assertEqual(field["source_origin"], "ingested")
        self.assertEqual(len(field["sources"]), field["source_count"])
        self.assertTrue(field["sources_url"].startswith("/api/v1/sources?"))
        self.assertTrue(any(e.get("source_count", 0) >= 2 for e in field["epochs"]))
        self.assertNotIn("synthetic", json.dumps(field).lower())

    def test_fits_ingest_sources_and_field(self) -> None:
        job = self._ingest(self.fits)
        self.assertEqual(job["state"], "committed")
        self.assertEqual(job["input_kind"], "fits")
        self.assertGreaterEqual(job["measurements_added"], 2)

        status, sources = self._get("/api/v1/sources?ra=121&dec=21&fov=0.1")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(sources["count"], 2)
        self.assertTrue(any(s["ingest_kind"] == "fits" for s in sources["sources"]))

        status, field = self._get("/api/v1/field?ra=121&dec=21&fov=0.1")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(field["source_count"], 2)

    def test_file_ingest_is_idempotent(self) -> None:
        first = self._ingest(self.npz)
        self.assertEqual(first["state"], "committed")
        status, second = self._post("/api/v1/ingest", {"input": {"path": str(self.npz)}})
        self.assertEqual(status, 202)
        self.assertTrue(second["replayed"])
        second_job = self._wait(second["job_id"])
        self.assertEqual(second_job["state"], "committed")
        self.assertEqual(second_job["measurements_added"], 0)
        status, sources = self._get("/api/v1/sources?ra=120&dec=20&fov=0.1")
        self.assertEqual(status, 200)
        count_after = sources["count"]
        self.assertGreaterEqual(count_after, 2)

    def test_url_path_outside_and_bad_file_are_400(self) -> None:
        status, body = self._post("/api/v1/ingest", {"input": {"path": "https://example.com/x.npz"}})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "NETWORK_SOURCE_NOT_ALLOWED")

        outside = Path(self.tmp.name).parent / "outside.npz"
        outside.write_bytes(b"x")
        try:
            status, body = self._post("/api/v1/ingest", {"input": {"path": str(outside)}})
            self.assertEqual(status, 400)
            self.assertEqual(body["error"]["code"], "PATH_NOT_ALLOWED")
        finally:
            outside.unlink(missing_ok=True)

        status, body = self._post("/api/v1/ingest", {"input": {"path": str(self.bad)}})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_INGEST_FILE")

        status, body = self._post("/api/v1/ingest", {"input": {"path": str(self.root / "missing.npz")}})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INGEST_FILE_NOT_FOUND")

    def test_pixel_file_ingest_does_not_use_network(self) -> None:
        import socket

        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                normalized = self.server.store.validate_file_manifest(
                    {"input": {"path": str(self.npz)}}
                )
        self.assertEqual(normalized["file"]["kind"], "npz")
        self.assertEqual(normalized["epoch"]["obsid"], "2026W50_1A_0001_1")

    def test_real_barnard_app_npz_sources(self) -> None:
        cache = SERVICE.parent / "app" / "data" / "epochcache"
        paths = [
            cache / "dr1_2025W35_2A_0221_1_D2_269.4462_4.7673_0.200.npz",
            cache / "dr1_2025W35_2A_0233_1_D1_269.4462_4.7673_0.200.npz",
            cache / "dr1_2025W36_2B_0123_1_D1_269.4462_4.7673_0.200.npz",
            cache / "dr1_2026W12_2A_0324_1_D3_269.4464_4.7688_0.200.npz",
        ]
        if not all(p.is_file() for p in paths):
            self.skipTest("real app NPZ fixtures not available")
        for path in paths:
            job = self._ingest(path)
            self.assertEqual(job["state"], "committed")
            self.assertGreaterEqual(job["measurements_added"], 1)
        status, sources = self._get(
            "/api/v1/sources?ra=269.4462&dec=4.7673&fov=0.2&limit=500"
        )
        self.assertEqual(status, 200)
        self.assertGreater(sources["count"], 0)
        self.assertTrue(any(s["snr"] > 5 for s in sources["sources"]))
        self.assertTrue(all(s["ra"] is not None and s["dec"] is not None for s in sources["sources"]))
        status, field = self._get("/api/v1/field?ra=269.4462&dec=4.7673&fov=0.2")
        self.assertEqual(status, 200)
        self.assertGreater(field["source_count"], 0)
        self.assertTrue(field["sources"])


# --------------------------------------------------------------------------- discovery / fetch
from fake_s3 import FakeS3, build_week_index  # noqa: E402


class AnyFieldDiscoveryFetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="anyfield-p1b-")
        cls.root = Path(cls.tmp.name)
        cls.db = build_fixture(cls.root / "catalog.sqlite")
        cls.sidecar = cls.root / "anyfield.sqlite"
        cls.fake = FakeS3(tmp=cls.root, ra=120.0, dec=20.0)
        cls.week_index = build_week_index(cls.root / "week_index.json", ra=120.0, dec=20.0)
        cls.server = build_server(
            db_path=cls.db,
            host="127.0.0.1",
            port=0,
            sidecar_path=cls.sidecar,
            blob_root=cls.root / "blobs",
            s3_base=cls.fake.base,
            irsa_base=cls.fake.base + "/ibe/data/spherex/",
            week_index_path=cls.week_index,
            discovery_cache_dir=cls.root / "discovery_headers",
            fetch_allow_hosts=["127.0.0.1"],
            fetch_timeout=3,
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
        cls.fake.close()
        cls.tmp.cleanup()

    def setUp(self) -> None:
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

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urlopen(Request(self.base + path), timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _post(self, path: str, payload: dict | str) -> tuple[int, dict]:
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        request = Request(self.base + path, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _wait(self, job_id: str, timeout: float = 5.0) -> dict:
        import time

        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            status, last = self._get(f"/api/v1/ingest/{job_id}")
            self.assertEqual(status, 200)
            if last.get("state") in ("committed", "failed", "retracted"):
                return last
            time.sleep(0.02)
        self.fail(f"fetch job did not finish: {last}")

    def test_discover_returns_candidates(self) -> None:
        status, body = self._get("/api/v1/discover?ra=120&dec=20&fov=0.2&max_weeks=1")
        self.assertEqual(status, 200)
        self.assertEqual(body["discovery"], "week_index+s3_header")
        self.assertEqual(body["count"], 6)
        required = {"obsid", "detector", "release", "version", "mjd", "date_obs", "key", "slice_url", "sep_deg"}
        for cand in body["candidates"]:
            self.assertTrue(required.issubset(cand.keys()))
            self.assertTrue(cand["slice_url"].startswith("http://127.0.0.1:"))
            self.assertIn("/dr1/level2/", cand["slice_url"])
            self.assertIn("/level2_2026W50_1A_0001_1D", cand["key"])

    def test_discover_empty_is_200_not_422(self) -> None:
        status, body = self._get("/api/v1/discover?ra=0&dec=0&fov=0.2&max_weeks=1")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["reason"], "NO_WEEK_INDEX_MATCH")
        self.assertNotIn("error", body)
        self.assertTrue(any("EMPTY_DISCOVERY" in w for w in body["warnings"]))

    def test_discover_bad_max_weeks_is_400(self) -> None:
        status, body = self._get("/api/v1/discover?ra=120&dec=20&fov=0.2&max_weeks=0")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_QUERY")

    def test_fetch_candidate_to_sources(self) -> None:
        _, disc = self._get("/api/v1/discover?ra=120&dec=20&fov=0.2&max_weeks=1")
        cand = disc["candidates"][0]
        status, created = self._post(
            "/api/v1/fetch",
            {
                "input": {"s3_key": cand["key"], "obsid": cand["obsid"], "detector": cand["detector"]},
                "ra": 120.0,
                "dec": 20.0,
                "fov": 0.2,
            },
        )
        self.assertEqual(status, 202)
        job = self._wait(created["job_id"])
        self.assertEqual(job["state"], "committed")
        self.assertEqual(job["input_kind"], "s3_cutout")
        self.assertGreater(job["measurements_added"], 0)

        status, sources = self._get("/api/v1/sources?ra=120&dec=20&fov=0.2&limit=100")
        self.assertEqual(status, 200)
        self.assertGreater(sources["count"], 0)
        self.assertEqual(sources["source_origin"], "on_demand_s3")
        first = sources["sources"][0]
        self.assertEqual(first["source_origin"], "on_demand_s3")
        self.assertEqual(first["provenance"], "real")
        self.assertIn("ra", first)
        self.assertIn("dec", first)
        self.assertIn("snr", first)

        status, field = self._get("/api/v1/field?ra=120&dec=20&fov=0.2")
        self.assertEqual(status, 200)
        self.assertGreater(field["source_count"], 0)
        self.assertEqual(field["source_origin"], "on_demand_s3")

    def test_fetch_idempotent(self) -> None:
        _, disc = self._get("/api/v1/discover?ra=120&dec=20&fov=0.2&max_weeks=1")
        cand = disc["candidates"][0]
        payload = {
            "input": {"s3_key": cand["key"], "obsid": cand["obsid"], "detector": cand["detector"]},
            "ra": 120.0, "dec": 20.0, "fov": 0.2,
        }
        status, first = self._post("/api/v1/fetch", payload)
        self.assertEqual(status, 202)
        self.assertEqual(self._wait(first["job_id"])["state"], "committed")
        status, second = self._post("/api/v1/fetch", payload)
        self.assertEqual(status, 202)
        self.assertTrue(second["replayed"])
        second_job = self._wait(second["job_id"])
        self.assertEqual(second_job["state"], "committed")
        self.assertEqual(second_job["measurements_added"], 0)
        _, sources = self._get("/api/v1/sources?ra=120&dec=20&fov=0.2")
        self.assertGreater(sources["count"], 0)

    def test_fetch_url_and_bad_key_are_400(self) -> None:
        status, body = self._post(
            "/api/v1/fetch",
            {"input": {"s3_key": "https://evil.example/x.fits", "obsid": "x", "detector": 1}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "NETWORK_SOURCE_NOT_ALLOWED")

        status, body = self._post(
            "/api/v1/fetch",
            {"input": {"s3_key": "not-a-key", "obsid": "x", "detector": 1}, "ra": 120, "dec": 20, "fov": 0.2},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_S3_KEY")

    def test_fetch_host_not_allowlisted_is_400(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="anyfield-fetch-acl-")
        try:
            root = Path(tmp.name)
            db = build_fixture(root / "catalog.sqlite")
            fake = FakeS3(tmp=root, ra=130.0, dec=30.0)
            week = build_week_index(root / "week_index.json", ra=130.0, dec=30.0)
            server = build_server(
                db_path=db, host="127.0.0.1", port=0,
                sidecar_path=root / "anyfield.sqlite", blob_root=root / "blobs",
                s3_base=fake.base, irsa_base=fake.base + "/ibe/data/spherex/",
                week_index_path=week, discovery_cache_dir=root / "hdr",
                fetch_allow_hosts=["example.com"], fetch_timeout=3,
            )
            server.quiet = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                base = f"http://{host}:{port}"
                body = json.dumps({
                    "input": {
                        "s3_key": fake.keys[1],
                        "obsid": fake.obsid,
                        "detector": 1,
                    },
                    "ra": 130.0, "dec": 30.0, "fov": 0.2,
                }).encode()
                request = Request(base + "/api/v1/fetch", data=body, headers={"Content-Type": "application/json"})
                try:
                    with urlopen(request, timeout=5) as response:
                        code = response.status
                        payload = json.loads(response.read())
                except HTTPError as exc:
                    code = exc.code
                    payload = json.loads(exc.read())
                self.assertEqual(code, 400)
                self.assertEqual(payload["error"]["code"], "FETCH_HOST_NOT_ALLOWED")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                fake.close()
        finally:
            tmp.cleanup()

    def test_fetch_size_limit_fails_job(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="anyfield-fetch-size-")
        try:
            root = Path(tmp.name)
            db = build_fixture(root / "catalog.sqlite")
            fake = FakeS3(tmp=root, ra=131.0, dec=31.0, cutout_data=b"x" * 4096)
            week = build_week_index(root / "week_index.json", ra=131.0, dec=31.0)
            server = build_server(
                db_path=db, host="127.0.0.1", port=0,
                sidecar_path=root / "anyfield.sqlite", blob_root=root / "blobs",
                s3_base=fake.base, irsa_base=fake.base + "/ibe/data/spherex/",
                week_index_path=week, discovery_cache_dir=root / "hdr",
                fetch_allow_hosts=["127.0.0.1"], fetch_timeout=3,
                fetch_max_bytes=1024,
            )
            server.quiet = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                base = f"http://{host}:{port}"
                body = json.dumps({
                    "input": {"s3_key": fake.keys[1], "obsid": fake.obsid, "detector": 1},
                    "ra": 131.0, "dec": 31.0, "fov": 0.2,
                }).encode()
                request = Request(base + "/api/v1/fetch", data=body, headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    created = json.loads(response.read())
                job_id = created["job_id"]
                import time

                status_payload = None
                for _ in range(200):
                    with urlopen(base + f"/api/v1/ingest/{job_id}", timeout=5) as response:
                        status_payload = json.loads(response.read())
                    if status_payload.get("state") in ("committed", "failed", "retracted"):
                        break
                    time.sleep(0.02)
                self.assertEqual(status_payload.get("state"), "failed")
                self.assertEqual(status_payload.get("error", {}).get("code"), "FETCH_TOO_LARGE")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                fake.close()
        finally:
            tmp.cleanup()


class AnyFieldDiscoveryUnavailableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="anyfield-disc-down-")
        cls.root = Path(cls.tmp.name)
        cls.db = build_fixture(cls.root / "catalog.sqlite")
        cls.week = build_week_index(cls.root / "week_index.json", ra=120.0, dec=20.0)
        cls.server = build_server(
            db_path=cls.db, host="127.0.0.1", port=0,
            sidecar_path=cls.root / "anyfield.sqlite", blob_root=cls.root / "blobs",
            s3_base="http://127.0.0.1:1", irsa_base="http://127.0.0.1:1/ibe/data/spherex/",
            week_index_path=cls.week, discovery_cache_dir=cls.root / "hdr",
            fetch_allow_hosts=["127.0.0.1"], fetch_timeout=1,
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

    def test_discover_s3_unavailable_is_503(self) -> None:
        try:
            with urlopen(self.base + "/api/v1/discover?ra=120&dec=20&fov=0.2&max_weeks=1", timeout=5) as response:
                code = response.status
                body = json.loads(response.read())
        except HTTPError as exc:
            code = exc.code
            body = json.loads(exc.read())
        self.assertEqual(code, 503)
        self.assertEqual(body["error"]["code"], "UPSTREAM_UNAVAILABLE")
