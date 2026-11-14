"""Epoch Resolver：把 (ra, dec, fov) 解析成数据库里的真实历元。

这是 R1 的 app 侧适配层，不是新服务：

  用户坐标 ──► 本地 0.05° 目标匹配 ──► DB /api/v1/epochs（唯一传输契约）
                                  └──► app/data/epoch_catalog.json（仅显式 target_id 且 DB 不可用）

设计约束（见 04_T2_数据桥接.md、02_架构设计与集成契约.md §5–§7）：

- 只读；不写数据库，不缓存跨请求结果。
- 只走契约 v1 ``GET /api/v1/epochs``。旧 ``/api/epochs`` 已 deprecated，**不得**拿来冒充 v1：
  v1 返回 404 说明数据库尚未提供契约端点，本模块显式降级为 ``DB_UNAVAILABLE``。
- v1 连接失败 / 超时 / 非 200 / 坏 JSON / 响应形状不对，一律降级为 ``DB_UNAVAILABLE``，
  不抛到 HTTP 500。
- 缺 ``key`` / ``obsid`` / ``mjd`` / ``detector`` / ``sep_deg`` 的条目丢弃并记 warning；
  ``date_obs`` 为空或格式非法时由 ``mjd`` 反推 UTC。
- 去重 ``(obsid, detector)``；同一 MJD（±0.02 d）保留 ``sep_deg`` 最小者；排序 ``(mjd, detector)``。
- 不把模拟数据标成 real：``provenance`` 默认 ``real`` 只用于数据库返回的真实目录条目。
- 显式 ``target_id`` 且 DB 不可用时，才回退本地 ``epoch_catalog.json``（``source=local_json``）；
  自定义 ra/dec 坐标绝不回退本地目录，保证“无真实历元 → 上游显式 SIM”。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DEFAULT_DB_URL = "http://127.0.0.1:8797"
DB_URL_ENV = "SPHEREX_DB_URL"
TARGET_MATCH_TOL_DEG = 0.05  # 官方契约；WISE 0855 的 1.6′ 歧义必须覆盖
FOV_MIN_DEG = 0.02
FOV_MAX_DEG = 3.5
DETECTOR_MIN = 1
DETECTOR_MAX = 6
_MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)
_SLICE_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
_PROVENANCE = ("real", "hybrid", "synthetic", "unknown")
_WEEK_RE = re.compile(r"^(?P<week>\d{4}W\d+_[0-9A-Z]+)")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$")
EPOCH_REF_FIELDS = (
    "obsid",
    "detector",
    "release",
    "week",
    "version",
    "mjd",
    "date_obs",
    "sep_deg",
    "key",
    "slice_url",
)


@dataclass
class EpochResolution:
    """``resolve_epochs`` 的返回值。字段语义见 04_T2_数据桥接.md §T1。"""

    epochs: list[dict] = field(default_factory=list)
    coverage: dict | None = None
    matched_target_id: str | None = None
    fallback_reason: str | None = None
    source: str = "none"  # db | local_json | none
    query: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    transport: str | None = None  # v1 | local_json | None

    @property
    def ok(self) -> bool:
        return bool(self.epochs)


# --------------------------------------------------------------------------- 基础工具


def db_base_url(base_url: str | None = None) -> str:
    """解析数据库地址：参数 > ``SPHEREX_DB_URL`` > 本地 8797。"""
    value = base_url or os.environ.get(DB_URL_ENV) or DEFAULT_DB_URL
    return str(value).rstrip("/")


def _data_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def load_known_targets() -> list[dict]:
    """读 ``app/data/targets.json``；缺失/损坏时返回空列表（不阻断 R1）。"""
    path = os.path.join(_data_dir(), "targets.json")
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    targets = payload.get("targets", [])
    if not isinstance(targets, list):
        return []
    return [t for t in targets if isinstance(t, dict) and t.get("id")]


def angular_sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两点球面角距（度）。"""
    r1, d1, r2, d2 = map(math.radians, (float(ra1), float(dec1), float(ra2), float(dec2)))
    # haversine：小角度下数值稳定，且对 RA 环绕（0/360）安全
    sd = math.sin((d2 - d1) / 2.0)
    sr = math.sin((r2 - r1) / 2.0)
    a = sd * sd + math.cos(d1) * math.cos(d2) * sr * sr
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(max(a, 0.0)))))


