"""T4 头文件采集器的离线单元测试。

全部走本地 ``http.server`` 假 S3，不访问真实网络、不碰生产库：
- Range / 206 / 200-忽略 Range / 404 / 500 / 429 / 超时；
- 原子写、幂等重跑、损坏缓存重采、manifest 合并；
- ``ingest headers`` 后 ``header_sha256/date_obs/target_epochs.frame_id`` 非空。
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

from tests.fixtures import build_small_db, make_header
from tools import harvest_headers as H


class FakeS3:
    """最小 ListObjectsV2 + GetObject(Range) 假服务。"""

    def __init__(self) -> None:
        self.headers: dict[str, bytes] = {}
        self.script: dict[str, deque[int]] = {}
        self.ignore_range: set[str] = set()
        self.delays: dict[str, float] = {}
        self.requests: list[dict] = []
        self.list_requests: list[dict] = []
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    # ---------------------------------------------------------------- 生命周期
    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def add(self, key: str, blob: bytes) -> None:
        self.headers[key] = blob

    def count(self, key: str) -> int:
        with self.lock:
            return sum(1 for r in self.requests if r["key"] == key)

    # ---------------------------------------------------------------- 处理
    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: D102
                pass

            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if "list-type" in qs:
                    fake._handle_list(self, qs)
                    return
                fake._handle_object(self, urllib.parse.unquote(parsed.path).lstrip("/"))

        return Handler

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: int, body: bytes, headers=None) -> None:
        handler.send_response(status)
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            try:
                handler.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _handle_object(self, handler: BaseHTTPRequestHandler, key: str) -> None:
        with self.lock:
            record = {"key": key, "range": handler.headers.get("Range"), "status": None}
            self.requests.append(record)
            script = self.script.get(key)
            status = script.popleft() if script else None
            blob = self.headers.get(key)
            delay = self.delays.get(key, 0.0)
        if delay:
            time.sleep(delay)
        if status is not None:
            record["status"] = status
            self._respond(handler, status, b"")
            return
        if blob is None:
            record["status"] = 404
            self._respond(handler, 404, b"not found")
            return
        rng = handler.headers.get("Range", "")
        if key in self.ignore_range or not rng:
            record["status"] = 200
            self._respond(handler, 200, blob, {"Content-Type": "application/octet-stream"})
            return
        try:
            start_s, end_s = rng.replace("bytes=", "").split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else len(blob) - 1
        except ValueError:
            record["status"] = 416
            self._respond(handler, 416, b"bad range")
            return
        body = blob[start : end + 1]
        record["status"] = 206
        self._respond(
            handler,
            206,
            body,
            {
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start}-{start + len(body) - 1}/{len(blob)}",
                "Accept-Ranges": "bytes",
            },
        )

    def _handle_list(self, handler: BaseHTTPRequestHandler, qs: dict[str, list[str]]) -> None:
        prefix = (qs.get("prefix") or [""])[0]
        delimiter = (qs.get("delimiter") or [""])[0]
        with self.lock:
            self.list_requests.append({"prefix": prefix, "delimiter": delimiter})
            keys = sorted(k for k in self.headers if k.startswith(prefix))
            bodies = [k for k in keys if not delimiter or delimiter not in k[len(prefix) :]]
            prefixes = sorted(
                {
                    prefix + k[len(prefix) :].split(delimiter, 1)[0] + delimiter
                    for k in keys
                    if delimiter and delimiter in k[len(prefix) :]
                }
            )
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
            "<Name>fake</Name>",
            f"<Prefix>{escape(prefix)}</Prefix>",
            "<IsTruncated>false</IsTruncated>",
        ]
        for key in bodies:
            parts.append(f"<Contents><Key>{escape(key)}</Key><Size>65536</Size></Contents>")
        for item in prefixes:
            parts.append(f"<CommonPrefixes><Prefix>{escape(item)}</Prefix></CommonPrefixes>")
        parts.append("</ListBucketResult>")
        self._respond(
            handler,
            200,
            "".join(parts).encode(),
            {"Content-Type": "application/xml"},
        )


class HarvesterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.fake = FakeS3()
        self.addCleanup(self.fake.stop)

    def tearDown(self) -> None:
        self.td.cleanup()

    # ---------------------------------------------------------------- 工具
    def key_for(self, week: str, detector: int = 1, *, field: int = 1, rep: int = 1,
                version: str = "l2b-test-001", release: str = "dr1") -> str:
        return (
            f"{release}/level2/{week}/{version}/{detector}/"
            f"level2_{week}_{field:04d}_{rep}D{detector}_spx_{version}.fits"
        )

    def serve_blob(self, key: str, *, obs_id: str, detector: int, ra: float = 150.0,
                   dec: float = 20.0, mjd: float = 61000.0) -> bytes:
        path = self.root / "make" / f"{len(self.fake.headers)}.hdr"
        make_header(path, obs_id, detector, ra, dec, mjd)
        blob = path.read_bytes()
        self.assertLessEqual(len(blob), H.HDR_BYTES)
        padded = blob + b"\0" * (H.HDR_BYTES - len(blob))
        self.fake.add(key, padded)
        return padded

    def serve_db_epochs(self) -> tuple[Path, Path, list[str]]:
        db, blobs = build_small_db(self.root)
        conn = sqlite3.connect(str(db))
        try:
            rows = list(
                conn.execute("SELECT fits_key, obs_id, detector FROM target_epochs ORDER BY id")
            )
        finally:
            conn.close()
        keys = []
        for idx, (key, obs_id, detector) in enumerate(rows):
            self.serve_blob(
                key,
                obs_id=str(obs_id),
                detector=int(detector),
                ra=150.0 + idx * 0.02,
                dec=20.0 + idx * 0.01,
                mjd=61000.0 + idx * 40.0,
            )
            keys.append(str(key))
        return db, blobs, keys

    def args(self, db: Path, blobs: Path, out: Path, manifest: Path, *extra: str) -> list[str]:
        return [
            "--source", "epochs",
            "--db", str(db),
            "--blobs", str(blobs),
            "--out", str(out),
            "--manifest", str(manifest),
            "--s3-base", self.fake.base,
            "--workers", "2",
            "--retries", "1",
            "--backoff", "0",
            "--timeout", "2",
            *extra,
        ]

    @staticmethod
    def run_main(argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = H.main(argv)
        return rc, buf.getvalue()

    @staticmethod
    def reset_db(db: Path) -> None:
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                """UPDATE frames SET header_sha256=NULL, date_obs=NULL,
                     pixel_scale_arcsec=NULL, crpix1=NULL, crpix2=NULL,
                     sip_order=NULL, psf_fwhm_arcsec=NULL, l2dqa=NULL"""
            )
            conn.execute("UPDATE target_epochs SET frame_id=NULL")
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- 解析/命名
    def test_parse_key_and_filename(self):
        key = self.key_for("2025W35_2A", 4, field=221, rep=1, version="l2b-v27-2026-231")
        parsed = H.parse_key(key)
        self.assertEqual(parsed["release"], "dr1")
        self.assertEqual(parsed["week"], "2025W35_2A")
        self.assertEqual(parsed["version"], "l2b-v27-2026-231")
        self.assertEqual(parsed["detector"], 4)
        self.assertEqual(parsed["obsid"], "2025W35_2A_0221_1")
        self.assertIsNone(H.parse_key("not/a/key.fits"))
        item = H.planned_from_key(key, "https://s3.test")
        self.assertTrue(item.file is None)
        self.assertTrue(H.readable_name(item).startswith("dr1_2025W35_2A_2025W35_2A_0221_1_D4_"))

    def test_assign_files_resolves_collision(self):
        key_a = self.key_for("2026W01_1A", 1)
        key_b = key_a.replace("/1/", "/2/")  # 不同 key，但 readable 字段完全一样
        items = [
            H.planned_from_key(key_a, "https://s3.test"),
            H.planned_from_key(key_b, "https://s3.test"),
        ]
        for item in items:
            item.detector = 1
        H.assign_files(items, self.root / "out")
        self.assertNotEqual(items[0].file.name, items[1].file.name)
        self.assertIn(__import__("hashlib").sha1(key_b.encode()).hexdigest()[:8], items[1].file.name)

    # ---------------------------------------------------------------- 计划
    def test_plan_epochs_prefers_fits_key(self):
        db, _ = build_small_db(self.root)
        items, stats = H.plan_epochs(db, self.fake.base)
        self.assertEqual(stats["target_epochs"], 6)
        self.assertEqual(stats["unique_keys"], 6)
        self.assertEqual(len(items), 6)
        self.assertTrue(all(item.url.startswith(self.fake.base + "/") for item in items))
        self.assertTrue(all(item.file is None for item in items))
        self.assertEqual(len({item.key for item in items}), 6)

    def test_plan_epochs_falls_back_to_week_version(self):
        db, _ = build_small_db(self.root)
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT id, fits_key, week FROM target_epochs ORDER BY id LIMIT 1"
            ).fetchone()
            conn.execute("UPDATE target_epochs SET fits_key=NULL WHERE id=?", (row[0],))
            conn.commit()
        finally:
            conn.close()
        items, _ = H.plan_epochs(db, self.fake.base)
        keys = {item.key for item in items}
        self.assertIn(row[1], keys)
        self.assertFalse(any(item.error for item in items))

    def test_plan_epochs_unresolved_when_no_version(self):
        db, _ = build_small_db(self.root)
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT id, week FROM target_epochs ORDER BY id LIMIT 1"
            ).fetchone()
            conn.execute("UPDATE target_epochs SET fits_key=NULL WHERE id=?", (row[0],))
            conn.execute("UPDATE weeks SET version='' WHERE week=?", (row[1],))
            conn.commit()
        finally:
            conn.close()
        items, _ = H.plan_epochs(db, self.fake.base)
        bad = [item for item in items if item.error]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].url, "")
        self.assertIn("fits_key 缺失", bad[0].error)

    def test_dry_run_writes_nothing(self):
        db, blobs, _ = self.serve_db_epochs()
        out = self.root / "cache"
        manifest = self.root / "manifest.json"
        rc, text = self.run_main(
            self.args(db, blobs, out, manifest, "--dry-run", "--no-ingest")
        )
        self.assertEqual(rc, 0)
        self.assertIn("计划 6 条", text)
        self.assertFalse(out.exists())
        self.assertFalse(manifest.exists())
        self.assertEqual(self.fake.requests, [])

    def test_limit(self):
        db, blobs, _ = self.serve_db_epochs()
        rc, text = self.run_main(
            self.args(db, blobs, self.root / "cache", self.root / "m.json",
                      "--dry-run", "--no-ingest", "--limit", "2")
        )
        self.assertEqual(rc, 0)
        self.assertIn("计划 2 条", text)
        plan_lines = [line for line in text.splitlines() if line.startswith("[")]
        self.assertEqual(len(plan_lines), 2)

    # ---------------------------------------------------------------- 采集
    def test_harvest_writes_headers_and_manifest(self):
        db, blobs, keys = self.serve_db_epochs()
        out = self.root / "cache"
        manifest = self.root / "manifest.json"
        rc, _ = self.run_main(self.args(db, blobs, out, manifest, "--no-ingest"))
        self.assertEqual(rc, 0)
        files = sorted(out.glob("*.hdr"))
        self.assertEqual(len(files), 6)
        self.assertEqual(len(list(out.glob(".*.tmp"))), 0)
        payload = json.loads(manifest.read_text())
        self.assertEqual(payload["summary"]["entries"], 6)
        self.assertEqual(payload["summary"]["ok"], 6)
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertEqual(payload["summary"]["bytes_total"], 6 * H.HDR_BYTES)
        entries = {e["key"]: e for e in payload["entries"]}
        for key in keys:
            self.assertEqual(entries[key]["status"], "ok")
            self.assertEqual(entries[key]["obsid"], H.parse_key(key)["obsid"])
            self.assertIsNotNone(entries[key]["sha256"])
            self.assertTrue(Path(entries[key]["file"]).is_file())
            self.assertTrue(self.fake.count(key) == 1)
        for request in self.fake.requests:
            self.assertEqual(request["range"], "bytes=0-65535")
            self.assertEqual(request["status"], 206)

    def test_resume_is_idempotent(self):
        db, blobs, keys = self.serve_db_epochs()
        out = self.root / "cache"
        manifest = self.root / "manifest.json"
        self.assertEqual(self.run_main(self.args(db, blobs, out, manifest, "--no-ingest"))[0], 0)
        before = {p.name: p.stat().st_mtime_ns for p in out.glob("*.hdr")}
        requests_before = len(self.fake.requests)
        rc, text = self.run_main(
            self.args(db, blobs, out, manifest, "--resume", "--no-ingest")
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.fake.requests), requests_before)
        self.assertIn("下载 0", text)
        after = {p.name: p.stat().st_mtime_ns for p in out.glob("*.hdr")}
        self.assertEqual(before, after)
        payload = json.loads(manifest.read_text())
        self.assertEqual(payload["summary"]["cached_this_run"], 6)
        self.assertEqual(payload["summary"]["ok"], 0)
        self.assertEqual(payload["summary"]["cached"], 6)

    def test_resume_refetches_corrupt_cache(self):
        db, blobs, keys = self.serve_db_epochs()
        out = self.root / "cache"
        manifest = self.root / "manifest.json"
        self.assertEqual(self.run_main(self.args(db, blobs, out, manifest, "--no-ingest"))[0], 0)
        payload = json.loads(manifest.read_text())
        victim = next(e for e in payload["entries"] if e["key"] == keys[0])
        Path(victim["file"]).write_bytes(b"garbage")
        before = self.fake.count(keys[0])
        rc, _ = self.run_main(self.args(db, blobs, out, manifest, "--resume", "--no-ingest"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.count(keys[0]), before + 1)
        self.assertTrue(Path(victim["file"]).read_bytes().startswith(b"SIMPLE"))
        updated = {e["key"]: e for e in json.loads(manifest.read_text())["entries"]}
        self.assertEqual(updated[keys[0]]["status"], "ok")

    def test_404_fails_fast_and_records_failure(self):
        db, blobs, keys = self.serve_db_epochs()
        missing = keys[0]
        self.fake.headers.pop(missing)
        out = self.root / "cache"
        manifest = self.root / "manifest.json"
        failures = self.root / "failures.json"
        rc, _ = self.run_main(
            self.args(db, blobs, out, manifest, "--no-ingest", "--failures", str(failures))
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake.count(missing), 1)
        payload = json.loads(manifest.read_text())
        entry = {e["key"]: e for e in payload["entries"]}[missing]
        self.assertEqual(entry["status"], "failed")
        self.assertIn("404", entry["error"])
        self.assertFalse(Path(entry["file"]).exists())
        self.assertEqual(json.loads(failures.read_text())["count"], 1)

    def test_500_is_retried(self):
        db, blobs, keys = self.serve_db_epochs()
        victim = keys[0]
        self.fake.script[victim] = deque([500])
        rc, _ = self.run_main(self.args(db, blobs, self.root / "c", self.root / "m.json", "--no-ingest"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.count(victim), 2)

    def test_429_is_retried(self):
        db, blobs, keys = self.serve_db_epochs()
        victim = keys[0]
        self.fake.script[victim] = deque([429])
        rc, _ = self.run_main(self.args(db, blobs, self.root / "c", self.root / "m.json", "--no-ingest"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.count(victim), 2)

    def test_range_ignored_200_is_rejected(self):
        db, blobs, keys = self.serve_db_epochs()
        victim = keys[0]
        self.fake.ignore_range.add(victim)
        rc, _ = self.run_main(self.args(db, blobs, self.root / "c", self.root / "m.json", "--no-ingest"))
        self.assertEqual(rc, 1)
        entry = {
            e["key"]: e for e in json.loads((self.root / "m.json").read_text())["entries"]
        }[victim]
        self.assertEqual(entry["status"], "failed")
        self.assertIn("Range", entry["error"])
        self.assertFalse(Path(entry["file"]).exists())
        self.assertEqual(self.fake.count(victim), 1)

    def test_timeout_is_retried_then_fails(self):
        db, blobs, keys = self.serve_db_epochs()
        victim = keys[0]
        self.fake.delays[victim] = 0.3
        argv = self.args(db, blobs, self.root / "c", self.root / "m.json", "--no-ingest")
        argv[argv.index("--timeout") + 1] = "0.05"
        rc, _ = self.run_main(argv)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fake.count(victim), 2)
        entry = {
            e["key"]: e for e in json.loads((self.root / "m.json").read_text())["entries"]
        }[victim]
        self.assertEqual(entry["status"], "failed")
        self.assertIn("尝试 2 次", entry["error"])

    def test_non_fits_body_is_rejected(self):
        db, blobs, keys = self.serve_db_epochs()
        victim = keys[0]
        self.fake.headers[victim] = b"<html>error</html>".ljust(H.HDR_BYTES, b" ")
        rc, _ = self.run_main(self.args(db, blobs, self.root / "c", self.root / "m.json", "--no-ingest"))
        self.assertEqual(rc, 1)
        entry = {
            e["key"]: e for e in json.loads((self.root / "m.json").read_text())["entries"]
        }[victim]
        self.assertEqual(entry["status"], "failed")
        self.assertIn("SIMPLE", entry["error"])
        self.assertFalse(Path(entry["file"]).exists())

    # ---------------------------------------------------------------- 入库
    def test_ingest_fills_header_columns_and_relinks(self):
        db, blobs, _ = self.serve_db_epochs()
        self.reset_db(db)
        out = self.root / "cache"
        manifest = self.root / "manifest.json"
        rc, _ = self.run_main(self.args(db, blobs, out, manifest))
        self.assertEqual(rc, 0)

        conn = sqlite3.connect(str(db))
        try:
            frame_count = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
            header_frames = conn.execute(
                "SELECT COUNT(*) FROM frames WHERE header_sha256 IS NOT NULL"
            ).fetchone()[0]
            linked = conn.execute(
                "SELECT COUNT(*) FROM target_epochs WHERE frame_id IS NOT NULL"
            ).fetchone()[0]
            date_obs = conn.execute(
                "SELECT COUNT(*) FROM frames WHERE date_obs IS NOT NULL AND date_obs<>''"
            ).fetchone()[0]
            filled = conn.execute(
                """SELECT COUNT(*) FROM frames
                   WHERE pixel_scale_arcsec IS NOT NULL AND crpix1 IS NOT NULL
                     AND psf_fwhm_arcsec IS NOT NULL AND l2dqa IS NOT NULL"""
            ).fetchone()[0]
            header_blobs = conn.execute(
                "SELECT COUNT(*) FROM blobs WHERE kind='fits-header'"
            ).fetchone()[0]
            run_count = conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(header_frames, 6)
        self.assertEqual(linked, 6)
        self.assertEqual(date_obs, 6)
        self.assertEqual(filled, 6)
        self.assertGreaterEqual(header_blobs, 6)

        rc, text = self.run_main(self.args(db, blobs, out, manifest, "--resume"))
        self.assertEqual(rc, 0)
        self.assertIn('"epochs_linked": 6', text)
        conn = sqlite3.connect(str(db))
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], frame_count)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM blobs WHERE kind='fits-header'").fetchone()[0],
                header_blobs,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM target_epochs WHERE frame_id IS NOT NULL").fetchone()[0],
                6,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0], run_count + 1)
        finally:
            conn.close()

    # ---------------------------------------------------------------- keys / weeks
    def test_keys_source_merges_manifest(self):
        _, _, keys = self.serve_db_epochs()
        manifest = self.root / "manifest.json"
        out = self.root / "cache"
        first = self.root / "first.keys"
        first.write_text("\n".join(keys[:2]) + "\n", encoding="utf-8")
        rc, _ = self.run_main([
            "--source", "keys", "--keys-file", str(first), "--out", str(out),
            "--manifest", str(manifest), "--s3-base", self.fake.base,
            "--workers", "2", "--retries", "1", "--backoff", "0", "--no-ingest",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(manifest.read_text())["summary"]["entries"], 2)
        second = self.root / "second.keys"
        second.write_text(f"s3://nasa-irsa-spherex/{keys[2]}\n", encoding="utf-8")
        rc, _ = self.run_main([
            "--source", "keys", "--keys-file", str(second), "--out", str(out),
            "--manifest", str(manifest), "--s3-base", self.fake.base,
            "--workers", "2", "--retries", "1", "--backoff", "0", "--no-ingest",
        ])
        self.assertEqual(rc, 0)
        payload = json.loads(manifest.read_text())
        self.assertEqual(payload["summary"]["entries"], 3)
        self.assertEqual(payload["last_run"]["planned"], 1)
        self.assertEqual(len(list(out.glob("*.hdr"))), 3)

    def test_weeks_source_lists_and_filters_detectors(self):
        week = "2026W01_1A"
        for det in (1, 2):
            key = self.key_for(week, det)
            self.serve_blob(key, obs_id=f"{week}_0001_1", detector=det)
        other = self.key_for("2026W02_1A", 1)
        self.serve_blob(other, obs_id="2026W02_1A_0001_1", detector=1)
        index = self.root / "week_index.json"
        index.write_text(
            json.dumps(
                {
                    "weeks": [
                        {"release": "dr1", "week": week, "version": "l2b-test-001"},
                        {"release": "dr1", "week": "2026W02_1A", "version": "l2b-test-001"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc, text = self.run_main([
            "--source", "weeks", "--week-index", str(index), "--weeks", week,
            "--detectors", "1,2", "--out", str(self.root / "cache"),
            "--manifest", str(self.root / "m.json"), "--s3-base", self.fake.base,
            "--dry-run",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("计划 2 条", text)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(
            sorted(r["prefix"] for r in self.fake.list_requests),
            sorted([
                f"dr1/level2/{week}/l2b-test-001/1/",
                f"dr1/level2/{week}/l2b-test-001/2/",
            ]),
        )

    def test_weeks_source_discovers_version(self):
        week = "2026W03_1A"
        key = self.key_for(week, 1, version="l2b-v27-2026-999")
        self.serve_blob(key, obs_id=f"{week}_0001_1", detector=1)
        index = self.root / "week_index.json"
        index.write_text(json.dumps({"weeks": [{"release": "dr1", "week": week}]}), encoding="utf-8")
        rc, text = self.run_main([
            "--source", "weeks", "--week-index", str(index), "--detectors", "1",
            "--out", str(self.root / "cache"), "--manifest", str(self.root / "m.json"),
            "--s3-base", self.fake.base, "--dry-run",
        ])
        self.assertEqual(rc, 0)
        self.assertIn(key, text)
        self.assertTrue(any(r["delimiter"] == "/" for r in self.fake.list_requests))


if __name__ == "__main__":
    unittest.main()
