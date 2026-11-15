# 星表服务 API 契约（阶段 B / D1）

> 独立星表服务：Gaia DR3 按场缓存、锥搜索、候选匹配。
> 端口 **8799**（不占 SPHEREx 生产服务的 8797）；数据库 **`var/star_catalog.sqlite`**
> （独立于生产库 `var/catalog.sqlite`）。
>
> 本文是 B 的对外契约，也是 D 分类层的输入说明。冲突时：
> - 星表数据/匹配语义以本文为准；
> - D 的 6 个顶层状态与科学分类以
>   `项目主控/架构/07_已知未知识别与数据比对设计.md` 为准；
> - A/B/C/D 分工见 `项目主控/架构/06_ABC任务分发.md`。

---

## 0. 一句话边界

**B 只回答“星表里有哪些源、与给定位置差多少、候选有几个”。**
B 不输出 `known_star / ambiguous / uncatalogued_candidate / unknown_*` 等科学分类，
不做 app/UI，不写 `var/catalog.sqlite`，不声称 Gaia 全量。
分类、视差传播、混合判定、`discovery_claim` 由 app 的 D 阶段完成。

B 的 `ambiguous` 是**几何歧义**（有效匹配半径内 ≥2 个星表候选），
不是 D 的状态机结论；两者同名但含义不同，见 §7。

---

## 1. 快速开始

```bash
cd 系统/数据库工具
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3

# 1) 联网 TAP 导入 7 个预设场（失败自动回退到真实 Gaia 行 fixture）
$PY tools/import_gaia.py --rebuild

# 2) 完全离线：只用 tests/fixtures/gaia_dr3_sample.csv（250 行，7 场）
$PY tools/import_gaia.py --rebuild --offline

# 3) 启动服务（只绑本机）
$PY catalog_server.py --port 8799

# 4) 示例
curl -s 'http://127.0.0.1:8799/api/v1/health' | python3 -m json.tool
curl -s 'http://127.0.0.1:8799/api/v1/catalog/cone?ra=269.452077&dec=4.693365&radius_deg=0.1&epoch=61210' \
  | python3 -m json.tool
curl -s -X POST 'http://127.0.0.1:8799/api/v1/catalog/match' \
  -H 'Content-Type: application/json' \
  -d '{"detections":[{"ra":269.44616498,"dec":4.76953633,"mjd":61210,"sigma_arcsec":0.3}]}' \
  | python3 -m json.tool

# 5) 测试（离线，不需要真实 7 MB 缓存）
$PY -m unittest tests.test_star_catalog -v
$PY -m unittest discover -s tests -t . -v
```

`/` 是只读 HTML 首页，`/api/docs` 直接返回本文。

---

## 2. 数据范围：17,679 源是“7 场 × 0.25° × G≤20”的按场缓存

| 项 | 值 |
|---|---|
| 主星表 | Gaia DR3（`gaiadr3.gaia_source`） |
| 预设场 | 7 个：barnard、epsind_b、wise0855、rw_aur、r_dra、m101、campo_a |
| 每场缓存半径 | **0.25°**（`FIELD_CACHE_RADIUS_DEG`） |
| 星等截断 | **G ≤ 20**（`GAIA_MAG_LIMIT`） |
| 真实 TAP 缓存行数 | **17,679**（7 场合计） |
| 场计数 | barnard 9,603；epsind_b 813；wise0855 1,210；rw_aur 4,684；r_dra 651；m101 458；campo_a 260 |
| 参考历元 | **J2016.0 = MJD 57388.5** |
| 实际 2MASS（IRSA Gator） | `2mass-irsa-gator`：**8,674** 源，7 场，provenance=`irsa_gator` |
| 实际 WISE（IRSA Gator） | `wise-irsa-gator`：**23,774** 源，7 场，provenance=`irsa_gator` |
| 派生 fallback | `2mass-derived-gaia-dr3` 8,278 / `wise-derived-gaia-dr3` 7,898 unique（来自 Gaia 交叉证认子集） |
| 本地缓存文件 | `var/star_catalog.sqlite`（约 7 MB，**不提交**） |

导入器对每个场先执行一次 `SELECT COUNT(*)` 校验，再拉带 2MASS/WISE join 的明细；
若 TAP 返回 HTTP 200 但唯一 `source_id` 数少于 COUNT（服务端高负载时可能静默截断），
会重试，仍不一致则抛 `GaiaTAPError` 并回退 fixture/中止，**不会把截断结果写成
`provenance=tap` + `complete=true`**。因此 17,679 是可复验的完整场缓存行数，
不是某次截断响应。

**17,679 不是 Gaia 全量。** Gaia DR3 有 1,811,709,771 个源；本服务只在 7 个预设场、
每个 0.25° 半径、G≤20 的范围内缓存，用于演示/验收天区。它也不是“全天天区可按需查询”
的服务：缓存外查询返回 `CATALOG_UNAVAILABLE`（`no_cached_field`），不会偷偷回源。

仓库**不包含**这 17,679 行缓存。`var/star_catalog.sqlite` 被
`系统/数据库工具/.gitignore` 忽略，可用 `tools/import_gaia.py --rebuild` 重建。

### 2.1 可提交的 fixture（250 行，仅测试）

| 项 | 值 |
|---|---|
| 文件 | `tests/fixtures/gaia_dr3_sample.csv`（250 行，7 场） |
| 元数据 | `tests/fixtures/gaia_dr3_sample.meta.json` |
| 来源 | ESA Gaia Archive TAP `sync` 锥搜索，真实 Gaia 行（不是合成坐标） |
| TAP URL | `https://gea.esac.esa.int/tap-server/tap/sync` |
| Archive | `https://gea.esac.esa.int/archive/` |
| 抓取时间 | `2026-11-15T02:28:40Z`（UTC） |
| 许可 | **CC BY-NC 3.0 IGO**（非 OSI；非商业限制） |
| Gaia 质量列 | `parallax_error/ruwe/astrometric_excess_noise/phot_variable_flag`，真实 DR3 原值 |
| 2MASS/WISE | IRSA Gator 真值对回；250 行中 173 行有 J/H/K、151 行有 W1/W2；失败/无匹配为 null |
| 完整性 | **不完整**；每场只抽 ~20 个近场心源 + 亮源 + 高自行源 + 最近对 + 有测光行 |
| 用途 | **仅离线测试/回归**；不得当作演示星表、全天星表或全量 Gaia |

