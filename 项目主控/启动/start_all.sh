#!/usr/bin/env bash
# 一条命令启动 DB → app → view，健康检查通过后打印三个 URL。
# 只停/管理本启动器写入 run/*.pid 的进程；不自动换端口、不 pkill。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

launcher_parse_args 1 "$(basename "$0")" "$@"
launcher_init_paths
launcher_require_commands || exit 1
launcher_require_python3 || exit 1

SECONDS=0
APPPY="$(resolve_app_python)" || exit 1

mkdir -p "$RUN_DIR" "$LOG_DIR"

echo "统一启动器 start（profile=${PROFILE}）"
echo "  app=$APP_PORT  db=$DB_PORT  view=$VIEW_PORT"
echo "  PYTHON=$APPPY"
echo "  PID=$RUN_DIR"
echo "  日志=$LOG_DIR"
if [ "$OFFLINE" -eq 1 ]; then
  echo "离线模式：IRSA 不可达，使用本地缓存（app 的 HTTP(S)_PROXY 指向 127.0.0.1:9）"
fi

launcher_acquire_lock || exit 1
trap 'launcher_release_lock' EXIT

ROLLBACK_DONE=0

rollback() {
  [ "$ROLLBACK_DONE" -eq 0 ] || return 0
  ROLLBACK_DONE=1
  echo >&2
  echo "回滚：停止本次启动的服务（view → app → DB）" >&2
  local rc=0
  stop_one view || rc=1
  stop_one app || rc=1
  stop_one db || rc=1
  if [ "$rc" -eq 0 ]; then
    echo "回滚完成：本次启动的服务已停止，PID 文件已清理。" >&2
  else
    echo "警告：回滚未完全成功，请按上面的 PID/命令行人工核对。" >&2
  fi
  return "$rc"
}

on_signal() {
  echo >&2
  echo "收到中断信号，回滚本次启动..." >&2
  rollback || true
  launcher_release_lock
  exit 130
}

# 返回 0=可以启动；3=三个服务已在运行；1/2=拒绝启动。
check_existing_state() {
  local -a names=() pids=() healths=()
  local svc pid i all
  for svc in db app view; do
    pid="$(read_pid "$svc" 2>/dev/null || true)"
    [ -n "$pid" ] || continue
    if ! pid_alive "$pid"; then
      echo "  清理陈旧 PID 文件：${svc}.pid（pid=$pid 已退出）"
      rm -f "$(pidfile "$svc")"
      continue
    fi
    if ! pid_matches_service "$svc" "$pid"; then
      echo "错误：$svc 的 PID 文件指向 ${pid}，但命令行与预期不符（可能 PID 复用）。" >&2
      echo "      实际命令行: $(pid_cmdline "$pid")" >&2
      echo "      预期包含:   $(service_marker "$svc")" >&2
      echo "      启动器不会杀它；请人工确认后删除 $(pidfile "$svc") 再重试。" >&2
      return 2
    fi
    names+=("$svc")
    pids+=("$pid")
    if health_once "$svc" 2 >/dev/null 2>&1; then
      healths+=(1)
    else
      healths+=(0)
    fi
  done

  if [ "${#pids[@]}" -eq 0 ]; then
    return 0
  fi

  if [ "${#pids[@]}" -eq 3 ]; then
    all=1
    for i in "${!healths[@]}"; do
      [ "${healths[$i]}" -eq 1 ] || all=0
    done
    if [ "$all" -eq 1 ]; then
      echo "三个服务已在运行（未重复启动）："
      for i in "${!names[@]}"; do
        printf '  %-4s %-34s ok（pid=%s）\n' \
          "$(service_label "${names[$i]}")" "$(service_url "${names[$i]}")" "${pids[$i]}"
      done
      echo "  如需重启：./stop_all.sh $(profile_flags) && ./start_all.sh $(profile_flags)"
      return 3
    fi
  fi

  echo "错误：检测到启动器已有部分/不健康服务在运行，拒绝重复启动。" >&2
  for i in "${!names[@]}"; do
    printf '  %-4s pid=%-7s health=%s\n' \
      "$(service_label "${names[$i]}")" "${pids[$i]}" \
      "$([ "${healths[$i]}" -eq 1 ] && echo ok || echo FAIL)" >&2
  done
  echo "  请先执行：./stop_all.sh $(profile_flags)  再重新 ./start_all.sh" >&2
  return 1
}

check_db_initialized() {
  local db="$LAUNCHER_ROOT_DIR/系统/数据库工具/var/catalog.sqlite"
  if [ -f "$db" ]; then
    return 0
  fi
  cat >&2 <<EOF
错误：数据库未初始化（缺少 系统/数据库工具/var/catalog.sqlite）。
先运行以下之一（一次性）：
  cd "$LAUNCHER_ROOT_DIR/系统/数据库工具"
  PYTHON="$APPPY" ./tools/import_all.sh
或最小初始化：
  PYTHON="$APPPY" ./cli.py init
启动器不会在缺库时静默启动 DB。
EOF
  return 1
}

