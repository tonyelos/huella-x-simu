"""导入层：把两个项目的现有数据搬进统一数据库。

支持的数据源
------------
- `app/data/header_cache/*.hdr`      45 KB FITS 头（14k 份，主要来源）
- `app/data/week_index.json`          151 个观测周 / 处理版本
- `app/data/targets.json`             7 个目标（5 官方 + 2 样本天区）
- `app/data/epoch_catalog.json`       每个目标的候选历元
- `app/data/epochcache/*.npz`         已下载的切割产品（图像/方差/FLAGS/PSF）
- `视图工具/data/fields.json`          兼容用曝光索引（可补头文件缺失的帧）
- `参考 资料/muestras/*.fits`         离线样本切片

所有导入幂等：重复运行只更新，不产生重复行。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .blobs import BlobStore
from .catalog import build_fits_key, split_obs_id, zone_of
from .config import DEFAULT_BLOBS, DEFAULT_DB, TOOL_VERSION
from .connection import connect, transaction, utcnow
from .fitshdr import HeaderError, extract_record
from .schema import apply_schema
from .timeutil import mjd_to_iso

try:  # numpy 只用于读 NPZ 元数据；没有也能靠文件名工作
    import numpy as np
except Exception:  # noqa: BLE001
    np = None


RELEASE_LABELS = {
    "qr3": "SPHEREx Quick Release 3（2026-09 起）",
    "dr1": "SPHEREx Data Release 1（2026-11）",
    "qr2": "SPHEREx Quick Release 2（只读直链）",
    "dr1_deep": "SPHEREx DR1 Deep",
    "unknown": "来源未标注",
}

_CUTOUT_RE = re.compile(
    r"^(?P<release>[a-z0-9]+)_(?P<week>\d{4}W\d{2}_[0-9A-Z]+)_"
    r"(?P<field>\d+)_(?P<repeat>\d+)_D(?P<det>\d)_"
    r"(?P<ra>-?\d+(?:\.\d+)?)_(?P<dec>-?\d+(?:\.\d+)?)_"
    r"(?P<fov>\d+(?:\.\d+)?)\.npz$"
)


def init_db(db_path: str | os.PathLike[str] = DEFAULT_DB, blob_root: str | os.PathLike[str] | None = None) -> Path:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    BlobStore(blob_root or DEFAULT_BLOBS)
    conn = connect(path)
    try:
        apply_schema(conn)
        now = utcnow()
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('created_at',?) ON CONFLICT(key) DO NOTHING", (now,)
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('tool_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (TOOL_VERSION,),
        )
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()
    return path


class Ingestor:
    def __init__(
        self,
        db_path: str | os.PathLike[str] = DEFAULT_DB,
        blob_root: str | os.PathLike[str] | None = None,
        *,
        store_blobs: bool = True,
        quiet: bool = False,
    ):
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.conn = connect(self.db_path)
        self.blobs = BlobStore(blob_root or DEFAULT_BLOBS)
        self.store_blobs = store_blobs
        self.quiet = quiet
        self._week_release: dict[str, str] = {}
        self._week_version: dict[str, str] = {}
        self._refresh_week_map()

    # ------------------------------------------------------------------ 生命周期
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Ingestor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    @contextmanager
    def _run(self, kind: str) -> Iterator[dict[str, Any]]:
        started = utcnow()
        t0 = time.time()
        cur = self.conn.execute(
            "INSERT INTO ingest_runs(kind, started_at, status) VALUES(?,?, 'running')", (kind, started)
        )
        run_id = int(cur.lastrowid)
        stats: dict[str, Any] = {
            "run_id": run_id,
            "kind": kind,
            "files_seen": 0,
            "files_ingested": 0,
            "rows_inserted": 0,
            "rows_updated": 0,
            "bytes_read": 0,
            "errors": 0,
        }
        try:
            yield stats
        except BaseException as exc:
            self.conn.execute(
                "UPDATE ingest_runs SET finished_at=?, status='failed', stats_json=? WHERE run_id=?",
                (utcnow(), json.dumps({"error": str(exc)}, ensure_ascii=False), run_id),
            )
            self.conn.commit()
            raise
        else:
            stats["elapsed_sec"] = round(time.time() - t0, 2)
            self.conn.execute(
                """UPDATE ingest_runs SET finished_at=?, status='ok', files_seen=?, files_ingested=?,
                   rows_inserted=?, rows_updated=?, bytes_read=?, errors=?, stats_json=? WHERE run_id=?""",
                (
                    utcnow(),
                    stats["files_seen"],
                    stats["files_ingested"],
                    stats["rows_inserted"],
                    stats["rows_updated"],
                    stats["bytes_read"],
                    stats["errors"],
                    json.dumps(stats, ensure_ascii=False),
                    run_id,
                ),
            )
            self.conn.commit()

    # ------------------------------------------------------------------ 小工具
    @staticmethod
    def _read_json(path: str | os.PathLike[str]) -> Any:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _refresh_week_map(self) -> None:
        self._week_release = {}
        self._week_version = {}
        for row in self.conn.execute("SELECT week, release, version FROM weeks"):
            self._week_release[row["week"]] = row["release"]
            if row["version"]:
                self._week_version[row["week"]] = row["version"]

    def _ensure_release(self, code: str, notes: str | None = None) -> None:
        code = (code or "unknown").strip() or "unknown"
        self.conn.execute(
            """INSERT INTO releases(code,label,status,notes) VALUES(?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET label=excluded.label, notes=COALESCE(excluded.notes, releases.notes)""",
            (code, RELEASE_LABELS.get(code, code), "active", notes),
        )

    # ------------------------------------------------------------------ 目标
    def ingest_targets(self, path: str | os.PathLike[str]) -> dict:
        payload = self._read_json(path)
        targets = payload.get("targets", payload if isinstance(payload, list) else [])
        with self._run("targets") as st:
            st["files_seen"] = 1
            st["bytes_read"] = Path(path).stat().st_size
            now = utcnow()
            known = {
                "id", "name", "name_zh", "official", "ra", "dec", "type", "expected",
                "truth", "note", "aliases",
            }
            for t in targets:
                tid = str(t.get("id") or "").strip()
                if not tid:
                    st["errors"] += 1
                    continue
                truth = t.get("truth")
                extra = {k: v for k, v in t.items() if k not in known}
                existed = self.conn.execute("SELECT 1 FROM targets WHERE id=?", (tid,)).fetchone() is not None
                self.conn.execute(
                    """INSERT INTO targets(id,name,name_zh,official,ra,dec,type,expected,truth_json,note,aliases,extra_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         name=excluded.name, name_zh=excluded.name_zh, official=excluded.official,
                         ra=excluded.ra, dec=excluded.dec, type=excluded.type, expected=excluded.expected,
                         truth_json=excluded.truth_json, note=excluded.note, aliases=excluded.aliases,
                         extra_json=excluded.extra_json, updated_at=excluded.updated_at""",
                    (
                        tid,
                        t.get("name") or tid,
                        t.get("name_zh"),
                        1 if t.get("official") else 0,
                        float(t.get("ra") or 0.0),
                        float(t.get("dec") or 0.0),
                        t.get("type"),
                        t.get("expected"),
                        json.dumps(truth, ensure_ascii=False) if truth is not None else None,
                        t.get("note"),
                        json.dumps(t.get("aliases"), ensure_ascii=False) if t.get("aliases") else None,
                        json.dumps(extra, ensure_ascii=False) if extra else None,
                        now,
                    ),
                )
                if existed:
                    st["rows_updated"] += 1
                else:
                    st["rows_inserted"] += 1
            st["files_ingested"] = 1
        self._log(f"[targets] {len(targets)} 个目标 -> {self.db_path}")
        return st

    # ------------------------------------------------------------------ 周索引
    def ingest_week_index(self, path: str | os.PathLike[str]) -> dict:
        payload = self._read_json(path)
        weeks = payload.get("weeks", [])
        with self._run("week_index") as st:
            st["files_seen"] = 1
            st["bytes_read"] = Path(path).stat().st_size
            now = utcnow()
            for w in weeks:
                release = str(w.get("release") or "unknown")
                self._ensure_release(release)
                existed = self.conn.execute(
                    "SELECT 1 FROM weeks WHERE release=? AND week=?", (release, w.get("week"))
                ).fetchone() is not None
                self.conn.execute(
                    """INSERT INTO weeks(release,week,version,mjd_obs,mjd_min,mjd_max,n_frames,first_key,first_obsid,
                                          first_detector,first_date_obs,elon,elat,ra,dec,imported_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(release,week) DO UPDATE SET
                         version=excluded.version, mjd_obs=excluded.mjd_obs,
                         first_key=excluded.first_key, first_obsid=excluded.first_obsid,
                         first_detector=excluded.first_detector, first_date_obs=excluded.first_date_obs,
                         elon=excluded.elon, elat=excluded.elat, ra=excluded.ra, dec=excluded.dec,
                         mjd_min=excluded.mjd_min, mjd_max=excluded.mjd_max, imported_at=excluded.imported_at""",
                    (
                        release,
                        w.get("week"),
                        w.get("version"),
                        w.get("mjd_obs"),
                        w.get("mjd_obs"),
                        w.get("mjd_obs"),
                        0,
                        w.get("first_key"),
                        w.get("obsid"),
                        int(w["detector"]) if str(w.get("detector") or "").strip().isdigit() else None,
                        w.get("date_obs"),
                        w.get("elon"),
                        w.get("elat"),
                        w.get("ra"),
                        w.get("dec"),
                        now,
                    ),
                )
                if existed:
                    st["rows_updated"] += 1
                else:
                    st["rows_inserted"] += 1
            st["files_ingested"] = 1
        self._refresh_week_map()
        self._log(f"[weeks] {len(weeks)} 个周 -> {self.db_path}")
        return st

    # ------------------------------------------------------------------ 头文件
    def _upsert_observation(self, rec: dict, release: str | None) -> None:
        now = utcnow()
        self.conn.execute(
            """INSERT INTO observations(obs_id,week,field_no,repeat_no,release,release_source,mjd,date_obs,first_seen_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(obs_id) DO UPDATE SET
                 week=excluded.week, field_no=excluded.field_no, repeat_no=excluded.repeat_no,
                 release=COALESCE(observations.release, excluded.release),
                 release_source=COALESCE(observations.release_source, excluded.release_source),
                 mjd=COALESCE(observations.mjd, excluded.mjd),
                 date_obs=COALESCE(observations.date_obs, excluded.date_obs),
                 updated_at=excluded.updated_at""",
            (
                rec["obs_id"],
                rec["week"],
                rec.get("field_no"),
                rec.get("repeat_no"),
                release,
                "week_index" if release else None,
                rec.get("mjd"),
                rec.get("date_obs") or mjd_to_iso(rec.get("mjd")),
                now,
                now,
            ),
        )

    def _effective_release(self, obs_id: str, fallback: str | None) -> str | None:
        row = self.conn.execute("SELECT release FROM observations WHERE obs_id=?", (obs_id,)).fetchone()
        return (row[0] if row and row[0] else fallback)

    def _upsert_frame(self, rec: dict, *, release: str | None, blob_sha: str | None, source_path: str) -> tuple[int, bool]:
        now = utcnow()
        z = zone_of(float(rec["dec"]))
        existing = self.conn.execute(
            "SELECT frame_id FROM frames WHERE obs_id=? AND detector=?", (rec["obs_id"], rec["detector"])
        ).fetchone()
        inserted = existing is None
        self.conn.execute(
            """INSERT INTO frames(
                 obs_id,detector,release,week,ra,dec,mjd,date_obs,xposure,
                 sgt_lat,sgt_lon,elon,elat,naxis1,naxis2,pixel_scale_arcsec,
                 crpix1,crpix2,sip_order,psf_fwhm_arcsec,l2dqa,
                 header_sha256,source_path,zone,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(obs_id,detector) DO UPDATE SET
                 release=COALESCE(frames.release, excluded.release),
                 week=excluded.week, ra=excluded.ra, dec=excluded.dec,
                 mjd=COALESCE(excluded.mjd, frames.mjd),
                 date_obs=COALESCE(excluded.date_obs, frames.date_obs),
                 xposure=COALESCE(excluded.xposure, frames.xposure),
                 sgt_lat=COALESCE(excluded.sgt_lat, frames.sgt_lat),
                 sgt_lon=COALESCE(excluded.sgt_lon, frames.sgt_lon),
                 elon=COALESCE(excluded.elon, frames.elon),
                 elat=COALESCE(excluded.elat, frames.elat),
                 naxis1=COALESCE(excluded.naxis1, frames.naxis1),
                 naxis2=COALESCE(excluded.naxis2, frames.naxis2),
                 pixel_scale_arcsec=COALESCE(excluded.pixel_scale_arcsec, frames.pixel_scale_arcsec),
                 crpix1=COALESCE(excluded.crpix1, frames.crpix1),
                 crpix2=COALESCE(excluded.crpix2, frames.crpix2),
                 sip_order=COALESCE(excluded.sip_order, frames.sip_order),
                 psf_fwhm_arcsec=COALESCE(excluded.psf_fwhm_arcsec, frames.psf_fwhm_arcsec),
                 l2dqa=COALESCE(excluded.l2dqa, frames.l2dqa),
                 header_sha256=COALESCE(excluded.header_sha256, frames.header_sha256),
                 source_path=excluded.source_path, zone=excluded.zone, updated_at=excluded.updated_at""",
            (
                rec["obs_id"],
                int(rec["detector"]),
                release,
                rec["week"],
                float(rec["ra"]),
                float(rec["dec"]),
                rec.get("mjd"),
                rec.get("date_obs") or mjd_to_iso(rec.get("mjd")),
                rec.get("xposure"),
                rec.get("sgt_lat"),
                rec.get("sgt_lon"),
                rec.get("elon"),
                rec.get("elat"),
                rec.get("naxis1"),
                rec.get("naxis2"),
                rec.get("pixel_scale_arcsec"),
                rec.get("crpix1"),
                rec.get("crpix2"),
                rec.get("sip_order"),
                rec.get("psf_fwhm_arcsec"),
                rec.get("l2dqa"),
                blob_sha,
                source_path,
                z,
                now,
                now,
            ),
        )
        row = self.conn.execute(
            "SELECT frame_id FROM frames WHERE obs_id=? AND detector=?", (rec["obs_id"], rec["detector"])
        ).fetchone()
        frame_id = int(row[0])
        if release:
            key = build_fits_key(
                release, rec["week"], self._week_version.get(rec["week"]), rec["obs_id"], int(rec["detector"])
            )
            self.conn.execute(
                """INSERT INTO frame_releases(frame_id,release,procver,source_key)
                   VALUES(?,?,?,?)
                   ON CONFLICT(frame_id,release) DO UPDATE SET
                     procver=COALESCE(excluded.procver, frame_releases.procver),
                     source_key=COALESCE(excluded.source_key, frame_releases.source_key)""",
                (frame_id, release, self._week_version.get(rec["week"]), key),
            )
        return frame_id, inserted

    def ingest_headers(
        self,
        directory: str | os.PathLike[str],
        *,
        limit: int | None = None,
        pattern: str = "*.hdr",
        progress_every: int = 2000,
    ) -> dict:
        src = Path(directory).expanduser().resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"头文件目录不存在: {src}")
        files = sorted(src.glob(pattern))
        if limit:
            files = files[: int(limit)]
        if not files:
            raise FileNotFoundError(f"{src} 下没有 {pattern}")

        batch = 500
        with self._run("headers") as st:
            t0 = time.time()
            for i, path in enumerate(files, start=1):
                st["files_seen"] += 1
                try:
                    data = path.read_bytes()
                    st["bytes_read"] += len(data)
                    rec = extract_record(data, str(path))
                    release = self._week_release.get(rec["week"])
                    blob_sha = None
                    if self.store_blobs:
                        meta = self.blobs.put_bytes(data, "fits-header")
                        self.blobs.register(self.conn, meta)
                        blob_sha = meta.sha256
                    self._upsert_observation(rec, release)
                    release = self._effective_release(rec["obs_id"], release)
                    _, inserted = self._upsert_frame(
                        rec, release=release, blob_sha=blob_sha, source_path=str(path)
                    )
                    if inserted:
                        st["rows_inserted"] += 1
                    else:
                        st["rows_updated"] += 1
                    st["files_ingested"] += 1
                except (HeaderError, OSError, ValueError, sqlite3.Error) as exc:
                    st["errors"] += 1
                    if st["errors"] <= 20:
                        self._log(f"  ! {path.name}: {exc}")
                if i % batch == 0:
                    self.conn.commit()
                if progress_every and i % progress_every == 0:
                    rate = i / max(time.time() - t0, 1e-9)
                    self._log(f"  {i}/{len(files)}  {rate:.0f} files/s  errors={st['errors']}")
            self.conn.commit()
        self.refresh_week_counts()
        self.relink_cutouts()
        self._log(
            f"[headers] {st['files_ingested']}/{len(files)} 帧，错误 {st['errors']}，"
            f"{st['elapsed_sec']}s，{st['bytes_read']/1e6:.1f} MB"
        )
        return st

    # ------------------------------------------------------------------ 历元目录
    def ingest_epoch_catalog(self, path: str | os.PathLike[str]) -> dict:
        payload = self._read_json(path)
        epochs = payload.get("epochs", {})
        now = utcnow()
        with self._run("epoch_catalog") as st:
            st["files_seen"] = 1
            st["bytes_read"] = Path(path).stat().st_size
            for tid, items in epochs.items():
                target = self.conn.execute("SELECT id FROM targets WHERE id=?", (tid,)).fetchone()
                if not target:
                    st["errors"] += 1
                    self._log(f"  ! 目标 {tid} 不在 targets 表，跳过其历元（先导 targets.json）")
                    continue
                for rank, ep in enumerate(items):
                    obs_id = str(ep.get("obsid") or "")
                    detector = ep.get("detector")
                    release = ep.get("release")
                    if not obs_id or detector is None:
                        st["errors"] += 1
                        continue
                    frame = self.conn.execute(
                        "SELECT frame_id FROM frames WHERE obs_id=? AND detector=?",
                        (obs_id, int(detector)),
                    ).fetchone()
                    frame_id = int(frame[0]) if frame else None
                    existed = self.conn.execute(
                        "SELECT 1 FROM target_epochs WHERE target_id=? AND release=? AND obs_id=? AND detector=?",
                        (tid, release, obs_id, int(detector)),
                    ).fetchone() is not None
                    self.conn.execute(
                        """INSERT INTO target_epochs(
                             target_id,frame_id,release,week,obs_id,detector,mjd,date_obs,sep_deg,rank,
                             fits_key,slice_url,source,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(target_id,release,obs_id,detector) DO UPDATE SET
                             frame_id=COALESCE(excluded.frame_id, target_epochs.frame_id),
                             week=excluded.week, mjd=COALESCE(excluded.mjd, target_epochs.mjd),
                             date_obs=COALESCE(excluded.date_obs, target_epochs.date_obs),
                             sep_deg=excluded.sep_deg, rank=excluded.rank,
                             fits_key=COALESCE(excluded.fits_key, target_epochs.fits_key),
                             slice_url=COALESCE(excluded.slice_url, target_epochs.slice_url)""",
                        (
                            tid,
                            frame_id,
                            release,
                            ep.get("week"),
                            obs_id,
                            int(detector),
                            ep.get("mjd"),
                            ep.get("date_obs") or mjd_to_iso(ep.get("mjd")),
                            ep.get("sep_deg"),
                            rank,
                            ep.get("key"),
                            ep.get("slice_url_002") or ep.get("slice_url"),
                            "epoch_catalog",
                            now,
                        ),
                    )
                    if existed:
                        st["rows_updated"] += 1
                    else:
                        st["rows_inserted"] += 1
            st["files_ingested"] = 1
        self._log(f"[epochs] {sum(len(v) for v in epochs.values())} 条候选历元")
        return st

    # ------------------------------------------------------------------ 切割缓存
    def _lookup_frame_id(self, obs_id: str, detector: int) -> int | None:
        row = self.conn.execute(
            "SELECT frame_id FROM frames WHERE obs_id=? AND detector=?", (obs_id, detector)
        ).fetchone()
        return int(row[0]) if row else None

    def _ingest_npz_meta(self, path: Path) -> tuple[str, int, float | None, str | None, dict]:
        meta: dict[str, Any] = {}
        obs_id = ""
        detector = 0
        mjd = None
        release = None
        m = _CUTOUT_RE.match(path.name)
        if m:
            obs_id = f"{m.group('week')}_{m.group('field')}_{m.group('repeat')}"
            detector = int(m.group("det"))
            release = m.group("release")
            meta.update(
                {
                    "ra": float(m.group("ra")),
                    "dec": float(m.group("dec")),
                    "fov_deg": float(m.group("fov")),
                }
            )
        if np is not None:
            try:
                with np.load(path, allow_pickle=True) as z:
                    if "obsid" in z:
                        obs_id = str(z["obsid"])
                    if "detector" in z:
                        detector = int(str(z["detector"]))
                    if "mjd" in z:
                        mjd = float(z["mjd"])
                    if "release" in z:
                        release = str(z["release"])
                    if "sep_deg" in z:
                        meta["sep_deg"] = float(z["sep_deg"])
                    if "wave" in z:
                        meta["wave_um"] = float(z["wave"])
                    if "image" in z:
                        shape = tuple(int(x) for x in z["image"].shape)
                        meta["shape"] = f"{shape[1]}x{shape[0]}"
                        meta["n_pix"] = int(shape[0] * shape[1])
            except Exception as exc:  # noqa: BLE001
                meta["npz_error"] = str(exc)
        return obs_id, detector, mjd, release, meta

    def ingest_cutouts(self, directory: str | os.PathLike[str], *, limit: int | None = None) -> dict:
        src = Path(directory).expanduser().resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"切割目录不存在: {src}")
        files = sorted(list(src.glob("*.npz")) + list(src.glob("*.fits")))
        if limit:
            files = files[: int(limit)]
        now = utcnow()
        with self._run("cutouts") as st:
            for path in files:
                st["files_seen"] += 1
                st["bytes_read"] += path.stat().st_size
                try:
                    meta = self.blobs.put_file(path, "npz-cutout" if path.suffix == ".npz" else "fits-cutout")
                    self.blobs.register(self.conn, meta)
                    if path.suffix == ".npz":
                        obs_id, detector, mjd, release, extra = self._ingest_npz_meta(path)
                        ra = extra.get("ra")
                        dec = extra.get("dec")
                        fov = extra.get("fov_deg")
                        shape = extra.get("shape")
                        n_pix = extra.get("n_pix")
                    else:
                        data = path.read_bytes()
                        rec = extract_record(data, str(path))
                        obs_id, detector, mjd, release = rec["obs_id"], rec["detector"], rec["mjd"], None
                        ra, dec, fov = rec["ra"], rec["dec"], None
                        shape, n_pix = None, rec.get("naxis1", 0) * rec.get("naxis2", 0)
                        extra = {"header_obsid": obs_id}

                    frame_id = self._lookup_frame_id(obs_id, detector) if obs_id and detector else None
                    cutout_id = path.stem
                    existed = self.conn.execute(
                        "SELECT 1 FROM cutouts WHERE cutout_id=?", (cutout_id,)
                    ).fetchone() is not None
                    self.conn.execute(
                        """INSERT INTO cutouts(cutout_id,frame_id,obs_id,detector,sha256,kind,ra,dec,fov_deg,mjd,n_pix,shape,meta_json,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(cutout_id) DO UPDATE SET
                             frame_id=COALESCE(excluded.frame_id, cutouts.frame_id),
                             obs_id=excluded.obs_id, detector=excluded.detector,
                             sha256=excluded.sha256, kind=excluded.kind, ra=excluded.ra, dec=excluded.dec,
                             fov_deg=excluded.fov_deg, mjd=COALESCE(excluded.mjd, cutouts.mjd),
                             n_pix=excluded.n_pix, shape=excluded.shape, meta_json=excluded.meta_json""",
                        (
                            cutout_id,
                            frame_id,
                            obs_id or None,
                            detector or None,
                            meta.sha256,
                            "npz" if path.suffix == ".npz" else "fits",
                            ra,
                            dec,
                            fov,
                            mjd,
                            n_pix,
                            shape,
                            json.dumps(extra, ensure_ascii=False),
                            now,
                        ),
                    )
                    if existed:
                        st["rows_updated"] += 1
                    else:
                        st["rows_inserted"] += 1
                    st["files_ingested"] += 1
                except (HeaderError, OSError, ValueError, sqlite3.Error) as exc:
                    st["errors"] += 1
                    if st["errors"] <= 20:
                        self._log(f"  ! {path.name}: {exc}")
            self.conn.commit()
        self._log(f"[cutouts] {st['files_ingested']}/{len(files)} 个切割产品，错误 {st['errors']}")
        return st

    # ------------------------------------------------------------------ 兼容字段索引
    def ingest_fields_json(self, path: str | os.PathLike[str]) -> dict:
        payload = self._read_json(path)
        fields = payload.get("fields", [])
        keys = payload.get("keys") or [
            "obsid", "detector", "ra", "dec", "mjd", "lat", "lon", "elon", "elat", "xposure"
        ]
        idx = {k: i for i, k in enumerate(keys)}
        now = utcnow()
        with self._run("fields_json") as st:
            st["files_seen"] = 1
            st["bytes_read"] = Path(path).stat().st_size
            for row in fields:
                st["files_ingested"] += 1
                try:
                    obs_id = str(row[idx["obsid"]])
                    detector = int(row[idx["detector"]])
                except (KeyError, IndexError, TypeError, ValueError):
                    st["errors"] += 1
                    continue
                week, field_no, repeat_no = split_obs_id(obs_id)
                ra = float(row[idx["ra"]])
                dec = float(row[idx["dec"]])
                mjd = float(row[idx["mjd"]]) if row[idx["mjd"]] is not None else None
                release = self._week_release.get(week)
                existing = self.conn.execute(
                    "SELECT frame_id FROM frames WHERE obs_id=? AND detector=?", (obs_id, detector)
                ).fetchone()
                if existing:
                    self.conn.execute(
                        """UPDATE frames SET
                             sgt_lat=COALESCE(?,sgt_lat), sgt_lon=COALESCE(?,sgt_lon),
                             elon=COALESCE(?,elon), elat=COALESCE(?,elat),
                             xposure=COALESCE(?,xposure), release=COALESCE(release,?),
                             mjd=COALESCE(mjd,?), date_obs=COALESCE(date_obs,?),
                             updated_at=?
                           WHERE frame_id=?""",
                        (
                            _opt(row, idx, "lat"),
                            _opt(row, idx, "lon"),
                            _opt(row, idx, "elon"),
                            _opt(row, idx, "elat"),
                            _opt(row, idx, "xposure"),
                            release,
                            mjd,
                            mjd_to_iso(mjd),
                            now,
                            existing[0],
                        ),
                    )
                    st["rows_updated"] += 1
                else:
                    self._upsert_observation(
                        {
                            "obs_id": obs_id,
                            "week": week,
                            "field_no": field_no,
                            "repeat_no": repeat_no,
                            "mjd": mjd,
                            "date_obs": mjd_to_iso(mjd),
                        },
                        release,
                    )
                    self._upsert_frame(
                        {
                            "obs_id": obs_id,
                            "detector": detector,
                            "week": week,
                            "ra": ra,
                            "dec": dec,
                            "mjd": mjd,
                            "date_obs": mjd_to_iso(mjd),
                            "xposure": _opt(row, idx, "xposure"),
                            "sgt_lat": _opt(row, idx, "lat"),
                            "sgt_lon": _opt(row, idx, "lon"),
                            "elon": _opt(row, idx, "elon"),
                            "elat": _opt(row, idx, "elat"),
                        },
                        release=release,
                        blob_sha=None,
                        source_path=str(path),
                    )
                    st["rows_inserted"] += 1
            # 冻结 fields.json 契约：只保存导入时 fields.json 的行（顺序也保留），
            # T4 新增的帧不改变视图工具的旧文件。
            self.conn.execute("DELETE FROM fields_snapshot")
            snap = []
            for seq, row in enumerate(fields):
                try:
                    snap.append(
                        (
                            seq,
                            str(row[idx["obsid"]]),
                            int(row[idx["detector"]]),
                            float(row[idx["ra"]]),
                            float(row[idx["dec"]]),
                            float(row[idx["mjd"]]) if row[idx["mjd"]] is not None else None,
                            _opt(row, idx, "lat"),
                            _opt(row, idx, "lon"),
                            _opt(row, idx, "elon"),
                            _opt(row, idx, "elat"),
                            _opt(row, idx, "xposure"),
                        )
                    )
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
            self.conn.executemany(
                """INSERT INTO fields_snapshot(seq,obsid,detector,ra,dec,mjd,lat,lon,elon,elat,xposure)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                snap,
            )
            self.conn.commit()
        self.relink_epochs()
        self.refresh_week_counts()
        self._log(f"[fields] {st['files_ingested']} 行曝光索引，快照 {len(snap)} 行")
        return st

    # ------------------------------------------------------------------ 回填/汇总
    def backfill_date_obs(self) -> dict:
        """MJD -> UTC ISO，回填 frames / target_epochs / observations 的空 date_obs。"""
        changed = 0
        for table, key in (("frames", "frame_id"), ("target_epochs", "id"), ("observations", "obs_id")):
            rows = self.conn.execute(
                f"SELECT {key}, mjd FROM {table} WHERE (date_obs IS NULL OR date_obs='') AND mjd IS NOT NULL"
            ).fetchall()
            for row in rows:
                iso = mjd_to_iso(row[1])
                if iso:
                    self.conn.execute(f"UPDATE {table} SET date_obs=? WHERE {key}=?", (iso, row[0]))
                    changed += 1
        self.conn.commit()
        return {"rows_updated": changed}

    def relink_epochs(self) -> dict:
        """frames 晚于 target_epochs 导入时，补上 frame_id。"""
        cur = self.conn.execute(
            """UPDATE target_epochs SET frame_id=(
                   SELECT f.frame_id FROM frames f
                   WHERE f.obs_id=target_epochs.obs_id AND f.detector=target_epochs.detector
               )
               WHERE frame_id IS NULL"""
        )
        self.conn.commit()
        return {"rows_updated": cur.rowcount}

    def relink_cutouts(self) -> dict:
        """头文件晚于切割缓存导入时，把 cutouts 重新挂到 frames。"""
        cur = self.conn.execute(
            """UPDATE cutouts SET frame_id=(
                   SELECT f.frame_id FROM frames f
                   WHERE f.obs_id=cutouts.obs_id AND f.detector=cutouts.detector
               )
               WHERE frame_id IS NULL AND obs_id IS NOT NULL AND detector IS NOT NULL"""
        )
        self.conn.commit()
        return {"rows_updated": cur.rowcount}

    def backfill_releases(self) -> dict:
        updated = 0
        for row in self.conn.execute("SELECT week, release FROM weeks"):
            week, release = row[0], row[1]
            cur = self.conn.execute(
                "UPDATE observations SET release=?, release_source='week_index' "
                "WHERE week=? AND (release IS NULL OR release='')",
                (release, week),
            )
            updated += cur.rowcount
            cur = self.conn.execute(
                "UPDATE frames SET release=? WHERE week=? AND (release IS NULL OR release='')",
                (release, week),
            )
            updated += cur.rowcount
            self.conn.execute(
                """INSERT OR IGNORE INTO frame_releases(frame_id,release,procver)
                   SELECT f.frame_id, ?, w.version
                   FROM frames f JOIN weeks w ON w.week=f.week AND w.release=?
                   WHERE f.week=?""",
                (release, release, week),
            )
        self.conn.commit()
        self._refresh_week_map()
        return {"rows_updated": updated}

    def refresh_week_counts(self) -> dict:
        self.conn.execute(
            """UPDATE weeks SET
                 mjd_min=COALESCE((SELECT MIN(f.mjd) FROM frames f WHERE f.week=weeks.week), weeks.mjd_min),
                 mjd_max=COALESCE((SELECT MAX(f.mjd) FROM frames f WHERE f.week=weeks.week), weeks.mjd_max),
                 n_frames=(SELECT COUNT(*) FROM frames f WHERE f.week=weeks.week)"""
        )
        self.conn.execute(
            """UPDATE observations SET
                 mjd=COALESCE(mjd,(SELECT MIN(f.mjd) FROM frames f WHERE f.obs_id=observations.obs_id))"""
        )
        self.conn.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ 一键导入
    def import_all(
        self,
        root: str | os.PathLike[str],
        *,
        skip_headers: bool = False,
        with_fields: bool = True,
        with_samples: bool = False,
        limit: int | None = None,
    ) -> dict:
        root = Path(root).expanduser().resolve()
        results: dict[str, Any] = {}
        p = root / "app" / "data" / "week_index.json"
        if p.exists():
            results["weeks"] = self.ingest_week_index(p)
        p = root / "app" / "data" / "targets.json"
        if p.exists():
            results["targets"] = self.ingest_targets(p)
        if not skip_headers:
            p = root / "app" / "data" / "header_cache"
            if p.is_dir():
                results["headers"] = self.ingest_headers(p, limit=limit)
            else:
                self._log(f"[headers] 跳过：{p} 不存在（可用 --dir 指定头文件目录）")
                results["headers_skipped"] = str(p)
        p = root / "app" / "data" / "epoch_catalog.json"
        if p.exists():
            results["epochs"] = self.ingest_epoch_catalog(p)
        p = root / "app" / "data" / "epochcache"
        if p.is_dir():
            results["cutouts"] = self.ingest_cutouts(p, limit=limit)
        if with_fields:
            p = root / "视图工具" / "data" / "fields.json"
            if p.exists():
                results["fields"] = self.ingest_fields_json(p)
        if with_samples:
            p = root / "参考 资料" / "muestras"
            if p.is_dir():
                results["samples"] = self.ingest_cutouts(p)
        results["date_obs"] = self.backfill_date_obs()
        results["backfill"] = self.backfill_releases()
        results["relink_epochs"] = self.relink_epochs()
        results["relink"] = self.relink_cutouts()
        results["refresh"] = self.refresh_week_counts()
        self.conn.execute("PRAGMA optimize")
        self.conn.commit()
        return results


def _opt(row: list, idx: dict[str, int], key: str) -> float | None:
    try:
        i = idx[key]
        v = row[i]
        return None if v is None else float(v)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
