# T1 · 验收与契约工具（Acceptance & Contract Harness）

一条命令验证三个工具是否仍然满足官方要求与跨工具契约，输出机器可读的
`report.json`、人读的 `report.md` 与证据 `artifacts/`。

- 类型：内部 QA 工具，不对外、不进入产品 UI。
- 边界：**只读** `系统/app/`、`系统/数据库工具/`、`系统/视图工具/`；所有写入都在本目录的
  `report/` 与 `/tmp`。
- 依赖：Python standard library（可选 numpy 仅用于读 NPZ，本工具不依赖）。
  不依赖 pytest / jsonschema / playwright。
- 默认**不启动**任何服务；app/DB 健康检查失败时退出码 2。
- `--offline` 时才会在 **8810** 启动一个只属于验收的 app 子进程（黑洞代理）。

## 快速开始

```bash
cd /Users/yiao/Documents/huellaX/项目主控/验收

# 1) 先确保 app 8790 和 DB 8797 已启动（本工具不会替你启动）
./run.sh --base-url http://127.0.0.1:8790 --db-url http://127.0.0.1:8797 \
         --view-url http://127.0.0.1:8830 \
         --report-dir report/smoke --offline
echo $?   # 0 无 P0 失败 / 1 有 P0 失败 / 2 环境错误
```

报告在 `report/smoke/report.md`，失败用例附期望/实际、复现命令与证据 JSON。

当前工作区没有 git，报告里的版本信息会写 `no-git`；数据快照用 sha256 记录。

### 端口隔离

默认入口参数即可。需要和正在被其他人使用的服务隔离时，用：

| 服务 | 隔离端口 | 启动方式 |
|---|---|---|
| app | 8810 | 由 `--offline` / `--db-down` 自动启动；不要手动占 8810 |
| DB | 8811 | `cd 数据库工具 && ./start.sh 8811`，然后 `--db-url http://127.0.0.1:8811` |
| view | 8812 | `cd 视图工具 && python3 -m http.server 8812 --bind 127.0.0.1`，然后 `--view-url http://127.0.0.1:8812` |

本工具**不会**杀用户已在跑的服务；只终止自己启动的 8810 子进程。

## 用例组

| 组 | 内容 | 级别 |
|---|---|---|
| A 契约 | `Target`（app/DB/view 三源、7 条、8 字段、`coord_unc_arcsec`）；`EpochRef`（两入口同形、十字段、`date_obs` 非空、无重复）；`Coverage`（默认 2.47、`C*`、不泄露 L0–L3）；`AnalysisResult`（`schema_version`、`provenance`、`fallback_reason`、`readiness_scope`） | P0；视图源/`coord_unc`/`real_epochs` 为 P1 |
| B 导出对账 | `cli.py export all` + `tools/check_compat.py` 四项 OK（fields / epoch_catalog / targets / week_index） | P0 |
| C 5 目标 HTTP | 5 × `POST /api/analyze`（`mode=auto,fov=0.25`）：HTTP 200、schema、`mode`/`provenance` 自洽、`readiness`/`class` 严格黄金、μ 在 ±5% 或 ±1σ、<30s | 500/class/mode/readiness/μ = P0；期数/耗时 = P1 |
| D 任意坐标 | ① Barnard 精确坐标 → REAL 4 期 L2 S；② RW Aur → ≥1 条真实历元；③ 未覆盖天区 → `NO_LOCAL_COVERAGE` + SIM；④ `--db-down` 可选 | 任一不符 = P0 |
| E 错误路径 | `sample` 不 500；`fov=-0.2/0/999` → 400；target+冲突坐标不静默；非法 JSON/缺字段 → 400 | sample/FOV 500 = P0；其余 = P1 |
| F 离线 | 8810 子进程 + `HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:9` + `NO_PROXY=127.0.0.1,localhost`；5 目标 200 且与在线一致 | P0 |
| G DB 一致性 | `/api/v1/epochs` 两入口同形：Barnard 4 / RW 1；Eps/WISE/R Dra 10/10/3；`date_obs`/`key` 非空；无重复；`coverage` 默认 2.47；`header_frames` 与 SQLite 实际一致 | P0 |
| H 视图工具 | 静态资源 200；`js/main.js` 不含 `../系统/app/index.html`、`method: 'HEAD'`、`toFixed(5)`；截图 = `WARN: manual` | P1/WARN，不阻塞提交门槛 |

## 参数

| 参数 | 说明 |
|---|---|
| `--base-url` | app 地址（默认 `http://127.0.0.1:8790`） |
| `--db-url` | DB 地址（默认 `http://127.0.0.1:8797`） |
| `--view-url` | 视图工具地址（可选，默认 `http://127.0.0.1:8830`） |
| `--report-dir` | 报告目录（默认 `report/<timestamp>`；相对本目录） |
| `--offline` | 启动 8810 子进程 + 黑洞代理，跑 F 组 |
| `--update-golden` | 用本次 C 组实测更新 `golden/five_targets.json`；非全绿时必须 `--confirm`，交互式还要输入 `yes` |
| `--only` / `--skip` | 按组或用例过滤：`--only C`、`--only C,D`、`--only C.barnard`、`--skip H` |
| `--no-start` | 只测已运行服务；与 `--offline`/`--db-down` 冲突时对应用例记 WARN |
| `--db-down` | 可选 D4：把 `SPHEREX_DB_URL` 指向黑洞端口，验证预设回退与自定义降级 |
| `--offline-port` | 离线/D4 子进程端口（默认 8810） |
| `--http-timeout` | 单次 HTTP 超时秒（默认 60） |

