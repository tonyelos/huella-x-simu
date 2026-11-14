# T5 · 统一启动器（Launcher / Health）

> 一句话：一条命令按 **DB → app → view** 顺序启动三服务并做健康检查，打印三个 URL；一条命令只停自己启动的进程。

- 工作区根目录：`/Users/yiao/Documents/huellaX`
- 本目录：`项目主控/启动/`
- 依赖：bash + Python 标准库；不新增第三方依赖。需要系统命令 `lsof`、`pgrep`、`tail`、`sed` 和 `python3`（macOS 自带；视图工具的 `start.sh` 使用 `python3`）。
- 生产默认端口：**app 8790 / DB 8797 / view 8830**（与 `项目主控/02_架构设计与集成契约.md` §8 一致，脚本不会自动改）。

---

## 1. 快速开始

```bash
cd /Users/yiao/Documents/huellaX/项目主控/启动

./start_all.sh                 # 生产默认 8790/8797/8830
./status.sh                    # 三服务 health、PID、端口、数据快照
./stop_all.sh                  # 只停本启动器启动的进程

./start_all.sh --offline       # 可选：给 app 设置黑洞代理，验证离线缓存路径
```

三服务就绪时的输出形如：

```text
DB   http://127.0.0.1:8797              ok
app  http://127.0.0.1:8790              ok
view http://127.0.0.1:8830/             ok

三服务已就绪（用时 1s）：
  DB   http://127.0.0.1:8797              ok（pid=...）
  app  http://127.0.0.1:8790              ok（pid=...）
  view http://127.0.0.1:8830/             ok（pid=...）
```

重复执行 `./start_all.sh` 会识别“三服务已在运行（启动器管理）”，**不重复起进程**，退出 0。

---

## 2. 端口与 sandbox 覆盖（显式，不自动换端口）

| 服务 | 生产默认 | 启动命令（脚本内部） | 健康检查 |
|---|---:|---|---|
| DB API | **8797** | `数据库工具/start.sh 8797` | `GET /api/health` → 200 且 `ok=true`、`service=spherex-db` |
| MoveScope | **8790** | `系统/app/server.py --port 8790 --host 127.0.0.1` | `GET /api/health` → 200 且 `ok=true`、`service=MoveScope` |
| 视图工具 | **8830** | `视图工具/start.sh 8830` | `GET /` → 200 |

### 测试用的 sandbox 端口（显式覆盖）

生产默认值永远不变；只有显式传参/设环境变量时才启用 sandbox profile。三个端口要一起改，避免和正在运行的生产服务串在一起：

```bash
# 方式 A：环境变量
LAUNCH_APP_PORT=8890 LAUNCH_DB_PORT=8897 LAUNCH_VIEW_PORT=8930 ./start_all.sh
LAUNCH_APP_PORT=8890 LAUNCH_DB_PORT=8897 LAUNCH_VIEW_PORT=8930 ./status.sh
LAUNCH_APP_PORT=8890 LAUNCH_DB_PORT=8897 LAUNCH_VIEW_PORT=8930 ./stop_all.sh

# 方式 B：命令行参数（优先级高于环境变量）
./start_all.sh --app-port 8890 --db-port 8897 --view-port 8930
./status.sh    --app-port 8890 --db-port 8897 --view-port 8930
./stop_all.sh  --app-port 8890 --db-port 8897 --view-port 8930
```

- 任一端口与生产默认值不同，就进入 **sandbox profile**：
  - PID：`run/sandbox-<app>-<db>-<view>/`
  - 日志：`logs/sandbox-<app>-<db>-<view>/`
  - 与 default profile 的 PID/日志/停止范围完全隔离；`stop_all.sh` 不会碰 8790 等服务。
- **status/stop 必须带同一组端口参数**（或同一组环境变量），否则会操作 default profile。
- sandbox 仅用于本地启动链路验证；app 前端走 same-origin `/api/...`，生产/演示仍应使用默认端口。

---

## 3. 目录、日志、PID

