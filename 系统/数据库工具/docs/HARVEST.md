# T4 头文件采集器 / Header Harvester

> 工具：`系统/数据库工具/tools/harvest_headers.py`
> 测试：`系统/数据库工具/tests/test_harvest_headers.py`
> 分支：`tool/t4-harvest`；只采头，不下载整幅 FITS、不采像素。

## 1. 做什么

用 S3 匿名 **Range** 只读每个 level2 文件的前 **64 KB**（`bytes=0-65535`），
把 FITS 头落成 `*.hdr` + `header_manifest.json`，再调用数据库的
`Ingestor.ingest_headers()` 回填：

- `frames.header_sha256` / `pixel_scale_arcsec` / `crpix1/2` / `sip_order` /
  `psf_fwhm_arcsec` / `l2dqa` / `naxis1/2`；
- `frames` / `target_epochs` / `observations.date_obs`；
- `target_epochs.frame_id`（`relink_epochs()`）。

SPHEREx IMAGE 头约 25–46 KB，64 KB 足够；65 MB 的 IMAGE 像素数据永远不会被请求。

```text
target_epochs / week_index / keys
        │  plan（只读 DB / S3 List）
        ▼
   HarvestItem[] ── Range 206 + read(65536) ──► extract_record 校验
        │                                           │ 通过
        │                                           ▼
        │                                   原子写 <out>/*.hdr
        ▼                                           │
 manifest.json（合并） + <manifest>.failures.json   │
                                                    ▼
                     Ingestor.ingest_headers(out) → relink → backfill_date_obs()
```

## 2. 安全边界（审计 P0 对应）

| # | 约束 | 实现 |
|---|---|---|
| P0-1 | 服务器忽略 Range 时不能读整幅 65 MB | 先判 HTTP 状态：**200 直接 failed，不读 body**；非 206 也 failed；只有 206 才 `resp.read(65536)` |
| P0-2 | 坏头不能落盘 | 写盘前调用 `db.fitshdr.extract_record()`；缺 `OBSID`/`DETECTOR`/`CRVAL`/MJD 或无法解析 → `failed`，不写、不覆盖已有好文件 |
| P0-3 | fresh 后 `date_obs` 不能为空 | `ingest_headers()` 末尾调用 `ing.backfill_date_obs()`，返回 `date_obs_backfilled`；并区分头 DATE-OBS / MJD 来源 |
| P0-4 | 计划阶段不能写库 | `plan_epochs()` 用 URI `mode=ro` + `PRAGMA query_only=ON` 打开数据库 |
| P0-5 | 不重命名历史缓存 | manifest 里已有 `file` 的 key 沿用其 **basename**（目录取当前 `--out`）；新文件命名不带重复 week |
| P0-6 | 失败可恢复 | 失败清单默认原子写 `<manifest>.failures.json`；`--resume` 优先复用 manifest 的 `file` + `sha256` |

原子写统一为「同目录临时文件 → `flush`/`fsync` → `os.replace`」，失败不留半截
`.hdr` / JSON。404 不重试；429/5xx/超时按 `--backoff` 指数退避重试。

## 3. 用法

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
cd 系统/数据库工具

# 计划（只读 DB，不写文件、不联网下载）
$PY tools/harvest_headers.py --source epochs --dry-run

# 采 74 条历元 + 入库
$PY tools/harvest_headers.py --source epochs \
  --out ../app/data/header_cache \
  --manifest ../app/data/header_manifest.json \
  --workers 8 --retries 3

# 幂等重跑：0 新下载；已缓存文件按 manifest sha256 校验
$PY tools/harvest_headers.py --source epochs --resume

# 只采不 ingest
$PY tools/harvest_headers.py --source epochs --no-ingest

# Phase 2：按周/探测器列举 S3
$PY tools/harvest_headers.py --source weeks --weeks 2026W40_2A --detectors 1,2,3,4,5,6

# 手工补采
$PY tools/harvest_headers.py --source keys --keys-file /tmp/keys.txt
```

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--source` | `epochs` | `epochs`（DB `target_epochs`）/ `weeks`（周索引+S3 列举）/ `keys` |
| `--db` / `--blobs` | `var/catalog.sqlite` / `var/blobs` | 计划只读；ingest 阶段才写 |
| `--out` | `系统/app/data/header_cache` | `.hdr` 目录 |
| `--manifest` | `系统/app/data/header_manifest.json` | 合并式清单 |
| `--failures` | `<manifest>.failures.json` | 失败清单；默认原子写 |
| `--workers` / `--retries` / `--backoff` / `--timeout` | 8 / 3 / 1.5 / 60 | 并发与重试 |
| `--limit` | 0 | 调试 |
| `--dry-run` | 关 | 只打印计划（不写 manifest、不联网） |
| `--resume` | 关 | 先比对 manifest `sha256` 与本地文件，命中则 0 下载 |
| `--no-ingest` | 关 | 只采不写 DB |

