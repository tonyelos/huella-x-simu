"""Coverage DTO 校验（契约 v1，见 02 §5.1 / §5.2 / §6）。

Coverage 只描述“观测机会”，不得复用 app 的 L0–L3 检测就绪度。
"""

from __future__ import annotations

from . import problem

EXPECTED_RADIUS_DEG = 2.47


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_coverage(doc, label: str, expected_radius: float = EXPECTED_RADIUS_DEG) -> list[dict]:
    problems: list[dict] = []
    if not isinstance(doc, dict):
        return [problem("COVERAGE_PAYLOAD", "P0", f"{label}: 响应不是 JSON 对象")]

    radius = doc.get("radius_deg")
    if radius is None:
        # 有些实现把 radius 放在 query 里；只接受顶层 radius_deg，避免“默认保守但不说”。
        problems.append(problem("COVERAGE_RADIUS_MISSING", "P0", f"{label}: 缺 radius_deg（期望默认 {expected_radius}）"))
    elif not _number(radius):
        problems.append(problem("COVERAGE_RADIUS_TYPE", "P0", f"{label}: radius_deg 不是数字（{radius!r}）"))
    elif abs(float(radius) - float(expected_radius)) > 1e-9:
        problems.append(
            problem(
                "COVERAGE_RADIUS_VALUE",
                "P0",
                f"{label}: radius_deg={radius}，期望 {expected_radius}",
            )
        )

    level = doc.get("coverage_level")
    if not isinstance(level, str) or not level:
        problems.append(problem("COVERAGE_LEVEL_MISSING", "P0", f"{label}: 缺 coverage_level"))
    elif not level.startswith("C"):
        problems.append(
            problem(
                "COVERAGE_LEVEL_SEMANTICS",
                "P0",
                f"{label}: coverage_level={level!r}，必须以 C 开头（L0–L3 是 analysis_readiness）",
            )
        )

    baseline = doc.get("baseline_days")
    if not _number(baseline):
        problems.append(
            problem("COVERAGE_BASELINE", "P0", f"{label}: baseline_days 不是数字（{baseline!r}）")
        )

    if "readiness" in doc:
        problems.append(
            problem(
                "COVERAGE_READINESS_LEAK",
                "P0",
                f"{label}: coverage 不得返回 readiness（应使用 coverage_level）",
            )
        )
    return problems


def summary(doc) -> str:
    if not isinstance(doc, dict):
        return f"非对象（{type(doc).__name__}）"
    return (
        f"radius={doc.get('radius_deg')} level={doc.get('coverage_level')} "
        f"baseline={doc.get('baseline_days')} n_epochs={doc.get('n_epochs')}"
    )
