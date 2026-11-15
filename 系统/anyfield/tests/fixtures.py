"""Temporary SQLite fixture for AnyField C-MVP tests.

The fixture is intentionally small and self-contained: tests must not depend
on the production ``catalog.sqlite`` or on a running DB service at 8797.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE weeks (
    release    TEXT NOT NULL,
    week       TEXT NOT NULL,
    version    TEXT,
    mjd_min    REAL,
    mjd_max    REAL,
    PRIMARY KEY (release, week)
);

CREATE TABLE observations (
    obs_id     TEXT PRIMARY KEY,
    week       TEXT NOT NULL,
    field_no   INTEGER,
    repeat_no  INTEGER,
    release    TEXT,
    mjd        REAL
);

CREATE TABLE frames (
    frame_id   INTEGER PRIMARY KEY,
    obs_id     TEXT NOT NULL,
    detector   INTEGER NOT NULL,
    release    TEXT,
    week       TEXT NOT NULL,
    ra         REAL NOT NULL,
    dec        REAL NOT NULL,
    mjd        REAL,
    date_obs   TEXT,
    UNIQUE (obs_id, detector)
);

CREATE TABLE targets (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    ra       REAL NOT NULL,
    dec      REAL NOT NULL,
    official INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE target_epochs (
    id          INTEGER PRIMARY KEY,
    target_id   TEXT NOT NULL,
    frame_id    INTEGER,
    release     TEXT,
    week        TEXT,
    obs_id      TEXT NOT NULL,
    detector    INTEGER,
    mjd         REAL,
    date_obs    TEXT,
    sep_deg     REAL,
    fits_key    TEXT,
    slice_url   TEXT,
    UNIQUE (target_id, release, obs_id, detector)
);
"""

# One real-like Barnard epoch (two target rows, one frame each) plus a second
# epoch, and one unrelated field with a local frame and no target.
TARGETS = [
    ("barnard", "Barnard's Star", 269.452077, 4.693365, 1),
]

WEEKS = [
    ("dr1", "2025W35_2A", "l2b-v27-2026-231", 60916.5, 60916.7),
    ("dr1", "2026W12_2A", "l2b-v27-2026-235", 61120.2, 61120.3),
    ("dr1", "2025W40_1A", "l2b-v27-2026-250", 60950.0, 60950.1),
]

OBSERVATIONS = [
    ("2025W35_2A_0221_1", "2025W35_2A", 221, 1, "dr1", 60916.656061887),
    ("2026W12_2A_0324_1", "2026W12_2A", 324, 1, "dr1", 61120.278598588),
    ("2025W40_1A_0001_1", "2025W40_1A", 1, 1, "dr1", 60950.0),
]

FRAMES = [
    (1, "2025W35_2A_0221_1", 2, "dr1", "2025W35_2A", 269.230983021, 4.6312446434,
     60916.656061887, "2025-08-29T15:44:43.747"),
    (2, "2026W12_2A_0324_1", 3, "dr1", "2026W12_2A", 268.455431417, 6.25585574796,
     61120.278598588, "2026-03-21T06:41:10.918"),
    (3, "2025W40_1A_0001_1", 1, "dr1", "2025W40_1A", 100.0, 10.0,
     60950.0, "2025-10-02T00:00:00.000"),
]

TARGET_EPOCHS = [
    (
        1, "barnard", 1, "dr1", "2025W35_2A", "2025W35_2A_0221_1", 2,
        60916.656061887, "2025-08-29T15:44:43.747", 0.229,
        "dr1/level2/2025W35_2A/l2b-v27-2026-231/2/"
        "level2_2025W35_2A_0221_1D2_spx_l2b-v27-2026-231.fits",
        None,
    ),
    (
        2, "barnard", 2, "dr1", "2026W12_2A", "2026W12_2A_0324_1", 3,
        61120.278598588, "2026-03-21T06:41:10.918", 1.851,
        "dr1/level2/2026W12_2A/l2b-v27-2026-235/3/"
        "level2_2026W12_2A_0324_1D3_spx_l2b-v27-2026-235.fits",
        None,
    ),
]

BARNARD_RA = 269.452077
BARNARD_DEC = 4.693365
UNKNOWN_RA = 0.0
UNKNOWN_DEC = 0.0
LOCAL_FRAME_RA = 100.0
LOCAL_FRAME_DEC = 10.0


def build_fixture(db_path: str | Path) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '6')")
        conn.executemany("INSERT INTO targets VALUES (?,?,?,?,?)", TARGETS)
        conn.executemany("INSERT INTO weeks VALUES (?,?,?,?,?)", WEEKS)
        conn.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?)", OBSERVATIONS)
        conn.executemany("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?)", FRAMES)
        conn.executemany("INSERT INTO target_epochs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", TARGET_EPOCHS)
        conn.commit()
    finally:
        conn.close()
    return path
