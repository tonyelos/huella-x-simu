#!/usr/bin/env bash
# 停止统一启动器启动的三个服务：view → app → DB。
# 只停 run/*.pid 指向且命令行校验通过的进程；先 TERM，5s 不退再 KILL。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

launcher_parse_args 0 "$(basename "$0")" "$@"
launcher_init_paths
launcher_require_commands || exit 1

echo "统一启动器 stop（profile=${PROFILE}）"
echo "  端口：app=$APP_PORT  db=$DB_PORT  view=$VIEW_PORT"

if [ -f "$RUN_DIR/.lock/pid" ]; then
  lock_pid="$(tr -cd '0-9' <"$RUN_DIR/.lock/pid")"
  if [ -n "$lock_pid" ] && pid_alive "$lock_pid"; then
    echo "错误：start_all.sh 正在运行（pid=${lock_pid}），请等它结束后再 stop。" >&2
    exit 1
  fi
fi

rc=0
for svc in view app db; do
  stop_one "$svc" || rc=1
done

# 进程退出后端口通常立即释放；最多等 3 s，避免把瞬间的 LISTEN 误报成残留。
for ((i = 0; i < 30; i++)); do
  any_listen=0
  for svc in db app view; do
    if port_has_listener "$(service_port "$svc")"; then any_listen=1; fi
  done
  if [ "$any_listen" -eq 0 ]; then break; fi
  sleep 0.1
done

echo
echo "端口检查："
for svc in db app view; do
  port="$(service_port "$svc")"
  if port_has_listener "$port"; then
    echo "  ! $(service_label "$svc") 端口 $port 仍被占用（不是本次停止的进程，未处理）："
    while IFS= read -r line; do
      if [ -n "$line" ]; then echo "      $line"; fi
    done < <(port_owner_lines "$port")
  else
    echo "  ok $(service_label "$svc") 端口 $port 已释放"
  fi
done

echo
echo "进程检查（只报告，不杀非本启动器管理的进程）："
leftover_any=0
for svc in db app view; do
  marker="$(service_marker "$svc")"
  leftover="$(pgrep -fl "$marker" 2>/dev/null || true)"
  if [ -n "$leftover" ]; then
    leftover_any=1
    echo "  ! $svc 仍匹配到进程："
    printf '%s\n' "$leftover" | sed 's/^/      /'
  else
    echo "  ok $svc 无残留进程"
  fi
done
if [ "$leftover_any" -eq 1 ]; then
  echo "  说明：上面若有进程，属于非本启动器（或先前人工启动）的实例，stop_all 不会杀它。"
fi

if [ "$rc" -eq 0 ]; then
  echo
  echo "停止完成。"
else
  echo
  echo "停止未完全成功，请根据上面的 PID/命令行人工处理。" >&2
fi
exit "$rc"
