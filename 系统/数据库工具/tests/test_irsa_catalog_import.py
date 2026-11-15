"""IRSA Gator 实际场目录导入的离线测试（fake Gator，不联网）。"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db.star_catalog import StarCatalog
from tests.test_star_catalog import build_fixture_db
from tools import import_irsa_catalogs


def _fake_photometry(ra, dec, radius, **kwargs):
    fid = f"{ra:.4f}-{dec:.4f}"
    return {
        "catalogs": {
            "2mass": [
                {
                    "catalog_id": f"tm-{fid}-1",
                    "ra": ra + 0.001,
                    "dec": dec + 0.001,
                    "jmag": 13.1,
                    "hmag": 12.9,
                    "kmag": 12.8,
                    "quality": "AAA",
                }
            ],
            "wise": [
                {
                    "catalog_id": f"w-{fid}-1",
                    "ra": ra + 0.001,
                    "dec": dec + 0.001,
                    "w1": 12.4,
                    "w2": 12.2,
                    "quality": "AA",
                }
            ],
        },
        "errors": {},
    }


def _run_tool(db: Path, *extra: str) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = import_irsa_catalogs.main(["--db", str(db), *extra])
    if rc != 0:
        raise AssertionError(f"import_irsa_catalogs rc={rc}")
    return json.loads(out.getvalue())


class IRSACatalogImportTest(unittest.TestCase):
    def test_import_7_fields_quality_and_complete_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "star.sqlite"
            build_fixture_db(db)
            with patch("tools.import_irsa_catalogs.fetch_field_photometry", _fake_photometry):
                report = _run_tool(db, "--rebuild")
            self.assertEqual(report["catalogs"]["2mass"]["n_fields"], 7)
            self.assertEqual(report["catalogs"]["2mass"]["n_sources"], 7)
            self.assertEqual(report["catalogs"]["wise"]["n_fields"], 7)
            self.assertEqual(report["catalogs"]["wise"]["n_sources"], 7)
            self.assertEqual(report["catalogs"]["2mass"]["provenance"], "irsa_gator")

            with StarCatalog(db, readonly=True) as cat:
                tm = cat.get_release("2mass")
                wise = cat.get_release("wise")
                self.assertEqual(tm["release"], "2mass-irsa-gator")
                self.assertEqual(tm["provenance"], "irsa_gator")
                self.assertEqual(tm["n_fields"], 7)
                self.assertEqual(tm["epoch_ref"], "J2000.0")
                self.assertEqual(tm["epoch_mjd"], 51544.5)
                self.assertEqual(wise["release"], "wise-irsa-gator")

                row = cat.conn.execute(
                    "SELECT * FROM catalog_source WHERE release_id=? LIMIT 1",
                    (tm["release_id"],),
                ).fetchone()
                self.assertEqual(row["quality"], "AAA")
                self.assertIsNone(row["pmra"])
                self.assertIsNone(row["pmdec"])
                self.assertEqual(row["epoch_ref"], 51544.5)

                cone = cat.cone(269.452077, 4.693365, 0.01, catalog="2mass", epoch=51544.5)
                self.assertEqual(cone["catalog_status"], "ok")
                self.assertTrue(cone["coverage"]["complete"])
                self.assertGreaterEqual(cone["count"], 1)
                source = cone["sources"][0]
                self.assertEqual(source["catalog"], "2mass")
                self.assertIsNotNone(source["jmag"])
                self.assertIsNone(source["w1"])
                self.assertEqual(source["quality"], "AAA")

    def test_rebuild_keeps_gaia_and_derived(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "star.sqlite"
            build_fixture_db(db)
            with StarCatalog(db, readonly=False) as cat:
                cat.derive_secondary_catalogs()
            with patch("tools.import_irsa_catalogs.fetch_field_photometry", _fake_photometry):
                _run_tool(db, "--rebuild")
                _run_tool(db, "--rebuild")
            with StarCatalog(db, readonly=True) as cat:
                self.assertEqual(cat.get_release("gaia")["release"], "gaia-dr3")
                self.assertEqual(
                    cat.get_release("2mass", "derived-gaia-dr3")["release"],
                    "2mass-derived-gaia-dr3",
                )
                self.assertEqual(
                    cat.conn.execute(
                        "SELECT COUNT(*) FROM catalog_release WHERE catalog='2mass'"
                    ).fetchone()[0],
                    2,
                )

    def test_multi_catalog_match_actual_irsa_source(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "star.sqlite"
            build_fixture_db(db)
            with patch("tools.import_irsa_catalogs.fetch_field_photometry", _fake_photometry):
                _run_tool(db, "--rebuild")
            with StarCatalog(db, readonly=True) as cat:
                # fake 行位于 barnard 场心 +0.001°；用同一位置检测。
                row = cat.conn.execute(
                    """SELECT * FROM catalog_source s JOIN catalog_release r
                       USING(release_id)
                       WHERE r.catalog='2mass' AND s.field_id='barnard' LIMIT 1"""
                ).fetchone()
                out = cat.match(
                    [
                        {
                            "detection_id": "irsa-multi",
                            "ra": float(row["ra"]),
                            "dec": float(row["dec"]),
                            "mjd": 51544.5,
                            "sigma_arcsec": 0.3,
                        }
                    ],
                    catalogs=["2mass", "wise"],
                    radius_arcsec=1.0,
                )
            self.assertEqual(out["catalog_status"], "ok")
            self.assertEqual(out["matches"][0]["n_candidates"], 2)
            by_cat = {c["catalog"]: c for c in out["matches"][0]["candidates"]}
            self.assertEqual(by_cat["2mass"]["quality"], "AAA")
            self.assertEqual(by_cat["wise"]["quality"], "AA")
            self.assertIsNotNone(by_cat["2mass"]["jmag"])
            self.assertIsNone(by_cat["2mass"]["w1"])
            self.assertIsNotNone(by_cat["wise"]["w1"])
            self.assertIsNone(by_cat["wise"]["jmag"])

    def test_partial_field_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "star.sqlite"
            build_fixture_db(db)

            def partial(ra, dec, radius, **kwargs):
                if abs(ra - 269.452077) < 1e-6:
                    return {
                        "catalogs": {"2mass": [], "wise": []},
                        "errors": {"2mass": "simulated Gator failure", "wise": ""},
                    }
                return _fake_photometry(ra, dec, radius, **kwargs)

            with patch("tools.import_irsa_catalogs.fetch_field_photometry", partial):
                report = _run_tool(db, "--rebuild")
            self.assertEqual(report["catalogs"]["2mass"]["n_fields"], 6)
            self.assertIn("barnard", report["catalogs"]["2mass"]["errors"])
            self.assertEqual(report["catalogs"]["wise"]["n_fields"], 7)
            with StarCatalog(db, readonly=True) as cat:
                # 失败场没有伪数据；查询该场明确 503/unavailable。
                with self.assertRaises(Exception) as ctx:
                    cat.cone(269.452077, 4.693365, 0.01, catalog="2mass")
                self.assertEqual(ctx.exception.reason, "no_cached_field")


if __name__ == "__main__":
    unittest.main()
