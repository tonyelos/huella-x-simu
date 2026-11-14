"""时间与坐标小工具（只依赖标准库）。"""
from __future__ import annotations

from datetime import datetime, timezone

MJD_EPOCH = 40587.0  # 1858-11-17 00:00:00 UTC 到 1970-01-01 的天数


def mjd_to_iso(mjd: float | None, *, timespec: str = "milliseconds") -> str | None:
    """MJD -> UTC ISO-8601（带 Z）。用于 date_obs 缺失时的确定性回填。"""
    if mjd is None:
        return None
    try:
        unix = (float(mjd) - MJD_EPOCH) * 86400.0
        dt = datetime.fromtimestamp(unix, tz=timezone.utc)
        return dt.isoformat(timespec=timespec).replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None
