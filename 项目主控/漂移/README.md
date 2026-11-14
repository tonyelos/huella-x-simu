# T6 · 跨工具漂移检查器

只读的跨工具一致性检查器，位于 `项目主控/漂移/`。它把 MoveScope（app）、数据库工具、
视图工具三处的**当前数据、常量、就绪度口径与文档数字**和 `expect.json` 基线逐项对账，
输出 `report.json` + `report.md`。**不修改任何源码，也不启动数据库服务。**

## 快速开始

```bash
cd 项目主控/漂移
./run.sh
# 退出码 0 = 全过；1 = 发现漂移；2 = 环境缺失
```

常用参数：

```bash
./run.sh --db /path/to/catalog.sqlite --out-dir /tmp/t6-report
./run.sh --root /path/to/worktree --expect ./expect.json
./run.sh --quiet                 # 只打印漂移/警告/汇总
python3 -m unittest discover -s tests -v   # fixture 自测
```

默认值：

- 根目录：本目录上两级（仓库根）；
- DB：`<root>/系统/数据库工具/var/catalog.sqlite`；
- 基线：本目录 `expect.json`；
- 报告：本目录 `report.json` / `report.md`（已加入 `.gitignore`，不随仓库提交）。

## 检查项（7 类）

| ID | 检查 | 内容 |
|---|---|---|
| `targets` | 三处目标字段 | app `data/targets.json`、DB `targets`、view `data/targets.json` 逐字段比较：`id/name/name_zh/official/ra/dec/type/expected/coord_unc_arcsec/truth/note`；`coord_unc_arcsec` 必须存在于三处，缺失按错误处理；view 的 `source_sha256` 必须等于 app 文件哈希。 |
| `epochs` | 历元 74 条 | app `epoch_catalog.json` vs DB `target_epochs` vs `var/exports/epoch_catalog.json`；按 `(obsid, detector)` 逐条比较 `release/week/version/mjd/date_obs/sep_deg/key`；总数、每目标数、去重、排序、`date_obs` 非空、`date_obs` 与 MJD 一致性。DB 无 `version` 列，只比较共有字段。 |
| `fields` | 星场 5,893 行 | view `fields.json`（10 列紧凑数组）vs DB `frames`（按 `frame_id`）vs `fields_snapshot` vs `var/exports/fields.json`；行数、键集合、顺序、10 列逐格比较；view 保留 5 位小数导致的浮点差异按容差接受并计数；同步 `count` / `exposures` / 本地观测周数。 |
| `constants` | 覆盖半径 / 去重容差 / L3 阈值 / 端口 | 覆盖半径 **2.47°**、单片半宽 **1.745°**、去重容差 **0.02 d**、L3 条件 **n≥6 且 baseline≥0.6 yr**、L3 文案、app **8790** / DB **8797** / view **8830**。用 AST/正则定位源码常量，不靠行号。 |
| `readiness` | coverage_level vs analysis_readiness | app 覆盖函数只返回 `coverage_level`（C0–C3）；app 分析结果只返回 `readiness`（L0–L3）；DB v1 coverage 显式剥离 `readiness`；DB 旧 `coverage()` 的 `readiness` 必须标 `deprecated`；视图工具不得出现 `readiness`；扫描 C/L 值混用。另检查 C3 覆盖阈值 app/DB 一致，并对 DB 旧 readiness 的 180 天阈值与 app L3 0.6 年（219.15 天）不一致给出 WARN。 |
| `docs` | 文档数字 | 扫描根 README、DB README/benchmarks README/INTEGRATION、view README、header cache README：帧数、fields 行数、曝光数、历元数、目标数、FITS 头数、cutout 数、DB/视图周数、schema 版本、app/DB 单测数。历史说明（“旧/从…扩展/审计基线/快照”等）按上下文跳过；合成规模基准（≥100,000 帧）跳过。 |
| `schema` | DB schema/API 版本 | `db/config.py` 的 `SCHEMA_VERSION` / `API_SCHEMA_VERSION` 与 DB `meta.schema_version` 对账；必需表是否齐全；`PRAGMA quick_check`。 |

