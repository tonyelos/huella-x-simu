import math
import os
import sys
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402


class FovValidationTest(unittest.TestCase):
    def test_accepts_documented_range(self):
        self.assertAlmostEqual(server.validate_fov(0.02), 0.02)
        self.assertAlmostEqual(server.validate_fov("0.25"), 0.25)
        self.assertAlmostEqual(server.validate_fov(3.0), 3.0)

    def test_rejects_zero_negative_nan_and_huge(self):
        for bad in (0, -0.2, float("nan"), float("inf"), 3.1, "abc", None):
            with self.assertRaises(server.ApiError) as ctx:
                server.validate_fov(bad)
            self.assertEqual(ctx.exception.status, 400)
            self.assertIn(ctx.exception.code, {"INVALID_FOV", "FOV_TOO_LARGE"})


class TargetValidationTest(unittest.TestCase):
    def test_conflict_between_target_and_coords(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.resolve_request({"target_id": "barnard", "ra": 10, "dec": 10})
        self.assertEqual(ctx.exception.code, "TARGET_COORD_CONFLICT")
        self.assertEqual(ctx.exception.status, 400)

    def test_matching_coords_are_ok(self):
        tid, ra, dec, target = server.resolve_request(
            {"target_id": "barnard", "ra": 269.452077, "dec": 4.693365}
        )
        self.assertEqual(tid, "barnard")
        self.assertAlmostEqual(ra, 269.452077)
        self.assertEqual(target["id"], "barnard")

    def test_unknown_target(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.resolve_request({"target_id": "not-a-target"})
        self.assertEqual(ctx.exception.code, "UNKNOWN_TARGET")

    def test_missing_and_invalid_coords(self):
        with self.assertRaises(server.ApiError) as ctx:
            server.resolve_request({"target_id": "custom", "ra": 10})
        self.assertEqual(ctx.exception.code, "MISSING_COORDINATE")
        for bad in ({"ra": 400, "dec": 0}, {"ra": 10, "dec": 91}, {"ra": math.nan, "dec": 0}):
            with self.assertRaises(server.ApiError):
                server.resolve_request({"target_id": "custom", **bad})


if __name__ == "__main__":
    unittest.main()
