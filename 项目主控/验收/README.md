# T1 · 验收与契约工具（Acceptance & Contract Harness）

一条命令验证三个工具是否仍然满足官方要求与跨工具契约，输出机器可读的
`report.json`、人读的 `report.md` 与证据 `artifacts/`。

- 类型：内部 QA 工具，不对外、不进入产品 UI。
- 边界：**只读** `系统/app/`、`系统/数据库工具/`、`系统/视图工具/`；所有写入都在本目录的
  `report/` 与 `/tmp`。
- 依赖：Python standard library（不依赖 pytest / jsonschema / playwright）。
- 默认**不启动**任何服务；app 健康检查失败时退出码 2。
- `--start`（可选）在 base-url 端口启动一个只属于验收的 app 子进程；`--offline`
  另在 `--offline-port` 启动黑洞代理子进程。只终止自己启动的进程，不杀用户服务。

## 当前状态（2026-11-15，分支 `tool/t1-acceptance`）

- **全量验收已全绿（main 官方基准版 app `1c17c3e`）**：rebase `origin/main` 后，在 8810 app +
  DB 8797 上运行 `report/full-main`：**45 PASS / 0 FAIL / 1 WARN / 0 BLOCKED**，
  共 46 条用例（45 PASS + `H.screenshot` manual WARN），49.7s，**退出码 0**。
  `D.custom_rw_aur`、`D.coord_matches_preset`、`C/F.barnard` 均 PASS。
- **`D.custom_rw_aur` 新断言**：不再要求 `real_epochs≥1`；要求 HTTP 200、`mode=real|hybrid`、
  `matched_target_id=rw_aur`、`class=V`，并与预设 `rw_aur` 的 mode/readiness/class/μ（±5%）一致；
  **HYBRID 允许 `real_epochs=0`**。修复版实际 `hybrid/8/L2/V`、`real_epochs=0`、`matched=rw_aur`、
  `μ=49.3`，与预设一致。
- **官方基准 Barnard `S→L`（Gaia PM 星表先验，不是独立实测）**：`golden/five_targets.json`
  与 `golden/custom_barnard.json` 的 Barnard `class` 改为 `L`，μ/μ_err/容差原样保留；
  `C.barnard`、`D.custom_barnard`、`F.offline.barnard` 新增断言：
  `catalog_motion.basis="catalog_prior"`、`moving=false`、`mu_signif<3`、
  `quality` 含 `CATALOG_PRIOR_MOTION`/`MEASUREMENT_NOT_SIGNIFICANT`、
  无 `discovery_claim=true`/“新天体”正结论。F 离线 5/5 随黄金更新恢复。
- **R3 等价性**：`D.coord_matches_preset` 对 5 个官方目标比较自定义坐标与预设，
  mode/readiness/class 严格一致、μ±5%；当前 5/5 一致。
- **BLOCKED 语义保留**：DB 停止或 `/api/v1/epochs` 不可用时，DB 相关 P0 仍记 BLOCKED（不计 PASS）；
  `D.custom_rw_aur` 若因 DB 停止导致 `matched_target_id` 无法确认，记 BLOCKED 并标 `blocked_by=DB`。
- **历史（修复前 app）**：`report/full-db-down`（28 PASS / 4 FAIL / 13 BLOCKED）与
  `report/full-db-up`（40 PASS / 5 FAIL）保留用于验证 BLOCKED 语义与旧回归，不是当前 main 基线。

## 快速开始

```bash
cd /Users/yiao/Documents/huellaX/项目主控/验收

# 全量验收：main 修复版 app（8810 已在跑时 --start 直接复用；DB 8797）
./run.sh --start \
  --base-url http://127.0.0.1:8810 \
  --db-url  http://127.0.0.1:8797 \
  --view-url http://127.0.0.1:8812 \
  --offline --offline-port 8813 \
  --report-dir report/full-main
echo $?   # 0：45 PASS / 0 FAIL / 1 WARN（截图 manual）

# 可选：DB 停止 / BLOCKED 语义演示（8798 无监听）
./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8798 \
  --view-url http://127.0.0.1:8812 --offline --offline-port 8813 \
  --report-dir report/full-db-down
```