fixture 的 `field_counts` 记录的是当时 TAP 缓存场的完整计数（合计 17,679），
不是 fixture 自身行数；`n_fixture_rows=250`。`provenance=fixture` 的 release
在 API 中永远 `coverage.complete=false` / `catalog_status=partial`，
不会因为“查到了几行”就声称完整。

---

## 3. 版本、许可与引用

| 字段 | 值 |
|---|---|
| `catalog` | `gaia` |
| `version` | `dr3` |
| `release` | `gaia-dr3` |
| `epoch_ref` | `J2016.0` |
| `epoch_ref_mjd` | `57388.5` |
| `license` | `CC BY-NC 3.0 IGO` |
| `citation` | `ESA/Gaia/DPAC; Gaia Collaboration et al. 2023, A&A 674, A1, DOI 10.1051/0004-6361/202243940` |
| `source_url` | `https://gea.esac.esa.int/tap-server/tap/sync` |
| 许可页 | <https://www.cosmos.esa.int/web/gaia-users/license> |
| DOI | <https://doi.org/10.1051/0004-6361/202243940> |

第二/第三目录：

| release | 内容 | provenance | 许可/引用 |
|---|---|---|---|
| `2mass-irsa-gator` | 8,674 个真实 2MASS 源 + J/H/K/quality，7 场 | `irsa_gator` | 2MASS 公共领域；Skrutskie et al. 2006 |
| `wise-irsa-gator` | 23,774 个真实 AllWISE 源 + W1/W2/quality，7 场 | `irsa_gator` | AllWISE 公共领域；Wright et al. 2010 / Cutri et al. 2021 |
| `2mass-derived-gaia-dr3` | 8,278 个唯一 2MASS ID + J/H/K（旧 fallback） | `derived_from_gaia_cache` | 2MASS 公共领域；Skrutskie et al. 2006 |
| `wise-derived-gaia-dr3` | 7,898 个唯一 WISE ID + W1/W2（旧 fallback） | `derived_from_gaia_cache` | AllWISE 公共领域；Wright et al. 2010 / Cutri et al. 2021 |
| `target_name`（表，不是 release） | 7 个 preset target 名称/别名/SIMBAD ID | `preset_target_names` | 名称是项目元数据；SIMBAD 引用/ODbL 仅在在线查询时适用 |

- 2MASS/WISE 派生目录**不是完整目录**，只含 Gaia 缓存中带交叉证认 ID 与所需星等的源；
  坐标/PM 沿用 Gaia J2016.0，不是 2MASS J2000 或 AllWISE 2010 原生位置。
- SIMBAD 本轮只做名称 enrichment；`simbad=unavailable` 时没有 `simbad` release，
  名称仍是 preset 元数据，不能用于 3σ 位置匹配。

- **Gaia 数据不是 OSI 开源许可**：CC BY-NC 3.0 IGO 允许非商业使用/再分发（带署名与许可），
  不能商业使用，不能暗示 ESA 背书。本项目只做非商业挑战赛演示，且仓库只提交 250 行
  fixture 子集，不重新分发全量星表。
- 代码/文档仍按项目 Apache-2.0；**数据许可不因代码许可而改变**。
- 每次 `cone` / `match` / `releases` 响应都带 `release`、`version`、`license`、
  `source_url`；release 更新时新增版本，不允许隐式漂移。
- 第三方材料总表：根目录 `THIRD_PARTY_NOTICES.md` §5.1。

SQLite 中 `star_meta.schema_version` 是 DB schema 版本（当前 **5**），
API 的 `schema_version` 是接口版本（当前 **1.0**）。两者独立。

---

## 4. 数据与持久化模型

`var/star_catalog.sqlite` 是**可重建缓存**，不是原始数据归档。删掉后用
`tools/import_gaia.py --rebuild` 重建（联网 TAP；失败回退 fixture）。
当前 **star schema v5**：v2 增加 `parallax_error/ruwe/astrometric_excess_noise/
phot_variable_flag` 与 `catalog_field.photometry_json`；v3 增加
`catalog_source.field_id`（场级 upsert 的归属键）及 `(release_id, field_id)` 索引；
v4 增加 `target_name`（7 个 preset target 名称/SIMBAD 状态）；v5 增加
`catalog_source.quality`（2MASS/WISE Gator quality flag）。
旧库用 `apply_star_schema()` 做 `ADD COLUMN` + 最近场心回填迁移（保留已有行），
也可直接 `--rebuild` 重建；两条路径都有测试。

| 表/视图 | 作用 |
|---|---|
| `star_meta` | `schema_version`、导入时间等 |
| `catalog_release` | 一次星表快照：catalog/version/epoch/license/citation/source_url/provenance/cache_radius/mag_limit/n_sources |
| `catalog_field` | 每个预设场的中心、缓存半径、行数、provenance |
| `catalog_source` | 星表源：位置、PM、视差、误差、G/BP-RP、J/H/K/W1/W2、2MASS/WISE ID、HEALPix、object_type |
| `catalog_match` | D 侧匹配产物：analysis_id、detection_id、release_id、source_id、sep、PM/PA 残差、ambiguity、confidence、检测位置/MJD |
| `catalog_import_run` | 导入审计：状态、模式、场数、行数 |
| `target_name` | 7 个 preset target 名称/别名/SIMBAD ID enrichment；不是场目录 |
| `v_catalog_source` / `v_catalog_release` | 对外列别名视图（`"2mass"`、`release`、`n_sources_actual`） |

关键列（`catalog_source`）：

`source_id`（服务内唯一键，`<release>:<catalog_id>`）、`release_id`、`field_id`、`catalog_id`（Gaia
`source_id` 字符串）、`ra`、`dec`、`pmra`、`pmdec`、`parallax`、`parallax_error`、
`ra_error`、`dec_error`、`pmra_error`、`pmdec_error`、`ruwe`、
`astrometric_excess_noise`、`phot_variable_flag`、`quality`、`gmag`、`bp_rp`、`jmag`、`hmag`、`kmag`、
`w1`、`w2`、`twomass_id`、`wise_id`、`epoch_ref`（MJD）、`healpix`（NESTED，nside=64）、
`object_type`、`provenance`、`created_at`。

- `catalog_id` / `source_id` 一律是**字符串**。Gaia 64 位 ID 超过 JavaScript
  `Number.MAX_SAFE_INTEGER`，JSON number 会静默改 ID（D-09）。
- `healpix` 由 `db/healpix.py` 在导入时计算（纯 Python NESTED ang2pix）。
  锥搜索正确性不依赖它；它是未来按像素分区/懒加载的索引。
