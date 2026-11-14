# T5 统一启动器 · 状态：PASS（sandbox）

> 时间：2026-11-14 23:52 CET
> 目录：`项目主控/启动/`

## 验收结果

| 项 | 结果 |
|---|---|
| sandbox 启动 | `8900/8907/8940` 三服务 1 s 全绿 |
| HTTP | app `/api/health` 200、DB `/api/health` 200、view `/` 200 |
| 重复启动 | 识别已在运行，不重复起进程 |
| stop | 端口全释放、PID 文件清理、无孤儿 |
| 端口冲突 | 明确报错，不杀占位进程、不自动换端口 |
| 失败回滚 | 健康失败时停掉本次启动的服务 |
| 防误杀 | PID 命令行不匹配时拒绝停止 |
| offline | `--offline` 三服务全绿 |
| 迁移后复验 | `8900/8907/8940` 再次 3/3 全绿，stop 后全释放 |

## 默认端口

- app **8790**、DB **8797**、view **8830**；不自动换端口。
- 当前默认三服务未运行（用户已停 DB/app）；`./status.sh` 会显示 0/3，属预期。
- 成功路径已在 sandbox 端口验证；默认端口需在服务释放后再跑一次 `start_all.sh`。

## 复验

```bash
cd 项目主控/启动

# sandbox 成功路径
LAUNCH_APP_PORT=8900 LAUNCH_DB_PORT=8907 LAUNCH_VIEW_PORT=8940 ./start_all.sh
LAUNCH_APP_PORT=8900 LAUNCH_DB_PORT=8907 LAUNCH_VIEW_PORT=8940 ./status.sh
LAUNCH_APP_PORT=8900 LAUNCH_DB_PORT=8907 LAUNCH_VIEW_PORT=8940 ./stop_all.sh

# 默认端口（服务释放后）
./start_all.sh
./status.sh
./stop_all.sh
```
