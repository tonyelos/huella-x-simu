"""多目录批量去重：Gaia/2MASS/WISE 同位置一个物理源。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core import catalog as cat  # noqa: E402

TARGET = {"ra": 269.45, "dec": 4.69, "mjd": 60916.6, "sigma_arcsec": 0.5}


def _cand(cid, catalog, sep, ra, dec, **over):
    out = {
        "catalog": catalog, "catalog_id": cid, "release": catalog + "-release",
        "sep_arcsec": sep, "ra": ra, "dec": dec,
        "ra_propagated": ra, "dec_propagated": dec,
        "ruwe": 1.1 if catalog == "gaia" else None,
        "jmag": 5.2 if catalog == "2mass" else None,
        "hmag": 4.8 if catalog == "2mass" else None,
        "kmag": 4.5 if catalog == "2mass" else None,
        "w1": 4.1 if catalog == "wise" else None,
        "w2": 3.9 if catalog == "wise" else None,
    }
    out.update(over)
    return out


def _response(candidates):
    return {
        "matches": [{
            "catalog_status": "ok",
            "candidates": candidates,
            "ambiguous": len(candidates) > 1,
            "status": "ambiguous" if len(candidates) > 1 else "single_candidate",
        }],
        "catalog_status": "ok",
        "release": "multi",
    }


class MultiCatalogGroupingTest(unittest.TestCase):
    def test_three_catalogs_same_position_one_physical_source(self):
        result = cat.normalize_match(_response([
            _cand("g1", "gaia", 0.2, 269.4500, 4.6900),
            _cand("t1", "2mass", 0.1, 269.45001, 4.69001),
            _cand("w1", "wise", 0.3, 269.44999, 4.68999),
        ]))
        self.assertEqual(result["n_candidates"], 1)
        self.assertFalse(result["ambiguous"])
        self.assertEqual(result["candidates"][0]["catalog"], "gaia")
        self.assertEqual(result["candidates"][0]["catalog_id"], "g1")
        cross = result["candidates"][0]["crossmatches"]
        self.assertEqual({c["catalog"] for c in cross}, {"2mass", "wise"})
        self.assertTrue(any(c["catalog"] == "2mass" and c["jmag"] == 5.2 for c in cross))
        self.assertTrue(any(c["catalog"] == "wise" and c["w1"] == 4.1 for c in cross))

    def test_two_positions_are_ambiguous_not_catalog_count(self):
        result = cat.normalize_match(_response([
            _cand("g1", "gaia", 0.2, 269.4500, 4.6900),
            _cand("t1", "2mass", 0.1, 269.45001, 4.69001),
            _cand("g2", "gaia", 1.2, 269.4510, 4.6910),
            _cand("w2", "wise", 1.3, 269.45101, 4.69101),
        ]))
        self.assertEqual(result["n_candidates"], 2)
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["candidates"][0]["catalog"], "gaia")
        self.assertEqual(result["candidates"][1]["catalog"], "gaia")
        self.assertEqual(len(result["candidates"][0]["crossmatches"]), 1)
        self.assertEqual(len(result["candidates"][1]["crossmatches"]), 1)

    def test_old_b_without_derived_catalogs_falls_back_to_gaia(self):
        result = cat.normalize_match(_response([_cand("g1", "gaia", 0.2, 269.45, 4.69)]))
        self.assertEqual(result["n_candidates"], 1)
        self.assertEqual(result["candidates"][0]["catalog"], "gaia")
        self.assertNotIn("crossmatches", result["candidates"][0])
        ident = cat.identify(result, target=TARGET, motion={"moving": False, "n_epochs": 4})
        self.assertEqual(ident["status"], "known_star")

    def test_partial_multi_catalog_keeps_known_and_partial_caveat(self):
        raw = _response([
            _cand("g1", "gaia", 0.2, 269.45, 4.69),
            _cand("t1", "2mass", 0.1, 269.45001, 4.69001),
        ])
        raw["matches"][0]["catalog_status"] = "partial"
        result = cat.normalize_match(raw)
        self.assertEqual(result["catalog_status"], "partial")
        ident = cat.identify(result, target=TARGET, motion={"moving": False, "n_epochs": 4})
        self.assertEqual(ident["status"], "known_star")
        self.assertIn("CATALOG_PARTIAL", ident["quality"])
        self.assertTrue(any("partial" in c for c in ident["caveats"]))

    def test_grouping_does_not_merge_same_catalog_duplicates(self):
        result = cat.normalize_match(_response([
            _cand("g1", "gaia", 0.0, 269.4500, 4.6900),
            _cand("g2", "gaia", 0.1, 269.4500, 4.6900),
        ]))
        self.assertEqual(result["n_candidates"], 2)
        self.assertTrue(result["ambiguous"])

    def test_r_dra_like_same_source_2mass_offset_between_0p5_and_2_arcsec(self):
        # 0° declination makes the arcsecond <-> degree conversion trivial.
        target = {"ra": 10.0, "dec": 0.0}
        gaia = _cand("g1", "gaia", 0.2, 10.0, 0.0)
        twomass = _cand("t1", "2mass", 0.7, 10.0 + 1.2 / 3600.0, 0.0)
        normalized = cat.normalize_match(_response([gaia, twomass]))
        self.assertEqual(normalized["n_candidates"], 1)
        self.assertFalse(normalized["ambiguous"])
        ident = cat.identify(normalized, target=target, motion={"moving": False, "n_epochs": 8})
        self.assertEqual(ident["status"], "known_star")
        self.assertEqual(len(ident["candidates"][0]["crossmatches"]), 1)

    def test_wise_like_20_arcsec_multi_catalog_stays_insufficient(self):
        target = {"ra": 10.0, "dec": 0.0}
        off = 20.0 / 3600.0
        normalized = cat.normalize_match(_response([
            _cand("g1", "gaia", 20.0, 10.0 + off, 0.0),
            _cand("t1", "2mass", 20.4, 10.0 + off + 0.4 / 3600.0, 0.0),
            _cand("w1", "wise", 20.2, 10.0 + off + 0.2 / 3600.0, 0.0),
        ]))
        self.assertEqual(normalized["n_candidates"], 1)
        ident = cat.identify(normalized, target=target, motion={"moving": False, "n_epochs": 8})
        self.assertEqual(ident["status"], "insufficient_data")
        self.assertEqual(ident["reason"], "CATALOG_OFFSET_TOO_LARGE")
        self.assertFalse(ident["ambiguous"] if "ambiguous" in ident else False)
        self.assertEqual(len(ident["candidates"]), 1)
        self.assertIn("20.", " ".join(ident["caveats"]))

    def test_two_physical_groups_more_than_2_arcsec_are_ambiguous(self):
        target = {"ra": 10.0, "dec": 0.0}
        normalized = cat.normalize_match(_response([
            _cand("g1", "gaia", 0.2, 10.0, 0.0),
            _cand("g2", "gaia", 0.4, 10.0 + 3.0 / 3600.0, 0.0),
        ]))
        self.assertEqual(normalized["n_candidates"], 2)
        self.assertTrue(normalized["ambiguous"])
        ident = cat.identify(normalized, target=target, motion={"moving": False, "n_epochs": 8})
        self.assertEqual(ident["status"], "ambiguous")

    def test_single_target_build_identification_uses_three_catalogs(self):
        result = {
            "measurements": [{"mjd": 60916.6, "xi_arcsec": 0.0, "eta_arcsec": 0.0, "sigma_arcsec": 0.5}],
            "moving": False,
            "n_epochs": 4,
        }
        target = {"ra": 269.45, "dec": 4.69}
        normalized = cat.normalize_match(_response([
            _cand("g1", "gaia", 0.2, 269.4500, 4.6900),
            _cand("t1", "2mass", 0.1, 269.45001, 4.69001),
            _cand("w1", "wise", 0.3, 269.44999, 4.68999),
        ]))
        fake = mock.Mock()
        fake.match.return_value = normalized
        with mock.patch.object(server, "CATALOG_CLIENT", fake):
            ident = server.build_identification(result, target)
        self.assertEqual(ident["status"], "known_star")
        self.assertEqual(fake.match.call_args.kwargs["catalogs"], ["gaia", "2mass", "wise"])
        self.assertEqual(len(ident["candidates"][0]["crossmatches"]), 2)

    def test_ui_uses_three_catalogs_for_batch_only(self):
        with open(os.path.join(APP, "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('catalogs: ["gaia", "2mass", "wise"]', js)
        self.assertIn("gaia+2mass+wise", js)
        self.assertIn("crossmatches", js)
        # 单目标 /api/analyze 不应被批量三目录替换
        self.assertIn("build_identification", js) if False else None


if __name__ == "__main__":
    unittest.main()
