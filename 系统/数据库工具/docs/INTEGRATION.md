# 接入 MoveScope 与视图工具

目标：不破坏两个项目现在的运行方式，先让数据库成为**可选的单一数据源**。
所有替换都是“生成同形状 JSON”，随时可以回滚。

---

## 0. 先决条件

```bash
cd 数据库工具
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY cli.py init
$PY cli.py ingest all --root ..
$PY cli.py stats
```

确认 `counts.frames > 0`。当前库是 5,893 帧 / 74 份 FITS 头 / 74 条历元；
若 `app/data/header_cache/` 不存在，先运行 T4 采集器：

```bash
$PY tools/harvest_headers.py --source epochs --resume
```

`ingest all` 总是从 `视图工具/data/fields.json` 导入 5,893 行基础元数据；
头文件采集会补齐 D2–D6、`date_obs`、像素尺度、CRPIX、PSF、DQA。

---

## 1. MoveScope（`app/`）

### 1.1 离线替换 JSON（零代码改动）

```bash
cd 数据库工具
$PY cli.py export all --out var/exports

cd ..
mkdir -p app/data/_backup_$(date +%Y%m%d)
cp app/data/epoch_catalog.json app/data/_backup_$(date +%Y%m%d)/
cp app/data/targets.json       app/data/_backup_$(date +%Y%m%d)/
cp app/data/week_index.json    app/data/_backup_$(date +%Y%m%d)/

cp 数据库工具/var/exports/epoch_catalog.json app/data/epoch_catalog.json
cp 数据库工具/var/exports/targets.json       app/data/targets.json
cp 数据库工具/var/exports/week_index.json    app/data/week_index.json

cd app
$PY server.py --port 8790
```

验收点：

- `app/core/data.py` 的 `load_targets()` / `load_epoch_catalog()` 不需要改；
- `epoch_catalog.json` 里每条仍有 `release/week/version/detector/obsid/mjd/date_obs/sep_deg/key`；
- `data.py` 的 `slice_url()` 仍能从 `key` 拼出 IRSA 地址；
- 目标数量仍为 7（5 官方 + 2 样本）。

回滚：把 `_backup_*` 里的三个文件复制回去即可。

### 1.2 走 HTTP（可选）

如果不想让 MoveScope 读本地 JSON，可以加一层薄适配：

```python
# 伪代码：在 app/core/data.py 的 custom 分支里调用契约 v1
import json, urllib.request
def load_epochs_v1(ra, dec, fov=0.25, base="http://127.0.0.1:8797"):
    url = f"{base}/api/v1/epochs?ra={ra}&dec={dec}&fov={fov}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())
```

- v1 返回 `{"schema_version","query","matched_target_id","coverage","epochs","count"}`；
- 每个 `EpochRef` 都有 `obsid/detector/key/date_obs/slice_url`，app 不再需要
  自己从 `fits_key` 拼路径；每条 `slice_url` 的 `size` 就是请求里的 `fov`；
- `matched_target_id` 命中 0.05° 内的目标时，app 可复用该目标 truth/coord_unc
  （`GET /api/v1/targets/<id>` 与 `app/data/targets.json` 逐字段一致，含 truth）；
  命中后返回的是该目标的 curated `target_epochs`（每个 obsid 一条，同曝光多探测器已合并）；
- `radius`（默认 2.47°）只影响未命中目标时的 frames 锥搜索与覆盖统计；
- 旧 `/api/epochs` 只是 v1 的兼容包装，**不要**再把它当稳定契约（`deprecated: true`）。

**app 侧 T2（custom 分支接线）由 `项目主控/提示词/04_T2_数据桥接.md` 负责；
本工作包只保证 DB 端 v1 契约可用。**

### 1.3 自定义坐标

MoveScope 的 `/api/analyze` 支持 `ra/dec/fov`。数据库对应：

```bash
curl 'http://127.0.0.1:8797/api/v1/epochs?ra=210.80227&dec=54.34895&fov=0.5'
curl 'http://127.0.0.1:8797/api/v1/coverage?ra=210.80227&dec=54.34895'
```

- `coverage_level` 是 C0–C3（覆盖机会），不是 app 的 L0–L3（检测质量）；
- 如果只有 1 期，`epochs.count=1`、`coverage_level=C0`；app 仍会走 HYBRID/SIM，
  并显式标 `INSUFFICIENT_EPOCHS`（由 T2 负责）；
- 0 条时 app 应标 `NO_LOCAL_COVERAGE`，不能伪装成“基线不足”。

---

## 2. 视图工具（`视图工具/`）

### 2.1 替换 `fields.json`

