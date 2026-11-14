"""EpochRef DTO 校验（契约 v1，见 02 §5.1 / §5.2）。"""

from __future__ import annotations

from . import problem

# 02 §5.1 的十字段 + provenance / frame_id。
FIELDS = (
    "obsid",
    "detector",
    "release",
    "week",
    "version",
    "mjd",
    "date_obs",
    "sep_deg",
    "key",
    "slice_url",
    "provenance",
    "frame_id",
)
# 必须非空的字段（frame_id 允许 null，但键必须存在）。
NONEMPTY = FIELDS[:-1]
PROVENANCE = {"real", "hybrid", "synthetic", "unknown"}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_epochs(epochs, label: str) -> list[dict]:
    problems: list[dict] = []
    if not isinstance(epochs, list):
        return [problem("EPOCHS_NOT_LIST", "P0", f"{label}: epochs 不是数组")]

    seen: dict[tuple, int] = {}
    for idx, e in enumerate(epochs):
        if not isinstance(e, dict):
            problems.append(problem("EPOCH_NOT_OBJECT", "P0", f"{label}: 第 {idx} 条不是对象"))
            continue
        for field in FIELDS:
            if field not in e:
                problems.append(
                    problem("EPOCH_FIELD_MISSING", "P0", f"{label}[{idx}]: 缺字段 {field}", field=field)
                )
        for field in NONEMPTY:
            value = e.get(field)
            if value is None or value == "":
                problems.append(
                    problem(
                        "EPOCH_FIELD_EMPTY",
                        "P0",
                        f"{label}[{idx}]: {field} 为空（{value!r}）",
                        field=field,
                    )
                )
        if not isinstance(e.get("detector"), int) or isinstance(e.get("detector"), bool):
            problems.append(
                problem("EPOCH_DETECTOR", "P0", f"{label}[{idx}]: detector 不是 int（{e.get('detector')!r}）")
            )
        for field in ("mjd", "sep_deg"):
            if not _number(e.get(field)):
                problems.append(
                    problem("EPOCH_NUMBER", "P0", f"{label}[{idx}]: {field} 不是数字（{e.get(field)!r}）")
                )
        if e.get("provenance") not in PROVENANCE:
            problems.append(
                problem(
                    "EPOCH_PROVENANCE",
                    "P0",
                    f"{label}[{idx}]: provenance 非法（{e.get('provenance')!r}）",
                )
            )
        key = (e.get("obsid"), e.get("detector"))
        if key in seen:
            problems.append(
                problem(
                    "EPOCH_DUPLICATE",
                    "P0",
                    f"{label}[{idx}]: (obsid,detector) 重复 {key}，首次见第 {seen[key]} 条",
                )
            )
        else:
            seen[key] = idx

    # 排序：mjd 升序，再 detector 升序（02 §5.1）
    order = [(e.get("mjd"), e.get("detector")) for e in epochs if isinstance(e, dict)]
    numeric = [(float(m), int(d)) for m, d in order if _number(m) and isinstance(d, int) and not isinstance(d, bool)]
    if len(numeric) == len(order) and numeric != sorted(numeric):
        problems.append(problem("EPOCH_ORDER", "P1", f"{label}: epochs 未按 (mjd, detector) 升序"))
    return problems


def _row(e: dict) -> dict:
    return {field: e.get(field) for field in FIELDS}


def compare(groups: dict[str, list], labels: dict[str, str]) -> list[dict]:
    """逐字段比较两组 EpochRef（同一组历元必须同形、同序）。"""
    problems: list[dict] = []
    names = list(groups)
    if len(names) < 2:
        return problems
    a_name, b_name = names[0], names[1]
    a, b = groups[a_name], groups[b_name]
    la, lb = labels.get(a_name, a_name), labels.get(b_name, b_name)
    if len(a) != len(b):
        problems.append(
            problem("EPOCH_PARITY_COUNT", "P0", f"{la} {len(a)} 条 vs {lb} {len(b)} 条")
        )
    for idx in range(min(len(a), len(b))):
        pa, pb = _row(a[idx]), _row(b[idx])
        if pa != pb:
            diff = {k: (pa.get(k), pb.get(k)) for k in FIELDS if pa.get(k) != pb.get(k)}
            problems.append(
                problem(
                    "EPOCH_PARITY_FIELD",
                    "P0",
                    f"第 {idx} 条字段不一致: {diff}",
                    index=idx,
                )
            )
    return problems


def summary(epochs) -> str:
    if not isinstance(epochs, list):
        return f"非数组（{type(epochs).__name__}）"
    dates = [e.get("date_obs") for e in epochs if isinstance(e, dict)]
    null_dates = sum(1 for d in dates if not d)
    keys = [e.get("key") for e in epochs if isinstance(e, dict)]
    null_keys = sum(1 for k in keys if not k)
    return f"{len(epochs)} 条；date_obs 空 {null_dates}；key 空 {null_keys}"
