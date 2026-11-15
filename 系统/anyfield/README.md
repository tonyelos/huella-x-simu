# AnyField P0（8798）

> 状态：**C-P0 骨架，不是完整 C**。独立只读服务 + 可写 sidecar：
> catalog DB 只读；sidecar 保存 74 条 header 足迹、ingest 元数据和
> measurement。不联网、不下载像素、不做 FITS 检测/重投影、不生成 SIM。
>
> 完整 C 的设计见
> [`项目主控/架构/06_C_任意天区与增量设计.md`](../../项目主控/架构/06_C_任意天区与增量设计.md)。

## 启动

从仓库根目录：

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY 系统/anyfield/server.py --port 8798
```

默认：

- catalog：`系统/数据库工具/var/catalog.sqlite`（只读）
- sidecar：`系统/anyfield/var/anyfield.sqlite`（可写，自动建表）
- header blobs：`系统/数据库工具/var/blobs`（只读）
- 启动时从 `frames.header_sha256` 关联的 74 条 `fits-header` blob 重建
  `epoch_footprint`；其余 5,819 帧没有 header，查询时明确用 `center_approx`。

也可显式指定：

```bash
$PY 系统/anyfield/server.py --port 8798 \
  --db 系统/数据库工具/var/catalog.sqlite \
  --sidecar 系统/anyfield/var/anyfield.sqlite \
  --blob-root 系统/数据库工具/var/blobs
```

从服务目录：

```bash
cd 系统/anyfield
./start.sh 8798
```

服务绑定 `127.0.0.1:8798`；不要直接暴露到公网。数据库缺失时服务仍启动，
`/api/v1/health` 返回 503 `DB_UNAVAILABLE`。

## 状态语义（P0）

| HTTP | 条件 | 含义 |
|---:|---|---|
| **200** | 查到本地 epochs | 有真实 WCS 足迹（74 条有 header）或 `center_approx`（5,819 帧无 header）覆盖；每个 epoch 带 `coverage_method`/`footprint_method` |
| **422** | 查询落在 `coverage_region.complete=1` 且确认无 epochs | 唯一允许的 `NO_LOCAL_COVERAGE` 强断言；生产库目前没有 complete region，因此生产未知天区**不会**返回 422 |
| **503** | 索引不完整/无证据 | `INDEX_INCOMPLETE`，`strong_assertion=false`、`no_local_coverage=false` |
| **503** | DB 缺失/不可读 | `DB_UNAVAILABLE` |

不生成 SIM；`coverage_mode="center_approx"` 和 `coverage.method="center_radius_2.47"`
是 C-MVP 遗留兼容字段，**权威字段**是：

- 每个 epoch 的 `coverage_method` / `footprint_method`：`wcs_header` 或 `center_approx`；
- 汇总 `coverage.coverage_method` / `footprint_method`：`wcs_header` / `center_approx` / `mixed`；
- `coverage.n_wcs_header` / `n_center_approx`；
- `index_completeness`（P0 生产为 `partial`）。

真实足迹来自 header 的 `CRVAL/CRPIX/PC|CD/CDELT/NAXIS`，四角 TAN 投影；
P0 **未应用 SIP**，`sip_applied=false`，所以不是完整 SIP 校正足迹。

## API

### health

```bash
curl -s http://127.0.0.1:8798/api/v1/health
```

200：

```json
{
  "status": "ok",
  "service": "anyfield-p0",
  "service_version": "0.2.0",
  "schema_version": "1.0",
  "coverage_mode": "center_approx",
  "index_completeness": "partial",
  "index_snapshot": "local-...",
  "network": "none",
  "writes": "none",
  "catalog_writes": "none",
  "sidecar_writes": "enabled",
  "sidecar": {"path": "...", "counts": {"footprints": 74, "epoch_extra": 0, "measurements": 0, "complete_regions": 0}},
  "footprints": {"headers": 74, "built": 74, "failed": 0}
}
```

`writes="none"` 指 catalog DB；sidecar 可写，所以另给 `catalog_writes`/`sidecar_writes`。

### field

```bash
curl -s "http://127.0.0.1:8798/api/v1/field?ra=269.452077&dec=4.693365&fov=0.1"
```

200（Barnard，生产 DB 实测）：

```json
{
  "schema_version": "1.0",
  "index_snapshot": "local-...",
  "index_completeness": "partial",
  "footprint_method": "wcs_header",
  "source": "target_epochs",
  "coverage": {
    "state": "covered",
    "coverage_method": "wcs_header",
    "footprint_method": "wcs_header",
    "n_wcs_header": 4,
    "n_center_approx": 0,
    "n_epochs": 4,
    "coverage_level": "C2",
    "target_id": "barnard",
    "index_completeness": "partial"
  },
  "epochs": [
    {
      "obsid": "2025W35_2A_0221_1",
      "detector": 2,
      "release": "dr1",
      "week": "2025W35_2A",
      "version": "l2b-v27-2026-231",
      "mjd": 60916.656061887,
      "date_obs": "2025-08-29T15:44:43.747",
      "sep_deg": 0.228951,
      "key": "dr1/level2/.../level2_2025W35_2A_0221_1D2_spx_l2b-v27-2026-231.fits",
      "slice_url": "https://irsa.ipac.caltech.edu/ibe/data/spherex/...",
      "provenance": "real",
      "frame_id": 744,
      "coverage_method": "wcs_header",
      "footprint_method": "wcs_header"
    }
  ],
  "count": 4
}
```

503（生产未知天区，例如 0,0；**不是** `NO_LOCAL_COVERAGE`）：

```bash
curl -s -o /tmp/u.json -w '%{http_code}' \
  "http://127.0.0.1:8798/api/v1/field?ra=0&dec=0&fov=0.1"
