"""星表查询/导入/匹配核心（阶段 B/D1）。

边界（刻意保持）：

- 这里只回答“星表里有哪些源、和给定位置差多少/是否多源歧义”；
  **不输出 known_star / uncatalogued / 新恒星等科学分类**，那是 app 的 D2–D4。
- `release` 是星表快照，不是 SPHEREx 处理版本；响应永远带 release/version/许可。
- 本地缓存不完整时返回 `complete=false`，缓存完全没有覆盖时抛
  `CatalogUnavailable`（HTTP 映射为 503 `CATALOG_UNAVAILABLE`），绝不把
  “查不到”说成“没有这个源/新源”。

坐标传播：Gaia DR3 的位置在 J2016.0（MJD 57388.5）。给定观测 MJD 时用
`pmra/pmdec` 做小角度线性传播，返回 `ra_propagated/dec_propagated`；不传播
视差（需要视差因子/太阳位置，D2 再由 app 做）。
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .catalog import angsep_deg, finite_scalar
from .connection import connect, transaction, utcnow
from .healpix import ang2pix_nest
from .star_config import (
    CATALOG_API_VERSION,
    DEFAULT_AMBIGUITY_ARCSEC,
    DEFAULT_MATCH_RADIUS_ARCSEC,
    DEFAULT_STAR_DB,
    FIELD_CACHE_RADIUS_DEG,
    GAIA_EPOCH_MJD,
    GAIA_EPOCH_REF,
    HEALPIX_NSIDE,
    JULIAN_YEAR_DAYS,
    MAS_PER_DEG,
    MAX_CONE_LIMIT,
    MAX_MATCH_DETECTIONS,
    MAX_MATCH_RADIUS_ARCSEC,
    MAX_QUERY_RADIUS_DEG,
    SERVICE_NAME,
    STAR_SCHEMA_VERSION,
)
from .star_schema import apply_star_schema, check_star_schema

MAX_SOURCES = 200_000


class CatalogError(Exception):
    """星表服务的基类异常。"""


class CatalogUnavailable(CatalogError):
    """星表不可用/未缓存：HTTP 层必须映射成 503 CATALOG_UNAVAILABLE。"""

    def __init__(
        self,
        message: str,
        *,
        catalog: str | None = None,
        version: str | None = None,
        reason: str = "catalog_unavailable",
    ):
        super().__init__(message)
        self.message = message
        self.catalog = catalog
        self.version = version
        self.reason = reason

    def as_dict(self) -> dict:
        return {
            "code": "CATALOG_UNAVAILABLE",
            "message": self.message,
            "reason": self.reason,
            "catalog": self.catalog,
            "version": self.version,
        }


class CatalogValidationError(ValueError):
    """参数/请求体非法（HTTP 400）。"""


def _finite(value: Any, name: str) -> float:
    try:
        return finite_scalar(value, name)
    except ValueError as exc:
        raise CatalogValidationError(str(exc)) from exc


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def propagate_pm(
    ra: float,
    dec: float,
    pmra: float | None,
    pmdec: float | None,
    dt_years: float,
) -> tuple[float, float]:
    """把 (ra, dec) 按 pmra/pmdec [mas/yr] 传播 dt_years 年（小角度）。"""
    if not dt_years or (not pmra and not pmdec):
        return ra % 360.0, dec
    cosd = max(math.cos(math.radians(dec)), 1e-6)
    dra = (pmra or 0.0) * dt_years / (MAS_PER_DEG * cosd)
    ddec = (pmdec or 0.0) * dt_years / MAS_PER_DEG
    new_dec = max(-90.0, min(90.0, dec + ddec))
    return (ra + dra) % 360.0, new_dec


def angsep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两点大圆距离（角秒）；非有限值返回 inf（匹配时不会误配）。"""
    sep = angsep_deg(ra1, dec1, ra2, dec2)
    return sep * 3600.0 if math.isfinite(sep) else math.inf


