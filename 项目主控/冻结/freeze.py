#!/usr/bin/env python3
"""T3 离线演示冻结器 · freeze

把 5 个官方目标 + 指定自定义坐标所需的 NPZ 切片复制进自包含快照，
并生成 manifest.json（逐历元 sha256/bytes/来源 URL/状态）、checksums.txt、README.md。

设计约束（见 主控提示词/06_T3_离线冻结器.md）：
- 只使用 stdlib + numpy；不启动服务即可 import app/core；
- 缓存路径必须调用 app.core.data._cache_path，不自己拼文件名；
- 表观位置调用 app.core.data.apparent_position；
- 默认不联网；只有显式 --fetch 才补缺失切片，且补采写入临时暂存目录，
  app/data/epochcache 保持只读，绝不删除/覆盖已有 NPZ；
- manifest 内不写绝对路径。

退出码：0=完整；3=已生成但存在 missing/fetch_failed；2=环境/参数错误。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import shutil
import socket
import sys
import tempfile
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402


def die(msg: str, code: int = 2):
    print(f"[freeze] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str):
    print(f"[freeze] WARN: {msg}", file=sys.stderr)


# ------------------------------------------------------------------ 参数


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="T3 离线演示冻结器：生成带 sha256 清单的可移植快照")
    ap.add_argument("--targets", default="all", help="all 或逗号分隔的目标 id（默认 all=5 个官方目标）")
    ap.add_argument("--coords", default="", help='自定义坐标，分号分隔： "ra,dec;ra,dec"')
    ap.add_argument("--fov", type=float, default=0.2, help="切片边长（度），默认 0.2；演示建议 0.25")
    ap.add_argument("--out", default="", help="快照目录（默认 demo/snapshot-<本地日期>）")
    ap.add_argument("--force", action="store_true", help="重建已存在的 T3 快照目录")
    ap.add_argument("--fetch", action="store_true", help="缓存缺失时联网补采（写入临时暂存，不写 app 缓存）")
    ap.add_argument("--max-real", type=int, default=8, help="与 build_dataset 一致的真实历元抽样上限（默认 8）")
    ap.add_argument("--api", default="http://127.0.0.1:8790", help="MoveScope 服务地址（用于冻结 results/*.json）")
    ap.add_argument("--api-timeout", type=float, default=120.0, help="单目标 API 超时秒数")
    ap.add_argument("--no-results", action="store_true", help="不调用 /api/analyze 冻结黄金结果")
    ap.add_argument("--no-resolver", action="store_true", help="不尝试 T2 epoch_source；自定义坐标直接走最近目标 fallback")
    ap.add_argument("--json", action="store_true", help="在 stdout 额外打印 manifest 摘要 JSON")
    return ap.parse_args(argv)


def parse_coords(spec: str) -> list[dict]:
    coords = []
    if not spec:
        return coords
    for i, chunk in enumerate(spec.split(";")):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) != 2:
            die(f"--coords 第 {i + 1} 项格式错误，应为 ra,dec：{chunk!r}")
        try:
            ra, dec = float(parts[0]), float(parts[1])
        except ValueError:
            die(f"--coords 第 {i + 1} 项不是数字：{chunk!r}")
        if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
            die(f"--coords 超出范围：ra={ra}, dec={dec}")
        coords.append({"ra": ra, "dec": dec})
    return coords


# ------------------------------------------------------------------ 输出目录


def prepare_out(out: Path, force: bool) -> None:
    critical = {
        Path("/").resolve(),
        common.ROOT.resolve(),
        common.APP.resolve(),
        (common.ROOT / "项目主控").resolve(),
        (common.SYSTEM / "数据库工具").resolve(),
        (common.SYSTEM / "视图工具").resolve(),
    }
    out = out.resolve()
    if out in critical:
        die(f"拒绝把快照写到受保护目录：{out}")
    for protected in (common.APP, common.SYSTEM / "数据库工具", common.SYSTEM / "视图工具"):
        protected = protected.resolve()
        if out == protected or protected in out.parents:
            die(f"拒绝把快照写到 app/DB/view 源码或数据目录：{out}")
    if out.exists():
        if not out.is_dir():
            die(f"--out 已存在且不是目录：{out}")
        if not force:
            die(f"--out 已存在：{out}\n默认不覆盖；确认后加 --force（只允许重建 T3 快照目录）")
        nonempty = any(out.iterdir())
        if nonempty:
            mf = out / "manifest.json"
            ok = False
            if mf.is_file():
                try:
                    ok = common.read_json(mf).get("tool") == common.TOOL_ID
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                die(f"--force 拒绝删除非 T3 快照目录（无匹配 manifest.json）：{out}")
            shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("epochs", "support", "results"):
        (out / sub).mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ app 数据层


def load_app():
    common.setup_app_path()
    try:
        import numpy as np

        from core import data as D
        from core.simulator import Epoch
        from core.wcs import WCS
    except Exception as exc:  # noqa: BLE001
        die(f"无法 import app/core（{exc}）")
    return np, D, Epoch, WCS


def placeholder_epoch(Epoch, WCS, np, ep, ra, dec, size, cache_path, request):
    w = WCS(
        crval1=float(ra),
        crval2=float(dec),
        crpix1=1.0,
        crpix2=1.0,
        cd=np.eye(2) * 0.001,
        naxis1=16,
        naxis2=16,
    )
    return Epoch(
        id="t3-probe",
        mjd=float(ep["mjd"]),
        image=np.zeros((16, 16), float),
        variance=np.ones((16, 16), float),
        flags=np.zeros((16, 16), np.int32),
        wcs=w,
        psf=None,
        provenance="real",
        band=f"D{ep.get('detector', '?')}",
        obsid=str(ep.get("obsid", "")),
        date_obs=str(ep.get("date_obs", "")),
        source="t3-probe",
        meta={"t3_request": request},
    )


def probe_dataset(D, Epoch, WCS, np, tid: str, fov: float, max_real: int):
    """用 build_dataset 的同一套抽样/视位置规则，记录它会请求哪些缓存。

    把 fetch_epoch 换成“记录请求并返回占位 Epoch”，因此：
    - 抽样规则（dedupe + np.linspace(max_real)）和 apparent_position 完全复用 app；
    - 不读 app 缓存、不联网、不生成真实图像。
    """
    requests: list[dict] = []
    orig_fetch = D.fetch_epoch

    def recorder(ep, ra, dec, size_deg=0.2, *args, **kwargs):
        cache = D._cache_path(ep, ra, dec, size_deg)
        req = {
            "ep": dict(ep),
            "ra": float(ra),
            "dec": float(dec),
            "size": float(size_deg),
            "cache_path": cache,
            "cache_key": os.path.basename(cache),
        }
        requests.append(req)
        return placeholder_epoch(Epoch, WCS, np, ep, ra, dec, size_deg, cache, req)

    D.fetch_epoch = recorder
    try:
        target, epochs, truth, meta = D.build_dataset(tid, mode="auto", max_real=max_real, size_deg=fov)
    finally:
        D.fetch_epoch = orig_fetch

    # fetch 被替换成占位对象时 build_dataset 一定走 real 分支；真实模式从返回数据确认。
    if meta.get("mode") == "real":
        # 用 ex.map 保序返回的占位对象恢复请求（避免线程 append 顺序不确定）
        reqs = []
        for ep_obj in epochs:
            req = (ep_obj.meta or {}).get("t3_request")
            if not req:
                die(f"内部错误：{tid} 的 real 历元请求不可恢复")
            reqs.append(req)
        return target, meta, reqs, None

    sim = simulated_info(tid, epochs, fov, np)
    return target, meta, [], sim


def simulated_info(tid: str, epochs, fov: float, np) -> dict:
    if not epochs:
        return {"generator": "app.core.simulator.generate_epochs", "n_epochs": 0, "times_mjd": []}
    times = [float(e.mjd) for e in epochs]
    digest = common.hashlib.sha256()
    for e in epochs:
        digest.update(np.ascontiguousarray(np.asarray(e.image, dtype="<f8")).tobytes())
        digest.update(np.ascontiguousarray(np.asarray(e.mjd, dtype="<f8")).tobytes())
    return {
        "generator": "app.core.simulator.generate_epochs",
        "provenance": sorted({str(getattr(e, "provenance", "synthetic")) for e in epochs}),
        "n_epochs": len(epochs),
        "seed": int(zlib.crc32(tid.encode()) % 10000),
        "seed_rule": "zlib.crc32(target_id.encode()) % 10000",
        "start_mjd": times[0],
        "end_mjd": times[-1],
        "times_mjd": [round(t, 9) for t in times],
        "fov": fov,
        "background": "support/backgrounds/campoA.npz",
        "psf": "support/psf_d2.npy",
        "generator_defaults": {
            "n_stars": 44,
            "inject_scale": 1.08,
            "inject_pa_shift": 3.5,
            "jitter_px": 0.18,
            "background": "campoA",
        },
        "image_stack_sha256": digest.hexdigest(),
    }


# ------------------------------------------------------------------ 单历元冻结


def validate_npz(np, path: Path) -> tuple[bool, str]:
    required = ("image", "variance", "flags", "mjd", "obsid", "date_obs", "release")
    try:
        with np.load(path, allow_pickle=False) as d:
            missing = [k for k in required if k not in d]
            if missing:
                return False, f"缺少字段 {missing}"
            img = np.asarray(d["image"])
            var = np.asarray(d["variance"])
            flags = np.asarray(d["flags"])
            if img.ndim != 2 or img.size == 0:
                return False, f"image 形状异常 {img.shape}"
            if var.shape != img.shape or flags.shape != img.shape:
                return False, "variance/flags 与 image 形状不一致"
            if not np.isfinite(img).any():
                return False, "image 全为非有限值"
            float(d["mjd"])
            json.loads(str(d["header"]))
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"
    return True, ""


def copy_with_hash(src: Path, dst: Path) -> tuple[str, int]:
    digest = common.sha256_file(src)
    size = src.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if common.sha256_file(dst) != digest:
            raise common.SnapshotError(f"快照内同名文件内容冲突：{dst}")
        return digest, size
    shutil.copyfile(src, dst)
    if common.sha256_file(dst) != digest:
        raise common.SnapshotError(f"复制后校验失败：{dst}")
    return digest, dst.stat().st_size


def fetch_to_staging(D, ep, ra, dec, size, out: Path):
    """显式 --fetch：把 app 缓存重定向到临时目录，补采后返回暂存 NPZ。

    这样 app/data/epochcache 不会被写入；暂存目录由调用方删除。
    """
    staging = Path(tempfile.mkdtemp(prefix=".t3fetch-", dir=str(out.parent)))
    old_cache = D.CACHE
    D.CACHE = str(staging)
    try:
        staged = Path(D._cache_path(ep, ra, dec, size))
        epoch = D.fetch_epoch(ep, ra, dec, size)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        D.CACHE = old_cache
    if epoch is None or not staged.is_file():
        return None, staging
    return staged, staging


def freeze_epochs(D, np, target_record: dict, requests: list[dict], out: Path, args, missing: list, warnings: list):
    tid = target_record["id"]
    entries = []
    for req in requests:
        ep = req["ep"]
        entry = {
            "obsid": str(ep.get("obsid", "")),
            "detector": ep.get("detector"),
            "release": str(ep.get("release", "")),
            "week": ep.get("week"),
            "version": ep.get("version"),
            "field": ep.get("field"),
            "mjd": float(ep.get("mjd")) if ep.get("mjd") is not None else None,
            "date_obs": ep.get("date_obs", ""),
            "key": ep.get("key", ""),
            "cache_key": req["cache_key"],
            "request": {"ra": req["ra"], "dec": req["dec"], "size": req["size"]},
            "file": None,
            "sha256": None,
            "bytes": None,
            "status": "missing",
            "source": "cache",
            "fetched_at": None,
        }
        try:
            entry["slice_url"] = D.slice_url(ep, req["ra"], req["dec"], req["size"])
        except Exception:  # noqa: BLE001
            entry["slice_url"] = None

        src = Path(req["cache_path"])
        usable = src.is_file()
        corrupt_reason = ""
        if usable:
            ok, corrupt_reason = validate_npz(np, src)
            usable = ok
        if not usable and args.fetch:
            warn(f"{tid} {entry['obsid']} D{entry['detector']} 缓存缺失/损坏，--fetch 补采中…")
            staged, staging = fetch_to_staging(D, ep, req["ra"], req["dec"], req["size"], out)
            try:
                if staged is None:
                    entry["status"] = "fetch_failed"
                    entry["source"] = "fetch"
                    missing.append(
                        {
                            "owner": f"target:{tid}",
                            "obsid": entry["obsid"],
                            "detector": entry["detector"],
                            "status": "fetch_failed",
                            "reason": "fetch_epoch returned None",
                            "slice_url": entry.get("slice_url"),
                        }
                    )
                else:
                    src = staged
                    usable = True
                    entry["source"] = "fetch"
                    entry["fetched_at"] = common.utc_now_iso()
            finally:
                shutil.rmtree(staging, ignore_errors=True)

        if usable:
            ok, why = validate_npz(np, src)
            if ok:
                rel = f"epochs/{tid}/{req['cache_key']}"
                digest, size = copy_with_hash(src, out / rel)
                entry.update(
                    {
                        "file": rel,
                        "sha256": digest,
                        "bytes": size,
                        "status": "ok",
                    }
                )
            else:
                entry["status"] = "fetch_failed" if args.fetch else "missing"
                entry["fetch_error"] = why
                warnings.append(f"corrupt_cache:{tid}:{entry['obsid']}:D{entry['detector']}")
                missing.append(
                    {
                        "owner": f"target:{tid}",
                        "obsid": entry["obsid"],
                        "detector": entry["detector"],
                        "status": entry["status"],
                        "reason": f"corrupt_cache: {why}",
                        "slice_url": entry.get("slice_url"),
                    }
                )
        elif entry["status"] == "missing":
            reason = corrupt_reason or "cache_not_found"
            entry["missing_reason"] = reason
            missing.append(
                {
                    "owner": f"target:{tid}",
                    "obsid": entry["obsid"],
                    "detector": entry["detector"],
                    "status": "missing",
                    "reason": reason,
                    "slice_url": entry.get("slice_url"),
                }
            )
        entries.append(entry)
    return entries


# ------------------------------------------------------------------ API 黄金结果


def api_reachable(api: str, timeout: float = 1.0) -> bool:
    try:
        from urllib.parse import urlparse

        u = urlparse(api)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _finite(v):
    try:
        import math

        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def freeze_api_result(tid: str, fov: float, out: Path, args) -> tuple[str | None, dict | None, str]:
    """调用运行中的 /api/analyze 冻结黄金结果。返回 (rel_path, record, status)。"""
    if args.no_results:
        return None, None, "skipped_no_results"
    if not api_reachable(args.api):
        return None, None, "skipped_api_unreachable"
    url = args.api.rstrip("/") + "/api/analyze"
    payload = json.dumps({"target_id": tid, "mode": "auto", "fov": fov}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "t3-offline-freezer/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.api_timeout) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        warn(f"{tid} API 冻结失败：{type(exc).__name__}: {str(exc)[:160]}")
        return None, None, "skipped_api_error"
    if not isinstance(data, dict) or data.get("error"):
        warn(f"{tid} API 返回错误：{data.get('error') if isinstance(data, dict) else data!r}")
        return None, None, "skipped_api_error"

    res = data.get("result", {}) or {}
    dat = data.get("data", {}) or {}
    golden = {
        "schema_version": "1.0",
        "target_id": tid,
        "frozen_at": common.utc_now_iso(),
        "fov": fov,
        "source": url,
        "status": "ok",
        "data_mode": dat.get("mode"),
        "readiness": res.get("readiness"),
        "class": res.get("class"),
        "mu_mas_yr": _finite(res.get("mu_mas_yr")),
        "mu_err_mas_yr": _finite(res.get("mu_err_mas_yr")),
        "pa_deg": _finite(res.get("pa_deg")),
        "confidence": _finite(res.get("confidence")),
        "n_epochs": dat.get("n_epochs"),
        "detected_epochs": res.get("detected_epochs"),
        "provenance": dat.get("provenance"),
        "quality": res.get("quality"),
        "truth_check": res.get("truth_check"),
    }
    rel = f"results/{tid}.json"
    common.write_json_atomic(out / rel, golden)
    return rel, golden, "ok"


# ------------------------------------------------------------------ 目标/坐标记录


def target_status(mode: str, entries: list[dict]) -> str:
    if mode == "real":
        if any(e["status"] == "fetch_failed" for e in entries):
            return "fetch_failed"
        if any(e["status"] != "ok" for e in entries):
            return "missing"
        if sum(1 for e in entries if e["status"] == "ok") < 2:
            return "missing"
        return "ok"
    if mode == "hybrid":
        return "hybrid_no_pixels"
    return "no_coverage"


def nearest_target(ra: float, dec: float, targets: list[dict], catalog: dict) -> tuple[dict | None, float]:
    best = None
    best_sep = None
    for t in targets:
        if not catalog.get(t["id"]):
            continue
        sep = angular_sep_deg(ra, dec, float(t["ra"]), float(t["dec"]))
        if best_sep is None or sep < best_sep:
            best, best_sep = t, sep
    return best, (best_sep if best_sep is not None else 180.0)


def angular_sep_deg(ra1, dec1, ra2, dec2) -> float:
    import math

    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((a2 - a1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s))))


# ------------------------------------------------------------------ T2 resolver（可选）


def try_epoch_resolver(D, ra: float, dec: float, fov: float, max_real: int) -> dict:
    """尝试 T2 的 epoch_source.resolve_epochs；任何失败都返回 ok=False 由调用方 fallback。"""
    try:
        from core import epoch_source as ES
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"import:{type(exc).__name__}", "source": "none"}
    try:
        res = ES.resolve_epochs(ra, dec, fov)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"resolve:{type(exc).__name__}", "source": "none"}
    raw = list(getattr(res, "epochs", []) or [])
    source = str(getattr(res, "source", "none"))
    if not getattr(res, "ok", False) or not raw:
        return {
            "ok": False,
            "reason": str(getattr(res, "fallback_reason", None) or "no_local_coverage"),
            "source": source,
        }
    sampler = getattr(D, "_sample_epochs", None)
    if callable(sampler):
        sampled = sampler(raw, max_real)
    elif len(raw) > max_real:
        import numpy as _np

        idx = _np.linspace(0, len(raw) - 1, max_real).round().astype(int)
        sampled = [raw[i] for i in sorted(set(idx.tolist()))]
    else:
        sampled = list(raw)
    return {
        "ok": True,
        "source": source,
        "raw_n": len(raw),
        "epochs": sampled,
        "matched_target_id": getattr(res, "matched_target_id", None),
        "coverage": getattr(res, "coverage", None),
        "warnings": list(getattr(res, "warnings", []) or []),
    }


def resolver_matches_frozen(D, res_info: dict, target: dict | None, matched: dict, fov: float, ra: float, dec: float):
    """用 app 的 apparent_position/_cache_path 对 resolver 历元重算缓存键，和目标冻结文件对比。"""
    keys = set()
    for ep in res_info.get("epochs") or []:
        try:
            if target is not None:
                ra_ep, dec_ep = D.apparent_position(target, float(ep["mjd"]))
            else:
                ra_ep, dec_ep = ra, dec
            path = D._cache_path(ep, ra_ep, dec_ep, fov)
        except Exception:  # noqa: BLE001
            return None, None, False
        keys.add(os.path.basename(path))
    frozen = {
        str(e.get("cache_key"))
        for e in matched.get("epochs") or []
        if e.get("status") == "ok" and e.get("cache_key")
    }
    return keys, frozen, bool(keys) and keys == frozen


# ------------------------------------------------------------------ README


def snapshot_readme(manifest: dict) -> str:
    s = manifest.get("summary", {})
    lines = [
        "# T3 离线演示快照",
        "",
        f"- 生成时间：`{manifest.get('generated_at')}`",
        f"- schema：`{manifest.get('schema_version')}`",
        f"- FOV：`{manifest.get('fov')}` 度",
        f"- app 指纹：`{(manifest.get('app_fingerprint') or {}).get('sha256', 'n/a')}`",
        f"- Python：`{manifest.get('python')}` · NumPy：`{manifest.get('numpy')}`",
        "",
        "## 这是什么",
        "",
        "这是 MoveScope 5 个官方目标（以及冻结时指定的自定义坐标）所需的 **SPHEREx NPZ 切片副本** +",
        "逐文件 sha256 清单 + 冻结时的黄金结果。目标是在断网、且不依赖 `app/data/epochcache` 的情况下",
        "复现同一组 `mode / readiness / class / μ`。",
        "",
        "**不是**原始 FITS 存档，**不含** EPSF/整幅 65 MB 曝光，也**不生成**新历元。",
        "RW Aur / R Dra 没有真实 NPZ，按 HYBRID（模拟时序）冻结输入参数，因此不占像素文件。",
        "",
        "## 摘要",
        "",
        f"- 目标：{s.get('n_targets', 0)}（ok {s.get('n_targets_ok', 0)} / hybrid {s.get('n_targets_hybrid', 0)}）",
        f"- 自定义坐标：{s.get('n_coords', 0)}",
        f"- 真实历元文件：{s.get('n_epoch_files', 0)}",
        f"- 结果文件：{s.get('n_result_files', 0)}",
        f"- 文件总数：{s.get('n_files_total', 0)}（{common.fmt_bytes(s.get('total_bytes', 0))}）",
        f"- 缺失：{s.get('n_missing', 0)}",
        "",
        "## 校验",
        "",
        "```bash",
        "python3 项目主控/冻结/verify.py --snapshot . --strict",
        "# 或直接：",
        "shasum -a 256 -c checksums.txt",
        "```",
        "",
        "## 断网运行 5 目标",
        "",
        "```bash",
        "python3 项目主控/冻结/run_offline.py --snapshot . --targets all",
        "```",
        "",
        "`run_offline.py` 会把 `urllib.request.urlopen` 替换成抛错函数，并把 `_cache_path` 重定向到",
        "本快照的 `epochs/`；若快照缺文件，会直接报错退出（退出码 2），不会静默退回模拟数据。",
        "",
        "## 目标",
        "",
        "| id | mode | status | 历元 | 文件 |",
        "|---|---|---|---|---|",
    ]
    for t in manifest.get("targets", []):
        n_ok = sum(1 for e in t.get("epochs", []) if e.get("status") == "ok")
        lines.append(f"| `{t.get('id')}` | {t.get('mode')} | {t.get('status')} | {n_ok} | {t.get('results_file') or '—'} |")
    if manifest.get("coords"):
        lines += ["", "## 自定义坐标", "", "| ra | dec | resolver | matched | sep(arcsec) | status | 历元 |", "|---|---|---|---|---|---|---|"]
        for c in manifest["coords"]:
            n_ok = sum(1 for e in c.get("epochs", []) if e.get("status") == "ok")
            lines.append(
                f"| {c.get('ra')} | {c.get('dec')} | {c.get('resolver')} | {c.get('matched_target') or '—'} | "
                f"{c.get('match_sep_arcsec')} | {c.get('status')} | {n_ok} |"
            )
    lines += [
        "",
        "## 已知限制",
        "",
        "- RW Aur / R Dra 没有真实像素：`mode=hybrid`、`status=hybrid_no_pixels`，只冻结模拟输入（种子/时间轴/PSF/背景）。",
        "- 任意未被本地时代目录覆盖的坐标会得到 `no_coverage`；当前自定义坐标用“最近目标”退化解析（`resolver=fallback`）。",
        "- 缓存键依赖冻结时的 `app/core/data.py::_cache_path`；换 app 版本后请重新 freeze，不能把旧快照当新缓存。",
        "- 快照只覆盖 NPZ 切片与少量模拟支撑文件（PSF、背景），不含 FITS/EPSF，不能用于重新测光或改 WCS。",
        "- `run_offline.py` 校验的是结果一致性（class/readiness 严格，μ ±5%、PA ±5°），不是像素级 bit 对比。",
        "",
        "## manifest 关键字段",
        "",
        "- `targets[].epochs[]`：`obsid/detector/release/version/mjd/key/slice_url/cache_key/file/sha256/bytes/status`。",
        "- `status`：`ok | missing | fetch_failed | no_coverage | hybrid_no_pixels`。",
        "- `manifest.app_fingerprint`：冻结时 app 关键源码/数据 sha256；换版本后结果不可比。",
        "- `coords[]`：输入坐标、resolver、匹配目标、匹配角距；epochs 复用匹配目标的冻结文件。",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ 主流程


def main(argv=None):
    args = parse_args(argv)
    if args.fov <= 0 or args.fov > 5:
        die(f"--fov 不合理：{args.fov}")
    if args.max_real < 2:
        die("--max-real 至少为 2（与 build_dataset 的真实模式一致）")
    coords_in = parse_coords(args.coords)

    out = Path(args.out).expanduser() if args.out else Path("demo") / f"snapshot-{common.local_today()}"
    if not out.is_absolute():
        out = Path.cwd() / out
    out = out.resolve()

    np, D, Epoch, WCS = load_app()
    all_targets = D.load_targets()
    official = [t for t in all_targets if t.get("official")]
    catalog = D.load_epoch_catalog()

    if args.targets.strip().lower() == "all":
        selected = official
    else:
        ids = [x.strip() for x in args.targets.split(",") if x.strip()]
        selected = []
        for tid in ids:
            try:
                selected.append(D.get_target(tid))
            except KeyError:
                die(f"未知目标 id：{tid}（可选：{', '.join(t['id'] for t in all_targets)}）")
    if not selected and not coords_in:
        die("没有选择任何目标/坐标")

    # 先 probe（只读 app），确认能写快照后再创建目录，避免半成品目录。
    probes = {}
    for t in selected:
        try:
            probes[t["id"]] = probe_dataset(D, Epoch, WCS, np, t["id"], args.fov, args.max_real)
        except Exception as exc:  # noqa: BLE001
            die(f"解析目标 {t['id']} 的历元失败：{type(exc).__name__}: {exc}")

    prepare_out(out, args.force)
    missing: list[dict] = []
    warnings: list[str] = []
    targets_out: list[dict] = []
    target_cache: dict[str, dict] = {}

    # 支撑文件（HYBRID 模拟需要 PSF/背景；可移植性必需）
    support = freeze_support(out)

    def build_target_record(t: dict, freeze_results: bool = True) -> dict:
        tid = t["id"]
        target, meta, requests, sim = probes[tid]
        mode = meta.get("mode", "synthetic")
        results_rel = None
        results_status = "skipped_incomplete"
        golden = None

        # 构建历元（real）或模拟输入（hybrid/synthetic）
        entries: list[dict] = []
        if mode == "real":
            record_stub = {"id": tid}
            entries = freeze_epochs(D, np, record_stub, requests, out, args, missing, warnings)
        status = target_status(mode, entries)

        if not freeze_results:
            # 只为自定义坐标懒加载匹配目标：不写 results/，否则会变成 manifest 之外的“多余文件”。
            results_status = "skipped_not_in_targets"
        elif mode == "real" and status == "ok":
            results_rel, golden, results_status = freeze_api_result(tid, args.fov, out, args)
        elif mode == "hybrid":
            results_rel, golden, results_status = freeze_api_result(tid, args.fov, out, args)
        else:
            results_status = "skipped_incomplete"
        if golden and mode != "real" and golden.get("data_mode") and golden.get("data_mode") != mode:
            warnings.append(f"api_mode_mismatch:{tid}:{golden.get('data_mode')}!={mode}")

        record = {
            "id": tid,
            "name": t.get("name"),
            "ra": t.get("ra"),
            "dec": t.get("dec"),
            "official": bool(t.get("official")),
            "mode_expected": mode,
            "mode": mode,
            "status": status,
            "network_required": False,
            "n_epochs": sum(1 for e in entries if e["status"] == "ok"),
            "epochs": entries,
            "simulation": sim if mode != "real" else None,
            "results_file": results_rel,
            "results_status": results_status,
            "note": meta.get("note", ""),
        }
        if results_rel:
            p = out / results_rel
            record["results_sha256"] = common.sha256_file(p)
            record["results_bytes"] = p.stat().st_size
        if mode == "real" and status == "ok" and results_status != "ok" and not args.no_results:
            warnings.append(f"results_unavailable:{tid}:{results_status}")
        if mode == "real" and status != "ok":
            warnings.append(f"target_incomplete:{tid}:{status}")
        return record

    for t in selected:
        record = build_target_record(t)
        targets_out.append(record)
        target_cache[t["id"]] = record
        print(
            f"[freeze] {record['id']:<10} mode={record['mode']:<8} status={record['status']:<16} "
            f"epochs={record['n_epochs']} results={record['results_status']}"
        )

    # 自定义坐标：T2 epoch_source 尚不可用时退化为最近目录目标
    coords_out: list[dict] = []
    for c in coords_in:
        ra, dec = c["ra"], c["dec"]
        mt, sep_deg = nearest_target(ra, dec, all_targets, catalog)
        sep_arcsec = sep_deg * 3600.0
        base = {
            "ra": ra,
            "dec": dec,
            "fov": args.fov,
            "resolver": "fallback",
            "resolver_detail": "nearest_epoch_catalog_target",
            "matched_target": mt["id"] if mt else None,
            "match_sep_arcsec": round(sep_arcsec, 3),
            "status": "no_coverage",
            "n_epochs": 0,
            "epochs": [],
            "simulation": None,
            "results_file": None,
            "note": "",
        }
        if mt is None:
            base["note"] = "epoch_catalog 为空；无法解析历元。"
            coords_out.append(base)
            warnings.append(f"coord_no_catalog:{ra},{dec}")
            continue
        if mt["id"] not in target_cache:
            if mt["id"] not in probes:
                try:
                    probes[mt["id"]] = probe_dataset(D, Epoch, WCS, np, mt["id"], args.fov, args.max_real)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"coord_match_probe_failed:{mt['id']}:{type(exc).__name__}")
                    base["note"] = f"最近目标 {mt['id']} 解析失败。"
                    coords_out.append(base)
                    continue
            target_cache[mt["id"]] = build_target_record(mt, freeze_results=False)
        matched = target_cache[mt["id"]]
        base["matched_mode"] = matched.get("mode")

        # T2 resolver：可用则用它解析坐标；只有解析出的缓存键与已冻结目标文件一致时才复用像素，
        # 否则明确退回 fallback（不把别处的图冒充成该坐标）。
        resolver_used = False
        if not args.no_resolver:
            res_info = try_epoch_resolver(D, ra, dec, args.fov, args.max_real)
            base["resolver_attempt"] = "epoch_source"
            base["resolver_source"] = res_info.get("source")
            if res_info.get("ok"):
                base["resolver_epochs"] = res_info.get("raw_n")
                base["resolver_sampled"] = len(res_info.get("epochs") or [])
                res_tid = res_info.get("matched_target_id") or mt["id"]
                res_target = None
                try:
                    res_target = D.get_target(str(res_tid))
                except KeyError:
                    res_target = None
                if res_target and str(res_target.get("id")) == mt["id"] and matched.get("mode") == "real":
                    keys, frozen_keys, same = resolver_matches_frozen(
                        D, res_info, res_target, matched, args.fov, ra, dec
                    )
                    base["resolver_cache_keys_match"] = bool(same)
                    if same:
                        base["resolver"] = "epoch_source"
                        base["resolver_detail"] = str(res_info.get("source") or "db")
                        resolver_used = True
                    else:
                        base["resolver_detail"] = "resolver_keys_mismatch->fallback"
                        warnings.append(
                            f"coord_resolver_keys_mismatch:{ra},{dec}:matched={mt['id']}"
                            f":resolver={len(keys or [])}:frozen={len(frozen_keys or [])}"
                        )
                else:
                    base["resolver_detail"] = "resolver_target_mismatch->fallback"
            else:
                base["resolver_error"] = res_info.get("reason")
        else:
            base["resolver_attempt"] = "disabled"

        if sep_deg > args.fov / 2.0:
            base["note"] = (
                f"最近目录目标 {mt['id']} 距输入坐标 {sep_arcsec:.1f}″，超过 FOV/2="
                f"{args.fov * 1800:.0f}″；不冻结像素，避免把别的天区冒充本坐标。"
            )
            warnings.append(f"coord_no_coverage:{ra},{dec}:nearest={mt['id']}:sep={sep_arcsec:.1f}arcsec")
        elif matched.get("mode") == "real":
            base["epochs"] = copy.deepcopy(matched.get("epochs", []))
            ok = sum(1 for e in base["epochs"] if e.get("status") == "ok")
            base["n_epochs"] = ok
            bad = [e for e in base["epochs"] if e.get("status") != "ok"]
            base["status"] = matched.get("status") if bad else "ok"
            if resolver_used:
                base["note"] = (
                    f"T2 epoch_source（{base.get('resolver_detail')}）解析到 "
                    f"{base.get('resolver_epochs')} 期，抽样 {base.get('resolver_sampled')} 期；"
                    f"缓存键与最近目标 {mt['id']} 的冻结文件完全一致，复用像素文件。"
                )
            else:
                base["note"] = (
                    f"T2 epoch_source 不可用/未命中；按 epoch_catalog 最近目标 {mt['id']} 退化解析，"
                    f"历元像素复用该目标冻结文件（表观位置由目标 truth 计算）。"
                )
            if bad:
                warnings.append(f"coord_incomplete:{ra},{dec}:{base['status']}")
        elif matched.get("mode") == "hybrid":
            base["status"] = "hybrid_no_pixels"
            base["note"] = (
                f"最近目标 {mt['id']} 为 HYBRID（无真实 NPZ）；自定义坐标的离线像素覆盖需 T2 resolver。"
            )
        else:
            base["status"] = "no_coverage"
            base["note"] = f"最近目标 {mt['id']} 无本地历元覆盖。"
        coords_out.append(base)
        print(
            f"[freeze] coord {ra:.6f},{dec:.6f} -> {mt['id']} sep={sep_arcsec:.1f}″ "
            f"resolver={base.get('resolver')} status={base['status']}"
        )

    # 组装 manifest（先算 summary 需要文件集合）
    app_files = common.app_source_paths()
    manifest = {
        "schema_version": common.SCHEMA_VERSION,
        "tool": common.TOOL_ID,
        "generated_at": common.utc_now_iso(),
        "app_git": "no-git-or-tag",
        "app_fingerprint": common.app_fingerprint(app_files),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "fov": args.fov,
        "max_real": args.max_real,
        "cache_source": "app.core.data._cache_path",
        "targets": targets_out,
        "coords": coords_out,
        "support": support,
        "missing": missing,
        "warnings": warnings,
        "results_api": args.api,
        "reproduce": {
            "freeze": "python3 项目主控/冻结/freeze.py --targets all --coords \"...\" --fov <fov> --out <out>",
            "verify": "python3 项目主控/冻结/verify.py --snapshot <out> --strict",
            "run_offline": "python3 项目主控/冻结/run_offline.py --snapshot <out> --targets all",
        },
    }

    files = common.expected_files(manifest)
    epoch_rels = [r for r, m in files.items() if m["role"] == "epoch"]
    support_rels = [r for r, m in files.items() if m["role"] == "support"]
    result_rels = [r for r, m in files.items() if m["role"] == "results"]
    summary = {
        "n_targets": len(targets_out),
        "n_targets_ok": sum(1 for t in targets_out if t.get("status") == "ok"),
        "n_targets_hybrid": sum(1 for t in targets_out if t.get("status") == "hybrid_no_pixels"),
        "n_coords": len(coords_out),
        "n_coords_ok": sum(1 for c in coords_out if c.get("status") == "ok"),
        "n_epochs_ok": sum(1 for t in targets_out for e in t.get("epochs", []) if e.get("status") == "ok"),
        "n_epoch_files": len(epoch_rels),
        "n_support_files": len(support_rels),
        "n_result_files": len(result_rels),
        "n_files_total": len(files),
        "total_bytes": sum((m.get("bytes") or 0) for m in files.values()),
        "epoch_bytes": sum((files[r].get("bytes") or 0) for r in epoch_rels),
        "support_bytes": sum((files[r].get("bytes") or 0) for r in support_rels),
        "result_bytes": sum((files[r].get("bytes") or 0) for r in result_rels),
        "n_missing": len(missing),
        "n_warnings": len(warnings),
    }
    manifest["summary"] = summary

    # 路径安全检查（manifest 内不得有绝对路径/..）
    problems = common.scan_manifest_paths(manifest)
    if problems:
        for p in problems:
            print(f"[freeze] path-check: {p}", file=sys.stderr)
        die("manifest 路径安全检查失败（见上）")

    common.write_checksums(out, files)
    common.write_json_atomic(out / "manifest.json", manifest)
    common.write_text_atomic(out / "README.md", snapshot_readme(manifest))

    print("")
    print(f"[freeze] snapshot: {out}")
    print(
        f"[freeze] targets={summary['n_targets']} (ok={summary['n_targets_ok']}, hybrid={summary['n_targets_hybrid']}) "
        f"coords={summary['n_coords']} epochs={summary['n_epochs_ok']} "
        f"files={summary['n_files_total']} bytes={summary['total_bytes']} ({common.fmt_bytes(summary['total_bytes'])})"
    )
    if missing:
        print(f"[freeze] INCOMPLETE: {len(missing)} 个历元缺失/补采失败（manifest.missing 可查）", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps({"manifest": str(out / "manifest.json"), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


def freeze_support(out: Path) -> list[dict]:
    support = []
    pairs = [
        (common.APP / "data" / "psf_d2.npy", "support/psf_d2.npy", "psf"),
        (common.APP / "data" / "backgrounds" / "campoA.npz", "support/backgrounds/campoA.npz", "background"),
    ]
    for src, rel, role in pairs:
        if not src.is_file():
            die(f"缺少 HYBRID 支撑文件：{src}（没有它无法复现模拟时序）")
        digest, size = copy_with_hash(src, out / rel)
        support.append(
            {
                "file": rel,
                "role": role,
                "source": src.relative_to(common.ROOT).as_posix(),
                "sha256": digest,
                "bytes": size,
            }
        )
    return support


if __name__ == "__main__":
    try:
        sys.exit(main())
    except common.SnapshotError as exc:
        print(f"[freeze] ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("[freeze] interrupted", file=sys.stderr)
        sys.exit(130)
