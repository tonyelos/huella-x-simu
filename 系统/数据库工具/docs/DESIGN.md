# 设计说明

## 1. 需求从哪来

`参考 资料/` 里最关键的几条事实：

1. **单个 SPHEREx level2 文件约 65 MB**，7 个 HDU；`EPSF`（8752×441）不参与切片，
   占了切片文件的 97%。所以“把整文件塞进数据库”既慢又浪费。
2. **空间 2040×2040、像素约 6.15″**；6 个探测器 D1–D6；102 个波段。
3. **每 6 个月扫全天**；QR3 与 DR1 两个处理版本并存，不能混着做光度。
4. **静态切片可用，检索服务（TAP/SIA/DataLink）当天全挂**；降级路径是
   S3 路径重建 + `?center=&size=`。所以本地必须有一个能离线回答“哪块天被拍过”的索引。
5. 两个项目已经各自积累了 `header_cache`（14k 个 45 KB 头）、`epochcache`（NPZ 切片）、
   `fields.json`（5,893 行）——这些就是数据库的第一批真实数据。

由此得到三条硬约束：

- 查询必须在**没有网络**时可用；
- 元数据要能被**增量更新**，不能每次全量重建 JSON；
- 单机、零安装、和现有 Python 运行环境兼容（只有 stdlib + numpy）。

## 2. 为什么是 SQLite + 内容寻址 Blob

| 方案 | 优点 | 不适合的地方 |
|---|---|---|
| 纯 JSON 文件 | 零依赖、前端直接读 | 无索引、无事务、全量重写、并发差 |
| SQLite 单库存所有内容 | 简单、事务强 | 45 KB 头 × 14k、4 MB 切片 × N 会把库撑大，VACUUM/备份变慢 |
| **SQLite 元数据 + Blob 仓库** | 库小而快；Blob 可 TB 级；天然去重 | 需要自己维护引用与 GC |
| PostgreSQL / DuckDB | 查询强、并发好 | 需要安装/服务/权限；演示环境不保证；DuckDB 生态不成熟 |
| Parquet + 目录 | 列式扫描快 | 点查/锥搜索需要额外索引层；更新麻烦 |

选择 SQLite 的理由是**部署成本为零**，而把大对象挪出去之后，元数据表在 10^7 行
量级仍然可控。如果未来要上服务端，schema 和 API 可以原样迁到 PostgreSQL；
Blob 层的 sha256 也不变。

## 3. Schema 的关键决策

### 3.1 为什么把 observations 和 frames 拆开

一次曝光（`obs_id = 2025W48_1A_0658_4`）有最多 6 个探测器帧。若把
`mjd/week/field/repeat/release` 抄 6 份，既冗余又容易不一致。拆开后：

- `observations` 是观测级事实（时间、周、场号、重复号）；
- `frames` 是探测器级事实（CRVAL、像素尺度、PSF、头 sha256、zone）。

查询仍然是一条 join，代价可忽略。

### 3.2 为什么 frames 的唯一键不是 release

`(obs_id, detector)` 唯一。原因是两个项目都把它当身份，且当前数据里没有重复。
但同一 obs 可能被 QR3 和 DR1 各处理一次，像素/定标不同；因此：

- `frames.release` 存“规范版本”（优先使用最近一次导入/回填的版本）；
- `frame_releases` 存所有版本 + `procver` + 原始 S3 key；
- 查询 `?release=dr1` 用 `EXISTS` 过滤，不复制帧。

这样既保持消费端简单，又保留处理溯源。做严格时序光度时，应按 release 分组，
不要跨版本拟合。

### 3.3 target_epochs 为什么允许 frame_id 为空

数据到达顺序不可控：`epoch_catalog.json` 可能先于头文件导入。若强制外键，
就只能先造空帧或丢历元。这里保留 `obs_id/detector` 与可空 `frame_id`：

- 目录先到时，`/api/epochs` 仍能给出 `slice_url`（MoveScope 够用）；
- 头文件后到时，`relink_cutouts`/导入逻辑把 `frame_id` 补上；
- 覆盖统计只统计有帧的行，不会把“只有目录”的历元当成真实覆盖。

