#!/usr/bin/env bash
# 跑全部单元/接口测试（约 1 秒）。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

exec "$PY" -m unittest discover -s tests -t . -v