- `object_type` 只是 Gaia 目录标志的粗粒度翻译（`star` / `qso_candidate` /
  `galaxy_candidate` / `non_single_star`），**不是科学分类**。
- `ruwe` / `astrometric_excess_noise` / `phot_variable_flag` 来自 Gaia DR3
  `gaia_source` 原列；`phot_variable_flag` 是目录字符串（常见 `NOT_AVAILABLE` /
  `CONSTANT` / `VARIABLE`），不是本项目判定。
- `jmag/hmag/kmag/w1/w2` 来自 **IRSA Gator**：同场锥查 2MASS PSC `fp_psc` 与
  AllWISE `allwise_p3as_psd`，按 Gaia 交叉证认 `twomass_id`/`wise_id`
  （designation）对回。没有交叉证认/目录没有质量值时是 **null，不是 0**。
- 真实 7 场缓存中，2MASS 星等对上 **8,419/17,679** 行，WISE 对上
  **8,052/17,679** 行（约 47.6% / 45.5%）；逐场覆盖率写入
  `catalog_field.photometry_json`，`/api/v1/catalog/fields` 以 `photometry` 返回。
  2MASS/WISE 是公共领域数据；Gator 查询失败时整列保持 null，release 仍明确
  标 `photometry.status=partial`（见 §10）。

---

## 5. HTTP API

### 5.1 通用约定

- 只绑 `127.0.0.1`，无鉴权/TLS；CORS `*` 便于本机 app 调用。
- JSON UTF-8；`NaN/Inf` 序列化为 `null`。
- 所有时间：`generated_at`/`imported_at` 是 ISO-8601 UTC（`Z`）；
  天文历元 `epoch`/`mjd`/`epoch_ref_mjd` 是 **MJD**。
- 所有响应带 `schema_version`、`service=star-catalog`；星表响应带 release 信息。
- 查询参数统一 `snake_case`；`epoch` 可省略（省略时返回星表原生 J2016.0 位置）。

### 5.2 `GET /api/v1/health`

```json
{
  "ok": true,
  "service": "star-catalog",
  "schema_version": "1.0",
  "star_schema_version": 5,
  "db_schema_version": 5,
  "counts": {"releases": 1, "sources": 250, "fields": 7, "matches": 0, "import_runs": 1},
  "simbad": "unavailable",
  "releases": [{
    "release": "gaia-dr3", "version": "dr3", "catalog": "gaia",
    "epoch_ref": "J2016.0", "epoch_ref_mjd": 57388.5,
    "license": "CC BY-NC 3.0 IGO", "citation": "ESA/Gaia/DPAC; ...",
    "provenance": "fixture", "n_sources": 250, "n_sources_actual": 250
  }],
  "db": {"path": ".../var/star_catalog.sqlite", "disk_bytes": 7340032,
         "schema_version": 5, "api_schema_version": "1.0"},
  "generated_at": "2026-11-15T01:02:43Z"
}
```

DB 不存在/损坏时 HTTP **503** `CATALOG_UNAVAILABLE`（不是 200 空健康）。

### 5.3 `GET /api/v1/catalog/releases`

已导入的 release 列表（按导入时间倒序），含 `release_key`（别名 `release`）、
`catalog`、`version`、`epoch_ref`、`epoch_mjd`、`imported_at`、`license`、
`citation`、`source_url`、`provenance`、`cache_radius_deg`、`mag_limit`、
`n_sources`、`n_sources_actual`、`n_fields`。
`GET /api/v1/catalog/targets` 给 7 个 preset target 名称/SIMBAD 状态（见 §5.10）。

### 5.4 `GET /api/v1/catalog/fields?catalog=gaia&version=dr3`

返回该 release 的预设场：`field_id`、`field_name`、`ra`、`dec`、`radius_deg`、
`n_sources`、`provenance`、`photometry`、`imported_at`。release 不存在 → 503。
`photometry` 是每场 2MASS/WISE 覆盖率摘要（`status`、`2mass.matched`、
`wise.matched`、`j_available` 等）；fixture 场为 `status=fixture_sample`，
真实 TAP 场为 `status=ok`/`partial`。D 用它判断“null 是没测到还是没缓存”。

### 5.5 `GET /api/v1/catalog/stats`

计数、磁盘字节、schema 版本、release 列表、`generated_at`。用于首页/运维，不做覆盖判断。

### 5.6 `GET /api/v1/catalog/cone`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ra` | float | 必填 | ICRS 赤经 [deg] |
| `dec` | float | 必填 | ICRS 赤纬 [deg]，[-90,90] |
| `radius_deg` | float | `0.1` | 锥半径，(0, 5]；也接受别名 `radius` |
| `epoch` | float | 省略 | 观测 MJD；给定时按 PM 传播到该历元 |
| `catalog` | str | `gaia` | `gaia` / `2mass`（派生）/ `wise`（派生） |
| `version` | str | 最新 | 固定 release 版本，如 `dr3` |
| `limit` | int | 500 | 分页大小，最大 2000 |
| `offset` | int | 0 | 分页偏移 |
| `ambiguity_arcsec` | float | 6.15 | 几何歧义半径（SPHEREx 像元） |

响应（节选）：

```json
{
  "schema_version": "1.0",
  "service": "star-catalog",
  "release": "gaia-dr3",
  "version": "dr3",
  "catalog": "gaia",
  "epoch_ref": "J2016.0",
  "epoch_ref_mjd": 57388.5,
  "license": "CC BY-NC 3.0 IGO",
  "citation": "ESA/Gaia/DPAC; Gaia Collaboration et al. 2023, ...",
  "source_url": "https://gea.esac.esa.int/tap-server/tap/sync",
  "provenance": "tap",
  "mag_limit": 20.0,
  "query": {"ra": 269.44616498, "dec": 4.76953633, "radius_deg": 0.001,
            "epoch": 61210.0, "catalog": "gaia", "version": null,
            "limit": 500, "offset": 0, "ambiguity_arcsec": 6.15},
  "coverage": {
    "complete": true, "field_id": "barnard", "field_name": "Barnard's Star",
    "field_ra": 269.452077, "field_dec": 4.693365, "field_radius_deg": 0.25,
    "center_sep_deg": 0.0764, "margin_deg": 0.1736,
    "field_provenance": "tap", "mag_limit": 20.0, "n_cached_sources": 9603
  },
  "count": 1, "returned": 1, "truncated": false, "has_more": false,
  "catalog_status": "ok",
  "sources": [{
    "source_id": "gaia-dr3:4472832130942575872",
    "catalog": "gaia", "catalog_id": "4472832130942575872",
    "release": "gaia-dr3", "version": "dr3",
    "ra": 269.44850252543836, "dec": 4.739420051112412,
    "ra_propagated": 269.446164982, "dec_propagated": 4.769536326,
    "epoch": 61210.0, "epoch_ref": 57388.5,
    "pmra": -801.550978, "pmdec": 10362.394207,
    "parallax": 546.97594, "parallax_error": 0.040116,
    "pmra_error": 0.03182, "pmdec_error": 0.03607,
    "ra_error": 0.02624, "dec_error": 0.02904,
    "ruwe": 1.0848505, "astrometric_excess_noise": 0.20481846,
    "phot_variable_flag": "NOT_AVAILABLE",
    "gmag": 8.1939745, "bp_rp": 2.8336968,
    "2mass": null, "wise": null, "jmag": null, "hmag": null, "kmag": null,
    "w1": null, "w2": null,
    "object_type": "star", "healpix": 31781,
    "provenance": "tap", "sep_arcsec": 0.0,
    "ambiguous": false, "n_within_ambiguity_arcsec": 1
  }],
  "generated_at": "2026-11-15T02:00:00Z"
}
```