def match_known_target(ra: float, dec: float, tol_deg: float = TARGET_MATCH_TOL_DEG) -> dict | None:
    """在 ``tol_deg``（默认 0.05°）内匹配官方目标；返回目标 dict，否则 None。

    命中只用于借用 truth / coord_unc_arcsec；用户输入的坐标仍是查询中心。
    """
    try:
        ra = float(ra)
        dec = float(dec)
    except (TypeError, ValueError):
        return None
    tol = _as_float(tol_deg)
    if tol is None or tol < 0:
        return None
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return None
    best: tuple[float, dict] | None = None
    for target in load_known_targets():
        try:
            sep = angular_sep_deg(ra, dec, target["ra"], target["dec"])
        except (KeyError, TypeError, ValueError):
            continue
        if sep <= tol and (best is None or sep < best[0]):
            best = (sep, target)
    return best[1] if best else None


def get_known_target(target_id: str | None) -> dict | None:
    if not target_id:
        return None
    for target in load_known_targets():
        if target.get("id") == target_id:
            return target
    return None


def local_catalog_epochs(target_id: str | None) -> list[dict]:
    """``app/data/epoch_catalog.json`` 里某目标的原始历元（仅作 DB 停机时的显式回退）。"""
    if not target_id:
        return []
    path = os.path.join(_data_dir(), "epoch_catalog.json")
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    epochs = payload.get("epochs", {})
    if not isinstance(epochs, dict):
        return []
    rows = epochs.get(target_id, [])
    if not isinstance(rows, list):
        return []
    return [e for e in rows if isinstance(e, dict)]


def mjd_to_iso(mjd: float) -> str:
    """MJD → UTC ISO（毫秒精度，不带时区后缀，与 EpochRef 示例一致）。"""
    moment = _MJD_EPOCH + timedelta(days=float(mjd))
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _iso_from_mjd(mjd: float) -> str:
    return mjd_to_iso(mjd)


def _slice_url(key: str, release: str, ra: float, dec: float, fov: float) -> str:
    relpath = key[len(release) + 1 :] if release and key.startswith(release + "/") else key
    prefix = f"{release}/" if release else ""
    return f"{_SLICE_BASE}{prefix}{relpath}?center={ra:.6f},{dec:.6f}d&size={fov:g}"


# --------------------------------------------------------------------------- DTO 规范化


def _first(raw: dict, *names: str):
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return None


def _as_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"[+-]?\d+", value):
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_date_obs(value, mjd: float, obsid: str) -> tuple[str, list[str]]:
    """把 ``date_obs`` 规范化成 UTC ISO；空/非法时由 ``mjd`` 反推。"""
    if value is not None:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1].strip()
        if text and _ISO_RE.match(text):
            return text, []
        if text:
            return _iso_from_mjd(mjd), [f"date_obs 格式非法，已由 mjd={mjd:.6f} 回填 UTC（obsid={obsid}）"]
    return _iso_from_mjd(mjd), [f"date_obs 为空，已由 mjd={mjd:.6f} 回填 UTC（obsid={obsid}）"]


