"""C-MVP AnyField: read-only local coverage resolver.

This module deliberately does **not** fetch pixels, call IRSA/S3/TAP, write the
database, run detection, or generate synthetic data. It only answers:

    "Does the existing local catalog.sqlite know any real epochs around this
     RA/Dec/FOV?"

The answer is intentionally conservative:

- known local coverage -> 200 with real EpochRefs and ``coverage_mode=center_approx``;
- no local evidence       -> 503 ``INDEX_INCOMPLETE`` (the MVP cannot assert
  ``NO_LOCAL_COVERAGE`` because it has no complete sky index);
- DB missing/unreadable   -> 503 ``DB_UNAVAILABLE``.

``center_approx`` means a frame is counted when its **center** is within
2.47 deg of the query; this is the same legacy approximation used by the DB
coverage endpoint, not a WCS footprint. The full C design replaces it with
footprint coverage; this MVP must say so in every response.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SERVICE_NAME = "anyfield-mvp"
SERVICE_VERSION = "0.1.0"
SCHEMA_VERSION = "1.0"
COVERAGE_MODE = "center_approx"
INDEX_COMPLETENESS = "partial"
CENTER_RADIUS_DEG = 2.47
TARGET_MATCH_TOL_DEG = 0.05
FOV_MIN = 0.02
FOV_MAX = 3.0
IRSA_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)
_VERSION_RE = re.compile(r"_spx_([^/_]+)\.fits$")


class ApiError(Exception):
    """HTTP error with a stable machine-readable code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.details = details or {}
        self.retryable = bool(retryable)


