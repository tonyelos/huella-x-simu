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

CREATE TABLE blobs (
    sha256       TEXT PRIMARY KEY,
    raw_bytes    INTEGER NOT NULL,
    stored_bytes INTEGER NOT NULL,
    codec        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    rel_path     TEXT NOT NULL
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


# --------------------------------------------------------------------------- P1A pixel fixtures
def _wcs_header(ra: float, dec: float, naxis: int = 96, obsid: str = "2026W50_1A_0001_1",
                detector: int = 1, mjd: float = 61340.0, release: str = "dr1") -> dict:
    scale = 6.15 / 3600.0  # deg/pixel
    return {
        "CRVAL1": ra,
        "CRVAL2": dec,
        "CRPIX1": naxis / 2.0 + 0.5,
        "CRPIX2": naxis / 2.0 + 0.5,
        "PC1_1": scale,
        "PC1_2": 0.0,
        "PC2_1": 0.0,
        "PC2_2": -scale,
        "CDELT1": 1.0,
        "CDELT2": 1.0,
        "CTYPE1": "RA---TAN-SIP",
        "CTYPE2": "DEC--TAN-SIP",
        "NAXIS1": naxis,
        "NAXIS2": naxis,
        "OBSID": obsid,
        "DETECTOR": detector,
        "MJD-OBS": mjd,
        "DATE-OBS": "2026-11-15T00:00:00.000",
        "BUNIT": "MJy/sr",
        "RELEASE": release,
    }


def _pixel_arrays(naxis: int = 96):
    import numpy as np

    yy, xx = np.mgrid[0:naxis, 0:naxis]
    image = np.full((naxis, naxis), 1.0, dtype=np.float32)
    for cx, cy, amp, sigma in ((naxis * 0.52, naxis * 0.51, 60.0, 1.4),
                               (naxis * 0.30, naxis * 0.66, 25.0, 1.5)):
        image += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma * sigma))
    rng = np.random.default_rng(20261115)
    image += rng.normal(0.0, 0.04, image.shape).astype(np.float32)
    variance = np.full(image.shape, 0.04 ** 2, dtype=np.float32)
    flags = np.zeros(image.shape, dtype=np.int32)
    psf = np.zeros((33, 33), dtype=np.float64)
    py, px = np.mgrid[0:33, 0:33]
    psf = np.exp(-((px - 16) ** 2 + (py - 16) ** 2) / (2 * 1.2 ** 2))
    psf /= psf.sum()
    return image.astype(np.float32), variance.astype(np.float32), flags, psf


def build_npz_fixture(path: str | Path, *, ra: float = 120.0, dec: float = 20.0,
                      obsid: str = "2026W50_1A_0001_1", detector: int = 1,
                      release: str = "dr1") -> Path:
    import json
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image, variance, flags, psf = _pixel_arrays()
    header = _wcs_header(ra, dec, obsid=obsid, detector=detector, release=release)
    np.savez_compressed(
        path,
        image=image,
        variance=variance,
        flags=flags,
        header=json.dumps(header),
        psf=psf,
        mjd=61340.0,
        obsid=obsid,
        detector=str(detector),
        date_obs="2026-11-15T00:00:00.000",
        release=release,
        sep_deg=0.0,
        wave=1.6,
    )
    return path


def _fits_card(key: str, value, comment: str = "") -> bytes:
    if isinstance(value, str):
        raw = f"'{value:<8}'"
        card = f"{key:<8}= {raw:<20}"
    elif isinstance(value, bool):
        card = f"{key:<8}= {'T' if value else 'F':>20}"
    elif isinstance(value, (int, float)):
        card = f"{key:<8}= {value:>20}"
    else:
        card = f"{key:<8}= {str(value):>20}"
    if comment:
        card = f"{card} / {comment}"
    return card.ljust(80)[:80].encode("latin-1")


def _fits_header(cards: list[bytes]) -> bytes:
    body = b"".join(cards)
    body += b"END".ljust(80)
    body = body.ljust(((len(body) + 2879) // 2880) * 2880, b" ")
    return body


def _fits_hdu(cards: list[bytes], data: bytes | None = None) -> bytes:
    out = _fits_header(cards)
    if data is not None:
        out += data
        out = out.ljust(((len(out) + 2879) // 2880) * 2880, b"\x00")
    return out


def build_fits_fixture(path: str | Path, *, ra: float = 121.0, dec: float = 21.0,
                       obsid: str = "2026W50_1A_0002_1", detector: int = 1,
                       release: str = "dr1") -> Path:
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image, variance, flags, _psf = _pixel_arrays()
    header = _wcs_header(ra, dec, obsid=obsid, detector=detector, release=release)
    primary = _fits_header([
        _fits_card("SIMPLE", True, "conforms to FITS"),
        _fits_card("BITPIX", 8),
        _fits_card("NAXIS", 0),
        _fits_card("EXTEND", True),
    ])
    image_cards = [
        _fits_card("XTENSION", "IMAGE"),
        _fits_card("BITPIX", -32),
        _fits_card("NAXIS", 2),
        _fits_card("NAXIS1", header["NAXIS1"]),
        _fits_card("NAXIS2", header["NAXIS2"]),
        _fits_card("PCOUNT", 0),
        _fits_card("GCOUNT", 1),
        _fits_card("EXTNAME", "IMAGE"),
    ]
    for key in ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "PC1_1", "PC1_2",
                "PC2_1", "PC2_2", "CDELT1", "CDELT2", "CTYPE1", "CTYPE2",
                "OBSID", "DETECTOR", "MJD-OBS", "DATE-OBS", "BUNIT", "RELEASE"):
        image_cards.append(_fits_card(key, header[key]))
    def plane_cards(name: str, bitpix: int) -> list[bytes]:
        return [
            _fits_card("XTENSION", "IMAGE"),
            _fits_card("BITPIX", bitpix),
            _fits_card("NAXIS", 2),
            _fits_card("NAXIS1", header["NAXIS1"]),
            _fits_card("NAXIS2", header["NAXIS2"]),
            _fits_card("PCOUNT", 0),
            _fits_card("GCOUNT", 1),
            _fits_card("EXTNAME", name),
        ]
    with path.open("wb") as fh:
        fh.write(primary)
        fh.write(_fits_hdu(image_cards, np.asarray(image, dtype=">f4").tobytes()))
        fh.write(_fits_hdu(plane_cards("VARIANCE", -32), np.asarray(variance, dtype=">f4").tobytes()))
        fh.write(_fits_hdu(plane_cards("FLAGS", 32), np.asarray(flags, dtype=">i4").tobytes()))
    return path
