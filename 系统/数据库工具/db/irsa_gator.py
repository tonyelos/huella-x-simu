"""IRSA Gator 2MASS / AllWISE 真值补充（只用标准库）。

Gaia DR3 的 `tmass_psc_xsc_best_neighbour` / `allwise_best_neighbour` 只给
交叉证认 ID，不给 J/H/K/W1/W2 星等。本模块在导入 Gaia 场后，用 IRSA Gator
按同一个场锥查 2MASS PSC 与 AllWISE Source Catalog，再按 designation 对回
Gaia 行，填充 `catalog_source.jmag/hmag/kmag/w1/w2`。

边界：

- 只用真实 Gator 行，不合成/不推断星等；
- 任一目录查询失败，该目录的列保持 null，并记录错误；
- 不把 2MASS/WISE 行写进 Gaia 的 `source_id`；它们是 Gaia 行的附加测光。
- 2MASS/WISE 数据公共领域；Gator 是 IRSA 的查询接口。

参考：
- 2MASS PSC: https://irsa.ipac.caltech.edu/Missions/2mass.html
- AllWISE: https://irsa.ipac.caltech.edu/Missions/wise.html
- Gator: https://irsa.ipac.caltech.edu/applications/Gator/
"""
from __future__ import annotations

import math
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

IRSA_GATOR_URL = "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query"
USER_AGENT = "huellaX-star-catalog/1.0 (2MASS/WISE photometry enrichment)"

CATALOGS = {
    "2mass": {
        "gator_catalog": "fp_psc",
        "label": "2MASS All-Sky Point Source Catalog (PSC)",
        "id_column": "designation",
        "photo": {"jmag": "j_m", "hmag": "h_m", "kmag": "k_m"},
        "quality": "ph_qual",
        "license": "Public domain (2MASS/IPAC/Caltech/UMass)",
        "source_url": "https://irsa.ipac.caltech.edu/Missions/2mass.html",
    },
    "wise": {
        "gator_catalog": "allwise_p3as_psd",
        "label": "AllWISE Source Catalog",
        "id_column": "designation",
        "photo": {"w1": "w1mpro", "w2": "w2mpro"},
        "quality": "ph_qual",
        "license": "Public domain (NASA/JPL-Caltech)",
        "source_url": "https://irsa.ipac.caltech.edu/Missions/wise.html",
    },
}


class IRSAGatorError(RuntimeError):
    pass


