"""SQLite 模式定义与迁移。

设计原则
--------
1. 元数据与像素分开：SQLite 只存可检索的数值/文本，原始 FITS 头与切割产品
   进内容寻址 Blob 仓库（见 blobs.py）。
2. 一个“帧”= 一次曝光的一个探测器。同一个 obs_id 的 D1–D6 共享观测级字段，
   拆到 observations 表避免 6 倍冗余。
3. release 不是帧的主键：同一 obs_id 可能同时存在于 qr3/dr1（不同处理版本）。
   frames 保留规范行（唯一 obs_id+detector），frame_releases 记录所有处理版本，
   需要严格时序光度时可按 release 过滤。
4. 天空检索用赤纬带 zone + RA 复合索引；先矩形粗筛，再在 Python 里做精确
   大圆距离过滤。这是 SDSS 用了二十年的 zone 算法，规模到 10^7 行仍然有效。
"""
from __future__ import annotations

import sqlite3

from .config import SCHEMA_VERSION

DDL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    code    TEXT PRIMARY KEY,
    label   TEXT NOT NULL,
    status  TEXT NOT NULL DEFAULT 'active',
    notes   TEXT
);

CREATE TABLE IF NOT EXISTS weeks (
    release    TEXT NOT NULL REFERENCES releases(code),
    week       TEXT NOT NULL,
    version    TEXT,
    mjd_obs    REAL,
    mjd_min    REAL,
    mjd_max    REAL,
    n_frames   INTEGER NOT NULL DEFAULT 0,
    first_key  TEXT,
    first_obsid TEXT,
    first_detector INTEGER,
    first_date_obs TEXT,
    elon       REAL,
    elat       REAL,
    ra         REAL,
    dec        REAL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (release, week)
);
CREATE INDEX IF NOT EXISTS idx_weeks_week ON weeks(week);
CREATE INDEX IF NOT EXISTS idx_weeks_mjd  ON weeks(mjd_min, mjd_max);

