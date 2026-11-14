# 数据库工具 · SPHEREx 数据层

> **给 MoveScope（`app/`）和视图工具（`视图工具/`）共用的一组本地数据库。**
> 元数据进 SQLite，原始头与切割产品进内容寻址 Blob 仓库；支持增量导入、天区锥搜索、
> 覆盖/就绪度、HTTP API，以及两个项目现有 JSON 格式的兼容导出。

当前库：**5,893 帧 / 5,562 次曝光 / 74 份 FITS 头 / 74 条目标历元 / 101 个切割产品 / 7 个目标**，
时间范围 **MJD 60789.75 – 61316.53（约 527 天）**，库文件约 5.4 MB，Blob 仓库 16.9 MB。

> 头文件状态：74 条 `target_epochs` 已用 T4 采集器（S3 Range 64 KB）回填，
> `header_frames=74`、`target_epochs.frame_id=74/74`、`date_obs` 缺失 0。
> `header_cache/` 是网络缓存，可删除后用 `tools/harvest_headers.py --resume` 重采。

---

## 1. 为什么需要它

两个项目原本各管一摊数据：

| 项目 | 数据形态 | 问题 |
|---|---|---|
| MoveScope `app/` | `targets.json`、`epoch_catalog.json`、`week_index.json`、`epochcache/*.npz`、`header_cache/*.hdr` | JSON 各自加载；头缓存 610 MB / 14k 文件；没有按天区检索；缓存与目录靠文件名约定 |
| 视图工具 `视图工具/` | `data/fields.json`（5,893 行紧凑数组）、`data/targets.json` | 每次全量重建；无法增量；与 MoveScope 的历元/目标数据重复维护 |

SPHEREx 的数据量只会更大：6 个探测器、102 个波段、每 6 个月扫全天，
单个 level2 文件约 65 MB，切片后仍约 4 MB。真正需要被反复查询的是**元数据**
（谁在何时拍了哪里、处理版本、覆盖了哪个目标），而不是每次都把像素重新读一遍。

这个工具的边界很清楚：

- **是**：统一元数据目录、天区检索、覆盖统计、切割产品索引、兼容导出、只读 API。
- **不是**：不替代 MoveScope 的测光/运动拟合，不存整幅 65 MB 原始 FITS，
  不做图像渲染（那是两个前端的事）。

---

## 2. 快速开始

```bash
cd 数据库工具
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3

$PY cli.py init                 # 建库（var/catalog.sqlite + var/blobs）
$PY cli.py ingest all --root .. # 从 ../app 与 ../视图工具 导入现有数据
$PY cli.py stats                # 看计数、时间范围、release、存储
$PY cli.py query coverage --ra 269.452077 --dec 4.693365
$PY cli.py query cone --ra 269.452077 --dec 4.693365 --radius 2.47 --limit 10
$PY tools/harvest_headers.py --source epochs --resume   # 回填 74 条头（幂等）
$PY tools/check_compat.py --root ..                     # 导出的 JSON 与两个项目逐项对账
```

启动 API：

```bash
./start.sh 8797
# → http://127.0.0.1:8797/
# → http://127.0.0.1:8797/api/health
```

> 头文件采集用 `tools/harvest_headers.py`（只读 64 KB，不下载整幅 FITS）。
> 重跑 `--resume` 时 0 新下载；manifest 在 `../app/data/header_manifest.json`，
> 记录每条 key/sha256/bytes/status。

---

## 3. 架构

