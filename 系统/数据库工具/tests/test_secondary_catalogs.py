"""2MASS/WISE 派生目录与 SIMBAD 名称 enrichment 的离线测试。"""
from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db.gaia_tap import read_fixture_rows
from db.simbad import run as run_simbad
from db.star_catalog import CatalogUnavailable, StarCatalog
from db.star_config import DEFAULT_GAIA_FIXTURE, STAR_SCHEMA_VERSION
from tools import import_simbad
from tests.test_star_catalog import build_fixture_db, http_json, running_server

FIXTURE = DEFAULT_GAIA_FIXTURE
TARGET_ID = "4472832135230352256"  # fixture 中同时有 2MASS/WISE + 星等的源


class SecondaryCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.db = Path(cls.td.name) / "star_catalog.sqlite"
        build_fixture_db(cls.db)
        with StarCatalog(cls.db, readonly=False) as cat:
            cls.derived = cat.derive_secondary_catalogs()
        cls._cm = running_server(cls.db)
        cls.port = cls._cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)
        cls.td.cleanup()

    def _cat(self):
        return StarCatalog(self.db, readonly=True)

    def test_derived_counts_fields_and_required_magnitudes(self):
        rows = read_fixture_rows(FIXTURE)
        # 派生 release 以 catalog_id 唯一：重叠场里的同一 2MASS/WISE 源只保留一行。
        expected_tm = len({
            r["twomass_id"] for r in rows
            if r.get("twomass_id")
            and all(r.get(k) is not None for k in ("jmag", "hmag", "kmag"))
        })
        expected_wise = len({
            r["wise_id"] for r in rows
            if r.get("wise_id")
            and all(r.get(k) is not None for k in ("w1", "w2"))
        })
        self.assertEqual(self.derived["2mass"]["n_sources"], expected_tm)
        self.assertEqual(self.derived["wise"]["n_sources"], expected_wise)
        self.assertGreater(expected_tm, 0)
        self.assertGreater(expected_wise, 0)
        for name in ("2mass", "wise"):
            self.assertEqual(
                sum(self.derived[name]["field_counts"].values()),
                self.derived[name]["n_sources"],
            )

        with self._cat() as cat:
            for name, mag_cols, id_col in (
                ("2mass", ("jmag", "hmag", "kmag"), "twomass_id"),
                ("wise", ("w1", "w2"), "wise_id"),
            ):
                rel = cat.get_release(name)
                self.assertEqual(rel["provenance"], "derived_from_gaia_cache")
                self.assertEqual(rel["release"], f"{name}-derived-gaia-dr3")
                self.assertEqual(len(cat.fields(rel["release_id"])), 7)
                self.assertTrue(all(
                    f["provenance"] == "derived_from_gaia_cache"
                    for f in cat.fields(rel["release_id"])
                ))
                clauses = [f"{c} IS NULL" for c in mag_cols] + [f"{id_col} IS NULL"]
                bad = cat.conn.execute(
                    "SELECT COUNT(*) FROM catalog_source WHERE release_id=? AND ("
                    + " OR ".join(clauses) + ")",
                    (rel["release_id"],),
                ).fetchone()[0]
                self.assertEqual(bad, 0)

    def test_derive_is_idempotent(self):
        with StarCatalog(self.db, readonly=False) as cat:
            again = cat.derive_secondary_catalogs()
        self.assertEqual(again["2mass"]["n_sources"], self.derived["2mass"]["n_sources"])
        self.assertEqual(again["wise"]["n_sources"], self.derived["wise"]["n_sources"])
        with self._cat() as cat:
            self.assertEqual(
                cat.conn.execute(
                    "SELECT COUNT(*) FROM catalog_release WHERE catalog IN ('2mass','wise')"
                ).fetchone()[0],
                2,
            )

    def test_multi_catalog_match_has_real_photometry(self):
        with self._cat() as cat:
            row = cat.conn.execute(
                "SELECT * FROM catalog_source WHERE catalog_id=?", (TARGET_ID,)
            ).fetchone()
            self.assertIsNotNone(row)
            out = cat.match(
                [
                    {
                        "detection_id": "multi",
                        "ra": row["ra"],
                        "dec": row["dec"],
                        "mjd": 57388.5,
                        "sigma_arcsec": 0.3,
                    }
                ],
                catalogs=["gaia", "2mass", "wise"],
                radius_arcsec=1.0,
            )
        self.assertEqual(out["n_detections"], 1)
        self.assertTrue(out["n_matched"] >= 1)
        self.assertEqual(out["catalog_status"], "partial")  # 派生目录不完整
        item = out["matches"][0]
        catalogs = {c["catalog"] for c in item["candidates"]}
        self.assertTrue({"gaia", "2mass", "wise"} <= catalogs)
        by_cat = {c["catalog"]: c for c in item["candidates"]}
        tm = by_cat["2mass"]
        wise = by_cat["wise"]
        self.assertEqual(tm["catalog_id"], "17574743+0441461")
        self.assertIsNotNone(tm["jmag"])
        self.assertIsNotNone(tm["hmag"])
        self.assertIsNotNone(tm["kmag"])
        self.assertIsNone(tm["w1"])
        self.assertIsNone(tm["w2"])
        self.assertEqual(wise["catalog_id"], "J175747.41+044146.0")
        self.assertIsNotNone(wise["w1"])
        self.assertIsNotNone(wise["w2"])
        self.assertIsNone(wise["jmag"])

    def test_http_multi_catalog_match_and_targets(self):
        with self._cat() as cat:
            row = cat.conn.execute(
                "SELECT * FROM catalog_source WHERE catalog_id=?", (TARGET_ID,)
            ).fetchone()
        code, data = http_json(
            self.port,
            "POST",
            "/api/v1/catalog/match",
            {
                "detections": [
                    {
                        "detection_id": "http-multi",
                        "ra": row["ra"],
                        "dec": row["dec"],
                        "mjd": 57388.5,
                        "sigma_arcsec": 0.3,
                    }
                ],
                "catalogs": ["gaia", "2mass", "wise"],
                "radius_arcsec": 1.0,
            },
        )
        self.assertEqual(code, 200, data)
        self.assertEqual({c["catalog"] for c in data["matches"][0]["candidates"]}, {
            "gaia", "2mass", "wise"
        })
        self.assertEqual(data["catalog_status"], "partial")

        code, targets = http_json(self.port, "GET", "/api/v1/catalog/targets")
        self.assertEqual(code, 200)
        self.assertEqual(targets["simbad"], "unavailable")
        self.assertEqual(targets["count"], 7)
        by_id = {t["target_id"]: t for t in targets["targets"]}
        self.assertEqual(by_id["barnard"]["simbad_id"], "NAME Barnard's Star")
        self.assertIsNone(by_id["campo_a"]["simbad_id"])
        self.assertEqual(by_id["barnard"]["provenance"], "preset_target_names")

    def test_simbad_unavailable_is_not_a_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "simbad.sqlite"
            build_fixture_db(db)
            # 离线模式：不联网，明确标 unavailable；名称仍是 preset 元数据。
            with contextlib.redirect_stdout(io.StringIO()):
                import_simbad.main(["--db", str(db), "--offline"])
            with StarCatalog(db, readonly=True) as cat:
                payload = cat.target_names()
                self.assertEqual(payload["simbad"], "unavailable")
                self.assertEqual(payload["count"], 7)
                self.assertIn("preset", payload["note"])
                self.assertIn("不是 SIMBAD 场目录", payload["note"])
                with self.assertRaises(CatalogUnavailable):
                    cat.get_release("simbad")
                with self.assertRaises(CatalogUnavailable):
                    cat.match(
                        [{"ra": 269.45, "dec": 4.69, "sigma_arcsec": 1.0}],
                        catalogs=["simbad"],
                    )

    def test_simbad_probe_success_still_no_fake_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "simbad.sqlite"
            build_fixture_db(db)
            with patch("db.simbad.probe_simbad", return_value=(True, "ok")):
                payload = run_simbad(db, probe=True, timeout=1)
            self.assertEqual(payload["simbad"], "available_names_only")
            self.assertEqual(payload["count"], 7)
            with StarCatalog(db, readonly=True) as cat:
                self.assertIsNone(
                    cat.conn.execute(
                        "SELECT 1 FROM catalog_release WHERE catalog='simbad'"
                    ).fetchone()
                )

    def test_schema_has_target_name_table(self):
        with self._cat() as cat:
            self.assertEqual(cat.schema_version(), STAR_SCHEMA_VERSION)
            n = cat.conn.execute("SELECT COUNT(*) FROM target_name").fetchone()[0]
            self.assertEqual(n, 7)
            cols = {
                r["name"] for r in cat.conn.execute("PRAGMA table_info(target_name)")
            }
            self.assertEqual(
                cols,
                {
                    "target_id", "name", "simbad_id", "aliases_json",
                    "object_type", "ra", "dec", "provenance",
                    "simbad_status", "updated_at",
                },
            )


if __name__ == "__main__":
    unittest.main()
