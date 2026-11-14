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

- **BLOCKED 语义已完成**：DB 停止或 `/api/v1/epochs` 不可用时，DB 相关 P0 用例记
  `BLOCKED`（不是 PASS，也不伪造 FAIL）；app-only 用例继续跑；R1 自定义坐标仍按实际
  `DB_UNAVAILABLE` 记 FAIL。报告新增「DB 状态」「红在哪 / 归因」「BLOCKED」三节。
- **主验收（DB 8798 无监听，模拟 DB 停止）**：`./run.sh --start ... --offline --offline-port 8813`
  耗时约 35–42s，`PASS=28 FAIL=3 WARN=1 BLOCKED=13`，退出码 **1**。
  （本轮运行期间 8797 被外部进程启动；8798 无监听，对 app 等价于 DB 停止。）
  红：`A.analysis_schema`、`D.custom_barnard`、`D.custom_rw_aur`；
  DB 相关 13 条全部 BLOCKED。
- **补充验收（DB 8811 只读库可用）**：`PASS=40 FAIL=4 WARN=1 BLOCKED=0`，退出码 **1**。
  红：`A.targets.db`、`A.targets.app_db`、`A.analysis_schema`、`D.custom_rw_aur`；
  G 组 7/7 全绿，R1 Barnard 自定义坐标 **PASS**（real/4/L2/S，μ=8032.9）。
- **关键发现**：
  1. 从当前源码 `数据库工具/cli.py serve --port 8811` 启动后，`/api/v1/epochs` 正常，
     G 组全绿；此前“`/api/v1/epochs` 404”**无法从本 worktree 源码复现**。
     8798 停止态只让 DB 相关用例 BLOCKED；若线上跑的是旧 DB 服务（v1 404），同样按 BLOCKED 处理。
  2. R1 在 DB 可用时仅 Barnard 通过；RW Aur 自定义坐标仍 HTTP 500
     （`negative dimensions are not allowed`，`photometry.is_saturated` 负维度），
     是 app 侧 P0。
  3. `/api/analyze` 缺顶层 `schema_version` 与 `result.readiness_scope`（app 侧 P0）。
  4. DB 旧 `/api/targets` 缺 `truth` 字段，导致 A.targets.db / app_db P0。
  5. B 导出对账只读本地 SQLite，**DB 服务停止也能跑**；当前 4/4 通过。
  6. F 离线 5/5 通过（黑洞代理 + 8813 子进程）。

## 快速开始

```bash
cd /Users/yiao/Documents/huellaX/项目主控/验收

# 主验收：一条命令；app 由 --start 启动，DB 用无监听端口模拟停止，view 可选
# 若 8797 确认已停，可把 --db-url 换成 http://127.0.0.1:8797
./run.sh --start \
  --base-url http://127.0.0.1:8810 \
  --db-url  http://127.0.0.1:8798 \
  --view-url http://127.0.0.1:8812 \
  --offline --offline-port 8813 \
  --report-dir report/full-db-down
echo $?   # 1：有 P0 失败 + DB 相关 P0 BLOCKED
```

报告在 `report/full-db-down/report.md`；`report.json` 的 `cases[]` 含
`status=BLOCKED` 与 `blocked_by=DB`，失败用例附期望/实际、归因、复现命令与证据 JSON。

补充跑 DB 可用形态（只读库，不改 DB）：

```bash
# 终端 A
cd 系统/数据库工具
/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3 \
  cli.py serve --port 8811 --quiet

# 终端 B
cd 项目主控/验收
./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8811 \
  --view-url http://127.0.0.1:8812 --offline --offline-port 8813 \
  --report-dir report/full-db-up
```

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
| D 任意坐标 | ① Barnard 精确坐标 → REAL 4 期 L2 S；② RW Aur → ≥1 条真实历元；③ 未覆盖天区 → `NO_LOCAL_COVERAGE` + SIM；④ `--db-down` 可选 | ①/② = P0 FAIL（DB 停时归因 DB_UNAVAILABLE）；③ DB 不可用时 BLOCKED |
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

- `golden/five_targets.json`：2026-11-14 快照的 5 目标 `mode/n/readiness/class/μ`
  （μ 容差 ±5% 或 ±1σ，`readiness`/`class` 严格，期数/耗时 P1）。
- `golden/custom_barnard.json`：R1 的 Barnard 黄金结果
  （`real/4/L2/S`，μ 与预设差 ≤1%，`real_epochs≥4`）。
- **不要在未修复时放宽容差或跳过 P0**。修复后要更新黄金样本，先跑全绿，
  再 `--update-golden`（人工确认）。

## 当前基线（2026-11-15，`report/full-db-down` 与 `report/full-db-up`）

### 主验收：DB 8798 无监听（模拟 DB 停止；app 8810 由 `--start` 启动）

- 退出码 **1**；`PASS=28 FAIL=3 WARN=1 BLOCKED=13`。
- 说明：本轮 8797 被外部进程启动，故停止态用 `--db-url http://127.0.0.1:8798`
  （无监听；app 同样拿到 `DB_UNAVAILABLE`），不杀外部服务。
- P0 失败（3）：
  1. `A.analysis_schema`：app `/api/analyze` 缺顶层 `schema_version` 与
     `result.readiness_scope`（app/T2 侧）。
  2. `D.custom_barnard`：`mode=synthetic`、`fallback=DB_UNAVAILABLE`、`real_epochs=0`
     （DB 停止导致；DB 可用时通过）。
  3. `D.custom_rw_aur`：同上 `DB_UNAVAILABLE`；DB 可用时仍 500（见补充验收）。
- P0 BLOCKED（13）：`A.targets.db`、`A.targets.app_db`、`A.epoch_ref.*`（2）、
  `A.coverage`、`D.uncovered`、`G.*`（7）。原因统一为 DB 服务不可达；报告
  「1.0 DB 状态」「2.1 红在哪 / 归因」「5. BLOCKED」三节可直接回答“哪些是 DB 停止导致”。
- 已绿：C 5/5、E 7/7、F 离线 5/5、B 导出对账 4/4、A target app/view、H 静态检查。

### 补充验收：DB 8811 只读库可用（`cli.py serve --port 8811`）

- 退出码 **1**；`PASS=40 FAIL=4 WARN=1 BLOCKED=0`。
- P0 失败（4）：
  1. `A.targets.db`：旧 `/api/targets` 缺 `truth` 对象（DB 契约）。
  2. `A.targets.app_db`：app/DB 目标 `truth` 不一致。
  3. `A.analysis_schema`：同上（app 侧）。
  4. `D.custom_rw_aur`：HTTP 500 `negative dimensions are not allowed`
     （app `photometry.is_saturated` 负维度）。
- **G 组 7/7 全绿**（Barnard 4 / RW 1 / Eps 10 / WISE 10 / R Dra 3；2.47°；
  `date_obs`/`key` 非空；`header_frames=74` 与 SQLite 一致）。
- **R1 自定义 Barnard 通过**：`real/4/L2/S/μ=8032.9`，`fallback=null`。
- `D.uncovered` 通过：`NO_LOCAL_COVERAGE` + SIM。

> 结论：当前“红”不是 T1 工具失败，而是 ① DB 服务未起（主验收）② app 缺
> `schema_version`/`readiness_scope` ③ 自定义 RW Aur 500 ④ DB 目标 `truth` 字段缺失。
> T2 的 R1 接线在 DB 可用时已对 Barnard 生效。

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
- 本工具不做科学判定，不替代产品测试；只验证接口/契约/降级行为。
