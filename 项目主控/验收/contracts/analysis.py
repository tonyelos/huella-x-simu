"""AnalysisResult DTO 校验（契约 v1，见 02 §5.1 / §7 / §11）。

分成三层，避免不同用例组互相“连坐”：
- shape_problems：C 组（5 目标 HTTP）需要的 mode/provenance/class/readiness 形状与自洽性；
- contract_problems：A 组（契约）需要的 schema_version / readiness_scope / fallback_reason / real_epochs；
- real_epochs_problems：T2 之后才必须存在的 data.real_epochs（当前按 P1 记录）。
"""

from __future__ import annotations

from . import problem

VALID_MODES = {"real", "hybrid", "synthetic", "sample", "unavailable"}
CLASSES = {"S", "V", "L", "A", "P", "O", "U"}
READINESS = {"L0", "L1", "L2", "L3"}
FALLBACK_REASONS = {
    None,
    "DB_UNAVAILABLE",
    "NO_LOCAL_COVERAGE",
    "INSUFFICIENT_EPOCHS",
    "BASELINE_TOO_SHORT",
    "FETCH_FAILED",
    "TARGET_TOO_FAINT",
}


def _parts(doc):
    if not isinstance(doc, dict):
        return None, None
    return doc.get("data"), doc.get("result")


def shape_problems(doc, label: str) -> list[dict]:
    problems: list[dict] = []
    data, result = _parts(doc)
    if not isinstance(data, dict):
        return [problem("ANALYSIS_DATA", "P0", f"{label}: 缺 data 对象")]
    if not isinstance(result, dict):
        return [problem("ANALYSIS_RESULT", "P0", f"{label}: 缺 result 对象")]

    mode = data.get("mode")
    if mode not in VALID_MODES:
        problems.append(problem("ANALYSIS_MODE", "P0", f"{label}: data.mode 非法（{mode!r}）"))

    provenance = data.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        problems.append(
            problem("ANALYSIS_PROVENANCE", "P0", f"{label}: data.provenance 必须是非空数组（{provenance!r}）")
        )
    elif any(p not in {"real", "hybrid", "synthetic", "unknown"} for p in provenance):
        problems.append(
            problem("ANALYSIS_PROVENANCE_VALUE", "P0", f"{label}: data.provenance 含非法值 {provenance!r}")
        )

    if "fallback_reason" not in data:
        problems.append(problem("ANALYSIS_FALLBACK_MISSING", "P0", f"{label}: 缺 data.fallback_reason（允许 null）"))
    elif data.get("fallback_reason") not in FALLBACK_REASONS:
        problems.append(
            problem(
                "ANALYSIS_FALLBACK_VALUE",
                "P1",
                f"{label}: fallback_reason={data.get('fallback_reason')!r} 不在契约枚举内",
            )
        )

    n_epochs = data.get("n_epochs")
    if not isinstance(n_epochs, int) or isinstance(n_epochs, bool) or n_epochs < 0:
        problems.append(problem("ANALYSIS_N_EPOCHS", "P0", f"{label}: data.n_epochs 非法（{n_epochs!r}）"))

    real_epochs = data.get("real_epochs")
    if real_epochs is not None:
        if not isinstance(real_epochs, int) or isinstance(real_epochs, bool) or real_epochs < 0:
            problems.append(
                problem("ANALYSIS_REAL_EPOCHS_VALUE", "P0", f"{label}: data.real_epochs 非法（{real_epochs!r}）")
            )
        elif isinstance(n_epochs, int) and real_epochs > n_epochs:
            problems.append(
                problem(
                    "ANALYSIS_REAL_EPOCHS_RANGE",
                    "P0",
                    f"{label}: data.real_epochs={real_epochs} > n_epochs={n_epochs}",
                )
            )

    cls = result.get("class")
    if not isinstance(cls, str) or cls not in CLASSES:
        problems.append(problem("ANALYSIS_CLASS", "P0", f"{label}: result.class 非法（{cls!r}）"))
    readiness = result.get("readiness")
    if not isinstance(readiness, str) or readiness not in READINESS:
        problems.append(
            problem("ANALYSIS_READINESS", "P0", f"{label}: result.readiness 非法（{readiness!r}）")
        )

    # mode 与 provenance 自洽（02 §11）
    if mode == "real":
        if isinstance(provenance, list) and any(p != "real" for p in provenance):
            problems.append(
                problem(
                    "ANALYSIS_REAL_PROVENANCE",
                    "P0",
                    f"{label}: mode=real 但 provenance={provenance!r}（不允许 synthetic 历元）",
                )
            )
        if data.get("fallback_reason") is not None:
            problems.append(
                problem(
                    "ANALYSIS_REAL_FALLBACK",
                    "P0",
                    f"{label}: mode=real 但 fallback_reason={data.get('fallback_reason')!r}",
                )
            )
    elif mode == "synthetic" and data.get("fallback_reason") is None:
        # 用户主动选 synthetic 时允许 null；本工具只在 auto 路径上报 P1。
        problems.append(
            problem("ANALYSIS_SYNTHETIC_FALLBACK", "P1", f"{label}: mode=synthetic 但没有 fallback_reason")
        )
    return problems


def contract_problems(doc, label: str) -> list[dict]:
    """A 组使用的完整 AnalysisResult 契约。"""
    problems = shape_problems(doc, label)
    data, result = _parts(doc)
    if not isinstance(data, dict) or not isinstance(result, dict):
        return problems

    schema_version = doc.get("schema_version")
    if schema_version is None:
        problems.append(problem("ANALYSIS_SCHEMA_VERSION_MISSING", "P0", f"{label}: 缺顶层 schema_version"))
    elif schema_version != "1.0":
        problems.append(
            problem("ANALYSIS_SCHEMA_VERSION", "P0", f"{label}: schema_version={schema_version!r}，期望 '1.0'")
        )

    scope = result.get("readiness_scope")
    if scope is None:
        problems.append(
            problem("ANALYSIS_READINESS_SCOPE_MISSING", "P0", f"{label}: 缺 result.readiness_scope")
        )
    elif scope != "analysis":
        problems.append(
            problem("ANALYSIS_READINESS_SCOPE", "P0", f"{label}: readiness_scope={scope!r}，期望 'analysis'")
        )
    return problems


def real_epochs_problems(doc, label: str) -> list[dict]:
    data, _ = _parts(doc)
    if not isinstance(data, dict) or "real_epochs" not in data:
        return [
            problem(
                "ANALYSIS_REAL_EPOCHS_MISSING",
                "P1",
                f"{label}: 缺 data.real_epochs（T2 数据桥接完成后必须存在）",
            )
        ]
    return []


def summary(doc) -> dict:
    data, result = _parts(doc)
    if not isinstance(data, dict) or not isinstance(result, dict):
        return {}
    return {
        "mode": data.get("mode"),
        "n_epochs": data.get("n_epochs"),
        "real_epochs": data.get("real_epochs"),
        "provenance": data.get("provenance"),
        "fallback_reason": data.get("fallback_reason"),
        "readiness": result.get("readiness"),
        "class": result.get("class"),
        "mu_mas_yr": result.get("mu_mas_yr"),
        "readiness_scope": result.get("readiness_scope"),
        "schema_version": doc.get("schema_version") if isinstance(doc, dict) else None,
    }
