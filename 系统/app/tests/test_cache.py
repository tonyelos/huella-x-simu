import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core import data as data_layer  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(APP), "参考 资料", "muestras", "M101_D2_2026W25_qr3_cutout.fits")


def fake_epoch(**over):
    ep = {"release": "dr1", "version": "v-test-1", "obsid": "OBS1", "detector": 2, "mjd": 60000.0, "sep_deg": 0.1, "key": "dr1/level2/x.fits"}
    ep.update(over)
    return ep


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_cache = data_layer.CACHE
        data_layer.CACHE = self.tmp.name

    def tearDown(self):
        data_layer.CACHE = self._old_cache
        self.tmp.cleanup()

    def test_cache_key_includes_version(self):
        ep1 = fake_epoch(version="v1")
        ep2 = fake_epoch(version="v2")
        p1 = data_layer._cache_path(ep1, 10.0, 20.0, 0.2)
        p2 = data_layer._cache_path(ep2, 10.0, 20.0, 0.2)
        self.assertNotEqual(p1, p2)
        self.assertIn("v1", os.path.basename(p1))
        self.assertIn("v2", os.path.basename(p2))

    def test_corrupt_cache_is_removed_and_not_fatal(self):
        ep = fake_epoch()
        path = data_layer._cache_path(ep, 10.0, 20.0, 0.2)
        with open(path, "wb") as fh:
            fh.write(b"not a real npz")
        with mock.patch.object(data_layer, "_download", side_effect=RuntimeError("offline")), mock.patch("time.sleep"):
            out = data_layer.fetch_epoch(ep, 10.0, 20.0, 0.2)
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(path), "损坏缓存应被删除")

    def test_atomic_save_leaves_no_tmp(self):
        path = os.path.join(self.tmp.name, "x.npz")
        data_layer._save_cache(path, image=np.zeros((2, 2), np.float32))
        self.assertTrue(os.path.exists(path))
        leftovers = [f for f in os.listdir(self.tmp.name) if ".tmp-" in f]
        self.assertEqual(leftovers, [])

    def test_fetch_from_fits_writes_cache(self):
        if not os.path.exists(SAMPLE):
            self.skipTest("sample fits missing")
        with open(SAMPLE, "rb") as fh:
            blob = fh.read()
        ep = fake_epoch()
        with mock.patch.object(data_layer, "_download", return_value=blob):
            out = data_layer.fetch_epoch(ep, 10.0, 20.0, 0.2)
        self.assertIsNotNone(out)
        self.assertEqual(out.provenance, "real")
        self.assertTrue(os.path.exists(data_layer._cache_path(ep, 10.0, 20.0, 0.2)))
        leftovers = [f for f in os.listdir(self.tmp.name) if ".tmp-" in f]
        self.assertEqual(leftovers, [])

    def test_corrupt_cache_recovers_with_successful_refetch(self):
        if not os.path.exists(SAMPLE):
            self.skipTest("sample fits missing")
        with open(SAMPLE, "rb") as fh:
            blob = fh.read()
        ep = fake_epoch()
        path = data_layer._cache_path(ep, 10.0, 20.0, 0.2)
        with open(path, "wb") as fh:
            fh.write(b"not a real npz")
        with mock.patch.object(data_layer, "_download", return_value=blob):
            out = data_layer.fetch_epoch(ep, 10.0, 20.0, 0.2)
        self.assertIsNotNone(out, "坏缓存应被删除并重新取数，而不是永久 500/None")
        # 重取后写入的文件本身可读，且不再需要下载
        out2 = data_layer.fetch_epoch(ep, 10.0, 20.0, 0.2)
        self.assertIsNotNone(out2)
        self.assertEqual(out2.mjd, out.mjd)
        leftovers = [f for f in os.listdir(self.tmp.name) if ".tmp-" in f]
        self.assertEqual(leftovers, [])

    def test_old_version_cache_not_reused(self):
        if not os.path.exists(SAMPLE):
            self.skipTest("sample fits missing")
        with open(SAMPLE, "rb") as fh:
            blob = fh.read()
        ep_v1 = fake_epoch(version="v1")
        p1 = data_layer._cache_path(ep_v1, 10.0, 20.0, 0.2)
        with mock.patch.object(data_layer, "_download", return_value=blob):
            self.assertIsNotNone(data_layer.fetch_epoch(ep_v1, 10.0, 20.0, 0.2))
        self.assertTrue(os.path.exists(p1))

        ep_v2 = fake_epoch(version="v2")
        p2 = data_layer._cache_path(ep_v2, 10.0, 20.0, 0.2)
        self.assertNotEqual(p1, p2)
        with mock.patch.object(data_layer, "_download", side_effect=RuntimeError("offline")), mock.patch("time.sleep"):
            out = data_layer.fetch_epoch(ep_v2, 10.0, 20.0, 0.2)
        self.assertIsNone(out, "不同处理版本不得复用旧 NPZ")
        self.assertFalse(os.path.exists(p2))
        self.assertTrue(os.path.exists(p1), "旧版本缓存不应被调用方改写")


if __name__ == "__main__":
    unittest.main()


class ConcurrentFetchTest(unittest.TestCase):
    def test_concurrent_fetch_does_not_crash(self):
        import threading

        if not os.path.exists(SAMPLE):
            self.skipTest("sample fits missing")
        with open(SAMPLE, "rb") as fh:
            blob = fh.read()
        tmp = tempfile.TemporaryDirectory()
        old = data_layer.CACHE
        data_layer.CACHE = tmp.name
        try:
            ep = fake_epoch()
            errors = []

            def worker():
                try:
                    out = data_layer.fetch_epoch(ep, 10.0, 20.0, 0.2)
                    if out is None:
                        errors.append("none")
                except Exception as exc:  # noqa: BLE001
                    errors.append(repr(exc))

            with mock.patch.object(data_layer, "_download", return_value=blob):
                threads = [threading.Thread(target=worker) for _ in range(4)]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join()
            self.assertEqual(errors, [])
            leftovers = [f for f in os.listdir(tmp.name) if ".tmp-" in f]
            self.assertEqual(leftovers, [])
        finally:
            data_layer.CACHE = old
            tmp.cleanup()
