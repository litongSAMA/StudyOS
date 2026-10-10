#!/usr/bin/env bash
# StudyOS 停止脚本：停掉 ./scripts/start.sh -d 启动的后台常驻后端（也兼容前台启动的实例）
# 用法：./scripts/stop.sh
#
# 安全设计：先校验 PID 确实属于本项目的 uvicorn 再动手，避免误杀同名无关进程；
# 先 SIGTERM 优雅退出（给 uvicorn 关连接、跑 shutdown 钩子的时间），超时才 SIGKILL。
set -e

# 脚本位于 <项目根>/scripts/，项目根是它的上一级；日志与 PID 都落在项目根下。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8800
PID_FILE="$ROOT/logs/studyos.pid"
EXPECT="uvicorn app.api.main:app"

_backend_started() { curl -sf --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; }

# 判断 PID 是否为本项目的 uvicorn，两道校验：命令行特征 + 本机确实存在该虚拟环境。
# 注意：前台启动是绝对路径（.../backend/.venv/bin/uvicorn），后台启动是相对路径
# （./.venv/bin/uvicorn），所以不能拿绝对路径去匹配命令行，否则会认不出自己启动的实例。
_pid_is_ours() {
  local pid="$1" cmd
  # 只认「$1 是 PID 列、其余为命令行」的行，避免误匹配命令行里含该数字的其它进程
  cmd="$(pgrep -fl "$EXPECT" 2>/dev/null | awk -v p="$pid" '$1 == p { $1=""; print }')" || true
  [ -n "$cmd" ] || return 1
  [ -f "$ROOT/backend/.venv/bin/uvicorn" ] || return 1  return 0
}

# 按监听端口找占用者（PID 文件丢失时的兜底）
_port_pid() { lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1; }

# 等待端口释放，最多 $1 秒；返回 0 = 已释放
_wait_gone() {
  local i
  for i in $(seq 1 "$1"); do
    _backend_started || return 0
    sleep 1
  done
  return 1
}

pid=""
if [ -f "$PID_FILE" ]; then
  # 只取数字；万一 tr 缺失等极端情况也不中断，退化为「按端口找」
  pid="$(tr -dc '0-9' <"$PID_FILE" 2>/dev/null || true)"
fi
if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
  pid="$(_port_pid || true)"
  if [ -n "$pid" ]; then
    echo "[*] PID 文件缺失或已失效，改用端口 $PORT 的监听进程（PID $pid ）"
  fi
fi

if [ -z "$pid" ]; then
  echo "StudyOS 后端未在运行（端口 $PORT 无监听）"
  rm -f "$PID_FILE"
  exit 0
fi

if ! _pid_is_ours "$pid"; then
  echo "[错误] PID $pid 不是本项目的 uvicorn（可能已被别的程序占用），为安全起见不结束它。" >&2
  echo "        请自行确认后处理：kill $pid" >&2
  echo "        （提示：本脚本位于 <项目根>/scripts/stop.sh）" >&2
  exit 1
fi

# 二道校验：该 PID 确实在监听 8800。PID 文件陈旧时（数字被系统复用给别的进程），
# 这条能拦住误杀；若端口已被别的实例占着，也提示得更准确。
listener="$(_port_pid || true)"
if [ -n "$listener" ] && [ "$listener" != "$pid" ]; then
  echo "[错误] PID $pid 与监听端口 $PORT 的进程（PID $listener ）不一致，为安全起见不做处理。" >&2
  echo "        请检查：lsof -nP -iTCP:$PORT -sTCP:LISTEN" >&2
  exit 1
fi

echo "正在停止 StudyOS 后端（PID $pid ）..."
kill "$pid" 2>/dev/null || true
if ! _wait_gone 10; then
  echo "[*] 10 秒内未退出，强制结束..."
  kill -9 "$pid" 2>/dev/null || true
  _wait_gone 5 || true
fi

rm -f "$PID_FILE"
if _backend_started; then
  echo "[警告] 端口 $PORT 上仍有服务响应，可能还有另一个实例在运行，请检查：lsof -nP -iTCP:$PORT -sTCP:LISTEN" >&2
  exit 1
fi
echo "StudyOS 后端已停止（端口 $PORT 已释放）"
