"""分析响应的 schema 契约（T1 A.analysis_schema 的回归）。

不跑真实管线：mock 掉取数与图像渲染，只检查响应组装：
- 顶层 ``schema_version == "1.0"``
- ``result.readiness_scope == "analysis"``
- ``data.real_epochs`` 在 real/hybrid/synthetic/unavailable 四种 mode 下都存在
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402


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
        self.date_obs = "2026-01-0%dT00:00:00" % (i + 1)
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


class AnalysisSchemaTest(unittest.TestCase):
    def setUp(self):
        self.target = server.data_layer.get_target("barnard")
        self.epochs = [_FakeEpoch(0), _FakeEpoch(1)]

    def _run(self, mode):
        meta = {
            "mode": mode,
            "note": "schema test",
            "real_epochs": 2,
            "fetch_attempted": 2,
            "fetch_succeeded": 2,
        }
        result = {
            "class": "U",
            "readiness": "L0",
            "readiness_scope": "analysis",
            "quality": [],
            "mu_mas_yr": 0.0,
            "mu_err_mas_yr": None,
        }
        with (
            mock.patch.object(server.data_layer, "build_dataset", return_value=(self.target, self.epochs, None, meta)),
            mock.patch.object(server.pipeline, "run_pipeline", return_value=result),
            mock.patch.object(
                server.pipeline,
                "reproject_to_common",
                return_value=[np.zeros((4, 4)), np.zeros((4, 4))],
            ),
            mock.patch.object(server, "prepare_frames", return_value={"frames": [], "stack": b"x"}),
        ):
            return server.run_analysis({"target_id": "barnard", "mode": "auto", "fov": 0.25})

    def test_schema_fields_for_all_modes(self):
        for mode in ("real", "hybrid", "synthetic", "unavailable"):
            with self.subTest(mode=mode):
                out = self._run(mode)
                self.assertEqual(out["schema_version"], "1.0")
                self.assertEqual(out["result"]["readiness_scope"], "analysis")
                self.assertIn("real_epochs", out["data"])
                self.assertEqual(out["data"]["mode"], mode)
                self.assertIn("quality", out["result"])
                self.assertIn("identification", out)
                self.assertIsInstance(out["identification"], dict)


if __name__ == "__main__":
    unittest.main()
