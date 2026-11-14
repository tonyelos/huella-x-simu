#!/usr/bin/env bash
# 一键把工作区里 MoveScope 与 视图工具 的数据导入数据库。
# 头文件缓存不存在时会自动跳过（仍然导入 week_index / targets / epochs / cutouts / fields）。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

"$PY" cli.py init
"$PY" cli.py ingest all --root .. "$@"
"$PY" cli.py stats
