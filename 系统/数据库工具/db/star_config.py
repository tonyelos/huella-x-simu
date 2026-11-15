"""星表服务（阶段 B/D1）的路径与常量。

星表库刻意与 SPHEREx 生产库分开：

    var/catalog.sqlite        SPHEREx 帧/历元/Blob 元数据（生产库，不改）
    var/star_catalog.sqlite   本模块：Gaia/2MASS/WISE 按场缓存（可重建）

这样星表导入/重建不会影响 MoveScope 正在使用的 SPHEREx 目录，也避免把
18 亿行 Gaia 全量塞进单文件 SQLite。
"""
from __future__ import annotations

from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
VAR_DIR = TOOL_ROOT / "var"
DEFAULT_STAR_DB = VAR_DIR / "star_catalog.sqlite"
FIXTURE_DIR = TOOL_ROOT / "tests" / "fixtures"
DEFAULT_GAIA_FIXTURE = FIXTURE_DIR / "gaia_dr3_sample.csv"
CATALOG_DOC = TOOL_ROOT / "docs" / "CATALOG.md"

SERVICE_NAME = "star-catalog"
CATALOG_API_VERSION = "1.0"
STAR_SCHEMA_VERSION = 5
CATALOG_PORT = 8799

# 每个预设场的缓存半径。0.25° 覆盖 app 默认 0.25° 视场（半对角线约 0.177°），
# 也覆盖 7 个目标 ±0.1° 的候选源搜索；超出缓存的查询会显式标 complete=false。
FIELD_CACHE_RADIUS_DEG = 0.25
GAIA_MAG_LIMIT = 20.0
HEALPIX_NSIDE = 64

# Gaia DR3 的参考历元 J2016.0 = MJD 57388.5（2016-01-01 12:00 TT）。
# Gaia 的 ref_epoch 列给的是 2016.0（Julian year），传播时必须换成 MJD。
GAIA_EPOCH_REF = "J2016.0"
GAIA_EPOCH_MJD = 57388.5
JULIAN_YEAR_DAYS = 365.25
MAS_PER_DEG = 3_600_000.0

# 6.15″ 是 SPHEREx 的典型像元尺度；同一像元内多颗星表源时标 ambiguous。
SPHEREX_PIXEL_ARCSEC = 6.15
DEFAULT_MATCH_RADIUS_ARCSEC = 3.0
DEFAULT_AMBIGUITY_ARCSEC = SPHEREX_PIXEL_ARCSEC
MAX_MATCH_RADIUS_ARCSEC = 60.0
MAX_QUERY_RADIUS_DEG = 5.0
MAX_CONE_LIMIT = 2_000
MAX_MATCH_DETECTIONS = 1_000

# 场级 ingest（POST /api/v1/catalog/ingest）的边界。
INGEST_MIN_RADIUS_DEG = 0.05
INGEST_MAX_RADIUS_DEG = 1.0
INGEST_MIN_MAG_LIMIT = 12.0
INGEST_MAX_MAG_LIMIT = 20.0
INGEST_TAP_TIMEOUT_SECONDS = 120.0
INGEST_TAP_RETRIES = 2
INGEST_MAX_FIELD_ID_LEN = 128

# 7 个预设场：5 个官方目标 + 2 个演示场（与 app/data/targets.json 一致）。
# 只在这里维护坐标；导入和文档都读这一份。
PRESET_FIELDS: dict[str, dict[str, object]] = {
    "barnard": {"name": "Barnard's Star", "ra": 269.452077, "dec": 4.693365, "official": True},
    "epsind_b": {"name": "Epsilon Indi B", "ra": 331.044147, "dec": -56.782844, "official": True},
    "wise0855": {"name": "WISE 0855-0714", "ra": 133.795129, "dec": -7.245147, "official": True},
    "rw_aur": {"name": "RW Aurigae", "ra": 76.956526, "dec": 30.401438, "official": True},
    "r_dra": {"name": "R Draconis", "ra": 248.167611, "dec": 66.754979, "official": True},
    "m101": {"name": "M101 field", "ra": 210.80227, "dec": 54.34895, "official": False},
    "campo_a": {"name": "Field A (DR1)", "ra": 160.617, "dec": 30.67, "official": False},
}


# 已知预设目标的名称/SIMBAD 标识 enrichment。
# provenance 一律是 preset_target_names：这是项目策展的名称，不是 SIMBAD 场目录；
# SIMBAD TAP 不可用时不能把这些名字伪装成在线 SIMBAD 查询结果。
PRESET_TARGET_NAMES: dict[str, dict] = {
    "barnard": {
        "name": "Barnard's Star",
        "simbad_id": "NAME Barnard's Star",
        "aliases": ["GJ 699", "HIP 87937", "BD+04 3561a", "V* V2500 Oph"],
        "object_type": "star",
    },
    "epsind_b": {
        "name": "Epsilon Indi B",
        "simbad_id": "* eps Ind B",
        "aliases": ["GJ 845 B", "TIC 219570174"],
        "object_type": "brown_dwarf",
    },
    "wise0855": {
        "name": "WISE 0855-0714",
        "simbad_id": "WISE J085510.83-071442.5",
        "aliases": ["WISE 0855-0714"],
        "object_type": "brown_dwarf",
    },
    "rw_aur": {
        "name": "RW Aurigae",
        "simbad_id": "V* RW Aur",
        "aliases": ["HD 240764", "HIP 25195"],
        "object_type": "star",
    },
    "r_dra": {
        "name": "R Draconis",
        "simbad_id": "V* R Dra",
        "aliases": ["HD 149730", "HIP 81181"],
        "object_type": "star",
    },
    "m101": {
        "name": "M101",
        "simbad_id": "M 101",
        "aliases": ["NGC 5457", "UGC 8981"],
        "object_type": "galaxy",
    },
    "campo_a": {
        "name": "Field A (DR1)",
        "simbad_id": None,
        "aliases": [],
        "object_type": "field",
        "note": "演示场，不是 SIMBAD 天体；无 SIMBAD ID。",
    },
}
