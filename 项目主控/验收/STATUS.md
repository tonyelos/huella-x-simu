# T1 验收工具 · 状态：全量全绿（main 修复版 app）

> 时间：2026-11-15 CET
> 分支：`tool/t1-acceptance`（worktree `项目主控/_worktrees/tool-t1-acceptance`）
> 目录：`项目主控/验收/`

## 结论

在 main `6b3098d`（含 app “自定义坐标 0.05° 命中复用预设”修复）的 app 上跑全量 T1：

**45 PASS / 0 FAIL / 1 WARN / 0 BLOCKED，46 条用例，50.6s，退出码 0。**

唯一 WARN 是 `H.screenshot`（`WARN: manual`，不引入 Playwright）。
报告：`report/full-main/report.{json,md}`；单跑 RW：`report/rw-only/report.md`。

## 本轮变更

- `D.custom_rw_aur` 断言更新：不再要求 `real_epochs≥1`，改为
  - HTTP 200；`mode=real|hybrid`；`matched_target_id="rw_aur"`；`class="V"`；
  - 与预设 `rw_aur` 的 mode/readiness/class 严格一致，μ ±5%；
  - **HYBRID 允许 `real_epochs=0`**（纯 SIM 回退是正确结果）。
- `contracts/analysis.py::summary` 增加 `matched_target_id` / `source` 提取。
- `D.custom_rw_aur` 在 DB 停止且 `matched_target_id` 无法确认时记 BLOCKED
  （`blocked_by=DB`），不伪造 PASS，也不当作 app 缺陷。
- 保留 `D.coord_matches_preset`：5 个官方目标自定义坐标 vs 预设，mode/readiness/class 严格、μ±5%。
- 报告仍保留 DB 停止 / `/api/v1/epochs` 404 的 BLOCKED 语义与归因。

## RW Aur 前后对比（`D.custom_rw_aur`）

| 版本 | 实际响应 | 旧/新断言 | 结果 |
|---|---|---|---|
| 修复前（旧 T1） | hybrid/8/L2/V、`real_epochs=0`、`matched=rw_aur`、μ=49.3 | 要求 `real_epochs≥1` | FAIL（`CUSTOM_RW_REAL_EPOCHS`，HYBRID 纯 SIM 被误判） |
| 修复后（新 T1） | 同上，预设 hybrid/8/L2/V/μ=49.3 | HTTP 200、mode=real\|hybrid、matched=rw_aur、class=V、与预设 mode/readiness/class/μ 一致；允许 0 | **PASS** |

## 验证命令

```bash
# 全量（main 修复版 app 在 8810；DB 8797；--start 复用已有 app）
./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8797 \
  --view-url http://127.0.0.1:8812 --offline --offline-port 8813 \
  --report-dir report/full-main
echo $?   # 0

# 单跑 RW
./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8797 \
  --view-url http://127.0.0.1:8812 --only D.custom_rw_aur \
  --report-dir report/rw-only
echo $?   # 0，PASS=1
```

## 历史 / 兼容性

- `report/full-db-down`（28 PASS / 4 FAIL / 13 BLOCKED）与 `report/full-db-up`
  （40 PASS / 5 FAIL）是修复前 app 的历史基线，只用于验证 BLOCKED 语义与旧回归。
- 修复版 app 的自定义命中走本地目录，DB 停止时仍可能与预设一致；若 `matched_target_id`
  无法确认则 D2 BLOCKED。DB 相关 P0 契约（A.epoch_ref/coverage、G）仍按 BLOCKED。
- `report/` 是运行产物，未提交（`.gitignore`）。

## 风险 / 限制

- `report/full-main` 的 green 使用手动在 8810 起的 main 版本 app；报告的 `source_hashes`
  仍取本 worktree（T1 只读自身 worktree），合并 main 后重跑才完全对齐。
- 本 worktree 的 `系统/app` 仍是修复前版本；直接 `--start` 会起旧版，不能复现 green。
- H 截图仍为 `WARN: manual`。
- 本工具只做接口/契约/降级验证，不做科学判定。
