"""场级 ingest / upsert_field 的离线测试（fake TAP，不联网）。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from catalog_server import make_server
from db.gaia_tap import GaiaTAPError
from db.star_catalog import StarCatalog
from db.star_config import STAR_SCHEMA_VERSION
from tests.test_star_catalog import (
    build_fixture_db,
    http_json,
    running_server,
)


def _fake_rows(prefix: str, *, n: int = 2, ra: float = 10.0, dec: float = 20.0) -> list[dict]:
    return [
        {
            "catalog_id": f"{prefix}-{i}",
            "ra": ra + i * 0.001,
            "dec": dec + i * 0.001,
            "pmra": 10.0 + i,
            "pmdec": -5.0 - i,
            "parallax": 2.0,
            "gmag": 15.0 + i,
            "ruwe": 1.0,
            "astrometric_excess_noise": 0.1,
            "phot_variable_flag": "NOT_AVAILABLE",
            "provenance": "tap",
        }
        for i in range(n)
    ]


def _fake_fetch(prefix: str, *, n: int = 2):
    def fetch(*args, **kwargs):
        return _fake_rows(prefix, n=n), "source_id\n", "SELECT 1"

    return fetch


def _body(field_id: str, **overrides) -> dict:
    body = {
        "catalog": "gaia",
        "ra": 10.0,
        "dec": 20.0,
        "radius_deg": 0.1,
        "mag_limit": 20.0,
        "field_id": field_id,
    }
    body.update(overrides)
    return body


class IngestHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.db = Path(cls.td.name) / "star_catalog.sqlite"
        build_fixture_db(cls.db)
        cls._cm = running_server(cls.db)
        cls.port = cls._cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)
        cls.td.cleanup()

    def _counts(self):
        conn = sqlite3.connect(self.db)
        try:
            return (
                conn.execute("SELECT COUNT(*) FROM catalog_field").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM catalog_source").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM catalog_release").fetchone()[0],
            )
        finally:
            conn.close()

    def test_ingest_new_field_and_repeat_is_idempotent(self):
        field_id = "http-" + uuid.uuid4().hex[:8]
        before_fields, before_sources, _ = self._counts()
        with patch("catalog_server.fetch_gaia_cone", _fake_fetch(field_id, n=2)):
            code, data = http_json(
                self.port, "POST", "/api/v1/catalog/ingest", _body(field_id)
            )
        self.assertEqual(code, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["catalog"], "gaia")
        self.assertEqual(data["release"], "gaia-dr3")
        self.assertEqual(data["field_id"], field_id)
        self.assertEqual(data["counts"], {"n_sources": 2, "n_new": 2, "n_fields": before_fields + 1})
        self.assertEqual(data["provenance"], "tap")
        self.assertTrue(data["complete"])
        self.assertEqual(data["license"], "CC BY-NC 3.0 IGO")
        self.assertIn("Gaia Collaboration", data["citation"])

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            f = conn.execute(
                "SELECT * FROM catalog_field WHERE field_id=?", (field_id,)
            ).fetchone()
            self.assertEqual(f["n_sources"], 2)
            self.assertEqual(f["provenance"], "tap")
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM catalog_source WHERE field_id=?", (field_id,)
                ).fetchone()[0],
                2,
            )
            run = conn.execute(
                "SELECT * FROM catalog_import_run ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(run["status"], "ok")
            self.assertEqual(run["mode"], "tap")
        finally:
            conn.close()

        # 同场重入：替换该场，n_new=0，总源数不增长。
        after_fields, after_sources, _ = self._counts()
        self.assertEqual(after_sources, before_sources + 2)
        with patch("catalog_server.fetch_gaia_cone", _fake_fetch(field_id, n=2)):
            code, again = http_json(
                self.port, "POST", "/api/v1/catalog/ingest", _body(field_id)
            )
        self.assertEqual(code, 200, again)
        self.assertEqual(again["counts"]["n_sources"], 2)
        self.assertEqual(again["counts"]["n_new"], 0)
        self.assertEqual(again["counts"]["n_fields"], after_fields)
        self.assertEqual(self._counts()[1], after_sources)

    def test_ingest_adjacent_field_preserves_existing(self):
        a = "adj-a-" + uuid.uuid4().hex[:8]
        b = "adj-b-" + uuid.uuid4().hex[:8]
        with patch("catalog_server.fetch_gaia_cone", _fake_fetch(a, n=1)):
            code, _ = http_json(self.port, "POST", "/api/v1/catalog/ingest", _body(a))
        self.assertEqual(code, 200)
        with patch("catalog_server.fetch_gaia_cone", _fake_fetch(b, n=3)):
            code, data = http_json(self.port, "POST", "/api/v1/catalog/ingest", _body(b))
        self.assertEqual(code, 200, data)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM catalog_source WHERE field_id=?", (a,)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM catalog_source WHERE field_id=?", (b,)
                ).fetchone()[0],
                3,
            )
        finally:
            conn.close()

    def test_ingest_tap_failure_502_and_no_write(self):
        field_id = "tap-fail-" + uuid.uuid4().hex[:8]
        before = self._counts()
        with patch(
            "catalog_server.fetch_gaia_cone",
            side_effect=GaiaTAPError("simulated TAP truncation"),
        ):
            code, data = http_json(
                self.port, "POST", "/api/v1/catalog/ingest", _body(field_id)
            )
        self.assertEqual(code, 502)
        self.assertEqual(data["code"], "UPSTREAM_TAP_ERROR")
        self.assertEqual(data["error"]["code"], "UPSTREAM_TAP_ERROR")
        self.assertEqual(self._counts(), before)
        conn = sqlite3.connect(self.db)
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM catalog_field WHERE field_id=?", (field_id,)
                ).fetchone()
            )
        finally:
            conn.close()

    def test_ingest_invalid_params_400(self):
        good = _body("valid-" + uuid.uuid4().hex[:8])
        for bad in (
            {**good, "radius_deg": 0.01},
            {**good, "radius_deg": 1.01},
            {**good, "mag_limit": 11.9},
            {**good, "mag_limit": 20.1},
            {**good, "catalog": "2mass"},
            {**good, "dec": 91.0},
            {**good, "ra": "nan"},
            {**good, "field_id": "bad id with spaces"},
            {k: v for k, v in good.items() if k != "ra"},
            {**good, "include_photometry": "yes"},
        ):
            code, data = http_json(self.port, "POST", "/api/v1/catalog/ingest", bad)
            self.assertEqual(code, 400, (bad, data))
            self.assertEqual(data["code"], "BAD_REQUEST")

    def test_ingest_include_photometry_partial_does_not_fail_tap(self):
        field_id = "photo-" + uuid.uuid4().hex[:8]
        fake_phot = {
            "catalogs": {"2mass": [], "wise": []},
            "errors": {"2mass": "simulated Gator failure"},
        }
        with patch("catalog_server.fetch_gaia_cone", _fake_fetch(field_id, n=1)), patch(
            "catalog_server.fetch_field_photometry", return_value=fake_phot
        ):
            code, data = http_json(
                self.port,
                "POST",
                "/api/v1/catalog/ingest",
                _body(field_id, include_photometry=True),
            )
        self.assertEqual(code, 200, data)
        self.assertEqual(data["photometry"]["status"], "partial")
        self.assertTrue(data["warnings"])


class UpsertFieldTest(unittest.TestCase):
    def test_new_reentry_adjacent_and_additive(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "star.sqlite"
            build_fixture_db(db)
            with StarCatalog(db, readonly=False) as cat:
                release = cat.get_release("gaia")
                rid = release["release_id"]
                base_fields = len(cat.fields(rid))
                base_sources = release["n_sources_actual"]

                def upsert(fid, sources, replace=True):
                    return cat.upsert_field(
                        catalog="gaia",
                        version="dr3",
                        field_id=fid,
                        field_name=fid,
                        ra=10.0,
                        dec=20.0,
                        radius_deg=0.2,
                        mag_limit=20.0,
                        sources=sources,
                        provenance="tap",
                        license="CC BY-NC 3.0 IGO",
                        citation="Gaia test",
                        source_url="https://example.test/tap",
                        replace_field=replace,
                    )

                r = upsert("unit-a", _fake_rows("unit-a", n=2))
                self.assertEqual(r["n_sources"], 2)
                self.assertEqual(r["n_new"], 2)
                self.assertEqual(r["n_fields"], base_fields + 1)
                self.assertEqual(r["n_release_sources"], base_sources + 2)
                r = upsert("unit-b", _fake_rows("unit-b", n=1))
                self.assertEqual(r["n_fields"], base_fields + 2)
                # 重入 A：删除旧 A 中已不存在的源，不改 B。
                r = upsert("unit-a", _fake_rows("unit-a", n=3))
                self.assertEqual(r["n_sources"], 3)
                self.assertEqual(r["n_new"], 1)
                self.assertEqual(r["n_fields"], base_fields + 2)
                conn = cat.conn
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM catalog_source WHERE field_id='unit-b'"
                    ).fetchone()[0],
                    1,
                )
                # 追加模式：不删旧源。
                r = upsert("unit-a", _fake_rows("unit-a", n=4), replace=False)
                self.assertEqual(r["n_sources"], 4)
                self.assertEqual(r["n_new"], 1)

    def test_upsert_does_not_touch_catalog_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "star.sqlite"
            build_fixture_db(db)
            with StarCatalog(db, readonly=False) as cat:
                release = cat.get_release("gaia")
                row = cat.conn.execute(
                    "SELECT * FROM catalog_source WHERE field_id='barnard' LIMIT 1"
                ).fetchone()
                self.assertIsNotNone(row)
                cat.conn.execute(
                    """INSERT INTO catalog_match(
                           analysis_id, detection_id, release_id, source_id,
                           sep_arcsec, ambiguity, created_at)
                       VALUES('analysis:ingest', 'd1', ?, ?, 0.1, 0, 'now')""",
                    (release["release_id"], row["source_id"]),
                )
                before = cat.conn.execute("SELECT COUNT(*) FROM catalog_match").fetchone()[0]
                self.assertEqual(before, 1)
                cat.upsert_field(
                    catalog="gaia",
                    version="dr3",
                    field_id="barnard",
                    field_name="Barnard upsert",
                    ra=269.452077,
                    dec=4.693365,
                    radius_deg=0.2,
                    mag_limit=20.0,
                    sources=[
                        {
                            "catalog_id": row["catalog_id"],
                            "ra": row["ra"],
                            "dec": row["dec"],
                            "gmag": row["gmag"],
                        }
                    ],
                    provenance="tap",
                    license="CC BY-NC 3.0 IGO",
                    citation="x",
                    source_url="u",
                )
                after = cat.conn.execute("SELECT COUNT(*) FROM catalog_match").fetchone()[0]
                self.assertEqual(after, 1)
                new_row = cat.conn.execute(
                    "SELECT field_id FROM catalog_source WHERE source_id=?",
                    (row["source_id"],),
                ).fetchone()
                self.assertEqual(new_row["field_id"], "barnard")

    def test_upsert_new_release_on_empty_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "empty.sqlite"
            from db.star_schema import init_star_db

            init_star_db(db, replace=True)
            with StarCatalog(db, readonly=False) as cat:
                r = cat.upsert_field(
                    catalog="gaia",
                    version="dr3",
                    field_id="fresh-field",
                    field_name="Fresh",
                    ra=1.0,
                    dec=2.0,
                    radius_deg=0.1,
                    mag_limit=18.0,
                    sources=_fake_rows("fresh", n=2),
                    provenance="tap",
                    license="CC BY-NC 3.0 IGO",
                    citation="x",
                    source_url="u",
                )
                self.assertEqual(r["release"], "gaia-dr3")
                self.assertEqual(r["n_sources"], 2)
                self.assertEqual(r["n_fields"], 1)
                self.assertEqual(r["n_release_sources"], 2)
                self.assertEqual(cat.schema_version(), STAR_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
