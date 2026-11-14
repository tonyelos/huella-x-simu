#!/usr/bin/env bash
# 只读状态：三服务 health、端口、PID、app/data 最新时间、DB schema_version、git。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

launcher_parse_args 0 "$(basename "$0")" "$@"
launcher_init_paths
launcher_require_commands || exit 1

echo "统一启动器 status（profile=${PROFILE}）"
echo "  时间: $(iso_now)"
echo "  工作区: $LAUNCHER_ROOT_DIR"
echo "  git: $(launcher_git_rev)"
echo "  PID 目录: ${RUN_DIR#"$LAUNCHER_ROOT_DIR"/}   日志目录: ${LOG_DIR#"$LAUNCHER_ROOT_DIR"/}"
echo

ok_count=0
declare -a health_text=()

for svc in db app view; do
  label="$(service_label "$svc")"
  case "$svc" in
    db) name="数据库 API（spherex-db）" ;;
    app) name="MoveScope" ;;
    view) name="视图工具（静态页）" ;;
  esac
  url="$(service_url "$svc")"
  echo "[$label] $name"
  echo "  url: $url"

  pid="$(read_pid "$svc" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    echo "  pid: -（无 PID 文件；不是本启动器启动的）"
  elif ! pid_alive "$pid"; then
    echo "  pid: ${pid}（PID 文件存在，但进程已退出 → 陈旧）"
  elif pid_matches_service "$svc" "$pid"; then
    echo "  pid: ${pid}（启动器管理，命令行校验通过）"
  else
    echo "  pid: ${pid}（PID 文件存在，但命令行与预期不符 → 可能 PID 复用）"
    echo "       actual: $(pid_cmdline "$pid")"
    echo "       expect: $(service_marker "$svc")"
  fi

  pids="$(port_pids "$(service_port "$svc")")"
  if [ -n "$pids" ]; then
    echo "  port: 127.0.0.1:$(service_port "$svc") LISTEN（pid=${pids}）"
    while IFS= read -r line; do
      if [ -n "$line" ]; then echo "        $line"; fi
    done < <(port_owner_lines "$(service_port "$svc")")
  else
    echo "  port: 127.0.0.1:$(service_port "$svc") 未监听"
  fi

  health="$(health_once "$svc" 2 || true)"
  if [[ "$health" == ok* ]]; then
    ok_count=$((ok_count + 1))
    echo "  health: $health"
    health_text+=("$label=ok")
  else
    echo "  health: FAIL（${health}）"
    health_text+=("$label=FAIL")
  fi
  echo
done

schema="$("$LAUNCHER_PROBE_PY" "$LAUNCHER_PROBE" schema \
  --db "$LAUNCHER_ROOT_DIR/系统/数据库工具/var/catalog.sqlite" 2>&1 || true)"
latest="$("$LAUNCHER_PROBE_PY" "$LAUNCHER_PROBE" latest \
  --path "$LAUNCHER_ROOT_DIR/系统/app/data" 2>&1 || true)"

echo "[数据快照]"
echo "  DB schema_version: $schema"
echo "  系统/app/data 最新文件: $latest"
echo

echo "[结论] ${health_text[*]} → $ok_count/3 健康"
if [ "$ok_count" -eq 3 ]; then
  exit 0
fi
exit 1
