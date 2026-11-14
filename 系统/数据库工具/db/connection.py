"""连接、PRAGMA 与事务辅助。

所有 SQLite 访问都走这里，保证：
- WAL 模式（读写不互锁，HTTP 读 + CLI 导入可并行）；
- 外键打开；
- 批量导入时显式事务，而不是一条 INSERT 一个事务；
- 只读连接不能意外写库。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect(db_path: str | os.PathLike[str], *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve()
    if readonly:
        if not path.exists():
            raise FileNotFoundError(f"数据库不存在: {path}")
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -65536")       # 64 MB 页缓存
    conn.execute("PRAGMA mmap_size = 268435456")     # 256 MB 只读映射
    if readonly:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint = 2000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """显式写事务；异常回滚。不能在已打开的事务里嵌套使用。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    else:
        conn.execute("COMMIT")


def db_disk_bytes(db_path: str | os.PathLike[str]) -> int:
    """主库 + WAL + SHM 的磁盘占用。"""
    path = Path(db_path)
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            total += p.stat().st_size
    return total


def sqlite_version() -> str:
    return sqlite3.sqlite_version
