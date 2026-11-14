from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from db.catalog import Catalog, angsep_deg
from db.export import export_epochs, export_fields, export_targets
from tests.fixtures import build_small_db


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.root = Path(cls.td.name)
        cls.db, cls.blobs = build_small_db(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def cat(self):
        return Catalog(self.db, readonly=True, blob_root=self.blobs)

    def test_counts(self):
        with self.cat() as c:
            s = c.stats()
            self.assertEqual(s["counts"]["frames"], 12)
            self.assertEqual(s["counts"]["observations"], 6)
            self.assertEqual(s["counts"]["weeks"], 6)
            self.assertEqual(s["counts"]["targets"], 1)
            self.assertEqual(s["counts"]["epochs"], 6)
            self.assertEqual(s["time"]["baseline_days"], 200.0)
            self.assertEqual(s["releases"][0]["release"], "dr1")

    def test_cone_exact(self):
        with self.cat() as c:
            out = c.cone(150.0, 20.0, 0.005, limit=10)
            self.assertEqual(out["total"], 2)  # 同一曝光的 D1+D2
            self.assertEqual({f["detector"] for f in out["frames"]}, {1, 2})
            self.assertLess(out["frames"][0]["sep_deg"], 1e-6)
            self.assertTrue(out["frames"][0]["key"].endswith(".fits"))
            self.assertIn("center=150.000000,20.000000d", out["frames"][0]["slice_url"])

    def test_cone_offset(self):
        with self.cat() as c:
            allf = c.cone(150.05, 20.025, 0.2, limit=100)
            page = c.cone(150.05, 20.025, 0.2, limit=5, offset=5)
            self.assertEqual(allf["total"], 12)
            self.assertEqual(len(page["frames"]), 5)
            self.assertNotEqual(allf["frames"][0]["frame_id"], page["frames"][0]["frame_id"])

    def test_coverage_readiness(self):
        with self.cat() as c:
            out = c.coverage(150.05, 20.025, 0.2)
            self.assertEqual(out["n_frames"], 12)
            self.assertEqual(out["n_epochs"], 6)
            self.assertEqual(out["n_observations"], 6)
            self.assertEqual(out["readiness"]["level"], "L3")
            self.assertEqual(len(out["nearest"]), 5)

    def test_filters(self):
        with self.cat() as c:
            out = c.cone(150.0, 20.0, 0.2, release="dr1", detector=2, limit=100)
            self.assertEqual(out["total"], 6)
            out2 = c.cone(150.0, 20.0, 0.2, mjd_min=61000, mjd_max=61040, limit=100)
            self.assertEqual(out2["total"], 4)

    def test_epochs_linked(self):
        with self.cat() as c:
            eps = c.epochs_for_target("test_target")
            self.assertEqual(len(eps), 6)
            self.assertTrue(all(e["frame_present"] for e in eps))
            self.assertTrue(all(e["slice_url"] for e in eps))
            self.assertTrue(all(e["version"] == "l2b-test-001" for e in eps))

    def test_frame(self):
        with self.cat() as c:
            f = c.frame("2026W01_1A_0001_1", 1)
            self.assertIsNotNone(f)
            self.assertEqual(f["detector"], 1)
            self.assertIn("cutouts", f)
            self.assertGreater(f["header_sha256"] and len(f["header_sha256"]), 32)

    def test_exports(self):
        with self.cat() as c:
            fields = export_fields(c.conn)
            self.assertEqual(fields["count"], 12)
            self.assertEqual(fields["keys"][0], "obsid")
            self.assertEqual(len(fields["fields"][0]), 10)
            epochs = export_epochs(c.conn)
            self.assertEqual(len(epochs["epochs"]["test_target"]), 6)
            item = epochs["epochs"]["test_target"][0]
            self.assertIn("key", item)
            self.assertIn("slice_url_002", item)
            targets = export_targets(c.conn)
            self.assertEqual(targets["targets"][0]["id"], "test_target")
            self.assertAlmostEqual(targets["targets"][0]["truth"]["mu_mas_yr"], 1000.0)
            self.assertEqual(targets["targets"][0]["coord_unc_arcsec"], 3.0)
            self.assertEqual(c.target("test_target")["coord_unc_arcsec"], 3.0)

    def test_v1_two_entries_same_epochs(self):
        with self.cat() as c:
            a = c.epochs_v1(target_id="test_target", fov=0.3)
            b = c.epochs_v1(ra=150.05, dec=20.025, fov=0.3)
            self.assertEqual(a["count"], 6)
            self.assertEqual(a["count"], b["count"])
            self.assertEqual(b["matched_target_id"], "test_target")
            self.assertEqual(
                [(e["obsid"], e["detector"], e["date_obs"], e["key"]) for e in a["epochs"]],
                [(e["obsid"], e["detector"], e["date_obs"], e["key"]) for e in b["epochs"]],
            )
            self.assertTrue(all(e["date_obs"] for e in a["epochs"]))
            self.assertTrue(all(e["slice_url"].endswith("size=0.3") for e in a["epochs"]))
            self.assertEqual(a["coverage"]["radius_deg"], 2.47)
            self.assertEqual(a["coverage"]["coverage_level"], "C3")
            self.assertTrue(a["coverage"]["header_available"])

    def test_v1_dedupe_and_match_threshold(self):
        with self.cat() as c:
            a = c.epochs_v1(target_id="test_target")
            self.assertEqual(len({(e["obsid"], e["detector"]) for e in a["epochs"]}), a["count"])
            far = c.epochs_v1(ra=10.0, dec=-10.0)
            self.assertEqual(far["count"], 0)
            self.assertIsNone(far["matched_target_id"])
            self.assertEqual(far["coverage"]["coverage_level"], "C0")

    def test_legacy_coverage_marks_readiness_deprecated(self):
        with self.cat() as c:
            out = c.coverage(150.05, 20.025, 2.47)
            self.assertIn("coverage_level", out)
            self.assertTrue(out["readiness"]["deprecated"])
            self.assertNotIn("readiness", c.epochs_v1(target_id="test_target")["coverage"])

    def test_angsep_nonfinite_is_nan(self):
        # NaN 经 min/max 夹取会变成 0°，足以把非法坐标错配到目标；必须显式返回 NaN。
        self.assertTrue(math.isnan(angsep_deg(float("nan"), 4.69, 269.452077, 4.693365)))
        self.assertTrue(math.isnan(angsep_deg(269.452077, float("inf"), 269.452077, 4.693365)))
        self.assertAlmostEqual(angsep_deg(150.0, 20.0, 150.0, 20.0), 0.0)

    def test_v1_rejects_nonfinite_coords(self):
        with self.cat() as c:
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.assertRaises(ValueError):
                    c.epochs_v1(ra=bad, dec=20.025)
                with self.assertRaises(ValueError):
                    c.epochs_v1(ra=150.05, dec=bad)
                with self.assertRaises(ValueError):
                    c.epochs_v1(target_id="test_target", ra=bad, dec=20.025)
                with self.assertRaises(ValueError):
                    c.cone(150.05, 20.025, bad)
            self.assertIsNone(c.resolve_target(float("nan"), 20.025))

    def test_integrity(self):
        with self.cat() as c:
            out = c.integrity_check(deep=True)
            self.assertEqual(out["quick_check"], "ok")
            self.assertEqual(out["foreign_key_violations"], 0)
            self.assertEqual(out["missing_blobs"], 0)
            self.assertEqual(out["corrupt_blobs"], 0)


if __name__ == "__main__":
    unittest.main()