preflight_ports() {
  local svc port pids conflicts=0 line
  echo "端口预检（app $APP_PORT / db $DB_PORT / view ${VIEW_PORT}）..."
  for svc in db app view; do
    port="$(service_port "$svc")"
    pids="$(port_pids "$port")"
    if [ -n "$pids" ]; then
      conflicts=1
      echo "  x $(service_label "$svc") 端口 $port 已被占用：" >&2
      while IFS= read -r line; do
        if [ -n "$line" ]; then echo "      $line" >&2; fi
      done < <(port_owner_lines "$port")
    fi
  done
  if [ "$conflicts" -eq 0 ]; then
    echo "  端口空闲"
    return 0
  fi
  cat >&2 <<EOF
错误：目标端口被占用，拒绝启动（不自动换端口，也不会杀掉占用进程）。
处理：
  1) 确认并停止占用进程：kill <上面的 PID>（先确认它是旧服务）
  2) 或显式换一组空闲 port 做 sandbox 测试（示例，先确认空闲）：
     LAUNCH_APP_PORT=8891 LAUNCH_DB_PORT=8898 LAUNCH_VIEW_PORT=8931 ./$(basename "$0")
  说明：生产/演示默认端口仍是 8790/8797/8830；sandbox 端口只用于验证启动链路。
EOF
  return 1
}

start_service() {
  local svc="$1" port log pid
  port="$(service_port "$svc")"
  log="$(logfile "$svc")"
  printf '[%s] === start %s port=%s profile=%s ===\n' \
    "$(iso_now)" "$svc" "$port" "$PROFILE" >>"$log"
  case "$svc" in
    db)
      (
        cd "$LAUNCHER_ROOT_DIR/系统/数据库工具" &&
          exec nohup env PYTHON="$APPPY" PYTHONUNBUFFERED=1 ./start.sh "$port"
      ) >>"$log" 2>&1 &
      ;;
    app)
      local -a envs=(env PYTHONUNBUFFERED=1)
      if [ "$OFFLINE" -eq 1 ]; then
        envs+=(HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9
          NO_PROXY=127.0.0.1,localhost)
      fi
      (
        cd "$LAUNCHER_ROOT_DIR" &&
          exec nohup "${envs[@]}" "$APPPY" 系统/app/server.py --port "$port" --host 127.0.0.1
      ) >>"$log" 2>&1 &
      ;;
    view)
      (
        cd "$LAUNCHER_ROOT_DIR/系统/视图工具" &&
          exec nohup env PYTHONUNBUFFERED=1 ./start.sh "$port"
      ) >>"$log" 2>&1 &
      ;;
  esac
  pid=$!
  printf '%s\n' "$pid" >"$(pidfile "$svc")"
  echo "  $svc: 启动 pid=${pid}（日志 ${log#"$LAUNCHER_ROOT_DIR"/}）"
}

wait_and_report() {
  local svc="$1" out url label
  label="$(service_label "$svc")"
  url="$(service_url "$svc")"
  if [ "$svc" = view ]; then url="$url/"; fi
  printf '  等待 %-4s 健康检查（最多 15s）... ' "$svc"
  if ! out="$(health_wait "$svc" 15)"; then
    echo "失败"
    log_event "$svc" "health FAILED $svc: $out"
    echo >&2
    echo "错误：$svc 健康检查失败：$out" >&2
    print_log_tail "$svc"
    return 1
  fi
  if ! owner_err="$(verify_service_owner "$svc")"; then
    echo "失败"
    log_event "$svc" "health owner-check FAILED $svc: $owner_err"
    echo >&2
    echo "错误：$svc 健康检查可达（${out}），但进程归属校验失败：$owner_err" >&2
    print_log_tail "$svc"
    return 1
  fi
  echo "ok"
  log_event "$svc" "healthy $svc port=$(service_port "$svc")"
  printf '%-4s %-34s %s\n' "$label" "$url" "ok"
}

state=0
check_existing_state || state=$?
case "$state" in
  0) ;;
  3) echo "启动器未做任何改动。"; exit 0 ;;
  *) exit 1 ;;
esac

check_db_initialized || exit 1
preflight_ports || exit 1

# 只在真正开始启动后才接管 Ctrl-C：此前的中断不应影响已在运行的服务。
trap 'on_signal' INT TERM

echo
echo "启动顺序：DB → app → view"
if ! start_service db; then rollback; exit 1; fi
if ! wait_and_report db; then rollback; exit 1; fi
if ! start_service app; then rollback; exit 1; fi
if ! wait_and_report app; then rollback; exit 1; fi
if ! start_service view; then rollback; exit 1; fi
if ! wait_and_report view; then rollback; exit 1; fi

echo
echo "三服务已就绪（用时 ${SECONDS}s）："
for svc in db app view; do
  url="$(service_url "$svc")"
  if [ "$svc" = view ]; then url="$url/"; fi
  printf '  %-4s %-34s ok（pid=%s）\n' \
    "$(service_label "$svc")" "$url" "$(read_pid "$svc")"
done
echo "  profile: $PROFILE"
echo "  git: $(launcher_git_rev)"
echo "  浏览器：http://127.0.0.1:$APP_PORT/"
echo "  状态：./status.sh $(profile_flags)"
echo "  停止：./stop_all.sh $(profile_flags)"
