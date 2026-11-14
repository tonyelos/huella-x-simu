#!/usr/bin/env python3
"""生成规模基准库（合成数据，明确不进入生产库）。

用途：验证 zone + RA 索引在百万级元数据下的查询行为，而不是拿 5,893 行
真实缓存“感觉很快”。

    python3 tools/make_scale_fixture.py --rows 200000 --db var/bench_scale.sqlite
    python3 cli.py bench --db var/bench_scale.sqlite --out benchmarks/scale_report.json
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.catalog import zone_of  # noqa: E402
from db.connection import connect  # noqa: E402
from db.ingest import init_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "var" / "bench_scale.sqlite"))
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=20261114)
    ap.add_argument("--batch", type=int, default=10_000)
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    if db.exists():
        db.unlink()
    init_db(db, ROOT / "var" / "bench_scale_blobs")
    conn = connect(db)
    conn.execute("PRAGMA synchronous = OFF")
    rng = random.Random(args.seed)
    now = "2026-11-14T00:00:00Z"

    conn.execute(
        "INSERT OR IGNORE INTO releases(code,label,status,notes) VALUES('dr1','scale fixture','active','synthetic')"
    )
    # 26 个合成周，保证 weeks 关联存在
    for w in range(26):
        week = f"2026W{w + 1:02d}_1A"
        conn.execute(
            """INSERT OR IGNORE INTO weeks(release,week,version,mjd_min,mjd_max,n_frames,imported_at)
               VALUES('dr1',?,?,?,?,0,?)""",
            (week, "l2b-scale", 60000 + w * 14, 60013 + w * 14, now),
        )
    for i, (tid, ra, dec) in enumerate(
        [
            ("scale_a", 45.0, 12.0),
            ("scale_b", 180.0, -30.0),
            ("scale_c", 300.0, 60.0),
            ("scale_d", 120.0, 5.0),
            ("scale_e", 250.0, -60.0),
        ]
    ):
        conn.execute(
            """INSERT OR IGNORE INTO targets(id,name,name_zh,official,ra,dec,type,expected,updated_at)
               VALUES(?,?,?,0,?,?, 'synthetic','U',?)""",
            (tid, tid, tid, ra, dec, now),
        )
    conn.commit()

    sql_obs = """INSERT OR IGNORE INTO observations(
        obs_id,week,field_no,repeat_no,release,release_source,mjd,date_obs,first_seen_at,updated_at)
        VALUES(?,?,?,?,'dr1','fixture',?,?,'2026-11-14T00:00:00Z','2026-11-14T00:00:00Z')"""
    sql_frame = """INSERT OR IGNORE INTO frames(
        obs_id,detector,release,week,ra,dec,mjd,date_obs,xposure,sgt_lat,sgt_lon,elon,elat,
        naxis1,naxis2,pixel_scale_arcsec,psf_fwhm_arcsec,l2dqa,zone,created_at,updated_at)
        VALUES(?,?,'dr1',?,?,?,?,?,?,?,?,?,?,2040,2040,6.15,7.5,'Pass',?,'2026-11-14T00:00:00Z','2026-11-14T00:00:00Z')"""

    t0 = time.time()
    obs_batch: list[tuple] = []
    frame_batch: list[tuple] = []
    for i in range(args.rows):
        # 球面均匀：z 均匀 -> dec = asin(z)
        z = rng.uniform(-1.0, 1.0)
        dec = math.degrees(math.asin(z))
        ra = rng.uniform(0.0, 360.0)
        week = f"2026W{(i % 26) + 1:02d}_1A"
        field = i + 1
        rep = 1
        obs_id = f"{week}_{field:04d}_{rep}"
        mjd = 60000.0 + rng.uniform(0.0, 730.0)
        date_obs = f"2026-01-{((i % 28) + 1):02d}T00:00:00.000"
        sgt_lat = rng.uniform(-80, 80)
        sgt_lon = rng.uniform(0, 360)
        elon = rng.uniform(0, 360)
        elat = rng.uniform(-75, 75)
        det = (i % 6) + 1
        obs_batch.append((obs_id, week, field, rep, mjd, date_obs))
        frame_batch.append(
            (obs_id, det, week, ra, dec, mjd, date_obs, 113.5, sgt_lat, sgt_lon, elon, elat, zone_of(dec))
        )
        if len(obs_batch) >= args.batch:
            conn.executemany(sql_obs, obs_batch)
            conn.executemany(sql_frame, frame_batch)
            conn.commit()
            obs_batch.clear()
            frame_batch.clear()
            print(f"  {i + 1}/{args.rows}", flush=True)
    if obs_batch:
        conn.executemany(sql_obs, obs_batch)
        conn.executemany(sql_frame, frame_batch)
        conn.commit()

    conn.execute(
        """UPDATE weeks SET
             mjd_min=(SELECT MIN(f.mjd) FROM frames f WHERE f.week=weeks.week),
             mjd_max=(SELECT MAX(f.mjd) FROM frames f WHERE f.week=weeks.week),
             n_frames=(SELECT COUNT(*) FROM frames f WHERE f.week=weeks.week)"""
    )
    conn.execute("ANALYZE")
    conn.execute("PRAGMA optimize")
    conn.commit()
    conn.close()
    elapsed = time.time() - t0
    size = db.stat().st_size / 1e6
    print(f"完成: rows={args.rows} elapsed={elapsed:.1f}s db={db} ({size:.1f} MB) 合成基准库，不是真实观测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
