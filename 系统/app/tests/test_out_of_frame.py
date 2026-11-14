"""越界/边缘像素的回归测试。

背景：RW Aur 的自定义坐标只命中 1 期真实历元，且该切片被探测器边缘截断
（目标预测像素在图像外），旧实现会在 photometry.is_saturated 里构造负维度
窗口并抛 ValueError → HTTP 500。现在必须显式跳过并记录 TARGET_OUT_OF_FRAME。
"""
import copy
import math
import os
import sys
import unittest

import numpy as np

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core import data as data_layer  # noqa: E402
from core import photometry as ph  # noqa: E402
from core import pipeline  # noqa: E402
from core.simulator import default_times, generate_epochs  # noqa: E402


class PhotometryBoundsTest(unittest.TestCase):
    def setUp(self):
        self.flags = np.zeros((20, 30), np.int32)
        self.flags[5, 5] = int(ph.SAT_BITS)
        self.image = np.ones((20, 30), float)
        self.image[10, 15] = 100.0
        self.var = np.full((20, 30), 0.01)

    def test_is_saturated_out_of_bounds_is_false_not_exception(self):
        for x, y in ((-100, 5), (5, -100), (10**6, 5), (5, 10**6), (math.nan, 5), (math.inf, 5)):
            self.assertFalse(ph.is_saturated(self.flags, x, y, 1))

    def test_is_saturated_in_and_near_bounds(self):
        self.assertTrue(ph.is_saturated(self.flags, 5, 5, 1))
        self.assertTrue(ph.is_saturated(self.flags, 4, 5, 1))  # 窗口部分越界仍能检查
        self.assertFalse(ph.is_saturated(self.flags, 0, 0, 1))
        self.assertFalse(ph.is_saturated(self.flags, 25, 15, 1))

    def test_measure_at_out_of_frame_returns_explicit_state(self):
        for x, y in ((-52.7, 146.3), (146.0, -3.0), (999, 10), (10, 999), (math.nan, 10)):
            m = ph.measure_at(self.image, self.var, self.flags, x, y, None)
            self.assertTrue(m["out_of_frame"], (x, y, m))
            self.assertTrue(math.isnan(m["flux"]))

    def test_measure_at_edge_and_all_nan_do_not_raise(self):
        # 图像边缘（0,0）允许返回有限测量或 NaN，但必须带显式状态
        edge = ph.measure_at(self.image, self.var, self.flags, 0, 0, None)
        self.assertIn("out_of_frame", edge)
        self.assertFalse(edge["out_of_frame"])
        self.assertIn("flux", edge)

        nan_img = np.full((20, 30), np.nan)
        nan_var = np.ones((20, 30))
        m = ph.measure_at(nan_img, nan_var, np.zeros((20, 30), np.int32), 10, 15, None)
        self.assertIn("out_of_frame", m)
        self.assertFalse(m["out_of_frame"])
        self.assertTrue(math.isnan(m["flux"]))


class PipelineOutOfFrameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        target = data_layer.get_target("rw_aur")
        times = default_times(8, start_mjd=60930.0, span_days=205.0)
        cls.epochs, cls.truth = generate_epochs(target, times, fov_deg=0.2, seed=7)
        cls.target = target

    def _shifted(self, indices):
        epochs = copy.deepcopy(list(self.epochs))
        for i in indices:
            ep = epochs[i]
            ep.wcs.crval1 = (ep.wcs.crval1 + 3.0) % 360.0  # 目标预测像素被推到切片外
        return epochs

    def test_one_out_of_frame_epoch_is_skipped_with_quality(self):
        epochs = self._shifted([0])
        result = pipeline.run_pipeline(self.target, epochs, truth=self.truth, mode="hybrid")
        self.assertIn("TARGET_OUT_OF_FRAME", result["quality"])
        self.assertEqual(result["detected_epochs"], 7)
        self.assertEqual(len(result["track"]["t"]), 7)
        self.assertTrue(any("切片外" in e for e in result["evidence"]))

    def test_all_out_of_frame_returns_u_without_exception(self):
        result = pipeline.run_pipeline(self.target, self._shifted(range(8)), truth=self.truth, mode="hybrid")
        self.assertEqual(result["class"], "U")
        self.assertEqual(result["readiness"], "L0")
        self.assertIn("TARGET_OUT_OF_FRAME", result["quality"])
        self.assertEqual(result["detected_epochs"], 0)


if __name__ == "__main__":
    unittest.main()