```text
项目主控/启动/
  start_all.sh       # 启动 + 健康检查 + 失败回滚
  stop_all.sh        # view → app → DB 停止（TERM 5s → KILL）
  status.sh          # 只读状态报告
  lib.sh             # 公共逻辑（端口/profile/PID/探针/停止）
  probe.py           # Python stdlib 探针（HTTP/SQLite/最新文件）
  README.md
  logs/              # 运行时生成；服务日志
  run/               # 运行时生成；PID 文件
```

| profile | PID | 日志 |
|---|---|---|
| default（8790/8797/8830） | `run/db.pid`、`run/app.pid`、`run/view.pid` | `logs/db.log`、`logs/app.log`、`logs/view.log` |
| sandbox（如 8890/8897/8930） | `run/sandbox-8890-8897-8930/*.pid` | `logs/sandbox-8890-8897-8930/*.log` |

- PID 文件内容就是进程 PID；停止时会校验该 PID 的命令行包含预期片段（`cli.py serve --port <db>` / `系统/app/server.py --port <app>` / `http.server <view>`），避免 PID 复用误杀。
- 启动/停止/健康结果会在日志中写 ISO 时间戳行，例如：
  `[2026-11-14T23:44:38+0100] === start view port=8941 profile=... ===`、
  `[2026-11-14T23:44:39+0100] === healthy view port=8941 ===`、
  `[2026-11-14T23:44:39+0100] === stop view pid=... ===`；
  失败时写 `health FAILED ...` 并附原因。
  服务自身输出原样保留（不套管道，避免 PID 错位或留下日志管道孤儿）。
- 日志为追加模式；排查一次启动失败时，看该服务日志 **最后 50 行**（start 失败时会自动打印）。

---

## 4. 启动器做了什么

1. 参数/端口校验；同一 profile 不允许端口重复。
2. 检查 PID 文件里的服务是否仍在运行：
   - 三个都健康 → 打印“已在运行”，退出 0，不重复启动；
   - 部分运行/不健康 → 明确报错并提示先 `stop_all.sh`；
   - 进程已死 → 清理陈旧 PID 文件；
   - PID 存活但命令行不匹配 → 拒绝启动，不杀、不删，等人工确认。
3. 数据库初始化检查：缺 `数据库工具/var/catalog.sqlite` 时提示先 `tools/import_all.sh` 或 `cli.py init`，不会静默启动。
4. 端口预检 8790/8797/8830（sandbox 时为对应端口）：被占则打印 **PID + 完整命令行**，拒绝启动，**不自动换端口、不杀进程**。
5. 按 DB → app → view 顺序启动；每步最多轮询 15 s（200 ms 间隔）：
   - DB/app 要求 200 + `ok=true`，并校验 `service` 字段；
   - view 要求 `GET /` 200；
   - 健康通过后再做 **进程归属校验**：PID 文件进程存活、命令行匹配、且该端口监听者就是它（防止别的进程冒名返回 200）。
6. 任一步失败：打印该服务最后 50 行日志 → 回滚停止本次已启动的服务 → 退出 1。
7. 成功：打印三个 URL、PID、git 信息、状态/停止命令。

`--offline` 仅影响 app 进程环境：
`HTTPS_PROXY=http://127.0.0.1:9`、`HTTP_PROXY=...`、`NO_PROXY=127.0.0.1,localhost`，并提示“离线模式：IRSA 不可达，使用本地缓存”；DB/view 不需要外网。

---

## 5. status 报告字段

```text
统一启动器 status（profile=default）
  时间 / 工作区 / git / PID 目录 / 日志目录

[DB] 数据库 API（spherex-db）
  url / pid（启动器管理或外部进程）/ port（监听 PID）/ health

[app] MoveScope
  ...
[view] 视图工具（静态页）
  ...

[数据快照]
  DB schema_version: <n>        # 只读打开 SQLite 的 meta 值（随库迁移变化），不要求 DB 服务在跑
  系统/app/data 最新文件: <ISO 时间>  <相对路径>  <bytes>

[结论] DB=ok app=ok view=ok → 3/3 健康
```

- `status.sh` 只读；退出码 0 = 3/3 健康，1 = 有服务不健康（打印仍完整）。
- 若端口上有非本启动器启动的进程，会显示其 PID 与命令行，但不会停止它。

---

## 6. 停止行为

