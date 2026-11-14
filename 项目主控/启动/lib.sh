#!/usr/bin/env bash
# 统一启动器公共库：端口/profile、PID 校验、端口检查、健康探针、停止逻辑。
# 只做只读检查 + 管理本启动器写入 run/*.pid 的进程；不 pkill、不自动换端口。
# shellcheck shell=bash

LAUNCHER_BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_ROOT_DIR="$(cd "$LAUNCHER_BASE_DIR/../.." && pwd)"
LAUNCHER_PROBE="$LAUNCHER_BASE_DIR/probe.py"
LAUNCHER_DSH_PY="/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3"

# 生产默认端口（与 02 架构 §8 一致），不自动改。
APP_PORT="${LAUNCH_APP_PORT:-8790}"
DB_PORT="${LAUNCH_DB_PORT:-8797}"
VIEW_PORT="${LAUNCH_VIEW_PORT:-8830}"
OFFLINE=0
LOCK_HELD=0

if [ -n "${LAUNCH_PYTHON:-}" ]; then
  LAUNCHER_PROBE_PY="$LAUNCH_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  LAUNCHER_PROBE_PY="$(command -v python3)"
elif [ -x "$LAUNCHER_DSH_PY" ]; then
  LAUNCHER_PROBE_PY="$LAUNCHER_DSH_PY"
else
  LAUNCHER_PROBE_PY="python3"
fi

launcher_usage() {
  local prog="$1"
  cat <<EOF
用法: $prog [选项]

  --app-port <port>    MoveScope 端口（默认 8790；当前 ${APP_PORT}）
  --db-port <port>     数据库 API 端口（默认 8797；当前 ${DB_PORT}）
  --view-port <port>   视图工具端口（默认 8830；当前 ${VIEW_PORT}）
  --offline            仅 start_all.sh：给 app 设置黑洞代理，验证离线缓存路径
  -h, --help           显示本帮助

环境变量（优先级低于命令行）：
  LAUNCH_APP_PORT / LAUNCH_DB_PORT / LAUNCH_VIEW_PORT
  PYTHON=<含 numpy/Pillow 的解释器>   # 仅 start_all.sh 使用
  LAUNCH_PYTHON=<启动器探针 Python>    # 默认 python3

说明：端口是显式配置，不是自动扫描；被占时启动器报错退出，不会换端口。
EOF
}

launcher_parse_args() {
  local allow_offline="$1"
  local prog="${2:-$(basename "$0")}"
  shift 2 || true
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --app-port)
        if [ "$#" -lt 2 ]; then echo "错误：--app-port 缺少端口值" >&2; exit 2; fi
        APP_PORT="$2"; shift 2 ;;
      --app-port=*) APP_PORT="${1#*=}"; shift ;;
      --db-port)
        if [ "$#" -lt 2 ]; then echo "错误：--db-port 缺少端口值" >&2; exit 2; fi
        DB_PORT="$2"; shift 2 ;;
      --db-port=*) DB_PORT="${1#*=}"; shift ;;
      --view-port)
        if [ "$#" -lt 2 ]; then echo "错误：--view-port 缺少端口值" >&2; exit 2; fi
        VIEW_PORT="$2"; shift 2 ;;
      --view-port=*) VIEW_PORT="${1#*=}"; shift ;;
      --offline)
        if [ "$allow_offline" -ne 1 ]; then
          echo "错误：--offline 只用于 start_all.sh" >&2
          exit 2
        fi
        OFFLINE=1; shift ;;
      -h|--help)
        launcher_usage "$prog"; exit 0 ;;
      *)
        echo "错误：未知参数 $1" >&2
        launcher_usage "$prog" >&2
        exit 2 ;;
    esac
  done
  launcher_validate_ports || exit 2
}

