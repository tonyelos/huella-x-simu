from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from db.blobs import BlobStore
from db.connection import connect
from db.ingest import init_db


class BlobStoreTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.store = BlobStore(self.root / "blobs")

    def tearDown(self):
        self.td.cleanup()

    def test_put_get_verify_dedupe(self):
        data = b"SIMPLE  = T" + b"x" * 1000
        m1 = self.store.put_bytes(data, "test")
        m2 = self.store.put_bytes(data, "test")
        self.assertEqual(m1.sha256, m2.sha256)
        self.assertEqual(m1.raw_bytes, len(data))
        self.assertLess(m1.stored_bytes, m1.raw_bytes)
        self.assertEqual(self.store.get_bytes(m1.sha256, m1.codec), data)
        self.assertTrue(self.store.verify(m1.sha256, m1.codec))

    def test_put_file_incompressible(self):
        path = self.root / "random.bin"
        data = os.urandom(5000)
        path.write_bytes(data)
        m = self.store.put_file(path, "binary")
        self.assertEqual(m.codec, "none")
        self.assertEqual(self.store.get_bytes(m.sha256, m.codec), data)
        self.assertTrue(self.store.verify(m.sha256, m.codec))

    def test_gc_keeps_referenced(self):
        db = self.root / "catalog.sqlite"
        init_db(db, self.root / "blobs")
        conn = connect(db)
        try:
            keep = self.store.put_bytes(b"keep-me" * 100, "test")
            drop = self.store.put_bytes(b"drop-me" * 100, "test")
            self.store.register(conn, keep)
            self.store.register(conn, drop)
            conn.execute(
                "INSERT INTO cutouts(cutout_id,sha256,kind,created_at) VALUES(?,?,?,?)",
                ("c1", keep.sha256, "test", "2026-01-01T00:00:00Z"),
            )
            conn.commit()
            out = self.store.gc(conn)
            self.assertEqual(out["removed"], 1)
            self.assertTrue(self.store.exists(keep.sha256, keep.codec))
            self.assertFalse(self.store.exists(drop.sha256, drop.codec))
            row = conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
            self.assertEqual(row, 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
