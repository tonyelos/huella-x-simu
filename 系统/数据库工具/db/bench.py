"""查询性能基准。

指标不是“跑一次觉得快”，而是每个 case 重复 N 次后的 p50/p95/p99。
基准结果写到 benchmarks/report.json，README 里引用实测值。
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import Catalog


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _case(cat: Catalog, name: str, calls: list[Any], runs: int) -> dict:
    # 预热，排除首次连接/页缓存的影响
    for fn in calls[:1]:
        fn()
    samples: list[float] = []
    last: Any = None
    for i in range(runs):
        fn = calls[i % len(calls)]
        t0 = time.perf_counter()
        last = fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    total_ms = sum(samples)
    return {
        "case": name,
        "runs": runs,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(_pct(samples, 0.95), 3),
        "p99_ms": round(_pct(samples, 0.99), 3),
        "max_ms": round(max(samples), 3),
        "mean_ms": round(total_ms / len(samples), 3),
        "qps": round(1000.0 * len(samples) / total_ms, 1) if total_ms else None,
        "last_count": len(last.get("frames", [])) if isinstance(last, dict) and "frames" in last else None,
    }


def run_bench(cat: Catalog, *, runs: int = 200, out_path: str | None = None) -> dict:
    targets = cat.targets()
    if not targets:
        targets = [
            {"id": "barnard", "ra": 269.452077, "dec": 4.693365},
            {"id": "epsind_b", "ra": 331.044147, "dec": -56.782844},
            {"id": "wise0855", "ra": 133.795129, "dec": -7.245147},
        ]

    # 目标位置大多落在 5,893 帧的稀疏子集里；再抽一批真实帧中心，保证每个 case 有命中。
    sampled = [
        {"id": f"frame-{r[0]}", "ra": r[1], "dec": r[2]}
        for r in cat.conn.execute(
            "SELECT frame_id, ra, dec FROM frames WHERE frame_id % 173 = 0 ORDER BY frame_id LIMIT 24"
        )
    ]
    positions = targets + sampled

    cases: list[dict] = []
    for radius in (0.1, 0.2, 0.5, 1.0):
        calls = [
            (lambda p=p, r=radius: cat.cone(p["ra"], p["dec"], r, limit=100, order="sep"))
            for p in positions
        ]
        cases.append(_case(cat, f"cone radius={radius}° (targets + sampled frames)", calls, runs))

    calls = [(lambda p=p: cat.coverage(p["ra"], p["dec"], 0.2)) for p in sampled]
    cases.append(_case(cat, "coverage radius=0.2° (sampled frames)", calls, runs))

    calls = []
    for t in targets:
        tid = t["id"]
        calls.append(lambda tid=tid: {"frames": cat.epochs_for_target(tid)})
    cases.append(_case(cat, "epochs_for_target", calls, runs))

    calls = [
        lambda: cat.cone(269.452077, 4.693365, 0.2, limit=100, detector=2),
        lambda: cat.cone(269.452077, 4.693365, 0.2, limit=100, release="dr1"),
        lambda: cat.cone(269.452077, 4.693365, 0.2, limit=100, mjd_min=60000),
        lambda: cat.cone(269.452077, 4.693365, 0.2, limit=100, order="mjd"),
    ]
    cases.append(_case(cat, "cone filters (detector/release/mjd/order)", calls, runs))

    t0 = time.perf_counter()
    for _ in range(max(5, runs // 20)):
        cat.stats()
    stats_ms = (time.perf_counter() - t0) * 1000.0 / max(5, runs // 20)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "runs_per_case": runs,
        "targets": len(targets),
        "db": cat.stats()["db"],
        "cases": cases,
        "stats_ms_mean": round(stats_ms, 3),
        "note": "单机、只读 SQLite、本地页缓存已预热；不含 HTTP 序列化与网络。",
    }
    if out_path:
        path = Path(out_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    return report