### 3.4 天空索引：zone + RA

```
zone = floor((dec + 90) / 0.5)        # 0 .. 359
INDEX (zone, ra)                      # 复合索引
```

锥搜索步骤：

1. `dec ∈ [dec−r, dec+r]` → `zone ∈ [z0, z1]`；
2. `ra_width = r / max(cos(dec), 0.02)`；跨 0° 时拆成两段；
3. SQL 只扫这些带内的行；
4. Python 用 haversine 做精确过滤、排序、分页。

复杂度约 `O(log N + k)`，`k` 是带内候选数，与总行数弱相关。对极区/大半径
自动退化为只按 zone 扫描。没有用 SQLite 的 `sin/cos` 扩展，避免不同编译选项
导致的“有的机器能跑、有的不能”。

### 3.5 Blob 仓库

```
var/blobs/ab/cd/abcdef...z
```

- `ab/cd` 两级分片，避免单目录几十万文件；
- 文件名是明文 SHA-256，校验就是“再算一次”；
- zlib-6 压缩；如果压缩后更大（已压缩的 NPZ），`put_file` 存 `.raw` 并标 `codec=none`；
- `put_bytes`/`put_file` 都走临时文件 + `os.replace`，崩溃不会留半截；
- 引用来自 `frames.header_sha256` 与 `cutouts.sha256`；`gc` 只删无引用项。

**为什么不用 SQLite BLOB**：SQLite 的页缓存和 WAL 会被大 BLOB 污染，备份/复制
也会变慢；把 BLOB 放文件系统、库里只留指针，是更常见的分层做法。

### 3.6 fields_snapshot 为什么存在

`视图工具/data/fields.json` 是**冻结的导出契约**，要求 5,893 行逐行/顺序一致。
T4 采集头文件后 `frames` 会增加 D2–D6 帧，如果直接从 `frames` 导出 fields.json，
旧契约会漂移。因此 `ingest_fields_json` 同时写一份 `fields_snapshot`
（seq/obsid/detector/ra/dec/mjd/lat/lon/elon/elat/xposure），`export_fields`
优先读快照；新帧仍可经 `/api/cone` 或 `/api/v1/epochs` 查询。
这不是第二事实源：快照是导入时 `fields.json` 的物化，且可由同一文件重建。

## 4. 导入流程

```
周索引 → 目标 → 头文件 → 历元目录 → 切割缓存 → 兼容字段索引 → 回填 → 汇总
```

- 每一步是一个 `ingest_runs` 记录；失败写 `status='failed'` 与错误摘要。
- 头文件每 500 帧提交一次；`blob` 与 `frame` 在同一事务，避免“有帧没头”。
- 同一文件重复导入：`ON CONFLICT` 更新；`frames` 用 `(obs_id, detector)` 判断
  inserted/updated，供审计。
- release 回填：`weeks` 里的 `(week → release)` 映射；头文件导入时先查映射，
  后导入周索引时再 `backfill_releases()` 补。
- `refresh_week_counts()` 用索引化的相关子查询更新 `n_frames/mjd_min/max`，
  不维护触发器（导入时触发器会拖慢批量写）。
- 切割缓存后到时，`relink_cutouts()` 按 `(obs_id, detector)` 重新挂接。

### 失败与恢复

- 单个头文件解析失败：记 `errors`，继续下一条；不会回滚整批。
- 进程中断：已提交批次保留；重跑幂等。
- Blob 写成功、DB 事务失败：留下孤儿 Blob；`gc` 会清理。
- DB 有引用、Blob 丢失：`verify --deep` 抽样报告 `missing_blobs`；重新导入可补。

## 5. 查询层

`Catalog` 是唯一的读入口，HTTP/CLI/客户端都复用它：

- `cone()`：精确大圆距离；支持 detector/release/mjd/order/cutouts；单页上限 2,000，
  候选上限 200,000，超出标 `truncated`。
- `coverage()`：默认按 **2.47°**（单片半对角线）搜索；`n_epochs` 按 obsid 去重，
  给出 `coverage_level`（C0–C3）。旧 `readiness`（L0–L3）保留但标 deprecated。