- 顺序：**view → app → DB**。
- 只处理本 profile 的 PID 文件；先 `TERM`，最多等 5 s，仍存活再 `KILL`。
- 进程已死 → 清理陈旧 PID 文件；PID 存活但命令行不匹配 → 拒绝停止、保留 PID 文件并报错（安全优先）。
- 停止后打印端口释放情况与残留进程匹配情况；**不会 `pkill -f python`**，不会碰非本启动器进程。
- 重复 `stop_all.sh` 是幂等的：无可停服务时打印“无 PID 文件”，退出 0。

---

## 7. 常见错误与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `端口 ... 已被占用` | 旧服务/其他进程占用 8790/8797/8830 | 按输出 PID 确认后 `kill <PID>`；或改用一组显式 sandbox 端口。启动器不会自动换端口 |
| `数据库未初始化（缺少 数据库工具/var/catalog.sqlite）` | 从未导入/初始化 | `cd 数据库工具 && PYTHON=<python> ./tools/import_all.sh`（或 `PYTHON=<python> ./cli.py init`） |
| `PYTHON=... 不可执行或缺少 numpy/Pillow` | Python 路径不对 | `PYTHON=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3 ./start_all.sh` |
| view 健康失败、日志里有 `端口 8930 已被占用` | 视图工具 `start.sh` 自己的 bind 预检在 TIME_WAIT 期间可能误报 | 等 ~60 s 让 TIME_WAIT 清掉，或换一组 sandbox 端口；生产 8830 正常时不受影响 |
| view 健康失败、日志里有 `Address already in use` | 端口在预检后被抢 | 启动器会打印最后 50 行日志并回滚已启动的 DB/app；释放端口后重试 |
| app 健康失败，日志 `ModuleNotFoundError: numpy` | 启动用了系统 python3 | 用 `PYTHON=...` 指定工作区自带解释器（脚本会自动优先尝试它） |
| `PID 文件存在，但命令行不匹配` | PID 复用或人工替换了进程 | 人工确认该 PID 后删除对应 `run/**/*.pid`；启动器不会杀它 |
| `另一个 start_all.sh 正在运行` | 同时启动了两次 | 等前一个结束；若确认是崩溃残留，删除 `run/.../.lock`（start 会自动清理 PID 已死的陈旧锁） |
| 视图页面 404 | 视图工具静态根不对/文件缺失 | 直接 `curl -i http://127.0.0.1:8830/`；检查 `视图工具/index.html`。启动器只负责端口与 200 |
| `status.sh` 报 DB FAIL 但 DB 文件在 | DB 服务没起/端口不对 | 先 `./start_all.sh` 或核对是否用了同一组 sandbox 端口 |

---

## 8. 与 T1 验收工具的关系

标准顺序：**先启动器，后验收**。

```bash
cd /Users/yiao/Documents/huellaX/项目主控/启动
./start_all.sh
./status.sh          # 三服务 ok 后再跑验收

cd /Users/yiao/Documents/huellaX
项目主控/验收/run.sh  # T1 工具（该目录当前可能尚未生成；生成后按此顺序执行）
```

- 启动器只负责“三服务在位 + 端口正确 + 失败可定位”，不替代 T1 的契约/数据验收。
- 演示机默认离线时：`./start_all.sh --offline` → 冻结 → 验收。

---

## 9. 退出码

| 脚本 | 0 | 非 0 |
|---|---|---|
| `start_all.sh` | 启动成功，或三服务已健康（no-op） | 1 = 端口冲突/DB 未初始化/健康失败且已回滚；2 = 参数错误 |
| `status.sh` | 3/3 健康 | 1 = 有服务不健康；2 = 参数错误 |
| `stop_all.sh` | 全部停止成功（端口若被非本启动器进程占用只报告） | 1 = 停止失败/start 锁被占；2 = 参数错误 |

---

## 10. 边界（非目标）

- 不做进程守护、Docker、nginx、launchd/systemd；本阶段只有脚本。
- 不自动扫描端口、不自动换端口。
- 不修改 `系统/app/`、`系统/数据库工具/`、`系统/视图工具/` 源码。
- 不 `pkill -f python`；只停 `run/*.pid` 中命令行校验通过的进程。