def _normalize_epoch(raw: dict, ra: float, dec: float, fov: float) -> tuple[dict | None, list[str]]:
    """把 v1 / 本地 JSON 的条目统一成 EpochRef；无效条目返回 (None, warnings)。"""
    warns: list[str] = []
    if not isinstance(raw, dict):
        return None, ["丢弃非对象历元条目"]

    obsid = _first(raw, "obsid", "obs_id", "OBSID")
    key = _first(raw, "key", "fits_key", "KEY")
    obsid = str(obsid).strip() if obsid is not None else ""
    key = str(key).strip() if key is not None else ""
    if not obsid:
        return None, ["丢弃缺 obsid 的历元条目"]
    if not key:
        return None, [f"丢弃缺 key 的历元条目（obsid={obsid}）"]

    mjd = _as_float(_first(raw, "mjd", "MJD-OBS", "MJD_OBS"))
    if mjd is None:
        return None, [f"丢弃缺 mjd 的历元条目（obsid={obsid}）"]

    detector = _as_int(_first(raw, "detector", "DETECTOR"))
    if detector is None or not (DETECTOR_MIN <= detector <= DETECTOR_MAX):
        return None, [f"丢弃 detector 非法的历元条目（obsid={obsid}）"]

    date_obs, date_warns = _normalize_date_obs(_first(raw, "date_obs", "DATE-OBS", "DATE_OBS"), mjd, obsid)
    warns.extend(date_warns)

    release = _first(raw, "release", "RELEASE")
    release = str(release).strip() if release is not None else ""
    if not release and "/" in key:
        candidate = key.split("/", 1)[0]
        if candidate in ("dr1", "qr3"):
            release = candidate
            warns.append(f"缺 release，已由 key 推断 {release}（obsid={obsid}）")
    if not release:
        warns.append(f"缺 release（obsid={obsid}）")

    week = _first(raw, "week", "WEEK")
    week = str(week).strip() if week is not None else ""
    if not week:
        match = _WEEK_RE.match(obsid)
        if match:
            week = match.group("week")
            warns.append(f"缺 week，已由 obsid 推断 {week}（obsid={obsid}）")
        else:
            warns.append(f"缺 week（obsid={obsid}）")

    version = _first(raw, "version", "VERSION")
    version = str(version).strip() if version is not None else ""
    if not version:
        parts = key.split("/")
        # dr1/level2/<week>/<version>/<detector>/<file>
        if len(parts) >= 5 and parts[1] == "level2":
            version = parts[3]
        else:
            match = re.search(r"_spx_(?P<version>[^/_]+)\.fits$", key)
            version = match.group("version") if match else ""
        if version:
            warns.append(f"缺 version，已由 key 推断 {version}（obsid={obsid}）")
        else:
            warns.append(f"缺 version（obsid={obsid}）")

    sep = _as_float(_first(raw, "sep_deg", "SEP_DEG"))
    if sep is None or sep < 0:
        frame_ra = _as_float(_first(raw, "frame_ra", "ra", "RA"))
        frame_dec = _as_float(_first(raw, "frame_dec", "dec", "DEC"))
        if frame_ra is not None and frame_dec is not None:
            sep = angular_sep_deg(ra, dec, frame_ra, frame_dec)
            warns.append(f"缺 sep_deg，已由坐标计算 {sep:.6f}°（obsid={obsid}）")
        else:
            # EpochRef 的十个必填字段之一不可恢复：丢弃，避免把不完整 DTO 交给管线。
            return None, [f"丢弃缺 sep_deg 的历元条目（obsid={obsid}）"]

    provenance = _first(raw, "provenance", "PROVENANCE")
    provenance = str(provenance or "real").strip().lower()
    if provenance not in _PROVENANCE:
        warns.append(f"未知 provenance={provenance!r}，按 unknown 处理（obsid={obsid}）")
        provenance = "unknown"

    epoch = {
        "obsid": obsid,
        "detector": detector,
        "release": release,
        "week": week,
        "version": version,
        "mjd": mjd,
        "date_obs": date_obs,
        "sep_deg": sep,
        "key": key,
        "slice_url": _slice_url(key, release, ra, dec, fov),
        "provenance": provenance,
        "frame_id": _as_int(_first(raw, "frame_id", "FRAME_ID")),
    }
    return epoch, warns


def _dedupe_and_sort(epochs: list[dict], tol_days: float = 0.02) -> list[dict]:
    """去重 ``(obsid, detector)`` + 同 MJD 保留 sep 最小者，最后按 (mjd, detector) 稳定排序。"""
    by_key: dict[tuple[str, int], dict] = {}
    for epoch in sorted(epochs, key=lambda e: (e["mjd"], e["sep_deg"], e["detector"])):
        k = (str(epoch["obsid"]), int(epoch["detector"]))
        if k not in by_key:
            by_key[k] = epoch

    ordered = sorted(by_key.values(), key=lambda e: (e["mjd"], e["detector"]))
    out: list[dict] = []
    for epoch in ordered:
        if out and abs(epoch["mjd"] - out[-1]["mjd"]) <= tol_days:
            if epoch["sep_deg"] < out[-1]["sep_deg"]:
                out[-1] = epoch
            continue
        out.append(epoch)
    return sorted(out, key=lambda e: (e["mjd"], e["detector"]))


def _normalize_many(raw_epochs, ra: float, dec: float, fov: float) -> tuple[list[dict], list[str]]:
    epochs: list[dict] = []
    warnings: list[str] = []
    for raw in raw_epochs or []:
        epoch, warns = _normalize_epoch(raw, ra, dec, fov)
        warnings.extend(warns)
        if epoch is not None:
            epochs.append(epoch)
    return _dedupe_and_sort(epochs), warnings