- `epochs_v1()`：契约 v1；`target_id` 与 `ra/dec` 两个入口同形；0.05° 内自动匹配目标，
  优先 curated `target_epochs`，否则按 radius 锥搜索 frames 兜底；每 obsid 一条 DTO，
  `date_obs` 缺失时由 MJD 反推，`slice_url.size=fov`。
- `epochs_for_target()`：旧内部方法，保留给 CLI/导出；优先 `target_epochs`。
- `stats()`：计数、时间范围、release/detector 分布、Blob 压缩率、最近导入；
  额外给出 `header_frames`、`frames_with_cutouts`。
- `health()`：`schema_version` 是 API 契约版本（"1.0"），`db_schema_version` 是 SQLite 版本（v6）。

### date_obs 与审计

- `fields.json` 没有 date_obs；`backfill_date_obs()` 用
  `datetime.fromtimestamp((mjd-40587)*86400, UTC)` 回填 frames / target_epochs / observations。
- API 层再兜底一次（`_frame_row`/`_curated_epochs`），保证响应永不为 null。
- 导入审计：`bytes_read` 记录源文件真实字节；`rows_inserted` 与 `rows_updated` 分开计数
  （此前 upsert 全算 inserted，导致 weeks 1,208 vs 实际 151 的膨胀）。
- 失败批次写 `status=failed` + 错误摘要；单个头文件解析失败只记 error，不中断整批。

### 头文件采集（T4）

`tools/harvest_headers.py` 只发 `Range: bytes=0-65535`，不下载整幅 FITS：

- `--source epochs` 从 DB `target_epochs` 取 74 条 key；`--source weeks` 从 S3 列 key；
- 可读文件名 + manifest（key/url/sha256/bytes/status）；原子写；
- `--resume` 先比 manifest sha256 与本地文件，哈希一致 0 下载；损坏自动重采；
- 404 跳过一次，429/5xx 退避重试；本地假 S3 契约测试覆盖 Range/重试/幂等/损坏缓存；
- 采完调用 `Ingestor.ingest_headers()` + `relink_epochs()`，回填 `header_sha256/date_obs/
  pixel_scale/crpix/psf/l2dqa`，不写 app 科学逻辑。

读连接统一 `PRAGMA query_only=ON`；HTTP 每线程一条连接，避免 `check_same_thread` 问题。

## 6. 规模分析

- 元数据：每帧约 200 B（含索引），10^7 帧约 2–3 GB；SQLite 可以处理。
- Blob：45 KB 纯头样本压缩后约 8 KB（0.18）；T4 采集的 64 KB 文件含头后二进制，
  实测 74 份 4.85 MB → 2.97 MB（0.61，约 40 KB/份）。10^7 份按 40 KB 估约 400 GB，
  若只存纯头则约 80 GB；文件系统可以处理。
- 查询：zone 索引把候选限制在带内；实测 5,893 行 p95 < 0.6 ms，
  200k 行 p95 1.1 ms（r=0.5°），1M 行见 `benchmarks/scale_report_1m.json`。
- 写入：批量事务下 200k 帧 11.8 s（含 observations），主要成本是 SQLite 页写，
  不是 Python 解析；真实头文件还多一项 zlib 压缩。

## 7. 安全与运维

- API 默认 `127.0.0.1`，只读，无写接口；CORS 允许 `*` 是为了本地前端跨端口调试。
- 公网部署应加反向代理、鉴权、限流；不要直接暴露 SQLite 文件。
- `backup` 做一致性快照；Blob 用 `rsync`，两者必须一起备份。
- `vacuum` 会重写主库，运行前停止导入；`gc` 会删无引用 Blob，先 `--dry-run`。
- 版本升级：`db/schema.py` 的 `migrate()` 只做可空列/索引的增量变更；
  大改版建议新建库并重新导入（当前全量导入 < 1 分钟）。

## 8. 不做的事

- 不做图像重投影/测光/运动拟合（MoveScope 的 `core/` 负责）。
- 不做前端三维地球/天图（视图工具的 `js/` 负责）。
- 不在服务端实时下载 IRSA 数据；那是 MoveScope `data.py` 的职责。
- 不把合成基准库混进 `var/catalog.sqlite`。