def angular_sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in degrees; NaN for non-finite inputs."""
    try:
        values = (float(ra1), float(dec1), float(ra2), float(dec2))
    except (TypeError, ValueError):
        return math.nan
    if not all(math.isfinite(v) for v in values):
        return math.nan
    a1, d1, a2, d2 = (math.radians(v) for v in values)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def mjd_to_iso(mjd: float) -> str:
    moment = MJD_EPOCH + timedelta(days=float(mjd))
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def coverage_level(n_epochs: int, baseline_days: float) -> str:
    """Same observational-opportunity levels as the DB tool (C0-C3)."""
    if n_epochs <= 1:
        return "C0"
    if n_epochs == 2:
        return "C1"
    if n_epochs < 6:
        return "C2"
    return "C3" if baseline_days >= 180.0 else "C2"


def build_fits_key(
    release: str | None,
    week: str | None,
    version: str | None,
    obs_id: str,
    detector: int | None,
) -> str | None:
    """Reconstruct a level2 key without network access."""
    if not (release and week and version and detector is not None):
        return None
    parts = str(obs_id).rsplit("_", 2)
    if len(parts) != 3:
        return None
    _, field_s, repeat_s = parts
    try:
        field_no = int(field_s)
        repeat = int(repeat_s)
    except ValueError:
        return None
    name = f"level2_{week}_{field_no:04d}_{repeat}D{int(detector)}_spx_{version}.fits"
    return f"{release}/level2/{week}/{version}/{int(detector)}/{name}"


def build_slice_url(key: str | None, ra: float, dec: float, fov: float) -> str | None:
    if not key:
        return None
    return f"{IRSA_BASE}{key}?center={ra:.6f},{dec:.6f}d&size={fov:g}"


def _version_from_key(key: str | None) -> str:
    if not key:
        return ""
    match = _VERSION_RE.search(key)
    return match.group(1) if match else ""


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _validate_query(ra: Any, dec: Any, fov: Any) -> tuple[float, float, float]:
    ra_f = _as_float(ra)
    dec_f = _as_float(dec)
    fov_f = _as_float(fov)
    if ra_f is None or dec_f is None:
        raise ApiError(400, "INVALID_QUERY", "ra/dec 必须是有限数字")
    if fov_f is None:
        raise ApiError(400, "INVALID_QUERY", "fov 必须是有限数字")
    if not (-90.0 <= dec_f <= 90.0):
        raise ApiError(400, "INVALID_QUERY", "dec 必须在 [-90, 90] 度")
    if not (FOV_MIN <= fov_f <= FOV_MAX):
        raise ApiError(400, "INVALID_QUERY", f"fov 必须在 [{FOV_MIN}, {FOV_MAX}] 度")
    return ra_f % 360.0, dec_f, fov_f


class Store:
    """Read-only view of the local catalog.sqlite.

    A new SQLite connection is opened per resolution. This is cheap for the
    MVP (5.9k frames) and avoids sharing a connection across HTTP threads.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self._snapshot_lock = threading.Lock()
        self._snapshot_signature: tuple[int, int] | None = None
        self._snapshot_value: str | None = None

    # ------------------------------------------------------------------ db
    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise ApiError(
                503,
                "DB_UNAVAILABLE",
                f"本地数据库不存在：{self.db_path}",
                details={"db_path": str(self.db_path), "read_only": True},
                retryable=True,
            )
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("SELECT 1").fetchone()
            return conn
        except sqlite3.Error as exc:
            raise ApiError(
                503,
                "DB_UNAVAILABLE",
                f"本地数据库不可读：{exc}",
                details={"db_path": str(self.db_path), "read_only": True},
                retryable=True,
            ) from exc

    def _signature(self) -> tuple[int, int] | None:
        try:
            stat = self.db_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def index_snapshot(self) -> str:
        """Stable local snapshot id derived from DB metadata.

        The id changes when the DB changes. The MVP is read-only and does not
        maintain a separate immutable index, so the id is explicitly local.
        """
        signature = self._signature()
        if signature is None:
            return "local-unavailable"
        with self._snapshot_lock:
            if self._snapshot_value and self._snapshot_signature == signature:
                return self._snapshot_value
            try:
                with closing(self._connect()) as conn:
                    counts = {
                        "frames": int(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]),
                        "observations": int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),
                        "target_epochs": int(conn.execute("SELECT COUNT(*) FROM target_epochs").fetchone()[0]),
                        "targets": int(conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]),
                    }
                    row = conn.execute("SELECT MAX(frame_id), MAX(mjd) FROM frames").fetchone()
                    counts["max_frame_id"] = row[0]
                    counts["max_mjd"] = row[1]
                    meta_row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                    payload = {
                        "kind": "local-catalog",
                        "db_schema": meta_row[0] if meta_row else "unknown",
                        "counts": counts,
                    }
            except (sqlite3.Error, ApiError):
                self._snapshot_value = "local-unavailable"
                self._snapshot_signature = signature
                return self._snapshot_value
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:12]
            self._snapshot_value = f"local-{digest}"
            self._snapshot_signature = signature
            return self._snapshot_value

    def health(self) -> dict[str, Any]:
        db_ok = True
        db_error: str | None = None
        try:
            with closing(self._connect()) as conn:
                conn.execute("SELECT COUNT(*) FROM frames").fetchone()
        except ApiError as exc:
            db_ok = False
            db_error = exc.message
        except sqlite3.Error as exc:
            db_ok = False
            db_error = str(exc)
        body: dict[str, Any] = {
            "status": "ok" if db_ok else "degraded",
            "service": SERVICE_NAME,
            "service_version": SERVICE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "coverage_mode": COVERAGE_MODE,
            "index_completeness": INDEX_COMPLETENESS,
            "index_snapshot": self.index_snapshot() if db_ok else "local-unavailable",
            "network": "none",
            "writes": "none",
            "db": {
                "path": str(self.db_path),
                "readable": db_ok,
                "read_only": True,
            },
        }
        if db_error:
            body["error"] = {
                "code": "DB_UNAVAILABLE",
                "message": db_error,
                "retryable": True,
            }
        return body

    # ------------------------------------------------------------------ target / frames
    def _match_target(self, conn: sqlite3.Connection, ra: float, dec: float) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        try:
            rows = conn.execute("SELECT id, name, ra, dec, official FROM targets").fetchall()
        except sqlite3.Error as exc:
            raise ApiError(503, "DB_UNAVAILABLE", f"读取 targets 失败：{exc}", retryable=True) from exc
        for row in rows:
            sep = angular_sep_deg(ra, dec, row["ra"], row["dec"])
            if not math.isfinite(sep) or sep > TARGET_MATCH_TOL_DEG:
                continue
            if best is None or sep < best[0]:
                best = (sep, {k: row[k] for k in row.keys()} | {"sep_deg": sep})
        return best[1] if best else None

    def _target_epochs(
        self, conn: sqlite3.Connection, target_id: str, ra: float, dec: float, fov: float
    ) -> list[dict[str, Any]]:
        try:
            rows = conn.execute(
                """
                SELECT e.id, e.obs_id, e.detector, e.release, e.week, e.mjd,
                       e.date_obs, e.sep_deg, e.fits_key, e.slice_url, e.frame_id,
                       w.version, f.ra AS frame_ra, f.dec AS frame_dec
                FROM target_epochs e
                LEFT JOIN weeks w
                       ON w.week = e.week AND w.release = e.release
                LEFT JOIN frames f ON f.frame_id = e.frame_id
                WHERE e.target_id = ?
                ORDER BY e.mjd ASC, e.detector ASC
                """,
                (str(target_id),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ApiError(503, "DB_UNAVAILABLE", f"读取 target_epochs 失败：{exc}", retryable=True) from exc

        out: list[dict[str, Any]] = []
        for row in rows:
            epoch = self._target_epoch(row, ra, dec, fov)
            if epoch is not None:
                out.append(epoch)
        return out

    def _target_epoch(
        self, row: sqlite3.Row, ra: float, dec: float, fov: float
    ) -> dict[str, Any] | None:
        obs_id = str(row["obs_id"] or "").strip()
        detector = row["detector"]
        mjd = _as_float(row["mjd"])
        if not obs_id or detector is None or mjd is None:
            return None
        try:
            detector = int(detector)
        except (TypeError, ValueError):
            return None
        if detector < 1 or detector > 6:
            return None

        release = str(row["release"] or "").strip()
        week = str(row["week"] or "").strip()
        key = str(row["fits_key"] or "").strip()
        version = str(row["version"] or "").strip()
        if not key:
            key = build_fits_key(release, week, version, obs_id, detector) or ""
        if not version:
            version = _version_from_key(key)
        if not release and key:
            release = key.split("/", 1)[0]
        if not week:
            week = obs_id.rsplit("_", 2)[0] if "_" in obs_id else ""
        if not (release and week and key):
            return None

        sep = _as_float(row["sep_deg"])
        frame_ra = _as_float(row["frame_ra"])
        frame_dec = _as_float(row["frame_dec"])
        if frame_ra is not None and frame_dec is not None:
            computed = angular_sep_deg(ra, dec, frame_ra, frame_dec)
            if math.isfinite(computed):
                sep = computed
        if sep is None or not math.isfinite(sep):
            sep = 0.0

        date_obs = str(row["date_obs"] or "").strip() or mjd_to_iso(mjd)
        return {
            "obsid": obs_id,
            "detector": detector,
            "release": release,
            "week": week,
            "version": version or "unknown",
            "mjd": float(mjd),
            "date_obs": date_obs,
            "sep_deg": round(float(sep), 6),
            "key": key,
            "slice_url": build_slice_url(key, ra, dec, fov),
            "provenance": "real",
            "frame_id": int(row["frame_id"]) if row["frame_id"] is not None else None,
        }

    def _frames(
        self, conn: sqlite3.Connection, ra: float, dec: float, fov: float
    ) -> tuple[list[dict[str, Any]], bool]:
        radius = CENTER_RADIUS_DEG
        dec_min = max(-90.0, dec - radius)
        dec_max = min(90.0, dec + radius)
        where = ["f.dec BETWEEN ? AND ?", "f.ra IS NOT NULL", "f.dec IS NOT NULL", "f.mjd IS NOT NULL"]
        args: list[Any] = [dec_min, dec_max]

        if dec_min > -90.0 and dec_max < 90.0:
            cos_dec = max(math.cos(math.radians(dec)), 0.02)
            ra_width = min(180.0, radius / cos_dec)
            if ra_width < 180.0:
                lo = (ra - ra_width) % 360.0
                hi = (ra + ra_width) % 360.0
                if lo <= hi:
                    where.append("f.ra BETWEEN ? AND ?")
                    args.extend([lo, hi])
                else:
                    where.append("(f.ra >= ? OR f.ra <= ?)")
                    args.extend([lo, hi])

        sql = f"""
            SELECT f.frame_id, f.obs_id, f.detector,
                   COALESCE(f.release, o.release) AS release,
                   f.week, f.mjd, f.date_obs, f.ra, f.dec, w.version
            FROM frames f
            JOIN observations o ON o.obs_id = f.obs_id
            LEFT JOIN weeks w
                   ON w.week = f.week
                  AND w.release = COALESCE(f.release, o.release)
            WHERE {' AND '.join(where)}
            ORDER BY f.mjd ASC, f.detector ASC
            LIMIT 5000
        """
        try:
            rows = conn.execute(sql, args).fetchall()
        except sqlite3.Error as exc:
            raise ApiError(503, "DB_UNAVAILABLE", f"读取 frames 失败：{exc}", retryable=True) from exc

        truncated = len(rows) >= 5000
        out: list[dict[str, Any]] = []
        for row in rows:
            sep = angular_sep_deg(ra, dec, row["ra"], row["dec"])
            if not math.isfinite(sep) or sep > radius + 1e-9:
                continue
            epoch = self._frame_epoch(row, sep, ra, dec, fov)
            if epoch is not None:
                out.append(epoch)
        return out, truncated

    def _frame_epoch(
        self, row: sqlite3.Row, sep: float, ra: float, dec: float, fov: float
    ) -> dict[str, Any] | None:
        obs_id = str(row["obs_id"] or "").strip()
        detector = row["detector"]
        mjd = _as_float(row["mjd"])
        if not obs_id or detector is None or mjd is None:
            return None
        try:
            detector = int(detector)
        except (TypeError, ValueError):
            return None
        if detector < 1 or detector > 6:
            return None
        release = str(row["release"] or "").strip()
        week = str(row["week"] or "").strip()
        version = str(row["version"] or "").strip()
        key = build_fits_key(release, week, version, obs_id, detector)
        if not key:
            return None
        date_obs = str(row["date_obs"] or "").strip() or mjd_to_iso(mjd)
        return {
            "obsid": obs_id,
            "detector": detector,
            "release": release,
            "week": week,
            "version": version,
            "mjd": float(mjd),
            "date_obs": date_obs,
            "sep_deg": round(float(sep), 6),
            "key": key,
            "slice_url": build_slice_url(key, ra, dec, fov),
            "provenance": "real",
            "frame_id": int(row["frame_id"]) if row["frame_id"] is not None else None,
        }

    @staticmethod
    def _dedupe_epochs(epochs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One EpochRef per obsid; keep the detector closest to the query."""
        by_obs: dict[str, dict[str, Any]] = {}
        for epoch in sorted(epochs, key=lambda e: (e["mjd"], e["sep_deg"], e["detector"])):
            cur = by_obs.get(epoch["obsid"])
            if cur is None or epoch["sep_deg"] < cur["sep_deg"]:
                by_obs[epoch["obsid"]] = epoch
        return sorted(by_obs.values(), key=lambda e: (e["mjd"], e["detector"]))

    # ------------------------------------------------------------------ public resolver
    def resolve_field(self, ra: Any, dec: Any, fov: Any) -> dict[str, Any]:
        qra, qdec, qfov = _validate_query(ra, dec, fov)
        with closing(self._connect()) as conn:
            target = self._match_target(conn, qra, qdec)
            if target is not None:
                raw = self._target_epochs(conn, target["id"], qra, qdec, qfov)
                if raw:
                    return self._response(
                        query={"ra": round(qra, 6), "dec": round(qdec, 6), "fov": qfov},
                        raw_epochs=raw,
                        target=target,
                        source="target_epochs",
                        truncated=False,
                    )
            raw, truncated = self._frames(conn, qra, qdec, qfov)
            if raw:
                return self._response(
                    query={"ra": round(qra, 6), "dec": round(qdec, 6), "fov": qfov},
                    raw_epochs=raw,
                    target=None,
                    source="frames",
                    truncated=truncated,
                )

        raise ApiError(
            503,
            "INDEX_INCOMPLETE",
            "C-MVP 只有本地已有帧/预设历元的局部索引；该天区没有本地证据，"
            "但不能据此断言 NO_LOCAL_COVERAGE（完整索引未实现）。",
            details={
                "coverage_mode": COVERAGE_MODE,
                "coverage_state": "unknown",
                "index_completeness": INDEX_COMPLETENESS,
                "index_snapshot": self.index_snapshot(),
                "strong_assertion": False,
                "no_local_coverage": False,
            },
            retryable=True,
        )

    def resolve_epochs(self, ra: Any, dec: Any, fov: Any) -> dict[str, Any]:
        """1.0-compatible epoch view over the same local resolver.

        Known coverage -> 200 EpochRefs; unknown/incomplete -> 503
        INDEX_INCOMPLETE (never 422, never an empty 200 that could be mistaken
        for NO_LOCAL_COVERAGE).
        """
        field = self.resolve_field(ra, dec, fov)
        return {
            "schema_version": SCHEMA_VERSION,
            "service_version": SERVICE_VERSION,
            "index_snapshot": field["index_snapshot"],
            "coverage_mode": COVERAGE_MODE,
            "query": field["query"],
            "coverage": field["coverage"],
            "epochs": field["epochs"],
            "count": field["count"],
            "returned": field["returned"],
            "fallback_reason": None,
            "warnings": field["warnings"],
            "generated_at": field["generated_at"],
        }

    def _response(
        self,
        *,
        query: dict[str, Any],
        raw_epochs: list[dict[str, Any]],
        target: dict[str, Any] | None,
        source: str,
        truncated: bool,
    ) -> dict[str, Any]:
        epochs = self._dedupe_epochs(raw_epochs)
        mjds = [e["mjd"] for e in epochs]
        baseline = (max(mjds) - min(mjds)) if len(mjds) >= 2 else 0.0
        n_epochs = len(epochs)
        n_weeks = len({e["week"] for e in epochs if e["week"]})
        detectors = sorted({e["detector"] for e in raw_epochs})
        releases = sorted({e["release"] for e in raw_epochs if e["release"]})
        target_id = target["id"] if target else None
        coverage = {
            "state": "covered",
            "coverage_mode": COVERAGE_MODE,
            "method": "center_radius_2.47",
            "radius_deg": CENTER_RADIUS_DEG,
            "search_radius_deg": CENTER_RADIUS_DEG,
            "n_frames": len(raw_epochs),
            "n_epochs": n_epochs,
            "n_observations": n_epochs,
            "n_weeks": n_weeks,
            "n_releases": len(releases),
            "releases": releases,
            "detectors": detectors,
            "mjd_min": round(min(mjds), 6) if mjds else None,
            "mjd_max": round(max(mjds), 6) if mjds else None,
            "baseline_days": round(baseline, 3),
            "coverage_level": coverage_level(n_epochs, baseline),
            "index_completeness": INDEX_COMPLETENESS,
            "target_id": target_id,
            "target_name": target.get("name") if target else None,
            "target_sep_deg": round(float(target["sep_deg"]), 6) if target else None,
        }
        warnings = [
            "CENTER_APPROX_COVERAGE: 2.47° center radius, not a WCS footprint",
            "LOCAL_INDEX_PARTIAL: only existing local frames/target_epochs; no complete sky index",
        ]
        if target:
            warnings.append(f"TARGET_MATCHED: {target_id} within {TARGET_MATCH_TOL_DEG}°")
        else:
            warnings.append("NO_PRESET_TARGET_MATCH: center-approx frame query")
        if truncated:
            warnings.append("FRAME_QUERY_TRUNCATED_AT_5000: narrow the field or add an index")
        return {
            "schema_version": SCHEMA_VERSION,
            "service_version": SERVICE_VERSION,
            "index_snapshot": self.index_snapshot(),
            "coverage_mode": COVERAGE_MODE,
            "query": query,
            "coverage": coverage,
            "epochs": epochs,
            "count": n_epochs,
            "returned": n_epochs,
            "source": source,
            "warnings": warnings,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def error_payload(exc: ApiError, *, index_snapshot: str | None = None, request_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "service_version": SERVICE_VERSION,
        "coverage_mode": COVERAGE_MODE,
        "request_id": request_id,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
            "details": dict(exc.details),
        },
    }
    if index_snapshot is not None:
        body["index_snapshot"] = index_snapshot
    if exc.code == "INDEX_INCOMPLETE":
        body["coverage"] = {
            "state": "unknown",
            "coverage_mode": COVERAGE_MODE,
            "index_completeness": INDEX_COMPLETENESS,
            "strong_assertion": False,
            "no_local_coverage": False,
        }
    return body