def coverage_for_epochs(epochs: list[dict]) -> dict:
    """由 EpochRef 列表计算 Coverage（C0–C3 只描述观测机会，不是检测质量）。"""
    if not epochs:
        return {
            "n_epochs": 0,
            "n_observations": 0,
            "baseline_days": 0.0,
            "coverage_level": "C0",
            "n_weeks": 0,
            "detectors": [],
            "releases": [],
        }
    mjds = [float(e["mjd"]) for e in epochs]
    n = len(epochs)
    baseline = max(mjds) - min(mjds) if n >= 2 else 0.0
    if n >= 6 and baseline >= 180.0:
        level = "C3"
    elif n >= 3:
        level = "C2"
    elif n == 2:
        level = "C1"
    else:
        level = "C0"
    return {
        "n_epochs": n,
        "n_observations": n,
        "baseline_days": round(baseline, 2),
        "mjd_min": round(min(mjds), 6),
        "mjd_max": round(max(mjds), 6),
        "n_weeks": len({e.get("week") for e in epochs if e.get("week")}),
        "detectors": sorted({int(e["detector"]) for e in epochs}),
        "releases": sorted({e.get("release") for e in epochs if e.get("release")}),
        "coverage_level": level,
    }


# --------------------------------------------------------------------------- HTTP


class _PayloadError(ValueError):
    """v1 响应形状不符合契约（200 但内容不可用）。"""


def _http_json(url: str, timeout: float) -> tuple[str, dict | list | None, str | None]:
    """GET JSON，1 次重试；返回 (status, payload, error)。

    status: ``ok`` | ``missing``（404，端点不存在）| ``error``（连接/超时/非 200/坏 JSON）。
    404 不重试：它表示契约 v1 尚未部署，重试没有意义。
    """
    last_error: str | None = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "MoveScope-epoch-source/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
            try:
                return "ok", json.loads(body), None
            except (ValueError, UnicodeDecodeError) as exc:
                last_error = f"JSON 解析失败: {exc}"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "missing", None, None
            last_error = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = str(exc) or exc.__class__.__name__
        if attempt == 0:
            time.sleep(0.05)  # 只做一次短重试；不要把 HTTP 线程拖死
    return "error", None, last_error


def _v1_url(base: str, ra: float, dec: float, fov: float, target_id: str | None) -> str:
    if target_id:
        params = {"target_id": str(target_id)}
    else:
        params = {"ra": f"{ra:.6f}", "dec": f"{dec:.6f}", "fov": f"{fov:g}"}
    return f"{base}/api/v1/epochs?{urllib.parse.urlencode(params)}"


def _parse_payload(payload, ra: float, dec: float, fov: float) -> dict:
    """解析并规范化 v1 响应；形状不对时抛 ``_PayloadError``（由调用方降级）。"""
    if not isinstance(payload, dict):
        raise _PayloadError("响应不是 JSON 对象")
    raw_epochs = payload.get("epochs")
    if not isinstance(raw_epochs, list):
        raise _PayloadError("响应缺少 epochs 列表")
    epochs, warnings = _normalize_many(raw_epochs, ra, dec, fov)
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or not coverage:
        coverage = coverage_for_epochs(epochs)
    matched = payload.get("matched_target_id")
    matched = str(matched).strip() if matched else None
    return {"epochs": epochs, "coverage": coverage, "matched": matched, "warnings": warnings}


# --------------------------------------------------------------------------- 主入口


