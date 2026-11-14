"""T1 验收工具的手写契约校验（只用 Python stdlib，不引入 jsonschema）。

每个校验函数返回一个 problem 列表：

    {"code": "TARGET_FIELD_MISSING", "severity": "P0", "message": "..."}

severity 语义：
- P0：契约硬失败，验收退出码 1。
- P1：不阻塞提交的契约偏差（可选组件/演示质量），但仍记为 FAIL。
"""

from __future__ import annotations

SEVERITY_RANK = {"P0": 0, "P1": 1}


def problem(code: str, severity: str, message: str, **extra) -> dict:
    if severity not in SEVERITY_RANK:
        raise ValueError(f"unknown severity: {severity}")
    out = {"code": code, "severity": severity, "message": message}
    out.update(extra)
    return out


def worst(problems: list[dict]) -> str | None:
    if not problems:
        return None
    return min((p["severity"] for p in problems), key=lambda s: SEVERITY_RANK[s])


def count_by_severity(problems: list[dict]) -> dict[str, int]:
    out = {"P0": 0, "P1": 0}
    for p in problems:
        out[p["severity"]] = out.get(p["severity"], 0) + 1
    return out