launcher_validate_ports() {
  local p
  for p in "$APP_PORT" "$DB_PORT" "$VIEW_PORT"; do
    if [[ ! "$p" =~ ^[0-9]+$ ]] || (( 10#$p < 1 || 10#$p > 65535 )); then
      echo "错误：端口必须是 1-65535 的整数，收到 '$p'" >&2
      return 1
    fi
  done
  (( APP_PORT = 10#$APP_PORT ))
  (( DB_PORT = 10#$DB_PORT ))
  (( VIEW_PORT = 10#$VIEW_PORT ))
  if [ "$APP_PORT" -eq "$DB_PORT" ] || [ "$APP_PORT" -eq "$VIEW_PORT" ] || [ "$DB_PORT" -eq "$VIEW_PORT" ]; then
    echo "错误：app/db/view 端口不能重复（$APP_PORT/$DB_PORT/${VIEW_PORT}）" >&2
    return 1
  fi
}

launcher_init_paths() {
  if [ "$APP_PORT" -eq 8790 ] && [ "$DB_PORT" -eq 8797 ] && [ "$VIEW_PORT" -eq 8830 ]; then
    PROFILE="default"
    RUN_DIR="$LAUNCHER_BASE_DIR/run"
    LOG_DIR="$LAUNCHER_BASE_DIR/logs"
  else
    PROFILE="sandbox-${APP_PORT}-${DB_PORT}-${VIEW_PORT}"
    RUN_DIR="$LAUNCHER_BASE_DIR/run/$PROFILE"
    LOG_DIR="$LAUNCHER_BASE_DIR/logs/$PROFILE"
  fi
}

profile_flags() {
  if [ "${PROFILE:-default}" = "default" ]; then
    return 0
  fi
  printf -- '--app-port %s --db-port %s --view-port %s' "$APP_PORT" "$DB_PORT" "$VIEW_PORT"
}

service_label() {
  case "$1" in
    db) echo "DB" ;;
    app) echo "app" ;;
    view) echo "view" ;;
    *) echo "$1" ;;
  esac
}

service_port() {
  case "$1" in
    db) echo "$DB_PORT" ;;
    app) echo "$APP_PORT" ;;
    view) echo "$VIEW_PORT" ;;
    *) echo "错误：未知服务 $1" >&2; return 1 ;;
  esac
}

service_url() {
  printf 'http://127.0.0.1:%s' "$(service_port "$1")"
}

service_health_url() {
  case "$1" in
    db|app) printf '%s/api/health' "$(service_url "$1")" ;;
    view) printf '%s/' "$(service_url "$1")" ;;
  esac
}

service_marker() {
  case "$1" in
    db) printf 'cli.py serve --port %s' "$DB_PORT" ;;
    app) printf '系统/app/server.py --port %s' "$APP_PORT" ;;
    view) printf 'http.server %s' "$VIEW_PORT" ;;
  esac
}

logfile() {
  printf '%s/%s.log' "$LOG_DIR" "$1"
}

iso_now() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

launcher_require_commands() {
  local cmd missing=0
  for cmd in lsof pgrep tail date awk sort tr sed; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "错误：缺少系统命令 '$cmd'，启动器无法安全检查端口/进程。" >&2
      missing=1
    fi
  done
  if [ ! -f "$LAUNCHER_PROBE" ]; then
    echo "错误：缺少探针 $LAUNCHER_PROBE" >&2
    missing=1
  fi
  if ! "$LAUNCHER_PROBE_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    echo "错误：探针需要 Python >= 3.8；当前 LAUNCH_PYTHON=$LAUNCHER_PROBE_PY 不可用。" >&2
    missing=1
  fi
  return "$missing"
}

# 只有 start 需要：视图工具的 start.sh 用 PATH 中的 python3 起静态服务。
launcher_require_python3() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "错误：视图工具 start.sh 需要 PATH 中的 python3（也可先修正 PATH）。" >&2
    return 1
  fi
}

# app 需要 numpy + Pillow。优先 PYTHON，其次工作区自带解释器，最后 PATH 的 python3。
resolve_app_python() {
  local explicit="${PYTHON:-}" cand tried=""
  if [ -n "$explicit" ]; then
    if [ ! -x "$explicit" ] || ! "$explicit" -c 'import numpy, PIL' >/dev/null 2>&1; then
      echo "错误：PYTHON=$explicit 不可执行或缺少 numpy/Pillow。" >&2
      echo "请指定工作区自带的解释器，例如：" >&2
      echo "  PYTHON=$LAUNCHER_DSH_PY ./start_all.sh" >&2
      return 1
    fi
    printf '%s\n' "$explicit"
    return 0
  fi
  for cand in "$LAUNCHER_DSH_PY" "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$cand" ] || continue
    [ -x "$cand" ] || continue
    tried="$tried $cand"
    if "$cand" -c 'import numpy, PIL' >/dev/null 2>&1; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  echo "错误：找不到含 numpy/Pillow 的 Python（已尝试:${tried}）。" >&2
  echo "请用 PYTHON=/path/to/python3 ./start_all.sh 显式指定。" >&2
  return 1
}

