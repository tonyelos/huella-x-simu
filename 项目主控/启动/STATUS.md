# T5 统一启动器 · 状态：PASS（sandbox）

> 时间：2026-11-15 00:04 CET
> 目录：`项目主控/启动/`（worktree `tool/t5-launcher`，基线 `b24f8ee`）
> 本轮：复核迁移后路径 + sandbox 端口启动/状态/停止 + 三项安全 smoke。

## 本轮复验（sandbox 8900/8907/8940）

| 项 | 结果 |
|---|---|
| start | 3/3 全绿，用时 2s，exit 0 |
| status | 3/3，DB `schema_version=6`、app `version=0.2.0`，exit 0 |
| 重复 start | 识别“三个服务已在运行”，不重复起进程（PID 不变），exit 0 |
| stop | view → app → DB 全部停止，8900/8907/8940 全释放，无残留进程，exit 0 |
| `--offline` | 三服务 3/3；stop 后端口全释放 |
| 端口冲突 | 占位进程占 8900：输出 PID + 完整命令行，拒绝启动 exit 1；占位进程未被杀，未自动换端口 |
| 失败回滚 | view 启动失败：打印 view 日志尾 → 回滚停掉本次已起的 app/DB → 三端口释放，exit 1 |
| 防误杀 | `view.pid` 指向命令行不匹配的进程：拒绝停止、保留 PID 文件，该进程仍存活，exit 1 |
| 静态检查 | `bash -n`（4 个脚本）+ `python3 -m py_compile probe.py` 通过 |
| 默认端口 | 8790/8797/8830 未改；default profile 只读 status 为 0/3（服务本就没启动） |

## 路径迁移

- 脚本内部（start/status/lib）已使用 `系统/app`、`系统/数据库工具`、`系统/视图工具`。
- 本轮修正 README 中残留的迁移前路径：端口表的 `数据库工具/start.sh`、`视图工具/start.sh`，DB 初始化提示、故障处理表。代码启动/停止/探针路径无需改动。

## 默认端口

- app **8790**、DB **8797**、view **8830**；不自动换端口。
- 默认三服务当前未运行（DB 按任务保持停止），`./status.sh` 显示 0/3 属预期。
- 本轮未占用 8790/8797/8830；8810–8812 为其他 worktree 的进程，与本启动器无关，未触碰。

## 复验命令

```bash
cd 项目主控/启动

# sandbox 成功路径
LAUNCH_APP_PORT=8900 LAUNCH_DB_PORT=8907 LAUNCH_VIEW_PORT=8940 ./start_all.sh
LAUNCH_APP_PORT=8900 LAUNCH_DB_PORT=8907 LAUNCH_VIEW_PORT=8940 ./status.sh
LAUNCH_APP_PORT=8900 LAUNCH_DB_PORT=8907 LAUNCH_VIEW_PORT=8940 ./start_all.sh   # no-op
LAUNCH_APP_PORT=8900 LAUNCH_DB_PORT=8907 LAUNCH_VIEW_PORT=8940 ./stop_all.sh

# 默认端口（DB/app/view 释放后）
./start_all.sh
./status.sh
./stop_all.sh
```

## 本轮 smoke 复现

- 端口冲突：先 `python3 -m http.server 8900 --bind 127.0.0.1 &`，再跑上面的 sandbox start → 应 exit 1 且占位进程存活。
- 失败回滚：`PATH` 前置一个只在 `-m http.server` 时退出的 `python3` 包装器，`LAUNCH_PYTHON` 指向真实 Python，再跑 start → 应回滚且三端口释放。
- 防误杀：`echo <无关PID> > run/sandbox-8900-8907-8940/view.pid`，跑 stop → 应拒绝停止、保留 PID 文件、exit 1。

## 遗留 / 风险

- 默认端口 8790/8797/8830 的成功路径未在本轮实跑（任务要求不占用这些端口；且 DB 服务保持停止）；启动链路已用同脚本在 sandbox 端口验证。
- 视图工具 `start.sh` 的 bind 预检在 TIME_WAIT 期间可能误报（见 README 故障表）；生产 8830 不受影响。
- 默认 profile 的 `status` 在 DB 未启动时报 0/3，属预期，不是回归。
