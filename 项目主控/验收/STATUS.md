# T1 验收工具 · 状态：WIP / BLOCKED

> 时间：2026-11-14 23:52 CET
> 目录：`项目主控/验收/`
> 结论：**未完成，不能当作已验收工具。**

## 已完成

- `accept.py`、`contracts/`、`golden/`、`run.sh`、`README.md`、`report/smoke` 已存在。
- 能在 app/DB 服务可用时跑出报告；最后一次 smoke：退出码 1，4 条 P0 红（R1、DB v1、date_obs/coverage 等）。
- 能区分 app-only 与 DB 相关用例。

## 未完成

- 按“DB 项目已停止”的要求，`accept.py` 的 BLOCKED 改造只改了一半；当前仍可编译，但报告语义不完整。
- `README.md` 仍停留在旧基线，未更新为 BLOCKED 说明。
- DB `/api/v1/epochs` 404，R1 无法闭环；T1 不能给出全绿。
- 报告目录 `report/` 是运行产物，未纳入版本控制。

## 现在不要做

- 不要把它当成“已验收”或“可提交”的证据。
- 不要为了让报告全绿而放宽 P0 断言或跳过 R1。
- 不要启动/重启 DB 服务；不要改 app/DB/view 源码。

## 恢复条件

1. DB 负责人恢复 `/api/v1/epochs` 与可用 DB 服务。
2. T2 完成 custom 接线并返回 `REAL` Barnard（4 期/L2/S）。
3. T1 完成 BLOCKED 语义与 README 更新，重跑 `./run.sh`，P0 全绿。

## 运行（服务可用时）

```bash
cd 项目主控/验收
./run.sh --base-url http://127.0.0.1:8790 --db-url http://127.0.0.1:8797 \
  --report-dir report/smoke
```
