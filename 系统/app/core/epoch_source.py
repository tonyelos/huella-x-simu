"""Epoch Resolver：把 (ra, dec, fov) 解析成数据库里的真实历元。

这是 R1 的 app 侧适配层，不是新服务：

  用户坐标 ──► 本地 0.05° 目标匹配 ──► DB /api/v1/epochs（契约 v1）
                                  └──► DB /api/epochs（旧端点，兼容通路）
                                  └──► app/data/epoch_catalog.json（仅显式 target_id 且 DB 不可用）

设计约束（见 04_T2_数据桥接.md）：

- 只读；不写数据库，不缓存跨请求结果。
- DB 连接失败 / 超时 / 非 200 / 坏 JSON 一律降级为 ``DB_UNAVAILABLE``，不抛到 HTTP 500。
- 缺 ``key`` / ``obsid`` / ``mjd`` 的条目丢弃并记 warning；``date_obs`` 为空时由 ``mjd`` 反推 UTC。
- 去重 ``(obsid, detector)``，同一 MJD（±0.02 d）保留 ``sep_deg`` 最小者；排序 ``(mjd, detector)``。
- 不把模拟数据标成 real：``provenance`` 默认 ``real`` 只用于数据库返回的真实目录条目。

旧端点兼容说明：工作包 02 的 ``/api/v1/epochs`` 尚未上线时，本模块会退到现有
``/api/epochs``。它返回的是同一个数据库里的真实历元（不是模拟数据），但缺少 v1 的
``target_id``/``ra/dec`` 目标感知保证；因此这里用本地 0.05° 匹配 + ``?target_id=``
补齐两入口一致性。02 上线后 v1 自动优先，旧通路不再被使用，可整段删除。
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
_MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)
_SLICE_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
_PROVENANCE = ("real", "hybrid", "synthetic", "unknown")
_WEEK_RE = re.compile(r"^(?P<week>\d{4}W\d+_[0-9A-Z]+)")


@dataclass
class EpochResolution:
    """``resolve_epochs`` 的返回值。字段语义见 04_T2_数据桥接.md §T1。"""

    epochs: list[dict] = field(default_factory=list)
    coverage: dict | None = None
    matched_target_id: str | None = None
    fallback_reason: str | None = None
    source: str = "none"  # db | db_legacy | local_json | none
    query: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    transport: str | None = None  # v1 | legacy

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
        targets = payload.get("targets", [])
        return [t for t in targets if isinstance(t, dict) and t.get("id")]
    except (OSError, ValueError, TypeError):
        return []


def angular_sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两点球面角距（度）。"""
    r1, d1, r2, d2 = map(math.radians, (float(ra1), float(dec1), float(ra2), float(dec2)))
    # haversine：小角度下数值稳定
    sd = math.sin((d2 - d1) / 2.0)
    sr = math.sin((r2 - r1) / 2.0)
    a = sd * sd + math.cos(d1) * math.cos(d2) * sr * sr
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(max(a, 0.0)))))


def match_known_target(ra: float, dec: float, tol_deg: float = TARGET_MATCH_TOL_DEG) -> dict | None:
    """在 0.05° 内匹配官方目标；返回目标 dict（含 truth / coord_unc_arcsec），否则 None。"""
    try:
        ra = float(ra)
        dec = float(dec)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return None
    best: tuple[float, dict] | None = None
    for target in load_known_targets():
        try:
            sep = angular_sep_deg(ra, dec, target["ra"], target["dec"])
        except (KeyError, TypeError, ValueError):
            continue
        if sep <= float(tol_deg) and (best is None or sep < best[0]):
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
        eps = payload.get("epochs", {}).get(target_id, [])
        return [e for e in eps if isinstance(e, dict)]
    except (OSError, ValueError, TypeError):
        return []


def mjd_to_iso(mjd: float) -> str:
    """MJD → UTC ISO（毫秒精度，不带时区后缀，与 EpochRef 示例一致）。"""
    moment = _MJD_EPOCH + timedelta(days=float(mjd))
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _iso_from_mjd(mjd: float) -> str:
    return mjd_to_iso(mjd)


def _slice_url(key: str, release: str, ra: float, dec: float, fov: float) -> str:
    relpath = key[len(release) + 1 :] if release and key.startswith(release + "/") else key
    return f"{_SLICE_BASE}{release}/{relpath}?center={ra:.6f},{dec:.6f}d&size={fov}"


# --------------------------------------------------------------------------- DTO 规范化


