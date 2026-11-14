import os
import sys
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402


class SampleModeTest(unittest.TestCase):
    def test_sample_returns_u_not_traceback(self):
        target = server.data_layer.get_target("barnard")
        out = server.run_sample_analysis(target, 0.25)
        self.assertEqual(out["data"]["mode"], "sample")
        self.assertEqual(out["data"]["fallback_reason"], "SAMPLE_MODE")
        self.assertEqual(out["result"]["class"], "U")
        self.assertEqual(out["result"]["readiness"], "L0")
        self.assertEqual(len(out["epochs"]), 2)
        self.assertIn("SAMPLE_MODE_NO_TARGET_MEASUREMENT", out["result"]["quality"])
        self.assertTrue(out["stack_url"].startswith("/api/frame/"))

    def test_sample_frames_are_served_from_store(self):
        target = server.data_layer.get_target("barnard")
        out = server.run_sample_analysis(target, 0.25)
        rec = server.STORE.get(out["analysis_id"])
        self.assertIsNotNone(rec)
        self.assertIn("frames", rec["rendered"])


if __name__ == "__main__":
    unittest.main()
