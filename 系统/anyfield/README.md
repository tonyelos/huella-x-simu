# AnyField C-MVP（8798）

> 状态：**C-MVP 骨架，不是完整 C**。独立只读服务，只回答“本地已有
> `catalog.sqlite` 里有没有这个天区的真实历元”；不联网、不写库、不做
> ingest/像素下载、不生成 SIM。

完整 C 的目标、接口和验收见
[`项目主控/架构/06_C_任意天区与增量设计.md`](../../项目主控/架构/06_C_任意天区与增量设计.md)。

## 启动

从仓库根目录：

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY 系统/anyfield/server.py --port 8798
```

默认只读 `系统/数据库工具/var/catalog.sqlite`。也可以显式指定：

```bash
$PY 系统/anyfield/server.py --port 8798 \
  --db 系统/数据库工具/var/catalog.sqlite
```

从服务目录：

```bash
cd 系统/anyfield
$PY server.py --port 8798 --db ../数据库工具/var/catalog.sqlite
# 或
./start.sh 8798
```

服务绑定 `127.0.0.1:8798`；不要直接暴露到公网。数据库缺失时服务仍启动，
`/api/v1/health` 返回 503 `DB_UNAVAILABLE`。

## API

### health

```bash
curl -s http://127.0.0.1:8798/api/v1/health
```

200：

```json
{
  "status": "ok",
  "service": "anyfield-mvp",
  "service_version": "0.1.0",
  "schema_version": "1.0",
  "coverage_mode": "center_approx",
  "index_completeness": "partial",
  "index_snapshot": "local-cffd824d4ac2",
  "network": "none",
  "writes": "none",
  "db": {"readable": true, "read_only": true}
}
```

### field

```bash
curl -s "http://127.0.0.1:8798/api/v1/field?ra=269.452077&dec=4.693365&fov=0.1"
```

200（有本地覆盖）：

```json
{
  "schema_version": "1.0",
  "index_snapshot": "local-...",
  "coverage_mode": "center_approx",
  "source": "target_epochs",
  "coverage": {
    "state": "covered",
    "method": "center_radius_2.47",
    "radius_deg": 2.47,
    "n_frames": 8,
    "n_epochs": 4,
    "baseline_days": 203.623,
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
      "frame_id": 744
    }
  ],
  "count": 4,
  "warnings": ["CENTER_APPROX_COVERAGE: ...", "LOCAL_INDEX_PARTIAL: ..."]
}
```

503（本地没有证据，**不是** `NO_LOCAL_COVERAGE`）：

```bash
curl -s "http://127.0.0.1:8798/api/v1/field?ra=0&dec=0&fov=0.1"
# 503
# {"error":{"code":"INDEX_INCOMPLETE","retryable":true,
#           "details":{"strong_assertion":false,"no_local_coverage":false,...}}}
```

- `fov`：0.02–3.0°，默认 0.2°。
- `center_approx`：用帧中心 2.47° 近似，不是真实 WCS 足迹；大 FOV/边缘会误判。
- 未知天区返回 **503 `INDEX_INCOMPLETE`**；MVP 不做 `422 NO_LOCAL_COVERAGE` 强断言。
- 不返回 `synthetic`/SIM；所有 epoch `provenance="real"`。

### epochs（1.0 兼容便捷视图）

```bash
curl -s "http://127.0.0.1:8798/api/v1/epochs?ra=269.452077&dec=4.693365&fov=0.1"
```

返回 `schema_version="1.0"`、EpochRef 12 字段、coverage `radius_deg=2.47`；
有本地覆盖 200，未知 503 `INDEX_INCOMPLETE`（不返回空 200 冒充无覆盖）。

## 测试

```bash
cd 系统/anyfield
$PY -m unittest discover -s tests -t . -v
```

测试使用临时 SQLite fixture，不依赖生产 DB、8797 服务或网络；当前 18 项，覆盖
health、Barnard 200、未知天区 503、DB 缺失 503、离线解析、只读不修改 DB、
`/epochs` 兼容和参数校验。

## 边界

- 只读 `catalog.sqlite`；不写库、不迁移 schema。
- 不联网；不调用 IRSA/S3/TAP/SIA/DataLink。
- 不做 ingest、HDU 裁剪、重投影、检测/匹配、measurement/series。
- 没有完整天空索引；未知 = 503 `INDEX_INCOMPLETE`，不是“确认空白”。
- 不是 C 完成；完整 C 见设计文档 §10–§12。
