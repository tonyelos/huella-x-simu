"""批量识别审计导出：CSV（候选展开）/ JSON（保留嵌套 candidates）。"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone

BASE_COLUMNS = [
    "source_id", "epoch_id", "frame", "ra", "dec", "mjd",
    "status", "label_zh", "catalog_id", "release", "separation_arcsec",
    "ruwe", "n_candidates", "jmag", "hmag", "kmag", "w1", "w2",
    "reason", "quality", "caveats",
]
CANDIDATE_COLUMNS = [
    "candidate_index", "candidate_catalog", "candidate_id",
    "candidate_sep_arcsec", "candidate_ruwe",
    "candidate_jmag", "candidate_hmag", "candidate_kmag",
    "candidate_w1", "candidate_w2",
]
COLUMNS = BASE_COLUMNS + CANDIDATE_COLUMNS


def _finite(v):
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _join(items):
    return ";".join(str(x) for x in (items or []) if x is not None and str(x) != "")


def _value(v):
    return "" if v is None else v


def _candidate_row(src, result, cand, index):
    phot = result.get("photometry") or {}
    cphot = cand or {}
    status = result.get("status") or ""
    ambiguous = status == "ambiguous" or int(result.get("n_candidates") or 0) > 1
    base_cid = None if ambiguous or status == "uncatalogued_candidate" else (result.get("catalog_id") or None)
    frame = src.get("frame")
    if frame is None:
        frame = ""
    return {
        "source_id": result.get("source_id") or src.get("source_id") or "",
        "epoch_id": src.get("epoch_id") or "",
        "frame": frame,
        "ra": _finite(src.get("ra")),
        "dec": _finite(src.get("dec")),
        "mjd": _finite(src.get("mjd")) if src.get("mjd") is not None else _finite((result.get("input") or {}).get("mjd")),
        "status": status,
        "label_zh": result.get("label_zh") or ("未收录候选（未经确认）" if status == "uncatalogued_candidate" else ""),
        "catalog_id": base_cid,
        "release": result.get("release") or result.get("catalog_release") or "",
        "separation_arcsec": _finite(result.get("separation_arcsec")),
        "ruwe": None if ambiguous else _finite(result.get("ruwe")),
        "n_candidates": result.get("n_candidates") if result.get("n_candidates") is not None else 0,
        "jmag": _finite(phot.get("jmag")),
        "hmag": _finite(phot.get("hmag")),
        "kmag": _finite(phot.get("kmag")),
        "w1": _finite(phot.get("w1")),
        "w2": _finite(phot.get("w2")),
        "reason": result.get("reason") or "",
        "quality": _join(result.get("quality")),
        "caveats": _join(result.get("caveats")),
        "candidate_index": index,
        "candidate_catalog": cphot.get("catalog") or "",
        "candidate_id": cphot.get("catalog_id") or "",
        "candidate_sep_arcsec": _finite(cphot.get("separation_arcsec")),
        "candidate_ruwe": _finite(cphot.get("ruwe")),
        "candidate_jmag": _finite(cphot.get("jmag")),
        "candidate_hmag": _finite(cphot.get("hmag")),
        "candidate_kmag": _finite(cphot.get("kmag")),
        "candidate_w1": _finite(cphot.get("w1")),
        "candidate_w2": _finite(cphot.get("w2")),
    }


def build_rows(sources, results):
    """每源按候选展开；每个源至少一行（无候选时 candidate 列留空）。"""
    rows = []
    for i, result in enumerate(results or []):
        src = sources[i] if i < len(sources or []) else {}
        candidates = result.get("candidates") or []
        if not candidates:
            rows.append(_candidate_row(src, result, {}, None))
            continue
        for j, cand in enumerate(candidates):
            rows.append(_candidate_row(src, result, cand, j))
    return rows


def to_csv(sources, results) -> str:
    rows = build_rows(sources, results)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _value(row.get(k)) for k in COLUMNS})
    # UTF-8 BOM for Excel.
    return "\ufeff" + out.getvalue()


def json_payload(sources, results, *, kind="batch", counts=None, catalog_status=None, disclaimer=None) -> dict:
    rows = build_rows(sources, results)
    by_id = {}
    for result in results or []:
        sid = str(result.get("source_id") or result.get("detection_id") or "")
        by_id[sid] = result
    return {
        "schema_version": "1.0",
        "kind": kind,
        "n_sources": len(sources or []),
        "n_rows": len(rows),
        "counts": counts or {},
        "catalog_status": catalog_status,
        "sources": list(sources or []),
        "results": list(results or []),
        "byId": by_id,
        "discovery_claim": False,
        "disclaimer": disclaimer or "未匹配不等于新天体；本工具只做已知/未知比对，不做发现声明。",
    }


def to_json(sources, results, *, kind="batch", counts=None, catalog_status=None, disclaimer=None) -> str:
    return json.dumps(
        json_payload(sources, results, kind=kind, counts=counts, catalog_status=catalog_status, disclaimer=disclaimer),
        ensure_ascii=False,
        indent=2,
        default=float,
    )


def filename(kind="batch", target="target", fmt="csv", now=None) -> str:
    k = str(kind or "batch").upper()
    t = str(target or "target")
    t = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in t).strip("_") or "target"
    d = now or datetime.now(timezone.utc)
    stamp = d.strftime("%Y%m%dT%H%M%SZ")
    return f"movescope_{k}_batch_{t}_{stamp}.{fmt}"
