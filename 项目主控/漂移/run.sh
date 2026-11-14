#!/usr/bin/env bash
# T6 漂移检查器入口。根目录由 drift.py 依据自身位置推断，不要求从本目录启动。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
exec "$PY" "$SCRIPT_DIR/drift.py" "$@"
