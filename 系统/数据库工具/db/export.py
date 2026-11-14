"""兼容导出：把数据库还原成两个项目已经在用的 JSON 形状。

这样 MoveScope / 视图工具可以零代码改动地切换到数据库：
- `fields.json`     -> 视图工具/data/fields.json
- `epoch_catalog.json` -> app/data/epoch_catalog.json
- `targets.json`    -> app/data/targets.json
- `week_index.json`  -> app/data/week_index.json（可选）

导出是“物化视图”，数据库仍是唯一写入口。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import build_fits_key, build_slice_url, split_obs_id

GENERATED_BY = "数据库工具 / db/export.py"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _round(v: Any, n: int) -> Any:
    if v is None:
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return v


def export_fields(conn: sqlite3.Connection) -> dict:
    """视图工具使用的紧凑曝光索引（10 列数组，省 JSON 体积）。"""
    keys = ["obsid", "detector", "ra", "dec", "mjd", "lat", "lon", "elon", "elat", "xposure"]
    # fields.json 是视图工具的冻结契约：用导入时的快照，避免 T4 新增的 D2–D6 帧
    # 改变旧文件的行数/顺序。新帧仍然可以通过 /api/cone 或 /api/v1/epochs 查询。
    try:
        rows = conn.execute(
            """SELECT obsid, detector, ra, dec, mjd, lat, lon, elon, elat, xposure
               FROM fields_snapshot ORDER BY seq"""
        ).fetchall()
    except Exception:
        rows = []
    if rows:
        fields = [
            [
                r[0], int(r[1]), _round(r[2], 5), _round(r[3], 5), _round(r[4], 6),
                _round(r[5], 4), _round(r[6], 4), _round(r[7], 3), _round(r[8], 3), _round(r[9], 1),
            ]
            for r in rows
        ]
        return {
            "generated_by": GENERATED_BY,
            "source": "数据库工具/var/catalog.sqlite (fields_snapshot)",
            "generated_at": _now(),
            "count": len(fields),
            "keys": keys,
            "fields": fields,
        }
    rows = conn.execute(
        """SELECT obs_id, detector, ra, dec, mjd, sgt_lat, sgt_lon, elon, elat, xposure
           FROM frames
           WHERE sgt_lat IS NOT NULL AND sgt_lon IS NOT NULL
           ORDER BY mjd, frame_id"""
    ).fetchall()
    fields = [
        [
            r[0],
            int(r[1]),
            _round(r[2], 5),
            _round(r[3], 5),
            _round(r[4], 6),
            _round(r[5], 4),
            _round(r[6], 4),
            _round(r[7], 3),
            _round(r[8], 3),
            _round(r[9], 1),
        ]
        for r in rows
    ]
    return {
        "generated_by": GENERATED_BY,
        "source": "数据库工具/var/catalog.sqlite (frames)",
        "generated_at": _now(),
        "count": len(fields),
        "keys": keys,
        "fields": fields,
    }


def export_epochs(conn: sqlite3.Connection) -> dict:
    """MoveScope 的 epoch_catalog.json：按目标分组，带 key / slice_url_002。"""
    targets = conn.execute("SELECT id, ra, dec FROM targets ORDER BY id").fetchall()
    out: dict[str, list[dict]] = {}
    for tid, tra, tdec in targets:
        rows = conn.execute(
            """SELECT e.release, e.week, e.obs_id, e.detector, e.mjd, e.date_obs, e.sep_deg,
                      e.fits_key, e.slice_url, e.rank, COALESCE(w.version, NULL) AS version
               FROM target_epochs e
               LEFT JOIN weeks w ON w.week=e.week AND w.release=e.release
               WHERE e.target_id=?
               ORDER BY e.mjd, e.detector""",
            (tid,),
        ).fetchall()
        items: list[dict] = []
        for r in rows:
            release, week, obs_id, detector, mjd, date_obs, sep, fits_key, slice_url, _rank, version = r
            key = fits_key or build_fits_key(release, week, version, obs_id, detector)
            if not key:
                # 没有处理版本就拼不出可下载路径；宁可跳过，也不导出坏链接。
                continue
            field, rep = split_obs_id(obs_id)[1], split_obs_id(obs_id)[2]
            item = {
                "release": release,
                "week": week,
                "version": version,
                "field": field,
                "detector": int(detector) if detector is not None else None,
                "key": key,
                "obsid": obs_id,
                "mjd": _round(mjd, 9),
                "date_obs": date_obs,
                "sep_deg": _round(sep, 4),
                "target_id": tid,
            }
            url = slice_url or build_slice_url(key, float(tra), float(tdec), 0.05)
            if url:
                item["slice_url_002"] = url
            items.append(item)
        if items:
            out[tid] = items
    return {
        "generated_by": GENERATED_BY,
        "generated_at": _now(),
        "epochs": out,
    }


def export_targets(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT * FROM targets ORDER BY official DESC, id").fetchall()
    targets = []
    for r in rows:
        d = dict(r)
        truth = None
        if d.get("truth_json"):
            try:
                truth = json.loads(d["truth_json"])
            except json.JSONDecodeError:
                truth = None
        extra = {}
        if d.get("extra_json"):
            try:
                extra = json.loads(d["extra_json"])
            except json.JSONDecodeError:
                extra = {}
        item = {
            "id": d["id"],
            "name": d["name"],
            "name_zh": d["name_zh"],
            "official": bool(d["official"]),
            "ra": d["ra"],
            "dec": d["dec"],
            "type": d["type"],
            "expected": d["expected"],
            "truth": truth,
            "note": d["note"],
        }
        # 保留数据库不认识的字段（例如并行项目新加的 coord_unc_arcsec）
        item = {**extra, **item}
        targets.append(item)
    return {
        "schema": "movescope/targets/v1",
        "updated": _now()[:10],
        "notes": "由数据库工具从 catalog.sqlite 导出；truth 仅用于验收对照。",
        "targets": targets,
    }


def export_weeks(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT * FROM weeks ORDER BY week").fetchall()
    weeks = []
    for r in rows:
        d = dict(r)
        weeks.append(
            {
                "release": d["release"],
                "week": d["week"],
                "version": d["version"],
                "first_key": d["first_key"],
                "obsid": d["first_obsid"],
                "detector": d.get("first_detector"),
                "date_obs": d.get("first_date_obs"),
                "mjd_obs": d.get("mjd_obs") if d.get("mjd_obs") is not None else d["mjd_min"],
                "elon": d["elon"],
                "elat": d["elat"],
                "ra": d["ra"],
                "dec": d["dec"],
                "n_frames": d["n_frames"],
            }
        )
    return {
        "generated_by": GENERATED_BY,
        "generated_at": _now(),
        "weeks": weeks,
    }


EXPORTERS = {
    "fields": export_fields,
    "epochs": export_epochs,
    "targets": export_targets,
    "weeks": export_weeks,
}


def write_json(payload: dict, path: str | os.PathLike[str], *, compact: bool = True) -> Path:
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return out


def export_one(conn: sqlite3.Connection, name: str, path: str | os.PathLike[str], *, compact: bool = True) -> Path:
    if name not in EXPORTERS:
        raise KeyError(f"未知导出目标: {name}（可选: {', '.join(EXPORTERS)}）")
    return write_json(EXPORTERS[name](conn), path, compact=compact)


def export_all(conn: sqlite3.Connection, out_dir: str | os.PathLike[str], *, compact: bool = True) -> dict[str, str]:
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    names = {"fields": "fields.json", "epochs": "epoch_catalog.json", "targets": "targets.json", "weeks": "week_index.json"}
    return {name: str(export_one(conn, name, out / filename, compact=compact)) for name, filename in names.items()}
