#!/usr/bin/env python3
"""T3 离线演示冻结器 · run_offline

在“断网”条件下，从快照复现 5 个官方目标（可选自定义坐标）的
`mode / readiness / class / μ / PA`，并与冻结时的 results/*.json 对比。

断网方式：
- 把 `urllib.request.urlopen` 替换成抛错函数，任何取数尝试都会被记录；
- 把 `app.core.data._cache_path` 重定向到快照 `epochs/`：缓存键仍由 app 的
  `_cache_path` 计算，但命中后读取快照文件；快照没有的键直接报错，
  绝不静默退回 HYBRID/SIM。

退出码：0=全过；1=结果不一致；2=环境/快照错误（缺文件、模式漂移等）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

# 审计基线（2026-11-14，5 目标离线实测）。results/*.json 缺失时的兜底黄金表。
# class/readiness 严格；μ ±5%；PA ±5°（与 T1 黄金容差一致）。
BUILTIN_GOLDEN = {
    "barnard": {
        "data_mode": "real",
        "readiness": "L2",
        "class": "S",
        "mu_mas_yr": 8032.88299249131,
        "pa_deg": 34.238051243583826,
    },
    "epsind_b": {
        "data_mode": "real",
        "readiness": "L3",
        "class": "L",
        "mu_mas_yr": 4706.664472174226,
        "pa_deg": 129.62203878091213,
    },
    "wise0855": {"data_mode": "real", "readiness": "L0", "class": "U", "mu_mas_yr": None, "pa_deg": None},
    "rw_aur": {
        "data_mode": "hybrid",
        "readiness": "L2",
        "class": "V",
        "mu_mas_yr": 49.327071582027905,
        "pa_deg": 77.18006883648837,
    },
    "r_dra": {
        "data_mode": "hybrid",
        "readiness": "L2",
        "class": "V",
        "mu_mas_yr": 794.938063205092,
        "pa_deg": 58.010702355334224,
    },
}


class CoverageError(RuntimeError):
    """快照缺切片 / 缓存键漂移 / 模式回退：属于环境错误。"""


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="T3 离线快照断网复跑（5 目标）")
    ap.add_argument("--snapshot", required=True, help="快照目录（含 manifest.json）")
    ap.add_argument("--targets", default="all", help="all 或逗号分隔 target id")
    ap.add_argument("--coords", action="store_true", help="同时复跑快照中的自定义坐标（若 status=ok）")
    ap.add_argument("--mu-tol", type=float, default=0.05, help="μ 相对容差（默认 5%%）")
    ap.add_argument("--pa-tol", type=float, default=5.0, help="PA 容差（度，默认 5）")
    ap.add_argument("--json", action="store_true", help="在 stdout 额外打印 JSON 报告")
    ap.add_argument("--json-out", default="", help="把 JSON 报告写到指定路径")
    return ap.parse_args(argv)


def finite(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def load_app():
    common.setup_app_path()
    try:
        import numpy as np

        import core.data as D
        from core import pipeline as PIPE
        from core import simulator as SIM
    except Exception as exc:  # noqa: BLE001
        raise CoverageError(f"无法 import app/core：{type(exc).__name__}: {exc}") from exc
    return np, D, PIPE, SIM


# ------------------------------------------------------------------ 快照映射


def preflight(snapshot: Path, manifest: dict) -> tuple[dict, list[str]]:
    check = common.check_snapshot_files(snapshot, manifest)
    errors = list(check["errors"])
    target_status = {str(t.get("id")): t for t in manifest.get("targets") or []}
    for t in manifest.get("targets") or []:
        tid = str(t.get("id"))
        mode = t.get("mode")
        status = t.get("status")
        if mode == "real" and status != "ok":
            errors.append(f"{tid}: real 目标快照不完整（status={status}），拒绝运行（避免静默退回 HYBRID）")
        if mode in ("hybrid", "synthetic"):
            sim = t.get("simulation") or {}
            if not sim.get("times_mjd"):
                errors.append(f"{tid}: {mode} 目标缺 simulation.times_mjd，无法复现模拟时序")
        if mode == "real":
            n_ok = sum(1 for e in t.get("epochs") or [] if e.get("status") == "ok")
            if n_ok < 2:
                errors.append(f"{tid}: real 目标 ok 历元 {n_ok} < 2")
    for c in manifest.get("coords") or []:
        if c.get("status") == "ok":
            n_ok = sum(1 for e in c.get("epochs") or [] if e.get("status") == "ok")
            if n_ok < 2:
                errors.append(f"coord {c.get('ra')},{c.get('dec')}: ok 历元 {n_ok} < 2")
    roles = {str(s.get("role")) for s in manifest.get("support") or []}
    if "psf" not in roles:
        errors.append("快照缺 support PSF（HYBRID/PSF 拟合需要）")
    if "background" not in roles:
        errors.append("快照缺 support 背景（HYBRID 模拟需要）")
    return check, errors


def build_keymap(manifest: dict) -> dict[str, str]:
    keymap: dict[str, str] = {}
    for owner, e in common.manifest_epoch_entries(manifest):
        if e.get("status") != "ok":
            continue
        ck = e.get("cache_key")
        rel = e.get("file")
        if not ck or not rel:
            raise CoverageError(f"{owner}: status=ok 的历元缺 cache_key/file")
        ck = str(ck)
        rel = str(rel)
        if ck in keymap and keymap[ck] != rel:
            raise CoverageError(f"cache_key 冲突：{ck} -> {keymap[ck]} / {rel}")
        keymap[ck] = rel
    if not keymap:
        raise CoverageError("manifest 没有任何可用的缓存键（keymap 为空）")
    return keymap


def install_cache_redirect(D, snapshot: Path, keymap: dict[str, str], temp_cache: Path):
    """把 _cache_path 重定向到快照：仍调用 app 原函数计算键，不自己拼文件名。"""
    old_cache = D.CACHE
    D.CACHE = str(temp_cache)
    orig = D._cache_path

    def patched(ep, ra, dec, size):
        original = orig(ep, ra, dec, size)
        key = os.path.basename(original)
        rel = keymap.get(key)
        if rel is None:
            raise CoverageError(
                f"快照未冻结该切片的缓存键 {key}"
                f"（obsid={ep.get('obsid')} D{ep.get('detector')} ra={ra:.6f} dec={dec:.6f} size={size}）"
            )
        path = snapshot / rel
        if not path.is_file():
            raise CoverageError(f"快照文件缺失：{rel}")
        return str(path)

    D._cache_path = patched
    return old_cache, orig


def block_network() -> tuple[list[str], object]:
    attempts: list[str] = []
    real = urllib.request.urlopen

    def blocked(url, *args, **kwargs):
        attempts.append(str(getattr(url, "full_url", url)))
        raise urllib.error.URLError("network disabled by T3 run_offline")

    urllib.request.urlopen = blocked
    return attempts, real


def sim_fingerprint(epochs, np) -> str:
    digest = common.hashlib.sha256()
    for e in epochs:
        digest.update(np.ascontiguousarray(np.asarray(e.image, dtype="<f8")).tobytes())
        digest.update(np.ascontiguousarray(np.asarray(e.mjd, dtype="<f8")).tobytes())
    return digest.hexdigest()


def ensure_psf(np, snapshot: Path, epochs) -> None:
    psf_path = snapshot / "support" / "psf_d2.npy"
    for ep in epochs:
        psf = getattr(ep, "psf", None)
        if psf is None or getattr(psf, "ndim", 0) != 2 or getattr(psf, "size", 0) < 9:
            if not psf_path.is_file():
                raise CoverageError("快照缺 support/psf_d2.npy，无法补齐 PSF")
            ep.psf = np.load(psf_path)


# ------------------------------------------------------------------ 黄金对比


def find_target_record(manifest: dict, tid: str) -> dict | None:
    for t in manifest.get("targets") or []:
        if str(t.get("id")) == tid:
            return t
    return None


def load_golden(snapshot: Path, manifest: dict, tid: str) -> tuple[dict | None, str]:
    t = find_target_record(manifest, tid)
    if t:
        rel = t.get("results_file")
        if rel:
            p = common.resolve_in_snapshot(snapshot, str(rel))
            if p.is_file():
                try:
                    d = common.read_json(p)
                    if isinstance(d, dict) and d.get("status", "ok") == "ok":
                        return d, "snapshot_results"
                except Exception:  # noqa: BLE001
                    pass
    if tid in BUILTIN_GOLDEN:
        return dict(BUILTIN_GOLDEN[tid]), "builtin_audit"
    return None, "none"


def compare(result: dict, golden: dict | None, mu_tol: float, pa_tol: float) -> tuple[bool, dict, list[str]]:
    checks: dict[str, dict] = {}
    errors: list[str] = []
    if not golden:
        return True, checks, ["没有 results/*.json，也没有内置黄金表：只检查可运行性"]

    g_mode = golden.get("data_mode")
    if g_mode:
        ok = result.get("mode") == g_mode
        checks["mode"] = {"ok": ok, "expected": g_mode, "actual": result.get("mode")}
        if not ok:
            errors.append(f"mode 不一致：期望 {g_mode}，实际 {result.get('mode')}")
    for key in ("readiness", "class"):
        g = golden.get(key)
        if g:
            ok = result.get(key) == g
            checks[key] = {"ok": ok, "expected": g, "actual": result.get(key)}
            if not ok:
                errors.append(f"{key} 不一致：期望 {g}，实际 {result.get(key)}")
    gmu = finite(golden.get("mu_mas_yr"))
    mu = finite(result.get("mu_mas_yr"))
    if gmu is not None and abs(gmu) > 1e-9:
        if mu is None:
            checks["mu"] = {"ok": False, "expected": gmu, "actual": None}
            errors.append(f"μ 非有限：期望 {gmu:g}")
        else:
            rel = abs(mu - gmu) / abs(gmu)
            ok = rel <= mu_tol
            checks["mu"] = {"ok": ok, "expected": gmu, "actual": mu, "rel_diff": rel, "tol": mu_tol}
            if not ok:
                errors.append(f"μ 超出容差：期望 {gmu:.2f}，实际 {mu:.2f}（{rel * 100:.2f}% > {mu_tol * 100:.0f}%）")
    gpa = finite(golden.get("pa_deg"))
    pa = finite(result.get("pa_deg"))
    if gpa is not None and pa is not None:
        diff = abs((pa - gpa + 180.0) % 360.0 - 180.0)
        ok = diff <= pa_tol
        checks["pa"] = {"ok": ok, "expected": gpa, "actual": pa, "diff_deg": diff, "tol": pa_tol}
        if not ok:
            errors.append(f"PA 超出容差：期望 {gpa:.2f}°，实际 {pa:.2f}°（差 {diff:.2f}°）")
    return not errors, checks, errors


# ------------------------------------------------------------------ 跑目标


def run_one_target(np, D, PIPE, SIM, manifest: dict, t: dict, snapshot: Path) -> dict:
    tid = str(t["id"])
    mode = str(t.get("mode"))
    expected_entries = [e for e in t.get("epochs") or [] if e.get("status") == "ok"]
    fov = float(manifest.get("fov", 0.2))
    max_real = int(manifest.get("max_real", 8))

    target, epochs, truth, meta = D.build_dataset(tid, mode="auto", max_real=max_real, size_deg=fov)
    run_mode = str(meta.get("mode"))
    if run_mode != mode:
        raise CoverageError(
            f"{tid}: 期望 mode={mode}，实际 build_dataset 返回 {run_mode}；"
            "快照不完整或 app 缓存键/降级规则已漂移，拒绝把回退结果当成功"
        )

    if mode == "real":
        if len(epochs) != len(expected_entries):
            raise CoverageError(f"{tid}: 期望 {len(expected_entries)} 期真实历元，实际加载 {len(epochs)} 期")
        for i, (ep, ent) in enumerate(zip(epochs, expected_entries)):
            if str(ep.obsid) != str(ent.get("obsid")) or abs(float(ep.mjd) - float(ent.get("mjd"))) > 1e-6:
                raise CoverageError(
                    f"{tid}: 第 {i + 1} 期与 manifest 不一致（{ep.obsid} {ep.mjd} vs {ent.get('obsid')} {ent.get('mjd')}）"
                )
            if str(ep.provenance) != "real":
                raise CoverageError(f"{tid}: 第 {i + 1} 期 provenance={ep.provenance}，不是 real")
    else:
        sim = t.get("simulation") or {}
        times = sim.get("times_mjd") or []
        if len(epochs) != int(sim.get("n_epochs", len(times))):
            raise CoverageError(f"{tid}: 模拟历元数不一致（manifest {sim.get('n_epochs')}，实际 {len(epochs)}）")
        if len(times) and len(times) == len(epochs):
            if not np.allclose([float(e.mjd) for e in epochs], [float(x) for x in times], atol=1e-6):
                raise CoverageError(f"{tid}: 模拟时间轴与冻结时不一致（app 版本漂移？）")
        frozen_fp = sim.get("image_stack_sha256")
        if frozen_fp:
            actual_fp = sim_fingerprint(epochs, np)
            if actual_fp != frozen_fp:
                raise CoverageError(
                    f"{tid}: 模拟图像栈指纹不一致（manifest {frozen_fp[:12]}…，实际 {actual_fp[:12]}…）；"
                    "generate_epochs/PSF/背景已漂移"
                )

    ensure_psf(np, snapshot, epochs)
    result = PIPE.run_pipeline(target, epochs, truth=truth, mode=str(meta.get("mode", mode)))
    return {
        "target_id": tid,
        "mode": run_mode,
        "n_epochs": len(epochs),
        "readiness": result.get("readiness"),
        "class": result.get("class"),
        "mu_mas_yr": finite(result.get("mu_mas_yr")),
        "mu_err_mas_yr": finite(result.get("mu_err_mas_yr")),
        "pa_deg": finite(result.get("pa_deg")),
        "confidence": finite(result.get("confidence")),
        "detected_epochs": result.get("detected_epochs"),
        "quality": result.get("quality"),
    }


def is_exact_match(c: dict, tol_arcsec: float = 1.0) -> bool:
    """0.0 是合法角距，不能用 `or` 兜底；缺失才视为不匹配。"""
    sep = c.get("match_sep_arcsec")
    if sep is None:
        return False
    try:
        return float(sep) <= tol_arcsec
    except (TypeError, ValueError):
        return False


def run_one_coord(np, D, PIPE, snapshot: Path, c: dict, manifest: dict) -> dict:
    ra = float(c["ra"])
    dec = float(c["dec"])
    matched = str(c.get("matched_target") or "")
    base = D.get_target(matched)
    exact = is_exact_match(c)
    truth = dict(base.get("truth") or {}) if exact else {}
    target = dict(
        base,
        id=f"custom:{ra:.6f},{dec:.6f}",
        name=f"RA {ra:.4f} Dec {dec:.4f}",
        ra=ra,
        dec=dec,
        truth=truth,
    )
    epochs = []
    for e in c.get("epochs") or []:
        if e.get("status") != "ok":
            continue
        ep = {
            "release": e.get("release"),
            "obsid": e.get("obsid"),
            "detector": e.get("detector"),
            "version": e.get("version"),
            "mjd": e.get("mjd"),
            "date_obs": e.get("date_obs"),
        }
        req = e.get("request") or {}
        epoch = D.fetch_epoch(ep, float(req["ra"]), float(req["dec"]), float(req["size"]))
        if epoch is None:
            raise CoverageError(
                f"coord {ra},{dec}: 无法从快照加载 {e.get('obsid')} D{e.get('detector')}"
            )
        epochs.append(epoch)
    if len(epochs) < 2:
        raise CoverageError(f"coord {ra},{dec}: 可用历元 {len(epochs)} < 2")
    epochs.sort(key=lambda x: x.mjd)
    ensure_psf(np, snapshot, epochs)
    result = PIPE.run_pipeline(target, epochs, truth=truth, mode="real")
    return {
        "target_id": target["id"],
        "matched_target": matched,
        "ra": ra,
        "dec": dec,
        "match_sep_arcsec": c.get("match_sep_arcsec"),
        "mode": "real",
        "n_epochs": len(epochs),
        "readiness": result.get("readiness"),
        "class": result.get("class"),
        "mu_mas_yr": finite(result.get("mu_mas_yr")),
        "mu_err_mas_yr": finite(result.get("mu_err_mas_yr")),
        "pa_deg": finite(result.get("pa_deg")),
        "confidence": finite(result.get("confidence")),
        "detected_epochs": result.get("detected_epochs"),
        "quality": result.get("quality"),
    }


# ------------------------------------------------------------------ 输出


def fmt_mu(v):
    return "—" if v is None else f"{v:.2f}"


def fmt_pa(v):
    return "—" if v is None else f"{v:.2f}"


def print_table(rows: list[dict]) -> None:
    print("")
    print(
        f"{'target':<16} {'mode':<7} {'n':>2}  {'readiness':<9} {'class':<5} "
        f"{'mu(mas/yr)':>11}  {'pa(deg)':>8}  {'golden':<16} verdict"
    )
    print("-" * 100)
    for r in rows:
        verdict = "PASS" if r.get("passed") else "FAIL"
        label = r.get("label") or r.get("target_id")
        print(
            f"{label:<16} {str(r.get('mode')):<7} {r.get('n_epochs', 0):>2}  {str(r.get('readiness')):<9} "
            f"{str(r.get('class')):<5} {fmt_mu(r.get('mu_mas_yr')):>11}  {fmt_pa(r.get('pa_deg')):>8}  "
            f"{r.get('golden_source', 'none'):<16} {verdict}"
        )
        for e in r.get("compare_errors", []):
            print(f"  ! {e}")


def main(argv=None):
    args = parse_args(argv)
    snapshot = Path(args.snapshot).expanduser().resolve()
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file():
        print(f"[run_offline] ERROR: 缺少 manifest.json：{snapshot}", file=sys.stderr)
        return 2
    try:
        manifest = common.read_json(manifest_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[run_offline] ERROR: manifest 不可读：{exc}", file=sys.stderr)
        return 2

    env_errors: list[str] = []
    mismatch_errors: list[str] = []
    warnings: list[str] = []
    try:
        check, pf_errors = preflight(snapshot, manifest)
    except common.SnapshotError as exc:
        print(f"[run_offline] ERROR: {exc}", file=sys.stderr)
        return 2
    env_errors.extend(pf_errors)

    # app 指纹对比（只 warning：同 Python 环境下 app 源码若变，结果可能漂移）
    current_fp = common.app_fingerprint(common.app_source_paths())
    frozen_fp = (manifest.get("app_fingerprint") or {}).get("sha256")
    app_drift = bool(frozen_fp and current_fp.get("sha256") != frozen_fp)
    if app_drift:
        warnings.append(
            f"app 指纹与冻结时不同（manifest {str(frozen_fp)[:12]}…，当前 {current_fp.get('sha256', '')[:12]}…）；"
            "若结果不一致，优先重新 freeze"
        )
    elif not frozen_fp:
        warnings.append("manifest 没有 app_fingerprint（旧版快照？）")

    if env_errors:
        print("[run_offline] 快照预检失败，拒绝运行：", file=sys.stderr)
        for e in env_errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    try:
        keymap = build_keymap(manifest)
    except CoverageError as exc:
        print(f"[run_offline] ERROR: {exc}", file=sys.stderr)
        return 2

    np, D, PIPE, SIM = load_app()

    temp_cache = Path(tempfile.mkdtemp(prefix="t3-offline-cache-"))
    attempts, real_urlopen = block_network()
    old_cache, orig_cache_path = install_cache_redirect(D, snapshot, keymap, temp_cache)
    old_sim_data = SIM.DATA
    SIM.DATA = str(snapshot / "support")

    rows: list[dict] = []
    coord_rows: list[dict] = []
    try:
        # 选择目标
        targets = manifest.get("targets") or []
        if args.targets.strip().lower() == "all":
            selected = targets
        else:
            ids = [x.strip() for x in args.targets.split(",") if x.strip()]
            by_id = {str(t.get("id")): t for t in targets}
            missing_ids = [i for i in ids if i not in by_id]
            if missing_ids:
                print(f"[run_offline] ERROR: 快照里没有目标 {missing_ids}", file=sys.stderr)
                return 2
            selected = [by_id[i] for i in ids]

        for t in selected:
            tid = str(t.get("id"))
            golden, golden_source = load_golden(snapshot, manifest, tid)
            try:
                res = run_one_target(np, D, PIPE, SIM, manifest, t, snapshot)
            except CoverageError as exc:
                env_errors.append(str(exc))
                rows.append(
                    {
                        "target_id": tid,
                        "mode": t.get("mode"),
                        "n_epochs": 0,
                        "readiness": None,
                        "class": None,
                        "mu_mas_yr": None,
                        "pa_deg": None,
                        "golden_source": golden_source,
                        "passed": False,
                        "compare_errors": [str(exc)],
                    }
                )
                continue
            passed, checks, cmp_errors = compare(res, golden, args.mu_tol, args.pa_tol)
            res.update(
                {
                    "golden": golden,
                    "golden_source": golden_source,
                    "checks": checks,
                    "compare_errors": cmp_errors,
                    "passed": passed,
                }
            )
            if passed:
                rows.append(res)
            else:
                mismatch_errors.extend(f"{tid}: {e}" for e in cmp_errors)
                rows.append(res)

        if args.coords:
            for c in manifest.get("coords") or []:
                if c.get("status") != "ok":
                    warnings.append(
                        f"coord {c.get('ra')},{c.get('dec')}: status={c.get('status')}，跳过离线像素复跑"
                    )
                    continue
                golden, golden_source = load_golden(snapshot, manifest, str(c.get("matched_target") or ""))
                try:
                    res = run_one_coord(np, D, PIPE, snapshot, c, manifest)
                except CoverageError as exc:
                    env_errors.append(str(exc))
                    continue
                # 精确匹配官方目标（sep<=1″）时，用该目标的黄金结果对比
                if is_exact_match(c):
                    passed, checks, cmp_errors = compare(res, golden, args.mu_tol, args.pa_tol)
                else:
                    passed, checks, cmp_errors = True, {}, ["自定义坐标无独立黄金结果，只验证可运行性"]
                res.update(
                    {
                        "label": f"coord {res['ra']:.4f},{res['dec']:.4f}",
                        "golden": golden,
                        "golden_source": golden_source,
                        "checks": checks,
                        "compare_errors": cmp_errors,
                        "passed": passed,
                    }
                )
                coord_rows.append(res)
                if not passed:
                    mismatch_errors.extend(f"{res['target_id']}: {e}" for e in cmp_errors)
    finally:
        D._cache_path = orig_cache_path
        D.CACHE = old_cache
        SIM.DATA = old_sim_data
        urllib.request.urlopen = real_urlopen
        shutil.rmtree(temp_cache, ignore_errors=True)

    # 网络必须一次都没尝试
    if attempts:
        env_errors.append(f"离线运行中检测到 {len(attempts)} 次网络取数尝试（应全走快照）：{attempts[:2]}")
    if app_drift:
        pass  # 已记入 warnings

    rows_all = rows + coord_rows
    n_tested = len(rows_all)
    n_pass = sum(1 for r in rows_all if r.get("passed"))
    report = {
        "snapshot": str(snapshot),
        "checked_at": common.utc_now_iso(),
        "offline": {"urlopen_blocked": True, "network_attempts": len(attempts)},
        "app_fingerprint": {
            "manifest": frozen_fp,
            "current": current_fp.get("sha256"),
            "drift": app_drift,
        },
        "cache": {"keymap_entries": len(keymap), "source": "snapshot/epochs"},
        "targets": rows,
        "coords": coord_rows,
        "summary": {"tested": n_tested, "passed": n_pass, "failed": n_tested - n_pass},
        "errors": env_errors + mismatch_errors,
        "env_errors": env_errors,
        "mismatch_errors": mismatch_errors,
        "warnings": warnings,
        "passed": not env_errors and not mismatch_errors and n_pass == n_tested,
    }

    print("T3 offline demo run")
    print(f"  snapshot : {snapshot}")
    print(f"  offline  : urllib.request.urlopen blocked · attempts={len(attempts)} · cache=snapshot/epochs ({len(keymap)} keys)")
    print(
        f"  app      : manifest {str(frozen_fp)[:12]}… · current {str(current_fp.get('sha256'))[:12]}…"
        + ("  [DRIFT]" if app_drift else "  [same]")
    )
    print_table(rows_all)
    if warnings:
        print("\nwarnings:")
        for w in warnings:
            print(f"  - {w}")
    all_errors = env_errors + mismatch_errors
    if all_errors:
        print("\nerrors:")
        for e in all_errors:
            print(f"  - {e}")
    print(f"\nRESULT: {'PASS' if report['passed'] else 'FAIL'} ({n_pass}/{n_tested})")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if args.json_out:
        common.write_json_atomic(Path(args.json_out), report)

    if env_errors:
        return 2
    if mismatch_errors:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CoverageError as exc:
        print(f"[run_offline] ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("[run_offline] interrupted", file=sys.stderr)
        sys.exit(130)
