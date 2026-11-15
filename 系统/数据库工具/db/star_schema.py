"""星表服务的 SQLite schema（独立于 SPHEREx 生产库）。

三张核心表对应产品路线图 D1：

    catalog_release  一次星表快照：目录/版本/参考历元/导入时间/许可/来源
    catalog_source   星表源：位置、自行、视差、亮度、2MASS/WISE、HEALPix
    catalog_match    匹配产物：分析 ID、源、角距、自行残差、歧义、置信度

数据库是“可重建缓存”，不是原始数据归档：删掉 `var/star_catalog.sqlite` 后，
用 `tools/import_gaia.py --rebuild`（联网）或 `--fixture`（离线）可重建。
版本信息在 `catalog_release` 与 `star_meta.schema_version` 里，不藏在代码里。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .connection import connect, utcnow
from .star_config import STAR_SCHEMA_VERSION

STAR_TABLES_DDL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS star_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_release (
    release_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    release_key     TEXT NOT NULL UNIQUE,
    catalog         TEXT NOT NULL,
    version         TEXT NOT NULL,
    epoch_ref       TEXT NOT NULL,
    epoch_mjd       REAL NOT NULL,
    imported_at     TEXT NOT NULL,
    license         TEXT NOT NULL,
    citation        TEXT,
    source_url      TEXT NOT NULL,
    provenance      TEXT NOT NULL DEFAULT 'import',
    cache_radius_deg REAL,
    mag_limit       REAL,
    n_sources       INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    UNIQUE (catalog, version)
);

CREATE TABLE IF NOT EXISTS catalog_field (
    release_id  INTEGER NOT NULL REFERENCES catalog_release(release_id) ON DELETE CASCADE,
    field_id    TEXT NOT NULL,
    field_name  TEXT,
    ra          REAL NOT NULL,
    dec         REAL NOT NULL,
    radius_deg  REAL NOT NULL,
    n_sources   INTEGER NOT NULL DEFAULT 0,
    provenance  TEXT NOT NULL DEFAULT 'import',
    photometry_json TEXT,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (release_id, field_id)
);
CREATE INDEX IF NOT EXISTS idx_catalog_field_pos ON catalog_field(release_id, ra, dec);

CREATE TABLE IF NOT EXISTS catalog_source (
    source_id       TEXT PRIMARY KEY,
    release_id      INTEGER NOT NULL REFERENCES catalog_release(release_id) ON DELETE CASCADE,
    catalog_id      TEXT NOT NULL,
    ra              REAL NOT NULL,
    dec             REAL NOT NULL,
    pmra            REAL,
    pmdec           REAL,
    parallax        REAL,
    ra_error        REAL,
    dec_error       REAL,
    pmra_error      REAL,
    pmdec_error     REAL,
    parallax_error  REAL,
    ruwe            REAL,
    astrometric_excess_noise REAL,
    phot_variable_flag TEXT,
    gmag            REAL,
    bp_rp           REAL,
    jmag            REAL,
    hmag            REAL,
    kmag            REAL,
    w1              REAL,
    w2              REAL,
    twomass_id      TEXT,
    wise_id         TEXT,
    epoch_ref       REAL,
    healpix         INTEGER NOT NULL,
    object_type     TEXT,
    provenance      TEXT NOT NULL DEFAULT 'import',
    created_at      TEXT NOT NULL,
    UNIQUE (release_id, catalog_id)
);
CREATE INDEX IF NOT EXISTS idx_catalog_source_pos ON catalog_source(release_id, dec, ra);
CREATE INDEX IF NOT EXISTS idx_catalog_source_hp ON catalog_source(release_id, healpix);
CREATE INDEX IF NOT EXISTS idx_catalog_source_pm ON catalog_source(release_id, pmra, pmdec);
CREATE INDEX IF NOT EXISTS idx_catalog_source_2mass ON catalog_source(twomass_id);
CREATE INDEX IF NOT EXISTS idx_catalog_source_wise ON catalog_source(wise_id);

CREATE TABLE IF NOT EXISTS catalog_match (
    match_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  TEXT NOT NULL,
    detection_id TEXT,
    release_id   INTEGER NOT NULL REFERENCES catalog_release(release_id) ON DELETE CASCADE,
    source_id    TEXT NOT NULL REFERENCES catalog_source(source_id) ON DELETE CASCADE,
    sep_arcsec   REAL NOT NULL,
    pm_residual  REAL,
    pa_residual  REAL,
    ambiguity    INTEGER NOT NULL DEFAULT 0,
    confidence   REAL,
    detection_ra REAL,
    detection_dec REAL,
    detection_mjd REAL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalog_match_analysis ON catalog_match(analysis_id);
CREATE INDEX IF NOT EXISTS idx_catalog_match_source ON catalog_match(source_id);
CREATE INDEX IF NOT EXISTS idx_catalog_match_release ON catalog_match(release_id);

CREATE TABLE IF NOT EXISTS catalog_import_run (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id  INTEGER REFERENCES catalog_release(release_id) ON DELETE SET NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    mode        TEXT,
    n_fields    INTEGER NOT NULL DEFAULT 0,
    n_sources   INTEGER NOT NULL DEFAULT 0,
    details_json TEXT,
    errors_json TEXT
);

"""

