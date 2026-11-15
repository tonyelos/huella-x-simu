"""Offline fake S3 + IRSA cutout server for discovery/fetch tests."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fixtures import _fits_card, _fits_header, _wcs_header, build_fits_fixture


def build_header_blob(*, ra: float, dec: float, obsid: str, detector: int = 1) -> bytes:
    header = _wcs_header(ra, dec, obsid=obsid, detector=detector)
    primary = _fits_header([
        _fits_card("SIMPLE", True),
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
    return primary + _fits_header(image_cards)


class FakeS3:
    def __init__(
        self,
        *,
        tmp: Path,
        ra: float = 120.0,
        dec: float = 20.0,
        obsid: str = "2026W50_1A_0001_1",
        release: str = "dr1",
        week: str = "2026W50_1A",
        version: str = "l2b-v27-2026-999",
        cutout_data: bytes | None = None,
    ) -> None:
        self.ra = float(ra)
        self.dec = float(dec)
        self.obsid = obsid
        self.release = release
        self.week = week
        self.version = version
        self.header_data = build_header_blob(ra=ra, dec=dec, obsid=obsid, detector=1)
        if cutout_data is None:
            path = tmp / "fake_cutout.fits"
            build_fits_fixture(path, ra=ra, dec=dec, obsid=obsid, detector=1, release=release)
            cutout_data = path.read_bytes()
        self.cutout_data = cutout_data
        self.keys = {
            det: f"{release}/level2/{week}/{version}/{det}/level2_{obsid}D{det}_spx_{version}.fits"
            for det in range(1, 7)
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    query = parse_qs(parsed.query)
                    prefix = (query.get("prefix") or [""])[0]
                    keys = []
                    for det, key in outer.keys.items():
                        if prefix == f"{outer.release}/level2/{outer.week}/{outer.version}/{det}/":
                            keys.append(key)
                    xml = (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        "<ListBucketResult>"
                        + "".join(f"<Key>{k}</Key>" for k in keys)
                        + "</ListBucketResult>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml")
                    self.send_header("Content-Length", str(len(xml)))
                    self.end_headers()
                    self.wfile.write(xml)
                    return
                if parsed.path.startswith("/ibe/data/spherex/"):
                    data = outer.cutout_data
                elif parsed.path.startswith(f"/{outer.release}/level2/"):
                    data = outer.header_data
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/fits")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.quiet = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def build_week_index(path: str | Path, *, ra: float, dec: float,
                     release: str = "dr1", week: str = "2026W50_1A",
                     version: str = "l2b-v27-2026-999") -> Path:
    from discovery import find_epochs

    lon, _lat = find_epochs.eq2ecl(float(ra), float(dec))
    payload = {
        "generated_by": "tests/fake_s3.py",
        "weeks": [
            {
                "release": release,
                "week": week,
                "version": version,
                "elon": lon,
                "elat": 0.0,
                "ra": float(ra),
                "dec": float(dec),
            }
        ],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p