## 4. 命名、manifest 与失败清单

新文件命名（`obsid` 已含 week，不再重复）：

```text
{release}_{obsid}_D{detector}_{version}.hdr
dr1_2025W35_2A_0221_1_D4_l2b-v27-2026-231.hdr
```

- 历史缓存（例如旧命名 `dr1_2025W35_2A_2025W35_2A_0221_1_D4_...hdr`）不会改名：
  只要 manifest 里有该 key 的 `file`，就沿用其 basename。manifest 里的历史绝对路径
  可能指向迁移前目录（`/.../huellaX/app/data/...`），因此**只取文件名**，目录用当前
  `--out`。
- 不同 key 生成同名时，用 `sha1(key)[:8]` 消歧；不会覆盖。

`header_manifest.json` 摘要：

```json
{
  "summary": {"entries": 74, "ok": 0, "cached": 74, "failed": 0, "bytes_total": 4849664},
  "last_run": {"planned": 74, "fetched": 0, "cached": 74, "failed": 0},
  "entries": [
    {"key": "...", "file": "...", "sha256": "...", "bytes": 65536,
     "status": "ok|cached|failed", "error": null, "targets": ["barnard"]}
  ]
}
```

每次运行都写 `<manifest>.failures.json`（0 失败也写 `count: 0`，避免旧失败清单误导）：

```json
{"generated_at": "...", "count": 1,
 "entries": [{"key": "...", "status": "failed", "error": "HTTP 404", "file": "..."}]}
```

## 5. 入库与 date_obs 语义

`ingest_headers()`（模块级函数）返回：

| 字段 | 含义 |
|---|---|
| `headers` | `Ingestor.ingest_headers()` 统计（files_seen / files_ingested / errors / bytes_read …） |
| `epochs_linked` | `target_epochs.frame_id IS NOT NULL` 数量 |
| `cutouts_linked` | 本次重新挂到 frames 的 cutout 行数 |
| `date_obs_from_header` | 缓存中头有非空 `DATE-OBS` 的帧数（按 `(obsid, detector)` 去重） |
| `date_obs_from_mjd` | 缓存中头没有 `DATE-OBS`、入库时用 `MJD-OBS/MJD-AVG` 生成日期的帧数 |
| `date_obs_backfilled` | 末尾 `backfill_date_obs()` 实际更新的行数（frames + target_epochs + observations 合计） |

注意：`Ingestor._upsert_frame()` 在写帧时已经会用 `DATE-OBS` 或 MJD 生成
`date_obs`；`date_obs_backfilled` 只统计**仍为空、靠 MJD 补**的行（例如先导入
fields/epochs、后采头的旧行）。因此正常 74 条新采头、且已导入 fields/epochs 的库，
`date_obs_backfilled` 可能为 0；fresh 库或空列库会大于 0。

## 6. 验证

离线单测（本地假 S3，不碰生产库）：

```bash
cd 系统/数据库工具
$PY -m unittest discover -s tests -t . -v
```

覆盖：Range 200 大 body 不读/206 读取上限、坏头不落盘且不覆盖、只读计划、
新命名、manifest 历史文件名复用、失败清单原子写、date_obs 双来源与 MJD 回填、
404/429/500/超时/原子写/幂等。

临时库验收（不写生产 DB）：把 `var/catalog.sqlite` + `var/blobs` 复制到
`/tmp/t4-*`，或用 `app/data/*.json` 建 fresh 库，再对本工具跑
`--no-ingest` / `ingest_headers`；核对：

```sql
select count(*) from frames where header_sha256 is not null;              -- 74
select count(*) from target_epochs where frame_id is not null;            -- 74
select count(*) from target_epochs;                                       -- 74
select count(*) from frames where date_obs is null or date_obs='';        -- 0
select count(*) from blobs where kind='fits-header';                     -- 74
```

生产库只允许只读核对（`mode=ro`），不要用本工具直接改生产库。

## 7. 不做 / 风险

- 不下载整幅 FITS、不采像素、不写 app/DB 科学逻辑；只喂头级元数据。
- 不对真实 S3 做 CI；单测全部用本地假 S3。真实网络 429/限流时降 `--workers`。
- 服务器若返回 200，本工具选择失败而不是“读一点点”；这是刻意的安全取舍。
- `.hdr` 是网络缓存，可删除后用 `--resume` 重采；manifest 可保留用于核对
  `sha256`/采集时点（缓存目录建议 gitignore）。
- Phase 2 全量 151 周未在本分支执行；`--source weeks` 已实现且由单测覆盖列举/过滤。