STAR_VIEWS_DDL = r"""
-- 对外字段名用 SQL 标准别名；列名 twomass_id/wise_id 只是 SQLite 的合法标识符。
DROP VIEW IF EXISTS v_catalog_source;
DROP VIEW IF EXISTS v_catalog_release;
CREATE VIEW IF NOT EXISTS v_catalog_source AS
SELECT
    s.source_id,
    s.release_id,
    s.catalog_id,
    s.ra,
    s.dec,
    s.pmra,
    s.pmdec,
    s.parallax,
    s.ra_error,
    s.dec_error,
    s.pmra_error,
    s.pmdec_error,
    s.parallax_error,
    s.ruwe,
    s.astrometric_excess_noise,
    s.phot_variable_flag,
    s.gmag,
    s.bp_rp,
    s.jmag,
    s.hmag,
    s.kmag,
    s.w1,
    s.w2,
    s.twomass_id AS "2mass",
    s.wise_id    AS wise,
    s.epoch_ref,
    s.healpix,
    s.object_type,
    s.provenance,
    s.created_at
FROM catalog_source s;

CREATE VIEW IF NOT EXISTS v_catalog_release AS
SELECT
    r.release_id,
    r.release_key AS release,
    r.catalog,
    r.version,
    r.epoch_ref,
    r.epoch_mjd,
    r.imported_at,
    r.license,
    r.citation,
    r.source_url,
    r.provenance,
    r.cache_radius_deg,
    r.mag_limit,
    r.n_sources,
    COUNT(s.source_id) AS n_sources_actual
FROM catalog_release r
LEFT JOIN catalog_source s ON s.release_id = r.release_id
GROUP BY r.release_id;
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def migrate_star_schema(conn: sqlite3.Connection) -> None:
    """在旧 v1 库上做可空列迁移；只做 ADD COLUMN，不丢数据。"""
    source_cols = _table_columns(conn, "catalog_source")
    for name, typ in (
        ("parallax_error", "REAL"),
        ("ruwe", "REAL"),
        ("astrometric_excess_noise", "REAL"),
        ("phot_variable_flag", "TEXT"),
    ):
        if name not in source_cols:
            conn.execute(f"ALTER TABLE catalog_source ADD COLUMN {name} {typ}")
    field_cols = _table_columns(conn, "catalog_field")
    if "photometry_json" not in field_cols:
        conn.execute("ALTER TABLE catalog_field ADD COLUMN photometry_json TEXT")
    release_cols = _table_columns(conn, "catalog_release")
    if "citation" not in release_cols:
        conn.execute("ALTER TABLE catalog_release ADD COLUMN citation TEXT")
    conn.commit()


def apply_star_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(STAR_TABLES_DDL)
    migrate_star_schema(conn)
    conn.executescript(STAR_VIEWS_DDL)
    conn.execute(
        "INSERT INTO star_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(STAR_SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO star_meta(key, value) VALUES('updated_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (utcnow(),),
    )
    conn.commit()


def check_star_schema(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM star_meta WHERE key='schema_version'").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def init_star_db(db_path: str | Path, *, replace: bool = False) -> Path:
    """建一个空的星表库；`replace=True` 时删除旧库重建（只用于可重建缓存）。"""
    path = Path(db_path).expanduser().resolve()
    if replace:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(path) + suffix)
            if p.exists():
                p.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        apply_star_schema(conn)
    finally:
        conn.close()
    return path
