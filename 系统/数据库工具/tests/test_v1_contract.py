"""契约 v1 的真实库回归：5 个官方目标的两入口一致性。

仓库自带只读生产库 `var/catalog.sqlite`（见 .gitignore 注释），本文件直接对它跑
`epochs_v1`，保证 R1 的核心承诺（“点预设”和“手输坐标”拿到同一组历元）不会被
以后的改动悄悄打破。库文件不存在时整类跳过，空克隆/离线环境不会误报。

只读；不写库、不启 HTTP。
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from db.catalog import Catalog
from db.config import (
    COVERAGE_RADIUS_DEG,
    DEFAULT_BLOBS,
    DEFAULT_DB,
    FRAME_HALF_WIDTH_DEG,
    TARGET_MATCH_RADIUS_DEG,
    TOOL_ROOT,
)
from db.server import Handler

APP_TARGETS = TOOL_ROOT.parent / "app" / "data" / "targets.json"
# T1 A.targets 的 8 个契约字段（含 truth / coord_unc_arcsec）。
TARGET_FIELDS = ("id", "name", "ra", "dec", "official", "expected", "coord_unc_arcsec", "truth")

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


def _app_targets() -> dict[str, dict]:
    return {
        t["id"]: t
        for t in json.loads(APP_TARGETS.read_text(encoding="utf-8"))["targets"]
    }


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

    @unittest.skipUnless(APP_TARGETS.exists(), f"需要 app targets: {APP_TARGETS}")
    def test_epochs_mjd_window(self):
        """时间窗口：Barnard 全窗口 4 期；2025-08-29–2025-09-05（MJD 60916–60923）2 期。"""
        full = self.cat.epochs_v1(target_id="barnard")
        self.assertEqual(full["count"], 4)
        self.assertFalse(full["time_filter"]["applied"])

        window = self.cat.epochs_v1(target_id="barnard", mjd_min=60916, mjd_max=60923)
        self.assertEqual(window["count"], 2)
        self.assertTrue(window["time_filter"]["applied"])
        self.assertEqual(
            window["time_filter"], {"mjd_min": 60916.0, "mjd_max": 60923.0, "applied": True}
        )
        self.assertEqual(
            [e["obsid"] for e in window["epochs"]],
            ["2025W35_2A_0221_1", "2025W35_2A_0233_1"],
        )
        self.assertTrue(all(60916.0 <= e["mjd"] <= 60923.0 for e in window["epochs"]))
        self.assertEqual(window["coverage"]["n_epochs"], 2)

        # ra/dec 入口与 target_id 入口在窗口下仍然同形
        coords = self.cat.epochs_v1(ra=269.452077, dec=4.693365, mjd_min=60916, mjd_max=60923)
        self.assertEqual(coords["matched_target_id"], "barnard")
        self.assertEqual(_epoch_keys(coords), _epoch_keys(window))

        # 单边窗口：只要 2026W12 那条 204 天历元
        late = self.cat.epochs_v1(target_id="barnard", mjd_min=61000)
        self.assertEqual(late["count"], 1)
        self.assertEqual(late["epochs"][0]["week"], "2026W12_2A")
        self.assertEqual(late["coverage"]["baseline_days"], 0.0)

        # 空窗口：命中目标时如实返回 0 条，不能悄悄回退到 frames
        empty = self.cat.epochs_v1(target_id="barnard", mjd_min=60917, mjd_max=60922)
        self.assertEqual(empty["count"], 0)
        self.assertEqual(empty["coverage"]["source"], "curated")
        self.assertEqual(empty["coverage"]["coverage_level"], "C0")

    def test_epochs_mjd_invalid(self):
        bad = (
            {"mjd_min": "abc"},
            {"mjd_max": "2025-08-29"},
            {"mjd_min": float("nan")},
            {"mjd_max": float("inf")},
            {"mjd_min": 60923, "mjd_max": 60916},
        )
        for kwargs in bad:
            with self.assertRaises(ValueError, msg=str(kwargs)):
                self.cat.epochs_v1(target_id="barnard", **kwargs)
            with self.assertRaises(ValueError, msg=str(kwargs)):
                self.cat.coverage(
                    269.452077,
                    4.693365,
                    mjd_min=kwargs.get("mjd_min"),
                    mjd_max=kwargs.get("mjd_max"),
                )

    def test_coverage_mjd_window(self):
        full = self.cat.coverage(269.452077, 4.693365)
        self.assertFalse(full["time_filter"]["applied"])
        self.assertEqual(full["radius_deg"], COVERAGE_RADIUS_DEG)

        window = self.cat.coverage(269.452077, 4.693365, mjd_min=60916, mjd_max=60923)
        self.assertTrue(window["time_filter"]["applied"])
        self.assertEqual(window["time_filter"]["mjd_min"], 60916.0)
        self.assertLess(window["n_frames"], full["n_frames"])
        self.assertLessEqual(window["n_epochs"], full["n_epochs"])
        self.assertGreaterEqual(window["mjd_min"], 60916.0)
        self.assertLessEqual(window["mjd_max"], 60923.0)
        self.assertEqual(window["query"]["mjd_min"], 60916.0)
        # 旧字段齐全，且不返回 readiness
        for key in (
            "radius_deg", "coverage_level", "coverage_label", "header_available",
            "n_local_cutouts", "n_observations", "n_weeks", "n_releases",
            "releases", "detectors", "baseline_days",
        ):
            self.assertIn(key, window)
        # Catalog.coverage() 是旧接口：仍带 deprecated readiness（v1 HTTP 层会剥离）。
        self.assertTrue(window["readiness"]["deprecated"])

        # 目录外窗口：0 期 / C0，而不是报错
        empty = self.cat.coverage(269.452077, 4.693365, mjd_min=62000, mjd_max=62001)
        self.assertEqual(empty["n_frames"], 0)
        self.assertEqual(empty["n_epochs"], 0)
        self.assertEqual(empty["coverage_level"], "C0")
        self.assertIsNone(empty["mjd_min"])
        self.assertIsNone(empty["mjd_max"])

    def test_target_truth_matches_app_json(self):
        """T1 A.targets.app_db：7 个目标 8 字段（含 truth）与 app 逐字段一致。"""
        app = _app_targets()
        db = {t["id"]: t for t in self.cat.targets()}
        self.assertEqual(set(db), set(app))
        for tid, a in app.items():
            t = db[tid]
            self.assertIsInstance(t.get("truth"), dict, tid)
            for field in TARGET_FIELDS:
                self.assertEqual(t.get(field), a.get(field), (tid, field))
            if a.get("official"):
                # 5 官方目标的 truth 不能是“全是 null 的空壳”。
                self.assertTrue(any(v is not None for v in t["truth"].values()), tid)
                self.assertGreater(float(t["coord_unc_arcsec"]), 0.0, tid)

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


@unittest.skipUnless(
    DEFAULT_DB.exists() and APP_TARGETS.exists(), "需要生产库与 app/data/targets.json"
)
class V1HttpContractTest(unittest.TestCase):
    """HTTP 层回归：targets 对账（T1 A 组）与 epochs/coverage 时间窗。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.db_path = str(DEFAULT_DB)
        cls.httpd.blob_root = str(DEFAULT_BLOBS)
        cls.httpd.docs_path = str(TOOL_ROOT / "README.md")
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.app = _app_targets()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def get_json(self, path: str) -> tuple[int, dict]:
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_v1_targets_truth_parity_with_app(self):
        code, data = self.get_json("/api/v1/targets")
        self.assertEqual(code, 200)
        self.assertTrue(data["include_truth"])
        by_id = {t["id"]: t for t in data["targets"]}
        self.assertEqual(set(by_id), set(self.app))
        for tid, a in self.app.items():
            t = by_id[tid]
            self.assertIsInstance(t.get("truth"), dict, tid)
            for field in TARGET_FIELDS:
                self.assertEqual(t.get(field), a.get(field), (tid, field))
        # v1 404 时 T1 会回退到 legacy；legacy 也必须带 truth。
        code, legacy = self.get_json("/api/targets")
        self.assertEqual(code, 200)
        self.assertEqual(
            {t["id"]: t["truth"] for t in legacy["targets"]},
            {tid: a["truth"] for tid, a in self.app.items()},
        )
        # 单目标入口（lead 复现用 /api/targets/barnard）。
        for path in ("/api/v1/targets/barnard", "/api/targets/barnard"):
            code, one = self.get_json(path)
            self.assertEqual(code, 200, path)
            self.assertEqual(one["truth"], self.app["barnard"]["truth"], path)
            self.assertEqual(one["coord_unc_arcsec"], self.app["barnard"]["coord_unc_arcsec"], path)

    def test_include_truth_opt_out(self):
        code, data = self.get_json("/api/targets?include_truth=0")
        self.assertEqual(code, 200)
        self.assertFalse(data["include_truth"])
        self.assertTrue(all("truth" not in t for t in data["targets"]))

    def test_v1_epochs_mjd_window_http(self):
        code, data = self.get_json(
            "/api/v1/epochs?target_id=barnard&mjd_min=60916&mjd_max=60923"
        )
        self.assertEqual(code, 200)
        self.assertEqual(data["count"], 2)
        self.assertEqual(
            data["time_filter"], {"mjd_min": 60916.0, "mjd_max": 60923.0, "applied": True}
        )
        code, full = self.get_json("/api/v1/epochs?target_id=barnard")
        self.assertEqual(code, 200)
        self.assertEqual(full["count"], 4)
        self.assertFalse(full["time_filter"]["applied"])
        for path in (
            "/api/v1/epochs?target_id=barnard&mjd_min=abc",
            "/api/v1/epochs?target_id=barnard&mjd_min=nan",
            "/api/v1/epochs?target_id=barnard&mjd_min=60923&mjd_max=60916",
            "/api/v1/coverage?ra=269.452077&dec=4.693365&mjd_min=abc",
        ):
            self.assertEqual(self.get_json(path)[0], 400, path)

    def test_v1_coverage_mjd_window_http(self):
        code, full = self.get_json("/api/v1/coverage?ra=269.452077&dec=4.693365")
        self.assertEqual(code, 200)
        code, win = self.get_json(
            "/api/v1/coverage?ra=269.452077&dec=4.693365&mjd_min=60916&mjd_max=60923"
        )
        self.assertEqual(code, 200)
        self.assertLess(win["n_frames"], full["n_frames"])
        self.assertTrue(win["time_filter"]["applied"])
        self.assertEqual(win["time_filter"]["mjd_min"], 60916.0)
        for key in (
            "n_frames", "n_epochs", "baseline_days", "coverage_level",
            "header_available", "n_local_cutouts", "radius_deg",
        ):
            self.assertIn(key, win)
        self.assertNotIn("readiness", win)


if __name__ == "__main__":
    unittest.main()
