"""On-demand epoch discovery: week_index + S3 Range headers.

Reuses the existing ``系统/tools/find_epochs.py`` implementation (week-index
ecliptic filter, S3 ListObjects, 45 KB Range headers, D1–D6 probe). It does
not download pixels and does not generate epochs. The only network dependency
is the public S3 bucket, which can be overridden for offline fake-S3 tests.

A discovery result is a candidate list, not a coverage assertion: an empty
list means "no candidate found in the probed weeks", never 422.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_ROOT.parent.parent
TOOLS_ROOT = SERVICE_ROOT.parent / "tools"
DEFAULT_S3_BASE = "https://nasa-irsa-spherex.s3.amazonaws.com"
DEFAULT_IRSA_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
DEFAULT_WEEK_INDEX = SERVICE_ROOT.parent / "app" / "data" / "week_index.json"
DEFAULT_CACHE = SERVICE_ROOT / "var" / "discovery_headers"
DEFAULT_MAX_WEEKS = 6
MAX_WEEKS_CAP = 20
WEEK_ECLIPTIC_MARGIN_DEG = 28.0
CACHE_TTL_S = 300.0
EMPTY_TTL_S = 60.0

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
import find_epochs  # noqa: E402  (existing repo logic)


class DiscoveryError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.retryable = bool(retryable)


class Discovery:
    def __init__(
        self,
        *,
        s3_base: str = DEFAULT_S3_BASE,
        irsa_base: str = DEFAULT_IRSA_BASE,
        week_index_path: str | Path = DEFAULT_WEEK_INDEX,
        cache_dir: str | Path = DEFAULT_CACHE,
        max_weeks_default: int = DEFAULT_MAX_WEEKS,
    ) -> None:
        self.s3_base = str(s3_base).rstrip("/")
        self.irsa_base = str(irsa_base).rstrip("/") + "/"
        self.week_index_path = Path(week_index_path).expanduser().resolve()
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.max_weeks_default = max(1, min(int(max_weeks_default), MAX_WEEKS_CAP))
        self._lock = threading.RLock()
        self._cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _eq2ecl(ra: float, dec: float) -> tuple[float, float]:
        return find_epochs.eq2ecl(ra, dec)

    def _slice_url(self, key: str, ra: float, dec: float, fov: float) -> str:
        # IRSA static cutout path keeps the release prefix: <base>/<dr1|qr3>/level2/...
        return f"{self.irsa_base}{key}?center={ra:.6f},{dec:.6f}d&size={fov:g}"

    def _load_index(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.week_index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DiscoveryError("WEEK_INDEX_UNAVAILABLE", f"week_index 不可读：{exc}") from exc
        weeks = payload.get("weeks") if isinstance(payload, dict) else None
        if not isinstance(weeks, list):
            raise DiscoveryError("WEEK_INDEX_UNAVAILABLE", "week_index 缺少 weeks 列表")
        return [w for w in weeks if isinstance(w, dict) and w.get("release") and w.get("week")]

    def _preflight(self) -> None:
        try:
            find_epochs._get(
                f"{self.s3_base}/?list-type=2&prefix=dr1/level2/&delimiter=/&max-keys=1",
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001 - network/proxy/timeout
            raise DiscoveryError(
                "UPSTREAM_UNAVAILABLE",
                f"S3 不可达或超时：{exc}",
            ) from exc

    def _probe(self, release: str, week: str, version: str, ra: float, dec: float) -> list[dict[str, Any]]:
        try:
            result = find_epochs.probe_week(release, week, version, (ra, dec))
        except Exception:
            return []
        if not result:
            return []
        out: list[dict[str, Any]] = []
        for ep in result:
            key = str(ep.get("key") or "")
            if not key:
                continue
            out.append(
                {
                    "obsid": str(ep.get("obsid") or ""),
                    "detector": int(ep.get("detector") or 0),
                    "release": str(ep.get("release") or release),
                    "week": str(ep.get("week") or week),
                    "version": str(ep.get("version") or version),
                    "mjd": ep.get("mjd"),
                    "date_obs": str(ep.get("date_obs") or ""),
                    "key": key,
                    "slice_url": self._slice_url(key, ra, dec, 0.2),
                    "sep_deg": round(float(ep.get("sep_deg") or 0.0), 6),
                    "discovery": "week_index+s3_header",
                    "provenance": "real",
                }
            )
        return out

    # ------------------------------------------------------------------ public
    def discover(self, ra: float, dec: float, fov: float, max_weeks: int | None = None) -> dict[str, Any]:
        max_weeks_i = self.max_weeks_default if max_weeks is None else int(max_weeks)
        if not (1 <= max_weeks_i <= MAX_WEEKS_CAP):
            raise DiscoveryError("INVALID_QUERY", f"max_weeks 必须在 [1, {MAX_WEEKS_CAP}]", retryable=False)
        ra = float(ra) % 360.0
        dec = float(dec)
        fov = float(fov)
        cache_key = (round(ra, 6), round(dec, 6), round(fov, 6), max_weeks_i)
        now = time.time()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

        index = self._load_index()
        lon, _beta = self._eq2ecl(ra, dec)
        weeks = [
            w for w in index
            if abs((float(w.get("elon") or 0.0) - lon + 180.0) % 360.0 - 180.0) < WEEK_ECLIPTIC_MARGIN_DEG
        ]
        weeks.sort(key=lambda w: abs((float(w.get("elon") or 0.0) - lon + 180.0) % 360.0 - 180.0))
        weeks = weeks[:max_weeks_i]
        if not weeks:
            result = self._empty(ra, dec, fov, max_weeks_i, "NO_WEEK_INDEX_MATCH", "周索引中没有黄经接近的候选周")
            with self._lock:
                self._cache[cache_key] = (now + EMPTY_TTL_S, result)
            return result

        with self._lock:
            self._preflight()
            old_s3, old_cache, old_hdr = find_epochs.S3, find_epochs.CACHE, find_epochs.HDR
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            find_epochs.S3 = self.s3_base
            find_epochs.CACHE = str(self.cache_dir)
            find_epochs.HDR = 65536
            found: list[dict[str, Any]] = []
            failed_weeks = 0
            try:
                for w in weeks:
                    release = str(w["release"])
                    week = str(w["week"])
                    version = str(w.get("version") or "")
                    if not version:
                        failed_weeks += 1
                        continue
                    eps = self._probe(release, week, version, ra, dec)
                    if not eps:
                        failed_weeks += 1
                    found.extend(eps)
            finally:
                find_epochs.S3 = old_s3
                find_epochs.CACHE = old_cache
                find_epochs.HDR = old_hdr

        # One candidate per (obsid,detector,release,version); keep nearest.
        dedup: dict[tuple[str, int, str, str], dict[str, Any]] = {}
        for ep in found:
            key = (ep["obsid"], ep["detector"], ep["release"], ep["version"])
            cur = dedup.get(key)
            if cur is None or ep["sep_deg"] < cur["sep_deg"]:
                dedup[key] = ep
        candidates = sorted(
            dedup.values(),
            key=lambda e: (e.get("mjd") or 0.0, e.get("detector") or 0, e["sep_deg"]),
        )
        for ep in candidates:
            ep["slice_url"] = self._slice_url(ep["key"], ra, dec, fov)

        if not candidates:
            # The preflight already established S3 reachability. A reachable
            # S3 with no candidates is an empty discovery (200), not a 422 and
            # not a fabricated 503. Per-week transient errors are counted in
            # weeks_without_candidates for observability.
            result = self._empty(ra, dec, fov, max_weeks_i, "NO_S3_CANDIDATES", "候选周已探测但没有覆盖该天区的历元")
            with self._lock:
                self._cache[cache_key] = (now + EMPTY_TTL_S, result)
            return result

        result = {
            "query": {"ra": round(ra, 6), "dec": round(dec, 6), "fov": fov, "max_weeks": max_weeks_i},
            "discovery": "week_index+s3_header",
            "candidates": candidates,
            "count": len(candidates),
            "reason": None,
            "weeks_probed": len(weeks),
            "weeks_without_candidates": failed_weeks,
            "warnings": ["METADATA_ONLY: candidates are header-based; no pixels downloaded"],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with self._lock:
            self._cache[cache_key] = (now + CACHE_TTL_S, result)
        return result

    @staticmethod
    def _empty(ra: float, dec: float, fov: float, max_weeks: int, reason: str, message: str) -> dict[str, Any]:
        return {
            "query": {"ra": round(ra, 6), "dec": round(dec, 6), "fov": fov, "max_weeks": max_weeks},
            "discovery": "week_index+s3_header",
            "candidates": [],
            "count": 0,
            "reason": reason,
            "message": message,
            "warnings": ["EMPTY_DISCOVERY: empty candidate list is not a coverage assertion"],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
