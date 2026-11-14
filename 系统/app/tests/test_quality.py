import os
import sys
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core import pipeline  # noqa: E402


class QualityMergeTest(unittest.TestCase):
    def test_merges_and_dedupes_preserving_order(self):
        merged = pipeline.merge_quality(
            ["BLEND_SUSPECTED", "IMPLAUSIBLE_PM"],
            ["SATURATED_SOURCE", "BLEND_SUSPECTED"],
            ["FEW_EPOCHS"],
        )
        self.assertEqual(merged, ["BLEND_SUSPECTED", "IMPLAUSIBLE_PM", "SATURATED_SOURCE", "FEW_EPOCHS"])

    def test_none_groups_are_safe(self):
        self.assertEqual(pipeline.merge_quality(None, ["A"], None), ["A"])
        self.assertEqual(pipeline.merge_quality(), [])


if __name__ == "__main__":
    unittest.main()


class BlendQualityTest(unittest.TestCase):
    def test_blend_flag_reaches_result_after_merge(self):
        import numpy as np
        from core.motion import Track, analyze_track, quality_flags

        rng = np.random.default_rng(7)
        t = np.sort(rng.uniform(60930, 61135, 8))
        period = 245.0
        mag = -1.0 * np.sin(2 * np.pi * (t - t[0]) / period)
        flux = 10 ** (-0.4 * mag)
        xi = 1.85 * (flux / np.median(flux) - 1.0) + rng.normal(0, 0.12, 8)
        eta = rng.normal(0, 0.12, 8)
        track = Track(
            t=t,
            xi=xi,
            eta=eta,
            sigma_xi=np.full(8, 0.12),
            sigma_eta=np.full(8, 0.12),
            flux=flux,
            flux_err=flux * 0.03 + 0.01,
            band=np.array(["D2"] * 8),
            epoch_id=[f"e{i}" for i in range(8)],
        )
        result = analyze_track(track, 100.0, 20.0)
        self.assertIn("BLEND_INDUCED_WANDER", result["quality"])
        merged = pipeline.merge_quality(result["quality"], quality_flags(track))
        self.assertIn("BLEND_INDUCED_WANDER", merged)