# 503
# {"error":{"code":"INDEX_INCOMPLETE","retryable":true,
#           "details":{"strong_assertion":false,"no_local_coverage":false,...}}}
```

422（只可能来自 `coverage_region.complete=1` 的 fixture/显式区域）：

```json
{
  "error": {"code": "NO_LOCAL_COVERAGE", "retryable": false,
            "details": {"strong_assertion": true, "no_local_coverage": true,
                        "sim_allowed": true, "index_completeness": "complete"}},
  "coverage": {"state": "none", "index_completeness": "complete"}
}
```

- `fov`：0.02–3.0°，默认 0.2°。
- `center_approx` 仍用 2.47° 帧中心近似；有 header 的帧先过真实四角点-in-多边形。
- `GET /api/v1/epochs` 是同一解析器的 1.0 兼容视图。

### ingest（P0，metadata-only）

```bash
curl -s -X POST http://127.0.0.1:8798/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "source": {"kind": "local_manifest", "name": "manual"},
    "epoch": {
      "obsid": "2026W50_1A_0001_1", "detector": 1,
      "release": "dr1", "version": "l2b-v27-2026-999",
      "week": "2026W50_1A", "mjd": 61340.0,
      "ra": 120.0, "dec": 20.0,
      "footprint": {"method": "wcs_header",
        "corners": [[119,19],[121,19],[121,21],[119,21]]}
    },
    "measurements": [
      {"target_key": "demo", "ra": 120.0, "dec": 20.0,
       "flux": 1.2, "flux_err": 0.1, "snr": 12.0, "quality": ["TEST"]}
    ]
  }'
# 202 {"job_id":"ing_...","state":"queued","poll":"/api/v1/ingest/ing_..."}

curl -s http://127.0.0.1:8798/api/v1/ingest/ing_...
# 200 {"state":"committed","measurements_added":1,...}

curl -s -X POST http://127.0.0.1:8798/api/v1/ingest/ing_.../rollback
# 200 {"state":"retracted",...}（软回滚，不硬删）
```

- 只接受 `source.kind=local_manifest` 的本地 JSON；包含 `http(s)://` 或
  `url/uri/access_url/...` 字段会 400 `NETWORK_SOURCE_NOT_ALLOWED`。
- 幂等键是派生的 `epoch_id = sha256(release|version|obsid|detector)`；同
  `epoch_id` 同内容重复提交返回 202 + `replayed=true`，不新增 epoch/measurement。
- 写 sidecar 表 `ingest_job`、`epoch_extra`、`measurement`；原子发布；软回滚。
- **不下载像素、不跑 FITS 检测/重投影**；`measurements` 是调用方提供的
  元数据，不是 C 从图像算出的测量。P0 ingest ≠ 完整 C ingest。

## 测试

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
cd 系统/anyfield
$PY -m unittest discover -s tests -t . -v
```

当前 29 项，使用临时 catalog SQLite fixture + 临时 sidecar；另有生产 DB
只读集成用例（74 条 header 足迹，缺 fixture 时 skip）。测试不依赖 8797、
不联网；覆盖 health、Barnard 200/wcs_header、未知 503、complete blank 422、
点-in-多边形、ingest 成功/幂等/软回滚/坏输入、只读 catalog 不修改。

## 边界

- catalog DB 只读；不迁移 schema、不改 app/DB。
- 不联网；不调用 IRSA/S3/TAP/SIA/DataLink；不下载 FITS 像素。
- 不做像素 ingest、检测、重投影、PSF/FLAGS、series 重算。
- 真实足迹仅覆盖 74 条有 `header_sha256` 的帧；其余 5,819 帧明确
  `center_approx`，不得声称真实足迹。
- 422 强断言只对 `coverage_region.complete=1` 的区域；生产库没有 complete
  region，因此未知天区是 503，不是 422。
- 不是 C 完成；完整 C 的门槛见设计文档 §10–§12。
