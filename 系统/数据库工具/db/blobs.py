"""内容寻址 Blob 仓库。

布局::

    var/blobs/ab/cd/abcdef...z

- 文件名 = 明文 SHA-256，两级目录分片，单目录不会堆到几十万文件；
- 默认 zlib 压缩（FITS 头 45 KB -> 约 8 KB），压缩后反而更大时存原样；
- 写入走临时文件 + os.replace，断电不会留下半截文件；
- 同一内容只写一次，天然去重；
- 数据库 `blobs` 表只存元数据与相对路径，像素/头文件本身不进 SQLite。

这层是“大量数据”的关键：SQLite 保持小而快，Blob 仓库可以长到 TB 级，
两者用 sha256 连接。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

CODEC_ZLIB = "zlib-6"
CODEC_NONE = "none"
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class BlobMeta:
    sha256: str
    raw_bytes: int
    stored_bytes: int
    codec: str
    kind: str
    rel_path: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 路径
    def rel_path_for(self, sha: str) -> str:
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError(f"非法 sha256: {sha!r}")
        return f"{sha[:2]}/{sha[2:4]}/{sha}.z"

    def path_for(self, sha: str, codec: str = CODEC_ZLIB) -> Path:
        rel = self.rel_path_for(sha)
        if codec == CODEC_NONE:
            rel = rel[:-2] + ".raw"
        return self.root / rel

    def exists(self, sha: str, codec: str = CODEC_ZLIB) -> bool:
        return self.path_for(sha, codec).is_file()

    # ------------------------------------------------------------------ 写入
    def put_bytes(self, data: bytes, kind: str) -> BlobMeta:
        sha = sha256_bytes(data)
        comp = zlib.compress(data, 6)
        if len(comp) < len(data):
            payload, codec = comp, CODEC_ZLIB
        else:
            payload, codec = data, CODEC_NONE
        rel = self.rel_path_for(sha)
        final = self.root / rel
        if final.is_file() and final.stat().st_size == len(payload):
            return BlobMeta(sha, len(data), len(payload), codec, kind, rel)
        self._atomic_write(final, payload)
        return BlobMeta(sha, len(data), len(payload), codec, kind, rel)

    def put_file(self, path: str | os.PathLike[str], kind: str) -> BlobMeta:
        """流式压缩，内存占用与文件大小无关。"""
        src = Path(path)
        raw_bytes = 0
        stored = 0
        digest = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(prefix=".blob-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as out, open(src, "rb") as fh:
                comp = zlib.compressobj(6)
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    raw_bytes += len(chunk)
                    part = comp.compress(chunk)
                    if part:
                        out.write(part)
                        stored += len(part)
                tail = comp.flush()
                if tail:
                    out.write(tail)
                    stored += len(tail)
            sha = digest.hexdigest()
            rel = self.rel_path_for(sha)
            final = self.root / rel
            if stored >= raw_bytes:
                # 不可压缩（如已压缩的 NPZ）：改为存原文件，避免白费 CPU。
                final_raw = self.root / (rel[:-2] + ".raw")
                if not final_raw.is_file():
                    final_raw.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, final_raw)
                codec = CODEC_NONE
                stored = raw_bytes
                rel = str(final_raw.relative_to(self.root))
                final = final_raw
            else:
                codec = CODEC_ZLIB
            if not final.is_file():
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp_name, final)
                tmp_name = ""
            return BlobMeta(sha, raw_bytes, stored, codec, kind, rel)
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _atomic_write(self, final: Path, payload: bytes) -> None:
        final.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".blob-", dir=final.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, final)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ------------------------------------------------------------------ 读取
    def get_bytes(self, sha: str, codec: str = CODEC_ZLIB) -> bytes:
        path = self.path_for(sha, codec)
        if not path.is_file():
            raise FileNotFoundError(f"blob 缺失: {sha} ({path})")
        data = path.read_bytes()
        if codec == CODEC_ZLIB:
            return zlib.decompress(data)
        if codec == CODEC_NONE:
            return data
        raise ValueError(f"未知 codec: {codec}")

    def verify(self, sha: str, codec: str = CODEC_ZLIB) -> bool:
        try:
            return sha256_bytes(self.get_bytes(sha, codec)) == sha
        except (OSError, zlib.error):
            return False

    # ------------------------------------------------------------------ 维护
    def register(self, conn: sqlite3.Connection, meta: BlobMeta) -> None:
        conn.execute(
            """
            INSERT INTO blobs(sha256, raw_bytes, stored_bytes, codec, kind, rel_path, created_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(sha256) DO UPDATE SET
                raw_bytes=excluded.raw_bytes,
                stored_bytes=excluded.stored_bytes,
                codec=excluded.codec,
                kind=excluded.kind,
                rel_path=excluded.rel_path
            """,
            (
                meta.sha256,
                meta.raw_bytes,
                meta.stored_bytes,
                meta.codec,
                meta.kind,
                meta.rel_path,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )

    def referenced(self, conn: sqlite3.Connection) -> set[str]:
        out: set[str] = set()
        for row in conn.execute("SELECT sha256 FROM blobs"):
            out.add(row[0])
        return out

    def gc(self, conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
        """删除没有任何表引用的 blob（文件 + 元数据行）。"""
        used: set[str] = set()
        for sql in (
            "SELECT header_sha256 FROM frames WHERE header_sha256 IS NOT NULL",
            "SELECT sha256 FROM cutouts",
        ):
            try:
                used.update(r[0] for r in conn.execute(sql))
            except sqlite3.OperationalError:
                pass

        rows = list(conn.execute("SELECT sha256, rel_path, stored_bytes FROM blobs"))
        removed = 0
        freed = 0
        for sha, rel, size in rows:
            if sha in used:
                continue
            path = self.root / rel
            if not dry_run:
                if path.is_file():
                    path.unlink()
                conn.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
            removed += 1
            freed += int(size or 0)
        if not dry_run:
            conn.commit()
            self._remove_empty_dirs()
        return {"removed": removed, "freed_bytes": freed, "referenced": len(used), "dry_run": dry_run}

    def _remove_empty_dirs(self) -> None:
        for p in sorted(self.root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_dir():
                try:
                    p.rmdir()
                except OSError:
                    pass

    def stats(self, conn: sqlite3.Connection) -> dict:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(raw_bytes),0), COALESCE(SUM(stored_bytes),0) FROM blobs"
        ).fetchone()
        by_kind = [
            {"kind": r[0], "blobs": r[1], "raw_bytes": r[2], "stored_bytes": r[3]}
            for r in conn.execute(
                "SELECT kind, COUNT(*), SUM(raw_bytes), SUM(stored_bytes) FROM blobs GROUP BY kind ORDER BY 2 DESC"
            )
        ]
        raw = int(row[1] or 0)
        stored = int(row[2] or 0)
        return {
            "blobs": int(row[0] or 0),
            "raw_bytes": raw,
            "stored_bytes": stored,
            "ratio": (stored / raw) if raw else None,
            "by_kind": by_kind,
        }

    def new_temp(self) -> Path:
        return self.root / f".tmp-{uuid.uuid4().hex}"