> 本 worktree 已 rebase `origin/main`，`--start` 会启动当前官方基准版 app；
> 若 8810 已有服务则直接复用（不接管）。DB 用 8797；离线子进程用 8813。

### 端口隔离

| 服务 | 端口 | 启动方式 |
|---|---|---|
| app 主验收 | 8810 | `--start` 自动启动（端口已占用则复用，不接管） |
| app 离线子进程 | 8813 | `--offline --offline-port 8813` 自动启动/停止 |
| DB 只读 | 8811 | `系统/数据库工具/cli.py serve --port 8811 --quiet`；测完停掉自己的进程 |
| view | 8812 | `cd 系统/视图工具 && python3 -m http.server 8812 --bind 127.0.0.1` |

不要占用 8790/8797/8830/8900；本工具不会杀用户已在跑的服务，只终止自己启动的
app 子进程（8810/8813）。

## 用例组

| 组 | 内容 | 级别 |
|---|---|---|
| A 契约 | `Target`（app/DB/view 三源、7 条、8 字段、`coord_unc_arcsec`）；`EpochRef`（两入口同形、十字段、`date_obs` 非空、无重复）；`Coverage`（默认 2.47、`C*`）；`AnalysisResult`（`schema_version`、`provenance`、`fallback_reason`、`readiness_scope`） | P0；视图源/`coord_unc`/`real_epochs` 为 P1；DB 停止时 DB 源/epoch_ref/coverage 为 BLOCKED |
| B 导出对账 | `cli.py export all` + `tools/check_compat.py` 四项 OK（fields / epoch_catalog / targets / week_index）；只读本地 SQLite，不需要 DB 服务；SQLite 不可读时 BLOCKED | P0 |
| C 5 目标 HTTP | 5 × `POST /api/analyze`（`mode=auto,fov=0.25`）：HTTP 200、schema、`mode`/`provenance` 自洽、`readiness`/`class` 严格黄金、μ 在 ±5% 或 ±1σ、<30s | 500/class/mode/readiness/μ = P0；期数/耗时 = P1 |
| D 任意坐标 | ① Barnard 精确坐标 → REAL 4 期 L2 L（Gaia PM 先验，`moving=false`、`mu_signif<3`）；② RW Aur 自定义坐标 = 预设（HYBRID 允许 `real_epochs=0`，`matched=rw_aur`、class V）；③ 未覆盖天区 → `NO_LOCAL_COVERAGE` + SIM；④ `--db-down` 可选；⑤ `D.coord_matches_preset`：5 官方目标自定义坐标应与 target_id 预设等价（R3） | ①/②/⑤ = P0；③/② 在 DB 不可用导致无法确认时 BLOCKED |
| E 错误路径 | `sample` 不 500；`fov=-0.2/0/999` → 400；target+冲突坐标不静默；非法 JSON/缺字段 → 400 | sample/FOV 500 = P0；其余 = P1 |
| F 离线 | 8813 子进程 + `HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:9` + `NO_PROXY=127.0.0.1,localhost`；5 目标 200 且与在线一致 | P0 |
| G DB 一致性 | `/api/v1/epochs` 两入口同形：Barnard 4 / RW 1；Eps/WISE/R Dra 10/10/3；`date_obs`/`key` 非空；无重复；`coverage` 默认 2.47；`header_frames` 与 SQLite 实际一致 | P0；DB 停止或 v1 404 时全部/部分 BLOCKED |
| H 视图工具 | 静态资源 200；`js/main.js` 不含 `../系统/app/index.html`、`method: 'HEAD'`、`toFixed(5)`；截图 = `WARN: manual` | P1/WARN，不阻塞提交门槛 |

## 参数

