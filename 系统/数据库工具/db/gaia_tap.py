"""Gaia DR3 TAP 锥搜索客户端（只用标准库）。

真实数据入口：ESA Gaia Archive TAP `sync` + ADQL `CIRCLE` 锥搜索。网络失败时
由导入工具回退到 `tests/fixtures/gaia_dr3_sample.csv`（真实 Gaia 行的小样本），
并把 release/source 的 provenance 标成 `fixture`/`mixed`，不伪装成在线结果。

参考：
- Gaia Archive: https://gea.esac.esa.int/archive/
- TAP: https://gea.esac.esa.int/tap-server/tap
- 许可：CC BY-NC 3.0 IGO
"""
from __future__ import annotations

import csv
import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .star_config import GAIA_EPOCH_MJD, GAIA_EPOCH_REF, GAIA_MAG_LIMIT, PRESET_FIELDS

GAIA_TAP_SYNC = "https://gea.esac.esa.int/tap-server/tap/sync"
GAIA_ARCHIVE_URL = "https://gea.esac.esa.int/archive/"
GAIA_DR3_LICENSE = "CC BY-NC 3.0 IGO"
GAIA_DR3_CITATION = (
    "ESA/Gaia/DPAC; Gaia Collaboration et al. 2023, A&A 674, A1, "
    "DOI 10.1051/0004-6361/202243940"
)
GAIA_DR3_VERSION = "dr3"
GAIA_DR3_RELEASE = "gaia-dr3"
USER_AGENT = "huellaX-star-catalog/1.0 (offline-friendly Gaia DR3 cache)"

# 2MASS/WISE 只保留接口与许可；正式导入器可以复用 catalog_source 的 j/h/k/w1/w2 列。
CATALOG_LICENSES = {
    "gaia": GAIA_DR3_LICENSE,
    "2mass": "Public domain (2MASS/IPAC/Caltech/UMass)",
    "wise": "Public domain (NASA/JPL-Caltech)",
}

BASE_COLUMNS = (
    "source_id",
    "ra",
    "dec",
    "pmra",
    "pmdec",
    "parallax",
    "ra_error",
    "dec_error",
    "pmra_error",
    "pmdec_error",
    "phot_g_mean_mag",
    "bp_rp",
    "ref_epoch",
    "in_qso_candidates",
    "in_galaxy_candidates",
    "non_single_star",
    "twomass_id",
    "wise_id",
)


class GaiaTAPError(RuntimeError):
    pass


def adql_cone_query(
    ra: float,
    dec: float,
    radius_deg: float,
    *,
    mag_limit: float = GAIA_MAG_LIMIT,
    columns: Iterable[str] = BASE_COLUMNS,
) -> str:
    cols = ",\n       ".join(f"g.{c}" for c in columns if c not in ("twomass_id", "wise_id"))
    # 2MASS/WISE 的 best-neighbour 表按 source_id 一对一；LEFT JOIN 保留没有
    # 红外对应体的 Gaia 源，ID 为空时 API 返回 null。
    return f"""
SELECT {cols},
       t.original_ext_source_id AS twomass_id,
       w.original_ext_source_id AS wise_id
FROM gaiadr3.gaia_source AS g
LEFT JOIN gaiadr3.tmass_psc_xsc_best_neighbour AS t ON t.source_id = g.source_id
LEFT JOIN gaiadr3.allwise_best_neighbour AS w ON w.source_id = g.source_id
WHERE CONTAINS(POINT('ICRS', g.ra, g.dec), CIRCLE('ICRS', {ra:.8f}, {dec:.8f}, {radius_deg:g})) = 1
  AND g.phot_g_mean_mag <= {mag_limit:g}
""".strip()


def tap_sync_url(query: str) -> str:
    params = {
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "csv",
        "QUERY": query,
    }
    return GAIA_TAP_SYNC + "?" + urllib.parse.urlencode(params)


