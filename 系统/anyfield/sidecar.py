"""Sidecar store for the AnyField P0 service.

The production ``catalog.sqlite`` is never modified. This module owns:

- ``epoch_footprint``: WCS-header footprints built from linked fits-header blobs;
- ``coverage_region``: explicitly-complete blank regions (only these can justify
  HTTP 422 ``NO_LOCAL_COVERAGE``);
- ``ingest_job`` / ``epoch_extra`` / ``measurement``: the metadata-only ingest
  skeleton (no pixels, no network).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wcs_footprint import (
    FOOTPRINT_METHOD_WCS,
    angular_sep_deg,
    footprint_from_blob,
)

SIDECAR_SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sidecar_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS epoch_footprint (
    frame_id       INTEGER PRIMARY KEY,
    obs_id         TEXT NOT NULL,
    detector       INTEGER NOT NULL,
    release        TEXT,
    version        TEXT,
    week           TEXT,
    mjd            REAL,
    header_sha256  TEXT NOT NULL,
    method         TEXT NOT NULL,
    naxis1         INTEGER,
    naxis2         INTEGER,
    center_ra      REAL,
    center_dec     REAL,
    corners_json   TEXT NOT NULL,
    polygon_json   TEXT NOT NULL,
    sip_applied    INTEGER NOT NULL DEFAULT 0,
    built_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_epoch_footprint_obs
    ON epoch_footprint(obs_id, detector);
CREATE INDEX IF NOT EXISTS idx_epoch_footprint_header
    ON epoch_footprint(header_sha256);

CREATE TABLE IF NOT EXISTS coverage_region (
    region_id    TEXT PRIMARY KEY,
    ra           REAL NOT NULL,
    dec          REAL NOT NULL,
    radius_deg   REAL NOT NULL,
    complete     INTEGER NOT NULL DEFAULT 0,
    note         TEXT,
    source       TEXT NOT NULL DEFAULT 'fixture',
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_job (
    job_id             TEXT PRIMARY KEY,
    epoch_id           TEXT NOT NULL,
    content_sha256     TEXT NOT NULL,
    state              TEXT NOT NULL,
    replayed           INTEGER NOT NULL DEFAULT 0,
    request_json       TEXT NOT NULL,
    measurements_added INTEGER NOT NULL DEFAULT 0,
    input_kind         TEXT NOT NULL DEFAULT 'manifest',
    input_path         TEXT,
    error_code         TEXT,
    error_message      TEXT,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT,
    retracted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_job_epoch
    ON ingest_job(epoch_id, state);

CREATE TABLE IF NOT EXISTS epoch_extra (
    epoch_id          TEXT PRIMARY KEY,
    obs_id            TEXT NOT NULL,
    detector          INTEGER NOT NULL,
    release           TEXT NOT NULL,
    version           TEXT NOT NULL,
    week              TEXT NOT NULL,
    mjd               REAL NOT NULL,
    ra                REAL NOT NULL,
    dec               REAL NOT NULL,
    frame_id          INTEGER,
    footprint_method  TEXT NOT NULL,
    footprint_json    TEXT,
    ingest_kind       TEXT NOT NULL DEFAULT 'manifest',
    input_path        TEXT,
    file_sha256       TEXT,
    source_count      INTEGER NOT NULL DEFAULT 0,
    content_sha256    TEXT NOT NULL,
    source            TEXT NOT NULL DEFAULT 'ingest',
    visible           INTEGER NOT NULL DEFAULT 0,
    retracted_at      TEXT,
    job_id            TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measurement (
    measurement_id TEXT PRIMARY KEY,
    epoch_id       TEXT NOT NULL REFERENCES epoch_extra(epoch_id) ON DELETE CASCADE,
    target_key     TEXT NOT NULL,
    mjd            REAL,
    ra             REAL NOT NULL,
    dec            REAL NOT NULL,
    flux           REAL,
    flux_err       REAL,
    snr            REAL,
    source_index   INTEGER,
    x              REAL,
    y              REAL,
    saturated      INTEGER NOT NULL DEFAULT 0,
    source_origin  TEXT NOT NULL DEFAULT 'manifest',
    quality_json   TEXT NOT NULL DEFAULT '[]',
    visible        INTEGER NOT NULL DEFAULT 0,
    retracted_at   TEXT,
    job_id         TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (epoch_id, target_key)
);
CREATE INDEX IF NOT EXISTS idx_measurement_epoch
    ON measurement(epoch_id);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Sidecar:
    """Thread-safe, file-backed sidecar with short-lived connections."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.ensure_schema()

    # ------------------------------------------------------------------ db
    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            if not write:
                conn.execute("PRAGMA query_only=ON")
            return conn
        except sqlite3.Error as exc:
            raise RuntimeError(f"sidecar unavailable: {exc}") from exc

    def ensure_schema(self) -> None:
        with self._lock:
            with closing(self._connect(write=True)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                conn.execute(
                    "INSERT INTO sidecar_meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SIDECAR_SCHEMA_VERSION),),
                )
                # P0 -> P1A additive migrations for existing sidecars.
                self._ensure_column(conn, "ingest_job", "input_kind", "TEXT NOT NULL DEFAULT 'manifest'")
                self._ensure_column(conn, "ingest_job", "input_path", "TEXT")
                self._ensure_column(conn, "epoch_extra", "ingest_kind", "TEXT NOT NULL DEFAULT 'manifest'")
                self._ensure_column(conn, "epoch_extra", "input_path", "TEXT")
                self._ensure_column(conn, "epoch_extra", "file_sha256", "TEXT")
                self._ensure_column(conn, "epoch_extra", "source_count", "INTEGER NOT NULL DEFAULT 0")
                self._ensure_column(conn, "measurement", "source_index", "INTEGER")
                self._ensure_column(conn, "measurement", "x", "REAL")
                self._ensure_column(conn, "measurement", "y", "REAL")
                self._ensure_column(conn, "measurement", "saturated", "INTEGER NOT NULL DEFAULT 0")
                self._ensure_column(conn, "measurement", "source_origin", "TEXT NOT NULL DEFAULT 'manifest'")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    # ------------------------------------------------------------------ footprints
    def sync_footprints(self, catalog_db: str | Path, blob_root: str | Path) -> dict[str, Any]:
        """Build epoch_footprint from linked fits-header blobs.

        Only frames with ``header_sha256`` are candidates. Frames without a
        header are intentionally absent, so callers must use
        ``center_approx`` for them.
        """
        catalog_path = Path(catalog_db).expanduser().resolve()
        blob_root_path = Path(blob_root).expanduser().resolve()
        rows: list[sqlite3.Row] = []
        try:
            with closing(sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True, timeout=3.0)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT f.frame_id, f.obs_id, f.detector,
                           COALESCE(f.release, o.release) AS release,
                           f.week, f.mjd, f.header_sha256,
                           w.version, b.rel_path, b.codec
                    FROM frames f
                    JOIN observations o ON o.obs_id = f.obs_id
                    LEFT JOIN weeks w
                           ON w.week = f.week
                          AND w.release = COALESCE(f.release, o.release)
                    JOIN blobs b ON b.sha256 = f.header_sha256 AND b.kind='fits-header'
                    WHERE f.header_sha256 IS NOT NULL
                    ORDER BY f.frame_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            return {
                "headers": 0,
                "built": 0,
                "failed": 0,
                "error": f"catalog header query failed: {exc}",
                "blob_root": str(blob_root_path),
            }

        parsed: list[dict[str, Any]] = []
        failed = 0
        for row in rows:
            try:
                rel = Path(str(row["rel_path"]))
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError("unsafe blob rel_path")
                blob_path = (blob_root_path / rel).resolve()
                if blob_root_path not in blob_path.parents:
                    raise ValueError("blob path escapes blob root")
                raw = blob_path.read_bytes()
                codec = str(row["codec"] or "").lower()
                if codec.startswith("zlib"):
                    raw = zlib.decompress(raw)
                footprint = footprint_from_blob(raw)
                if footprint is None:
                    raise ValueError("no WCS image header")
                parsed.append(
                    {
                        "frame_id": int(row["frame_id"]),
                        "obs_id": str(row["obs_id"]),
                        "detector": int(row["detector"]),
                        "release": str(row["release"] or ""),
                        "version": str(row["version"] or ""),
                        "week": str(row["week"] or ""),
                        "mjd": float(row["mjd"]) if row["mjd"] is not None else None,
                        "header_sha256": str(row["header_sha256"]),
                        "footprint": footprint,
                    }
                )
            except Exception:  # noqa: BLE001 - one bad blob must not kill sync
                failed += 1

        with self._lock:
            with closing(self._connect(write=True)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute("DELETE FROM epoch_footprint")
                    now = _utcnow()
                    for item in parsed:
                        fp = item["footprint"]
                        conn.execute(
                            """
                            INSERT INTO epoch_footprint(
                                frame_id, obs_id, detector, release, version, week,
                                mjd, header_sha256, method, naxis1, naxis2,
                                center_ra, center_dec, corners_json, polygon_json,
                                sip_applied, built_at
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                item["frame_id"],
                                item["obs_id"],
                                item["detector"],
                                item["release"],
                                item["version"],
                                item["week"],
                                item["mjd"],
                                item["header_sha256"],
                                fp.get("method", FOOTPRINT_METHOD_WCS),
                                fp.get("naxis1"),
                                fp.get("naxis2"),
                                fp.get("center_ra"),
                                fp.get("center_dec"),
                                json.dumps(fp.get("corners") or [], separators=(",", ":")),
                                json.dumps(fp.get("polygon") or [], separators=(",", ":")),
                                1 if fp.get("sip_applied") else 0,
                                now,
                            ),
                        )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        return {
            "headers": len(rows),
            "built": len(parsed),
            "failed": failed,
            "blob_root": str(blob_root_path),
        }

    def footprint_map(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM epoch_footprint").fetchall()
        by_frame: dict[int, dict[str, Any]] = {}
        by_obs_det: dict[tuple[str, int], dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            try:
                item["corners"] = json.loads(item.get("corners_json") or "[]")
            except ValueError:
                item["corners"] = []
            item["method"] = item.get("method") or FOOTPRINT_METHOD_WCS
            by_frame[int(item["frame_id"])] = item
            by_obs_det[(str(item["obs_id"]), int(item["detector"]))] = item
        return {"by_frame": by_frame, "by_obs_det": by_obs_det, "count": len(rows)}

    # ------------------------------------------------------------------ complete regions
    def complete_region_for(self, ra: float, dec: float) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM coverage_region WHERE complete=1 ORDER BY radius_deg ASC"
            ).fetchall()
        best: tuple[float, dict[str, Any]] | None = None
        for row in rows:
            sep = angular_sep_deg(ra, dec, row["ra"], row["dec"])
            if not (sep <= float(row["radius_deg"]) + 1e-12):
                continue
            if best is None or sep < best[0]:
                best = (sep, dict(row) | {"sep_deg": sep})
        return best[1] if best else None

    def insert_complete_region(
        self,
        region_id: str,
        ra: float,
        dec: float,
        radius_deg: float,
        *,
        note: str = "",
        source: str = "fixture",
    ) -> None:
        with self._lock, closing(self._connect(write=True)) as conn:
            conn.execute(
                """
                INSERT INTO coverage_region(region_id, ra, dec, radius_deg, complete, note, source, updated_at)
                VALUES (?,?,?,?,1,?,?,?)
                ON CONFLICT(region_id) DO UPDATE SET
                    ra=excluded.ra, dec=excluded.dec, radius_deg=excluded.radius_deg,
                    complete=1, note=excluded.note, source=excluded.source, updated_at=excluded.updated_at
                """,
                (region_id, float(ra), float(dec), float(radius_deg), note, source, _utcnow()),
            )

    # ------------------------------------------------------------------ epochs
    def visible_epochs(self) -> list[dict[str, Any]]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM epoch_extra WHERE visible=1 ORDER BY mjd ASC, detector ASC"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["footprint"] = json.loads(item.get("footprint_json") or "null")
            except ValueError:
                item["footprint"] = None
            out.append(item)
        return out

    def epoch_by_id(self, epoch_id: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM epoch_extra WHERE epoch_id=?", (epoch_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["footprint"] = json.loads(item.get("footprint_json") or "null")
        except ValueError:
            item["footprint"] = None
        return item

    # ------------------------------------------------------------------ jobs
    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM ingest_job WHERE job_id=?", (job_id,)).fetchone()
        return _row_dict(row)

    def active_job_for_epoch(self, epoch_id: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM ingest_job WHERE epoch_id=? AND state IN ('queued','running') "
                "ORDER BY created_at DESC LIMIT 1",
                (epoch_id,),
            ).fetchone()
        return _row_dict(row)

    def create_job(
        self,
        job_id: str,
        epoch_id: str,
        content_sha256: str,
        request_json: str,
        *,
        state: str = "queued",
        replayed: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
        input_kind: str = "manifest",
        input_path: str | None = None,
    ) -> None:
        now = _utcnow()
        with self._lock, closing(self._connect(write=True)) as conn:
            conn.execute(
                """
                INSERT INTO ingest_job(
                    job_id, epoch_id, content_sha256, state, replayed, request_json,
                    measurements_added, input_kind, input_path, error_code, error_message,
                    created_at, started_at, finished_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    epoch_id,
                    content_sha256,
                    state,
                    1 if replayed else 0,
                    request_json,
                    0,
                    input_kind,
                    input_path,
                    error_code,
                    error_message,
                    now,
                    now if state != "queued" else None,
                    now if state in ("committed", "failed", "retracted") else None,
                ),
            )

    def mark_running(self, job_id: str) -> None:
        with self._lock, closing(self._connect(write=True)) as conn:
            conn.execute(
                "UPDATE ingest_job SET state='running', started_at=? WHERE job_id=? AND state='queued'",
                (_utcnow(), job_id),
            )

    def fail_job(self, job_id: str, code: str, message: str) -> None:
        with self._lock, closing(self._connect(write=True)) as conn:
            conn.execute(
                "UPDATE ingest_job SET state='failed', error_code=?, error_message=?, finished_at=? "
                "WHERE job_id=?",
                (code, message, _utcnow(), job_id),
            )

    def publish_job(
        self,
        job_id: str,
        epoch: dict[str, Any],
        measurements: list[dict[str, Any]],
    ) -> None:
        """Atomically publish epoch_extra + measurements + committed job."""
        now = _utcnow()
        with self._lock, closing(self._connect(write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO epoch_extra(
                        epoch_id, obs_id, detector, release, version, week, mjd,
                        ra, dec, frame_id, footprint_method, footprint_json,
                        ingest_kind, input_path, file_sha256, source_count,
                        content_sha256, source, visible, retracted_at, job_id,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        epoch["epoch_id"],
                        epoch["obsid"],
                        epoch["detector"],
                        epoch["release"],
                        epoch["version"],
                        epoch["week"],
                        epoch["mjd"],
                        epoch["ra"],
                        epoch["dec"],
                        epoch.get("frame_id"),
                        epoch["footprint"]["method"],
                        json.dumps(epoch.get("footprint"), separators=(",", ":")),
                        epoch.get("ingest_kind", "manifest"),
                        epoch.get("input_path"),
                        epoch.get("file_sha256"),
                        int(epoch.get("source_count") or 0),
                        epoch["content_sha256"],
                        epoch.get("source", "ingest"),
                        1,
                        None,
                        job_id,
                        now,
                        now,
                    ),
                )
                for m in measurements:
                    conn.execute(
                        """
                        INSERT INTO measurement(
                            measurement_id, epoch_id, target_key, mjd, ra, dec,
                            flux, flux_err, snr, source_index, x, y, saturated,
                            source_origin, quality_json, visible, retracted_at,
                            job_id, created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            m["measurement_id"],
                            epoch["epoch_id"],
                            m["target_key"],
                            m.get("mjd"),
                            m["ra"],
                            m["dec"],
                            m.get("flux"),
                            m.get("flux_err"),
                            m.get("snr"),
                            m.get("source_index"),
                            m.get("x"),
                            m.get("y"),
                            1 if m.get("saturated") else 0,
                            m.get("source_origin", "manifest"),
                            json.dumps(m.get("quality") or [], separators=(",", ":")),
                            1,
                            None,
                            job_id,
                            now,
                        ),
                    )
                conn.execute(
                    "UPDATE ingest_job SET state='committed', measurements_added=?, finished_at=? "
                    "WHERE job_id=?",
                    (len(measurements), now, job_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def rollback_job(self, job_id: str) -> dict[str, Any]:
        now = _utcnow()
        with self._lock, closing(self._connect(write=True)) as conn:
            row = conn.execute("SELECT * FROM ingest_job WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            state = str(row["state"])
            if state == "retracted":
                return _row_dict(row) or {}
            if state != "committed":
                raise ValueError(f"job is {state}, only committed jobs can be rolled back")
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE epoch_extra SET visible=0, retracted_at=?, updated_at=? WHERE job_id=?",
                    (now, now, job_id),
                )
                conn.execute(
                    "UPDATE measurement SET visible=0, retracted_at=? WHERE job_id=?",
                    (now, job_id),
                )
                conn.execute(
                    "UPDATE ingest_job SET state='retracted', retracted_at=? WHERE job_id=?",
                    (now, job_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_job(job_id) or {}

    # ------------------------------------------------------------------ sources
    def sources(
        self,
        *,
        ra: float | None = None,
        dec: float | None = None,
        radius_deg: float | None = None,
        epoch: str | None = None,
        limit: int = 1000,
        origin: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return visible C sources.

        Default returns local-ingested and on-demand-S3 sources; P0 manifest
        measurements (source_origin='manifest') are excluded because they are
        caller-provided target measurements, not source detections.
        """
        limit = max(1, min(int(limit), 5000))
        if origin in (None, "", "all"):
            origins = ("ingested", "on_demand_s3")
        elif origin in ("ingested", "on_demand_s3"):
            origins = (origin,)
        else:
            origins = (str(origin),)
        placeholders = ",".join("?" for _ in origins)
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT m.measurement_id, m.epoch_id, m.target_key, m.mjd,
                       m.ra, m.dec, m.flux, m.flux_err, m.snr,
                       m.source_index, m.x, m.y, m.saturated, m.source_origin,
                       m.quality_json, e.obs_id, e.detector, e.release,
                       e.version, e.week, e.ingest_kind, e.input_path
                FROM measurement m
                JOIN epoch_extra e ON e.epoch_id = m.epoch_id
                WHERE m.visible=1 AND e.visible=1 AND m.source_origin IN ({placeholders})
                ORDER BY e.mjd ASC, COALESCE(m.source_index, 9999) ASC
                """,
                origins,
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            if epoch and str(row["epoch_id"]) != str(epoch) and str(row["obs_id"]) != str(epoch):
                continue
            if ra is not None and dec is not None and radius_deg is not None:
                sep = angular_sep_deg(ra, dec, row["ra"], row["dec"])
                if not (sep <= float(radius_deg) + 1e-12):
                    continue
            else:
                sep = None
            try:
                quality = json.loads(row["quality_json"] or "[]")
            except ValueError:
                quality = []
            out.append(
                {
                    "source_id": row["measurement_id"],
                    "epoch_id": row["epoch_id"],
                    "obsid": row["obs_id"],
                    "detector": int(row["detector"]),
                    "release": row["release"],
                    "version": row["version"],
                    "week": row["week"],
                    "mjd": row["mjd"],
                    "source_index": row["source_index"],
                    "x": row["x"],
                    "y": row["y"],
                    "ra": row["ra"],
                    "dec": row["dec"],
                    "flux": row["flux"],
                    "flux_err": row["flux_err"],
                    "snr": row["snr"],
                    "saturated": bool(row["saturated"]),
                    "quality": quality,
                    "source_origin": row["source_origin"],
                    "provenance": "real",
                    "ingest_kind": row["ingest_kind"],
                    "sep_deg": round(sep, 8) if sep is not None else None,
                }
            )
            if len(out) >= limit:
                break
        return out

    def source_counts_for_epochs(self, epoch_ids: list[str]) -> dict[str, int]:
        ids = [str(x) for x in epoch_ids if x]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT epoch_id, COUNT(*) AS n FROM measurement "
                f"WHERE visible=1 AND source_origin IN ('ingested','on_demand_s3') "
                f"AND epoch_id IN ({placeholders}) "
                f"GROUP BY epoch_id",
                ids,
            ).fetchall()
        return {str(row["epoch_id"]): int(row["n"]) for row in rows}

    # ------------------------------------------------------------------ counts
    def counts(self) -> dict[str, int]:
        with self._lock, closing(self._connect()) as conn:
            return {
                "footprints": int(conn.execute("SELECT COUNT(*) FROM epoch_footprint").fetchone()[0]),
                "epoch_extra": int(
                    conn.execute("SELECT COUNT(*) FROM epoch_extra WHERE visible=1").fetchone()[0]
                ),
                "measurements": int(
                    conn.execute("SELECT COUNT(*) FROM measurement WHERE visible=1").fetchone()[0]
                ),
                "sources": int(
                    conn.execute(
                        "SELECT COUNT(*) FROM measurement WHERE visible=1 "
                        "AND source_origin IN ('ingested','on_demand_s3')"
                    ).fetchone()[0]
                ),
                "complete_regions": int(
                    conn.execute("SELECT COUNT(*) FROM coverage_region WHERE complete=1").fetchone()[0]
                ),
            }