> 示例数值取自真实 Gaia 行；示例按真实 TAP 缓存标注 `provenance=tap` /
> `coverage.complete=true`。若用 250 行 fixture 建库，数值相同，但
> `provenance=fixture`、`complete=false`、`catalog_status=partial`、
> `cache_limited=true`，`n_cached_sources` 是该场 fixture 行数（不是 9,603）。

语义：

- `ra/dec` 永远是星表**原生参考历元**（Gaia J2016.0）位置；给 `epoch` 时另给
  `ra_propagated/dec_propagated`，`sep_arcsec` 是**传播后**位置到查询中心的角距。
- `count` 是本次查询全部命中数；`returned` 是当前页；`has_more` 表示还有下一页；
  `truncated` 是内部 20 万行扫描上限（与分页无关）。
- `coverage.complete=true` 仅在：场 provenance 是 `tap`，**且**查询圆完全落在该场缓存半径内。
  `fixture`/`mixed` 一律 `false`；边缘/超出半径也是 `false` 并带 `warning`。
- `catalog_status`：`ok`（complete=true）或 `partial`；`partial` 时带 `cache_limited=true`。
- `ambiguous=true` 表示传播后位置 `ambiguity_arcsec` 内还有别的星表源；
  `n_within_ambiguity_arcsec` 含自身，≥2 即同像元多源。
- `count=0` 且 `catalog_status=ok` 只表示“星表未列出”，**不是新恒星**；
  `partial` 时 0 行更不能下结论。响应 `note` 会明说。

高自行场：查询中心应使用“目标坐标 + 足够大的锥”，或直接查传播后的位置。
例如 J2000 目标 (269.452077, 4.693365) 与 MJD 61210 的 Barnard：
星表源在 275.0″ 外；`radius_deg=0.1` + `epoch=61210` 能查到，3″ 小锥会漏。
D-03 建议锥半径 ≥0.3°，B 支持到 5°。

### 5.7 `POST /api/v1/catalog/match`

请求体：

