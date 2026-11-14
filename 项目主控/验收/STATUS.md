# T1 验收工具 · 状态：BLOCKED 语义完成；系统验收仍红

> 时间：2026-11-15 CET
> 分支：`tool/t1-acceptance`（worktree `项目主控/_worktrees/tool-t1-acceptance`）
> 目录：`项目主控/验收/`

## 结论

T1 的 BLOCKED 语义与一条命令入口已完成，可重复回答“红在哪、哪些是 DB 停止导致”。
系统当前**不是全绿**：主验收退出码 1（3 条 P0 失败 + 13 条 P0 BLOCKED），
补充验收（DB 8811 只读库可用）退出码 1（4 条 P0 失败）。

## 已完成

- `add_blocked` 真正接入 A（DB target 源/app_db/epoch_ref/coverage）、D.uncovered、G（v1 与 header）
  与 B（SQLite 不可读时）；BLOCKED 不计 PASS，也不伪装成 FAIL。
- `report.md` 新增「1.0 DB 状态」「2.1 红在哪 / 归因」「5. BLOCKED」；失败详情带 `cause`；
  `report.json` 的 `summary` 增加 `blocked / p0_blocked / p1_blocked`，顶层增加 `db_status`。
- 退出码：无 P0 失败且无 P0 BLOCKED → 0；有 P0 失败或 P0 BLOCKED → 1；环境错误 → 2。
- `--start`：app 未运行时自动在 8810 起验收专用子进程（已占用则复用），跑完停止；
  离线子进程用 `--offline-port 8813`，不占用 8810。
- 主验收命令（约 35–42s）：
  ```bash
  # 8797 本轮被外部进程启动；8798 无监听，模拟 DB 停止
  ./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8798 \
    --view-url http://127.0.0.1:8812 --offline --offline-port 8813 \
    --report-dir report/full-db-down
  ```
  结果：`PASS=28 FAIL=3 WARN=1 BLOCKED=13`，退出码 1。
- 补充验收（DB 8811 只读）：`PASS=40 FAIL=4 WARN=1 BLOCKED=0`，退出码 1；
  G 组 7/7 全绿，R1 Barnard 自定义坐标通过（real/4/L2/S），RW Aur 仍 500。

## 当前红（主验收，DB 8798 无监听模拟停止）

| 用例 | 结果 | 归因 |
|---|---|---|
| `A.analysis_schema` | FAIL P0 | app `/api/analyze` 缺 `schema_version` / `readiness_scope` |
| `D.custom_barnard` | FAIL P0 | DB 停止 → `DB_UNAVAILABLE` / synthetic / 0 real（DB 可用时通过） |
| `D.custom_rw_aur` | FAIL P0 | DB 停止 → `DB_UNAVAILABLE`；DB 可用时仍 HTTP 500（app bug） |

DB 相关 P0 共 13 条 BLOCKED：A 5 条、D.uncovered、G 7 条；原因与复现见报告。

## 未完成 / 风险

- `/api/v1/epochs` 404 **无法从当前 worktree 源码复现**：用 `cli.py serve --port 8811`
  启动后 v1 正常、G 全绿。8798 停止态与 8797 停服对 app 等价；若线上跑旧 DB 服务，仍会 404，
  此时报告为 `legacy-only` + BLOCKED。
- R1 的 RW Aur 500（`negative dimensions are not allowed`）是 app 侧 P0，T1 不改 app。
- A.targets.db/app_db 的 `truth` 缺失是 DB 旧 `/api/targets` 契约问题。
- H 截图仍为 `WARN: manual`（不引入 Playwright）。
- `report/` 是运行产物，未提交（`.gitignore`）。

## 恢复条件

1. DB 负责人确认线上 DB 服务版本/端口；需要时用只读 8811 跑补充验收。
2. app 补 `schema_version` / `readiness_scope`，修自定义 RW Aur 500。
3. DB 旧 `/api/targets` 补 `truth`（或 app 明确以 `/api/v1` 为唯一源）。
4. 上述修复后重跑主验收，目标：P0 0 失败、0 BLOCKED、退出码 0。
