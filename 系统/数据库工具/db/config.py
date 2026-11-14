"""全局路径与常量。

所有路径都以 `数据库工具/` 为根。测试可以通过参数覆盖，不依赖当前工作目录。
"""
from __future__ import annotations

from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
VAR_DIR = TOOL_ROOT / "var"
DEFAULT_DB = VAR_DIR / "catalog.sqlite"
DEFAULT_BLOBS = VAR_DIR / "blobs"
DEFAULT_EXPORTS = VAR_DIR / "exports"

TOOL_VERSION = "1.1.0"
SCHEMA_VERSION = 6
API_SCHEMA_VERSION = "1.0"

# SPHEREx 单片视场约 3.49°×3.49°：半宽 1.745°，半对角线 2.47°。
# 覆盖查询默认用半对角线（2.47°），否则会系统性丢角上的长基线历元；
# 1.745° 只作为“中心附近”的保守过滤保留。
FRAME_HALF_WIDTH_DEG = 1.745
COVERAGE_RADIUS_DEG = 2.47
FRAME_RADIUS_DEG = FRAME_HALF_WIDTH_DEG  # 向后兼容旧代码
TARGET_MATCH_RADIUS_DEG = 0.05
# 天空分区高度（度）。0.5° 一条赤纬带，配 (zone, ra) 复合索引做锥搜索。
ZONE_HEIGHT_DEG = 0.5

# 单次查询最多取回的候选行，防止一个大圆盘把内存打爆。
MAX_CANDIDATES = 200_000
MAX_PAGE_SIZE = 2_000

READINESS_LABELS = {
    "L0": "1 期：只能判“有没有源”，不能判运动",
    "L1": "2 期：只能给一次位移，不能定规律",
    "L2": "3–5 期：可测速率与方向",
    "L3": "≥6 期且跨 6 个月：可判加速/视差/周期",
}

# 数据库覆盖等级：只描述“拍过几次、跨多久”，不是检测质量。
COVERAGE_LABELS = {
    "C0": "≤1 期：只能确认拍过",
    "C1": "2 期：一次位移",
    "C2": "3–5 期，或 ≥6 期但基线 <180 天",
    "C3": "≥6 期且基线 ≥180 天",
}
