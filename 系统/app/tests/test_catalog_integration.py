"""与 B 星表服务的可选本地集成（fixture/临时库/随机端口）。

不依赖 7 MB 真实缓存，也不占用常驻 8799：
- 用 B 仓库自带的 234 行 fixture 建临时 SQLite（tempfile）；
- B server 绑 127.0.0.1:0（随机端口）；
- 测试结束关闭并删除临时库。
"""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
B_ROOT = os.path.normpath(os.path.join(os.path.dirname(APP), "数据库工具"))
HAS_B = os.path.exists(os.path.join(B_ROOT, "catalog_server.py"))
BARNARD_ID = "4472832130942575872"
PAIR_IDS = {"4472820208110875136", "4472820212403005440"}


@unittest.skipUnless(HAS_B, "B 星表服务代码不存在")
class CatalogServiceIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="movescope-catalog-")
        cls.db_path = os.path.join(cls.tmp.name, "star_catalog.sqlite")
        subprocess.run(
            [
                sys.executable,
                "tools/import_gaia.py",
                "--offline",
                "--rebuild",
                "--db",
                cls.db_path,
            ],
            cwd=B_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if B_ROOT not in sys.path:
            sys.path.insert(0, B_ROOT)
        import catalog_server  # noqa: E402

        cls.httpd = catalog_server.make_server(cls.db_path, port=0)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)
        cls.tmp.cleanup()

    def _client(self):
        from core import catalog

        return catalog.CatalogClient(base_url=self.base, timeout=5.0)

    def test_barnard_target_requires_ruwe_for_known(self):
        import server
        from core import catalog

        client = self._client()
        with mock.patch.object(server, "CATALOG_CLIENT", client):
            out = server.run_analysis({"target_id": "barnard", "mode": "synthetic", "fov": 0.2})
        ident = out["identification"]
        # B v2 有 RUWE=1.08 时应 known；B v1 fixture 没有 RUWE 时必须降级为
        # ambiguous，不能因“位置对上”就高置信 known。
        self.assertIn(ident["status"], {"known_star", "ambiguous"}, ident)
        self.assertEqual(ident["n_candidates"], 1)
        self.assertEqual(ident["candidates"][0]["catalog_id"], BARNARD_ID)
        self.assertEqual(ident["release"], "gaia-dr3")
        self.assertLess(float(ident["separation_arcsec"]), 6.0)
        if ident["status"] == "known_star":
            self.assertEqual(ident["catalog_id"], BARNARD_ID)
            self.assertIsInstance(ident["catalog_id"], str)
            self.assertIsNotNone(ident.get("ruwe"))
            self.assertLessEqual(float(ident["ruwe"]), 2.0)
            self.assertIn("CATALOG_PARTIAL", ident["quality"])
            self.assertIn("HIGH_PM", ident["quality"])
        else:
            self.assertEqual(ident["reason"], "RUWE_UNAVAILABLE")
            self.assertIn("RUWE_UNAVAILABLE", ident["quality"])
            self.assertLessEqual(ident["confidence"], 0.4)
        self.assertNotIn("新恒星", str(ident))

    def test_0216_arcsec_pair_is_ambiguous(self):
        from core import catalog

        client = self._client()
        result = client.match(
            ra=269.6346612482693,
            dec=4.669660257173105,
            mjd=57388.5,
            sigma_arcsec=0.05,
        )
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["n_candidates"], 2)
        target = {
            "ra": 269.6346612482693,
            "dec": 4.669660257173105,
            "mjd": 57388.5,
            "sigma_arcsec": 0.05,
        }
        ident = catalog.identify(result, target=target, motion=None)
        self.assertEqual(ident["status"], "ambiguous")
        self.assertIsNone(ident["catalog_id"])
        ids = {c["catalog_id"] for c in ident["candidates"]}
        self.assertTrue(PAIR_IDS.issubset(ids), ids)
        self.assertNotIn("新恒星", str(ident))

    def test_service_unavailable_is_not_empty(self):
        from core import catalog

        client = catalog.CatalogClient(base_url="http://127.0.0.1:1", timeout=0.5)
        result = client.match(ra=269.446165, dec=4.769536, mjd=61210.0, sigma_arcsec=1.0)
        self.assertFalse(result["ok"])
        ident = catalog.identify(
            result,
            target={"ra": 269.446165, "dec": 4.769536, "mjd": 61210.0, "sigma_arcsec": 1.0},
            motion=None,
        )
        self.assertEqual(ident["status"], "catalog_unavailable")


if __name__ == "__main__":
    unittest.main()
