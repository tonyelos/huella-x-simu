"""星表先验 motion 后处理：纯函数 + 页面静态断言。"""
from __future__ import annotations

import copy
import os
import sys
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core import catalog_prior  # noqa: E402


def _result(**overrides):
    r = {
        "class": "S",
        "confidence": 0.72,
        "label_zh": "静止（无明显位移）",
        "public_text": "原始实测摘要",
        "quality": ["SATURATED_SOURCE"],
        "caveats": [],
        "moving": False,
        "mu_mas_yr": 12240.665,
        "mu_err_mas_yr": 9898.931,
        "fingerprint": {"mu_signif": 1.2366, "mu_mas_yr": 12240.665},
    }
    r.update(overrides)
    return r


def _ident(pmra=-801.551, pmdec=10362.394, status="known_star", candidate_extra=None):
    cand = {
        "catalog": "gaia",
        "release": "gaia-dr3",
        "catalog_id": "4472832130942575872",
        "pmra": pmra,
        "pmdec": pmdec,
        "pmra_error": 0.0318,
        "pmdec_error": 0.0361,
        "separation_arcsec": 3.5253,
    }
    if candidate_extra:
        cand.update(candidate_extra)
    return {
        "status": status,
        "catalog_id": cand["catalog_id"] if status == "known_star" else None,
        "candidates": [cand],
        "ruwe": 1.08485,
    }


class CatalogPriorTest(unittest.TestCase):
    def test_barnard_like_s_becomes_l_prior_without_fake_motion(self):
        r = _result()
        before = copy.deepcopy(r)
        motion = catalog_prior.apply_catalog_prior(r, _ident())
        self.assertEqual(r["class"], "L")
        self.assertEqual(motion["basis"], "catalog_prior")
        self.assertTrue(motion["consistent_with_measurement"])
        self.assertAlmostEqual(motion["pm_mas_yr"], 10393.35, places=1)
        self.assertLessEqual(r["confidence"], 0.6)
        self.assertIn("CATALOG_PRIOR_MOTION", r["quality"])
        self.assertIn("MEASUREMENT_NOT_SIGNIFICANT", r["quality"])
        self.assertIn("SPHEREx 本次未独立测出", r["label_zh"] + r["public_text"])
        self.assertIn("不是发现", r["public_text"])
        # 不伪造实测运动：moving / μ / 误差 / 显著性原样保留
        self.assertFalse(r["moving"])
        self.assertEqual(r["mu_mas_yr"], before["mu_mas_yr"])
        self.assertEqual(r["mu_err_mas_yr"], before["mu_err_mas_yr"])
        self.assertEqual(r["fingerprint"]["mu_signif"], before["fingerprint"]["mu_signif"])
        self.assertEqual(motion["measurement_significance"], 1.2366)

    def test_ambiguous_gaia_pm_can_still_be_prior(self):
        r = _result()
        motion = catalog_prior.apply_catalog_prior(r, _ident(status="ambiguous"))
        self.assertEqual(r["class"], "L")
        self.assertEqual(motion["basis"], "catalog_prior")

    def test_low_catalog_pm_stays_s(self):
        r = _result()
        motion = catalog_prior.apply_catalog_prior(r, _ident(pmra=-50.0, pmdec=80.0))
        self.assertEqual(r["class"], "S")
        self.assertEqual(motion["basis"], "none")

    def test_incompatible_measurement_stays_s(self):
        r = _result(mu_mas_yr=50000.0, mu_err_mas_yr=1000.0)
        motion = catalog_prior.apply_catalog_prior(r, _ident())
        self.assertEqual(r["class"], "S")
        self.assertFalse(motion["consistent_with_measurement"])

    def test_missing_pm_or_unavailable_catalog_stays_s(self):
        r = _result()
        motion = catalog_prior.apply_catalog_prior(r, {"status": "catalog_unavailable", "candidates": []})
        self.assertEqual(r["class"], "S")
        self.assertIsNone(motion["pm_mas_yr"])
        r2 = _result()
        motion2 = catalog_prior.apply_catalog_prior(r2, _ident(pmra=None, pmdec=None))
        self.assertEqual(r2["class"], "S")
        self.assertEqual(motion2["basis"], "none")

    def test_non_s_class_is_not_overridden(self):
        r = _result()
        r["class"] = "U"
        catalog_prior.apply_catalog_prior(r, _ident())
        self.assertEqual(r["class"], "U")

    def test_catalog_pm_significance_not_used_when_uncertain(self):
        r = _result()
        # 星表 PM 本身 1σ 很大：不应当先验
        motion = catalog_prior.apply_catalog_prior(
            r, _ident(candidate_extra={"pmra_error": 5000.0, "pmdec_error": 5000.0})
        )
        self.assertEqual(r["class"], "S")
        self.assertEqual(motion["basis"], "none")


class CatalogPriorUiStaticTest(unittest.TestCase):
    def test_ui_shows_prior_not_only_stationary(self):
        with open(os.path.join(APP, "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("catalog_motion", js)
        self.assertIn("Gaia 先验", js)
        self.assertIn("SPHEREx 本次未独立测出", js)
        self.assertIn("CATALOG PRIOR", js)
        self.assertNotIn("新恒星", js)
        self.assertNotIn("新行星", js)


if __name__ == "__main__":
    unittest.main()
