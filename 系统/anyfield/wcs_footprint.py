"""Pure WCS-footprint helpers for the AnyField P0 sidecar.

The P0 contract is deliberately narrow: for the 74 frames whose FITS header
blob is linked in ``frames.header_sha256``, parse the WCS cards from the blob
and build a four-corner spherical polygon. We do **not** apply SIP here (the
stored cards are CRVAL/CRPIX/CD/PC/NAXIS); the result is therefore labelled
``wcs_header`` with ``sip_applied=false``, not a full SIP-corrected footprint.
Frames without a header are never given a real footprint; callers fall back to
``center_approx`` explicitly.
"""
from __future__ import annotations

import math
from typing import Any

FOOTPRINT_METHOD_WCS = "wcs_header"
FOOTPRINT_METHOD_CENTER = "center_approx"


def _parse_card_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("'"):
        out: list[str] = []
        i = 1
        while i < len(value):
            ch = value[i]
            if ch == "'":
                if i + 1 < len(value) and value[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(ch)
            i += 1
        return "".join(out).rstrip()
    value = value.split("/", 1)[0].strip()
    low = value.lower()
    if low in ("t", "true"):
        return True
    if low in ("f", "false"):
        return False
    try:
        if any(c in value for c in ".eEdD"):
            return float(value.replace("D", "E").replace("d", "e"))
        return int(value)
    except ValueError:
        return value


def _parse_header_at(blob: bytes, start: int) -> tuple[dict[str, Any], int] | None:
    cards: dict[str, Any] = {}
    block = 0
    while True:
        offset = start + block * 2880
        chunk = blob[offset : offset + 2880]
        if len(chunk) < 2880:
            return None
        text = chunk.decode("latin-1")
        for j in range(0, 2880, 80):
            card = text[j : j + 80]
            key = card[:8].strip()
            if not key:
                continue
            if key == "END":
                return cards, start + (block + 1) * 2880
            if card[8:10] == "= ":
                cards[key] = _parse_card_value(card[10:80])
            elif key == "HIERARCH":
                body = card[9:].strip()
                if "=" in body:
                    k, v = body.split("=", 1)
                    cards["HIERARCH " + k.strip()] = _parse_card_value(v)
        block += 1


def parse_fits_headers(blob: bytes, max_headers: int = 8) -> list[dict[str, Any]]:
    """Parse consecutive FITS headers from a header blob (data skipped)."""
    out: list[dict[str, Any]] = []
    offset = 0
    while offset + 2880 <= len(blob) and len(out) < max_headers:
        parsed = _parse_header_at(blob, offset)
        if parsed is None:
            break
        header, next_offset = parsed
        out.append(header)
        naxis = int(header.get("NAXIS", 0) or 0)
        bitpix = int(header.get("BITPIX", 8) or 8)
        nbytes = 0
        if naxis > 0:
            n = 1
            for d in range(1, naxis + 1):
                n *= int(header.get(f"NAXIS{d}", 0) or 0)
            nbytes = abs(bitpix) // 8 * n
        offset = next_offset + (int(math.ceil(nbytes / 2880.0)) * 2880 if nbytes else 0)
    return out


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _header_value(header: dict[str, Any], *names: str) -> float | None:
    for name in names:
        if name in header:
            value = _as_float(header[name])
            if value is not None:
                return value
    return None


def _wrap_ra(ra: float) -> float:
    return ra % 360.0


def _pixel_to_world(header: dict[str, Any], x: float, y: float) -> tuple[float, float] | None:
    crval1 = _header_value(header, "CRVAL1")
    crval2 = _header_value(header, "CRVAL2")
    crpix1 = _header_value(header, "CRPIX1")
    crpix2 = _header_value(header, "CRPIX2")
    if None in (crval1, crval2, crpix1, crpix2):
        return None

    cd1_1 = _header_value(header, "CD1_1")
    cd1_2 = _header_value(header, "CD1_2")
    cd2_1 = _header_value(header, "CD2_1")
    cd2_2 = _header_value(header, "CD2_2")
    if None not in (cd1_1, cd1_2, cd2_1, cd2_2):
        matrix = (cd1_1, cd1_2, cd2_1, cd2_2)
    else:
        pc1_1 = _header_value(header, "PC1_1") or 1.0
        pc1_2 = _header_value(header, "PC1_2") or 0.0
        pc2_1 = _header_value(header, "PC2_1") or 0.0
        pc2_2 = _header_value(header, "PC2_2") or 1.0
        cdelt1 = _header_value(header, "CDELT1") or 1.0
        cdelt2 = _header_value(header, "CDELT2") or 1.0
        matrix = (pc1_1 * cdelt1, pc1_2 * cdelt1, pc2_1 * cdelt2, pc2_2 * cdelt2)
    if not all(math.isfinite(v) for v in matrix):
        return None

    u = float(x) - crpix1
    v = float(y) - crpix2
    # CD/PC*CDELT are in degrees per pixel; the gnomonic formulae below use
    # radians. This conversion is essential: without it a 3.5 deg frame would
    # be misinterpreted as a ~57 deg frame.
    xi = math.radians(matrix[0] * u + matrix[1] * v)
    eta = math.radians(matrix[2] * u + matrix[3] * v)
    rho = math.hypot(xi, eta)
    ra0 = math.radians(crval1)
    dec0 = math.radians(crval2)
    if rho == 0.0:
        return _wrap_ra(math.degrees(ra0)), math.degrees(dec0)
    c = math.atan(rho)
    sin_c, cos_c = math.sin(c), math.cos(c)
    dec = math.asin(
        max(-1.0, min(1.0, cos_c * math.sin(dec0) + eta * sin_c * math.cos(dec0) / rho))
    )
    ra = ra0 + math.atan2(
        xi * sin_c,
        rho * math.cos(dec0) * cos_c - eta * math.sin(dec0) * sin_c,
    )
    return _wrap_ra(math.degrees(ra)), math.degrees(dec)


def footprint_from_header(header: dict[str, Any]) -> dict[str, Any] | None:
    """Build a four-corner polygon from CRVAL/CRPIX/PC/CD/NAXIS cards."""
    naxis1 = _header_value(header, "NAXIS1")
    naxis2 = _header_value(header, "NAXIS2")
    crval1 = _header_value(header, "CRVAL1")
    crval2 = _header_value(header, "CRVAL2")
    if None in (naxis1, naxis2, crval1, crval2):
        return None
    if not (16 <= naxis1 <= 8192 and 16 <= naxis2 <= 8192):
        return None
    corners: list[list[float]] = []
    for x, y in (
        (0.5, 0.5),
        (naxis1 + 0.5, 0.5),
        (naxis1 + 0.5, naxis2 + 0.5),
        (0.5, naxis2 + 0.5),
    ):
        world = _pixel_to_world(header, x, y)
        if world is None:
            return None
        corners.append([round(world[0], 8), round(world[1], 8)])
    if len({tuple(c) for c in corners}) < 4:
        return None
    # Basic sanity: all corners finite and the polygon is not obviously tiny.
    lons = [math.radians(c[0]) for c in corners]
    lats = [math.radians(c[1]) for c in corners]
    xs = [math.cos(lats[i]) * math.cos(lons[i]) for i in range(len(corners))]
    ys = [math.cos(lats[i]) * math.sin(lons[i]) for i in range(len(corners))]
    zs = [math.sin(lats[i]) for i in range(len(corners))]
    cx, cy, cz = sum(xs), sum(ys), sum(zs)
    norm = math.sqrt(cx * cx + cy * cy + cz * cz)
    if norm <= 0:
        return None
    center_ra = _wrap_ra(math.degrees(math.atan2(cy, cx)))
    center_dec = math.degrees(math.asin(max(-1.0, min(1.0, cz / norm))))
    return {
        "method": FOOTPRINT_METHOD_WCS,
        "naxis1": int(round(naxis1)),
        "naxis2": int(round(naxis2)),
        "crval1": crval1,
        "crval2": crval2,
        "center_ra": round(center_ra, 8),
        "center_dec": round(center_dec, 8),
        "corners": corners,
        "polygon": corners,
        "sip_applied": False,
    }


def footprint_from_blob(blob: bytes) -> dict[str, Any] | None:
    """Find the first image header in a header blob and build its footprint."""
    for header in parse_fits_headers(blob):
        if "NAXIS1" in header and "NAXIS2" in header and "CRVAL1" in header:
            footprint = footprint_from_header(header)
            if footprint is not None:
                footprint["header_obsid"] = str(header.get("OBSID", "")).strip()
                footprint["header_detector"] = int(header.get("DETECTOR", 0) or 0)
                return footprint
    return None


def angular_sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    try:
        values = (float(ra1), float(dec1), float(ra2), float(dec2))
    except (TypeError, ValueError):
        return math.nan
    if not all(math.isfinite(v) for v in values):
        return math.nan
    a1, d1, a2, d2 = (math.radians(v) for v in values)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _unit(ra: float, dec: float) -> tuple[float, float, float]:
    lon, lat = math.radians(ra), math.radians(dec)
    return math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normalize(v):
    n = math.sqrt(_dot(v, v))
    if n <= 0:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def point_in_polygon(ra: float, dec: float, vertices: list[list[float]]) -> bool:
    """Spherical point-in-polygon via a local tangent plane + ray casting.

    The AnyField footprints are ~3.5 deg; the tangent plane at the vertex
    centroid is accurate well below the 6.15 arcsec pixel scale.
    """
    if len(vertices) < 3:
        return False
    vectors = [_unit(float(v[0]), float(v[1])) for v in vertices]
    center = _normalize(tuple(sum(v[i] for v in vectors) for i in range(3)))
    if _dot(center, center) <= 0:
        return False
    # A stable east vector; fall back to x-axis near the poles.
    east = _cross((0.0, 0.0, 1.0), center)
    if _dot(east, east) < 1e-14:
        east = _cross((1.0, 0.0, 0.0), center)
    east = _normalize(east)
    north = _normalize(_cross(center, east))

    def project(vec):
        return _dot(vec, east), _dot(vec, north)

    point = _unit(ra, dec)
    if _dot(point, center) <= 0:
        return False
    px, py = project(point)
    poly = [project(v) for v in vectors]
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        # Boundary counts as inside.
        cross = (xi - px) * (yj - py) - (yi - py) * (xj - px)
        if abs(cross) < 1e-12 and min(xi, xj) - 1e-12 <= px <= max(xi, xj) + 1e-12 and min(yi, yj) - 1e-12 <= py <= max(yi, yj) + 1e-12:
            return True
        if (yi > py) != (yj > py):
            x_int = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_int:
                inside = not inside
        j = i
    return inside