CREATE TABLE IF NOT EXISTS observations (
    obs_id        TEXT PRIMARY KEY,
    week          TEXT NOT NULL,
    field_no      INTEGER,
    repeat_no     INTEGER,
    release       TEXT,
    release_source TEXT,
    mjd           REAL,
    date_obs      TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_week ON observations(week);
CREATE INDEX IF NOT EXISTS idx_obs_mjd  ON observations(mjd);

CREATE TABLE IF NOT EXISTS blobs (
    sha256      TEXT PRIMARY KEY,
    raw_bytes   INTEGER NOT NULL,
    stored_bytes INTEGER NOT NULL,
    codec       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    rel_path    TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    last_verified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_blobs_kind ON blobs(kind);

CREATE TABLE IF NOT EXISTS frames (
    frame_id     INTEGER PRIMARY KEY,
    obs_id       TEXT NOT NULL REFERENCES observations(obs_id) ON DELETE CASCADE,
    detector     INTEGER NOT NULL,
    release      TEXT,
    week         TEXT NOT NULL,
    ra           REAL NOT NULL,
    dec          REAL NOT NULL,
    mjd          REAL,
    date_obs     TEXT,
    xposure      REAL,
    sgt_lat      REAL,
    sgt_lon      REAL,
    elon         REAL,
    elat         REAL,
    naxis1       INTEGER,
    naxis2       INTEGER,
    pixel_scale_arcsec REAL,
    crpix1       REAL,
    crpix2       REAL,
    sip_order    INTEGER,
    psf_fwhm_arcsec REAL,
    l2dqa        TEXT,
    header_sha256 TEXT REFERENCES blobs(sha256),
    source_path  TEXT,
    zone         INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (obs_id, detector)
);
CREATE INDEX IF NOT EXISTS idx_frames_zone_ra ON frames(zone, ra);
CREATE INDEX IF NOT EXISTS idx_frames_dec     ON frames(dec);
CREATE INDEX IF NOT EXISTS idx_frames_mjd     ON frames(mjd);
CREATE INDEX IF NOT EXISTS idx_frames_week    ON frames(week);
CREATE INDEX IF NOT EXISTS idx_frames_det     ON frames(detector);
CREATE INDEX IF NOT EXISTS idx_frames_release ON frames(release);
CREATE INDEX IF NOT EXISTS idx_frames_mjd_det ON frames(mjd, detector);

CREATE TABLE IF NOT EXISTS frame_releases (
    frame_id  INTEGER NOT NULL REFERENCES frames(frame_id) ON DELETE CASCADE,
    release   TEXT NOT NULL,
    procver   TEXT,
    source_key TEXT,
    slice_url  TEXT,
    PRIMARY KEY (frame_id, release)
);
CREATE INDEX IF NOT EXISTS idx_frame_releases_release ON frame_releases(release);

CREATE TABLE IF NOT EXISTS targets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    name_zh     TEXT,
    official    INTEGER NOT NULL DEFAULT 0,
    ra          REAL NOT NULL,
    dec         REAL NOT NULL,
    type        TEXT,
    expected    TEXT,
    truth_json  TEXT,
    note        TEXT,
    aliases     TEXT,
    extra_json  TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_epochs (
    id          INTEGER PRIMARY KEY,
    target_id   TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    frame_id    INTEGER REFERENCES frames(frame_id) ON DELETE SET NULL,
    release     TEXT,
    week        TEXT,
    obs_id      TEXT NOT NULL,
    detector    INTEGER,
    mjd         REAL,
    date_obs    TEXT,
    sep_deg     REAL,
    rank        INTEGER NOT NULL DEFAULT 0,
    fits_key    TEXT,
    slice_url   TEXT,
    source      TEXT NOT NULL DEFAULT 'epoch_catalog',
    created_at  TEXT NOT NULL,
    UNIQUE (target_id, release, obs_id, detector)
);
CREATE INDEX IF NOT EXISTS idx_epochs_target ON target_epochs(target_id, mjd);
CREATE INDEX IF NOT EXISTS idx_epochs_frame  ON target_epochs(frame_id);

CREATE TABLE IF NOT EXISTS cutouts (
    cutout_id   TEXT PRIMARY KEY,
    frame_id    INTEGER REFERENCES frames(frame_id) ON DELETE SET NULL,
    obs_id      TEXT,
    detector    INTEGER,
    sha256      TEXT NOT NULL REFERENCES blobs(sha256),
    kind        TEXT NOT NULL,
    ra          REAL,
    dec         REAL,
    fov_deg     REAL,
    mjd         REAL,
    n_pix       INTEGER,
    shape       TEXT,
    meta_json   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cutouts_frame ON cutouts(frame_id);
CREATE INDEX IF NOT EXISTS idx_cutouts_sha   ON cutouts(sha256);

CREATE TABLE IF NOT EXISTS fields_snapshot (
    seq      INTEGER PRIMARY KEY,
    obsid    TEXT NOT NULL,
    detector INTEGER NOT NULL,
    ra       REAL NOT NULL,
    dec      REAL NOT NULL,
    mjd      REAL,
    lat      REAL,
    lon      REAL,
    elon     REAL,
    elat     REAL,
    xposure  REAL
);
CREATE INDEX IF NOT EXISTS idx_fields_snapshot_mjd ON fields_snapshot(mjd, seq);

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    files_seen    INTEGER NOT NULL DEFAULT 0,
    files_ingested INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_updated  INTEGER NOT NULL DEFAULT 0,
    bytes_read    INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    stats_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_started ON ingest_runs(started_at);

CREATE VIEW IF NOT EXISTS v_frames AS
SELECT
    f.frame_id,
    f.obs_id,
    f.detector,
    COALESCE(f.release, o.release) AS release,
    f.week,
    f.ra,
    f.dec,
    f.mjd,
    f.date_obs,
    f.xposure,
    f.sgt_lat,
    f.sgt_lon,
    f.elon,
    f.elat,
    f.naxis1,
    f.naxis2,
    f.pixel_scale_arcsec,
    f.psf_fwhm_arcsec,
    f.l2dqa,
    f.header_sha256,
    f.source_path,
    o.field_no,
    o.repeat_no,
    w.version,
    w.ra  AS week_ra,
    w.dec AS week_dec
FROM frames f
JOIN observations o ON o.obs_id = f.obs_id
LEFT JOIN weeks w
       ON w.week = f.week
      AND w.release = COALESCE(f.release, o.release);

CREATE VIEW IF NOT EXISTS v_epochs AS
SELECT
    e.id,
    e.target_id,
    t.name        AS target_name,
    t.ra          AS target_ra,
    t.dec         AS target_dec,
    e.frame_id,
    e.release,
    e.week,
    e.obs_id,
    e.detector,
    e.mjd,
    e.date_obs,
    e.sep_deg,
    e.rank,
    e.fits_key,
    e.slice_url,
    e.source,
    w.version,
    f.ra          AS frame_ra,
    f.dec         AS frame_dec,
    f.header_sha256,
    f.sgt_lat,
    f.sgt_lon
FROM target_epochs e
JOIN targets t ON t.id = e.target_id
LEFT JOIN frames f ON f.frame_id = e.frame_id
LEFT JOIN weeks w
       ON w.week = e.week
      AND w.release = e.release;
"""


def apply_schema(conn: sqlite3.Connection) -> None:
    """建表并写入版本信息；幂等。"""
    conn.executescript(DDL)
    migrate(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def check_schema(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    """向后兼容旧库：只做可空列/索引的增量变更。"""
    wcols = _table_columns(conn, "weeks")
    if "mjd_obs" not in wcols:
        conn.execute("ALTER TABLE weeks ADD COLUMN mjd_obs REAL")
    if "first_detector" not in wcols:
        conn.execute("ALTER TABLE weeks ADD COLUMN first_detector INTEGER")
    if "first_date_obs" not in wcols:
        conn.execute("ALTER TABLE weeks ADD COLUMN first_date_obs TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fields_snapshot (
             seq INTEGER PRIMARY KEY, obsid TEXT NOT NULL, detector INTEGER NOT NULL,
             ra REAL NOT NULL, dec REAL NOT NULL, mjd REAL, lat REAL, lon REAL,
             elon REAL, elat REAL, xposure REAL)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fields_snapshot_mjd ON fields_snapshot(mjd, seq)")
    tcols = _table_columns(conn, "targets")
    if "extra_json" not in tcols:
        conn.execute("ALTER TABLE targets ADD COLUMN extra_json TEXT")
    cols = _table_columns(conn, "cutouts")
    if "obs_id" not in cols:
        conn.execute("ALTER TABLE cutouts ADD COLUMN obs_id TEXT")
    if "detector" not in cols:
        conn.execute("ALTER TABLE cutouts ADD COLUMN detector INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cutouts_obs ON cutouts(obs_id, detector)")
    conn.commit()
