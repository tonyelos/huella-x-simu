#!/usr/bin/env python3
"""官方 5 个实例的只读基准（不写 DB，不改 targets.json）。

用法：
    python3 项目主控/验收/benchmark_official.py \
        --base-url http://127.0.0.1:8790 \
        --out 项目主控/验收/官方案例基准_report.md

判定：
- PASS：与官方预期一致，且没有超数据能力；
- DATA_LIMITED：数据不足，但明确 U/insufficient，不伪造；
- FAIL：与预期冲突且不是数据限制。
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

CASES = [
    {
        "id": "barnard",
        "name": "Barnard's Star",
        "fov": 0.2,
        "expected_class": "L",
        "expected_identity": "known_star",
        "note": "高自行；SPHEREx 4 期可能测不出，需星表 PM 先验",
    },
    {
        "id": "epsind_b",
        "name": "Epsilon Indi B",
        "fov": 0.3,
        "expected_class": "L",
        "expected_identity": "ambiguous",
        "note": "RUWE>2；必须是 ambiguous",
    },
    {
        "id": "wise0855",
        "name": "WISE 0855",
        "fov": 0.3,
        "expected_class": "L",
        "expected_identity": "insufficient_data",
        "note": "极暗/历元少；允许 DATA_LIMITED",
    },
    {
        "id": "rw_aur",
        "name": "RW Aurigae",
        "fov": 0.2,
        "expected_class": "V",
        "expected_identity": "ambiguous",
        "note": "变光 + 双源；identity ambiguous",
    },
    {
        "id": "r_dra",
        "name": "R Draconis",
        "fov": 0.2,
        "expected_class": "V",
        "expected_identity": "known_star",
        "note": "Mira 变星；饱和",
    },
]


def analyze(base: str, target_id: str, fov: float, timeout: int = 240) -> dict:
    body = json.dumps({"target_id": target_id, "fov": fov, "mode": "auto"}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/analyze",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def candidate_ruwe(ident: dict) -> list:
    out = []
    for c in ident.get("candidates") or []:
        out.append({"source_id": c.get("source_id"), "ruwe": c.get("ruwe")})
    return out


def verdict(case: dict, payload: dict) -> tuple[str, str]:
    res = payload.get("result") or {}
    data = payload.get("data") or {}
    ident = payload.get("identification") or {}
    actual = str(res.get("class") or "")
    expected = case["expected_class"]
    identity = str(ident.get("status") or "")
    n = int(data.get("n_epochs") or 0)
    detected = int(res.get("detected_epochs") or 0)
    readiness = str(data.get("readiness") or res.get("readiness") or "")

    class_ok = actual == expected or (expected == "L" and actual.startswith("L"))
    if expected == "L" and actual == "U" and (detected <= 1 or readiness == "L0"):
        class_state = "DATA_LIMITED"
    elif class_ok:
        class_state = "PASS"
    else:
        class_state = "FAIL"

    id_ok = identity == case["expected_identity"]
    if not id_ok and actual == "U" and identity in ("insufficient_data", "catalog_unavailable"):
        id_ok = True
    id_state = "PASS" if id_ok else "FAIL"

    if class_state == "FAIL" or id_state == "FAIL":
        state = "FAIL"
    elif class_state == "DATA_LIMITED":
        state = "DATA_LIMITED"
    else:
        state = "PASS"

    bits = [
        f"class {actual or '?'} vs {expected}",
        f"identity {identity or '?'} vs {case['expected_identity']}",
        f"n_epochs={n} readiness={readiness}",
    ]
    if res.get("catalog_motion"):
        bits.append(f"catalog_prior={res['catalog_motion'].get('basis')}")
    if res.get("quality"):
        bits.append("quality=" + ",".join(map(str, res["quality"])))
    return state, "; ".join(bits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8790")
    ap.add_argument("--out", default="项目主控/验收/官方案例基准_report.md")
    args = ap.parse_args()

    rows = []
    for case in CASES:
        try:
            payload = analyze(args.base_url, case["id"], case["fov"])
            state, detail = verdict(case, payload)
            res = payload.get("result") or {}
            data = payload.get("data") or {}
            ident = payload.get("identification") or {}
            rows.append({
                "id": case["id"],
                "name": case["name"],
                "state": state,
                "expected_class": case["expected_class"],
                "actual_class": res.get("class"),
                "expected_identity": case["expected_identity"],
                "actual_identity": ident.get("status"),
                "n_epochs": data.get("n_epochs"),
                "readiness": data.get("readiness"),
                "ruwe": candidate_ruwe(ident),
                "catalog_motion": res.get("catalog_motion"),
                "quality": res.get("quality"),
                "detail": detail,
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "id": case["id"], "name": case["name"], "state": "FAIL",
                "expected_class": case["expected_class"], "actual_class": None,
                "expected_identity": case["expected_identity"], "actual_identity": None,
                "n_epochs": None, "readiness": None, "ruwe": [],
                "catalog_motion": None, "quality": None,
                "detail": "", "error": f"{type(exc).__name__}: {exc}",
            })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 官方 5 个实例基准报告",
        "",
        "> 只读基准：不写 DB、不改 targets.json；用于机制回归，不是“标记/确认”。",
        "",
        "| 目标 | 状态 | expected | actual | n | readiness | identity | RUWE | 说明 |",
        "|---|---|---|---|---:|---|---|---|---|",
    ]
    for r in rows:
        ruwe = ", ".join(f"{x['source_id']}={x['ruwe']}" for x in r["ruwe"] or []) or "—"
        lines.append(
            f"| {r['name']} | **{r['state']}** | {r['expected_class']} | {r['actual_class'] or '—'} | "
            f"{r['n_epochs'] if r['n_epochs'] is not None else '—'} | {r['readiness'] or '—'} | "
            f"{r['actual_identity'] or '—'} | {ruwe} | {r['detail'] or r['error'] or ''} |"
        )
    lines += [
        "",
        "## 状态定义",
        "",
        "- **PASS**：与官方预期一致，且没有超出数据能力；",
        "- **DATA_LIMITED**：数据不足，但明确 U/insufficient，不伪造；",
        "- **FAIL**：与预期冲突且不是数据限制。",
        "",
        "## 原始 JSON",
        "",
        "```json",
        json.dumps(rows, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
    states = {}
    for r in rows:
        states[r["state"]] = states.get(r["state"], 0) + 1
    print(json.dumps(states, ensure_ascii=False))
    return 0 if not states.get("FAIL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
