"""合成 FITS 头夹具：不依赖 astropy / 真实数据，测试可以离线跑。"""
from __future__ import annotations

from pathlib import Path


def _card(key: str, value, comment: str = "") -> str:
    if isinstance(value, bool):
        val = "T" if value else "F"
    elif isinstance(value, str):
        val = "'" + value.replace("'", "''") + "'"
    else:
        val = str(value)
    text = f"{key:<8}= {val:>20}"
    if comment:
        text += f" / {comment}"
    return text[:80].ljust(80)


def _hierarch(key: str, value, comment: str = "") -> str:
    text = f"HIERARCH {key} = {value}"
    if comment:
        text += f" / {comment}"
    return text[:80].ljust(80)


def _block(cards: list[str]) -> bytes:
    body = "".join(cards)
    body += " " * (2880 - (len(body) % 2880) if len(body) % 2880 else 0)
    return body.encode("latin-1")


def make_header(
    path: str | Path,
    obs_id: str,
    detector: int,
    ra: float,
    dec: float,
    mjd: float,
    *,
    date_obs: str = "2026-01-01T00:00:00.000",
    sgt_lat: float = 10.0,
    sgt_lon: float = 20.0,
    elon: float = 30.0,
    elat: float = 40.0,
    naxis1: int = 64,
    naxis2: int = 64,
) -> Path:
    path = Path(path)
    primary = _block(
        [
            _card("SIMPLE", True),
            _card("BITPIX", 8),
            _card("NAXIS", 0),
            _card("EXTEND", True),
            "END".ljust(80),
        ]
    )
    image = _block(
        [
            _card("XTENSION", "IMAGE"),
            _card("BITPIX", -32),
            _card("NAXIS", 2),
            _card("NAXIS1", naxis1),
            _card("NAXIS2", naxis2),
            _card("PCOUNT", 0),
            _card("GCOUNT", 1),
            _card("EXTNAME", "IMAGE"),
            _card("BUNIT", "MJy / sr"),
            _card("DETECTOR", detector),
            _card("OBSID", obs_id),
            _card("CRPIX1", 32.5),
            _card("CRPIX2", 32.5),
            _card("PC1_1", 0.0017),
            _card("PC1_2", 0.0),
            _card("PC2_1", 0.0),
            _card("PC2_2", 0.0017),
            _card("CDELT1", 1.0),
            _card("CDELT2", 1.0),
            _card("CRVAL1", ra),
            _card("CRVAL2", dec),
            _card("MJD-OBS", mjd),
            _card("DATE-OBS", date_obs),
            _card("XPOSURE", 113.5),
            _card("PSF_FWHM", 7.5),
            _card("L2DQAFLG", "Pass"),
            _card("A_ORDER", 3),
            _hierarch("SGT_LAT_MIDPT", sgt_lat, "deg"),
            _hierarch("SGT_LON_MIDPT", sgt_lon, "deg"),
            _card("SPS_ELON", elon),
            _card("SPS_ELAT", elat),
            "END".ljust(80),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(primary + image)
    return path


def make_headers(root: str | Path, *, n_epochs: int = 6, start_mjd: float = 61000.0, step: float = 40.0) -> list[Path]:
    root = Path(root)
    paths = []
    for e in range(n_epochs):
        # 两个探测器同一次曝光；不同历元间有规律地位移 0.02°
        obs_id = f"2026W{e + 1:02d}_1A_{e + 1:04d}_1"
        for detector in (1, 2):
            p = root / f"{obs_id}_D{detector}.hdr"
            make_header(
                p,
                obs_id=obs_id,
                detector=detector,
                ra=150.0 + e * 0.02,
                dec=20.0 + e * 0.01,
                mjd=start_mjd + e * step,
                sgt_lat=10.0 + e,
                sgt_lon=20.0 + e,
                elon=30.0 + e,
                elat=40.0 + e,
            )
            paths.append(p)
    return paths


def build_small_db(root: str | Path, *, n_epochs: int = 6):
    """建一个 6 历元 × 2 探测器的小库，返回 (db_path, blob_root)。"""
    import json

    from db.ingest import Ingestor, init_db

    root = Path(root)
    db = root / "catalog.sqlite"
    blobs = root / "blobs"
    init_db(db, blobs)
    make_headers(root / "headers", n_epochs=n_epochs)

    targets = {
        "schema": "movescope/targets/v1",
        "targets": [
            {
                "id": "test_target",
                "name": "Test Target",
                "name_zh": "测试目标",
                "official": True,
                "ra": 150.05,
                "dec": 20.025,
                "type": "test",
                "expected": "L",
                "truth": {"mu_mas_yr": 1000.0},
                "note": "fixture",
                "coord_unc_arcsec": 3.0,
            }
        ],
    }
    (root / "targets.json").write_text(json.dumps(targets), encoding="utf-8")

    weeks = []
    for e in range(n_epochs):
        week = f"2026W{e + 1:02d}_1A"
        weeks.append(
            {
                "release": "dr1",
                "week": week,
                "version": "l2b-test-001",
                "first_key": f"dr1/level2/{week}/l2b-test-001/1/level2_{week}_{e + 1:04d}_1D1_spx_l2b-test-001.fits",
                "obsid": f"{week}_{e + 1:04d}_1",
                "detector": "1",
                "date_obs": "2026-01-01T00:00:00.000",
                "mjd_obs": 61000.0 + e * 40.0,
                "elon": 30.0 + e,
                "elat": 40.0 + e,
                "ra": 150.0 + e * 0.02,
                "dec": 20.0 + e * 0.01,
            }
        )
    (root / "weeks.json").write_text(json.dumps({"weeks": weeks}), encoding="utf-8")

    epochs = {"epochs": {"test_target": []}}
    for e in range(n_epochs):
        week = f"2026W{e + 1:02d}_1A"
        obs_id = f"{week}_{e + 1:04d}_1"
        epochs["epochs"]["test_target"].append(
            {
                "release": "dr1",
                "week": week,
                "version": "l2b-test-001",
                "field": e + 1,
                "detector": 1,
                "key": f"dr1/level2/{week}/l2b-test-001/1/level2_{week}_{e + 1:04d}_1D1_spx_l2b-test-001.fits",
                "obsid": obs_id,
                "mjd": 61000.0 + e * 40.0,
                "date_obs": "2026-01-01T00:00:00.000",
                "sep_deg": 0.01 + e * 0.001,
                "target_id": "test_target",
                "slice_url_002": f"https://example.test/{obs_id}",
            }
        )
    (root / "epochs.json").write_text(json.dumps(epochs), encoding="utf-8")

    with Ingestor(db, blobs, quiet=True) as ing:
        ing.ingest_week_index(root / "weeks.json")
        ing.ingest_targets(root / "targets.json")
        ing.ingest_headers(root / "headers")
        ing.ingest_epoch_catalog(root / "epochs.json")
        ing.backfill_releases()
    return db, blobs
