#!/usr/bin/env python3
"""T3 离线演示冻结器 · verify

读取 manifest.json，逐文件重算 sha256，检查：
- 缺失 / 损坏 / 大小不符 / 符号链接；
- checksums.txt 与 manifest 完全一致（且能被 `shasum -a 256 -c` 校验）；
- manifest 内没有绝对路径、没有 .. 路径段；
- real 目标的 ok 历元数 >= 2；hybrid 目标允许 0，但必须有模拟输入；
- epochs/support/results 下是否有 manifest 未列出的“多余文件”；
- manifest.missing 与逐历元 status 是否自洽。

`--strict`：任何 missing/fetch_failed、real 目标不完整、或多余文件都判失败。
默认：缺失/状态类问题只 warning，哈希/清单不一致仍然失败。

退出码：0=通过；1=校验失败；2=快照/manifest 不可读。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="T3 离线快照校验")
    ap.add_argument("--snapshot", required=True, help="快照目录（含 manifest.json）")
    ap.add_argument("--strict", action="store_true", help="missing/fetch_failed/不完整 real 目标/多余文件也失败")
    ap.add_argument("--json", action="store_true", help="在 stdout 额外打印 JSON 报告")
    ap.add_argument("--json-out", default="", help="把 JSON 报告写到指定路径")
    return ap.parse_args(argv)


def human(result: dict, strict: bool) -> str:
    lines = []
    m = result.get("manifest", {})
    lines.append("T3 snapshot verify")
    lines.append(f"  snapshot : {result.get('snapshot')}")
    lines.append(
        f"  manifest : schema {m.get('schema_version')} · generated {m.get('generated_at')} · fov {m.get('fov')}"
    )
    f = result.get("files", {})
    lines.append(
        f"  files    : {f.get('ok', 0)}/{f.get('total', 0)} ok · {common.fmt_bytes(f.get('bytes', 0))}"
    )
    ts = result.get("targets", [])
    lines.append("  targets  : " + " · ".join(f"{t['id']}={t['status']}({t['n_epochs']})" for t in ts))
    cs = result.get("coords", [])
    if cs:
        lines.append(
            "  coords   : " + " · ".join(f"{c['ra']:.4f},{c['dec']:.4f}={c['status']}({c['n_epochs']})" for c in cs)
        )
    ck = result.get("checksums", {})
    lines.append(f"  checksums: {ck.get('entries', 0)} entries · {ck.get('status', '?')}")
    lines.append(f"  extras   : {len(result.get('extra_files', []))}")
    lines.append(f"  missing  : {len(result.get('missing', []))}")
    lines.append(f"  warnings : {len(result.get('warnings', []))}")
    if result.get("errors"):
        lines.append("  errors   :")
        for e in result["errors"]:
            lines.append(f"    - {e}")
    if result.get("warnings"):
        lines.append("  warning detail:")
        for w in result["warnings"][:20]:
            lines.append(f"    - {w}")
        if len(result["warnings"]) > 20:
            lines.append(f"    ... 另有 {len(result['warnings']) - 20} 条")
    lines.append(f"RESULT: {'PASS' if result.get('passed') else 'FAIL'} ({'strict' if strict else 'default'})")
    return "\n".join(lines)


def verify(snapshot: Path, strict: bool) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    snapshot = snapshot.resolve()
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file():
        raise common.SnapshotError(f"缺少 manifest.json：{snapshot}")
    try:
        manifest = common.read_json(manifest_path)
    except Exception as exc:  # noqa: BLE001
        raise common.SnapshotError(f"manifest.json 不是合法 JSON：{exc}") from exc
    if not isinstance(manifest, dict):
        raise common.SnapshotError("manifest.json 顶层必须是对象")

    schema = manifest.get("schema_version")
    if schema != common.SCHEMA_VERSION:
        raise common.SnapshotError(f"不支持的 schema_version：{schema!r}（期望 {common.SCHEMA_VERSION}）")
    if manifest.get("tool") not in (None, common.TOOL_ID):
        warnings.append(f"manifest.tool 不是 {common.TOOL_ID}: {manifest.get('tool')!r}")

    # 1) 路径安全
    for p in common.scan_manifest_paths(manifest):
        errors.append(f"manifest 路径问题：{p}")

    # 2) 文件哈希
    check = common.check_snapshot_files(snapshot, manifest)
    errors.extend(check["errors"])
    warnings.extend(check["warnings"])
    expected = common.expected_files(manifest)

    # 3) 多余文件
    managed = common.list_managed_files(snapshot)
    extra = sorted(managed - set(expected))
    if extra:
        msg = f"{len(extra)} 个 manifest 未列出的多余文件（例如 {extra[0]}）"
        (errors if strict else warnings).append(msg)

    # 4) checksums.txt
    ck_status = "ok"
    ck_entries = 0
    try:
        parsed = common.parse_checksums(snapshot / "checksums.txt")
        ck_entries = len(parsed)
        seen: dict[str, str] = {}
        dup = []
        for digest, rel in parsed:
            if rel in seen:
                dup.append(rel)
            seen[rel] = digest
        if dup:
            errors.append(f"checksums.txt 有重复条目：{sorted(set(dup))[:3]}")
        exp_set = {(m["sha256"], rel) for rel, m in expected.items() if m.get("sha256")}
        got_set = set(parsed)
        only_ck = sorted(got_set - exp_set)
        only_mf = sorted(exp_set - got_set)
        if only_ck:
            errors.append(f"checksums.txt 含 manifest 之外/哈希不符的条目：{only_ck[:3]}")
        if only_mf:
            errors.append(f"checksums.txt 缺少 manifest 条目：{[r for _h, r in only_mf][:3]}")
        if only_ck or only_mf or dup:
            ck_status = "mismatch"
    except common.SnapshotError as exc:
        errors.append(str(exc))
        ck_status = "error"

    # 5) 目标/坐标结构
    targets = manifest.get("targets") or []
    if not isinstance(targets, list) or not targets:
        errors.append("manifest.targets 为空或不是列表")
    seen_ids = set()
    target_out = []
    for t in targets:
        if not isinstance(t, dict):
            errors.append("manifest.targets 含非对象条目")
            continue
        tid = str(t.get("id", ""))
        if not tid:
            errors.append("target 缺少 id")
        if tid in seen_ids:
            errors.append(f"target id 重复：{tid}")
        seen_ids.add(tid)
        mode = t.get("mode") or t.get("mode_expected")
        status = t.get("status")
        epochs = t.get("epochs") or []
        ok_epochs = [e for e in epochs if isinstance(e, dict) and e.get("status") == "ok"]
        n_ok = len(ok_epochs)
        if status not in common.EPOCH_STATUSES:
            errors.append(f"{tid}: 非法 target.status {status!r}")
        for e in epochs:
            if not isinstance(e, dict):
                errors.append(f"{tid}: epochs 含非对象条目")
                continue
            if e.get("status") not in common.EPOCH_STATUSES:
                errors.append(f"{tid}: 非法 epoch.status {e.get('status')!r}")
            if e.get("status") == "ok":
                if not e.get("file"):
                    errors.append(f"{tid}: status=ok 的历元缺 file")
                if not common.HEX64_RE.match(str(e.get("sha256") or "")):
                    errors.append(f"{tid}: status=ok 的历元 sha256 非法")
                if not isinstance(e.get("bytes"), int) or e.get("bytes", 0) <= 0:
                    errors.append(f"{tid}: status=ok 的历元 bytes 非法")
        if mode == "real":
            if status == "ok" and n_ok < 2:
                errors.append(f"{tid}: real 目标 status=ok 但 ok 历元 {n_ok} < 2")
            if status != "ok":
                msg = f"{tid}: real 目标不完整（status={status}）"
                (errors if strict else warnings).append(msg)
        elif mode in ("hybrid", "synthetic"):
            if n_ok:
                warnings.append(f"{tid}: {mode} 目标却有 {n_ok} 个 ok 像素历元（可能被误标）")
            sim = t.get("simulation") or {}
            times = sim.get("times_mjd") or []
            if not sim or not times:
                errors.append(f"{tid}: {mode} 目标缺 simulation.times_mjd（无法复现模拟时序）")
            elif len(times) != int(sim.get("n_epochs", len(times))):
                errors.append(f"{tid}: simulation.n_epochs 与 times_mjd 长度不一致")
            elif int(sim.get("n_epochs", 0)) < 2:
                errors.append(f"{tid}: {mode} 目标模拟历元 < 2")
        target_out.append({"id": tid, "mode": mode, "status": status, "n_epochs": n_ok})

    coords = manifest.get("coords") or []
    coord_out = []
    for c in coords:
        if not isinstance(c, dict):
            errors.append("manifest.coords 含非对象条目")
            continue
        status = c.get("status")
        n_ok = sum(1 for e in (c.get("epochs") or []) if isinstance(e, dict) and e.get("status") == "ok")
        if status not in common.EPOCH_STATUSES:
            errors.append(f"coord {c.get('ra')},{c.get('dec')}: 非法 status {status!r}")
        if status == "ok" and n_ok < 2:
            errors.append(f"coord {c.get('ra')},{c.get('dec')}: status=ok 但 ok 历元 {n_ok} < 2")
        if status in ("missing", "fetch_failed"):
            msg = f"coord {c.get('ra')},{c.get('dec')}: 状态 {status}"
            (errors if strict else warnings).append(msg)
        elif status == "no_coverage":
            warnings.append(f"coord {c.get('ra')},{c.get('dec')}: no_coverage（未冻结像素）")
        if c.get("resolver") == "fallback":
            warnings.append(
                f"coord {c.get('ra')},{c.get('dec')}: resolver=fallback（按最近目标 {c.get('matched_target')}）"
            )
        coord_out.append(
            {
                "ra": c.get("ra"),
                "dec": c.get("dec"),
                "status": status,
                "n_epochs": n_ok,
                "matched_target": c.get("matched_target"),
                "resolver": c.get("resolver"),
            }
        )

    # 6) missing[] 与逐历元 status 自洽（只看 targets；coords 复用文件不重复记账）
    status_missing = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        for e in t.get("epochs") or []:
            if isinstance(e, dict) and e.get("status") in ("missing", "fetch_failed"):
                status_missing.append(
                    (
                        f"target:{t.get('id')}",
                        str(e.get("obsid", "")),
                        str(e.get("detector", "")),
                        e.get("status"),
                    )
                )
    manifest_missing = []
    for m in manifest.get("missing") or []:
        if not isinstance(m, dict):
            errors.append("manifest.missing 含非对象条目")
            continue
        manifest_missing.append(
            (
                str(m.get("owner")),
                str(m.get("obsid", "")),
                str(m.get("detector", "")),
                str(m.get("status")),
            )
        )
    if sorted(status_missing) != sorted(manifest_missing):
        errors.append(
            "manifest.missing 与逐历元 missing/fetch_failed 不一致"
            f"（manifest {len(manifest_missing)} 条，逐历元 {len(status_missing)} 条）"
        )
    if manifest_missing and strict:
        errors.append(f"strict：manifest.missing 非空（{len(manifest_missing)} 条）")
    elif manifest_missing:
        warnings.append(f"manifest.missing 非空（{len(manifest_missing)} 条；--strict 会失败）")

    # 7) summary 自洽
    summary = manifest.get("summary") or {}
    files = expected
    epoch_rels = [r for r, m in files.items() if m["role"] == "epoch"]
    support_rels = [r for r, m in files.items() if m["role"] == "support"]
    result_rels = [r for r, m in files.items() if m["role"] == "results"]
    calc = {
        "n_epoch_files": len(epoch_rels),
        "n_support_files": len(support_rels),
        "n_result_files": len(result_rels),
        "n_files_total": len(files),
        "total_bytes": sum((m.get("bytes") or 0) for m in files.values()),
        "n_missing": len(manifest_missing),
    }
    for key, val in calc.items():
        if key in summary and summary[key] != val:
            errors.append(f"manifest.summary.{key}={summary[key]} 与实际 {val} 不一致")
        elif key not in summary:
            warnings.append(f"manifest.summary 缺 {key}（旧版 manifest？）")

    # 8) 统计
    checked_files = check["files"]
    n_ok_files = sum(1 for v in checked_files.values() if v.get("status") == "ok")
    n_missing_files = sum(1 for v in checked_files.values() if v.get("status") == "missing")
    n_bad_files = sum(1 for v in checked_files.values() if v.get("status") == "mismatch")
    passed = not errors

    return {
        "passed": passed,
        "strict": strict,
        "snapshot": str(snapshot),
        "checked_at": common.utc_now_iso(),
        "manifest": {
            "schema_version": schema,
            "generated_at": manifest.get("generated_at"),
            "fov": manifest.get("fov"),
            "app_fingerprint": (manifest.get("app_fingerprint") or {}).get("sha256"),
        },
        "files": {
            "total": check["total_files"],
            "ok": n_ok_files,
            "missing": n_missing_files,
            "mismatch": n_bad_files,
            "bytes": check["total_bytes"],
            "details": [
                {"file": k, "status": v.get("status"), "sha256": v.get("actual_sha256") or v.get("sha256")}
                for k, v in sorted(checked_files.items())
            ],
        },
        "targets": target_out,
        "coords": coord_out,
        "checksums": {"entries": ck_entries, "status": ck_status},
        "extra_files": extra,
        "missing": manifest_missing,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv=None):
    args = parse_args(argv)
    try:
        result = verify(Path(args.snapshot), args.strict)
    except common.SnapshotError as exc:
        print(f"[verify] ERROR: {exc}", file=sys.stderr)
        return 2
    print(human(result, args.strict))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if args.json_out:
        common.write_json_atomic(Path(args.json_out), result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