def _as_float(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _as_int(value) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out


def _normalize_epoch(raw: dict, ra: float, dec: float, fov: float) -> tuple[dict | None, list[str]]:
    """把 v1 / 旧端点 / 本地 JSON 的条目统一成 EpochRef；无效条目返回 (None, warnings)。"""
    warns: list[str] = []
    if not isinstance(raw, dict):
        return None, ["丢弃非对象历元条目"]

    obsid = raw.get("obsid") or raw.get("obs_id") or raw.get("OBSID")
    key = raw.get("key") or raw.get("fits_key")
    if not obsid:
        return None, ["丢弃缺 obsid 的历元条目"]
    if not key:
        return None, [f"丢弃缺 key 的历元条目（obsid={obsid}）"]
    obsid = str(obsid)
    key = str(key)

    mjd = _as_float(raw.get("mjd", raw.get("MJD-OBS")))
    if mjd is None:
        return None, [f"丢弃缺 mjd 的历元条目（obsid={obsid}）"]
    detector = _as_int(raw.get("detector", raw.get("DETECTOR")))
    if detector is None:
        return None, [f"丢弃缺 detector 的历元条目（obsid={obsid}）"]

    date_obs = raw.get("date_obs") or raw.get("DATE-OBS") or raw.get("DATE_OBS")
    if date_obs:
        date_obs = str(date_obs).strip()
        if date_obs.endswith("Z"):
            date_obs = date_obs[:-1]
    if not date_obs:
        date_obs = _iso_from_mjd(mjd)
        warns.append(f"date_obs 为空，已由 mjd={mjd:.6f} 回填 UTC（obsid={obsid}）")

    release = str(raw.get("release") or "").strip()
    if not release:
        release = key.split("/", 1)[0] if "/" in key else ""
        if not release:
            warns.append(f"缺 release（obsid={obsid}）")
    week = str(raw.get("week") or "").strip()
    if not week:
        match = _WEEK_RE.match(obsid)
        week = match.group("week") if match else ""
        if not week:
            warns.append(f"缺 week（obsid={obsid}）")
    version = str(raw.get("version") or "").strip()
    if not version:
        parts = key.split("/")
        # dr1/level2/<week>/<version>/<detector>/<file>
        if len(parts) >= 5 and parts[1] == "level2":
            version = parts[3]
        if not version:
            warns.append(f"缺 version（obsid={obsid}）")

    sep = _as_float(raw.get("sep_deg"))
    if sep is None:
        frame_ra = _as_float(raw.get("frame_ra", raw.get("ra")))
        frame_dec = _as_float(raw.get("frame_dec", raw.get("dec")))
        if frame_ra is not None and frame_dec is not None:
            sep = angular_sep_deg(ra, dec, frame_ra, frame_dec)
        else:
            warns.append(f"缺 sep_deg（obsid={obsid}）")

    provenance = str(raw.get("provenance") or "real").lower()
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
        "frame_id": raw.get("frame_id"),
    }
    return epoch, warns


def _dedupe_and_sort(epochs: list[dict], tol_days: float = 0.02) -> list[dict]:
    """去重 ``(obsid, detector)`` + 同 MJD 保留 sep 最小者，最后按 (mjd, detector) 稳定排序。"""
    by_key: dict[tuple[str, int], dict] = {}
    for epoch in sorted(
        epochs,
        key=lambda e: (e.get("mjd") or 0.0, e.get("sep_deg") if e.get("sep_deg") is not None else 9.0, e.get("detector") or 0),
    ):
        k = (str(epoch.get("obsid")), int(epoch.get("detector")))
        current = by_key.get(k)
        if current is None:
            by_key[k] = epoch
        else:
            cur_sep = current.get("sep_deg") if current.get("sep_deg") is not None else 9.0
            new_sep = epoch.get("sep_deg") if epoch.get("sep_deg") is not None else 9.0
            if new_sep < cur_sep:
                by_key[k] = epoch

    ordered = sorted(by_key.values(), key=lambda e: (e["mjd"], e["detector"]))
    out: list[dict] = []
    for epoch in ordered:
        if not out or abs(epoch["mjd"] - out[-1]["mjd"]) > tol_days:
            out.append(epoch)
        else:
            prev_sep = out[-1].get("sep_deg") if out[-1].get("sep_deg") is not None else 9.0
            new_sep = epoch.get("sep_deg") if epoch.get("sep_deg") is not None else 9.0
            if new_sep < prev_sep:
                out[-1] = epoch
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