# ---------------------------------------------------------------- PID / 端口

pidfile() {
  printf '%s/%s.pid' "$RUN_DIR" "$1"
}

read_pid() {
  local f
  f="$(pidfile "$1")"
  [ -f "$f" ] || return 1
  local v
  v="$(tr -cd '0-9' <"$f")"
  [ -n "$v" ] || return 1
  printf '%s' "$v"
}

pid_alive() {
  kill -0 "$1" 2>/dev/null
}

# 用 pgrep -fl 取完整命令行（macOS 的 ps 在某些沙箱里不可用；pgrep 可用）。
pid_cmdline() {
  local pid="$1"
  pgrep -fl . 2>/dev/null | awk -v p="$pid" '$1 == p { sub(/^[0-9]+[[:space:]]+/, ""); print; exit }' || true
}

pid_matches_service() {
  local svc="$1" pid="$2" cmd marker
  cmd="$(pid_cmdline "$pid")"
  [ -n "$cmd" ] || return 1
  marker="$(service_marker "$svc")"
  [[ "$cmd" == *"$marker"* ]]
}

port_pids() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u | tr '\n' ' ' | sed 's/ $//' || true
}

port_has_listener() {
  [ -n "$(port_pids "$1")" ]
}

port_owner_lines() {
  local port="$1" pids pid cmd
  pids="$(port_pids "$port")"
  [ -n "$pids" ] || return 1
  for pid in $pids; do
    cmd="$(pid_cmdline "$pid")"
    printf 'PID %s  %s\n' "$pid" "${cmd:-<无法读取命令行>}"
  done
}

# ---------------------------------------------------------------- 健康探针

health_wait() {
  local svc="$1" timeout="${2:-15}" expect="" out
  local -a args=("$LAUNCHER_PROBE_PY" "$LAUNCHER_PROBE" wait
    --kind "$svc" --url "$(service_health_url "$svc")"
    --timeout "$timeout" --interval 0.2)
  case "$svc" in
    db) expect="spherex-db" ;;
    app) expect="MoveScope" ;;
  esac
  if [ -n "$expect" ]; then
    args+=(--expect-service "$expect")
  fi
  local pid
  pid="$(read_pid "$svc" 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    args+=(--pid "$pid")
  fi
  if out="$("${args[@]}" 2>&1)"; then
    printf '%s\n' "$out"
    return 0
  fi
  printf '%s\n' "$out"
  return 1
}

health_once() {
  health_wait "$1" "${2:-2}"
}

# 健康检查可能被同一端口上的“冒名”进程满足（例如临时占位 http.server 对任意
# 路径都返回 200）。启动成功后必须确认：PID 文件里的进程仍存活、命令行匹配、
# 且该端口的监听者就是它。
verify_service_owner() {
  local svc="$1" pid pids port
  pid="$(read_pid "$svc" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    echo "PID 文件缺失"
    return 1
  fi
  if ! pid_alive "$pid"; then
    echo "记录的进程 $pid 已退出"
    return 1
  fi
  if ! pid_matches_service "$svc" "$pid"; then
    echo "进程 $pid 的命令行与预期不符"
    return 1
  fi
  port="$(service_port "$svc")"
  pids="$(port_pids "$port")"
  if [[ " $pids " != *" $pid "* ]]; then
    echo "端口 $port 的监听者不是记录的进程 ${pid}（当前监听者: ${pids:-无}）"
    return 1
  fi
  return 0
}

log_event() {
  local svc="$1"
  shift
  [ -d "$LOG_DIR" ] || return 0
  printf '[%s] === %s ===\n' "$(iso_now)" "$*" >>"$(logfile "$svc")" 2>/dev/null || true
}

print_log_tail() {
  local svc="$1" log
  log="$(logfile "$svc")"
  echo "---- $svc 最后 50 行日志: ${log#"$LAUNCHER_ROOT_DIR"/} ----" >&2
  if [ -f "$log" ]; then
    tail -n 50 "$log" >&2 || true
  else
    echo "(日志文件不存在)" >&2
  fi
  echo "---- 日志结束 ----" >&2
}

