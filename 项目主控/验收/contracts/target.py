"""Target DTO 校验（契约 v1，见 项目主控/02_架构设计与集成契约.md §5.1）。"""

from __future__ import annotations

from . import problem

# 7 个目标（5 官方 + 2 演示）
EXPECTED_IDS = ("barnard", "epsind_b", "wise0855", "rw_aur", "r_dra", "m101", "campo_a")
OFFICIAL_IDS = ("barnard", "epsind_b", "wise0855", "rw_aur", "r_dra")
REQUIRED_FIELDS = ("id", "name", "ra", "dec", "official", "expected", "coord_unc_arcsec", "truth")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_payload(payload, source: str) -> dict:
    """校验一个目标列表载荷。

    返回 {"ok": bool, "problems": [...], "targets": [...], "by_id": {...}, "count": int}
    """
    problems: list[dict] = []
    if not isinstance(payload, dict):
        problems.append(problem("TARGETS_PAYLOAD", "P0", f"{source}: 响应不是 JSON 对象"))
        return {"ok": False, "problems": problems, "targets": [], "by_id": {}, "count": 0}

    targets = payload.get("targets")
    if not isinstance(targets, list):
        problems.append(problem("TARGETS_NOT_LIST", "P0", f"{source}: 缺少 targets 数组"))
        return {"ok": False, "problems": problems, "targets": [], "by_id": {}, "count": 0}

    if len(targets) != len(EXPECTED_IDS):
        problems.append(
            problem(
                "TARGETS_COUNT",
                "P0",
                f"{source}: 目标数 {len(targets)}，期望 {len(EXPECTED_IDS)}",
            )
        )

    by_id: dict[str, dict] = {}
    for idx, t in enumerate(targets):
        if not isinstance(t, dict):
            problems.append(problem("TARGET_NOT_OBJECT", "P0", f"{source}: 第 {idx} 条不是对象"))
            continue
        tid = t.get("id")
        label = tid if isinstance(tid, str) and tid else f"#{idx}"
        if isinstance(tid, str) and tid:
            if tid in by_id:
                problems.append(problem("TARGET_DUPLICATE_ID", "P0", f"{source}: 目标 id 重复 {tid}"))
            by_id[tid] = t
        else:
            problems.append(problem("TARGET_ID_MISSING", "P0", f"{source}: 第 {idx} 条缺 id"))

        for field in REQUIRED_FIELDS:
            if field not in t:
                severity = "P1" if field == "coord_unc_arcsec" else "P0"
                problems.append(
                    problem(
                        "TARGET_FIELD_MISSING",
                        severity,
                        f"{source}/{label}: 缺字段 {field}",
                        target=label,
                        field=field,
                    )
                )

        # coord_unc_arcsec：必须是非空正数（缺失是 P1，0/None 是 P1）
        unc = t.get("coord_unc_arcsec")
        if unc is None or unc == "" or not _number(unc) or float(unc) <= 0:
            problems.append(
                problem(
                    "TARGET_COORD_UNC",
                    "P1",
                    f"{source}/{label}: coord_unc_arcsec 不是正数（实际 {unc!r}）",
                    target=label,
                )
            )

        ra, dec = t.get("ra"), t.get("dec")
        if not _number(ra) or not (0.0 <= float(ra) < 360.0):
            problems.append(problem("TARGET_RA", "P0", f"{source}/{label}: ra 非法（{ra!r}）", target=label))
        if not _number(dec) or not (-90.0 <= float(dec) <= 90.0):
            problems.append(problem("TARGET_DEC", "P0", f"{source}/{label}: dec 非法（{dec!r}）", target=label))
        if not isinstance(t.get("official"), bool):
            problems.append(
                problem("TARGET_OFFICIAL", "P0", f"{source}/{label}: official 不是 bool", target=label)
            )
        if not isinstance(t.get("truth"), dict):
            problems.append(
                problem("TARGET_TRUTH", "P0", f"{source}/{label}: truth 不是对象", target=label)
            )
        if not isinstance(t.get("name"), str) or not t.get("name"):
            problems.append(problem("TARGET_NAME", "P0", f"{source}/{label}: name 为空", target=label))
        if not isinstance(t.get("expected"), str) or not t.get("expected"):
            problems.append(
                problem("TARGET_EXPECTED", "P0", f"{source}/{label}: expected 为空", target=label)
            )

    for tid in EXPECTED_IDS:
        if tid not in by_id:
            problems.append(problem("TARGET_MISSING", "P0", f"{source}: 缺目标 {tid}", target=tid))
    for tid in OFFICIAL_IDS:
        t = by_id.get(tid)
        if t is not None and t.get("official") is not True:
            problems.append(problem("TARGET_NOT_OFFICIAL", "P0", f"{source}/{tid}: official 应为 true", target=tid))

    return {
        "ok": not problems,
        "problems": problems,
        "targets": targets,
        "by_id": by_id,
        "count": len(targets),
    }


def compare(base: dict[str, dict], other: dict[str, dict], source_a: str, source_b: str, severity: str = "P0") -> list[dict]:
    """逐字段比较两份目标表（只比较契约字段）。"""
    problems: list[dict] = []
    for tid in EXPECTED_IDS:
        a, b = base.get(tid), other.get(tid)
        if a is None or b is None:
            problems.append(
                problem(
                    "TARGET_PARITY_MISSING",
                    "P0",
                    f"{source_a} / {source_b}: 缺目标 {tid}",
                    target=tid,
                )
            )
            continue
        for field in REQUIRED_FIELDS:
            if a.get(field) != b.get(field):
                problems.append(
                    problem(
                        "TARGET_FIELD_MISMATCH",
                        severity,
                        f"{tid}.{field}: {source_a}={a.get(field)!r} vs {source_b}={b.get(field)!r}",
                        target=tid,
                        field=field,
                    )
                )
    return problems
