# T1 验收工具 · 状态：全量全绿（main 官方基准版 app）

> 时间：2026-11-15 CET
> 分支：`tool/t1-acceptance`（worktree `项目主控/_worktrees/tool-t1-acceptance`，已 rebase `origin/main`）
> 目录：`项目主控/验收/`

## 结论

在 main 官方基准版 app（`1c17c3e`）上跑全量 T1：

**45 PASS / 0 FAIL / 1 WARN / 0 BLOCKED，46 条用例，49.7s，退出码 0。**

唯一 WARN 是 `H.screenshot`（`WARN: manual`，不引入 Playwright）。
报告：`report/full-main/report.{json,md}`；Barnard 单测：`report/prior-check`、`report/offline-prior`。

## 本轮变更（官方基准 Barnard `S→L`）

- `golden/five_targets.json`：Barnard `class` `S→L`；`mode=real/n=4/readiness=L2/μ=8033.0/μ_err=9899.0` 原样保留；
  其他 4 目标不动。
- `golden/custom_barnard.json`：`class` `S→L`；μ 容差与 `real_epochs≥4` 原样保留。
- 新增 `barnard_prior_problems()`，在 **C.barnard / D.custom_barnard / F.offline.barnard** 断言：
  - `class=="L"`；
  - `result.catalog_motion.basis=="catalog_prior"`；
  - `result.moving is False`；
  - `mu_signif<3`（读 `result.fingerprint.mu_signif`，等价读 `catalog_motion.measurement_significance`）；
  - `quality` 含 `CATALOG_PRIOR_MOTION`、`MEASUREMENT_NOT_SIGNIFICANT`；
  - 无 `discovery_claim=true`、无“新天体”正结论；
  - `public_text/caveats` 明确“星表先验、不是独立实测”。
- `barnard_prior_actual()` 把 `basis/moving/mu_signif/quality` 写进 C/D/F 的 actual，报告可直接看出不是实测运动。
- 负向自检：手动把 class 改 S、basis 改 measured、moving 改 True、mu_signif 改 3.5、
  删 quality 标记、discovery_claim 改 true、public_text 改“新天体” → 分别命中
  `BAR_CLASS` / `BAR_PRIOR_BASIS` / `BAR_MOVING` / `BAR_MU_SIGNIF` / `BAR_QUALITY` /
  `BAR_DISCOVERY_CLAIM` / `BAR_DISCOVERY_TEXT`，断言非空转。
- `F.offline.5of5` 随黄金更新恢复 5/5（Barnard 离线也是 L + catalog_prior）。

## Barnard 断言实际值（C/D/F 共用）

| 字段 | 期望 | 实际 |
|---|---|---|
| class | L | L |
| catalog_motion.basis | catalog_prior | catalog_prior |
| moving | false | False |
| mu_signif | <3 | 0.8115 |
| quality | 含两个标记 | `SATURATED_SOURCE, LARGE_CENTROID_ERROR, CATALOG_PRIOR_MOTION, MEASUREMENT_NOT_SIGNIFICANT` |
| μ / μ_err | 保留实测 | 8032.9 / 9898.9 mas/yr |
| discovery_claim / 新天体 | 不允许 | 无 |

## RW Aur 前后对比（`D.custom_rw_aur`）

| 版本 | 实际响应 | 旧/新断言 | 结果 |
|---|---|---|---|
| 修复前（旧 T1） | hybrid/8/L2/V、`real_epochs=0`、`matched=rw_aur`、μ=49.3 | 要求 `real_epochs≥1` | FAIL（`CUSTOM_RW_REAL_EPOCHS`，HYBRID 纯 SIM 被误判） |
| 修复后（新 T1） | 同上，预设 hybrid/8/L2/V/μ=49.3 | HTTP 200、mode=real\|hybrid、matched=rw_aur、class=V、与预设 mode/readiness/class/μ 一致；允许 0 | **PASS** |

## 验证命令

```bash
# 全量（本 worktree 已 rebase origin/main；app 由 --start 起，DB 8797）
./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8797 \
  --view-url http://127.0.0.1:8812 --offline --offline-port 8813 \
  --report-dir report/full-main
echo $?   # 0

# 只跑 Barnard C/D
./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8797 \
  --only C.barnard,D.custom_barnard --report-dir report/prior-check
echo $?   # 0
```

## 历史 / 兼容性

- `report/full-db-down`（28 PASS / 4 FAIL / 13 BLOCKED）与 `report/full-db-up`
  （40 PASS / 5 FAIL）是官方基准前的历史基线，只用于验证 BLOCKED 语义与旧回归。
- DB 停止 / `/api/v1/epochs` 404 的 BLOCKED 语义保留；D2 在 `matched_target_id` 无法确认时 BLOCKED。
- `report/` 是运行产物，未提交（`.gitignore`）。

## 风险 / 限制

- Barnard 的 `class=L` 依赖 Gaia PM 先验与 identification；若先验/识别不可用，
  `BAR_PRIOR_*` 会红，不能用“任意 class”放行。
- H 截图仍为 `WARN: manual`。
- 本工具只做接口/契约/降级验证，不做科学判定。
