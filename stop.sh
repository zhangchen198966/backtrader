#!/usr/bin/env bash
# bt-lab 停止脚本
# 用法: ./stop.sh [端口]     默认 8600（与 start.sh 一致）
set -uo pipefail
cd "$(dirname "$0")"

PORT="${1:-8600}"
PID_FILE=".btlab.pid"
stopped=0

# 按 PID 文件优雅停止
if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 20); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$PID" 2>/dev/null || true
    echo "已停止 (PID $PID)"
    stopped=1
  fi
  rm -f "$PID_FILE"
fi

# 兜底：按端口清理残留进程
if lsof -ti :"$PORT" >/dev/null 2>&1; then
  lsof -ti :"$PORT" | xargs kill -9 2>/dev/null || true
  echo "已按端口 $PORT 清理残留进程"
  stopped=1
fi

[ "$stopped" -eq 1 ] || echo "bt-lab 未在运行"
exit 0
