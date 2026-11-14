"""自定义坐标与预设目标的产品一致性回归。

问题：手动输入官方目标坐标（不带 target_id）时，RW/R Dra 走 DB 的
“少量真实历元 + 模拟”混合，得到 S；而预设目标是 HYBRID 的纯 SIM 回退，得到 V。
修复：custom 坐标在 0.05° 内命中且有本地历元目录时，复用 build_dataset(target_id)。
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402

COORDS = {
    "barnard": (269.452077, 4.693365, 0.25),
    "epsind_b": (331.044147, -56.782844, 0.25),
    "wise0855": (133.795129, -7.245147, 0.25),
    "rw_aur": (76.956526, 30.401438, 0.5),
    "r_dra": (248.167611, 66.754979, 0.5),
}


class _FakeWCS:
    crval1 = 10.0
    crval2 = 20.0
    naxis1 = 64
    naxis2 = 64

    def pixel_scale_arcsec(self):
        return 6.15


class _FakeEpoch:
    def __init__(self, i):
        self.id = f"fake-{i}"
        self.mjd = 61000.0 + i
        self.image = np.zeros((8, 8), float)
        self.psf = None
        self.provenance = "real"
        self.band = "D2"
        self.obsid = f"OBS{i}"
        self.date_obs = "2026-01-01T00:00:00"
        self.source = "fake"
        self.wcs = _FakeWCS()

    def json(self):
        return {
            "id": self.id,
            "mjd": self.mjd,
            "date_obs": self.date_obs,
            "band": self.band,
            "obsid": self.obsid,
            "provenance": self.provenance,
            "source": self.source,
            "shape": [8, 8],
            "wcs": {},
        }


def summary(out):
    r = out["result"]
    return {
        "target_id": out["target"]["id"],
        "mode": out["data"]["mode"],
        "n": out["data"]["n_epochs"],
        "real": out["data"]["real_epochs"],
        "matched": out["data"].get("matched_target_id"),
        "fallback": out["data"].get("fallback_reason"),
        "readiness": r["readiness"],
        "class": r["class"],
        "mu": round(r.get("mu_mas_yr") or 0.0, 6),
    }


class MatchPresetTargetTest(unittest.TestCase):
    def test_exact_and_within_tolerance(self):
        self.assertEqual(server.match_preset_target(*COORDS["rw_aur"][:2])["id"], "rw_aur")
        self.assertEqual(server.match_preset_target(*COORDS["r_dra"][:2])["id"], "r_dra")
        # 0.04° 偏移（赤纬 30° 处约 0.035° 球面距离）仍在 0.05° 内
        self.assertEqual(server.match_preset_target(76.956526 + 0.04, 30.401438)["id"], "rw_aur")

    def test_outside_tolerance_is_none(self):
        self.assertIsNone(server.match_preset_target(76.956526 + 0.2, 30.401438))
        self.assertIsNone(server.match_preset_target(0.0, 0.0))


class MatchedCustomRoutingTest(unittest.TestCase):
    def setUp(self):
        self.epochs = [_FakeEpoch(0), _FakeEpoch(1)]
        self.fake_result = {"class": "U", "readiness": "L0", "readiness_scope": "analysis", "quality": []}

    def tearDown(self):
        server.STORE.clear()

    def _run_routed(self, ra, dec, fov):
        with (
            mock.patch.object(server.data_layer, "build_dataset", return_value=(
                server.data_layer.get_target("rw_aur"), self.epochs, None,
                {"mode": "real", "note": "", "real_epochs": 2},
            )) as bd,
            mock.patch.object(server.pipeline, "run_pipeline", return_value=dict(self.fake_result)),
            mock.patch.object(
                server.pipeline, "reproject_to_common", return_value=[np.zeros((4, 4)), np.zeros((4, 4))]
            ),
            mock.patch.object(server, "prepare_frames", return_value={"frames": [], "stack": b"x"}),
        ):
            out = server.run_analysis({"ra": ra, "dec": dec, "fov": fov, "mode": "auto"})
        return out, bd

    def test_custom_near_preset_reuses_preset_build_dataset(self):
        for tid, (ra, dec, fov) in COORDS.items():
            with self.subTest(target=tid):
                out, bd = self._run_routed(ra, dec, fov)
                bd.assert_called_once()
                self.assertEqual(bd.call_args.args[0], tid)
                self.assertEqual(bd.call_args.kwargs["size_deg"], fov)
                self.assertEqual(out["target"]["id"], "custom")
                self.assertAlmostEqual(out["target"]["ra"], ra)
                self.assertEqual(out["data"]["matched_target_id"], tid)
                self.assertEqual(out["schema_version"], "1.0")
                self.assertEqual(out["result"]["readiness_scope"], "analysis")

    def test_unmatched_custom_still_uses_custom_path(self):
        with (
            mock.patch.object(server.data_layer, "build_dataset", return_value=(
                server.data_layer.get_target("rw_aur"), self.epochs, None,
                {"mode": "synthetic", "note": "", "real_epochs": 0},
            )) as bd,
            mock.patch.object(server.pipeline, "run_pipeline", return_value=dict(self.fake_result)),
            mock.patch.object(
                server.pipeline, "reproject_to_common", return_value=[np.zeros((4, 4)), np.zeros((4, 4))]
            ),
            mock.patch.object(server, "prepare_frames", return_value={"frames": [], "stack": b"x"}),
        ):
            out = server.run_analysis({"ra": 0.0, "dec": 0.0, "fov": 0.25, "mode": "auto"})
        bd.assert_called_once()
        self.assertEqual(bd.call_args.args[0], "custom")
        self.assertEqual(bd.call_args.kwargs["ra"], 0.0)
        self.assertIsNone(out["data"].get("matched_target_id"))


class CustomVsPresetIntegrationTest(unittest.TestCase):
    """RW / R Dra 的本地目录不足 2 期或基线 <30 天，走纯 SIM 回退，不需要网络。"""

    def tearDown(self):
        server.STORE.clear()

    def _compare(self, tid):
        ra, dec, fov = COORDS[tid]
        custom = summary(server.run_analysis({"ra": ra, "dec": dec, "fov": fov, "mode": "auto"}))
        preset = summary(server.run_analysis({"target_id": tid, "fov": fov, "mode": "auto"}))
        self.assertEqual(custom["mode"], "hybrid")
        self.assertEqual(custom["n"], 8)
        self.assertEqual(custom["readiness"], "L2")
        self.assertEqual(custom["class"], "V")
        self.assertEqual(custom["matched"], tid)
        self.assertEqual(custom["target_id"], "custom")
        keys = ("mode", "n", "real", "fallback", "readiness", "class", "mu")
        self.assertEqual({k: custom[k] for k in keys}, {k: preset[k] for k in keys})

    def test_custom_rw_matches_preset(self):
        self._compare("rw_aur")

    def test_custom_r_dra_matches_preset(self):
        self._compare("r_dra")


if __name__ == "__main__":
    unittest.main()