def _http_json(url: str, timeout: float) -> tuple[str, dict | list | None, str | None]:
    """GET JSON，1 次重试；返回 (status, payload, error)。

    status: "ok" | "missing"（404）| "error"
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
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        if attempt == 0:
            time.sleep(0.05)  # 只做一次短重试；不要把 HTTP 线程拖死
    return "error", None, last_error


def _url_v1(base: str, ra: float, dec: float, fov: float, target_id: str | None) -> str:
    if target_id:
        params = {"target_id": target_id}
    else:
        params = {"ra": f"{ra:.6f}", "dec": f"{dec:.6f}", "fov": f"{fov:g}"}
    return f"{base}/api/v1/epochs?{urllib.parse.urlencode(params)}"


def _url_legacy(base: str, ra: float, dec: float, fov: float, target_id: str | None) -> str:
    if target_id:
        params = {"target_id": target_id}
    else:
        params = {"ra": f"{ra:.6f}", "dec": f"{dec:.6f}", "fov": f"{fov:g}"}
    return f"{base}/api/epochs?{urllib.parse.urlencode(params)}"


def _candidate(
    payload: dict | list | None,
    ra: float,
    dec: float,
    fov: float,
) -> tuple[list[dict], dict | None, str | None, list[str]]:
    """从 v1/旧端点响应中取出 (epochs, coverage, matched_target_id, warnings)。"""
    if isinstance(payload, list):
        epochs, warns = _normalize_many(payload, ra, dec, fov)
        return epochs, coverage_for_epochs(epochs), None, warns
    if not isinstance(payload, dict):
        return [], None, None, ["响应不是 JSON 对象"]
    raw_epochs = payload.get("epochs")
    if raw_epochs is None:
        raw_epochs = []
    epochs, warns = _normalize_many(raw_epochs, ra, dec, fov)
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        coverage = coverage_for_epochs(epochs)
    matched = payload.get("matched_target_id")
    return epochs, coverage, str(matched) if matched else None, warns


def _best_candidate(candidates: list[dict]) -> dict:
    """多个入口都成功时：命中的目标优先用 curated target_id 历元，否则取历元较多者。

    这条规则与数据库契约一致（命中 target 时优先 target_epochs），也保证
    “手动输入 Barnard 坐标”与“点预设”拿到同一组历元；目标条目为空时才退回
    ra/dec 的 frames 锥搜索结果。
    """
    def rank(c):
        curated = 1 if c["kind"] == "target_id" and c["epochs"] else 0
        return (curated, len(c["epochs"]))

    return max(candidates, key=rank)


def _query_both(
    base: str,
    url_builder,
    ra: float,
    dec: float,
    fov: float,
    target_id: str | None,
    local_target_id: str | None,
    timeout: float,
) -> tuple[str, list[dict], str | None, list[str]]:
    """查 ra/dec；本地匹配到目标时再查 target_id，取历元较多者。

    返回 (status, candidates, error, warnings)。status: ok | missing | error。
    """
    candidates: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    if target_id is not None:
        status, payload, error = _http_json(url_builder(base, ra, dec, fov, target_id), timeout)
        if status == "missing":
            return "missing", [], None, []
        if status == "ok":
            epochs, coverage, matched, warns = _candidate(payload, ra, dec, fov)
            candidates.append(
                {"epochs": epochs, "coverage": coverage, "matched": matched, "warnings": warns, "kind": "target_id"}
            )
        else:
            errors.append(error or "请求失败")
    else:
        status, payload, error = _http_json(url_builder(base, ra, dec, fov, None), timeout)
        if status == "missing":
            return "missing", [], None, []
        if status == "ok":
            epochs, coverage, matched, warns = _candidate(payload, ra, dec, fov)
            candidates.append({"epochs": epochs, "coverage": coverage, "matched": matched, "warnings": warns, "kind": "radec"})
            warnings.extend(warns)
        else:
            errors.append(error or "请求失败")
        if local_target_id:
            status_t, payload_t, error_t = _http_json(url_builder(base, ra, dec, fov, local_target_id), timeout)
            if status_t == "ok":
                epochs_t, coverage_t, matched_t, warns_t = _candidate(payload_t, ra, dec, fov)
                candidates.append(
                    {"epochs": epochs_t, "coverage": coverage_t, "matched": matched_t, "warnings": warns_t, "kind": "target_id"}
                )
                warnings.extend(warns_t)
            elif status_t not in ("missing",):
                errors.append(error_t or "target_id 请求失败")
            # 404 的旧/v1 目标入口不影响 ra/dec 的成功结果

    if not candidates:
        return "error", [], "; ".join(errors) or "无可用响应", warnings
    return "ok", candidates, None, warnings


# --------------------------------------------------------------------------- 主入口


def resolve_epochs(
    ra: float,
    dec: float,
    fov: float,
    target_id: str | None = None,
    *,
    base_url: str | None = None,
    timeout: float = 2.0,
    allow_legacy: bool = True,
) -> EpochResolution:
    """解析 (ra, dec, fov) 对应的真实历元。

    失败时返回 ``EpochResolution``，绝不抛网络异常；只有参数非法才抛 ``ValueError``
    （由 HTTP 层转 400）。
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
    if not (0.0 < fov <= 3.5):
        raise ValueError("fov 必须在 (0, 3.5] 度")

    base = db_base_url(base_url)
    local = match_known_target(ra, dec)
    local_id = local.get("id") if local else None
    matched = str(target_id) if target_id else local_id
    query = {"ra": ra, "dec": dec, "fov": fov, "target_id": target_id}

    # 1) 契约 v1
    status, candidates, error, warns = _query_both(
        base, _url_v1, ra, dec, fov, target_id, local_id if target_id is None else None, timeout
    )
    if status == "missing" and allow_legacy:
        # 2) 旧端点兼容通路：02 上线前使用；v1 一旦可用就自动不再走到这里
        status, candidates, error, legacy_warns = _query_both(
            base, _url_legacy, ra, dec, fov, target_id, local_id if target_id is None else None, timeout
        )
        warns.extend(legacy_warns)
        if status == "ok":
            return _resolution_from_candidates(
                candidates, ra, dec, fov, target_id, local_id, "db_legacy", "legacy", warns, query
            )
        if status == "missing":
            error = "旧端点 /api/epochs 不存在"
        return _unavailable(
            ra, dec, fov, target_id, local_id, query, warns, error or "数据库不可用", allow_local=target_id is not None
        )

    if status == "ok":
        return _resolution_from_candidates(candidates, ra, dec, fov, target_id, local_id, "db", "v1", warns, query)
    return _unavailable(
        ra, dec, fov, target_id, local_id, query, warns, error or "数据库不可用", allow_local=target_id is not None
    )


