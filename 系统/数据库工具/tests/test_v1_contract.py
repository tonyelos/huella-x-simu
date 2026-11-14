"""契约 v1 的真实库回归：5 个官方目标的两入口一致性。

仓库自带只读生产库 `var/catalog.sqlite`（见 .gitignore 注释），本文件直接对它跑
`epochs_v1`，保证 R1 的核心承诺（“点预设”和“手输坐标”拿到同一组历元）不会被
以后的改动悄悄打破。库文件不存在时整类跳过，空克隆/离线环境不会误报。

只读；不写库、不启 HTTP。
"""
from __future__ import annotations

import unittest

from db.catalog import Catalog
from db.config import COVERAGE_RADIUS_DEG, DEFAULT_DB, FRAME_HALF_WIDTH_DEG, TARGET_MATCH_RADIUS_DEG

# T2 硬验收：去重后的历元数（见 项目主控/02_架构设计与集成契约.md §5.2）。
EXPECTED_COUNTS = {
    "barnard": 4,
    "epsind_b": 10,
    "wise0855": 10,
    "rw_aur": 1,
    "r_dra": 3,
}

# 规范 DTO 的必备字段：缺一个 app 就没法取数。
REQUIRED_FIELDS = (
    "obsid",
    "detector",
    "release",
    "week",
    "version",
    "mjd",
    "date_obs",
    "sep_deg",
    "key",
    "slice_url",
    "provenance",
    "frame_id",
)


def _epoch_keys(out: dict) -> list[tuple]:
    return [
        (e["obsid"], e["detector"], e["key"], e["date_obs"], e["sep_deg"], e["week"], e["mjd"])
        for e in out["epochs"]
    ]