def resolve_epochs(
    ra: float,
    dec: float,
    fov: float,
    target_id: str | None = None,
    *,
    base_url: str | None = None,
    timeout: float = 2.0,
) -> EpochResolution:
    """解析 (ra, dec, fov) 对应的真实历元。

    失败时返回 ``EpochResolution``，绝不抛网络异常；只有参数非法才抛 ``ValueError``
    （由 HTTP 层转 400）。不缓存跨请求结果——数据库是事实源。
    """
    try:
        ra = float(ra)
        dec = float(dec)
        fov = float(fov)
    except (TypeError, ValueError) as exc:
        raise ValueError("ra/dec/fov 必须是数字") from exc
    if not (math.isfinite(ra) and math.isfinite(dec) and math.isfinite(fov)):
        raise ValueError("ra/dec/fov 必须是有限数字")
    if not (-90.0 <= dec <= 90.0):
        raise ValueError("dec 必须在 [-90, 90] 度")
    if not (FOV_MIN_DEG <= fov <= FOV_MAX_DEG):
        raise ValueError(f"fov 必须在 [{FOV_MIN_DEG}, {FOV_MAX_DEG}] 度")

    timeout_value = _as_float(timeout)
    if timeout_value is None or timeout_value <= 0:
        timeout_value = 2.0

    base = db_base_url(base_url)
    query = {"ra": ra, "dec": dec, "fov": fov, "target_id": target_id}
    local = match_known_target(ra, dec)
    local_id = local.get("id") if local else None
    explicit = get_known_target(target_id) if target_id else None

    # 1) 显式预设目标：优先 target_id 入口（两入口在 DB 侧保证同形）。
    if explicit is not None:
        status, payload, error = _http_json(_v1_url(base, ra, dec, fov, explicit["id"]), timeout_value)
        if status == "ok":
            try:
                parsed = _parse_payload(payload, ra, dec, fov)
            except _PayloadError as exc:
                return _unavailable(ra, dec, fov, query, explicit, local_id, list(), str(exc))
            matched = parsed["matched"] or explicit["id"]
            return _resolution(parsed["epochs"], parsed["coverage"], matched, parsed["warnings"], query)
        message = "v1 端点不存在（404）" if status == "missing" else (error or "数据库不可用")
        return _unavailable(ra, dec, fov, query, explicit, local_id, list(), message)

    # 2) 自定义坐标：只查 ra/dec 入口。
    status, payload, error = _http_json(_v1_url(base, ra, dec, fov, None), timeout_value)
    if status != "ok":
        message = "v1 端点不存在（404）" if status == "missing" else (error or "数据库不可用")
        return _unavailable(ra, dec, fov, query, None, local_id, list(), message)
    try:
        parsed = _parse_payload(payload, ra, dec, fov)
    except _PayloadError as exc:
        return _unavailable(ra, dec, fov, query, None, local_id, list(), str(exc))

    epochs = parsed["epochs"]
    coverage = parsed["coverage"]
    warnings = list(parsed["warnings"])
    matched = parsed["matched"] or local_id

    # DB 未给 matched_target_id 时，才用本地 0.05° 匹配补查 curated target 入口。
    # DB 给了就以 DB 为准，不额外发请求。
    if parsed["matched"] is None and local_id:
        status_t, payload_t, error_t = _http_json(
            _v1_url(base, ra, dec, fov, local_id), timeout_value
        )
        if status_t == "ok":
            try:
                parsed_t = _parse_payload(payload_t, ra, dec, fov)
            except _PayloadError as exc:
                warnings.append(f"target_id={local_id} 响应不可用: {exc}")
            else:
                warnings.extend(parsed_t["warnings"])
                if parsed_t["epochs"]:
                    epochs = parsed_t["epochs"]
                    coverage = parsed_t["coverage"]
                    matched = parsed_t["matched"] or local_id
        elif status_t != "missing":
            warnings.append(f"target_id={local_id} 查询失败: {error_t or '未知错误'}")

    return _resolution(epochs, coverage, matched, warnings, query)


def _resolution(
    epochs: list[dict],
    coverage: dict | None,
    matched: str | None,
    warnings: list[str],
    query: dict,
) -> EpochResolution:
    return EpochResolution(
        epochs=epochs,
        coverage=coverage if isinstance(coverage, dict) and coverage else coverage_for_epochs(epochs),
        matched_target_id=str(matched) if matched else None,
        fallback_reason=None if epochs else "NO_LOCAL_COVERAGE",
        source="db",
        query=query,
        warnings=list(dict.fromkeys(warnings)),
        transport="v1",
    )


def _unavailable(
    ra: float,
    dec: float,
    fov: float,
    query: dict,
    explicit_target: dict | None,
    local_id: str | None,
    warnings: list[str],
    error: str,
) -> EpochResolution:
    """DB 不可用：显式预设目标可回退本地 JSON；ra/dec 自定义坐标不回退，显式 SIM 由上游决定。"""
    matched = str(explicit_target["id"]) if explicit_target else local_id
    all_warnings = list(dict.fromkeys(list(warnings) + [f"DB 不可用: {error}"]))
    if explicit_target:
        raw = local_catalog_epochs(explicit_target["id"])
        epochs, local_warns = _normalize_many(raw, ra, dec, fov)
        if epochs:
            return EpochResolution(
                epochs=epochs,
                coverage=coverage_for_epochs(epochs),
                matched_target_id=matched,
                fallback_reason="DB_UNAVAILABLE",
                source="local_json",
                query=query,
                warnings=list(dict.fromkeys(all_warnings + local_warns)),
                transport="local_json",
            )
    return EpochResolution(
        epochs=[],
        coverage=coverage_for_epochs([]),
        matched_target_id=matched,
        fallback_reason="DB_UNAVAILABLE",
        source="none",
        query=query,
        warnings=all_warnings,
        transport=None,
    )