```json
{
  "detections": [
    {"detection_id": "barnard-known", "ra": 269.44616498, "dec": 4.76953633,
     "mjd": 61210, "sigma_arcsec": 0.3,
     "pmra_mas_yr": -801.55, "pmdec_mas_yr": 10362.39}
  ],
  "catalogs": ["gaia"],
  "radius_arcsec": 3.0,
  "epoch": 61210,
  "persist": true,
  "analysis_id": "analysis:2026-11-15:barnard"
}
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `detections` | 必填 | 1..1000 个检测源 |
| `detections[].ra/dec` | 必填 | 检测位置（ICRS deg） |
| `detections[].mjd` / `epoch` | 省略→顶层 `epoch` | 观测 MJD |
| `detections[].sigma_arcsec` | 1.0 | 位置误差；有效半径 `max(radius_arcsec, 3σ)`，上限 60″ |
| `detections[].pmra_mas_yr` / `pmdec_mas_yr` | 省略 | 有值时算 PM 大小/方向残差 |
| `catalogs` | `["gaia"]` | 可多目录：`["gaia","2mass","wise"]`；2mass/wise 是 Gaia 缓存派生子集 |
| `radius_arcsec` | 3.0 | 基础匹配半径，(0, 60] |
| `persist` | false | 写 `catalog_match`；必须同时给 `analysis_id` |
| `analysis_id` | — | 持久化主键（D 的分析批次 ID） |

响应（节选，0.216″ 对）：

```json
{
  "schema_version": "1.0",
  "service": "star-catalog",
  "release": "gaia-dr3",
  "version": "dr3",
  "releases": [{"release": "gaia-dr3", "license": "CC BY-NC 3.0 IGO", "...": "..."}],
  "unavailable": false,
  "catalog_status": "ok",
  "query": {"catalogs": ["gaia"], "radius_arcsec": 0.5, "epoch": null, "n_detections": 1},
  "matches": [{
    "index": 0, "detection_id": "pair-0216",
    "ra": 269.6346612482693, "dec": 4.669660257173105,
    "epoch_mjd": 57388.5, "sigma_arcsec": 0.05,
    "match_radius_arcsec": 0.5,
    "coverage": {"gaia": {"complete": true, "field_id": "barnard", "...": "..."}},
    "catalog_status": "ok",
    "n_candidates": 2, "ambiguous": true, "status": "ambiguous",
    "note": null,
    "candidates": [
      {"source_id": "gaia-dr3:4472820208110875136", "catalog_id": "4472820208110875136",
       "ra": 269.6346612482693, "dec": 4.669660257173105,
       "ra_propagated": 269.6346612482693, "dec_propagated": 4.669660257173105,
       "sep_arcsec": 0.0, "ambiguous": true,
       "n_within_ambiguity_arcsec": 2, "confidence": 1.0,
       "pm_residual": null, "pa_residual": null, "...": "..."},
      {"source_id": "gaia-dr3:4472820212403005440", "catalog_id": "4472820212403005440",
       "sep_arcsec": 0.2163, "confidence": 0.687781, "...": "..."}
    ]
  }],
  "n_detections": 1, "n_matched": 1, "n_ambiguous": 1, "n_unmatched": 0,
  "persisted": 0,
  "note": "本接口只做星表关联（位置/自行/角距/歧义），不输出 known_star 或新恒星等科学分类。",
  "generated_at": "2026-11-15T02:00:00Z"
}
```

`status` 只有三种：`single_candidate` / `ambiguous` / `no_match`。
这是**候选数量**，不是 D 的 6 个顶层状态：

| `n_candidates` | `status` | D 可怎么用 |
|---|---|---|
| 1 | `single_candidate` | 位置候选；D 再查 PM 一致性/RUWE/混合，才可能给 `known_star` |
| ≥2 | `ambiguous` | D 的 `ambiguous` 输入之一（D 还叠加 blend/RUWE/PM 不一致） |
| 0 | `no_match` | D 结合 `catalog_status`：`ok` 才可能进入未收录候选流程；`partial` 只能 `insufficient_data` |

- `confidence = exp(-0.5 * (sep/σ_eff)^2)`，其中 `σ_eff = max(sigma_arcsec, 0.25″)`；
  这是**几何关联权重**，不是科学概率。D 不要把它直接当 `known_star` 概率。
  例：σ=0.05″、sep=0.2163″ 时候选 confidence≈0.688（不是 1）。
- `pm_residual` 是 PM 大小差 [mas/yr]，`pa_residual` 是 PM 方向差 [deg]（−180..180）；
  检测没给 PM 或星表无 PM 时为 `null`。
- 2MASS/WISE 实际 Gator 候选额外带 `quality`（2MASS/WISE `ph_qual`）；
  Gaia 候选/缺失为 null。候选的 `jmag/hmag/kmag/w1/w2` 按目录返回，不适用字段为 null。
- `coverage` 按目录给；检测点不在任何已缓存场 → **HTTP 503**
  `CATALOG_UNAVAILABLE` / `no_cached_field`，绝不返回 `no_match`。
- `catalog_status=partial` 表示 fixture/边缘缓存：0 候选不能当“未收录”；
  响应带 `cache_limited=true` 与 `warning`。
- 多目录时顶层 `release/version` 为 `null`，`releases` 数组给每个目录的 release。
- `persist=true` 时 `persisted` 是写入的 `catalog_match` 行数；候选 N 个就写 N 行
  （同一 `analysis_id`/`detection_id`，不同 `source_id`），`ambiguity` 记 1/0。
- **整批重放语义（幂等）**：同一 `analysis_id` 的旧行先删除再写入；重复 POST
  （网络重试/D 重算）不会累积重复行。`persist=true` 缺 `analysis_id` → 400。
  删除+插入在同一个事务；任一 INSERT 失败会回滚到调用前的旧行。空候选批次会
  清空该 `analysis_id` 的旧行并返回 `persisted=0`。
- `catalog_match` 不存 D 的 6 个分类标签，只存 `analysis_id/detection_id/source_id/
  sep_arcsec/pm_residual/pa_residual/ambiguity/confidence` 与检测位置/MJD；
  D 用自己的 analysis 表关联这些行做分类。

### 5.8 错误契约

| HTTP | `code` | 场景 |
|---|---|---|
| 400 | `BAD_REQUEST` | 缺 ra/dec、dec 越界、radius 非正、JSON 非法、persist 缺 analysis_id |
| 400 | `CATALOG_ERROR` | 只读连接写库等调用错误 |
| 404 | `NOT_FOUND` | 未知路径 / CATALOG.md 不存在 |
| 405 | `METHOD_NOT_ALLOWED` | GET `/api/v1/catalog/match` / `/api/v1/catalog/ingest` |
| 502 | `UPSTREAM_TAP_ERROR` | ingest 的 Gaia TAP 失败/截断；**不写任何数据** |
| 503 | `CATALOG_UNAVAILABLE` | 缺库、坏库、无 release、空 release、查询天区未缓存、SQLite 错误 |
| 500 | `INTERNAL` | 未预期异常（带 traceback 到 stderr） |

503 body 额外带 `reason`：

| `reason` | 含义 |
|---|---|
| `catalog_not_imported` | release 尚未导入 |
| `release_empty` | release 存在但 0 源 |
| `no_cached_field` | 查询点不在任何已缓存场覆盖内（需先导入该天区） |
| 其他/缺省 | SQLite/文件系统错误，message 里有细节 |

错误 body 同时有 `code`、`message`、`error.code`、`error.message`，方便客户端统一处理：

```json
{"schema_version":"1.0","service":"star-catalog","code":"CATALOG_UNAVAILABLE",
 "status":503,"message":"查询中心 (0.0000, 0.0000) 不在任何已缓存场覆盖范围内；...",
 "error":{"code":"CATALOG_UNAVAILABLE","message":"..."},
 "catalog":"gaia","version":"dr3","reason":"no_cached_field",
 "generated_at":"2026-11-15T02:00:00Z"}