| 参数 | 说明 |
|---|---|
| `--base-url` | app 地址（默认 `http://127.0.0.1:8790`） |
| `--db-url` | DB 地址（默认 `http://127.0.0.1:8797`） |
| `--view-url` | 视图工具地址（可选，默认 `http://127.0.0.1:8830`） |
| `--report-dir` | 报告目录（默认 `report/<timestamp>`；相对本目录） |
| `--start` | app 未运行时，在 base-url 端口启动验收专用 app 子进程；已运行则复用。只支持本地 URL |
| `--offline` | 启动 `--offline-port` 子进程 + 黑洞代理，跑 F 组 |
| `--offline-port` | 离线/D4 子进程端口（默认 8810；与 base-url 端口冲突时必须改，如 8813） |
| `--update-golden` | 用本次 C 组实测更新 `golden/five_targets.json`；非全绿时必须 `--confirm` |
| `--only` / `--skip` | 按组或用例过滤：`--only C`、`--only C,D`、`--only C.barnard`、`--skip H` |
| `--no-start` | 只测已运行服务；与 `--offline`/`--db-down` 冲突时对应用例记 WARN；与 `--start` 互斥 |
| `--db-down` | 可选 D4：把 `SPHEREX_DB_URL` 指向黑洞端口，验证预设回退与自定义降级 |
| `--http-timeout` | 单次 HTTP 超时秒（默认 60） |

## 输出与退出码

```text
report/<name>/
  report.json     # schema_version=1.0；cases[] 含 id/group/severity/status/expected/actual/evidence/repro
  report.md       # 环境、DB 状态、红在哪/归因、分组表、失败详情、BLOCKED 清单、复现命令
  artifacts/      # 实际 HTTP/DB 输出、导出日志、子进程日志
```

- `0`：没有 P0 失败，且没有 P0 BLOCKED（全绿）。
- `1`：至少一条 P0 失败，**或**至少一条 P0 BLOCKED（依赖不可用、未验证，不能当 PASS）。
- `2`：环境错误（app 不可达/`--start` 起不来/报告目录不可写/黄金样本损坏）。

`report.json` 的 `summary` 形如：

```json
{"pass":28,"fail":3,"warn":1,"blocked":13,
 "p0_fail":3,"p1_fail":0,"p0_blocked":13,"p1_blocked":0}
```

BLOCKED 用例带 `blocked_by: "DB"`、`blocked_reason`、`cause`；DB 停止时
`environment.services.db.status="unreachable"`，v1 404 时 `"legacy-only"`，
正常时 `"v1-ok"`。

`report.md` 顶部会写：git commit/tag、数据快照 sha256、服务端口/版本、Python 版本；
每条用例都有复现命令。

## 黄金样本

- `golden/five_targets.json`：2026-11-15 官方基准的 5 目标 `mode/n/readiness/class/μ`
  （μ 容差 ±5% 或 ±1σ，`readiness`/`class` 严格，期数/耗时 P1）。
  Barnard：`mode=real/n=4/readiness=L2/class=L/μ=8033.0`（μ/μ_err 实测值保留）；
  `L` 来自 Gaia PM 星表先验，不是 SPHEREx 独立实测运动。
- `golden/custom_barnard.json`：R1 的 Barnard 黄金结果
  （`real/4/L2/L`，μ 与预设差 ≤1%，`real_epochs≥4`；同样要求 `catalog_prior` 语义）。
- **不要在未修复时放宽容差或跳过 P0**。修复后要更新黄金样本，先跑全绿，
  再 `--update-golden`（人工确认）。

## 当前基线（2026-11-15，main 修复版 app）

### 全量：45 PASS / 0 FAIL / 1 WARN / 0 BLOCKED，退出码 0

- 报告 `report/full-main`，49.7s，共 46 条用例；唯一 WARN 是 `H.screenshot`（manual）。
- A/B/C/D/E/F/G 全绿；D 组 4 条全 PASS：
  - `D.custom_barnard`：real/4/L2/L/μ=8032.9；basis=catalog_prior、moving=False、mu_signif=0.81
  - `D.custom_rw_aur`：hybrid/8/L2/V/real_epochs=0/matched=rw_aur/μ=49.3，与预设一致
  - `D.uncovered`：NO_LOCAL_COVERAGE + SIM
  - `D.coord_matches_preset`：5/5 自定义坐标与预设一致（R3）
- 运行方式：本 worktree 已 rebase `origin/main`（含 app 官方基准优化）；app 由 `--start` 在 8810 启动，
  DB 8797；报告的 `source_hashes` 与当前 app 源码一致。