# ---------------------------------------------------------------- 停止

# 只停 PID 文件中的进程，并校验命令行包含预期脚本；失败时绝不误杀。
stop_one() {
  local svc="$1" pf pid cmd log
  pf="$(pidfile "$svc")"
  if [ ! -f "$pf" ]; then
    echo "  $svc: 未由启动器启动（无 PID 文件）"
    return 0
  fi
  if ! pid="$(read_pid "$svc")"; then
    echo "  $svc: PID 文件内容无效，已清理 ${pf#"$LAUNCHER_ROOT_DIR"/}"
    rm -f "$pf"
    return 0
  fi
  if ! pid_alive "$pid"; then
    echo "  $svc: 进程 $pid 已退出，清理陈旧 PID 文件"
    rm -f "$pf"
    return 0
  fi
  cmd="$(pid_cmdline "$pid")"
  if [ -z "$cmd" ] || [[ "$cmd" != *"$(service_marker "$svc")"* ]]; then
    echo "  $svc: PID $pid 仍存活，但命令行不匹配预期；拒绝停止（保留 PID 文件）" >&2
    echo "        实际命令行: ${cmd:-<无法读取>}" >&2
    echo "        预期包含: $(service_marker "$svc")" >&2
    return 1
  fi
  log="$(logfile "$svc")"
  if [ -d "$LOG_DIR" ]; then
    printf '[%s] === stop %s pid=%s ===\n' "$(iso_now)" "$svc" "$pid" >>"$log" 2>/dev/null || true
  fi
  echo "  $svc: TERM pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for ((i = 0; i < 50; i++)); do
    pid_alive "$pid" || break
    sleep 0.1
  done
  if pid_alive "$pid"; then
    echo "  $svc: 5s 未退出，KILL pid=$pid"
    kill -KILL "$pid" 2>/dev/null || true
    for ((i = 0; i < 20; i++)); do
      pid_alive "$pid" || break
      sleep 0.1
    done
  fi
  if pid_alive "$pid"; then
    echo "  $svc: 停止失败，pid=$pid 仍存活" >&2
    return 1
  fi
  rm -f "$pf"
  echo "  $svc: 已停止，端口 $(service_port "$svc") 等待释放"
  return 0
}

launcher_git_rev() {
  local out
  if command -v git >/dev/null 2>&1 && git -C "$LAUNCHER_ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if out="$(git -C "$LAUNCHER_ROOT_DIR" describe --tags --always --dirty 2>/dev/null)"; then
      printf '%s\n' "$out"
      return 0
    fi
    if out="$(git -C "$LAUNCHER_ROOT_DIR" rev-parse --short HEAD 2>/dev/null)"; then
      printf 'commit %s\n' "$out"
      return 0
    fi
  fi
  printf 'no-git\n'
}

# ---------------------------------------------------------------- 启动锁

launcher_acquire_lock() {
  local lock="$RUN_DIR/.lock" pid=""
  if mkdir "$lock" 2>/dev/null; then
    printf '%s\n' "$$" >"$lock/pid"
    LOCK_HELD=1
    return 0
  fi
  if [ -f "$lock/pid" ]; then
    pid="$(tr -cd '0-9' <"$lock/pid")"
  fi
  if [ -n "$pid" ] && pid_alive "$pid"; then
    echo "错误：另一个 start_all.sh 正在运行（pid=${pid}，锁=${lock}）。" >&2
    return 1
  fi
  echo "清理陈旧启动锁：${lock#"$LAUNCHER_ROOT_DIR"/}" >&2
  rm -f "$lock/pid" 2>/dev/null || true
  rmdir "$lock" 2>/dev/null || true
  if mkdir "$lock" 2>/dev/null; then
    printf '%s\n' "$$" >"$lock/pid"
    LOCK_HELD=1
    return 0
  fi
  echo "错误：无法获取启动锁 ${lock}（请检查目录权限）" >&2
  return 1
}

launcher_release_lock() {
  if [ "${LOCK_HELD:-0}" -eq 1 ]; then
    rm -f "$RUN_DIR/.lock/pid" 2>/dev/null || true
    rmdir "$RUN_DIR/.lock" 2>/dev/null || true
    LOCK_HELD=0
  fi
}
