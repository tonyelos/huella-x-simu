#!/usr/bin/env python3
"""针对性论证：亮度变化会不会被误判成移动。

四类受控轨迹（每类 30 个随机种子）：
  A 纯变星      μ=0，振幅 2 等，周期 245 天（R Dra 型）
  B 纯移动      μ=10.4″/yr，恒亮（Barnard 型）
  C 又动又变    μ=8″/yr，振幅 1 等
  D 变星+混叠   位置本不动，但质心被邻近星"拉"着随亮度漂移（最危险的假移动）

用同一套 analyze_track 决策，输出混淆矩阵与位移显著性。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from core.motion import Track, analyze_track  # noqa: E402

YEAR = 365.25
N_SEEDS = 30
N_EPOCHS = 8


def make_track(kind: str, seed: int) -> Track:
    rng = np.random.default_rng(seed)
    t = np.sort(rng.uniform(60930, 60930 + 205, N_EPOCHS))
    ty = (t - t[0]) / YEAR
    sig = 0.12  # 角秒，约 0.02 px
    amp_mag = {"A": 2.0, "B": 0.0, "C": 1.0, "D": 2.0, "E": 2.0}.get(kind, 0.0)
    period = 245.0
    phase = rng.uniform(0, 2 * np.pi)
    m = -2.5 * np.log10(1.0) - amp_mag / 2 * np.sin(2 * np.pi * (t - t[0]) / period + phase)
    flux = 10 ** (-0.4 * m)
    flux_err = flux * 0.03 + 0.01

    mu = {"A": 0.0, "B": 10.36, "C": 8.0, "D": 0.0, "E": 8.0}.get(kind, 0.0)
    xi = mu * ty
    eta = np.zeros_like(t)

    # 混叠：变星与一颗固定邻星混合，质心随流量比漂移
    if kind in ("D", "E"):
        blend_shift = 1.85  # 角秒，约 0.3 px
        xi = xi + blend_shift * (flux / np.median(flux) - 1.0)

    xi = xi + rng.normal(0, sig, N_EPOCHS)
    eta = eta + rng.normal(0, sig, N_EPOCHS)
    return Track(
        t=t,
        xi=xi,
        eta=eta,
        sigma_xi=np.full(N_EPOCHS, sig),
        sigma_eta=np.full(N_EPOCHS, sig),
        flux=flux,
        flux_err=flux_err,
        band=np.array(["D2"] * N_EPOCHS),
        epoch_id=[f"e{i}" for i in range(N_EPOCHS)],
    )


def main():
    kinds = {"A": "纯变星 μ=0", "B": "纯移动 μ=10.4″/yr", "C": "又动又变", "D": "变星+混叠（假移动陷阱）", "E": "移动+混叠"}
    report = {}
    print(f"{'类别':28s} {'L':>3s} {'V':>3s} {'S':>3s} {'U':>3s} {'A':>3s}  {'μ中位':>9s} {'μ显著性':>8s}")
    for kind, label in kinds.items():
        classes = []
        mus = []
        sigs = []
        flags = []
        for seed in range(N_SEEDS):
            tr = make_track(kind, seed)
            r = analyze_track(tr, 100.0, 20.0)
            classes.append(r["class"])
            mus.append(r["mu_mas_yr"])
            sigs.append(r["fingerprint"]["mu_signif"])
            flags.extend(r.get("quality") or [])
            if r.get("caveats"):
                flags.extend(["CAVEAT"] * len(r["caveats"]))
        counts = {c: classes.count(c) for c in "LVSUA"}
        report[kind] = {
            "label": label,
            "counts": counts,
            "mu_median": float(np.median(mus)),
            "mu_signif_median": float(np.median(sigs)),
            "false_motion_rate": (counts["L"] + counts["A"]) / N_SEEDS,
            "flags": sorted(set(flags)),
        }
        print(
            f"{label:28s} {counts['L']:3d} {counts['V']:3d} {counts['S']:3d} {counts['U']:3d}  "
            f"{np.median(mus):9.0f} {np.median(sigs):8.1f}σ  A={counts['A']}"
        )
    out = os.path.join(ROOT, "docs", "motion_vs_variability.json")
    json.dump(report, open(out, "w"), ensure_ascii=False, indent=1)
    print("写入", out)


if __name__ == "__main__":
    main()