def _resolution_from_candidates(
    candidates: list[dict],
    ra: float,
    dec: float,
    fov: float,
    target_id: str | None,
    local_id: str | None,
    source: str,
    transport: str,
    warnings: list[str],
    query: dict,
) -> EpochResolution:
    chosen = _best_candidate(candidates)
    epochs = chosen["epochs"]
    coverage = chosen["coverage"] or coverage_for_epochs(epochs)
    matched = chosen.get("matched") or target_id or local_id
    all_warnings = list(dict.fromkeys(warnings + list(chosen.get("warnings") or [])))
    return EpochResolution(
        epochs=epochs,
        coverage=coverage,
        matched_target_id=str(matched) if matched else None,
        fallback_reason=None if epochs else "NO_LOCAL_COVERAGE",
        source=source,
        query=query,
        warnings=all_warnings,
        transport=transport,
    )


def _unavailable(
    ra: float,
    dec: float,
    fov: float,
    target_id: str | None,
    local_id: str | None,
    query: dict,
    warnings: list[str],
    error: str,
    *,
    allow_local: bool,
) -> EpochResolution:
    """DB 不可用：显式 target_id 可回退本地 JSON；ra/dec 自定义坐标不回退（契约要求 SIM）。"""
    matched = str(target_id) if target_id else local_id
    if allow_local and target_id:
        raw = local_catalog_epochs(target_id)
        epochs, local_warns = _normalize_many(raw, ra, dec, fov)
        if epochs:
            return EpochResolution(
                epochs=epochs,
                coverage=coverage_for_epochs(epochs),
                matched_target_id=matched,
                fallback_reason="DB_UNAVAILABLE",
                source="local_json",
                query=query,
                warnings=list(dict.fromkeys(warnings + local_warns + [f"DB 不可用: {error}"])),
                transport="local_json",
            )
    return EpochResolution(
        epochs=[],
        coverage=coverage_for_epochs([]),
        matched_target_id=matched,
        fallback_reason="DB_UNAVAILABLE",
        source="none",
        query=query,
        warnings=list(dict.fromkeys(warnings + [f"DB 不可用: {error}"])),
        transport=None,
    )