def _release_dict(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    d["release"] = d.get("release_key") or d.get("release")
    d["epoch_ref_mjd"] = d.get("epoch_mjd")
    d["citation"] = d.get("citation")
    d["n_sources"] = int(d.get("n_sources") or 0)
    d["n_sources_actual"] = int(d.get("n_sources_actual") or 0)
    d["n_fields"] = int(d.get("n_fields") or 0)
    return d


def _source_dict(
    row: sqlite3.Row,
    release: dict,
    *,
    epoch_mjd: float | None = None,
    sep_arcsec: float | None = None,
    ambiguous: bool | None = None,
    n_within: int | None = None,
) -> dict:
    src = {
        "source_id": row["source_id"],
        "catalog": release["catalog"],
        "catalog_id": row["catalog_id"],
        "release": release["release"],
        "version": release["version"],
        "ra": row["ra"],
        "dec": row["dec"],
        "pmra": row["pmra"],
        "pmdec": row["pmdec"],
        "parallax": row["parallax"],
        "pmra_error": row["pmra_error"],
        "pmdec_error": row["pmdec_error"],
        "ra_error": row["ra_error"],
        "dec_error": row["dec_error"],
        "gmag": row["gmag"],
        "bp_rp": row["bp_rp"],
        "2mass": row["twomass_id"],
        "wise": row["wise_id"],
        "jmag": row["jmag"],
        "hmag": row["hmag"],
        "kmag": row["kmag"],
        "w1": row["w1"],
        "w2": row["w2"],
        "object_type": row["object_type"],
        "healpix": row["healpix"],
        "epoch_ref": row["epoch_ref"],
        "provenance": row["provenance"],
    }
    if epoch_mjd is not None:
        ref = row["epoch_ref"] if row["epoch_ref"] is not None else release["epoch_mjd"]
        dt_years = (epoch_mjd - float(ref)) / JULIAN_YEAR_DAYS
        ra_p, dec_p = propagate_pm(
            row["ra"], row["dec"], row["pmra"], row["pmdec"], dt_years
        )
        src["ra_propagated"] = round(ra_p, 9)
        src["dec_propagated"] = round(dec_p, 9)
        src["epoch"] = float(epoch_mjd)
    else:
        src["ra_propagated"] = row["ra"]
        src["dec_propagated"] = row["dec"]
        src["epoch"] = None
    if sep_arcsec is not None:
        src["sep_arcsec"] = round(float(sep_arcsec), 4)
    if ambiguous is not None:
        src["ambiguous"] = bool(ambiguous)
    if n_within is not None:
        src["n_within_ambiguity_arcsec"] = int(n_within)
    return src


class StarCatalog:
    """只读/可写星表缓存。HTTP 服务和离线 CLI 共用这一条代码路径。"""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_STAR_DB,
        *,
        readonly: bool = True,
    ):
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.readonly = bool(readonly)
        self.conn = connect(self.db_path, readonly=self.readonly)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "StarCatalog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ 版本/统计
    def schema_version(self) -> int:
        return check_star_schema(self.conn)

    def releases(self, catalog: str | None = None) -> list[dict]:
        sql = """
            SELECT r.*,
                   (SELECT COUNT(*) FROM catalog_source s WHERE s.release_id=r.release_id)
                       AS n_sources_actual,
                   (SELECT COUNT(*) FROM catalog_field f WHERE f.release_id=r.release_id)
                       AS n_fields
            FROM catalog_release r
        """
        args: list[Any] = []
        if catalog:
            sql += " WHERE r.catalog=?"
            args.append(catalog)
        sql += " ORDER BY r.imported_at DESC, r.release_id DESC"
        return [_release_dict(r) for r in self.conn.execute(sql, args)]

    def fields(self, release_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM catalog_field WHERE release_id=? ORDER BY field_id",
            (int(release_id),),
        )
        return [dict(r) for r in rows]

    def get_release(self, catalog: str = "gaia", version: str | None = None) -> dict:
        if not catalog:
            raise CatalogValidationError("catalog 不能为空")
        if version:
            row = self.conn.execute(
                "SELECT * FROM catalog_release WHERE catalog=? AND version=?",
                (catalog, version),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM catalog_release WHERE catalog=? "
                "ORDER BY imported_at DESC, release_id DESC LIMIT 1",
                (catalog,),
            ).fetchone()
        if row is None:
            raise CatalogUnavailable(
                f"星表 {catalog}"
                + (f" version={version}" if version else "")
                + " 尚未导入本地缓存",
                catalog=catalog,
                version=version,
                reason="catalog_not_imported",
            )
        release = _release_dict(row)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM catalog_source WHERE release_id=?", (release["release_id"],)
        ).fetchone()[0]
        release["n_sources_actual"] = int(n)
        if n == 0:
            raise CatalogUnavailable(
                f"星表 {release['release']} 存在但没有源（空 release）",
                catalog=catalog,
                version=release["version"],
                reason="release_empty",
            )
        return release

    def stats(self) -> dict:
        c = self.conn
        counts = {
            "releases": c.execute("SELECT COUNT(*) FROM catalog_release").fetchone()[0],
            "sources": c.execute("SELECT COUNT(*) FROM catalog_source").fetchone()[0],
            "fields": c.execute("SELECT COUNT(*) FROM catalog_field").fetchone()[0],
            "matches": c.execute("SELECT COUNT(*) FROM catalog_match").fetchone()[0],
            "import_runs": c.execute("SELECT COUNT(*) FROM catalog_import_run").fetchone()[0],
        }
        size = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.db_path + suffix)
            if p.exists():
                size += p.stat().st_size
        return {
            "db": {
                "path": self.db_path,
                "disk_bytes": size,
                "schema_version": self.schema_version(),
                "api_schema_version": CATALOG_API_VERSION,
            },
            "counts": counts,
            "releases": self.releases(),
            "generated_at": utcnow(),
        }

    def health(self) -> dict:
        stats = self.stats()
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "schema_version": CATALOG_API_VERSION,
            "star_schema_version": STAR_SCHEMA_VERSION,
            "db_schema_version": stats["db"]["schema_version"],
            "counts": stats["counts"],
            "releases": stats["releases"],
            "db": stats["db"],
            "generated_at": stats["generated_at"],
        }

    # ------------------------------------------------------------------ 内部：行/覆盖
    def _max_pm(self, release_id: int) -> float:
        row = self.conn.execute(
            "SELECT MAX(COALESCE(ABS(pmra),0)) AS a, MAX(COALESCE(ABS(pmdec),0)) AS b "
            "FROM catalog_source WHERE release_id=?",
            (int(release_id),),
        ).fetchone()
        return max(float(row["a"] or 0.0), float(row["b"] or 0.0))

    def _coverage_for(self, release: dict, ra: float, dec: float, radius: float) -> dict:
        fields = self.fields(release["release_id"])
        if not fields:
            return {
                "complete": False,
                "field_id": None,
                "cached_radius_deg": release.get("cache_radius_deg"),
                "warning": "release 没有 field 覆盖元数据；只能返回缓存中已有源，完整性未知",
            }
        best: dict | None = None
        best_margin = -math.inf
        for f in fields:
            sep = angsep_deg(ra, dec, f["ra"], f["dec"])
            if not math.isfinite(sep):
                continue
            margin = float(f["radius_deg"]) - sep
            if margin < -radius - 1e-9:
                continue
            if margin > best_margin:
                best, best_margin = f, margin
        if best is None:
            centers = ", ".join(
                f"{f['field_id']}({f['ra']:.4f},{f['dec']:.4f},r={f['radius_deg']}°)" for f in fields[:8]
            )
            raise CatalogUnavailable(
                f"查询中心 ({ra:.4f}, {dec:.4f}) 不在任何已缓存场覆盖范围内；"
                f"已缓存场: {centers}. 请先导入该天区，或把查询移到已缓存场。",
                catalog=release["catalog"],
                version=release["version"],
                reason="no_cached_field",
            )
        complete = best_margin + 1e-9 >= radius
        cov = {
            "complete": complete,
            "field_id": best["field_id"],
            "field_name": best.get("field_name"),
            "field_ra": best["ra"],
            "field_dec": best["dec"],
            "field_radius_deg": best["radius_deg"],
            "center_sep_deg": round(float(best["radius_deg"]) - float(best_margin), 6),
            "margin_deg": round(float(best_margin), 6),
            "field_provenance": best.get("provenance"),
            "mag_limit": release.get("mag_limit"),
            "n_cached_sources": int(best.get("n_sources") or 0),
        }
        if best.get("provenance") != "tap":
            # fixture/mixed 只是真实行的小样本，不是完整场缓存；不能声称完整。
            cov["complete"] = False
            cov["warning"] = (
                "该场来自 fixture 小样本（真实 Gaia 行，但不是完整场缓存）；"
                "fixture 缺失的源不会出现在结果里，complete=false。"
            )
        elif not complete:
            if best_margin < 0:
                cov["warning"] = (
                    "查询中心在缓存场边界外，返回的是与缓存场重叠部分的源；"
                    "缓存未覆盖整个查询圆，结果不完整。"
                )
            else:
                cov["warning"] = (
                    "查询半径超出该场缓存半径；返回缓存内源，外圈可能缺少星表源。"
                )
        return cov

    def _query_rows(
        self,
        release_id: int,
        ra: float,
        dec: float,
        radius_deg: float,
    ) -> tuple[list[sqlite3.Row], bool]:
        """按 (dec, ra) 矩形粗筛，返回候选行（不做精确距离过滤）。"""
        dec_min = max(-90.0, dec - radius_deg)
        dec_max = min(90.0, dec + radius_deg)
        where = ["release_id=?", "dec BETWEEN ? AND ?"]
        args: list[Any] = [int(release_id), dec_min, dec_max]
        if radius_deg < 179.0:
            ra_width = min(180.0, radius_deg / max(math.cos(math.radians(dec)), 0.02))
            if ra_width < 180.0:
                lo = (ra - ra_width) % 360.0
                hi = (ra + ra_width) % 360.0
                if lo <= hi:
                    where.append("ra BETWEEN ? AND ?")
                    args.extend([lo, hi])
                else:
                    where.append("(ra >= ? OR ra <= ?)")
                    args.extend([lo, hi])
        sql = (
            "SELECT * FROM catalog_source WHERE "
            + " AND ".join(where)
            + " ORDER BY dec, ra LIMIT ?"
        )
        rows = self.conn.execute(sql, (*args, MAX_SOURCES + 1)).fetchall()
        truncated = len(rows) > MAX_SOURCES
        if truncated:
            rows = rows[:MAX_SOURCES]
        return rows, truncated

    def _coord_for(
        self, row: sqlite3.Row, release: dict, epoch_mjd: float | None
    ) -> tuple[float, float]:
        if epoch_mjd is None:
            return row["ra"], row["dec"]
        ref = row["epoch_ref"] if row["epoch_ref"] is not None else release["epoch_mjd"]
        dt_years = (epoch_mjd - float(ref)) / JULIAN_YEAR_DAYS
        return propagate_pm(row["ra"], row["dec"], row["pmra"], row["pmdec"], dt_years)

    def _neighbour_grid(
        self,
        rows: Sequence[sqlite3.Row],
        ra: float,
        dec: float,
        radius_deg: float,
        cell_deg: float,
        *,
        release: dict,
        epoch_mjd: float | None,
    ) -> tuple[dict[tuple[int, int], list[tuple[sqlite3.Row, float, float]]], list[sqlite3.Row], dict[str, tuple[float, float]]]:
        """把候选行按 cell_deg 分桶；返回网格、半径内命中、传播后坐标。"""
        hits: list[sqlite3.Row] = []
        grid: dict[tuple[int, int], list[tuple[sqlite3.Row, float, float]]] = {}
        coords: dict[str, tuple[float, float]] = {}
        cell = max(cell_deg, 1e-6)
        for r in rows:
            rra, rdec = self._coord_for(r, release, epoch_mjd)
            sep = angsep_deg(ra, dec, rra, rdec)
            if not math.isfinite(sep) or sep > radius_deg + 1e-9:
                continue
            hits.append(r)
            coords[r["source_id"]] = (rra, rdec)
            key = (int(math.floor(rra / cell)), int(math.floor(rdec / cell)))
            grid.setdefault(key, []).append((r, rra, rdec))
        return grid, hits, coords

    def _neighbour_count(
        self,
        source_id: str,
        coords: dict[str, tuple[float, float]],
        grid: dict[tuple[int, int], list[tuple[sqlite3.Row, float, float]]],
        cell_deg: float,
        ambiguity_arcsec: float,
    ) -> int:
        """在网格中数 source_id 之外、ambig 角秒内的源（传播后坐标）。"""
        if ambiguity_arcsec <= 0 or source_id not in coords:
            return 1
        ra, dec = coords[source_id]
        cell = max(cell_deg, 1e-6)
        cell_x = int(math.floor(ra / cell))
        cell_y = int(math.floor(dec / cell))
        n_cells = max(1, int(round(360.0 / cell)))
        count = 1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other, ora, odec in grid.get(((cell_x + dx) % n_cells, cell_y + dy), ()):
                    if other["source_id"] == source_id:
                        continue
                    if angsep_arcsec(ra, dec, ora, odec) <= ambiguity_arcsec:
                        count += 1
        return count

    # ------------------------------------------------------------------ 锥搜索
    def cone(
        self,
        ra: float,
        dec: float,
        radius_deg: float = 0.1,
        *,
        epoch: float | None = None,
        catalog: str = "gaia",
        version: str | None = None,
        limit: int = 500,
        offset: int = 0,
        ambiguity_arcsec: float = DEFAULT_AMBIGUITY_ARCSEC,
    ) -> dict:
        """星表锥搜索；返回 release/version/许可 + 星表源。

        `epoch` 是观测 MJD。给定时用 PM 传播到该历元，并返回
        `ra_propagated/dec_propagated`；`sep_arcsec` 是传播后位置到查询中心的角距。
        """
        ra = _finite(ra, "ra")
        dec = _finite(dec, "dec")
        radius = _finite(radius_deg, "radius_deg")
        if not (-90.0 <= dec <= 90.0):
            raise CatalogValidationError("dec 必须在 [-90, 90]")
        if not (0.0 < radius <= MAX_QUERY_RADIUS_DEG):
            raise CatalogValidationError(f"radius_deg 必须在 (0, {MAX_QUERY_RADIUS_DEG:g}]")
        if epoch is not None:
            epoch = _finite(epoch, "epoch")
        ambiguity = _finite(ambiguity_arcsec, "ambiguity_arcsec")
        if ambiguity < 0:
            raise CatalogValidationError("ambiguity_arcsec 必须 >= 0")
        try:
            limit = max(1, min(int(limit), MAX_CONE_LIMIT))
        except (TypeError, ValueError) as exc:
            raise CatalogValidationError("limit 必须是整数") from exc
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError) as exc:
            raise CatalogValidationError("offset 必须是整数") from exc

        release = self.get_release(catalog, version)
        coverage = self._coverage_for(release, ra, dec, radius)

        # 给定时历元时，星表 J2016 位置可能已经被高自行源移出查询圆；
        # 先用“最大自行 × 时间”把搜索框放大，再做传播后的精确过滤。
        margin = 0.0
        if epoch is not None:
            dt_years = abs(float(epoch) - float(release["epoch_mjd"])) / JULIAN_YEAR_DAYS
            margin = self._max_pm(release["release_id"]) * dt_years / MAS_PER_DEG
            margin = margin * 1.15 + 0.0005

        # 一次多查 ambiguity 的宽度，用于在整页之外也正确标记“同像元多源”。
        expand = ambiguity / 3600.0
        search_radius = min(MAX_QUERY_RADIUS_DEG, radius + expand + margin)
        rows, truncated = self._query_rows(release["release_id"], ra, dec, search_radius)
        grid, hits, coords = self._neighbour_grid(
            rows,
            ra,
            dec,
            radius + expand,
            max(expand, 1e-6),
            release=release,
            epoch_mjd=epoch,
        )
        # 只保留传播后位置在请求半径内的源（网格里还包含外圈邻居）。
        hits = [
            r
            for r in hits
            if angsep_deg(ra, dec, *self._coord_for(r, release, epoch)) <= radius + 1e-9
        ]
        hits.sort(key=lambda r: (angsep_deg(ra, dec, *self._coord_for(r, release, epoch)), r["source_id"]))
        total = len(hits)
        page = hits[offset : offset + limit]

        sources = []
        for r in page:
            ra_p, dec_p = self._coord_for(r, release, epoch)
            sep_native = angsep_arcsec(ra, dec, r["ra"], r["dec"])
            src = _source_dict(r, release, epoch_mjd=epoch, sep_arcsec=sep_native)
            n_within = self._neighbour_count(
                r["source_id"], coords, grid, max(expand, 1e-6), ambiguity
            )
            src["ambiguous"] = n_within > 1
            src["n_within_ambiguity_arcsec"] = n_within
            if epoch is not None:
                src["sep_arcsec"] = round(angsep_arcsec(ra, dec, ra_p, dec_p), 4)
            sources.append(src)

        out = {
            "schema_version": CATALOG_API_VERSION,
            "service": SERVICE_NAME,
            "release": release["release"],
            "version": release["version"],
            "catalog": release["catalog"],
            "epoch_ref": release["epoch_ref"],
            "epoch_ref_mjd": release["epoch_mjd"],
            "license": release["license"],
            "citation": release.get("citation"),
            "source_url": release["source_url"],
            "provenance": release["provenance"],
            "mag_limit": release.get("mag_limit"),
            "query": {
                "ra": ra,
                "dec": dec,
                "radius_deg": radius,
                "epoch": epoch,
                "catalog": catalog,
                "version": version,
                "limit": limit,
                "offset": offset,
                "ambiguity_arcsec": ambiguity,
            },
            "coverage": coverage,
            "count": total,
            "returned": len(page),
            "truncated": bool(truncated),
            "has_more": offset + len(page) < total,
            "sources": sources,
            "generated_at": utcnow(),
        }
        if not coverage.get("complete"):
            out["cache_limited"] = True
            out["warning"] = coverage.get("warning")
        out["catalog_status"] = "ok" if coverage.get("complete") else "partial"
        if total == 0:
            out["note"] = (
                "缓存覆盖范围内没有星表源。这只表示“星表未列出”，"
                "不是新恒星/未分类结论；known/unknown 分类由 app 的 D 阶段完成。"
            )
        return out

    # ------------------------------------------------------------------ 匹配
    def _candidates_for_detection(
        self,
        release: dict,
        ra: float,
        dec: float,
        epoch_mjd: float | None,
        match_radius_arcsec: float,
    ) -> list[dict]:
        if epoch_mjd is not None:
            dt_years = abs(epoch_mjd - float(release["epoch_mjd"])) / JULIAN_YEAR_DAYS
            margin = self._max_pm(release["release_id"]) * dt_years / MAS_PER_DEG
            margin = margin * 1.15 + 0.0005
        else:
            margin = 0.0005
        search_deg = match_radius_arcsec / 3600.0 + margin
        rows, _ = self._query_rows(release["release_id"], ra, dec, search_deg)
        out: list[dict] = []
        for r in rows:
            src = _source_dict(r, release, epoch_mjd=epoch_mjd)
            sep = angsep_arcsec(ra, dec, src["ra_propagated"], src["dec_propagated"])
            if math.isfinite(sep) and sep <= match_radius_arcsec + 1e-9:
                src["sep_arcsec"] = round(sep, 4)
                out.append(src)
        out.sort(key=lambda d: (d["sep_arcsec"], d["source_id"]))
        return out

    def match(
        self,
        detections: Iterable[dict],
        *,
        catalogs: Sequence[str] = ("gaia",),
        radius_arcsec: float = DEFAULT_MATCH_RADIUS_ARCSEC,
        epoch: float | None = None,
        versions: dict[str, str] | None = None,
        persist: bool = False,
        analysis_id: str | None = None,
    ) -> dict:
        """位置 ± 误差 → 星表候选；返回 sep/ambiguous/confidence，不做分类。"""
        dets = list(detections or [])
        if not dets:
            raise CatalogValidationError("detections 不能为空")
        if len(dets) > MAX_MATCH_DETECTIONS:
            raise CatalogValidationError(f"detections 最多 {MAX_MATCH_DETECTIONS} 个")
        radius = _finite(radius_arcsec, "radius_arcsec")
        if not (0.0 < radius <= MAX_MATCH_RADIUS_ARCSEC):
            raise CatalogValidationError(
                f"radius_arcsec 必须在 (0, {MAX_MATCH_RADIUS_ARCSEC:g}]"
            )
        if epoch is not None:
            epoch = _finite(epoch, "epoch")
        if not catalogs:
            catalogs = ["gaia"]
        versions = dict(versions or {})
        releases = {c: self.get_release(c, versions.get(c)) for c in catalogs}
        if persist and not analysis_id:
            raise CatalogValidationError("persist=true 时必须提供 analysis_id")
        if persist and self.readonly:
            raise CatalogError("只读连接不能持久化 catalog_match；请用可写连接")

        matches: list[dict] = []
        n_matched = n_ambiguous = n_unmatched = 0
        for i, det in enumerate(dets):
            if not isinstance(det, dict):
                raise CatalogValidationError(f"detections[{i}] 必须是对象")
            if "ra" not in det or "dec" not in det:
                raise CatalogValidationError(f"detections[{i}] 需要 ra 与 dec")
            dra = _finite(det.get("ra"), f"detections[{i}].ra")
            ddec = _finite(det.get("dec"), f"detections[{i}].dec")
            if not (-90.0 <= ddec <= 90.0):
                raise CatalogValidationError(f"detections[{i}].dec 必须在 [-90, 90]")
            sigma = _optional_float(det.get("sigma_arcsec"))
            if sigma is None:
                sigma = 1.0
            if sigma <= 0:
                raise CatalogValidationError(f"detections[{i}].sigma_arcsec 必须 > 0")
            det_epoch = det.get("mjd")
            if det_epoch is None:
                det_epoch = det.get("epoch")
            det_epoch = _finite(det_epoch, f"detections[{i}].mjd") if det_epoch is not None else epoch
            match_radius = max(radius, 3.0 * sigma)
            match_radius = min(match_radius, MAX_MATCH_RADIUS_ARCSEC)

            # 检测位置不在任何已缓存场覆盖内时必须显式 CATALOG_UNAVAILABLE；
            # 否则“缓存里没有”会被误读成“星表里没有”，破坏 D-08。
            coverage: dict[str, dict] = {}
            for cat in catalogs:
                coverage[cat] = self._coverage_for(
                    releases[cat], dra, ddec, match_radius / 3600.0
                )
            complete = all(cov.get("complete") for cov in coverage.values())

            candidates: list[dict] = []
            for cat in catalogs:
                release = releases[cat]
                for src in self._candidates_for_detection(
                    release, dra, ddec, det_epoch, match_radius
                ):
                    # 几何关联权重：exp(-0.5 (sep/σ)^2)，不是科学概率。
                    src["confidence"] = round(
                        math.exp(-0.5 * (float(src["sep_arcsec"]) / max(sigma, 0.25)) ** 2), 6
                    )
                    src["pm_residual"] = None
                    src["pa_residual"] = None
                    pmra_det = _optional_float(det.get("pmra_mas_yr"))
                    pmdec_det = _optional_float(det.get("pmdec_mas_yr"))
                    if (
                        pmra_det is not None
                        and pmdec_det is not None
                        and src.get("pmra") is not None
                        and src.get("pmdec") is not None
                    ):
                        dpmra = pmra_det - float(src["pmra"])
                        dpmdec = pmdec_det - float(src["pmdec"])
                        src["pm_residual"] = round(math.hypot(dpmra, dpmdec), 3)
                        pa_det = math.degrees(math.atan2(pmdec_det, pmra_det))
                        pa_cat = math.degrees(math.atan2(float(src["pmdec"]), float(src["pmra"])))
                        src["pa_residual"] = round(((pa_det - pa_cat + 180.0) % 360.0) - 180.0, 3)
                    candidates.append(src)
            candidates.sort(key=lambda d: (d["sep_arcsec"], d["catalog"], d["source_id"]))
            ambiguous = len(candidates) > 1
            if not candidates:
                status = "no_match"
                n_unmatched += 1
            elif ambiguous:
                status = "ambiguous"
                n_ambiguous += 1
                n_matched += 1
            else:
                status = "single_candidate"
                n_matched += 1
            matches.append(
                {
                    "index": i,
                    "detection_id": det.get("detection_id"),
                    "ra": dra,
                    "dec": ddec,
                    "epoch_mjd": det_epoch,
                    "sigma_arcsec": sigma,
                    "match_radius_arcsec": round(match_radius, 4),
                    "coverage": coverage,
                    "catalog_status": "ok" if complete else "partial",
                    "n_candidates": len(candidates),
                    "ambiguous": ambiguous,
                    "status": status,
                    "candidates": candidates,
                    "note": (
                        "星表候选；无候选不等于新恒星/未分类。"
                        "known/unknown 由 app 的 D 阶段按位置+运动+颜色判定。"
                    )
                    if not candidates
                    else None,
                }
            )

        # 多目录时每个目录的 release 都写进响应；单目录仍给顶层 release/version。
        release_list = [releases[c] for c in catalogs]
        out = {
            "schema_version": CATALOG_API_VERSION,
            "service": SERVICE_NAME,
            "release": release_list[0]["release"] if len(release_list) == 1 else None,
            "version": release_list[0]["version"] if len(release_list) == 1 else None,
            "releases": release_list,
            "unavailable": False,
            "query": {
                "catalogs": list(catalogs),
                "radius_arcsec": radius,
                "epoch": epoch,
                "n_detections": len(dets),
            },
            "matches": matches,
            "n_detections": len(dets),
            "n_matched": n_matched,
            "n_ambiguous": n_ambiguous,
            "n_unmatched": n_unmatched,
            "generated_at": utcnow(),
            "note": (
                "本接口只做星表关联（位置/自行/角距/歧义），"
                "不输出 known_star 或新恒星等科学分类。"
            ),
            "catalog_status": (
                "ok"
                if all(m["catalog_status"] == "ok" for m in matches)
                else "partial"
            ),
        }
        if out["catalog_status"] != "ok":
            out["cache_limited"] = True
            out["warning"] = (
                "至少一个检测点位于 fixture/场边缘缓存；缺失的星表源不会出现在候选中，"
                "catalog_status=partial。0 候选不等于未收录/新天体。"
            )
        if persist:
            out["persisted"] = self.record_matches(
                analysis_id or "",
                matches,
                detections=dets,
            )
        return out

    # ------------------------------------------------------------------ 匹配持久化
    def record_matches(
        self,
        analysis_id: str,
        matches: Iterable[dict],
        *,
        detections: Sequence[dict] | None = None,
    ) -> int:
        """把一次匹配结果写入 catalog_match；返回写入行数。"""
        if self.readonly:
            raise CatalogError("只读连接不能写 catalog_match")
        if not analysis_id:
            raise CatalogValidationError("analysis_id 不能为空")
        dets = list(detections or [])
        rows: list[tuple] = []
        now = utcnow()
        for m in matches:
            idx = int(m.get("index", 0))
            det = dets[idx] if 0 <= idx < len(dets) else {}
            for cand in m.get("candidates") or []:
                rows.append(
                    (
                        analysis_id,
                        m.get("detection_id") or det.get("detection_id"),
                        None,  # release_id 由子查询填充
                        cand["source_id"],
                        float(cand["sep_arcsec"]),
                        cand.get("pm_residual"),
                        cand.get("pa_residual"),
                        1 if m.get("ambiguous") else 0,
                        cand.get("confidence"),
                        m.get("ra"),
                        m.get("dec"),
                        m.get("epoch_mjd"),
                        now,
                    )
                )
        if not rows:
            return 0
        with transaction(self.conn):
            for row in rows:
                self.conn.execute(
                    """INSERT INTO catalog_match(
                           analysis_id, detection_id, release_id, source_id,
                           sep_arcsec, pm_residual, pa_residual, ambiguity, confidence,
                           detection_ra, detection_dec, detection_mjd, created_at)
                       SELECT ?, ?, s.release_id, s.source_id, ?, ?, ?, ?, ?, ?, ?, ?, ?
                       FROM catalog_source s WHERE s.source_id=?""",
                    (*row[:2], *row[4:], row[3]),
                )
        return len(rows)

    # ------------------------------------------------------------------ 导入
    def import_release(
        self,
        *,
        catalog: str,
        version: str,
        epoch_ref: str = GAIA_EPOCH_REF,
        epoch_mjd: float = GAIA_EPOCH_MJD,
        license: str,
        citation: str | None = None,
        source_url: str,
        provenance: str,
        sources: Iterable[dict],
        fields: Sequence[dict] = (),
        release_key: str | None = None,
        cache_radius_deg: float | None = FIELD_CACHE_RADIUS_DEG,
        mag_limit: float | None = None,
        notes: str | None = None,
        replace: bool = True,
        run_mode: str | None = None,
    ) -> dict:
        """导出一个 release（可重建）。返回 release 元数据。

        `sources` 每项至少包含 `catalog_id, ra, dec`；可选 pmra/pmdec/parallax/
        gmag/bp_rp/jmag/hmag/kmag/w1/w2/twomass_id/wise_id/object_type/provenance。
        HEALPix 在导入时计算并落库。
        """
        if self.readonly:
            raise CatalogError("只读连接不能导入星表")
        catalog = str(catalog).strip()
        version = str(version).strip()
        if not catalog or not version:
            raise CatalogValidationError("catalog/version 不能为空")
        release_key = release_key or f"{catalog}-{version}"
        started = utcnow()
        now = started
        with transaction(self.conn):
            run = self.conn.execute(
                "INSERT INTO catalog_import_run(started_at, status, mode) VALUES(?, 'running', ?)",
                (started, run_mode or provenance),
            )
            run_id = int(run.lastrowid)
            if replace:
                self.conn.execute(
                    "DELETE FROM catalog_release WHERE catalog=? AND version=?",
                    (catalog, version),
                )
            self.conn.execute(
                """INSERT INTO catalog_release(
                       release_key, catalog, version, epoch_ref, epoch_mjd, imported_at,
                       license, citation, source_url, provenance, cache_radius_deg,
                       mag_limit, n_sources, notes)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    release_key,
                    catalog,
                    version,
                    str(epoch_ref),
                    float(epoch_mjd),
                    now,
                    license,
                    citation,
                    source_url,
                    provenance,
                    cache_radius_deg,
                    mag_limit,
                    0,
                    notes,
                ),
            )
            release_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            field_counts: dict[str, int] = {}
            batch: list[tuple] = []
            n = 0
            seen_catalog_ids: set[str] = set()
            for src in sources:
                catalog_id = str(src.get("catalog_id") or src.get("source_id") or "").strip()
                if not catalog_id:
                    raise CatalogValidationError("source 缺少 catalog_id")
                if catalog_id in seen_catalog_ids:
                    # TAP 的 best-neighbour join 可能返回重复行；保持一行一源。
                    continue
                seen_catalog_ids.add(catalog_id)
                ra = _finite(src.get("ra"), f"source {catalog_id} ra")
                dec = _finite(src.get("dec"), f"source {catalog_id} dec")
                if not (-90.0 <= dec <= 90.0):
                    raise CatalogValidationError(f"source {catalog_id} dec 越界")
                source_id = str(src.get("source_id") or f"{release_key}:{catalog_id}")
                hp = src.get("healpix")
                if hp is None:
                    hp = ang2pix_nest(HEALPIX_NSIDE, ra, dec)
                batch.append(
                    (
                        source_id,
                        release_id,
                        catalog_id,
                        ra,
                        dec,
                        _optional_float(src.get("pmra")),
                        _optional_float(src.get("pmdec")),
                        _optional_float(src.get("parallax")),
                        _optional_float(src.get("ra_error")),
                        _optional_float(src.get("dec_error")),
                        _optional_float(src.get("pmra_error")),
                        _optional_float(src.get("pmdec_error")),
                        _optional_float(src.get("gmag")),
                        _optional_float(src.get("bp_rp")),
                        _optional_float(src.get("jmag")),
                        _optional_float(src.get("hmag")),
                        _optional_float(src.get("kmag")),
                        _optional_float(src.get("w1")),
                        _optional_float(src.get("w2")),
                        src.get("twomass_id"),
                        src.get("wise_id"),
                        _optional_float(src.get("epoch_ref")),
                        int(hp),
                        src.get("object_type"),
                        str(src.get("provenance") or provenance),
                        now,
                    )
                )
                fld = src.get("field_id")
                if fld:
                    field_counts[str(fld)] = field_counts.get(str(fld), 0) + 1
                n += 1
                if len(batch) >= 1000:
                    self._insert_sources(batch)
                    batch.clear()
            if batch:
                self._insert_sources(batch)

            for f in fields:
                fid = str(f["field_id"])
                self.conn.execute(
                    """INSERT INTO catalog_field(
                           release_id, field_id, field_name, ra, dec, radius_deg,
                           n_sources, provenance, imported_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        release_id,
                        fid,
                        f.get("field_name"),
                        float(f["ra"]),
                        float(f["dec"]),
                        float(f["radius_deg"]),
                        int(f.get("n_sources", field_counts.get(fid, 0))),
                        str(f.get("provenance") or provenance),
                        now,
                    ),
                )
            self.conn.execute(
                "UPDATE catalog_release SET n_sources=? WHERE release_id=?", (n, release_id)
            )
            self.conn.execute(
                "UPDATE catalog_import_run SET finished_at=?, status='ok', release_id=?, "
                "n_fields=?, n_sources=? WHERE run_id=?",
                (utcnow(), release_id, len(fields), n, run_id),
            )
            self.conn.execute(
                "INSERT INTO star_meta(key,value) VALUES('last_import_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now,),
            )
            self.conn.execute(
                "INSERT INTO star_meta(key,value) VALUES('catalog_snapshot', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (utcnow(),),
            )
            row = self.conn.execute(
                "SELECT * FROM catalog_release WHERE release_id=?", (release_id,)
            ).fetchone()
        result = _release_dict(row)
        result["n_sources_actual"] = n
        result["n_fields"] = len(fields)
        return result

    def _insert_sources(self, batch: Sequence[tuple]) -> None:
        self.conn.executemany(
            """INSERT INTO catalog_source(
                   source_id, release_id, catalog_id, ra, dec, pmra, pmdec, parallax,
                   ra_error, dec_error, pmra_error, pmdec_error,
                   gmag, bp_rp, jmag, hmag, kmag, w1, w2,
                   twomass_id, wise_id, epoch_ref, healpix, object_type,
                   provenance, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )

    def init(self, *, replace: bool = False) -> None:
        if self.readonly:
            raise CatalogError("只读连接不能初始化 schema")
        apply_star_schema(self.conn)
        if replace:
            with transaction(self.conn):
                for table in (
                    "catalog_match",
                    "catalog_source",
                    "catalog_field",
                    "catalog_import_run",
                    "catalog_release",
                ):
                    self.conn.execute(f"DELETE FROM {table}")