```
             参考资料（SPHEREx 访问事实）
                        │
   app/data/*.json ─────┼──── 视图工具/data/*.json
   app/data/epochcache  │
   app/data/header_cache│
                        ▼
              ┌──────────────────┐
              │  db/ingest.py    │  解析 / 去重 / 批量事务 / 断点续跑
              └────────┬─────────┘
                       │
        ┌──────────────┴────────────────┐
        ▼                               ▼
┌──────────────────┐          ┌────────────────────────┐
│ catalog.sqlite   │          │ var/blobs/ab/cd/<sha>.z│
│ 元数据 + 索引      │◄─sha256─►│ 45 KB 头 / NPZ 切割     │
│ WAL / zone 索引   │          │ zlib 压缩 / 内容去重     │
└────────┬─────────┘          └────────────────────────┘
         │
   ┌─────┴──────────────┬─────────────────┐
   ▼                    ▼                 ▼
db/catalog.py       db/export.py      db/server.py
锥搜索/覆盖/统计     兼容 JSON 导出     只读 HTTP API
   │                    │                 │
   └────────► MoveScope / 视图工具 ◄──────┘
```

关键取舍：

- **SQLite 而不是服务器**：单文件、零安装、WAL 支持“一边导入一边查询”，
  5,893 行的真实库约 5.4 MB；200k/1M 行的规模基准见 §6。
- **元数据与像素分离**：SQLite 不存 45 KB 头，也不存 4 MB FITS；只存 sha256
  与可检索字段。Blob 仓库按内容寻址，天然去重，能长到 TB 级。
- **zone + RA 索引**：赤纬每 0.5° 一条带，先矩形粗筛，再用大圆距离精确过滤。
  这是 SDSS 用过多年的方案，规模上去仍有效；不依赖 SQLite 的三角函数扩展。
- **release 不放进帧主键**：同一 `obs_id` 可能同时存在于 `qr3` 和 `dr1`。
  `frames` 保留一行规范帧，`frame_releases` 记录所有处理版本，
  需要严格时序光度时按 release 过滤。

---

## 4. 数据模型

| 表 | 一行是什么 | 关键列 |
|---|---|---|
| `frames` | 一次曝光的一个探测器 | `obs_id, detector, ra, dec, mjd, sgt_lat, sgt_lon, zone, header_sha256` |
| `observations` | 一次曝光（D1–D6 共享） | `obs_id, week, field_no, repeat_no, mjd, release` |
| `weeks` | 一个观测周 / 处理版本 | `release, week, version, mjd_obs, first_key, n_frames, mjd_min/max` |
| `targets` | 官方/样本目标 | `id, ra, dec, truth_json, official` |
| `target_epochs` | 某目标的一个候选历元 | `target_id, frame_id, slice_url, sep_deg, fits_key` |
| `cutouts` | 已下载的 NPZ/FITS 切片 | `obs_id, detector, frame_id, sha256, shape, fov_deg` |
| `fields_snapshot` | 视图工具 `fields.json` 的冻结快照 | `seq, obsid, detector, ra, dec, mjd, lat, lon, elon, elat, xposure` |
| `blobs` | 一个内容寻址文件 | `sha256, codec, raw_bytes, stored_bytes, rel_path` |
| `frame_releases` | 帧 × 处理版本 | `frame_id, release, procver, source_key` |
| `ingest_runs` | 一次导入的审计记录 | `kind, status, rows_inserted, errors, elapsed` |

关系：

```
weeks 1 ─── n observations 1 ─── n frames 1 ─── n cutouts
                                   │
targets 1 ─── n target_epochs ─────┘（frame_id 可空：目录先到、帧后到）

frames.header_sha256 ─── blobs.sha256
cutouts.sha256       ─── blobs.sha256
```

完整 DDL 见 [`schema.sql`](schema.sql)；源定义在 `db/schema.py`，当前 schema v6。
`schema.sql` 只读副本，改表请改 `db/schema.py` 后重新生成。

---

## 5. 两个项目怎么接

### 5.1 MoveScope

数据库可以只作为“离线物化视图”，完全不改 MoveScope 的运行方式：

```bash
cd 数据库工具
$PY cli.py export all --out var/exports
# 需要时替换（建议先备份）
#   var/exports/epoch_catalog.json  -> ../app/data/epoch_catalog.json
#   var/exports/targets.json        -> ../app/data/targets.json
#   var/exports/week_index.json     -> ../app/data/week_index.json
```

