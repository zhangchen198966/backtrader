#!/usr/bin/env bash
# bt-lab 启动脚本
# 用法: ./start.sh [端口]     默认 8600
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8600}"
PID_FILE=".btlab.pid"
LOG_FILE="/tmp/btlab-server.log"

# 已在运行则直接提示
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "bt-lab 已在运行 (PID $(cat "$PID_FILE"))，访问 http://localhost:$PORT"
  exit 0
fi

# 端口被其他进程占用
if lsof -ti :"$PORT" >/dev/null 2>&1; then
  echo "端口 $PORT 已被占用：先执行 ./stop.sh，或换个端口启动 ./start.sh 8601"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "未找到 .venv/bin/python，请先创建虚拟环境并安装依赖（见 BT-LAB-GUIDE.md §2）"
  exit 1
fi

nohup .venv/bin/python -m uvicorn webapp.server:app \
  --host 127.0.0.1 --port "$PORT" --log-level warning \
  > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

# 就绪探活（最多 15 秒）
for _ in $(seq 1 30); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/tasks"; then
    echo "✅ bt-lab 已启动: http://localhost:$PORT"
    echo "   PID $(cat "$PID_FILE") · 日志 $LOG_FILE · 停止: ./stop.sh"
    exit 0
  fi
  sleep 0.5
done

echo "❌ 启动超时，查看日志: $LOG_FILE"
exit 1
