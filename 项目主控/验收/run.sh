#!/usr/bin/env bash
# T1 验收与契约工具 · 一条命令入口
#
#   # 主验收：app 由 --start 启动，DB 8797 保持停止，离线子进程用 8813
#   ./run.sh --start --base-url http://127.0.0.1:8810 --db-url http://127.0.0.1:8797 \
#            --view-url http://127.0.0.1:8812 --offline --offline-port 8813 \
#            --report-dir report/full-db-down
#
# 说明：
# - 默认不启动任何服务；app 健康检查失败退出 2。DB 不参与环境错误判定：
#   DB 停止只让 DB 相关用例 BLOCKED，app-only 用例继续跑。
# - --start 才会在 base-url 端口启动验收专用 app 子进程（已占用则复用）。
# - --offline 时在 --offline-port（默认 8810，建议 8813）启动黑洞代理子进程。
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
