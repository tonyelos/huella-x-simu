#!/usr/bin/env python3
"""MoveScope 本地服务：静态前端 + JSON API + 图像渲染。

只用标准库 + numpy + Pillow。启动：
    python3 app/server.py --port 8790

错误约定：
- 参数错误返回 400，body = {"error": {"code": "...", "message": "..."}}
- 未知分析/帧返回 404
- 未预期异常返回 500，带可读 message（不泄露 traceback 给客户端）
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import threading
import uuid
import warnings
import zlib
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from core import catalog as catalog_layer  # noqa: E402
from core import data as data_layer  # noqa: E402
from core import epoch_source  # noqa: E402
from core import pipeline  # noqa: E402
from core.simulator import Epoch  # noqa: E402
from core.wcs import ARCSEC, WCS, angular_sep, gnomonic_inverse  # noqa: E402

WEB = os.path.join(HERE, "web")

FOV_MIN = 0.02
FOV_MAX = 3.0  # 与 UI/文档保持一致；3.0° 约 246 MB 峰值估算，低于 300 MB 上限
MAX_ALLOC_BYTES = 300 * 1024 * 1024
MAX_RESULTS = 12
MAX_RESULT_BYTES = 256 * 1024 * 1024
MAX_PNG_PX = 2048  # 单帧 PNG 最大边长：0.25° 仍按 ×4 放大（≈584 px），大 FOV 不做无意义上采样
VALID_MODES = {"auto", "real", "synthetic", "sample"}

# D 阶段：B 星表服务的 app 侧客户端。测试/部署可通过
# CATALOG_URL（兼容 MOVESCOPE_CATALOG_URL）/ MOVESCOPE_CATALOG_TIMEOUT 覆盖。
CATALOG_CLIENT = catalog_layer.CatalogClient()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ------------------------------------------------------------------ 色彩


def _sanitize(obj):
    """JSON 不允许 NaN/Inf；递归转成 null。"""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return _sanitize(obj.tolist())
    return obj


def _lut(anchors: list[tuple[float, tuple[int, int, int]]], n: int = 256) -> np.ndarray:
    xs = np.array([a[0] for a in anchors])
    cols = np.array([a[1] for a in anchors], float)
    grid = np.linspace(0, 1, n)
    out = np.zeros((n, 3))
    for c in range(3):
        out[:, c] = np.interp(grid, xs, cols[:, c])
    return out.astype(np.uint8)


CMAP_SPACE = _lut(
    [
        (0.0, (4, 6, 11)),
        (0.16, (10, 15, 30)),
        (0.34, (20, 28, 52)),
        (0.52, (52, 48, 66)),
        (0.68, (118, 76, 54)),
        (0.83, (196, 132, 66)),
        (0.94, (238, 196, 128)),
        (1.0, (255, 248, 230)),
    ]
)
CMAP_DIFF = _lut(
    [
        (0.0, (28, 110, 200)),
        (0.35, (20, 52, 96)),
        (0.5, (8, 10, 16)),
        (0.65, (96, 40, 36)),
        (1.0, (250, 180, 80)),
    ]
)


def stretch_to_rgb(img: np.ndarray, cmap: np.ndarray, lo=None, hi=None, gamma: float = 0.5) -> np.ndarray:
    a = np.asarray(img, float)
    finite = np.isfinite(a)
    if not finite.any():
        return np.zeros((*a.shape, 3), np.uint8)
    vals = a[finite]
    if lo is None:
        lo = float(np.percentile(vals, 42))
    if hi is None:
        hi = float(np.percentile(vals, 99.85))
    span = max(hi - lo, 1e-9)
    x = np.clip((a - lo) / span, 0, 1)
    x = np.arcsinh(x / gamma) / math.asinh(1 / gamma)
    x = np.clip(x, 0, 1)
    x[~finite] = 0
    idx = (x * 255).astype(np.uint8)
    return cmap[idx]


def png_bytes(rgb: np.ndarray, scale: int = 4) -> bytes:
    h, w = rgb.shape[:2]
    if scale > 1:
        # 大 FOV 的输入本身已足够大：限制上采样后的最大边长，避免单请求生成
        # 数千像素级的 PNG（3.0° 输入约 1756 px，×4 会变成 7024 px）。
        scale = max(1, min(int(scale), MAX_PNG_PX // max(int(h), int(w))))
        if scale > 1:
            im = Image.fromarray(rgb, "RGB")
            im = im.resize((im.width * scale, im.height * scale), Image.BILINEAR)
    else:
        im = Image.fromarray(rgb, "RGB")
    buf = io.BytesIO()
    # 大图不必为了几 % 的体积做全量 optimize（3.0° 帧会慢到不可用）
    im.save(buf, format="PNG", optimize=max(im.width, im.height) <= 1024)
    return buf.getvalue()


# ------------------------------------------------------------------ 参数校验


def validate_fov(raw) -> float:
    try:
        fov = float(raw)
    except (TypeError, ValueError):
        raise ApiError(400, "INVALID_FOV", "视场必须是数字（单位：度）。")
    if not math.isfinite(fov):
        raise ApiError(400, "INVALID_FOV", "视场必须是有限数字。")
    if fov < FOV_MIN:
        raise ApiError(400, "INVALID_FOV", f"视场不能小于 {FOV_MIN} 度（当前 {fov:g}°）。")
    if fov > FOV_MAX:
        raise ApiError(
            400,
            "FOV_TOO_LARGE",
            f"视场不能大于 {FOV_MAX} 度（当前 {fov:g}°）；过大视场会在单请求中分配过多内存。",
        )
    n = fov * 3600 / 6.15
    est = n * n * 8 * 10  # 保守估计：每像素 8 字节 × 约 10 份拷贝
    if est > MAX_ALLOC_BYTES:
        raise ApiError(
            400,
            "FOV_TOO_LARGE",
            f"该视场约需 {est / 1e6:.0f} MB，超过单请求 {MAX_ALLOC_BYTES // 1024 // 1024} MB 上限；请减小 FOV。",
        )
    return fov


def _coord(raw, name: str, lo: float, hi: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ApiError(400, "INVALID_COORDINATE", f"{name} 必须是数字。")
    if not math.isfinite(v) or v < lo or (v > hi if lo < 0 else v >= hi):
        raise ApiError(400, "INVALID_COORDINATE", f"{name} 超出范围 [{lo}, {hi}{'' if lo < 0 else ')'}）。")
    return v


def resolve_request(payload: dict):
    """返回 (target_id, ra, dec, target_or_None)。校验目标与坐标冲突。"""
    tid = payload.get("target_id")
    has_ra = payload.get("ra") is not None
    has_dec = payload.get("dec") is not None
    if has_ra != has_dec:
        raise ApiError(400, "MISSING_COORDINATE", "ra 与 dec 必须同时提供（度）。")
    ra = dec = None
    if has_ra:
        ra = _coord(payload.get("ra"), "赤经 RA", 0.0, 360.0)
    if has_dec:
        dec = _coord(payload.get("dec"), "赤纬 Dec", -90.0, 90.0)
    if tid and tid != "custom":
        try:
            target = data_layer.get_target(tid)
        except KeyError:
            raise ApiError(400, "UNKNOWN_TARGET", f"未知目标 {tid!r}。")
        if has_ra and has_dec:
            if abs(ra - target["ra"]) > 1e-6 or abs(dec - target["dec"]) > 1e-6:
                raise ApiError(
                    400,
                    "TARGET_COORD_CONFLICT",
                    "target_id 与 ra/dec 冲突；请只传 target_id，或改用 target_id=custom 查自定义坐标。",
                )
        return tid, float(target["ra"]), float(target["dec"]), target
    if not (has_ra and has_dec):
        raise ApiError(400, "MISSING_COORDINATE", "需要同时提供 ra 和 dec（度）。")
    return "custom", ra, dec, None


PRESET_MATCH_TOL_DEG = 0.05


def match_preset_target(ra: float, dec: float, tol: float = PRESET_MATCH_TOL_DEG):
    """自定义坐标 0.05° 内命中的最近预设目标；无命中返回 None。"""
    best, best_sep = None, tol
    for target in data_layer.load_targets():
        try:
            sep = angular_sep(float(ra), float(dec), float(target["ra"]), float(target["dec"]))
        except (KeyError, TypeError, ValueError):
            continue
        if sep <= best_sep:
            best, best_sep = target, sep
    return best


def matched_custom_target(ra: float, dec: float, preset: dict) -> dict:
    """响应里的自定义目标身份：保留用户坐标，同时标注命中的预设目标。"""
    return {
        "id": "custom",
        "name": f"RA {ra:.4f}  Dec {dec:.4f}",
        "name_zh": "自定义天区",
        "ra": float(ra),
        "dec": float(dec),
        "type": preset.get("type", "unknown"),
        "expected": preset.get("expected", "U"),
        "note": preset.get("note", ""),
        "truth": dict(preset.get("truth") or {}),
        "coord_unc_arcsec": float(preset.get("coord_unc_arcsec") or 0.0),
        "matched_target_id": preset.get("id"),
        "matched_name": preset.get("name"),
    }


# ------------------------------------------------------------------ 结果存储


def _rendered_bytes(rendered: dict) -> int:
    total = len(rendered.get("stack", b""))
    for f in rendered.get("frames", []):
        total += len(f.get("main", b"")) + len(f.get("diff", b""))
    return total


class ResultStore:
    """有容量上限的分析结果缓存：LRU + 总字节上限。"""

    def __init__(self, max_entries: int = MAX_RESULTS, max_bytes: int = MAX_RESULT_BYTES):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._items: "OrderedDict[str, dict]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def put(self, aid: str, rendered: dict) -> None:
        size = _rendered_bytes(rendered)
        with self._lock:
            if aid in self._items:
                self._bytes -= self._items[aid]["bytes"]
            self._items[aid] = {"rendered": rendered, "bytes": size}
            self._bytes += size
            while len(self._items) > self.max_entries or self._bytes > self.max_bytes:
                _, old = self._items.popitem(last=False)
                self._bytes -= old["bytes"]

    def get(self, aid: str):
        with self._lock:
            rec = self._items.get(aid)
            if rec is None:
                return None
            self._items.move_to_end(aid)
            return rec

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def nbytes(self) -> int:
        with self._lock:
            return self._bytes

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0


STORE = ResultStore()


# ------------------------------------------------------------------ 分析


def prepare_frames(epochs: list[Epoch], common: WCS, stack: np.ndarray) -> dict:
    frames = []
    finite = np.isfinite(stack)
    lo = float(np.nanpercentile(stack[finite], 42)) if finite.any() else 0.0
    hi = float(np.nanpercentile(stack[finite], 99.85)) if finite.any() else 1.0
    for img in pipeline.reproject_to_common(epochs, common):
        rgb = stretch_to_rgb(img, CMAP_SPACE, lo, hi)
        frames.append({"main": png_bytes(rgb), "diff": png_bytes(diff_rgb(img, stack, lo, hi))})
    return {"frames": frames, "stack": png_bytes(stretch_to_rgb(stack, CMAP_SPACE, lo, hi))}


def diff_rgb(img: np.ndarray, stack: np.ndarray, lo: float, hi: float) -> np.ndarray:
    d = np.asarray(img, float) - np.asarray(stack, float)
    finite = np.isfinite(d)
    if not finite.any():
        return np.zeros((*d.shape, 3), np.uint8)
    s = float(np.nanmedian(np.abs(d[finite]))) * 1.4826
    s = max(s, 1e-4)
    x = np.nan_to_num(np.clip(d / (6 * s), -1, 1), nan=0.0)
    rgb = CMAP_DIFF[((x + 1) / 2 * 255).astype(np.uint8)]
    rgb[~finite] = (5, 7, 13)
    return rgb


def _empty_result(class_: str = "U", label: str = "不可判定", readiness: str = "L0") -> dict:
    fp = {
        "mu_mas_yr": 0.0,
        "mu_err_mas_yr": None,
        "pa_deg": None,
        "displacement_arcsec": 0.0,
        "displacement_err_arcsec": 0.0,
        "pa_disp_deg": None,
        "linearity": None,
        "accel_arcsec_yr2": 0.0,
        "accel_sig": 0.0,
        "delta_bic_accel": 0.0,
        "amp_mag": 0.0,
        "period_d": None,
        "var_chi2_red": None,
        "delta_bic_var": 0.0,
        "n_epochs": 0,
        "baseline_days": 0.0,
        "scale_arcsec_px": 6.15,
        "mu_px_yr": 0.0,
        "mov_signif": 0.0,
        "mu_signif": 0.0,
    }
    return {
        "class": class_,
        "confidence": 0.1,
        "readiness": readiness,
        "fingerprint": fp,
        "models": [],
        "best_model": None,
        "mu_mas_yr": 0.0,
        "mu_err_mas_yr": None,
        "pa_deg": None,
        "moving": False,
        "variable": False,
        "detected_epochs": 0,
        "baseline_days": 0.0,
        "n_boot": 0,
        "multi_band": False,
        "bands": [],
        "quality": [],
        "match_stats": [],
        "n_detected_in_catalog": [],
        "target_xy_common": [],
        "track": {"t": [], "xi": [], "eta": [], "sx": [], "sy": [], "flux": [], "flux_err": [], "band": []},
        "measurements": [],
        "label_zh": label,
        "evidence": [],
        "falsifier": "",
        "public_text": "",
        "caveats": [],
    }


THEORY_BLOCK = {
    "models": [
        {"id": "M0", "label": "静止 + 恒亮", "k": 3},
        {"id": "M2", "label": "匀速自行 + 恒亮", "k": 5},
        {"id": "M4", "label": "自行 + 加速", "k": 7},
        {"id": "M1", "label": "静止 + 周期变光", "k": 6},
        {"id": "M3", "label": "自行 + 视差", "k": 6},
    ],
    "readiness": {
        "L0": "1 期：只能判“有没有源”，不能判运动",
        "L1": "2 期：只能给一次位移，不能定规律",
        "L2": "3–5 期：可测速率与方向",
        "L3": "≥6 期且跨 6 个月：可判加速/视差/周期",
    },
}


def sources_meta(epochs: list[Epoch], sources: list[dict], note: str | None = None) -> dict:
    """源表的说明块：计数、每期数量与质量门槛（不改动源数据本身）。"""
    by_frame: dict[int, int] = {}
    for s in sources or []:
        frame = int(s.get("frame", 0) or 0)
        by_frame[frame] = by_frame.get(frame, 0) + 1
    meta = {
        "n": len(sources or []),
        "n_selectable": sum(1 for s in (sources or []) if not (s.get("quality") or [])),
        "n_by_frame": [by_frame.get(i, 0) for i in range(len(epochs or []))],
        "common_frame_epoch_id": str(epochs[0].id) if epochs else None,
        "quality_rule": {
            "snr_min": pipeline.SOURCE_SNR_MIN,
            "edge_margin_px": pipeline.SOURCE_EDGE_MARGIN_PX,
            "duplicate_radius_px": pipeline.SOURCE_DUPLICATE_RADIUS_PX,
        },
    }
    if note:
        meta["note"] = note
    return meta


def run_sample_analysis(target: dict, fov: float) -> dict:
    """离线样本模式：两份切片来自不同天区，只做解析/渲染核验，不测目标。"""
    epochs = data_layer.load_sample_epochs()
    if not epochs:
        raise ApiError(503, "SAMPLE_UNAVAILABLE", "离线样本文件缺失（参考 资料/muestras）。")
    frames = []
    for ep in epochs:
        rgb = stretch_to_rgb(ep.image, CMAP_SPACE)
        black = np.zeros((*ep.image.shape, 3), np.uint8)
        frames.append({"main": png_bytes(rgb, scale=16), "diff": png_bytes(black, scale=16)})
    first = epochs[0]
    stack = png_bytes(stretch_to_rgb(first.image, CMAP_SPACE), scale=16)
    rendered = {"frames": frames, "stack": stack}

    aid = uuid.uuid4().hex[:12]
    STORE.put(aid, rendered)

    frames_json = [
        {
            **ep.json(),
            "main_url": f"/api/frame/{aid}/{i}/main.png",
            "diff_url": f"/api/frame/{aid}/{i}/diff.png",
        }
        for i, ep in enumerate(epochs)
    ]
    result = _empty_result("U", "样本核验（不做目标测量）", "L0")
    result["readiness_scope"] = "analysis"
    result["quality"] = ["SAMPLE_MODE_NO_TARGET_MEASUREMENT"]
    result["evidence"] = [
        "两份离线样本来自 M101 与场 A，是两个不同天区",
        "样本只用于验证 FITS 解析、HDU 裁剪与渲染链路",
        "样本模式不对任何目标做质心/位移/变光测量",
    ]
    result["falsifier"] = "这不是一次目标分析：要判断运动规律，请改用 auto/real 模式并指定一个天区。"
    result["public_text"] = (
        "当前是“离线样本”模式：两张图分别来自 M101 和场 A，不是同一天区，因此不能用来判断任何天体是否移动。"
        "这个模式只验证文件能不能读、图能不能画。"
    )
    result["caveats"] = ["样本模式不产生运动结论。"]

    return {
        "schema_version": "1.0",
        "analysis_id": aid,
        "target": {k: target.get(k) for k in ("id", "name", "name_zh", "ra", "dec", "type", "expected", "note")},
        "data": {
            "mode": "sample",
            "note": "两份离线真实切片（M101 / 场 A，不同天区），仅用于解析与渲染核验，不做目标测量。",
            "readiness": "L0",
            "n_epochs": len(epochs),
            "real_epochs": len(epochs),
            "bands": sorted({ep.band for ep in epochs}),
            "provenance": ["real"],
            "fallback_reason": "SAMPLE_MODE",
        },
        "truth": None,
        "epochs": frames_json,
        "stack_url": f"/api/frame/{aid}/stack.png",
        "result": result,
        "sources": [],
        "sources_meta": sources_meta(epochs, [], "样本模式只做解析/渲染核验，不做源探测（不伪造源）。"),
        "identification": None,
        "common_wcs": {
            "crval1": first.wcs.crval1,
            "crval2": first.wcs.crval2,
            "scale_arcsec": first.wcs.pixel_scale_arcsec(),
            "naxis": first.wcs.naxis1,
        },
        "theory": THEORY_BLOCK,
    }


def unavailable_result(meta: dict) -> dict:
    """取数失败时的显式 UNAVAILABLE 结果：不判定，不把 SIM 标成 real。"""
    attempted = int(meta.get("fetch_attempted") or 0)
    succeeded = int(meta.get("fetch_succeeded") or 0)
    fingerprint = {
        "mu_mas_yr": 0.0,
        "mu_err_mas_yr": None,
        "pa_deg": None,
        "displacement_arcsec": 0.0,
        "displacement_err_arcsec": None,
        "pa_disp_deg": None,
        "linearity": None,
        "accel_arcsec_yr2": 0.0,
        "accel_sig": 0.0,
        "delta_bic_accel": 0.0,
        "amp_mag": 0.0,
        "period_d": None,
        "var_chi2_red": None,
        "delta_bic_var": 0.0,
        "n_epochs": 0,
        "baseline_days": 0.0,
        "scale_arcsec_px": 6.15,
        "mu_px_yr": 0.0,
        "mov_signif": 0.0,
        "mu_signif": 0.0,
    }
    note = meta.get("note") or "真实历元存在但切片取数失败；未做判定。"
    return {
        "class": "U",
        "confidence": 0.0,
        "readiness": "L0",
        "fingerprint": fingerprint,
        "models": [],
        "best_model": None,
        "mu_mas_yr": 0.0,
        "mu_err_mas_yr": None,
        "pa_deg": None,
        "moving": False,
        "variable": False,
        "detected_epochs": 0,
        "baseline_days": 0.0,
        "n_boot": 0,
        "multi_band": False,
        "bands": [],
        "quality": ["FETCH_FAILED"],
        "evidence": [
            f"真实历元取数失败：成功 {succeeded}/{attempted} 期",
            note,
        ],
        "falsifier": "若网络/IRSA 恢复并能取回切片，则应重新分析；当前结论不用于判定运动。",
        "public_text": "这次没有取回真实图像，所以不能判断天体是否移动。请联系网络或稍后重试。",
        "label_zh": "不可判定（取数失败）",
        "measurements": [],
        "track": {"t": [], "xi": [], "eta": [], "sx": [], "sy": [], "flux": [], "flux_err": []},
    }


def compose_note(meta: dict, result: dict, data_block: dict) -> str:
    """按“取数期数 / 探测期数 / 探测基线 / 目录基线”分别写清楚。

    旧文案把目录基线（如 Barnard 204 天）直接写成“实取 4/4 期，基线 204 天”，
    与管线实际用于拟合的 3 期 / 6.451 天冲突。real/hybrid 的 note 统一在这里
    由探测结果生成，目录基线只作为带限定词的补充。
    """
    mode = data_block.get("mode") or meta.get("mode") or "synthetic"
    base = (meta.get("note") or "").strip()
    if mode not in ("real", "hybrid"):
        return base
    detected = int(result.get("detected_epochs") or 0)
    probe = float(result.get("baseline_days") or 0.0)
    attempts = int(meta.get("fetch_attempted") or 0)
    succeeded = int(meta.get("fetch_succeeded") or 0)
    cat = meta.get("catalog_baseline_days")
    cat_txt = f"（目录基线 {float(cat):.0f} 天）" if cat else ""
    if mode == "real":
        return (
            f"IRSA 静态切片取数 {succeeded}/{attempts} 期；其中 {detected} 期用于拟合，"
            f"探测基线 {probe:.0f} 天{cat_txt}；参考星相对定标。"
        )
    n = int(data_block.get("n_epochs") or 0)
    head = f"取数 {succeeded}/{attempts} 期；" if attempts else ""
    note = (
        f"{head}验证时序 {n} 期，其中 {detected} 期用于拟合，"
        f"探测基线 {probe:.0f} 天{cat_txt}。"
    )
    return note + ((" " + base) if base else "")


def target_measurement(result: dict, target: dict) -> dict | None:
    """目标源在最新有效历元的实测位置（ICRS deg）与 MJD/误差。

    识别用的是**分析目标**的实测位置，不是目录里的 J2000 坐标：高自行星
    （Barnard 10.4″/yr）在 20+ 年后早已离开 J2000 位置。B 按该 MJD 把星表源
    PM 传播到观测历元，app 不重复做星表传播。
    """
    measurements = result.get("measurements") or []
    valid = []
    for m in measurements:
        if not isinstance(m, dict):
            continue
        try:
            mjd = float(m.get("mjd"))
            xi = float(m.get("xi_arcsec"))
            eta = float(m.get("eta_arcsec"))
            sigma = float(m.get("sigma_arcsec"))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (mjd, xi, eta, sigma)):
            continue
        valid.append(
            {
                "mjd": mjd,
                "xi": xi,
                "eta": eta,
                "sigma": max(abs(sigma), 0.01),
                "saturated": bool(m.get("saturated")),
            }
        )
    if not valid:
        return None
    latest = max(valid, key=lambda m: m["mjd"])
    try:
        ra, dec = gnomonic_inverse(
            latest["xi"] / ARCSEC,
            latest["eta"] / ARCSEC,
            float(target["ra"]),
            float(target["dec"]),
        )
        ra = float(np.atleast_1d(ra)[0])
        dec = float(np.atleast_1d(dec)[0])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return None
    return {
        "ra": ra,
        "dec": dec,
        "mjd": latest["mjd"],
        "sigma_arcsec": latest["sigma"],
        "n_epochs": len(valid),
        "saturated": latest["saturated"],
    }


def build_identification(result: dict, target: dict, *, detection_id: str = "target") -> dict:
    """目标源识别的安全入口：星表任何失败都不能让 /api/analyze 变成 500。"""
    measurement = target_measurement(result, target)
    if measurement is None:
        reason = "FETCH_FAILED" if "FETCH_FAILED" in (result.get("quality") or []) else "TARGET_NOT_DETECTED"
        return catalog_layer.insufficient(reason)

    try:
        catalog = CATALOG_CLIENT.match(
            ra=measurement["ra"],
            dec=measurement["dec"],
            mjd=measurement["mjd"],
            sigma_arcsec=measurement["sigma_arcsec"],
            detection_id=detection_id,
            analysis_id=None,
        )
    except Exception as exc:  # noqa: BLE001 - 客户端约定不抛异常，这里再兜一层
        catalog = {
            "ok": False,
            "catalog_status": "unavailable",
            "catalog_reason": "CATALOG_CLIENT_ERROR",
            "detail": str(exc),
        }
    try:
        return catalog_layer.identify(catalog, target=measurement, motion=result)
    except Exception as exc:  # noqa: BLE001 - 分类失败只降级为数据不足，不伪造 known
        return catalog_layer.insufficient("IDENTIFICATION_ERROR", catalog=catalog, detail=str(exc))


def run_identify(payload: dict) -> dict:
    """D-MVP 单源识别接口：检测位置 + MJD + 误差（+ 可选 PM）→ 星表结论。

    与 ``/api/analyze`` 内的 identification 共用同一个 ``core.catalog`` 分类器；
    不跑图像分析，也不做行星确认。星表失败时仍返回 200 + ``catalog_unavailable``，
    只有请求参数非法才 400。
    """
    if not isinstance(payload, dict):
        raise ApiError(400, "BAD_JSON", "请求体必须是 JSON 对象。")

    def num(name: str, *, default=None, required: bool = True) -> float:
        raw = payload.get(name, default)
        if raw is None:
            if required:
                raise ApiError(400, "MISSING_FIELD", f"缺少 {name}。")
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ApiError(400, f"INVALID_{name.upper()}", f"{name} 必须是数字。")
        if not math.isfinite(value):
            raise ApiError(400, f"INVALID_{name.upper()}", f"{name} 必须是有限数字。")
        return value

    ra = num("ra")
    dec = num("dec")
    if not (0.0 <= ra < 360.0):
        raise ApiError(400, "INVALID_RA", "ra 必须在 [0, 360) 度。")
    if not (-90.0 <= dec <= 90.0):
        raise ApiError(400, "INVALID_DEC", "dec 必须在 [-90, 90] 度。")
    mjd = num("mjd")
    sigma = num("sigma_arcsec", default=1.0)
    if not (0.0 < sigma <= 60.0):
        raise ApiError(400, "INVALID_SIGMA", "sigma_arcsec 必须在 (0, 60] 角秒。")

    pmra = num("pmra_mas_yr", required=False)
    pmdec = num("pmdec_mas_yr", required=False)
    if (pmra is None) != (pmdec is None):
        raise ApiError(400, "INVALID_PM", "pmra_mas_yr 与 pmdec_mas_yr 必须同时提供。")
    pm_err = num("pm_err_mas_yr", required=False)
    if pm_err is not None and (not math.isfinite(pm_err) or pm_err <= 0):
        raise ApiError(400, "INVALID_PM_ERR", "pm_err_mas_yr 必须 > 0。")
    if pm_err is not None and pmra is None:
        raise ApiError(400, "INVALID_PM_ERR", "pm_err_mas_yr 需要同时提供 pmra/pmdec。")

    raw_epochs = payload.get("n_epochs")
    if raw_epochs is None:
        n_epochs = 2 if pmra is not None else 1
    else:
        try:
            n_epochs = int(raw_epochs)
        except (TypeError, ValueError):
            raise ApiError(400, "INVALID_N_EPOCHS", "n_epochs 必须是整数。")
        if n_epochs < 1 or n_epochs > 1000:
            raise ApiError(400, "INVALID_N_EPOCHS", "n_epochs 必须在 [1, 1000]。")

    detection_id = str(payload.get("detection_id") or "target")[:200]
    analysis_id = str(payload.get("analysis_id") or "")[:200] or None
    target = {
        "ra": ra,
        "dec": dec,
        "mjd": mjd,
        "sigma_arcsec": sigma,
        "n_epochs": n_epochs,
    }
    # 即使没有 PM，也把历元数带进分类器：单期/多期 caveat 需要它。
    motion = {"moving": False, "n_epochs": n_epochs, "detected_epochs": n_epochs}
    if pmra is not None:
        mu = math.hypot(pmra, pmdec)
        pa = math.degrees(math.atan2(pmra, pmdec)) % 360.0
        motion.update(
            {
                "moving": True,
                "mu_mas_yr": mu,
                "mu_err_mas_yr": pm_err,
                "pa_deg": pa,
            }
        )

    try:
        catalog = CATALOG_CLIENT.match(
            ra=ra,
            dec=dec,
            mjd=mjd,
            sigma_arcsec=sigma,
            detection_id=detection_id,
            analysis_id=analysis_id,
            pmra_mas_yr=pmra,
            pmdec_mas_yr=pmdec,
        )
    except Exception as exc:  # noqa: BLE001 - 星表失败不能变成 500
        catalog = {
            "ok": False,
            "catalog_status": "unavailable",
            "catalog_reason": "CATALOG_CLIENT_ERROR",
            "detail": str(exc),
        }
    try:
        ident = catalog_layer.identify(catalog, target=target, motion=motion)
    except Exception as exc:  # noqa: BLE001 - 分类失败降级，不伪造 known
        ident = catalog_layer.insufficient("IDENTIFICATION_ERROR", catalog=catalog, detail=str(exc))

    ident["detection_id"] = detection_id
    ident["input"] = {
        "ra": ra,
        "dec": dec,
        "mjd": mjd,
        "sigma_arcsec": sigma,
        "pmra_mas_yr": pmra,
        "pmdec_mas_yr": pmdec,
        "pm_err_mas_yr": pm_err,
        "n_epochs": n_epochs,
    }
    if pmra is not None and pm_err is None:
        ident.setdefault("caveats", []).append(
            "提供了 PM 但未提供 PM 误差；未做 3σ 自行一致性判定，known 置信度降级。"
        )
    return ident


def run_analysis(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ApiError(400, "BAD_JSON", "请求体必须是 JSON 对象。")
    mode = payload.get("mode", "auto")
    if mode not in VALID_MODES:
        raise ApiError(400, "INVALID_MODE", f"mode 必须是 {sorted(VALID_MODES)} 之一。")
    fov = validate_fov(payload.get("fov", 0.2))
    tid, ra, dec, target_preset = resolve_request(payload)

    if mode == "sample":
        target = target_preset or {
            "id": "custom",
            "name": f"RA {ra:.4f}  Dec {dec:.4f}",
            "name_zh": "自定义天区",
            "ra": ra,
            "dec": dec,
            "type": "unknown",
            "expected": "U",
            "truth": {},
        }
        return run_sample_analysis(target, fov)

    if tid and tid != "custom":
        target = target_preset
        if payload.get("ra") is not None and payload.get("dec") is not None:
            target = dict(target, ra=float(ra), dec=float(dec))
        tgt, epochs, truth, meta = data_layer.build_dataset(tid, mode=mode, size_deg=fov)
        response_target = None
    else:
        # 与预设目标保持产品一致：自定义坐标 0.05° 内命中且有本地历元目录时，
        # 复用 build_dataset(target_id) 的 real/hybrid 规则（不再出现“少量真实+SIM”变 S）。
        preset = match_preset_target(ra, dec)
        if preset is not None and data_layer.load_epoch_catalog().get(preset["id"]):
            tgt, epochs, truth, meta = data_layer.build_dataset(preset["id"], mode=mode, size_deg=fov)
            meta = dict(meta)
            meta["matched_target_id"] = preset["id"]
            meta["matched_name"] = preset.get("name")
            meta["source"] = "local_json_matched"
            prefix = (
                f"自定义坐标在 {PRESET_MATCH_TOL_DEG}° 内匹配到 {preset.get('name')}（{preset['id']}）；"
                "复用该目标的本地历元与 HYBRID/SIM 回退规则（与预设一致）。"
            )
            meta["note"] = prefix + ((" " + meta["note"]) if meta.get("note") else "")
            response_target = matched_custom_target(ra, dec, preset)
        else:
            # T2：任意坐标 → DB 真实历元 / 显式降级（不再无条件生成模拟时序）
            tgt, epochs, truth, meta = data_layer.build_dataset(
                "custom",
                mode=mode,
                size_deg=fov,
                ra=ra,
                dec=dec,
            )
            response_target = None

    if not epochs:
        raise ApiError(503, "NO_EPOCHS", "没有可用历元。")

    for ep in epochs:
        if ep.psf is None:
            p = os.path.join(HERE, "data", "psf_d2.npy")
            ep.psf = np.load(p) if os.path.exists(p) else None

    mode_out = meta.get("mode", "synthetic")
    if mode_out == "unavailable":
        # 真实历元存在但取数失败：渲染 SIM 帧，但明确 UNAVAILABLE，不跑判定
        result = unavailable_result(meta)
    else:
        result = pipeline.run_pipeline(tgt, epochs, truth=truth, mode=mode_out)
    result["readiness_scope"] = "analysis"
    # 阶段 D MVP：目标源级星表识别。B 不可用时只降级 identification，
    # 不阻塞原有的运动分析（响应仍 200）。
    identification = build_identification(result, tgt, detection_id=str(tgt.get("id") or "target"))
    # 阶段 A：源表放在顶层，与 epochs 的帧一一对应；result 保持运动指纹的原有结构。
    sources = result.pop("sources", []) or []
    if mode_out == "unavailable":
        source_note = "取数失败模式未做源探测（sources 为空，不伪造源）。"
    elif not sources:
        source_note = "本次分析没有可用的检测源表。"
    else:
        source_note = "x/y 为 common WCS 第 1 期像素；quality 非空的源不可点选。"
    common = epochs[0].wcs
    imgs = pipeline.reproject_to_common(epochs, common)
    cube = np.stack(imgs, axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        stack = np.nanmedian(np.where(np.isfinite(cube), cube, np.nan), axis=0)
        fill = float(np.nanmedian(stack)) if np.isfinite(stack).any() else 0.0
    stack = np.where(np.isfinite(stack), stack, fill)
    rendered = prepare_frames(epochs, common, stack)

    aid = uuid.uuid4().hex[:12]
    STORE.put(aid, rendered)

    frames_json = []
    for i, ep in enumerate(epochs):
        frames_json.append(
            {
                **ep.json(),
                "main_url": f"/api/frame/{aid}/{i}/main.png",
                "diff_url": f"/api/frame/{aid}/{i}/diff.png",
            }
        )

    real_epochs = sum(1 for ep in epochs if ep.provenance == "real")
    fallback = meta.get("fallback_reason")

    data_block = {
        "mode": mode_out,
        "note": "",
        "readiness": result.get("readiness"),
        "n_epochs": len(epochs),
        "real_epochs": real_epochs,
        "bands": sorted({ep.band for ep in epochs}),
        "provenance": sorted({ep.provenance for ep in epochs}),
        "fallback_reason": fallback,
        "source": meta.get("source"),
        "matched_target_id": meta.get("matched_target_id"),
        "matched_name": meta.get("matched_name"),
        "fetch_attempted": meta.get("fetch_attempted", 0),
        "fetch_succeeded": meta.get("fetch_succeeded", 0),
        "detected_epochs": int(result.get("detected_epochs") or 0),
        "detection_baseline_days": result.get("baseline_days"),
        "catalog_baseline_days": meta.get("catalog_baseline_days"),
    }
    data_block["note"] = compose_note(meta, result, data_block)
    if meta.get("coverage") is not None:
        data_block["coverage"] = meta["coverage"]
    if meta.get("catalog_epochs") is not None:
        data_block["catalog_epochs"] = meta["catalog_epochs"]
    if meta.get("warnings"):
        data_block["warnings"] = list(meta["warnings"])

    payload = {
        "schema_version": "1.0",
        "analysis_id": aid,
        "target": {k: (response_target or tgt).get(k) for k in ("id", "name", "name_zh", "ra", "dec", "type", "expected", "note")},
        "data": data_block,
        "truth": truth,
        "epochs": frames_json,
        "stack_url": f"/api/frame/{aid}/stack.png",
        "result": result,
        "sources": sources,
        "sources_meta": sources_meta(epochs, sources, source_note),
        "identification": identification,
        "common_wcs": {
            "crval1": common.crval1,
            "crval2": common.crval2,
            "scale_arcsec": common.pixel_scale_arcsec(),
            "naxis": common.naxis1,
        },
        "theory": THEORY_BLOCK,
    }
    return payload


# ------------------------------------------------------------------ HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "MoveScope/0.2"

    def log_message(self, fmt, *args):  # 安静一点
        if "404" in fmt % args or "500" in fmt % args or "400" in fmt % args:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        body = json.dumps(_sanitize(obj), ensure_ascii=False, default=float).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _api_error(self, exc: ApiError):
        return self._json({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/") :])
            if path == "/api/health":
                return self._json(
                    {
                        "ok": True,
                        "service": "MoveScope",
                        "version": "0.2.0",
                        "theory": "docs/01_理论设计编排.md",
                        "results_cached": len(STORE),
                        "results_bytes": STORE.nbytes,
                    }
                )
            if path == "/api/targets":
                return self._json({"targets": data_layer.load_targets()})
            if path == "/api/epochs":
                q = parse_qs(u.query)
                tid = (q.get("target_id") or [""])[0]
                ra_q = (q.get("ra") or [None])[0]
                dec_q = (q.get("dec") or [None])[0]
                if tid and tid != "custom":
                    # 预设目标保持原有本地 catalog 行为（DB 停机也能查）
                    cat = data_layer.load_epoch_catalog().get(tid, [])
                    return self._json({"target_id": tid, "epochs": cat})
                if ra_q is None or dec_q is None:
                    raise ApiError(400, "MISSING_COORDINATE", "需要同时提供 ra 和 dec（度）。")
                try:
                    query_ra = float(ra_q)
                    query_dec = float(dec_q)
                    query_fov = float((q.get("fov") or ["0.25"])[0])
                except (TypeError, ValueError):
                    raise ApiError(400, "INVALID_QUERY", "ra/dec/fov 必须是数字。")
                try:
                    resolution = epoch_source.resolve_epochs(query_ra, query_dec, query_fov)
                except ValueError as exc:
                    raise ApiError(400, "INVALID_QUERY", str(exc))
                return self._json(
                    {
                        "schema_version": "1.0",
                        "query": resolution.query,
                        "target_id": tid or None,
                        "matched_target_id": resolution.matched_target_id,
                        "coverage": resolution.coverage,
                        "epochs": resolution.epochs,
                        "count": len(resolution.epochs),
                        "fallback_reason": resolution.fallback_reason,
                        "source": resolution.source,
                        "warnings": resolution.warnings,
                    }
                )
            if path.startswith("/api/frame/"):
                parts = path.strip("/").split("/")
                aid = parts[2]
                rec = STORE.get(aid)
                if not rec:
                    return self._send(404, b"no result", "text/plain")
                r = rec["rendered"]
                if parts[-1] == "stack.png":
                    return self._send(200, r["stack"], "image/png")
                idx = parts[3]
                kind = parts[4].replace(".png", "") if len(parts) > 4 else "main"
                if kind == "stack":
                    data = r["stack"]
                elif idx.isdigit() and int(idx) < len(r["frames"]):
                    data = r["frames"][int(idx)][kind if kind in ("main", "diff") else "main"]
                else:
                    return self._send(404, b"bad frame", "text/plain")
                return self._send(200, data, "image/png")
            if path == "/api/docs":
                doc = os.path.join(ROOT, "docs", "01_理论设计编排.md")
                if os.path.exists(doc):
                    return self._send(200, open(doc, "rb").read(), "text/markdown; charset=utf-8")
                return self._send(404, b"no docs", "text/plain")
            return self._send(404, b"not found", "text/plain")
        except ApiError as exc:  # noqa: BLE001
            return self._api_error(exc)
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            return self._json({"error": {"code": "INTERNAL", "message": str(exc)}}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/identify":
            try:
                n = int(self.headers.get("Content-Length", 0))
                try:
                    payload = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    raise ApiError(400, "BAD_JSON", "请求体不是合法 JSON。")
                out = run_identify(payload)
                return self._json(out)
            except ApiError as exc:  # noqa: BLE001
                return self._api_error(exc)
            except Exception as exc:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                return self._json({"error": {"code": "INTERNAL", "message": str(exc)}}, 500)
        if u.path != "/api/analyze":
            return self._send(404, b"not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(400, "BAD_JSON", "请求体不是合法 JSON。")
            out = run_analysis(payload)
            return self._json(out)
        except ApiError as exc:  # noqa: BLE001
            return self._api_error(exc)
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            return self._json({"error": {"code": "INTERNAL", "message": str(exc)}}, 500)

    def _static(self, rel: str):
        path = os.path.normpath(os.path.join(WEB, rel))
        if not path.startswith(WEB) or not os.path.isfile(path):
            return self._send(404, b"not found", "text/plain")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".json": "application/json",
        }.get(os.path.splitext(path)[1], "application/octet-stream")
        return self._send(200, open(path, "rb").read(), ctype)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MoveScope → http://{args.host}:{args.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