def _http_get(url: str, *, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_gaia_cone(
    ra: float,
    dec: float,
    radius_deg: float,
    *,
    mag_limit: float = GAIA_MAG_LIMIT,
    timeout: float = 240.0,
    retries: int = 3,
) -> tuple[list[dict], str, str]:
    """执行一次真实 Gaia DR3 TAP 锥搜索。

    返回 `(normalized_rows, raw_csv, query)`。网络/服务失败抛 GaiaTAPError；
    调用方负责决定是否回退 fixture。
    """
    query = adql_cone_query(ra, dec, radius_deg, mag_limit=mag_limit)
    url = tap_sync_url(query)
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            raw = _http_get(url, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            raise GaiaTAPError(f"Gaia TAP 请求失败: {exc}") from exc
        text = raw.decode("utf-8", "replace")
        try:
            rows = parse_gaia_csv(text)
        except GaiaTAPError as exc:
            # TAP 有时 HTTP 200 但返回错误文本；保留响应片段便于排障。
            raise GaiaTAPError(f"{exc}; 响应片段: {text[:300]!r}") from exc
        return rows, text, query
    raise GaiaTAPError(f"Gaia TAP 请求失败: {last}")


def _bool(value) -> bool | None:
    if value is None or value == "":
        return None
    s = str(value).strip().lower()
    if s in ("true", "t", "1"):
        return True
    if s in ("false", "f", "0"):
        return False
    return None


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    s = str(value).strip()
    if s.lower() in ("nan", "null", "none"):
        return None
    try:
        out = float(s)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def object_type_from_gaia(row: dict) -> str:
    """把 Gaia 的目录标志翻成粗粒度 object_type，不是科学分类。

    `in_qso_candidates` / `in_galaxy_candidates` 是 Gaia 发布方给出的候选标志；
    两者都不是时写 `star`（Gaia 源默认点源，不代表已做恒星/星系判定）。
    """
    if _bool(row.get("in_qso_candidates")):
        return "qso_candidate"
    if _bool(row.get("in_galaxy_candidates")):
        return "galaxy_candidate"
    if _bool(row.get("non_single_star")):
        return "non_single_star"
    return "star"


def jyear_to_mjd(jyear: float | None) -> float | None:
    """Julian year → MJD（J2000.0 = MJD 51544.5，365.25 d/yr）。"""
    if jyear is None:
        return None
    return 51544.5 + (float(jyear) - 2000.0) * 365.25


def normalize_gaia_row(row: dict, *, field_id: str | None = None, provenance: str = "tap") -> dict:
    sid = str(row.get("source_id") or "").strip()
    if not sid:
        raise GaiaTAPError("Gaia 行缺少 source_id")
    ra = _float(row.get("ra"))
    dec = _float(row.get("dec"))
    if ra is None or dec is None:
        raise GaiaTAPError(f"Gaia source {sid} 缺少有限 ra/dec")
    ref_epoch_jy = _float(row.get("ref_epoch"))
    out = {
        "catalog_id": sid,
        "ra": ra,
        "dec": dec,
        "pmra": _float(row.get("pmra")),
        "pmdec": _float(row.get("pmdec")),
        "parallax": _float(row.get("parallax")),
        "ra_error": _float(row.get("ra_error")),
        "dec_error": _float(row.get("dec_error")),
        "pmra_error": _float(row.get("pmra_error")),
        "pmdec_error": _float(row.get("pmdec_error")),
        "gmag": _float(row.get("phot_g_mean_mag", row.get("gmag"))),
        "bp_rp": _float(row.get("bp_rp")),
        "ref_epoch": ref_epoch_jy,
        # 库里统一存 MJD（release 另存 epoch_ref="J2016.0" 文本，二者互相对应）。
        "epoch_ref": jyear_to_mjd(ref_epoch_jy),
        "twomass_id": row.get("twomass_id") or None,
        "wise_id": row.get("wise_id") or None,
        "in_qso_candidates": row.get("in_qso_candidates"),
        "in_galaxy_candidates": row.get("in_galaxy_candidates"),
        "non_single_star": row.get("non_single_star"),
        "object_type": object_type_from_gaia(row),
        "provenance": provenance,
    }
    if field_id:
        out["field_id"] = field_id
    return out


def parse_gaia_csv(text: str) -> list[dict]:
    if not text or not text.strip():
        raise GaiaTAPError("Gaia TAP 返回空响应")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "source_id" not in reader.fieldnames:
        raise GaiaTAPError("Gaia TAP 响应不是预期的 CSV（缺少 source_id 列）")
    rows: list[dict] = []
    for raw in reader:
        rows.append(normalize_gaia_row(raw, provenance="tap"))
    return rows


# ---------------------------------------------------------------------- fixture
FIXTURE_COLUMNS = [
    "field_id",
    "source_id",
    "ra",
    "dec",
    "pmra",
    "pmdec",
    "parallax",
    "ra_error",
    "dec_error",
    "pmra_error",
    "pmdec_error",
    "phot_g_mean_mag",
    "bp_rp",
    "ref_epoch",
    "in_qso_candidates",
    "in_galaxy_candidates",
    "non_single_star",
    "twomass_id",
    "wise_id",
]


def read_fixture_rows(
    path: str | Path,
    *,
    fields: Iterable[str] | None = None,
    provenance: str = "fixture",
) -> list[dict]:
    """读取真实 Gaia 行的小样本 CSV；`field_id` 列决定场归属。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Gaia fixture 不存在: {p}")
    wanted = set(fields) if fields else None
    out: list[dict] = []
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "source_id" not in reader.fieldnames:
            raise GaiaTAPError(f"fixture 不是预期 CSV: {p}")
        for raw in reader:
            fld = raw.get("field_id") or raw.get("field") or ""
            if wanted is not None and fld not in wanted:
                continue
            out.append(normalize_gaia_row(raw, field_id=fld or None, provenance=provenance))
    return out


def fixture_meta(path: str | Path) -> dict:
    p = Path(path)
    meta_path = p.with_suffix(".meta.json")
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_fixture(
    path: str | Path,
    rows: Iterable[dict],
    *,
    meta: dict,
) -> Path:
    """写一个可提交的小样本（真实 Gaia 行）+ 来源元数据。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIXTURE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "field_id": row.get("field_id", ""),
                    "source_id": row.get("catalog_id"),
                    "ra": row.get("ra"),
                    "dec": row.get("dec"),
                    "pmra": row.get("pmra"),
                    "pmdec": row.get("pmdec"),
                    "parallax": row.get("parallax"),
                    "ra_error": row.get("ra_error"),
                    "dec_error": row.get("dec_error"),
                    "pmra_error": row.get("pmra_error"),
                    "pmdec_error": row.get("pmdec_error"),
                    "phot_g_mean_mag": row.get("gmag"),
                    "bp_rp": row.get("bp_rp"),
                    "ref_epoch": row.get("ref_epoch", 2016.0),
                    "in_qso_candidates": row.get("in_qso_candidates", "false"),
                    "in_galaxy_candidates": row.get("in_galaxy_candidates", "false"),
                    "non_single_star": row.get("non_single_star", 0),
                    "twomass_id": row.get("twomass_id") or "",
                    "wise_id": row.get("wise_id") or "",
                }
            )
    meta_path = p.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def preset_field_specs(names: Iterable[str] | None = None) -> list[dict]:
    selected = list(names) if names else list(PRESET_FIELDS)
    out = []
    for name in selected:
        if name not in PRESET_FIELDS:
            raise KeyError(f"未知预设场: {name}")
        spec = dict(PRESET_FIELDS[name])
        spec["field_id"] = name
        out.append(spec)
    return out
