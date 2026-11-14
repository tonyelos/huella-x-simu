#!/usr/bin/env bash
# 视图工具静态服务。默认 8830（MoveScope 用 8790，数据库用 8797）。
# 用法: ./start.sh [端口]
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8830}"

if ! python3 -c '
import socket, sys
p = int(sys.argv[1])
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", p))
except OSError:
    sys.exit(1)
finally:
    s.close()
' "$PORT" 2>/dev/null; then
  echo "端口 ${PORT} 已被占用。" >&2
  echo "MoveScope 默认 8790、数据库默认 8797，本工具默认 8830。" >&2
  echo "换一个端口：./start.sh 8831" >&2
  exit 1
fi

echo "视图工具 → http://127.0.0.1:${PORT}/"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
