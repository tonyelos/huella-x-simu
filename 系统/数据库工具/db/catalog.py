"""查询层：锥搜索、覆盖统计、历元、统计信息。

所有读操作默认只读连接。HTTP 服务和 CLI 都复用它，避免两套 SQL。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .blobs import BlobStore
from .config import (
    API_SCHEMA_VERSION,
    COVERAGE_LABELS,
    COVERAGE_RADIUS_DEG,
    DEFAULT_BLOBS,
    DEFAULT_DB,
    MAX_CANDIDATES,
    MAX_PAGE_SIZE,
    READINESS_LABELS,
    TOOL_VERSION,
    TARGET_MATCH_RADIUS_DEG,
    ZONE_HEIGHT_DEG,
)
from .connection import connect, db_disk_bytes, sqlite_version, utcnow
from .timeutil import mjd_to_iso

IRSA_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"


def angsep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def zone_of(dec: float) -> int:
    z = int(math.floor((dec + 90.0) / ZONE_HEIGHT_DEG))
    return max(0, min(359, z))


def split_obs_id(obs_id: str) -> tuple[str, int | None, int | None]:
    parts = obs_id.rsplit("_", 2)
    if len(parts) != 3:
        return obs_id, None, None
    week, field, rep = parts
    try:
        return week, int(field), int(rep)
    except ValueError:
        return week, None, None


def build_fits_key(release: str | None, week: str | None, version: str | None, obs_id: str, detector: int | None) -> str | None:
    if not (release and week and version and detector is not None):
        return None
    _, field, rep = split_obs_id(obs_id)
    if field is None or rep is None:
        return None
    name = f"level2_{week}_{field:04d}_{rep}D{detector}_spx_{version}.fits"
    return f"{release}/level2/{week}/{version}/{detector}/{name}"


def build_slice_url(key: str | None, ra: float, dec: float, size_deg: float) -> str | None:
    if not key:
        return None
    return f"{IRSA_BASE}{key}?center={ra:.6f},{dec:.6f}d&size={size_deg:g}"


def readiness_level(n_epochs: int, baseline_days: float) -> str:
    if n_epochs <= 1:
        return "L0"
    if n_epochs == 2:
        return "L1"
    if n_epochs < 6:
        return "L2"
    return "L3" if baseline_days >= 180 else "L2"


def coverage_level(n_epochs: int, baseline_days: float) -> str:
    """数据库覆盖等级；与 app 的 L0–L3 分析就绪度语义分离。"""
    if n_epochs <= 1:
        return "C0"
    if n_epochs == 2:
        return "C1"
    if n_epochs < 6:
        return "C2"
    return "C3" if baseline_days >= 180 else "C2"


class Catalog:
    def __init__(
        self,
        db_path: str | os.PathLike[str] = DEFAULT_DB,
        *,
        readonly: bool = True,
        blob_root: str | os.PathLike[str] | None = None,
    ):
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.conn = connect(self.db_path, readonly=readonly)
        self.blobs = BlobStore(blob_root or DEFAULT_BLOBS)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ 基础
    def targets(self) -> list[dict]:
        out = []
        for r in self.conn.execute("SELECT * FROM targets ORDER BY official DESC, id"):
            d = dict(r)
            d["official"] = bool(d["official"])
            if d.get("truth_json"):
                try:
                    d["truth"] = json.loads(d["truth_json"])
                except json.JSONDecodeError:
                    d["truth"] = None
            if d.get("extra_json"):
                try:
                    d.update({k: v for k, v in json.loads(d["extra_json"]).items() if k not in d})
                except json.JSONDecodeError:
                    pass
            d.pop("truth_json", None)
            d.pop("extra_json", None)
            out.append(d)
        return out

    def target(self, target_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["official"] = bool(d["official"])
        if d.get("truth_json"):
            try:
                d["truth"] = json.loads(d["truth_json"])
            except json.JSONDecodeError:
                d["truth"] = None
        if d.get("extra_json"):
            try:
                d.update({k: v for k, v in json.loads(d["extra_json"]).items() if k not in d})
            except json.JSONDecodeError:
                pass
        d.pop("truth_json", None)
        d.pop("extra_json", None)
        return d

    def weeks(self, release: str | None = None) -> list[dict]:
        sql = "SELECT * FROM weeks"
        args: list[Any] = []
        if release:
            sql += " WHERE release=?"
            args.append(release)
        sql += " ORDER BY week"
        return [dict(r) for r in self.conn.execute(sql, args)]

    def _frame_row(self, r: sqlite3.Row, qra: float, qdec: float, size_deg: float) -> dict:
        d = dict(r)
        d["sep_deg"] = round(angsep_deg(qra, qdec, d["ra"], d["dec"]), 6)
        d["date_obs"] = d.get("date_obs") or mjd_to_iso(d.get("mjd"))
        d["key"] = build_fits_key(d.get("release"), d.get("week"), d.get("version"), d["obs_id"], d.get("detector"))
        d["slice_url"] = build_slice_url(d["key"], qra, qdec, size_deg)
        return d

    # ------------------------------------------------------------------ 锥搜索
    def cone(
        self,
        ra: float,
        dec: float,
        radius_deg: float = 0.2,
        *,
        limit: int = 100,
        offset: int = 0,
        order: str = "sep",
        detector: Iterable[int] | int | None = None,
        release: str | None = None,
        mjd_min: float | None = None,
        mjd_max: float | None = None,
        size_deg: float = 0.2,
        include_cutouts: bool = False,
    ) -> dict:
        """按天区锥搜索。先 zone/RA 粗筛，再精确大圆距离过滤。"""
        ra = float(ra) % 360.0
        dec = float(dec)
        radius = float(radius_deg)
        if not (-90.0 <= dec <= 90.0):
            raise ValueError("dec 必须在 [-90, 90]")
        if not (0.0 < radius <= 180.0):
            raise ValueError("radius 必须在 (0, 180]")
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        offset = max(0, int(offset))

        dec_min = max(-90.0, dec - radius)
        dec_max = min(90.0, dec + radius)
        z0, z1 = zone_of(dec_min), zone_of(dec_max)

        where = ["f.dec BETWEEN ? AND ?", "f.zone BETWEEN ? AND ?"]
        args: list[Any] = [dec_min, dec_max, z0, z1]

        if radius < 179.0:
            ra_width = min(180.0, radius / max(math.cos(math.radians(dec)), 0.02))
            if ra_width < 180.0:
                lo = (ra - ra_width) % 360.0
                hi = (ra + ra_width) % 360.0
                if lo <= hi:
                    where.append("f.ra BETWEEN ? AND ?")
                    args.extend([lo, hi])
                else:
                    where.append("(f.ra >= ? OR f.ra <= ?)")
                    args.extend([lo, hi])

        dets: list[int] = []
        if detector is not None:
            dets = [int(x) for x in (detector if isinstance(detector, (list, tuple, set)) else [detector])]
            if dets:
                where.append("f.detector IN (%s)" % ",".join("?" for _ in dets))
                args.extend(dets)
        if release:
            where.append(
                "(COALESCE(f.release, o.release)=? OR "
                "EXISTS (SELECT 1 FROM frame_releases fr WHERE fr.frame_id=f.frame_id AND fr.release=?))"
            )
            args.extend([release, release])
        if mjd_min is not None:
            where.append("f.mjd >= ?")
            args.append(float(mjd_min))
        if mjd_max is not None:
            where.append("f.mjd <= ?")
            args.append(float(mjd_max))

        sql = f"""
            SELECT f.frame_id, f.obs_id, f.detector,
                   COALESCE(f.release, o.release) AS release,
                   f.week, f.ra, f.dec, f.mjd, f.date_obs, f.xposure,
                   f.sgt_lat, f.sgt_lon, f.elon, f.elat,
                   f.naxis1, f.naxis2, f.pixel_scale_arcsec, f.psf_fwhm_arcsec,
                   f.l2dqa, f.header_sha256, f.source_path,
                   o.field_no, o.repeat_no, w.version
            FROM frames f
            JOIN observations o ON o.obs_id = f.obs_id
            LEFT JOIN weeks w ON w.week = f.week AND w.release = COALESCE(f.release, o.release)
            WHERE {' AND '.join(where)}
            ORDER BY f.mjd ASC
            LIMIT ?
        """
        rows = self.conn.execute(sql, (*args, MAX_CANDIDATES + 1)).fetchall()
        truncated = len(rows) > MAX_CANDIDATES
        if truncated:
            rows = rows[:MAX_CANDIDATES]

        hits: list[dict] = []
        for r in rows:
            sep = angsep_deg(ra, dec, r["ra"], r["dec"])
            if sep <= radius + 1e-9:
                hits.append(self._frame_row(r, ra, dec, size_deg))

        if order == "mjd":
            hits.sort(key=lambda d: (d.get("mjd") or 0.0, d.get("detector") or 0))
        else:
            hits.sort(key=lambda d: (d["sep_deg"], d.get("mjd") or 0.0, d.get("detector") or 0))

        total = len(hits)
        page = hits[offset : offset + limit]
        if include_cutouts and page:
            ids = [d["frame_id"] for d in page]
            cuts = self._cutouts_for_frame_ids(ids)
            for d in page:
                d["cutouts"] = cuts.get(d["frame_id"], [])

        return {
            "query": {
                "ra": round(ra, 6),
                "dec": round(dec, 6),
                "radius_deg": radius,
                "size_deg": size_deg,
                "order": order,
                "offset": offset,
                "limit": limit,
                "release": release,
                "detector": dets or None,
                "mjd_min": mjd_min,
                "mjd_max": mjd_max,
            },
            "total": total,
            "count": len(page),
            "truncated": truncated,
            "frames": page,
        }

    # ------------------------------------------------------------------ 覆盖
    def coverage(self, ra: float, dec: float, radius_deg: float = COVERAGE_RADIUS_DEG, *, size_deg: float = 0.2) -> dict:
        """天区覆盖 + 数据就绪度（按 0.02 天去重同一曝光的多探测器）。"""
        res = self.cone(ra, dec, radius_deg, limit=MAX_PAGE_SIZE, order="mjd", size_deg=size_deg)
        frames = list(res["frames"])
        # cone 有单页上限；覆盖统计需要全部候选，按页取完（最多 20 页，防病态查询）。
        offset = len(frames)
        for _ in range(20):
            if offset >= res["total"]:
                break
            part = self.cone(
                ra, dec, radius_deg, limit=MAX_PAGE_SIZE, offset=offset, order="mjd", size_deg=size_deg
            )
            if not part["frames"]:
                break
            frames.extend(part["frames"])
            offset += len(part["frames"])

        # 多探测器同一曝光去重：保留离查询点最近的那个。
        dedup: list[dict] = []
        for f in sorted(frames, key=lambda d: (d.get("mjd") or 0.0, d.get("sep_deg") or 9)):
            if dedup and abs((f.get("mjd") or 0.0) - (dedup[-1].get("mjd") or 0.0)) <= 0.02:
                if f["sep_deg"] < dedup[-1]["sep_deg"]:
                    dedup[-1] = f
            else:
                dedup.append(f)

        mjds = [f["mjd"] for f in dedup if f.get("mjd") is not None]
        baseline = (max(mjds) - min(mjds)) if len(mjds) >= 2 else 0.0
        n_epochs = len(dedup)
        level = coverage_level(n_epochs, baseline)
        analysis_level = readiness_level(n_epochs, baseline)

        return {
            "query": {"ra": round(float(ra) % 360.0, 6), "dec": float(dec), "radius_deg": float(radius_deg)},
            "radius_deg": float(radius_deg),
            "n_frames": len(frames),
            "n_epochs": n_epochs,
            "n_observations": len({f["obs_id"] for f in dedup}),
            "n_weeks": len({f["week"] for f in dedup if f.get("week")}),
            "n_releases": len({f.get("release") for f in dedup if f.get("release")}),
            "releases": sorted({f.get("release") for f in dedup if f.get("release")}),
            "detectors": sorted({f.get("detector") for f in dedup if f.get("detector") is not None}),
            "mjd_min": min(mjds) if mjds else None,
            "mjd_max": max(mjds) if mjds else None,
            "baseline_days": round(baseline, 3),
            "coverage_level": level,
            "coverage_label": COVERAGE_LABELS[level],
            "header_available": any(f.get("header_sha256") for f in frames),
            "n_local_cutouts": self._count_cutouts_for_frames(frames),
            # 旧字段：app 不再应该使用它；保留一个版本兼容旧调用方。
            "readiness": {
                "level": analysis_level,
                "label": READINESS_LABELS[analysis_level],
                "deprecated": True,
                "note": "覆盖等级请用 coverage_level；analysis_readiness 由 MoveScope 管线给出。",
            },
            "nearest": dedup[:5],
        }

    # ------------------------------------------------------------------ 历元
    def epochs_for_target(self, target_id: str, *, limit: int = 500, fallback: bool = True) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT e.*, w.version, f.ra AS frame_ra, f.dec AS frame_dec
            FROM target_epochs e
            LEFT JOIN weeks w ON w.week=e.week AND w.release=e.release
            LEFT JOIN frames f ON f.frame_id=e.frame_id
            WHERE e.target_id=?
            ORDER BY e.mjd ASC, e.detector ASC
            LIMIT ?
            """,
            (target_id, int(limit)),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            d["frame_present"] = d.get("frame_id") is not None
            d["version"] = d.get("version")
            d["kind"] = "catalog"
            out.append(d)
        if not out and fallback:
            t = self.target(target_id)
            if t:
                res = self.cone(t["ra"], t["dec"], COVERAGE_RADIUS_DEG, limit=limit, order="mjd")
                for f in res["frames"]:
                    out.append(
                        {
                            "target_id": target_id,
                            "release": f.get("release"),
                            "week": f.get("week"),
                            "version": f.get("version"),
                            "obs_id": f["obs_id"],
                            "detector": f["detector"],
                            "mjd": f.get("mjd"),
                            "date_obs": f.get("date_obs"),
                            "sep_deg": f["sep_deg"],
                            "fits_key": f.get("key"),
                            "slice_url": f.get("slice_url"),
                            "frame_id": f["frame_id"],
                            "frame_present": True,
                            "kind": "cone",
                        }
                    )
        return out

    # ------------------------------------------------------------------ v1 EpochRef / Coverage
    def _count_cutouts_for_pairs(self, pairs: Iterable[tuple[str, int]]) -> int:
        """按 (obs_id, detector) 统计本地切割产品；分块避免 SQLite 参数上限。"""
        clean = sorted({(str(o), int(d)) for o, d in pairs if o and d is not None})
        total = 0
        for i in range(0, len(clean), 300):
            chunk = clean[i : i + 300]
            clauses = " OR ".join("(obs_id=? AND detector=?)" for _ in chunk)
            args: list[Any] = []
            for o, d in chunk:
                args.extend([o, d])
            total += int(
                self.conn.execute(
                    f"SELECT COUNT(DISTINCT cutout_id) FROM cutouts WHERE {clauses}", args
                ).fetchone()[0]
                or 0
            )
        return total

    def _count_cutouts_for_frames(self, frames: Iterable[dict]) -> int:
        return self._count_cutouts_for_pairs(
            [(f.get("obs_id"), f.get("detector")) for f in frames]  # type: ignore[misc]
        )

    def resolve_target(self, ra: float, dec: float, *, tol: float = TARGET_MATCH_RADIUS_DEG) -> dict | None:
        """把任意坐标匹配到 0.05° 内的目标；用于 target_id / ra-dec 两入口同形。"""
        best: dict | None = None
        best_sep = float(tol)
        for t in self.targets():
            sep = angsep_deg(ra, dec, float(t["ra"]), float(t["dec"]))
            if sep <= best_sep:
                best, best_sep = t, sep
        return best

    def _epoch_from_frame(self, f: dict, ref_ra: float, ref_dec: float, fov: float) -> dict:
        mjd = f.get("mjd")
        return {
            "obsid": f["obs_id"],
            "detector": int(f["detector"]),
            "release": f.get("release"),
            "week": f.get("week"),
            "version": f.get("version"),
            "mjd": mjd,
            "date_obs": f.get("date_obs") or mjd_to_iso(mjd),
            "sep_deg": round(float(f.get("sep_deg") or 0.0), 6),
            "key": f.get("key"),
            "slice_url": f.get("slice_url") or build_slice_url(f.get("key"), ref_ra, ref_dec, fov),
            "provenance": "real",
            "frame_id": f.get("frame_id"),
            "header_sha256": f.get("header_sha256"),
        }

    def _curated_epochs(self, target: dict, fov: float, release: str | None) -> list[dict]:
        sql = """
            SELECT e.*, w.version, f.header_sha256,
                   f.ra AS frame_ra, f.dec AS frame_dec
            FROM target_epochs e
            LEFT JOIN weeks w ON w.week=e.week AND w.release=e.release
            LEFT JOIN frames f ON f.frame_id=e.frame_id
            WHERE e.target_id=?
        """
        args: list[Any] = [target["id"]]
        if release:
            sql += " AND e.release=?"
            args.append(release)
        sql += " ORDER BY e.mjd ASC, e.detector ASC"
        refs: list[dict] = []
        for r in self.conn.execute(sql, args):
            key = r["fits_key"] or build_fits_key(
                r["release"], r["week"], r["version"], r["obs_id"], r["detector"]
            )
            if not key:
                continue
            mjd = r["mjd"]
            sep = r["sep_deg"]
            if sep is None and r["frame_ra"] is not None and r["frame_dec"] is not None:
                sep = angsep_deg(float(target["ra"]), float(target["dec"]), r["frame_ra"], r["frame_dec"])
            refs.append(
                {
                    "obsid": r["obs_id"],
                    "detector": int(r["detector"]) if r["detector"] is not None else None,
                    "release": r["release"],
                    "week": r["week"],
                    "version": r["version"],
                    "mjd": mjd,
                    "date_obs": r["date_obs"] or mjd_to_iso(mjd),
                    "sep_deg": round(float(sep or 0.0), 6),
                    "key": key,
                    "slice_url": build_slice_url(key, float(target["ra"]), float(target["dec"]), fov),
                    "provenance": "real",
                    "frame_id": r["frame_id"],
                    "header_sha256": r["header_sha256"],
                }
            )
        return refs

    def _cone_epochs(self, ra: float, dec: float, radius: float, fov: float, release: str | None) -> list[dict]:
        frames: list[dict] = []
        offset = 0
        for _ in range(20):
            part = self.cone(
                ra, dec, radius, limit=MAX_PAGE_SIZE, offset=offset,
                order="mjd", release=release, size_deg=fov,
            )
            frames.extend(part["frames"])
            offset += len(part["frames"])
            if offset >= part["total"] or not part["frames"]:
                break
        return [self._epoch_from_frame(f, ra, dec, fov) for f in frames]

    @staticmethod
    def dedupe_epoch_refs(refs: Iterable[dict], *, tol: float = 0.02) -> list[dict]:
        """每个 obsid 一条 EpochRef；同一曝光的多探测器保留 sep_deg 最小者。

        说明：`obsid` 本身是“一次曝光”的身份，多探测器共享同一 mjd。验收要求
        WISE 0855 的 10 个不同 obsid 都保留（其中两个相隔仅 0.003 d），所以这里
        不把“不同 obsid 但 mjd 接近”合法曝光合并；`tol` 仅用于同一 obsid 内的
        极端时间抖动容差，保留参数是为了兼容旧调用。
        """
        rows = [r for r in refs if r.get("mjd") is not None]
        rows.sort(key=lambda r: (float(r["mjd"]), int(r.get("detector") or 0)))
        by_obs: dict[str, dict] = {}
        for r in rows:
            obsid = str(r["obsid"])
            cur = by_obs.get(obsid)
            if cur is None:
                by_obs[obsid] = r
                continue
            # 同一 obsid 的多探测器：取离查询点最近的一条；时间差超过 tol 的
            # 异常重复也保留更近的那条，避免同一曝光出现两条 DTO。
            if float(r.get("sep_deg") or 9.0) < float(cur.get("sep_deg") or 9.0):
                by_obs[obsid] = r
        out = list(by_obs.values())
        out.sort(key=lambda r: (float(r["mjd"]), int(r.get("detector") or 0)))
        return out

    def _coverage_from_refs(
        self,
        refs_all: list[dict],
        refs_unique: list[dict],
        ra: float,
        dec: float,
        radius: float,
        source: str,
    ) -> dict:
        mjds = [float(r["mjd"]) for r in refs_unique if r.get("mjd") is not None]
        baseline = (max(mjds) - min(mjds)) if len(mjds) >= 2 else 0.0
        n_epochs = len(refs_unique)
        level = coverage_level(n_epochs, baseline)
        return {
            "ra": round(float(ra) % 360.0, 6),
            "dec": round(float(dec), 6),
            "radius_deg": round(float(radius), 3),
            "n_frames": len(refs_all),
            "n_epochs": n_epochs,
            "n_observations": len({r["obsid"] for r in refs_unique}),
            "n_weeks": len({r.get("week") for r in refs_unique if r.get("week")}),
            "n_releases": len({r.get("release") for r in refs_unique if r.get("release")}),
            "releases": sorted({r.get("release") for r in refs_unique if r.get("release")}),
            "detectors": sorted({int(r["detector"]) for r in refs_all if r.get("detector") is not None}),
            "mjd_min": min(mjds) if mjds else None,
            "mjd_max": max(mjds) if mjds else None,
            "baseline_days": round(baseline, 3),
            "coverage_level": level,
            "coverage_label": COVERAGE_LABELS[level],
            "header_available": any(r.get("header_sha256") for r in refs_all),
            "n_local_cutouts": self._count_cutouts_for_pairs(
                [(r.get("obsid"), r.get("detector")) for r in refs_all]  # type: ignore[misc]
            ),
            "source": source,
        }

    def epochs_v1(
        self,
        *,
        target_id: str | None = None,
        ra: float | None = None,
        dec: float | None = None,
        fov: float = 0.25,
        radius: float = COVERAGE_RADIUS_DEG,
        release: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        """契约 v1：target_id 与 ra/dec 两入口返回同一组 EpochRef。"""
        matched: dict | None = None
        qra: float
        qdec: float
        if target_id:
            matched = self.target(target_id)
            if not matched:
                raise KeyError(target_id)
            qra, qdec = float(matched["ra"]), float(matched["dec"])
            if ra is not None and dec is not None:
                if angsep_deg(float(ra) % 360.0, float(dec), qra, qdec) > TARGET_MATCH_RADIUS_DEG:
                    raise ValueError("ra/dec 与 target_id 不在 0.05° 内")
        elif ra is not None and dec is not None:
            qra, qdec = float(ra) % 360.0, float(dec)
            matched = self.resolve_target(qra, qdec)
            if matched:
                qra, qdec = float(matched["ra"]), float(matched["dec"])
        else:
            raise ValueError("需要 target_id，或 ra 与 dec")

        refs_all: list[dict]
        source: str
        if matched:
            refs_all = self._curated_epochs(matched, fov, release)
            refs_unique = self.dedupe_epoch_refs(refs_all)
            source = "curated"
        else:
            refs_all = []
            refs_unique = []
            source = "frames"
        if not refs_unique:
            refs_all = self._cone_epochs(qra, qdec, radius, fov, release)
            refs_unique = self.dedupe_epoch_refs(refs_all)
            source = "frames"
        cov = self._coverage_from_refs(refs_all, refs_unique, qra, qdec, radius, source)
        page = refs_unique[offset : offset + limit]
        return {
            "schema_version": API_SCHEMA_VERSION,
            "query": {
                "target_id": target_id,
                "ra": round(qra, 6),
                "dec": round(qdec, 6),
                "fov": float(fov),
                "radius": float(radius),
                "release": release,
                "limit": int(limit),
                "offset": int(offset),
            },
            "matched_target_id": matched["id"] if matched else None,
            "coverage": cov,
            "epochs": page,
            "count": len(refs_unique),
            "returned": len(page),
            "generated_at": utcnow(),
        }

    # ------------------------------------------------------------------ 单帧/切割
    def frame(self, obs_id: str, detector: int) -> dict | None:
        r = self.conn.execute(
            """
            SELECT f.*, COALESCE(f.release,o.release) AS release_eff,
                   o.field_no, o.repeat_no, w.version
            FROM frames f
            JOIN observations o ON o.obs_id=f.obs_id
            LEFT JOIN weeks w ON w.week=f.week AND w.release=COALESCE(f.release,o.release)
            WHERE f.obs_id=? AND f.detector=?
            """,
            (obs_id, int(detector)),
        ).fetchone()
        if not r:
            return None
        d = dict(r)
        d["release"] = d.pop("release_eff", d.get("release"))
        d["key"] = build_fits_key(d.get("release"), d.get("week"), d.get("version"), d["obs_id"], d["detector"])
        d["cutouts"] = self._cutouts_for_frame_ids([d["frame_id"]]).get(d["frame_id"], [])
        return d

    def _cutouts_for_frame_ids(self, ids: list[int]) -> dict[int, list[dict]]:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM cutouts WHERE frame_id IN ({marks}) ORDER BY created_at",
            ids,
        ).fetchall()
        out: dict[int, list[dict]] = {}
        for r in rows:
            d = dict(r)
            if d.get("meta_json"):
                try:
                    d["meta"] = json.loads(d["meta_json"])
                except json.JSONDecodeError:
                    d["meta"] = None
            out.setdefault(d["frame_id"], []).append(d)
        return out

    def cutouts(self, *, obs_id: str | None = None, frame_id: int | None = None) -> list[dict]:
        if obs_id:
            rows = self.conn.execute(
                """SELECT * FROM cutouts WHERE obs_id=? OR frame_id IN
                   (SELECT frame_id FROM frames WHERE obs_id=?)
                   ORDER BY created_at""",
                (obs_id, obs_id),
            ).fetchall()
        elif frame_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM cutouts WHERE frame_id=? ORDER BY created_at", (int(frame_id),)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM cutouts ORDER BY created_at DESC LIMIT 500").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("meta_json"):
                try:
                    d["meta"] = json.loads(d["meta_json"])
                except json.JSONDecodeError:
                    d["meta"] = None
            out.append(d)
        return out

    # ------------------------------------------------------------------ 统计
    def stats(self) -> dict:
        c = self.conn
        counts = {
            "frames": c.execute("SELECT COUNT(*) FROM frames").fetchone()[0],
            "observations": c.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "weeks": c.execute("SELECT COUNT(*) FROM weeks").fetchone()[0],
            "targets": c.execute("SELECT COUNT(*) FROM targets").fetchone()[0],
            "epochs": c.execute("SELECT COUNT(*) FROM target_epochs").fetchone()[0],
            "cutouts": c.execute("SELECT COUNT(*) FROM cutouts").fetchone()[0],
            "header_frames": c.execute(
                "SELECT COUNT(*) FROM frames WHERE header_sha256 IS NOT NULL"
            ).fetchone()[0],
            "frames_with_cutouts": c.execute(
                "SELECT COUNT(DISTINCT frame_id) FROM cutouts WHERE frame_id IS NOT NULL"
            ).fetchone()[0],
        }
        tr = c.execute("SELECT MIN(mjd), MAX(mjd) FROM frames").fetchone()
        releases = [
            {"release": r[0], "frames": r[1], "weeks": r[2]}
            for r in c.execute(
                """SELECT COALESCE(f.release,o.release) AS rel, COUNT(*), COUNT(DISTINCT f.week)
                   FROM frames f JOIN observations o ON o.obs_id=f.obs_id
                   GROUP BY rel ORDER BY 2 DESC"""
            )
        ]
        detectors = [
            {"detector": r[0], "frames": r[1]}
            for r in c.execute("SELECT detector, COUNT(*) FROM frames GROUP BY detector ORDER BY detector")
        ]
        top_weeks = [
            {"week": r[0], "release": r[1], "frames": r[2]}
            for r in c.execute(
                "SELECT week, release, COUNT(*) FROM frames GROUP BY week, release ORDER BY 3 DESC LIMIT 10"
            )
        ]
        last_runs = [
            dict(r)
            for r in c.execute("SELECT * FROM ingest_runs ORDER BY run_id DESC LIMIT 5")
        ]
        blob_stats = self.blobs.stats(c)
        return {
            "db": {
                "path": self.db_path,
                "disk_bytes": db_disk_bytes(self.db_path),
                "sqlite_version": sqlite_version(),
                "schema_version": self._schema_version(),
                "api_schema_version": API_SCHEMA_VERSION,
            },
            "counts": counts,
            "time": {
                "mjd_min": tr[0],
                "mjd_max": tr[1],
                "baseline_days": round((tr[1] - tr[0]), 3) if tr[0] is not None and tr[1] is not None else 0,
            },
            "releases": releases,
            "detectors": detectors,
            "top_weeks": top_weeks,
            "storage": blob_stats,
            "last_ingests": last_runs,
        }

    def _schema_version(self) -> int:
        try:
            r = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            return int(r[0]) if r else 0
        except sqlite3.Error:
            return 0

    def health(self) -> dict:
        s = self.stats()
        try:
            snapshot = self.conn.execute("SELECT MAX(finished_at) FROM ingest_runs").fetchone()[0]
        except sqlite3.Error:
            snapshot = None
        return {
            "ok": True,
            "service": "spherex-db",
            "version": TOOL_VERSION,
            "schema_version": API_SCHEMA_VERSION,
            "db_schema_version": s["db"]["schema_version"],
            "data_snapshot": snapshot,
            "counts": s["counts"],
            "time": s["time"],
        }

    # ------------------------------------------------------------------ 维护
    def integrity_check(self, deep: bool = False) -> dict:
        quick = self.conn.execute("PRAGMA quick_check").fetchone()[0]
        fk = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        out = {
            "quick_check": quick,
            "foreign_key_violations": len(fk),
            "foreign_keys": [dict(r) for r in fk[:20]],
        }
        if deep:
            missing = 0
            bad = 0
            rows = self.conn.execute(
                "SELECT sha256, codec FROM blobs ORDER BY random() LIMIT 200"
            ).fetchall()
            for sha, codec in rows:
                if not self.blobs.exists(sha, codec):
                    missing += 1
                elif not self.blobs.verify(sha, codec):
                    bad += 1
            out["sampled_blobs"] = len(rows)
            out["missing_blobs"] = missing
            out["corrupt_blobs"] = bad
        return out