```

**关键不变量：** 503 与服务成功但 0 候选严格区分。客户端不得把 503/`partial`
缓存当成“星表里没有这个源”，更不得据此写“新恒星”。

启动时如果 `--db` 指向的库不存在，`catalog_server.py` 拒绝启动并返回退出码 2
（提示先导入）；服务启动后库被删除/损坏，则请求返回 503 `CATALOG_UNAVAILABLE`。

### 5.9 `POST /api/v1/catalog/ingest`

场级接收：调用 Gaia TAP 锥搜索，成功后写入 `catalog_source`/`catalog_field`；
失败（TAP 超时/502/结果截断）返回 **502 `UPSTREAM_TAP_ERROR`，不写部分数据**。
这是 B 提供给 app D 阶段 auto-ingest 的写入口；app 端开关/重试由 identify 分支实现。

请求：

```json
{
  "catalog": "gaia",
  "ra": 158.578,
  "dec": 9.595,
  "radius_deg": 0.25,
  "mag_limit": 20.0,
  "field_id": "auto-158.5780-9.5950-r0.25",
  "field_name": "auto field RA 158.578 Dec 9.595",
  "include_photometry": false,
  "replace_field": true
}
```

| 字段 | 默认 | 规则 |
|---|---|---|
| `catalog` | `gaia` | MVP 只接受 `gaia` |
| `ra`/`dec` | 必填 | ICRS deg，dec [-90,90] |
| `radius_deg` | 0.25 | **0.05–1.0** |
| `mag_limit` | 20.0 | **12–20** |
| `field_id` | 自动生成 | 1..128 字符，只允许 `A-Za-z0-9._:-` |
| `field_name` | 自动生成 | 可选展示名 |
| `include_photometry` | false | true 时额外用 IRSA Gator 补 2MASS/WISE；失败只 warning，不影响 Gaia 落库 |
| `replace_field` | true | true=替换该 `field_id` 的旧源；false=追加/更新 |

成功响应：

```json
{
  "schema_version": "1.0",
  "service": "star-catalog",
  "ok": true,
  "catalog": "gaia",
  "release": "gaia-dr3",
  "version": "dr3",
  "field_id": "auto-158.5780-9.5950-r0.25",
  "counts": {"n_sources": 1234, "n_new": 1234, "n_fields": 8},
  "provenance": "tap",
  "release_provenance": "mixed",
  "complete": true,
  "radius_deg": 0.25,
  "mag_limit": 20.0,
  "fetched_at": "2026-11-15T06:30:00Z",
  "license": "CC BY-NC 3.0 IGO",
  "citation": "ESA/Gaia/DPAC; Gaia Collaboration et al. 2023, ...",
  "source_url": "https://gea.esac.esa.int/tap-server/tap/sync",
  "n_release_sources": 18913,
  "replace_field": true,
  "include_photometry": false,
  "photometry": null,
  "warnings": []
}
```

语义：

- 已有 release **不删整个 release**；只对 `field_id` 做场级替换/追加；
- 按 `source_id` UPSERT；仍在新列表中的源不删除，避免级联删 `catalog_match`；
- 只删除该场不再出现的 stale 源；其它场保留；
- 写入在事务内，server 级 `write_lock` 串行化；写连接与读连接分离；
- 成功更新 `catalog_release.n_sources`、`catalog_field`、`catalog_import_run`、
  `star_meta.last_import_at`；不写 `catalog_match`；
- `n_new` 是本次新增（此前 release 中不存在）的 source_id 数；
  同场重入通常 `n_new=0`；
- `provenance=tap` 只在真实 TAP 成功时返回；fixture 不会被标成 tap；
- `complete=true` 表示该场是完整 TAP 缓存；若 release 混有 fixture 场，
  `release_provenance=mixed` 并在 `warnings` 说明；
- `include_photometry=true` 且 Gator 失败时，Gaia 仍落库，`photometry.status=partial`，
  缺失 J/H/K/W1/W2 为 null。

示例：

```bash
curl -X POST 'http://127.0.0.1:8799/api/v1/catalog/ingest' \
  -H 'Content-Type: application/json' \
  -d '{"catalog":"gaia","ra":158.578,"dec":9.595,"radius_deg":0.25,
       "mag_limit":20.0,"field_id":"auto-158.5780-9.5950-r0.25",
       "include_photometry":false,"replace_field":true}'
```

TAP 失败示例：

```json
{"schema_version":"1.0","service":"star-catalog","code":"UPSTREAM_TAP_ERROR",
 "status":502,"message":"Gaia TAP 失败，未写入任何数据: ...",
 "error":{"code":"UPSTREAM_TAP_ERROR","message":"..."},
 "catalog":"gaia","field_id":"...","generated_at":"..."}
```

**不变量：** 502 后数据库计数/字段/import_run 均不变化；客户端必须把这次 ingest
当成“未缓存”，不能把随后 match 的 no_match 当新天体。


### 5.10 第二/第三目录：IRSA 实际 2MASS/WISE + SIMBAD 名称

**实际场目录（优先，联网 IRSA Gator）**：

```bash
$PY tools/import_irsa_catalogs.py --db var/star_catalog.sqlite --rebuild
```

- 对 7 个预设场各做一次 0.25° IRSA Gator 锥搜索；
- `2mass-irsa-gator`：**8,674 源 / 7 场**，源字段
  `catalog_id/ra/dec/jmag/hmag/kmag/quality`；
- `wise-irsa-gator`：**23,774 源 / 7 场**，源字段
  `catalog_id/ra/dec/w1/w2/quality`；
- 坐标是 Gator 原始 RA/Dec；`epoch_ref=J2000.0`，PM=null（不伪造自行）；
- `provenance=irsa_gator`，`coverage.complete=true`（场级实际目录，不是全球目录）；
- 2MASS/WISE 为公共领域数据；AllWISE 坐标原生约 2010，本服务按契约存 J2000
  且无 PM，D 不能把它当 2025–2026 位置锚点；
- `--rebuild` 只删/重建 `2mass-irsa-gator` / `wise-irsa-gator`，不碰 `gaia-dr3`；
- 某场失败：该场 skip 并写 `errors`，不写伪数据；实际 release 可能只有 6/7 场。

**旧派生 fallback（不联网）**：

```bash
$PY tools/derive_secondary_catalogs.py --db var/star_catalog.sqlite
```

- 从 Gaia 缓存抽 `twomass_id + JHK` / `wise_id + W1W2`，建 `*-derived-gaia-dr3`；
- 计数 2MASS 8,278、WISE 7,898 unique；坐标/PM 沿用 Gaia J2016.0；
- `provenance=derived_from_gaia_cache`，`coverage.complete=false`、`catalog_status=partial`；
- 没有实际 Gator release 时作为 fallback；实际 release 更新后 `get_release` 选最新。

查询：

```bash
curl 'http://127.0.0.1:8799/api/v1/catalog/cone?ra=269.44756&dec=4.69609&radius_deg=0.001&catalog=2mass'
curl 'http://127.0.0.1:8799/api/v1/catalog/cone?ra=269.44756&dec=4.69609&radius_deg=0.001&catalog=wise'
curl -X POST 'http://127.0.0.1:8799/api/v1/catalog/match' \
  -H 'Content-Type: application/json' \
  -d '{"detections":[{"ra":269.44756,"dec":4.69609,"mjd":61210,"sigma_arcsec":0.3}],
       "catalogs":["gaia","2mass","wise"],"radius_arcsec":1.0}'
