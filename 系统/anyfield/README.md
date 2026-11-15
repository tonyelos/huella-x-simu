# AnyField P1B-1（8798）

> 状态：**C-P1B 第一步，不是完整 C**。独立只读 catalog + 可写 sidecar：
> 74 条 header 真实足迹、本地 NPZ/FITS ingest、C-native 源列表，以及
> **任意天区按需发现 + 一张 allowlisted S3/IRSA cutout**。
> 不重投影/共同网格、不做 series 重算、不输出 known/unknown/行星/新天体。
> 联网仅限 week_index + S3 Range 64 KB 头发现与 allowlisted IRSA cutout fetch；
> 不接受任意 URL。P1B 仍临时依赖 app `系统/app/core` 做 FITS 解析/检测，
> 这是阶段性依赖，**不是长期架构**。
>
> 完整 C 设计见
> [`项目主控/架构/06_C_任意天区与增量设计.md`](../../项目主控/架构/06_C_任意天区与增量设计.md)。

## 启动

从仓库根目录：

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY 系统/anyfield/server.py --port 8798
```

默认：

- catalog：`系统/数据库工具/var/catalog.sqlite`（只读）
- sidecar：`系统/anyfield/var/anyfield.sqlite`（可写，自动建表/迁移）
- header blobs：`系统/数据库工具/var/blobs`（只读）
- 允许的本地像素 root：
  - `系统/app/data`
  - `系统/参考 资料/muestras`
- 可用 `ANYFIELD_INGEST_ROOT`（`:` 分隔）覆盖允许 root；相对路径按仓库根解析。
- 按需发现：`系统/app/data/week_index.json` + S3 公开桶（默认
  `nasa-irsa-spherex.s3.amazonaws.com`）；
- fetch：只允许服务端构造的 IRSA static cutout / S3 allowlist，默认
  `irsa.ipac.caltech.edu` + `nasa-irsa-spherex.s3.amazonaws.com`，有字节上限。

```bash
# 显式配置允许 root（可多个）
ANYFIELD_INGEST_ROOT="系统/app/data:系统/参考 资料/muestras" \
  $PY 系统/anyfield/server.py --port 8798
```

也可用服务目录的 `./start.sh 8798`。服务只绑定 `127.0.0.1`；不要直接暴露公网。

## 状态语义

| HTTP | 条件 | 含义 |
|---:|---|---|
| **200** | 查到本地 epochs | 真实 header 足迹（74 条）或 `center_approx`（5,819 帧）覆盖；每个 epoch 带 `coverage_method`/`footprint_method` |
| **200** | `GET /sources` 有结果 | 本地已 ingest / on-demand S3 的源；`source_origin=ingested` 或 `on_demand_s3`、`provenance=real` |
| **200** | `GET /discover` 有候选 | week_index + S3 Range 64 KB 头返回候选；空列表 + `reason` 也是 200，不 422 |
| **503** | S3 不可达/超时 | `UPSTREAM_UNAVAILABLE`；不伪造候选 |
| **202** | `POST /fetch` | allowlisted cutout 异步 fetch；同 `epoch_id` 幂等 |
| **422** | `coverage_region.complete=1` 且确认无 epochs | 唯一允许的 `NO_LOCAL_COVERAGE` 强断言；生产库没有 complete region |
| **503** | 索引不完整/无证据 | `INDEX_INCOMPLETE`，`strong_assertion=false` |
| **503** | DB 缺失/不可读 | `DB_UNAVAILABLE` |
| **400** | ingest 路径/文件失败 | URL、越界路径、坏 NPZ/FITS、超大文件等 |

不生成 SIM。`coverage_mode="center_approx"` / `coverage.method="center_radius_2.47"`
是 C-MVP 遗留兼容字段；**权威**是 `coverage.coverage_method`/`footprint_method`
和 per-epoch 字段。真实足迹来自 header 的
`CRVAL/CRPIX/PC|CD/CDELT/NAXIS`，四角 TAN；**P1B 未应用 SIP**。

## API

### health

```bash
curl -s http://127.0.0.1:8798/api/v1/health
```

```json
{
  "status": "ok",
  "service": "anyfield-p1b",
  "service_version": "0.4.0",
  "schema_version": "1.0",
  "coverage_mode": "center_approx",
  "index_snapshot": "local-...",
  "network": "none",
  "writes": "none",
  "catalog_writes": "none",
  "sidecar_writes": "enabled",
  "sidecar": {"counts": {"footprints": 74, "epoch_extra": 0, "measurements": 0, "sources": 0}},
  "footprints": {"headers": 74, "built": 74, "failed": 0},
  "ingest": {"pixel_kinds": ["npz", "fits"], "allowed_roots": ["..."], "network": false}
}
```

`writes="none"` 指 catalog DB；sidecar 可写。

### discover

```bash
curl -s "http://127.0.0.1:8798/api/v1/discover?ra=120&dec=20&fov=0.2&max_weeks=2"
```

200：

```json
{
  "discovery": "week_index+s3_header",
  "count": 6,
  "candidates": [
    {"obsid": "2026W50_1A_0001_1", "detector": 1,
     "release": "dr1", "version": "l2b-v27-2026-999",
     "mjd": 61340.0, "date_obs": "...",
     "key": "dr1/level2/.../level2_2026W50_1A_0001_1D1_spx_....fits",
     "slice_url": "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/...",
     "sep_deg": 0.0, "discovery": "week_index+s3_header"}
  ],
  "reason": null
}
```

- 空结果：200 + `candidates=[]` + `reason=NO_WEEK_INDEX_MATCH`/`NO_S3_CANDIDATES`。
- S3 不可达/超时：503 `UPSTREAM_UNAVAILABLE`。
- 候选是 header 元数据，不下载像素；不生成 SIM，不做覆盖强断言。

### fetch

```bash
curl -s -X POST http://127.0.0.1:8798/api/v1/fetch \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {"s3_key": "dr1/level2/.../level2_2026W50_1A_0001_1D1_spx_....fits",
              "obsid": "2026W50_1A_0001_1", "detector": 1},
    "ra": 120.0, "dec": 20.0, "fov": 0.2
  }'
