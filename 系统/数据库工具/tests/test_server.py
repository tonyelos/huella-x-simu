from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from db.server import Handler
from tests.fixtures import build_small_db


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.root = Path(cls.td.name)
        cls.db, cls.blobs = build_small_db(cls.root)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.db_path = str(cls.db)
        cls.httpd.blob_root = str(cls.blobs)
        cls.httpd.docs_path = str(cls.root / "README.md")
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.td.cleanup()

    def get(self, path: str) -> tuple[int, bytes, str]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("Content-Type", "")

    def test_health(self):
        code, body, _ = self.get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["counts"]["frames"], 12)

    def test_cone(self):
        code, body, _ = self.get("/api/cone?ra=150&dec=20&radius=0.005&limit=10")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["total"], 2)
        sha = data["frames"][0]["header_sha256"]
        code, blob, ctype = self.get(f"/api/blob/{sha}")
        self.assertEqual(code, 200)
        self.assertTrue(blob.startswith(b"SIMPLE"))
        self.assertEqual(ctype, "application/fits")

    def test_epochs_and_coverage(self):
        code, body, _ = self.get("/api/epochs?target_id=test_target")
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)["epochs"]), 6)
        code, body, _ = self.get("/api/coverage?ra=150.05&dec=20.025&radius=0.2")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["readiness"]["level"], "L3")

    def test_v1_epochs(self):
        code, body, _ = self.get("/api/v1/epochs?target_id=test_target&fov=0.3")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["count"], 6)
        self.assertTrue(all(e["date_obs"] for e in data["epochs"]))
        self.assertTrue(all(e["slice_url"].endswith("size=0.3") for e in data["epochs"]))
        code, body2, _ = self.get("/api/v1/epochs?ra=150.05&dec=20.025&fov=0.3")
        self.assertEqual(code, 200)
        data2 = json.loads(body2)
        self.assertEqual(data["epochs"], data2["epochs"])
        self.assertEqual(data2["matched_target_id"], "test_target")

    def test_v1_coverage_and_health(self):
        code, body, _ = self.get("/api/v1/coverage?ra=150.05&dec=20.025")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["coverage_level"], "C3")
        self.assertEqual(data["radius_deg"], 2.47)
        self.assertNotIn("readiness", data)
        code, body, _ = self.get("/api/v1/health")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["schema_version"], "1.0")

    def test_v1_errors(self):
        self.assertEqual(self.get("/api/v1/epochs?ra=150&dec=20&fov=0.001")[0], 400)
        self.assertEqual(self.get("/api/v1/epochs?ra=150&dec=200")[0], 400)
        self.assertEqual(self.get("/api/v1/epochs?target_id=nope")[0], 404)

    def test_nonfinite_params_are_400(self):
        # nan/inf 会让 SQL 与比较静默失效，甚至命中别的目标；一律 400，不是 500/200。
        for path in (
            "/api/v1/epochs?ra=nan&dec=20&fov=0.25",
            "/api/v1/epochs?ra=inf&dec=20",
            "/api/v1/epochs?ra=150&dec=20&fov=nan",
            "/api/v1/epochs?ra=150&dec=20&radius=inf",
            "/api/v1/coverage?ra=nan&dec=20",
            "/api/cone?ra=nan&dec=20&radius=0.2",
            "/api/coverage?ra=150&dec=inf",
        ):
            self.assertEqual(self.get(path)[0], 400, path)

    def test_targets_include_truth(self):
        # T1 A.targets：v1/legacy 列表与单目标都必须带 truth 与 coord_unc_arcsec。
        for path in ("/api/v1/targets", "/api/targets"):
            code, body, _ = self.get(path)
            self.assertEqual(code, 200, path)
            data = json.loads(body)
            self.assertEqual(data["count"], 1, path)
            t = data["targets"][0]
            self.assertEqual(t["id"], "test_target", path)
            self.assertEqual(t["truth"], {"mu_mas_yr": 1000.0}, path)
            self.assertEqual(t["coord_unc_arcsec"], 3.0, path)
        for path in ("/api/v1/targets/test_target", "/api/targets/test_target"):
            code, body, _ = self.get(path)
            self.assertEqual(code, 200, path)
            t = json.loads(body)
            self.assertEqual(t["truth"], {"mu_mas_yr": 1000.0}, path)
            self.assertEqual(t["coord_unc_arcsec"], 3.0, path)
        # 显式 include_truth=0 才隐藏（公网部署开关；验收默认要真值）
        code, body, _ = self.get("/api/targets?include_truth=0")
        self.assertNotIn("truth", json.loads(body)["targets"][0])
        code, body, _ = self.get("/api/targets/test_target?include_truth=0")
        self.assertNotIn("truth", json.loads(body))
        self.assertEqual(self.get("/api/v1/targets/nope")[0], 404)
        self.assertEqual(self.get("/api/v1/targets/test_target/epochs")[0], 200)

    def test_v1_epochs_mjd_window(self):
        # 无参数：不回归
        code, body, _ = self.get("/api/v1/epochs?target_id=test_target")
        self.assertEqual(code, 200)
        full = json.loads(body)
        self.assertEqual(full["count"], 6)
        self.assertFalse(full["time_filter"]["applied"])

        code, body, _ = self.get(
            "/api/v1/epochs?target_id=test_target&mjd_min=61000&mjd_max=61080"
        )
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["count"], 3)
        self.assertEqual(
            data["time_filter"], {"mjd_min": 61000.0, "mjd_max": 61080.0, "applied": True}
        )
        self.assertEqual(data["query"]["mjd_min"], 61000.0)
        self.assertEqual(data["query"]["mjd_max"], 61080.0)
        self.assertEqual([e["mjd"] for e in data["epochs"]], [61000.0, 61040.0, 61080.0])

        # 与 ra/dec 入口兼容（同一目标同一窗口 → 同一组历元）
        code, body, _ = self.get(
            "/api/v1/epochs?ra=150.05&dec=20.025&mjd_min=61040&mjd_max=61120"
        )
        self.assertEqual(code, 200)
        by_coords = json.loads(body)
        self.assertEqual(by_coords["count"], 3)
        self.assertEqual(by_coords["matched_target_id"], "test_target")
        self.assertEqual(
            [(e["obsid"], e["detector"]) for e in by_coords["epochs"]],
            [(e["obsid"], e["detector"]) for e in json.loads(
                self.get("/api/v1/epochs?target_id=test_target&mjd_min=61040&mjd_max=61120")[1]
            )["epochs"]],
        )

        # 单边窗口
        code, body, _ = self.get("/api/v1/epochs?target_id=test_target&mjd_min=61160")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["count"], 2)

    def test_v1_coverage_mjd_window(self):
        code, body, _ = self.get("/api/v1/coverage?ra=150.05&dec=20.025")
        self.assertEqual(code, 200)
        full = json.loads(body)
        self.assertEqual((full["n_frames"], full["n_epochs"]), (12, 6))
        self.assertFalse(full["time_filter"]["applied"])

        code, body, _ = self.get(
            "/api/v1/coverage?ra=150.05&dec=20.025&mjd_min=61000&mjd_max=61040"
        )
        self.assertEqual(code, 200)
        win = json.loads(body)
        self.assertEqual(win["n_frames"], 4)
        self.assertEqual(win["n_epochs"], 2)
        self.assertEqual(win["mjd_min"], 61000.0)
        self.assertEqual(win["mjd_max"], 61040.0)
        self.assertTrue(win["time_filter"]["applied"])
        self.assertEqual(win["query"]["mjd_min"], 61000.0)
        # 旧字段不破坏
        for key in (
            "radius_deg", "coverage_level", "coverage_label", "header_available",
            "n_local_cutouts", "releases", "detectors", "baseline_days",
        ):
            self.assertIn(key, win)
        self.assertNotIn("readiness", win)

        # 目录外窗口 → 0 期（200，不是 500/400）
        code, body, _ = self.get(
            "/api/v1/coverage?ra=150.05&dec=20.025&mjd_min=70000&mjd_max=70001"
        )
        self.assertEqual(code, 200)
        empty = json.loads(body)
        self.assertEqual((empty["n_frames"], empty["n_epochs"]), (0, 0))
        self.assertEqual(empty["coverage_level"], "C0")

    def test_mjd_window_errors(self):
        for path in (
            "/api/v1/epochs?target_id=test_target&mjd_min=abc",
            "/api/v1/epochs?target_id=test_target&mjd_max=nan",
            "/api/v1/epochs?target_id=test_target&mjd_min=61120&mjd_max=61000",
            "/api/v1/coverage?ra=150.05&dec=20.025&mjd_min=abc",
            "/api/v1/coverage?ra=150.05&dec=20.025&mjd_min=61120&mjd_max=61000",
            "/api/coverage?ra=150.05&dec=20.025&mjd_min=inf",
        ):
            self.assertEqual(self.get(path)[0], 400, path)

    def test_export(self):
        code, body, _ = self.get("/api/export/fields.json")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["count"], 12)

    def test_errors(self):
        code, _, _ = self.get("/api/cone?ra=150")
        self.assertEqual(code, 400)
        code, _, _ = self.get("/api/nope")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