## 输出与退出码

```text
report/<name>/
  report.json     # schema_version=1.0；cases[] 含 id/group/severity/status/expected/actual/evidence/repro
  report.md       # 环境、分组表、失败详情、复现命令
  artifacts/      # 实际 HTTP/DB 输出、导出日志、子进程日志
```

- `0`：没有 P0 失败（P1/WARN 不阻塞；H 组明确不进入提交硬门槛）。
- `1`：至少一条 P0 失败。
- `2`：环境错误（系统/app/DB 不可达、8810 子进程起不来、报告目录不可写）。

`report.md` 顶部会写：git commit/tag（无 git 写 `no-git`）、数据快照 sha256、
服务端口/版本、Python 版本；每条用例都有复现命令。

## 黄金样本

- `golden/five_targets.json`：2026-11-14 快照的 5 目标 `mode/n/readiness/class/μ`
  （μ 容差 ±5% 或 ±1σ，`readiness`/`class` 严格，期数/耗时 P1）。
- `golden/custom_barnard.json`：T2 完成后的 R1 黄金结果
  （`real/4/L2/S`，μ 与预设差 ≤1%，`real_epochs≥4`）。
- **不要在未修复时放宽容差或跳过 P0**。修复后要更新黄金样本，先跑全绿，
  再 `--update-golden`（人工确认）。

## 当前基线（2026-11-14 23:48 快照，`report/smoke`）

工具第一次跑是红的，这是基线失败而不是工具失败。当前快照的 P0 红灯（4 条）：

1. **D.custom_barnard / D.custom_rw_aur**：运行中的 app（8790）自定义坐标仍是
   `synthetic`；`系统/app/` 源码已在并行写入 T2，但**服务进程未重启**，验收只认运行中的服务。
2. **A.analysis_schema**：`/api/analyze` 缺顶层 `schema_version` 与
   `result.readiness_scope`（02 §5.1 要求）。
3. **G.v1_epochs.counts**：WISE 0855 两入口都是 9 条，T8 期望 10 条
   （`2025W44_2B_0321_1` 与 `0322_1` 相隔 4.7 分钟，按 0.02 d 去重被合并；
   `target_epochs` 里是 10 个不同 obsid）。

已转绿的基线项：`sample` 500、FOV 500、DB v1 形状/日期/去重/2.47°、
视图工具路径/HEAD/toFixed(5)、`export + check_compat` 四项 OK、离线 5/5。
视图工具为可选组件，其失败为 P1/WARN（当前只剩 `H.screenshot` 的 `WARN: manual`）。

> **T2 预览（非正式基线）**：用当前源码在 8810 启动 app 并指向 8811 DB 时，
> `D.custom_barnard` 已能 PASS（REAL 4 期 / L2 / S / μ=8032.9），
> 但 `D.custom_rw_aur` 返回 HTTP 500（`photometry.is_saturated` 负维度），
> 是 app 侧 T2 的待修 bug；正式验收仍以 8790 运行中的服务为准。

> 注意：三工具在验收期间可能被并行编辑。报告里的 `ENV.source_drift` 会记录
> 运行期间变化的源文件；此时结果只绑定该次快照，建议等代码稳定后重跑。

## 只跑某组 / 单条复现

```bash
# 契约 + DB
./run.sh --db-url http://127.0.0.1:8811 --view-url http://127.0.0.1:8812 --only A,G

# 离线（需要 8810 空闲）
./run.sh --report-dir report/offline --offline --only F

# 只跑 C 组中 barnard
./run.sh --report-dir report/one --only C.barnard

# 手工 R1 复现
curl -s -X POST http://127.0.0.1:8790/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"ra":269.452077,"dec":4.693365,"fov":0.25}'

# 手工 DB v1 两入口
curl -s 'http://127.0.0.1:8797/api/v1/epochs?target_id=barnard'
curl -s 'http://127.0.0.1:8797/api/v1/epochs?ra=269.452077&dec=4.693365&fov=0.25'
```

## 文件布局

```text
项目主控/验收/
  run.sh                 # 一条命令入口（选 Python、切到本目录）
  accept.py              # 主程序：用例、HTTP/DB 探针、报告
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
```

## 已知限制

- H 组不做浏览器截图（不引入 Playwright）；`H.screenshot` 固定为 `WARN: manual`。
  视图工具的静态检查与 GET 200 仍会执行。
- B 组依赖 DB 导出与 app/view 数据当前一致；并行导入期间可能出现瞬时漂移，
  报告会记录当时的 sha256。
- G 组的 `header_frames` 真值只在本地 `系统/数据库工具/var/catalog.sqlite` 存在时核对；
  远程 DB 只校验 health 自报值。
- F 组固定使用 8810；如果 8810 已被占用，会报环境错误而不是杀进程。
- 本工具不做科学判定，不替代产品测试；只验证接口/契约/降级行为。
