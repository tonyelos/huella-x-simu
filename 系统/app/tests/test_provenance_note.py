"""provenance/文案一致性回归：取数期数、探测期数、探测基线、目录基线必须分开。

复现的旧 bug：Barnard fov=0.5 取数 4/4、目录基线 204 天，但只有 3 期参与拟合、
探测基线 6.451 天，note 却写“实取 4/4 期，基线 204 天”，UI 同一行出现 6 天/204 天。
"""
import os
import sys
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core import data as data_layer  # noqa: E402


def _caches_ready(tid: str, fov: float) -> bool:
    """5 目标 0.25/0.5 的带版本 NPZ 已随仓库提供；缺失时跳过联网集成测试。"""
    target = data_layer.get_target(tid)
    eps = data_layer.dedupe_epochs(data_layer.load_epoch_catalog().get(tid, []))
    eps = data_layer._sample_epochs(eps, 8)
    for ep in eps:
        ra, dec = data_layer.apparent_position(target, ep["mjd"])
        if not os.path.exists(data_layer._cache_path(ep, ra, dec, fov)):
            return False
    return True


class ComposeNoteTest(unittest.TestCase):
    def test_real_partial_detection_separates_baselines(self):
        meta = {
            "mode": "real",
            "fetch_attempted": 4,
            "fetch_succeeded": 4,
            "catalog_baseline_days": 203.6225,
            "note": "IRSA 静态切片取数 4/4 期；参考星相对定标。",
        }
        result = {"detected_epochs": 3, "baseline_days": 6.4511}
        note = server.compose_note(meta, result, {"mode": "real", "n_epochs": 4})
        self.assertIn("取数 4/4 期", note)
        self.assertIn("其中 3 期用于拟合", note)
        self.assertIn("探测基线 6 天", note)
        self.assertIn("目录基线 204 天", note)
        self.assertNotIn("基线 204 天；", note)  # 目录基线必须带限定词
        self.assertNotIn("，基线 204 天", note)

    def test_real_single_detection_zero_detection_baseline(self):
        meta = {
            "mode": "real",
            "fetch_attempted": 8,
            "fetch_succeeded": 8,
            "catalog_baseline_days": 182.6789,
            "note": "",
        }
        result = {"detected_epochs": 1, "baseline_days": 0.0}
        note = server.compose_note(meta, result, {"mode": "real", "n_epochs": 8})
        self.assertIn("取数 8/8 期", note)
        self.assertIn("其中 1 期用于拟合", note)
        self.assertIn("探测基线 0 天", note)
        self.assertIn("目录基线 183 天", note)

    def test_real_partial_fetch(self):
        meta = {
            "mode": "real",
            "fetch_attempted": 4,
            "fetch_succeeded": 3,
            "catalog_baseline_days": 204.0,
            "note": "",
        }
        note = server.compose_note(meta, {"detected_epochs": 3, "baseline_days": 6.0}, {"mode": "real", "n_epochs": 3})
        self.assertIn("取数 3/4 期", note)

    def test_hybrid_all_sim_has_no_zero_fetch_phrase(self):
        meta = {
            "mode": "hybrid",
            "fetch_attempted": 0,
            "fetch_succeeded": 0,
            "catalog_baseline_days": 0.0,
            "note": "只找到 1 期真实观测（2026W37_2A），无法测运动；以下为验证时序（HYBRID）。",
        }
        note = server.compose_note(meta, {"detected_epochs": 8, "baseline_days": 205.0}, {"mode": "hybrid", "n_epochs": 8})
        self.assertIn("验证时序 8 期", note)
        self.assertIn("探测基线 205 天", note)
        self.assertNotIn("取数 0/0", note)
        self.assertIn("只找到 1 期真实观测", note)

    def test_unavailable_and_synthetic_keep_meta_note(self):
        for mode in ("unavailable", "synthetic"):
            meta = {"mode": mode, "note": "DB_UNAVAILABLE：没有取到真实历元"}
            note = server.compose_note(meta, {"detected_epochs": 0}, {"mode": mode, "n_epochs": 8})
            self.assertIn("DB_UNAVAILABLE", note)


class RealNoteIntegrationTest(unittest.TestCase):
    def tearDown(self):
        server.STORE.clear()

    @unittest.skipUnless(_caches_ready("barnard", 0.5), "Barnard 0.5° 缓存缺失，跳过集成测试")
    def test_barnard_fov_0_5_detected_3_of_4(self):
        out = server.run_analysis({"target_id": "barnard", "mode": "auto", "fov": 0.5})
        data, result = out["data"], out["result"]
        self.assertEqual(data["fetch_attempted"], 4)
        self.assertEqual(data["fetch_succeeded"], 4)
        self.assertEqual(result["detected_epochs"], 3)
        self.assertLess(result["detected_epochs"], data["n_epochs"])
        self.assertAlmostEqual(data["detection_baseline_days"], 6.451, delta=0.01)
        self.assertAlmostEqual(data["catalog_baseline_days"], 203.62, delta=0.1)
        note = data["note"]
        self.assertIn("取数 4/4 期", note)
        self.assertIn("其中 3 期用于拟合", note)
        self.assertIn("探测基线 6 天", note)
        self.assertIn("目录基线 204 天", note)
        self.assertNotIn("，基线 204 天", note)
        # UI sourceNote 使用的字段：取数/探测/探测基线 + note 里的目录基线
        self.assertEqual(data["detected_epochs"], result["detected_epochs"])
        self.assertEqual(data["detection_baseline_days"], result["baseline_days"])

    @unittest.skipUnless(_caches_ready("wise0855", 0.25), "WISE 0.25° 缓存缺失，跳过集成测试")
    def test_wise_detected_1_note_has_zero_detection_baseline(self):
        out = server.run_analysis({"target_id": "wise0855", "mode": "auto", "fov": 0.25})
        data, result = out["data"], out["result"]
        self.assertEqual(result["detected_epochs"], 1)
        self.assertEqual(data["detection_baseline_days"], 0.0)
        self.assertGreater(data["catalog_baseline_days"], 180)
        note = data["note"]
        self.assertIn("其中 1 期用于拟合", note)
        self.assertIn("探测基线 0 天", note)
        self.assertIn("目录基线 183 天", note)
        self.assertNotIn("，基线 183 天", note)


if __name__ == "__main__":
    unittest.main()