- `C.barnard` / `F.offline.barnard` / `D.custom_barnard` 均断言 `basis=catalog_prior`、`moving=false`、
  `mu_signif<3`、`quality` 含两标记、无 `discovery_claim`/“新天体”；`F.offline.5of5` 5/5。

### RW Aur 前后对比（`D.custom_rw_aur`）

| 版本 | 实际响应 | 断言 | 结果 |
|---|---|---|---|
| 修复前（旧 T1） | hybrid/8/L2/V、`real_epochs=0`、`matched=rw_aur`、μ=49.3 | 要求 `real_epochs≥1` | FAIL（`CUSTOM_RW_REAL_EPOCHS`；HYBRID 纯 SIM 被误判） |
| 修复后（新 T1） | 同上，预设 hybrid/8/L2/V/μ=49.3 | HTTP 200、mode=real\|hybrid、matched=rw_aur、class=V、与预设 mode/readiness/class/μ 一致；允许 0 | **PASS** |

### 历史：修复前 app 的 DB 停止 / DB 可用基线

- `report/full-db-down`：28 PASS / 4 FAIL / 13 BLOCKED，退出码 1；用于验证 BLOCKED 语义。
- `report/full-db-up`：40 PASS / 5 FAIL / 0 BLOCKED，退出码 1；RW 500、R Dra S vs V 等旧回归。
- 这两组不是当前 main 基线；修复版 app 的详细结果以 `report/full-main` 为准。

## 只跑某组 / 单条复现

```bash
# 契约 + DB（DB 8811 可用时）
./run.sh --db-url http://127.0.0.1:8811 --view-url http://127.0.0.1:8812 --only A,G

# 离线（需要 8813 空闲）
./run.sh --start --report-dir report/offline --offline --offline-port 8813 --only F

# 只跑 R1 两条
./run.sh --start --db-url http://127.0.0.1:8811 --report-dir report/r1 --only D

# 手工 R1 复现（app 8810）
curl -s -X POST http://127.0.0.1:8810/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"ra":269.452077,"dec":4.693365,"fov":0.25}'

# 手工 DB v1 两入口
curl -s 'http://127.0.0.1:8811/api/v1/epochs?target_id=barnard'
curl -s 'http://127.0.0.1:8811/api/v1/epochs?ra=269.452077&dec=4.693365&fov=0.25'
```

## 文件布局

```text
项目主控/验收/
  run.sh                 # 一条命令入口（选 Python、切到本目录）
  accept.py              # 主程序：用例、HTTP/DB 探针、BLOCKED 归因、报告
  contracts/
    __init__.py          # problem/severity 小工具
    target.py            # Target DTO
    epoch_ref.py         # EpochRef DTO + 两入口逐字段比较
    coverage.py          # Coverage DTO
    analysis.py          # AnalysisResult DTO（shape / contract / real_epochs 三层）
  golden/
    five_targets.json
    custom_barnard.json
  report/                # 运行输出（.gitignore）
  README.md
  STATUS.md
```

## 已知限制

- H 组不做浏览器截图（不引入 Playwright）；`H.screenshot` 固定为 `WARN: manual`。
- B 组依赖本地 `数据库工具/var/catalog.sqlite`；并行导入期间可能出现瞬时漂移，
  报告会记录当时的 sha256。
- G 组的 `header_frames` 真值只在本地 SQLite 存在时核对；DB 服务不可达时整条 BLOCKED。
- `--start` 只支持本地 base-url；`--offline-port` 必须与 base-url 端口不同。
- 从本 worktree 当前 DB 源码启动的 8811 已有完整 v1；若线上 DB 是旧服务（v1 404），
  报告会标 `legacy-only` 并把 v1 用例 BLOCKED，不会伪绿。
- `D.custom_rw_aur` 的 HYBRID 允许 `real_epochs=0`；若 DB 停止导致 `matched_target_id`
  无法确认，该用例记 BLOCKED（`blocked_by=DB`），不当成 app 缺陷。
- Barnard 的 `class=L` 依赖 Gaia PM 先验与 identification；若先验/识别不可用，
  `BAR_PRIOR_*` 断言会红，不能用“任意 class”放行。
- 本工具不做科学判定，不替代产品测试；只验证接口/契约/降级行为。
