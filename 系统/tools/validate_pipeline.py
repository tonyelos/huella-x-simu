#!/usr/bin/env python3
"""注入-回收验证：对 5 个官方目标做多随机种子的端到端回收。

输出 docs/validation_report.json，并在终端打印一张表。
"""
from __future__ import annotations

import json
import os
import statistics
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from core.pipeline import run_pipeline  # noqa: E402
from core.simulator import default_times, generate_epochs  # noqa: E402


def main():
    targets = json.load(open(os.path.join(ROOT, "app", "data", "targets.json")))["targets"]
    official = [t for t in targets if t.get("official")]
    seeds = [3, 11, 23, 41, 57, 73, 91, 107]
    report = {"targets": {}, "summary": {}}
    acc = []
    for t in official:
        rows = []
        for s in seeds:
            epochs, truth = generate_epochs(t, default_times(8), fov_deg=0.2, seed=s)
            res = run_pipeline(t, epochs, truth=truth)
            rows.append(
                {
                    "seed": s,
                    "class": res["class"],
                    "readiness": res["readiness"],
                    "mu_meas": res["mu_mas_yr"],
                    "mu_err": res["mu_err_mas_yr"],
                    "mu_inj": truth["mu_mas_yr"],
                    "pa_meas": res["pa_deg"],
                    "pa_inj": truth["pa_deg"],
                    "disp": res["fingerprint"]["displacement_arcsec"],
                    "disp_err": res["fingerprint"]["displacement_err_arcsec"],
                    "amp": res["fingerprint"]["amp_mag"],
                    "sig": res["fingerprint"]["mu_signif"],
                }
            )
        # 回收统计（只在移动目标上统计 μ）
        moving_rows = [r for r in rows if r["mu_inj"] > 500]
        ratios = [r["mu_meas"] / r["mu_inj"] for r in moving_rows if r["mu_inj"]]
        pas = [abs((r["pa_meas"] - r["pa_inj"] + 180) % 360 - 180) for r in moving_rows if r["pa_meas"] is not None]
        # 分类：官方 expected 可能是 "L+O"，接受 L
        expected = t["expected"].split("+")[0]
        ok = sum(1 for r in rows if r["class"] == expected)
        acc.append(ok / len(rows))
        report["targets"][t["id"]] = {
            "name": t["name"],
            "expected": expected,
            "rows": rows,
            "class_accuracy": ok / len(rows),
            "mu_ratio_median": statistics.median(ratios) if ratios else None,
            "mu_ratio_scatter": float(np.std(ratios)) if len(ratios) > 1 else None,
            "pa_error_median_deg": statistics.median(pas) if pas else None,
            "mu_signif_median": statistics.median([r["sig"] for r in moving_rows]) if moving_rows else None,
        }
        rec = report["targets"][t["id"]]
        ratio = f"{rec['mu_ratio_median']:.2f}" if rec["mu_ratio_median"] is not None else "—"
        pae = f"{rec['pa_error_median_deg']:.1f}°" if rec["pa_error_median_deg"] is not None else "—"
        sig = f"{rec['mu_signif_median']:.1f}σ" if rec["mu_signif_median"] is not None else "—"
        print(f"{t['id']:10s} 期望 {expected}  分类正确 {ok}/{len(rows)}  μ回收比 {ratio}  PA误差 {pae}  μ显著性 {sig}")

    report["summary"] = {
        "n_seeds": len(seeds),
        "mean_class_accuracy": float(np.mean(acc)),
        "n_targets": len(official),
        "date": "2026-11-14",
    }

    # 数据就绪度实验：2 / 4 / 8 期
    dep = []
    t = [x for x in official if x["id"] == "barnard"][0]
    for n in (2, 4, 8):
        for s in (3, 11, 23):
            epochs, truth = generate_epochs(t, default_times(n), fov_deg=0.2, seed=s)
            res = run_pipeline(t, epochs, truth=truth)
            dep.append({"n": n, "seed": s, "readiness": res["readiness"], "class": res["class"], "sig": res["fingerprint"]["mu_signif"]})
    report["readiness_experiment"] = dep

    out = os.path.join(ROOT, "docs", "validation_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(report, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n写入 {out}")
    print(f"平均分类正确率：{report['summary']['mean_class_accuracy']*100:.0f}%")


if __name__ == "__main__":
    main()