兼容性已实测：`fields.json` 5,893 行逐行一致（含顺序）、`epoch_catalog.json` 74 条一致、
`targets.json` 7 个一致、`week_index.json` 151 个周一致。可随时重跑：

```bash
$PY tools/check_compat.py --root .. --exports var/exports
```

导出的 `epoch_catalog.json` 保留 MoveScope 现有的字段：
`release / week / version / field / detector / key / obsid / mjd / date_obs / sep_deg / target_id / slice_url_002`。
`data.py` 的 `slice_url()` 仍能从 `key` 拼出 IRSA 地址，不需要改一行代码。

运行时走 **契约 v1**（两个入口同形，app 的 T2 工作包直接消费）：

```bash
curl 'http://127.0.0.1:8797/api/v1/epochs?target_id=barnard'
curl 'http://127.0.0.1:8797/api/v1/epochs?ra=269.452077&dec=4.693365&fov=0.25'
curl 'http://127.0.0.1:8797/api/v1/coverage?ra=269.452077&dec=4.693365'
curl 'http://127.0.0.1:8797/api/cone?ra=269.452077&dec=4.693365&radius=2.47&limit=50'
```

v1 的硬规则：

- 每个 `obsid` 一条 `EpochRef`；同一曝光的 D1–D6 只保留离查询点最近的一条；
- `date_obs` 永不为 null（缺失由 MJD 反推 UTC）；`key` 与 `slice_url` 必填；
- `slice_url` 的 `size` 等于查询 `fov`；
- `target_id` 与 `ra/dec` 两入口返回同一组历元（0.05° 内自动匹配目标）；
- 默认覆盖半径 **2.47°**（单片半对角线），等级用 **C0–C3**，不是 app 的 L0–L3。

旧 `/api/epochs` 仍保留，但已标 `deprecated: true`，内部委托 v1；
APP 不应再把旧接口当作稳定契约。

### 5.2 视图工具

`视图工具/data/fields.json` 的 10 列数组格式可以直接由数据库重新生成：

```bash
cd 数据库工具
$PY cli.py export fields --out var/exports
# var/exports/fields.json -> ../视图工具/data/fields.json
```

> 为保证导出契约不因 T4 新增 D2–D6 帧而漂移，`fields.json` 从导入时冻结的
> `fields_snapshot` 导出（当前 5,893 行）；新增帧通过 API/cone 提供，不改变旧文件。

它比现在的 `build_field_index.py` 多出的能力：

- **增量**：新头文件只需 `ingest headers`，不用全量重扫 14k 文件；
- **可查询**：可以直接问 API，而不是让前端把 5,893 行全部加载；
- **可追溯**：每行都能回到 `frames` 与 `blobs.sha256`，头文件缺失时也有明确来源。

也可以只把视图工具当 API 客户端：

```bash
curl 'http://127.0.0.1:8797/api/cone?ra=160.617&dec=30.67&radius=3.5&limit=200'
```

### 5.3 两者共用同一个库

- 目标坐标只维护在 `targets` 表；两个项目通过导出/接口取。
- 历元候选只维护在 `target_epochs`；MoveScope 不再手写 JSON。
- **覆盖等级 C0–C3** 由数据库给（拍过几次/跨多久）；**分析就绪度 L0–L3** 由
  MoveScope 测光管线给（测到几期/够不够判）。两者不复用同一个字段；
  旧 `readiness` 字段保留但标 `deprecated`。

### 5.4 头文件采集（T4）

数据库的 `frames` 里已有 74 份 FITS 头（只读 64 KB，不下载整幅 65 MB）：

```bash
$PY tools/harvest_headers.py --source epochs --dry-run   # 计划 74 条
$PY tools/harvest_headers.py --source epochs --resume    # 采集 + ingest
```