@unittest.skipUnless(DEFAULT_DB.exists(), f"需要仓库自带的生产库: {DEFAULT_DB}")
class V1ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = Catalog(DEFAULT_DB, readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def _target(self, target_id: str) -> dict:
        t = self.cat.target(target_id)
        self.assertIsNotNone(t, target_id)
        return t

    def test_two_entries_return_same_epochs(self):
        for target_id, expected in EXPECTED_COUNTS.items():
            t = self._target(target_id)
            by_target = self.cat.epochs_v1(target_id=target_id, fov=0.3)
            by_coords = self.cat.epochs_v1(ra=t["ra"], dec=t["dec"], fov=0.3)
            self.assertEqual(by_target["count"], expected, target_id)
            self.assertEqual(by_coords["count"], expected, target_id)
            self.assertEqual(by_target["matched_target_id"], target_id)
            self.assertEqual(by_coords["matched_target_id"], target_id)
            self.assertEqual(_epoch_keys(by_target), _epoch_keys(by_coords), target_id)

    def test_barnard_keeps_204_day_epoch(self):
        out = self.cat.epochs_v1(target_id="barnard", fov=0.25)
        weeks = {e["week"] for e in out["epochs"]}
        self.assertIn("2026W12_2A", weeks)
        self.assertGreater(out["coverage"]["baseline_days"], 180.0)
        # 这条历元 sep≈1.851°，超过旧的 1.745° 默认半径；默认 2.47° 才能保住它。
        self.assertGreater(max(e["sep_deg"] for e in out["epochs"]), FRAME_HALF_WIDTH_DEG)
        self.assertEqual(out["coverage"]["coverage_level"], "C2")

    def test_rw_aur_returns_one_real_epoch(self):
        out = self.cat.epochs_v1(target_id="rw_aur", fov=0.5)
        self.assertEqual(out["count"], 1)
        epoch = out["epochs"][0]
        self.assertEqual(epoch["release"], "qr3")
        self.assertEqual(epoch["provenance"], "real")
        self.assertIn("size=0.5", epoch["slice_url"])

    def test_epoch_ref_fields_dedupe_and_order(self):
        for target_id in EXPECTED_COUNTS:
            out = self.cat.epochs_v1(target_id=target_id, fov=0.3)
            seen = set()
            for e in out["epochs"]:
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, e, (target_id, field))
                    self.assertIsNotNone(e[field], (target_id, field))
                self.assertEqual(e["provenance"], "real", target_id)
                self.assertIn("size=0.3", e["slice_url"], target_id)
                self.assertTrue(e["key"].endswith(".fits"), (target_id, e["key"]))
                pair = (e["obsid"], e["detector"])
                self.assertNotIn(pair, seen, (target_id, pair))
                seen.add(pair)
            order = [(e["mjd"], e["detector"]) for e in out["epochs"]]
            self.assertEqual(order, sorted(order), target_id)
            # 每个 obsid 只出现一次（同一次曝光的 D1–D6 合并成一条）
            self.assertEqual(len(seen), out["count"], target_id)
            self.assertEqual(len({e["obsid"] for e in out["epochs"]}), out["count"], target_id)
            self.assertEqual(out["coverage"]["n_epochs"], out["count"], target_id)

    def test_coverage_block_semantics(self):
        out = self.cat.epochs_v1(target_id="barnard")
        cov = out["coverage"]
        self.assertEqual(cov["radius_deg"], COVERAGE_RADIUS_DEG)
        self.assertIn(cov["coverage_level"], {"C0", "C1", "C2", "C3"})
        self.assertNotIn("readiness", cov)
        self.assertNotIn("analysis_readiness", cov)
        self.assertIsInstance(cov["header_available"], bool)
        self.assertIsInstance(cov["n_local_cutouts"], int)
        self.assertGreaterEqual(cov["n_local_cutouts"], 0)
        self.assertEqual(cov["source"], "curated")

    def test_target_aware_within_005_deg(self):
        t = self._target("wise0855")
        # SIMBAD 与赛道坐标差约 0.027°，必须落在 0.05° 匹配阈值内。
        near = self.cat.epochs_v1(ra=t["ra"], dec=t["dec"] + 0.027, fov=0.25)
        self.assertEqual(near["matched_target_id"], "wise0855")
        direct = self.cat.epochs_v1(target_id="wise0855", fov=0.25)
        self.assertEqual(_epoch_keys(near), _epoch_keys(direct))
        self.assertLess(0.027, TARGET_MATCH_RADIUS_DEG)

        far = self.cat.epochs_v1(ra=t["ra"], dec=t["dec"] + TARGET_MATCH_RADIUS_DEG * 2, fov=0.25)
        self.assertIsNone(far["matched_target_id"])
        self.assertEqual(far["coverage"]["source"], "frames")

    def test_frames_fallback_has_full_dto(self):
        # 无目标命中的天区走 frames 锥搜索：DTO 字段同样必须齐全。
        out = self.cat.epochs_v1(ra=270.6, dec=5.4, fov=0.5)
        self.assertIsNone(out["matched_target_id"])
        self.assertEqual(out["coverage"]["source"], "frames")
        self.assertGreaterEqual(out["count"], 1)
        for e in out["epochs"]:
            for field in REQUIRED_FIELDS:
                self.assertIsNotNone(e[field], field)
            self.assertIn("size=0.5", e["slice_url"])
            self.assertTrue(e["date_obs"])

    def test_health_reports_real_header_frames(self):
        health = self.cat.health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["service"], "spherex-db")
        self.assertEqual(health["schema_version"], "1.0")
        actual = self.cat.conn.execute(
            "SELECT COUNT(*) FROM frames WHERE header_sha256 IS NOT NULL"
        ).fetchone()[0]
        # 健康检查必须报真实值，而不是设计目标。
        self.assertEqual(health["counts"]["header_frames"], actual)
        self.assertGreater(actual, 0)
        # T4 的 DoD：74 条 curated 历元 74/74 有头（不写死数字，按库内实际算）。
        total_epochs, with_headers = self.cat.conn.execute(
            """SELECT COUNT(*), COUNT(f.header_sha256)
               FROM target_epochs e LEFT JOIN frames f ON f.frame_id = e.frame_id"""
        ).fetchone()
        self.assertEqual(with_headers, total_epochs)
        self.assertGreaterEqual(total_epochs, 74)
        self.assertEqual(health["counts"]["epochs"], total_epochs)


if __name__ == "__main__":
    unittest.main()
