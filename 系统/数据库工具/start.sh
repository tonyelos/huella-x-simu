#!/usr/bin/env bash
# 启动只读 HTTP API。默认端口 8797，可用 ./start.sh 9000 覆盖。
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

PORT="${1:-8797}"
shift || true

if [ ! -f "var/catalog.sqlite" ]; then
  echo "数据库还不存在。先运行："
  echo "  PYTHON=$PY ./tools/import_all.sh"
  exit 1
fi

exec "$PY" cli.py serve --port "$PORT" "$@"