- `--source epochs`：从 DB 的 74 条 `target_epochs` 取 key；
- `--source weeks`：按 `week_index.json` 的周/探测器从 S3 列 key（Phase 2，可选）；
- `--source keys`：手工清单；
- manifest：`app/data/header_manifest.json`（key/url/sha256/bytes/status），写入原子文件；
- 重跑 `--resume`：哈希一致则 0 新下载；损坏缓存自动重采；
- 只写 `app/data/header_cache/` 与 manifest，不改 app 代码/科学逻辑。

---

## 6. 大量数据怎么处理

| 机制 | 做法 | 效果 |
|---|---|---|
| 批量事务 | 头文件每 500 帧提交一次；NPZ 一次事务 | 14k 头文件不会 14k 次 fsync |
| WAL | `journal_mode=WAL` + `synchronous=NORMAL` | 导入时 API 仍可只读查询 |
| 流式 Blob | 读一块压缩一块，内存与单文件大小无关 | 65 MB FITS 也只占一个块的内存 |
| 内容去重 | 文件名 = 明文 SHA-256，两级目录 | 同一头/切片只存一份 |
| 压缩 | FITS 头 zlib-6；不可压缩时存原样 | 45 KB → 约 8 KB（实测样本切片前 45 KB，压缩率 0.18） |
| 分区索引 | `zone`（0.5° 赤纬带）+ `(zone, ra)` | 锥搜索只扫邻近带 |
| 分页 | `limit/offset`，单页上限 2,000 | 大圆盘查询不会打爆内存 |
| 幂等 | 唯一键 + upsert | 重复导入只更新，不重复 |
| 审计 | `ingest_runs` 记录每次导入 | 出错能定位到哪一批 |
| 维护 | `verify` / `gc` / `vacuum` / `backup` | 完整性、回收、压实、快照 |

### 实测（本机、只读 SQLite、页缓存预热）

真实库（5,893 帧）的锥搜索 p95 **< 0.6 ms**；v1 历元查询含去重/目标匹配，通常 1–3 ms。规模基准是**合成数据**，
明确与生产库隔离（`var/bench_scale_1m.sqlite`，不是真实观测）：

| 数据量 | 生成耗时 | 库大小 | 锥搜索 p95（r=0.5°） | 1° 锥搜索 p95 |
|---|---|---|---|---|
| 200,000 帧 | 11.8 s | 118 MB | 1.13 ms | 2.03 ms |
| 1,000,000 帧 | 76.3 s | 590 MB | 8.20 ms | 16.16 ms |

结论：查询成本主要由“命中多少帧”决定，而不是总行数；zone 索引把全表扫描
变成了常数级别的带内扫描。10^7 行元数据仍在 SQLite 的舒适区，像素继续放 Blob 仓库。

---

## 7. 运维

```bash
# 增量更新（新一周的头文件/处理版本）
$PY cli.py ingest weeks --file ../app/data/week_index.json
$PY tools/harvest_headers.py --source epochs --resume   # 或 weeks/keys
$PY cli.py ingest headers --dir ../app/data/header_cache

# 只导入目标/历元/切割
$PY cli.py ingest targets --file ../app/data/targets.json
$PY cli.py ingest epochs  --file ../app/data/epoch_catalog.json
$PY cli.py ingest cutouts --dir ../app/data/epochcache

# 体检
$PY cli.py verify --deep
$PY cli.py gc --dry-run
$PY cli.py gc
$PY cli.py vacuum
$PY cli.py backup --out var/backup/catalog-$(date +%Y%m%d).sqlite
```

备份说明：`backup` 只做 SQLite 一致性快照；`var/blobs/` 是普通文件，
用 `rsync -a var/blobs/ <备份盘>/blobs/` 即可。两者要一起备份，否则 sha256 会悬空。

---

## 8. 目录

