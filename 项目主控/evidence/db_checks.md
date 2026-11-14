# 数据库工具 · 验证记录

快照：2026-11-14 23:07 CET。只读；所有会写的操作仅对 `/tmp` 或只读连接。

## 1. 测试与完整性

```bash
cd 数据库工具
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY -m unittest discover -s tests -v
# Ran 27 tests in 0.709s — OK
$PY cli.py verify --deep
```

```json
{
  "quick_check": "ok",
  "foreign_key_violations": 0,
  "foreign_keys": [],
  "sampled_blobs": 55,
  "missing_blobs": 0,
  "corrupt_blobs": 0
}
```

> `pytest` 不可用（未安装），用 `unittest`。27 个测试全部通过。

## 2. 当前统计（schema v4）

| 项 | 值 |
|---|---|
| frames | 5,857 |
| observations | 5,544 |
| weeks | 151 |
| targets | 7（5 官方） |
| target_epochs | 74 |
| cutouts | 55 |
| header_frames | **0** |
| frames_with_cutouts | 17 |
| 时间范围 | MJD 60793.541849–61316.529902（522.988 d） |
| releases | dr1 4,935 帧/13 周；qr3 922 帧/2 周 |
| detectors | D1 5,543；D2 64；D3 64；D4 63；D5 62；D6 61 |
| SQLite | 4.37 MB；schema v4 |
| Blob | 55 个；10,185,057 raw / 10,132,053 stored；全部 `npz-cutout` |

## 3. 导出契约（当前通过）

```bash
$PY cli.py export all --out /tmp/finexports2
$PY tools/check_compat.py --root .. --exports /tmp/finexports2
```

```text
fields.json      OK  (5857 行)
epoch_catalog    OK  (74 条)
targets.json     OK  (7 个)
week_index       OK  (151 个周)

全部兼容检查通过
```

结论：**离线 JSON 替换路径是可靠的**；`coord_unc_arcsec` 已在 v4 保留。问题在 HTTP API 与运行时接线。

## 4. HTTP API（:8799 临时实例，已停止）

| 端点 | 结果 | 关键字段 |
|---|---|---|
| `/api/health` | 200 | schema_version 4；frames 5857 |
| `/api/stats` | 200 | 同上 |
| `/api/targets` | 200 | 7 目标，含 truth/coord_unc_arcsec |
| `/api/epochs?target_id=barnard` | 200 | **8 行（未去重）**；`obs_id`/`fits_key`，无 `key`/`obsid` |
| `/api/epochs?ra=269.452077&dec=4.693365&fov=0.25` | 200 | **3 行**；`key` 有，`date_obs: null` |
| `/api/epochs?ra=76.956526&dec=30.401438&fov=0.5` | 200 | **0 行**（RW Aur） |
| `/api/coverage?ra=269.452077&dec=4.693365` | 200 | 3 期 / 6.451 d / L2 |
| `/api/coverage?ra=133.795129&dec=-7.245147` | 200 | 5 期 / 0.948 d / L2 |

**同一目标坐标，两个分支不一致**：

- Barnard：`target_id` 8 行（4 个唯一 MJD，含 2026W12 的 204 天历元）；`ra/dec` 3 行（丢掉 2026W12，因 sep=1.851° > 1.745°）。
- RW Aur：`target_id` 2 行；`ra/dec` 0 行（sep 1.843° > 1.745°）。

## 5. 覆盖半径实验

| 目标 | 1.745°（默认） | 1.9° | 2.47°（半对角线） |
|---|---|---|---|
| barnard | 3 期 / 6.45 d / L2 | 4 期 / 203.6 d / L2 | 5 期 / 203.8 d / L2 |
| epsind_b | 10 / 361.0 / L3 | 10 / 361.0 / L3 | 13 / 361.0 / L3 |
| wise0855 | 5 / 0.95 / L2 | 5 / 0.95 / L2 | 7 / 167.7 / L2 |
| rw_aur | **0 / 0 / L0** | **0 / 0 / L0** | 6 / 0.95 / L2 |
| r_dra | 5 / 7.68 / L2 | 5 / 7.68 / L2 | 5 / 7.68 / L2 |

结论：1.745° 是**半宽**，不是“单片覆盖半径”；用它做默认会系统性丢角和长基线历元。

## 6. 数据溯源

- 34 次 ingest 全 `status=ok`；**0 次 headers**；`bytes_read=0`。
- `header_frames=0/5857`；`naxis1`、`pixel_scale_arcsec`、`crpix1`、`sip_order`、`psf_fwhm_arcsec`、`l2dqa` 全空。
- Blob 只有 55 个 `npz-cutout`；**没有 `fits-header`**。
- `app/data/header_cache/` 不存在；`视图工具/data/fields.json` 仍声明 source 是它。
- 可复现性：当前 DB 能从 `app/data/*.json` + `视图工具/data/fields.json` + 55 个 NPZ **重建**（行哈希一致）；但**头级元数据无法离线重建**，必须回 S3/IRSA 采集。

## 7. 文档/实现对账

| 声明 | 实际 | 判定 |
|---|---|---|
| README：5,857 帧 / 5,544 曝光 | 一致 | ✅ |
| README：schema v4 | 一致 | ✅ |
| README：27 个测试 | 一致 | ✅ |
| README：`check_compat.py` 全过 | 一致 | ✅ |
| README 开头/landing 页：原始头在 Blob | 设计目标如此；当前 `header_frames=0`，Blob 只有 NPZ；README §2 已注明头缓存缺失 | ⚠️ 文档内部不一致（设计 vs 现状） |
| README：`/api/epochs` 与 MoveScope 语义对齐 | target 分支形状不同、未去重；ra 分支 date_obs 空、丢历元 | ❌ |
| INTEGRATION：`/api/epochs` 字段“同义” | 否 | ❌ |
| INTEGRATION：自定义坐标可查 | DB 可查；app 未接 | ⚠️ |
| INTEGRATION：视图深链不变 | 视图实际只读 hash，且指向不存在的 `app/index.html` | ❌ |
| `var/bench_scale_1m.sqlite` | 已删除；1M 数字当前不可复跑 | ⚠️ |

## 8. 其他已确认风险

- `isolation_level=None` + 立即 commit：批量导入非原子；`rows_inserted` 把 upsert 也计入，审计数字膨胀（week 1,208 vs 实际 151）。
- `check_schema()` 未被调用；`apply_schema()` 总是写入当前版本。
- CORS `*`；`/api/targets` 暴露 acceptance truth；无鉴权（仅本机）。
- `gc --dry-run` 仍打开可写连接；`schema.sql` 缺迁移创建的 `idx_cutouts_obs`。
- 1M benchmark 库已删除；bench 混用 7 个目标 + 24 个采样帧中心，不代表真实目标覆盖。

## 9. 结论

**代码工程质量高，数据层现状不足以直接支撑 R1。**
- 可用：离线导出（5,857/74/7/151 全过）、测试、完整性、cone/coverage 基础。
- 需修：`/api/v1/epochs` 规范 DTO、2.47° 覆盖、`date_obs`、去重、truth 隐藏、头文件采集。
