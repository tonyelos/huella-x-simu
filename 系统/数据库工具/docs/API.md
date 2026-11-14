# HTTP API 与 Python 客户端

启动：

```bash
cd 数据库工具
./start.sh 8797
# 或
python3 cli.py serve --port 8797
```

默认只监听 `127.0.0.1`，只读；`/api/*` 返回 JSON（`/api/blob/*` 除外），
带 `Access-Control-Allow-Origin: *`，方便两个前端跨端口调试。

> 契约版本：**API v1**（`schema_version: "1.0"`）；SQLite 库版本 **v6**。
> 旧 `/api/epochs`、`/api/coverage` 仍可用，但已标记 deprecated。

---

## 1. 端点总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 契约 v1 健康检查（`schema_version: "1.0"`） |
| GET | `/api/v1/epochs` | 规范 EpochRef；`target_id` 或 `ra/dec` 两个入口 |
| GET | `/api/v1/coverage` | 覆盖机会 C0–C3、`header_available`、`n_local_cutouts` |
| GET | `/api/health` | 兼容健康检查（字段同 v1） |
| GET | `/api/stats` | 完整统计：数量/时间/release/存储/最近导入 |
| GET | `/api/targets` | 目标列表；默认不含 acceptance truth |
| GET | `/api/targets/{id}` | 单个目标 |
| GET | `/api/epochs` | **deprecated**；委托 v1，保留旧形状 |
| GET | `/api/coverage` | **deprecated**；含 `readiness`（已标 deprecated） |
| GET | `/api/cone` | 锥搜索（精确大圆距离） |
| GET | `/api/weeks?release=` | 观测周与处理版本 |
| GET | `/api/frames/{obsid}/{detector}` | 单帧元数据 + 切割产品 |
| GET | `/api/cutouts?obs_id=` | 切割产品列表 |
| GET | `/api/blob/{sha256}` | 原始头/切割 Blob，immutable 缓存 |
| GET | `/api/export/{fields,epochs,targets,weeks}.json` | 兼容 JSON 导出 |
| GET | `/api/docs` | README |
| GET | `/` | 数据概览页（HTML） |

---

## 2. `/api/v1/epochs`

### 参数

- `target_id=<id>` 或 `ra=<deg>&dec=<deg>`（二选一；同时给时以 `target_id` 为准，
  且要求坐标在 0.05° 内，否则 400）。
- `fov=<deg>`（0.02–3.5，默认 **0.25**）；只影响 `slice_url` 的 `size`。
- `radius=<deg>`（0.2–5，默认 **2.47**）；只影响覆盖统计/兜底锥搜索。
- `release=dr1|qr3`（可选）。
- `limit/offset`（默认 500/0，最大 2000）。
- `format=v1|movescope`（默认 v1；movescope 返回旧形状，便于过渡）。

### 规则

- 每个 **obsid** 一条 `EpochRef`；同一曝光的 D1–D6 只保留 `sep_deg` 最小者。
- 同一 `(obsid, detector)` 不重复；排序 `mjd` 升序、`detector` 升序。
- `date_obs` 不得为 null；缺失时由 `mjd` 反推 UTC ISO。
- `slice_url` 的 `size == fov`；`key` 必须存在。
- `ra/dec` 先匹配 0.05° 内目标；命中则用该目标的 curated `target_epochs`，
  保证“手动输入 Barnard 坐标”和“点 Barnard 预设”返回同一组历元。
- 没有 curated 或没有 frames 时，按 `radius` 锥搜索 `frames` 兜底。

### 示例

```bash
curl 'http://127.0.0.1:8797/api/v1/epochs?target_id=barnard'
curl 'http://127.0.0.1:8797/api/v1/epochs?ra=269.452077&dec=4.693365&fov=0.25'
```