DB 相关检查（`targets` / `epochs` / `fields` / `schema`）在 `catalog.sqlite` 不存在或不可读时标 **SKIP**，
不会因为 DB 服务未启动而失败；常量、就绪度、文档检查仍照常运行。

## 退出码与状态

- **0**：没有 ERROR（可以有 WARN/SKIP）；
- **1**：至少一个 ERROR（真实漂移）；
- **2**：环境缺失（工作区不存在、核心 JSON 缺失、`--expect` 指定文件缺失、报告无法写入）。

单类检查状态：

- `PASS`：无错误、无跳过、无警告；
- `WARN`：只有警告；
- `SKIP`：DB 等可选输入缺失，但已完成可用部分；
- `FAIL`：至少一个错误（即使同时 SKIP，也以 FAIL 计）。

## 基线：`expect.json`

`expect.json` 固定当前应保持的数字与常量：

- 数据量：`7` 目标 / `5` 官方目标 / `74` 历元 / `5,893` fields 行 / `5,562` 曝光 /
  `151` DB 周 / `23` 本地视图周 / `74` FITS 头 / `101` cutout；
- 常量：`2.47` / `1.745` / `0.02` / L3 `6` 期 + `0.6` 年 / 端口 `8790,8797,8830`；
- schema：`6`（SQLite）/ `1.0`（API）；
- 单测数：app `52`、DB `54`；
- 文档清单 `docs`。

数据增长或常量有意变更时，先更新 `expect.json`，再让检查重新全绿；**不要为了让检查通过而改检查逻辑**。

## 当前实测（2026-11-14，DB 服务停止但 sqlite 文件可用）

`./run.sh` 当前退出 **1**，发现 3 项文档数字漂移：

| 文件:行 | 现值 | 实测 |
|---|---|---|
| `系统/数据库工具/benchmarks/README.md:5` | 生产库 5,857 帧 | 5,893 |
| `系统/视图工具/README.md:79` | 本地 15 个观测周 | 23 |
| `系统/视图工具/README.md:136` | fields.json 5,857 帧 | 5,893 |

另有 1 项 WARN：DB 旧 `coverage()` 的 `readiness` L3 阈值是 180 天，而 app 的 L3 阈值是
0.6 年（219.15 天）；该字段已标 `deprecated`，不得当作 `analysis_readiness` 使用。

数据/常量其余全绿：targets 5 官方 + 2 样本逐字段一致（含 `coord_unc_arcsec`）；
74 条历元三处逐条一致；5,893 行 fields 与 DB `frames` 仅差 view 的 5 位小数舍入（0 处超容差）；
覆盖半径 2.47°、去重 0.02 d、L3 0.6 yr、端口 8790/8797/8830 均一致。

## 只读与边界

- 数据库只以只读 URI 打开：无 `-wal` 时用 `mode=ro&immutable=1` 快照（避免生成 `-shm/-wal`）；
  存在非空 WAL（DB 正在写入）时退回 `mode=ro` 读取最新内容；并设置 `PRAGMA query_only=ON`。只执行 SELECT/PRAGMA。
- 不导入 app/DB/view 的运行时代码，不触发任何业务逻辑；源码常量用 AST/正则静态读取。
- 不修改三工具源码、数据或 `expect.json`；唯一写入是报告文件。
- 文档检查只做“数字对账”，不判断文案质量；被历史标记跳过的行需人工确认是否真的是历史说明。
- 本工具不修 README：发现漂移后由对应 owner 修文档/数据，再重跑。

## 自测

`tests/test_drift.py` 在临时目录生成一套最小 app/DB/view 数据、源码片段和 README，
覆盖：

- 全绿 → 0；
- targets / epochs / fields / constants / readiness / docs 漂移 → 1；
- 缺 DB → DB 类检查 SKIP，仍可全绿；
- 缺核心 JSON → 2；
- 历史标记行被跳过。

```bash
cd 项目主控/漂移
python3 -m unittest discover -s tests -v
```
