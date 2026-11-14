from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db.blobs import BlobStore
from db.connection import connect
from db.ingest import Ingestor
from tests.fixtures import build_small_db


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.db, self.blobs = build_small_db(self.root)

    def tearDown(self):
        self.td.cleanup()

    def counts(self) -> tuple[int, int, int]:
        conn = connect(self.db, readonly=True)
        try:
            return (
                conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM frame_releases").fetchone()[0],
            )
        finally:
            conn.close()

    def test_header_ingest_idempotent(self):
        before = self.counts()
        with Ingestor(self.db, self.blobs, quiet=True) as ing:
            st = ing.ingest_headers(self.root / "headers")
        self.assertEqual(st["files_ingested"], 12)
        self.assertEqual(st["rows_inserted"], 0)
        self.assertEqual(st["rows_updated"], 12)
        self.assertEqual(self.counts(), before)

    def test_epoch_relink(self):
        conn = connect(self.db)
        try:
            conn.execute("UPDATE target_epochs SET frame_id=NULL")
            conn.commit()
        finally:
            conn.close()
        with Ingestor(self.db, self.blobs, quiet=True) as ing:
            out = ing.relink_epochs()
        self.assertEqual(out["rows_updated"], 6)
        conn = connect(self.db, readonly=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM target_epochs WHERE frame_id IS NOT NULL").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 6)

    def test_json_import_audit_and_repeat(self):
        with Ingestor(self.db, self.blobs, quiet=True) as ing:
            wk = ing.ingest_week_index(self.root / "weeks.json")
            tg = ing.ingest_targets(self.root / "targets.json")
            ep = ing.ingest_epoch_catalog(self.root / "epochs.json")
        for st in (wk, tg, ep):
            self.assertGreater(st["bytes_read"], 0)
            self.assertEqual(st["rows_inserted"], 0)
            self.assertGreater(st["rows_updated"], 0)

    def test_cutout_relink(self):
        store = BlobStore(self.blobs)
        meta = store.put_bytes(b"cutout-bytes" * 100, "npz-cutout")
        conn = connect(self.db)
        try:
            store.register(conn, meta)
            conn.execute(
                """INSERT INTO cutouts(cutout_id,obs_id,detector,sha256,kind,created_at)
                   VALUES('relink-test','2026W01_1A_0001_1',2,?,'npz','2026-01-01T00:00:00Z')""",
                (meta.sha256,),
            )
            conn.commit()
        finally:
            conn.close()
        with Ingestor(self.db, self.blobs, quiet=True) as ing:
            out = ing.relink_cutouts()
        self.assertGreaterEqual(out["rows_updated"], 1)
        conn = connect(self.db, readonly=True)
        try:
            row = conn.execute("SELECT frame_id FROM cutouts WHERE cutout_id='relink-test'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row[0])


if __name__ == "__main__":
    unittest.main()