```json
{
  "schema_version": "1.0",
  "query": {"target_id": "barnard", "ra": 269.452077, "dec": 4.693365,
            "fov": 0.25, "radius": 2.47, "release": null, "limit": 500, "offset": 0},
  "matched_target_id": "barnard",
  "coverage": {
    "radius_deg": 2.47, "n_frames": 8, "n_epochs": 4, "n_observations": 4,
    "n_weeks": 3, "releases": ["dr1"], "detectors": [1,2,3,4,5,6],
    "mjd_min": 60916.656, "mjd_max": 61120.279, "baseline_days": 203.623,
    "coverage_level": "C2", "header_available": true, "n_local_cutouts": 20,
    "source": "curated"
  },
  "epochs": [
    {
      "obsid": "2025W35_2A_0221_1",
      "detector": 2,
      "release": "dr1",
      "week": "2025W35_2A",
      "version": "l2b-v27-2026-231",
      "mjd": 60916.656062,
      "date_obs": "2025-08-29T15:44:43.747",
      "sep_deg": 0.229,
      "key": "dr1/level2/2025W35_2A/l2b-v27-2026-231/2/level2_2025W35_2A_0221_1D2_spx_l2b-v27-2026-231.fits",
      "slice_url": "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/2025W35_2A/l2b-v27-2026-231/2/level2_2025W35_2A_0221_1D2_spx_l2b-v27-2026-231.fits?center=269.452077,4.693365d&size=0.25",
      "provenance": "real",
      "frame_id": 744,
      "header_sha256": "..."
    }
  ],
  "count": 4,
  "returned": 4,
  "generated_at": "2026-11-14T23:45:00Z"
}
```

### 两入口一致性（实测）

| target | target_id 条数 | ra/dec 条数 | 一致 |
|---|---:|---:|---|
| barnard | 4 | 4 | ✅ |
| epsind_b | 10 | 10 | ✅ |
| wise0855 | 10 | 10 | ✅ |
| rw_aur | 1 | 1 | ✅ |
| r_dra | 3 | 3 | ✅ |

Barnard 包含 2026W12 的 204 天历元（`sep≈1.851°`），说明默认半径 2.47° 生效。

---

## 3. `/api/v1/coverage`

参数：`ra`,`dec` 必填；`radius` 默认 2.47（0.2–5）。

```bash
curl 'http://127.0.0.1:8797/api/v1/coverage?ra=269.452077&dec=4.693365'
```

返回：

```json
{
  "query": {"ra": 269.452077, "dec": 4.693365, "radius": 2.47},
  "radius_deg": 2.47,
  "n_frames": 10, "n_epochs": 5, "n_observations": 5, "n_weeks": 3,
  "n_releases": 1, "releases": ["dr1"], "detectors": [1,2,3,5],
  "mjd_min": 60916.519, "mjd_max": 61120.279, "baseline_days": 203.759,
  "coverage_level": "C2",
  "coverage_label": "3–5 期，或 ≥6 期但基线 <180 天",
  "header_available": true,
  "n_local_cutouts": 20,
  "schema_version": "1.0",
  "generated_at": "2026-11-14T23:45:00Z"
}
```

- **不返回** `readiness` / L0–L3；覆盖机会与检测质量分开。
- `C0` ≤1；`C1` 2；`C2` 3–5 或 ≥6 但基线 <180 d；`C3` ≥6 且 ≥180 d。
- `n_frames` 是 detector 帧数；`n_epochs` 按 obsid 去重后的曝光数。
- `header_available` 表示该天区是否已有 FITS 头；`n_local_cutouts` 按
  `(obs_id, detector)` 匹配本地 NPZ/FITS 切割。

---

## 4. 兼容 / 旧端点

### `/api/epochs`

内部调用 v1，返回：

```json
{
  "target_id": "barnard",
  "count": 4,
  "epochs": [{"obsid": "...", "obs_id": "...", "key": "...", "fits_key": "...", "date_obs": "...", "...": "..."}],
  "coverage": {"...": "..."},
  "deprecated": true,
  "schema_version": "1.0"
}
```

旧字段 `obs_id`、`fits_key`、`frame_present` 仍提供；但新代码请用 v1。

### `/api/coverage`

返回 `Catalog.coverage()` 的旧形状，含 `readiness`，其中：

```json
"readiness": {"level": "L2", "deprecated": true,
              "note": "覆盖等级请用 coverage_level；analysis_readiness 由 MoveScope 管线给出。"}
```

---

## 5. 其他端点

