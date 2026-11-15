#!/usr/bin/env bash
# AnyField P0 local footprint service. Default port 8798.
# Usage: ./start.sh [port]
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

PORT="${1:-8798}"
DB="${ANYFIELD_DB:-../数据库工具/var/catalog.sqlite}"
SIDECAR="${ANYFIELD_SIDECAR:-var/anyfield.sqlite}"
BLOB_ROOT="${ANYFIELD_BLOB_ROOT:-../数据库工具/var/blobs}"

if [ ! -f "$DB" ]; then
  echo "warning: database not found: $DB" >&2
  echo "         service will start and /api/v1/health will return 503 DB_UNAVAILABLE" >&2
fi

exec "$PY" server.py --host 127.0.0.1 --port "$PORT" --db "$DB" \
  --sidecar "$SIDECAR" --blob-root "$BLOB_ROOT"