```
数据库工具/
├── cli.py                 统一命令行（init/ingest/query/export/serve/...）
├── client.py              本地/HTTP Python 客户端
├── schema.sql             schema 只读副本
├── start.sh               启动只读 API（默认 8797）
├── db/
│   ├── config.py          路径、zone、分页等常量
│   ├── schema.py          DDL + 迁移
│   ├── connection.py      WAL/PRAGMA/事务
│   ├── fitshdr.py         FITS 头解析（含 HIERARCH）
│   ├── blobs.py           内容寻址存储 + zlib + GC
│   ├── catalog.py         锥搜索/覆盖/历元/统计
│   ├── ingest.py          所有数据源导入 + 回填
│   ├── export.py          两个项目的兼容 JSON
│   ├── server.py          只读 HTTP API + 首页
│   └── bench.py           查询基准
├── tools/
│   ├── import_all.sh          一键导入
│   ├── check_compat.py        导出 JSON 与两个项目逐项对账
│   ├── run_tests.sh           跑全部测试
│   ├── harvest_headers.py     T4 头文件采集器（S3 Range 64 KB）
│   └── make_scale_fixture.py  合成规模基准库（不写生产库）
├── tests/                 54 个单元/接口测试（含 T4 离线假 S3 契约）
├── benchmarks/            真实库与规模库的基准报告
├── docs/
│   ├── DESIGN.md          设计与取舍
│   ├── API.md             接口与客户端
│   └── INTEGRATION.md     两个项目的接入步骤
└── var/
    ├── catalog.sqlite     生产库（可直接用）
    ├── blobs/             内容寻址仓库
    └── exports/           兼容 JSON 输出
```

---

## 9. 边界与已知限制

1. **这是元数据/索引库，不是像素库。** 默认不保存整幅 65 MB FITS；只保存
   64 KB 头（T4 采集）和已下载的 NPZ/FITS 切片。需要整幅时按 `slice_url` 回源 IRSA。
2. **覆盖判据是“帧中心距”**，默认 **2.47°**（单片半对角线，`radius` 可覆盖，
   范围 0.2–5°）；不是逐像素多边形求交，大倾角扫描带会偏保守。
3. **单写者。** SQLite 允许多读，但同一时刻只建议一个导入进程；导入进程与 API
   可以并行（WAL）。
4. **不认证。** API 默认只绑 `127.0.0.1`，面向本机/内网演示；公网部署需加反向代理与鉴权。
5. **release 混用要显式过滤。** 同一目标的 `qr3` 与 `dr1` 处理版本不同，
   做时序光度时用 `?release=dr1` 或导出时只取一版；DB 保留 `frame_releases` 供追溯。
6. **样本数据不伪装。** `var/bench_scale*.sqlite` 是合成基准库，命名和文档都标明；
   生产库只接收真实头/切片/公开目录。
7. **头文件是网络缓存。** `app/data/header_cache/` 可删可重采；manifest 的 sha256
   是“这份头是否已采过”的唯一凭据，删缓存后 `--resume` 会重新下载 74 条。

---

## 10. 参考资料与数据来源

- SPHEREx level2 数据：NASA/IPAC IRSA，QR3 / DR1；静态切片与 S3 公开桶路径见
  `参考 资料/03_SPHEREx数据访问笔记.md`。
- 赛道与验收目标：`参考 资料/01_挑战原文.md`、`04_测试目标天体.md`。
- 单个 level2 文件约 65 MB、7 个 HDU（PRIMARY/IMAGE/FLAGS/VARIANCE/ZODI/EPSF/WCS-WAVE）、
  6 个探测器、102 波段、像素约 6.15″——这些事实直接决定了本库的列选择与切片策略。
- 目标坐标：SIMBAD（2026-11-14 查询），`app/data/targets.json`。
- 交互范式参考：WiseView（Backyard Worlds: Planet 9）。

数据许可遵循 IRSA / NASA 公开数据条款；本项目只做本地索引与缓存，不重新分发原始观测。