```bash
cd 数据库工具
$PY cli.py export fields --out var/exports

cd ..
cp 视图工具/data/fields.json 视图工具/data/fields.backup.json
cp 数据库工具/var/exports/fields.json 视图工具/data/fields.json

cd 视图工具
./start.sh 9000
```

`fields.json` 仍是同样的 10 列数组：

```
[obsid, detector, ra, dec, mjd, lat, lon, elon, elat, xposure]
```

视图工具的 `build_field_index.py` 可以暂时保留作为对照；后续新增头文件时只需：

```bash
cd 数据库工具
$PY tools/harvest_headers.py --source epochs --resume
$PY cli.py export fields --out var/exports
cp var/exports/fields.json ../视图工具/data/fields.json
```

不再需要全量重扫 5,893/14k 个文件。

### 2.2 走 API（可选）

视图工具现在把 `fields.json` 全量加载到浏览器。若以后数据量上去，可以改成：

```js
const res = await fetch("http://127.0.0.1:8797/api/cone?ra=" + ra + "&dec=" + dec + "&radius=3.5&limit=500");
const { frames } = await res.json();
```

API 的 `frames[]` 已经带 `ra/dec/mjd/sgt_lat/sgt_lon/elon/elat/xposure`，
正好对应 `fields.json` 的 10 列。

### 2.3 深链不变

视图工具对外的约定是：

```
index.html?ra=269.452&dec=4.693&fov=0.50
```

数据库的 API 也接受同样的 `ra/dec`，所以深链、复制 JSON、MoveScope 跳转都不受影响。

---

## 3. 数据新鲜度流程

推荐把“更新数据库”作为唯一入口，两个项目只读：

```bash
# 1) 新一周的头文件到达
cd 数据库工具
$PY cli.py ingest weeks --file ../app/data/week_index.json
$PY tools/harvest_headers.py --source epochs --resume
#    或用 --source weeks --weeks 2026W41_1A 采某一周

# 2) 新目标/历元
$PY cli.py ingest targets --file ../app/data/targets.json
$PY cli.py ingest epochs  --file ../app/data/epoch_catalog.json

# 3) 新切割缓存
$PY cli.py ingest cutouts --dir ../app/data/epochcache

# 4) 物化给两个项目
$PY cli.py export all --out var/exports
cp var/exports/fields.json ../视图工具/data/fields.json
# MoveScope 的三个 JSON 按 §1.1 复制（建议先备份）

# 5) 自检
$PY cli.py verify --deep
$PY cli.py stats
```

如果头文件缓存被清理：

```bash
cd 数据库工具
$PY tools/harvest_headers.py --source epochs --resume
```

数据库不需要重建；采集器只读 64 KB，按 `(obs_id, detector)` 增量补齐，
并自动 `relink_epochs()`/`relink_cutouts()`。manifest 的 sha256 是幂等凭据。

---

## 4. 版本与回滚

- API 契约版本在 `/api/health.schema_version`（"1.0"）；
  数据库 schema 在 `meta.schema_version` 与 `db_schema_version`（当前 v6）。
- 升级前：`$PY cli.py backup --out var/backup/catalog-before-v6.sqlite`；
  同时 `rsync -a var/blobs/ var/backup/blobs/`。
- 回滚 JSON：恢复 `app/data/_backup_*` 与 `视图工具/data/fields.backup.json`。
- 回滚数据库：用 `backup` 的 sqlite 文件替换 `var/catalog.sqlite`（停止 API/导入后）。

---

## 5. 验收清单

- [ ] `cli.py stats` 的 `frames/observations/weeks/targets` 均为正；
- [ ] `cli.py query coverage` 对 5 个官方目标返回合理 `coverage_level`（C0–C3）；
- [ ] `cli.py export all` 生成 4 个 JSON，形状与现有文件一致；
- [ ] MoveScope 能加载导出的 `epoch_catalog.json` 并跑一次分析；
- [ ] 视图工具能加载导出的 `fields.json`，曝光数与数据库 `frames` 一致；
- [ ] `cli.py verify --deep` 无 missing/corrupt；
- [ ] `curl /api/v1/epochs?target_id=barnard` 与 `ra/dec` 都返回 4 条；
- [ ] 5 个官方目标两入口一致：barnard 4 / epsind_b 10 / wise0855 10 / rw_aur 1 / r_dra 3；
- [ ] `curl /api/v1/targets` 返回 7 条，8 个契约字段齐全，`truth` 与 `app/data/targets.json` 逐字段一致；
- [ ] `curl /api/v1/coverage?ra=269.452077&dec=4.693365` 返回 `coverage_level=C2`、`header_available=true`；
- [ ] `curl /api/health` 正常，`counts.header_frames` 与库里实际有头的帧数一致；
- [ ] 备份/回滚路径演练过一次。