```

每个候选带 `catalog/catalog_id/quality` 与真实星等；2MASS 候选 W1/W2=null，
WISE 候选 J/H/K=null；缺失值保持 null。实际 Gator 源无 PM，高自行源用 2026
检测位置直接匹配 2MASS/WISE 可能落空，这是物理边界，D 不应强配。

**SIMBAD 名称 enrichment**：

```bash
$PY tools/import_simbad.py --db var/star_catalog.sqlite   # 会探测 TAP；失败标 unavailable
$PY tools/import_simbad.py --db var/star_catalog.sqlite --offline
curl 'http://127.0.0.1:8799/api/v1/catalog/targets'
```

- 只做 7 个 preset target 的 `name/simbad_id/aliases/object_type`；
- 不建 `simbad` release，不把 SIMBAD 坐标用于 match/3σ；
- TAP 不可用：`simbad=unavailable`，名称 `provenance=preset_target_names`；
  不能把本地名称说成 SIMBAD 在线结果；
- 若 TAP 可达：`simbad=available_names_only`，场锥搜索仍未接入；
- `campo_a` 是演示场，`simbad_id=null`，不伪造 SIMBAD 对象。

---

## 6. PM 传播：B 做了什么、边界在哪

参考历元 `t0 = 57388.5`（Gaia J2016.0）；观测 `t`；`Δt_yr = (t-t0)/365.25`。

```text
Δα* = pmra  * Δt_yr / 3.6e6 / cos(dec)     # deg
Δδ  = pmdec * Δt_yr / 3.6e6                # deg
```

实现见 `db/star_catalog.py:propagate_pm`。这是小角度线性传播：

- **做**：高自行主序星/褐矮星的位置预测（Barnard 10.36″/yr，10.46 年 ≈ 108″）；
  `cone`/`match` 在给 `epoch` 时按传播后位置筛选；
  `cone`/`match` 搜索框按 `max(|pm|) × Δt_yr` 自动放大，再精确过滤。
- **不做**：视差（需要太阳/地球位置）、透视加速度、光行差、引力偏折；
  位置误差协方差、RUWE/astrometric excess noise 降级；PM 误差传播。
- **不判断**：检测 PM 与星表 PM 是否“一致”（只给 `pm_residual/pa_residual` 数值）；
  阈值 3σ/5σ、`BLEND_RISK`、`COLOR_OUTLIER` 都在 D。

**与 D 的分工：**

1. D 先按 D-03 用 ≥0.3°（或传播后位置的小锥）调 B `cone`，拿候选；
2. B 已把 `ra_propagated/dec_propagated` 和原生 `ra/dec` 都返回；
   D 可以直接用 B 的传播结果做 3σ 匹配，也可以在此基础上加视差项重算；
3. D 用 B 的 `pm_residual/pa_residual` + 自己的误差模型判“运动一致”；
4. D 决定 6 个状态之一；B 不写分类字段。
5. 需要复现时，D 把 `catalog_match` 行（`release_id/source_id/sep/confidence`）
   与自己的 `analysis_id` 关联；B 只存匹配产物，不存 D 的标签。

如果 D 要做视差传播：B 已返回 `parallax`、`ra_error/dec_error`、PM 误差；
但 B 的 `ra_propagated` 不含视差项。D 应明确标记“B 给的是一阶 PM 预测”，
在质量标记里写 `PM_LINEAR_NO_PARALLAX`，而不是把 B 的位置当作最终天体测量。

---

## 7. 与 app/D 的边界（known/ambiguous 归属）

| 能力 | B（本服务/DB） | D（app 分类层） |
|---|---|---|
| `catalog_release/source/match` schema | ✅ 拥有 | 只读/写自己的 analysis |
| 星表候选 + 角距 + PM 残差 | ✅ | 消费 |
| PM 线性传播 | ✅（可选 epoch） | 可在此基础上加视差/协方差 |
| 几何歧义（≥2 候选） | ✅ `ambiguous` | 输入之一 |
| known/ambiguous/uncatalogued/unknown 状态 | ❌ 绝不输出 | ✅ 6 个顶层状态 |
| 混合/饱和/颜色/RUWE 判定 | ❌ | ✅ |
| 持久化分类结论 | ❌（只存 match 行） | ✅ 自己的分析结果 |
| app/UI/端口 8797 | ❌ 不碰 | — |

同名不同义，务必区分：

- B `ambiguous=true`：有效匹配半径内星表候选 ≥2（纯几何）。
- D `ambiguous`：候选 ≥2 **或** `BLEND_RISK` **或** PM 不一致 **或** RUWE 高。

B 的 `catalog_status` 与 D 的 `catalog_status` 对齐：
`ok` / `partial` / 503→`unavailable`。D 在 `unavailable` 或 `partial` 时按设计输出
`insufficient_data`（或明确降级），不能输出 `uncatalogued_candidate`。

---

## 8. 缓存/临时文件与运维

- `var/star_catalog.sqlite`：Gaia 按场缓存（约 7 MB / 17,679 行），**本地保留、不提交**；
  `.gitignore` 已忽略主库与 `-wal/-shm`。
- `var/catalog.sqlite`：SPHEREx 生产库，**本服务只读不写、也不改其 schema**。
- `.tmp_hp/`：验证 HEALPix 时临时安装的参考实现（51 MB），**已删除、不提交**；
  根 `.gitignore` 保留忽略规则以防再次生成。测试不依赖它（`db/healpix.py` 是纯 Python）。
- 端口：8799 是星表服务；8797 属于 SPHEREx 生产服务，不占用。
- 单写者：SQLite WAL 支持多读；同一时刻只建议一个导入进程。
- 重建：`tools/import_gaia.py --rebuild`；只重建一个场：`--fields barnard --rebuild`；
  只导出可提交样本：`--export-sample`。
- 实际 2MASS/WISE：`tools/import_irsa_catalogs.py --db var/star_catalog.sqlite --rebuild`；
- 派生 fallback：`tools/derive_secondary_catalogs.py --db var/star_catalog.sqlite`；
- 名称：`tools/import_simbad.py --db var/star_catalog.sqlite [--offline]`。
- release 更新：当前导入器对同名 `catalog+version` 做 replace。冻结演示应改用新
  `version`/`release_key`，不要覆盖旧快照（D 设计要求旧结果可复现）。见 §10。

---

## 9. 验收与测试证据（离线，fixture）

```bash
$PY -m unittest tests.test_star_catalog -v
$PY -m unittest discover -s tests -t . -v
```

覆盖：

- schema/列/视图/外键、`schema_version=5`、release 的 epoch/license/citation/source_url；
- v1/v2/v3→v4 迁移：旧库缺 `ruwe/astrometric_excess_noise/phot_variable_flag/parallax_error/
  field_id/photometry_json` 时 `ADD COLUMN` 安全迁移，并按最近场心回填 `field_id`；
  v4 新增 `target_name` 表并 seed 7 个 preset 名称；
- fixture 250 行、7 场、`fetched_at=2026-11-15T02:28:40Z`、`field_counts` 合计 17,679；
- `ruwe=1.0848505`、`astrometric_excess_noise=0.20481846`、`phot_variable_flag=
  NOT_AVAILABLE`、`parallax_error>0` 在 DB 与 HTTP cone/match 都返回；
- 2MASS/WISE 真实值（Gator 行）与 null 边界：无交叉证认的源 `jmag/hmag/kmag/w1/w2`
  必须为 null，不能是 0；
- persist 幂等/重复/回滚/缺 analysis_id 400；重复 POST 后 `catalog_match` 行数不翻倍；
- HEALPix nside=1 参考值 + nside=64 子像素一致性 + 导入列一致；
- Barnard 锥搜索：J2016 原生 (269.448503, 4.739420) → MJD 61210 传播
  (269.446165, 4.769536)；对 J2000 目标 0.1° 锥的角距 **275.04″**；
- Barnard known match：1 个候选、`sep<0.01″`、`status=single_candidate`，
  且输出里没有 `known_star`/分类字段；
- 0.216″ 对（`4472820208110875136` / `4472820212403005440`）：
  match `ambiguous` / 2 候选 / sep 0.0000″ + 0.2163″，锥搜索两颗都 `ambiguous`；
- `CATALOG_UNAVAILABLE`：缺库（直接 + HTTP 503）、坏库（HTTP 503）、
  空 schema（`catalog_not_imported`）、缺天区（`no_cached_field`）、
  启动后删库；
- HTTP health/cone/match/match persist/stats/docs；`catalog_match` 持久化 2 行；
- `POST /api/v1/catalog/ingest`（fake TAP，离线）：200 落库、重复幂等、相邻场保留、
  TAP 失败 502 且计数不变、非法参数 400、photometry partial 不阻塞 Gaia 落库；
- `upsert_field` 新场/重入/相邻场保留/追加模式；UPSERT 不删仍在新列表的 source，
  `catalog_match` 不被级联清空；
- IRSA Gator 实际目录（fake Gator，离线）：7 场 × 2 目录落库、quality/epoch/JHK/W1W2、
  PM=null；`--rebuild` 不删 Gaia/旧派生；某场失败只 skip 不写伪数据；
- 2MASS/WISE 派生 fallback：fixture 2MASS 167 唯一 / WISE 146 唯一；7 场复制；
  只含 JHK/W1W2 完整行；重复 ID 折叠；重复 derive 幂等；
- 多目录 match：`catalogs=["gaia","2mass","wise"]` 返回三类候选，
  2MASS 候选 W1/W2=null、WISE 候选 J/H/K=null，派生目录 `catalog_status=partial`；
- SIMBAD：7 个 preset target 名称/ID；TAP 不可用 `simbad=unavailable`；
  不生成 `simbad` release，`match(catalogs=["simbad"])` 返回 `CATALOG_UNAVAILABLE`；
  `campo_a` 无 SIMBAD ID，不伪造。

**真实缓存复验（可选，本地有 7 MB 库时）：**

```bash
$PY - <<'PY'
from db.star_catalog import StarCatalog, propagate_pm
c = StarCatalog("var/star_catalog.sqlite")
out = c.cone(269.4461649815, 4.7695363257, 0.001, epoch=61210)
print(out["count"], out["sources"][0]["catalog_id"], out["sources"][0]["sep_arcsec"])
PY
```

预期：`1 4472832130942575872 0.0`。真实库的 `provenance=tap` 时，
中心 0.001° 查询 `catalog_status=ok`（不是 fixture 的 `partial`）。

---

## 10. 未完成/已知限制（交给 lead/D 决策）

1. **2MASS/WISE 实际目录只覆盖 7 个 0.25° 场，不是全球目录。** `2mass-irsa-gator`
   8,674 源、`wise-irsa-gator` 23,774 源，坐标是 Gator 原始 RA/Dec；2MASS 为 J2000，
   AllWISE 原生约 2010（本服务按契约存 `epoch_ref=J2000.0` 且不写 PM，D 不能把它当
   2025–2026 位置锚点）。旧 `*-derived-gaia-dr3` 是可离线重建的交叉证认 fallback
   （2MASS 8,278 / WISE 7,898 unique），不是完整目录。未覆盖天区/源不能声称全量。
2. **RUWE/astrometric_excess_noise/phot_variable_flag 已接入；仍缺**
   `visibility_periods_used`、`astrometric_excess_noise_sig`、2MASS/WISE 误差列。
   D 可以用 `ruwe` 做降级，但不能把 `phot_variable_flag` 当成 D 的变星结论。
3. **SIMBAD 只做名称 enrichment。** 当前 TAP 不可用，`simbad=unavailable`；
   7 个名称/ID 是 preset 元数据，没有 `simbad` release，也不参与位置匹配。
   TAP 恢复后仍需实现场锥搜索；不能把名称/文献坐标当 3σ 天体测量。
4. **PM 只做一阶线性、不含视差/协方差**（§6）。高视差近星（Barnard ϖ=547 mas）
   D 应加视差项或明确标注。
5. **release 替换语义**：导入器对 `gaia+dr3` 做 replace；没有 checksum 列。
   冻结/复现要求“旧 release 不覆盖”时，需要新版本号或冻结 manifest（C/D 阶段）。
6. **不是全天服务**：只有 7 个 0.25° 场；锥半径上限 5°，`_query_rows` 扫描上限 20 万行；
   没有按 HEALPix 分片/懒加载，没有 TAP 异步大查询。
7. **fixture 不完整**：250/17,679 行，且每场是抽样；任何基于 fixture 的
   “complete” 都被强制 false。不要用 fixture 做“全星表”演示。
8. **服务无鉴权/TLS**，只适合本机/内网；公网需反代。
9. **D 的分类实现不在本分支**：B 只提供契约、候选与 `catalog_match` 持久化；
   `identify` 状态机等 D 代码由 D owner 完成。

---

## 11. 参考

- Gaia DR3 许可：<https://www.cosmos.esa.int/web/gaia-users/license>
- Gaia Archive：<https://gea.esac.esa.int/archive/>
- Gaia TAP：<https://gea.esac.esa.int/tap-server/tap>
- Gaia Collaboration (Vallenari et al.) 2023, A&A 674, A1,
  <https://doi.org/10.1051/0004-6361/202243940>
- HEALPix：Górski et al. 2005, ApJ 622, 759；本实现只用 NESTED ang2pix
- 项目内：`项目主控/架构/06_ABC任务分发.md`、`07_已知未知识别与数据比对设计.md`、
  `docs/DESIGN.md`、`docs/API.md`
- 第三方材料：根目录 `THIRD_PARTY_NOTICES.md` §5.1
