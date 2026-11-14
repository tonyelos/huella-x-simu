import os
import sys
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402


def rendered(size=10):
    return {"frames": [{"main": b"x" * size, "diff": b"y" * size}], "stack": b"z" * size}


class ResultStoreTest(unittest.TestCase):
    def test_lru_eviction(self):
        store = server.ResultStore(max_entries=2, max_bytes=10**9)
        store.put("a", rendered())
        store.put("b", rendered())
        store.get("a")  # a becomes most recent
        store.put("c", rendered())
        self.assertEqual(len(store), 2)
        self.assertIsNotNone(store.get("a"))
        self.assertIsNotNone(store.get("c"))
        self.assertIsNone(store.get("b"))

    def test_byte_cap(self):
        store = server.ResultStore(max_entries=100, max_bytes=60)
        store.put("a", rendered(10))  # 30 bytes
        store.put("b", rendered(10))
        store.put("c", rendered(10))
        self.assertLessEqual(store.nbytes, 60)
        self.assertEqual(len(store), 2)
        self.assertIsNone(store.get("a"))

    def test_evicted_id_returns_none(self):
        store = server.ResultStore(max_entries=1)
        store.put("only", rendered())
        store.put("next", rendered())
        self.assertIsNone(store.get("only"))


if __name__ == "__main__":
    unittest.main()