### `/api/health` 与 `/api/v1/health`

```json
{
  "ok": true,
  "service": "spherex-db",
  "version": "1.1.0",
  "schema_version": "1.0",
  "db_schema_version": 6,
  "data_snapshot": "2026-11-14T22:43:38Z",
  "counts": {"frames": 5893, "observations": 5562, "weeks": 151, "targets": 7,
             "epochs": 74, "cutouts": 101, "header_frames": 74, "frames_with_cutouts": 30},
  "time": {"mjd_min": 60789.75, "mjd_max": 61316.53, "baseline_days": 526.779}
}
```

### `/api/targets`

默认隐藏 acceptance `truth`；需要时传 `?include_truth=1`：

```bash
curl 'http://127.0.0.1:8797/api/targets?include_truth=0'
```

### `/api/cone`

参数：`ra`,`dec` 必填；`radius` 默认 0.2；`limit/offset`；`order=sep|mjd`；
`detector=1,2`；`release=dr1|qr3`；`mjd_min/mjd_max`；`size`；`cutouts=1`。

```bash
curl 'http://127.0.0.1:8797/api/cone?ra=269.452077&dec=4.693365&radius=2.47&limit=10'
```

### `/api/frames`、`/api/cutouts`、`/api/blob`

```bash
curl 'http://127.0.0.1:8797/api/frames/2025W35_2A_0221_1/2'
curl 'http://127.0.0.1:8797/api/cutouts?obs_id=2025W35_2A_0221_1'
SHA=$(curl -s 'http://127.0.0.1:8797/api/cutouts?frame_id=744' | jq -r '.cutouts[0].sha256')
curl -s -o cutout.npz "http://127.0.0.1:8797/api/blob/$SHA"
```

`/api/blob` 返回 `ETag`/`Cache-Control: immutable`，带 `If-None-Match` 返回 304。

### `/api/export/...`

```bash
curl -s 'http://127.0.0.1:8797/api/export/fields.json'  > fields.json
curl -s 'http://127.0.0.1:8797/api/export/epochs.json'  > epoch_catalog.json
curl -s 'http://127.0.0.1:8797/api/export/targets.json' > targets.json
curl -s 'http://127.0.0.1:8797/api/export/weeks.json'   > week_index.json
```

`fields.json` 从 `fields_snapshot` 导出（冻结的 5,893 行，顺序一致）；
T4 新增的帧不会改变旧文件，但可通过 `/api/cone` / `/api/v1/epochs` 查到。

---

## 6. 通用约定

- 分页：`limit` 默认 100（v1 epochs 默认 500），单页最大 2000；`offset` 从 0 开始。
- 参数错误 400；目标/帧/blob/路径不存在 404；内部错误 500。
- JSON 在 `Accept-Encoding: gzip` 且 ≥1 KB 时 gzip。
- `ra` 自动对 360 取模；`dec` 必须在 [-90,90]。

---

## 7. Python 客户端

```python
from client import RemoteCatalog

db = RemoteCatalog("http://127.0.0.1:8797")
print(db.health()["counts"])
print(db.epochs_v1(target_id="barnard", fov=0.25)["count"])
print(db.epochs_v1(ra=269.452077, dec=4.693365)["matched_target_id"])
print(db.coverage_v1(269.452077, 4.693365)["coverage_level"])
for e in db.epochs_v1(target_id="barnard")["epochs"]:
    print(e["obsid"], e["detector"], e["date_obs"], e["slice_url"])
```

离线批处理：

```python
from client import LocalCatalog

with LocalCatalog("var/catalog.sqlite") as db:
    for t in db.targets():
        print(t["id"], db.epochs_v1(target_id=t["id"])["count"])
```

---

## 8. 性能与安全

- 真实库 5,893 帧：cone p95 **< 0.6 ms**；v1 epochs（含去重/目标匹配）通常 1–3 ms。
- 200k/1M 合成规模库见 `benchmarks/`；查询成本主要随命中帧数增长。
- 只读、无写接口；默认绑定 `127.0.0.1`；CORS 通配仅用于本地前端。
- 公网部署请加反向代理、鉴权、限流，并关闭 CORS 通配。