# 202 {"job_id":"ing_...","state":"queued","epoch_id":"..."}

curl -s http://127.0.0.1:8798/api/v1/ingest/ing_...
# 200 {"state":"committed","measurements_added":3,"input_kind":"s3_cutout"}
```

- 只接受 `s3_key`（`dr1|qr3/level2/.../*.fits`）+ `obsid`/`detector` + `ra/dec/fov`；
  服务端构造 IRSA cutout URL，不接受任意 URL。
- host 必须在 allowlist；redirect 到非 allowlist 拒绝；字节上限 16 MB（默认）。
- 下载后用 app `fitsio`/`pipeline` 解析/检测；`source_origin=on_demand_s3`、`provenance=real`。
- 202 异步；幂等/原子发布/软回滚沿用 P0/P1A；不重投影、不做 series。

### field（含 sources）

```bash
curl -s "http://127.0.0.1:8798/api/v1/field?ra=269.4462&dec=4.7673&fov=0.2"
```

200 响应会包含：

```json
{
  "schema_version": "1.0",
  "index_completeness": "partial",
  "coverage_mode": "center_approx",
  "footprint_method": "wcs_header",
  "coverage": {
    "state": "covered",
    "coverage_method": "wcs_header",
    "footprint_method": "wcs_header",
    "n_wcs_header": 4,
    "n_center_approx": 0,
    "source_count": 188
  },
  "epochs": [
    {
      "obsid": "2025W35_2A_0221_1", "detector": 2,
      "coverage_method": "wcs_header", "source_count": 49,
      "provenance": "real", "...": "..."
    }
  ],
  "sources": [
    {
      "source_id": "...", "source_index": 0,
      "x": 62.016, "y": 8.682,
      "ra": 269.45476104, "dec": 4.8516613,
      "flux": 128.28, "flux_err": 1.46, "snr": 161.68,
      "saturated": false, "quality": [],
      "source_origin": "ingested", "provenance": "real",
      "release": "dr1", "version": "local-..."
    }
  ],
  "source_count": 188,
  "source_origin": "ingested",
  "sources_url": "/api/v1/sources?ra=...&dec=...&fov=0.2&limit=200"
}
```

- `sources` 包含已 ingest / on-demand S3 的源，最多 200；`sources_truncated` 标记截断。
- 每条 source 带 `source_origin=ingested` 或 `on_demand_s3`、`provenance=real`。
- `source_count` 是当前响应中返回的源数；`epochs[].source_count` 是该 epoch
  的 ingested 源数。
- `fov` 0.02–3.0°，默认 0.2°；sources 搜索半径是 `fov/2`。

### sources

```bash
curl -s "http://127.0.0.1:8798/api/v1/sources?ra=269.4462&dec=4.7673&fov=0.2&limit=500"
curl -s "http://127.0.0.1:8798/api/v1/sources?ra=269.4462&dec=4.7673&fov=0.2&epoch=2025W35_2A_0221_1"
```

返回 `source_id/epoch_id/obsid/detector/release/version/mjd/source_index/x/y/ra/dec/flux/flux_err/snr/saturated/quality/source_origin/provenance/sep_deg`。
`x/y` 是**该期原图 0-based 像素**；服务不做 common grid/重投影，因此没有公共像素坐标。
`epoch=` 同时接受 sidecar `epoch_id` 或 `obsid`。没有本地 ingested / on-demand S3 源时返回
200 + 空列表，不返回 SIM。

### ingest：P0 manifest（metadata-only）

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
       "flux": 1.2, "flux_err": 0.1, "snr": 12.0}
    ]
  }'
# 202 {"job_id":"ing_...","state":"queued"}
```

### ingest：P1A 本地 NPZ/FITS

```bash
curl -s -X POST http://127.0.0.1:8798/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"input": {"path": "系统/app/data/epochcache/dr1_2025W35_2A_0221_1_D2_269.4462_4.7673_0.200.npz"}}'
# 202 {"job_id":"ing_...","state":"queued","epoch_id":"..."}

curl -s http://127.0.0.1:8798/api/v1/ingest/ing_...
# 200 {"state":"committed","measurements_added":49,"input_kind":"npz",...}
```

- `.npz`：复用 app NPZ 格式（`image/variance/flags/header/psf/mjd/obsid/detector/release/date_obs`）。
- `.fits`：复用 app `core/fitsio` 读 IMAGE/VARIANCE/FLAGS/ZODI/WCS-WAVE；EPSF 不参与。
- 检测：复用 app `core/pipeline.detect_sources` + `dedupe_sources`/`source_quality`，
  每个源写 sidecar `measurement`：`source_index,x,y,ra,dec,flux,flux_err,snr,saturated,quality`，
  `source_origin=ingested`、`provenance=real`。
- 只接受本地路径；`http(s)://` → 400 `NETWORK_SOURCE_NOT_ALLOWED`；越界 →
  400 `PATH_NOT_ALLOWED`；坏 NPZ/FITS → 400 `INVALID_INGEST_FILE`。
- 幂等键仍为 `epoch_id`：同文件重复 → 202 `replayed=true`，不新增 measurement。
- 202 异步；原子发布/软回滚沿用 P0；rollback 同时隐藏 epoch 与对应 sources。

## 测试

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
cd 系统/anyfield
$PY -m unittest discover -s tests -t . -v
```

当前 **45 项**（P0 29 + P1A 7 + P1B 9），使用临时 catalog SQLite + 临时
sidecar + synthetic NPZ/FITS + fake S3/IRSA（week list/header/cutout）；
不依赖 8797、不访问真实外网。
真实 app NPZ 验收：Barnard 4 期 ingest 后 `/sources` 在 0.2° 场返回 188 个源
（原始 49/52/53/61，field 半径过滤后 188），`field.sources` 可见。
app 205 tests、DB 110 tests 同时通过。

## 边界

- catalog DB 只读；不迁移 app/DB schema；sidecar 独立。
- 发现仅 week_index + S3 Range 64 KB 头；fetch 仅一张 allowlisted IRSA cutout；
  无 TAP/SIA/DataLink、无公网/鉴权。
- 只支持本地 NPZ/FITS 与单张 S3 cutout；不做像素重投影/共同网格、不做
  forced photometry、不做 series 重算。
- 不做已知/未知识别、不输出行星/新天体；sources 只是检测源，classification 属 D。
- 74 条真实 header 足迹之外的 5,819 帧仍是 `center_approx`；P1B 未应用 SIP。
- 不是 C 完成；完整 C 门槛见设计文档 §10–§12。
