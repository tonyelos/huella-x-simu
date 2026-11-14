// astro.js — 时间 / 坐标 / 太阳位置。无外部依赖，全部为度制。
export const DEG = Math.PI / 180;
export const RAD = 180 / Math.PI;

export const norm360 = (a) => ((a % 360) + 360) % 360;
export const norm180 = (a) => {
  const x = norm360(a);
  return x > 180 ? x - 360 : x;
};

// MJD 40587 = 1970-01-01T00:00:00Z
export const mjdToDate = (mjd) => new Date(Math.round((mjd - 40587) * 86400000));
export const dateToMjd = (d) => d.getTime() / 86400000 + 40587;
export const jdFromMjd = (mjd) => mjd + 2400000.5;

/** Greenwich mean sidereal time, degrees. (IAU 1982, sub-arcsecond) */
export function gmstDeg(mjd) {
  const jd = jdFromMjd(mjd);
  const d = jd - 2451545.0;
  const T = d / 36525.0;
  const g =
    280.46061837 +
    360.98564736629 * d +
    0.000387933 * T * T -
    (T * T * T) / 38710000.0;
  return norm360(g);
}

/** Low-precision solar apparent position (Meeus ch.25), degrees. */
export function sunRaDec(mjd) {
  const n = jdFromMjd(mjd) - 2451545.0;
  const L = norm360(280.46 + 0.9856474 * n);
  const g = norm360(357.528 + 0.9856003 * n) * DEG;
  const lambda = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * DEG;
  const eps = (23.439 - 0.0000004 * n) * DEG;
  const ra = norm360(
    Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda)) * RAD
  );
  const dec = Math.asin(Math.sin(eps) * Math.sin(lambda)) * RAD;
  return { ra, dec };
}

/** Sub-solar point (geographic), degrees east positive. */
export function subsolarPoint(mjd) {
  const s = sunRaDec(mjd);
  return { lat: s.dec, lon: norm180(s.ra - gmstDeg(mjd)) };
}

/** Geographic point where (ra, dec) is at the zenith right now. */
export function zenithOf(ra, dec, mjd) {
  return { lat: dec, lon: norm180(ra - gmstDeg(mjd)) };
}

/** Inverse: the sky coordinates currently overhead a geographic point. */
export function skyOverhead(lat, lon, mjd) {
  return { ra: norm360(gmstDeg(mjd) + lon), dec: lat };
}

/** Great-circle separation, degrees. */
export function angSep(ra1, dec1, ra2, dec2) {
  const d1 = dec1 * DEG;
  const d2 = dec2 * DEG;
  const dra = (ra1 - ra2) * DEG;
  const c =
    Math.sin(d1) * Math.sin(d2) + Math.cos(d1) * Math.cos(d2) * Math.cos(dra);
  return Math.acos(Math.min(1, Math.max(-1, c))) * RAD;
}

// ICRS -> Galactic rotation matrix (Hipparcos / ESA, J2000)
const GX = [-0.0548755604, -0.8734370902, -0.4838350155];
const GY = [0.4941094279, -0.44482963, 0.7469822445];
const GZ = [-0.867666149, -0.1980763734, 0.4559837762];

export function equatorialToGalactic(ra, dec) {
  const a = ra * DEG;
  const d = dec * DEG;
  const ex = Math.cos(d) * Math.cos(a);
  const ey = Math.cos(d) * Math.sin(a);
  const ez = Math.sin(d);
  const gx = GX[0] * ex + GX[1] * ey + GX[2] * ez;
  const gy = GY[0] * ex + GY[1] * ey + GY[2] * ez;
  const gz = GZ[0] * ex + GZ[1] * ey + GZ[2] * ez;
  const l = norm360(Math.atan2(gy, gx) * RAD);
  const b = Math.asin(Math.min(1, Math.max(-1, gz))) * RAD;
  return { l, b };
}

export function eqFromGalactic(l, b) {
  const lb = l * DEG;
  const bb = b * DEG;
  const gx = Math.cos(bb) * Math.cos(lb);
  const gy = Math.cos(bb) * Math.sin(lb);
  const gz = Math.sin(bb);
  // e = R^T g
  const ex = GX[0] * gx + GY[0] * gy + GZ[0] * gz;
  const ey = GX[1] * gx + GY[1] * gy + GZ[1] * gz;
  const ez = GX[2] * gx + GY[2] * gy + GZ[2] * gz;
  return {
    ra: norm360(Math.atan2(ey, ex) * RAD),
    dec: Math.asin(Math.min(1, Math.max(-1, ez))) * RAD,
  };
}

/** Ecliptic latitude -> equatorial (used to draw the ecliptic on the sky map). */
export function eclipticToEquatorial(lambdaDeg) {
  const eps = 23.4392911 * DEG;
  const l = lambdaDeg * DEG;
  const ra = norm360(Math.atan2(Math.cos(eps) * Math.sin(l), Math.cos(l)) * RAD);
  const dec = Math.asin(Math.sin(eps) * Math.sin(l)) * RAD;
  return { ra, dec };
}

export const fmt = (v, n = 2) => v.toFixed(n);

export function fmtRa(raDeg) {
  const h = norm360(raDeg) / 15;
  const hh = Math.floor(h);
  const mm = Math.floor((h - hh) * 60);
  const ss = ((h - hh) * 60 - mm) * 60;
  return `${String(hh).padStart(2, '0')}h${String(mm).padStart(2, '0')}m${ss
    .toFixed(1)
    .padStart(4, '0')}s`;
}

export function fmtDec(decDeg) {
  const s = decDeg < 0 ? '-' : '+';
  const a = Math.abs(decDeg);
  const dd = Math.floor(a);
  const mm = Math.floor((a - dd) * 60);
  const ss = Math.round(((a - dd) * 60 - mm) * 60);
  return `${s}${String(dd).padStart(2, '0')}°${String(mm).padStart(2, '0')}′${String(
    ss
  ).padStart(2, '0')}″`;
}

export function fmtUtc(mjd, withDate = true) {
  const d = mjdToDate(mjd);
  const p = (n) => String(n).padStart(2, '0');
  const t = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
  if (!withDate) return t;
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${t}`;
}

/** Local sidereal time in degrees for a geographic longitude. */
export const lst = (lon, mjd) => norm360(gmstDeg(mjd) + lon);
