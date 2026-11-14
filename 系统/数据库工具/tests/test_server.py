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
