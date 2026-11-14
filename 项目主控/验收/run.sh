#!/usr/bin/env bash
# T1 验收与契约工具 · 一条命令入口
#
#   ./run.sh --base-url http://127.0.0.1:8790 --db-url http://127.0.0.1:8797 \
#            --report-dir report/2026-11-14 --offline
#
# 说明：
# - 默认不启动任何服务；app/DB 健康检查失败退出 2。
# - --offline 时才会在 8810 启动一个只属于验收的 app 子进程（黑洞代理）。
# - 所有相对 --report-dir 以本目录为基准，报告只写在本目录下。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

DSH_PY="/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3"
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x "$DSH_PY" ]; then
  PY="$DSH_PY"
else
  PY="$(command -v python3 || true)"
fi
if [ -z "${PY:-}" ] || [ ! -x "$PY" ]; then
  echo "找不到可用的 Python 3；请设置 PYTHON=/path/to/python3" >&2
  exit 2
fi
if [ ! -f "$DIR/accept.py" ]; then
  echo "缺少 $DIR/accept.py" >&2
  exit 2
fi

exec "$PY" "$DIR/accept.py" "$@"