def _curl_get(url: str, *, timeout: float) -> bytes:
    """用系统 curl 兜底：IRSA 只发 leaf 证书，Python OpenSSL 常缺中间 CA，
    而 macOS/Linux 的系统 curl 能走系统信任链。curl 仍做证书校验（不加 -k）。"""
    try:
        proc = subprocess.run(
            [
                "curl",
                "-fsS",
                "--max-time",
                str(max(1, int(timeout))),
                "-A",
                USER_AGENT,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 5,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise IRSAGatorError(f"IRSA Gator curl 超时: {timeout:g}s") from exc
    if proc.returncode != 0:
        raise IRSAGatorError(
            f"IRSA Gator curl 请求失败 ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace')[:300]}"
        )
    return proc.stdout


def _http_get(url: str, *, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError) and shutil.which("curl"):
            return _curl_get(url, timeout=timeout)
        raise


def _request_with_retries(url: str, *, timeout: float, retries: int) -> bytes:
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return _http_get(url, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise IRSAGatorError(f"IRSA Gator 请求失败: {last}")


def gator_url(catalog: str, ra: float, dec: float, radius_deg: float) -> str:
    conf = CATALOGS[catalog]
    params = {
        "catalog": conf["gator_catalog"],
        "spatial": "cone",
        "objstr": f"{ra:.8f} {dec:.8f}",
        # Gator 的 radius 单位是角秒。
        "radius": f"{float(radius_deg) * 3600.0:.3f}",
        "outfmt": "1",  # IPAC table
    }
    return IRSA_GATOR_URL + "?" + urllib.parse.urlencode(params)


def _float(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "nan", "none"):
        return None
    try:
        out = float(s)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def _string(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "null":
        return None
    return s


def parse_ipac_table(text: str) -> list[dict]:
    """解析 Gator 的 IPAC 表（outfmt=1）。

    Gator 返回：`\\` 元数据行 + `|` 分隔的 header/type/unit/null 行 + 数据行。
    数据行通常是空白分隔；设计ation/qual 等字符串字段不含空格。
    """
    if not text or not text.strip():
        raise IRSAGatorError("IRSA Gator 返回空响应")
    lines = text.splitlines()
    meta = {}
    for line in lines:
        m = re.match(r"\\RowsRetrieved\s*=\s*(\d+)", line)
        if m:
            meta["rows"] = int(m.group(1))
    header_idx = next((i for i, line in enumerate(lines) if line.startswith("|")), None)
    if header_idx is None:
        raise IRSAGatorError(f"IRSA Gator 响应不是 IPAC 表: {text[:300]!r}")
    header = [x.strip() for x in lines[header_idx].strip().strip("|").split("|")]
    rows: list[dict] = []
    for line in lines[header_idx + 4 :]:
        if not line.strip() or line.startswith("\\"):
            continue
        values = line.split()
        if len(values) != len(header):
            # 固定宽度的字符串列若含空格会在这里暴露；宁可显式失败也不猜。
            raise IRSAGatorError(
                f"IRSA Gator 行列数不匹配: {len(values)} != {len(header)}: {line[:160]!r}"
            )
        rows.append(dict(zip(header, values)))
    expected = meta.get("rows")
    if expected is not None and expected != len(rows):
        raise IRSAGatorError(
            f"IRSA Gator 结果不完整: RowsRetrieved={expected}, parsed={len(rows)}"
        )
    return rows


def fetch_gator_cone(
    catalog: str,
    ra: float,
    dec: float,
    radius_deg: float,
    *,
    timeout: float = 240.0,
    retries: int = 3,
) -> list[dict]:
    if catalog not in CATALOGS:
        raise KeyError(f"未知 IRSA 目录: {catalog}")
    url = gator_url(catalog, ra, dec, radius_deg)
    raw = _request_with_retries(url, timeout=timeout, retries=retries)
    return parse_ipac_table(raw.decode("utf-8", "replace"))


def normalize_gator_rows(catalog: str, rows: Iterable[dict]) -> list[dict]:
    conf = CATALOGS[catalog]
    id_col = conf["id_column"]
    out: list[dict] = []
    for row in rows:
        cid = _string(row.get(id_col))
        if not cid:
            continue
        item = {
            "catalog": catalog,
            "catalog_id": cid,
            "ra": _float(row.get("ra")),
            "dec": _float(row.get("dec")),
            "quality": _string(row.get(conf["quality"])),
        }
        for field, column in conf["photo"].items():
            item[field] = _float(row.get(column))
        out.append(item)
    return out


def fetch_field_photometry(
    ra: float,
    dec: float,
    radius_deg: float,
    *,
    timeout: float = 240.0,
    retries: int = 3,
) -> dict:
    """抓取一个场的 2MASS/WISE 真值；单个目录失败只影响该目录。"""
    result: dict = {
        "catalogs": {},
        "errors": {},
    }
    for catalog in ("2mass", "wise"):
        try:
            raw_rows = fetch_gator_cone(
                catalog, ra, dec, radius_deg, timeout=timeout, retries=retries
            )
            rows = normalize_gator_rows(catalog, raw_rows)
            result["catalogs"][catalog] = rows
        except (IRSAGatorError, KeyError, ValueError) as exc:
            result["catalogs"][catalog] = []
            result["errors"][catalog] = str(exc)
    return result


def enrich_gaia_rows(
    gaia_rows: list[dict],
    photometry: dict,
) -> dict:
    """按 Gaia crossmatch ID 把 2MASS/WISE 星等填入 Gaia 行。

    返回覆盖率统计（matched 是成功对上星等的 Gaia 行数，rows 是 Gator 行数）。
    """
    twomass = {
        r["catalog_id"]: r for r in photometry.get("catalogs", {}).get("2mass", [])
    }
    wise = {
        r["catalog_id"]: r for r in photometry.get("catalogs", {}).get("wise", [])
    }
    stats = {
        "2mass": {"rows": len(twomass), "matched": 0, "j_available": 0, "h_available": 0, "k_available": 0},
        "wise": {"rows": len(wise), "matched": 0, "w1_available": 0, "w2_available": 0},
        "errors": dict(photometry.get("errors") or {}),
    }
    for row in gaia_rows:
        # 统一键存在；无匹配/无值时保持 null，避免下游 KeyError。
        for key in ("jmag", "hmag", "kmag", "w1", "w2"):
            row.setdefault(key, None)
        tmid = row.get("twomass_id")
        if tmid and tmid in twomass:
            rec = twomass[tmid]
            row["jmag"] = rec.get("jmag")
            row["hmag"] = rec.get("hmag")
            row["kmag"] = rec.get("kmag")
            stats["2mass"]["matched"] += 1
            for f, k in (("jmag", "j_available"), ("hmag", "h_available"), ("kmag", "k_available")):
                if rec.get(f) is not None:
                    stats["2mass"][k] += 1
        wid = row.get("wise_id")
        if wid and wid in wise:
            rec = wise[wid]
            row["w1"] = rec.get("w1")
            row["w2"] = rec.get("w2")
            stats["wise"]["matched"] += 1
            for f, k in (("w1", "w1_available"), ("w2", "w2_available")):
                if rec.get(f) is not None:
                    stats["wise"][k] += 1
    return stats


def photometry_summary(photometry: dict, stats: dict) -> dict:
    """给 catalog_field.photometry_json / fixture meta 用的稳定摘要。"""
    return {
        "status": "ok" if not photometry.get("errors") else "partial",
        "source": "IRSA Gator: 2MASS PSC fp_psc + AllWISE allwise_p3as_psd",
        "source_url": IRSA_GATOR_URL,
        "license": {
            "2mass": CATALOGS["2mass"]["license"],
            "wise": CATALOGS["wise"]["license"],
        },
        "2mass": stats.get("2mass"),
        "wise": stats.get("wise"),
        "errors": stats.get("errors") or {},
    }
