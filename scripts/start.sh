#!/usr/bin/env bash
# StudyOS 一键启动：后端(FastAPI :8800，托管前端 dist)
# 监听 0.0.0.0：局域网内其它设备（如 Windows）可用 http://<本机IP>:8800 访问
#
# 用法：
#   ./scripts/start.sh         前台运行，Ctrl+C 停止（关掉终端窗口后端也会停）
#   ./scripts/start.sh -d      后台常驻，脱离终端；用 ./scripts/stop.sh 停止（推荐日常使用）
set -e

DAEMON=0
case "${1:-}" in
  -d|--daemon) DAEMON=1 ;;
  -h|--help)
    cat <<'USAGE'
StudyOS 启动脚本

  ./scripts/start.sh            前台运行，Ctrl+C 停止（关掉终端窗口后端也会停）
  ./scripts/start.sh -d         后台常驻，脱离终端；终端关掉也不影响，用 ./scripts/stop.sh 停止

后端监听 0.0.0.0:8800，局域网内其它设备（如 Windows）可用 http://<本机IP>:8800 访问。
日志：logs/backend.out.log、logs/backend.err.log；PID：logs/studyos.pid
USAGE
    exit 0 ;;
  "") ;;
  *) echo "未知参数：$1（可用：./scripts/start.sh [-d|--help]）" >&2; exit 2 ;;
esac

# 脚本位于 <项目根>/scripts/，项目根是它的上一级；这样脚本可以从任意目录调用。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
mkdir -p "$ROOT/logs"
PID_FILE="$ROOT/logs/studyos.pid"

# 后端是否已就绪（进程活着 + 能响应 /api/health）
_backend_started() { curl -sf --max-time 2 http://127.0.0.1:8800/api/health >/dev/null 2>&1; }

# 局域网 IP（供其它设备访问提示；拿不到则静默跳过）
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi

# venv 自愈：venv 内部保存的是「创建时」的绝对路径，整个项目文件夹被移动/改名后，
# .venv/bin/* 的 shebang 仍指向旧目录，直接调用 uvicorn 会报 "bad interpreter"，
# 而按旧路径启动的旧进程会一直连旧数据库 → 每个页面 500。这里就地修好路径。
VENV_DIR="$(pwd)/.venv"
if [ -f "$VENV_DIR/pyvenv.cfg" ] && [ -f "$VENV_DIR/bin/uvicorn" ]; then
  _stale_venv="$(head -1 "$VENV_DIR/bin/uvicorn" | sed -n 's/^#!\(.*\)\/bin\/python[0-9.]*$/\1/p')"
  if [ -n "$_stale_venv" ] && [ "$_stale_venv" != "$VENV_DIR" ]; then
    echo "[*] 检测到虚拟环境路径失效（项目文件夹被移动过）：$_stale_venv → $VENV_DIR"
    "$VENV_DIR/bin/python" - "$_stale_venv" "$VENV_DIR" <<'PY' || echo "[警告] venv 路径自愈失败，必要时删除 backend/.venv 后重新运行本脚本"
import pathlib, sys
old, new = sys.argv[1], sys.argv[2]
bin_dir = pathlib.Path(new, "bin")
fixed = 0
for path in bin_dir.iterdir():
    if path.is_symlink() or not path.is_file():
        continue
    data = path.read_bytes()
    if old.encode() in data:
        path.write_bytes(data.replace(old.encode(), new.encode()))
        fixed += 1
print(f"[*] 已修正 {fixed} 个入口脚本 / 激活脚本")
PY
  fi
fi

# OCR 组件缺失补装（venv 已存在但之前 OCR 装失败/被跳过时自动补上；失败不阻断启动）
if ! ./.venv/bin/python -c "import rapidocr_onnxruntime" >/dev/null 2>&1; then
  echo "[*] OCR 组件缺失，尝试补装 rapidocr-onnxruntime（识图提炼/课表拍照识别，可选）..."
  ./.venv/bin/pip install rapidocr-onnxruntime || \
    echo "[警告] OCR 组件补装失败：识图提炼/课表拍照识别不可用，其余功能正常。"
fi

# Excel 课表解析组件缺失补装（openpyxl + xlrd，纯 Python 包；失败不阻断启动）
if ! ./.venv/bin/python -c "import openpyxl" >/dev/null 2>&1; then
  echo "[*] Excel 解析组件缺失，尝试补装 openpyxl（课表 Excel 导入用）..."
  ./.venv/bin/pip install openpyxl || \
    echo "[警告] openpyxl 补装失败：课表 Excel(.xlsx) 导入不可用，.ics/图片/文本通道正常。"
fi
if ! ./.venv/bin/python -c "import xlrd" >/dev/null 2>&1; then
  echo "[*] 老式 .xls 解析组件缺失，尝试补装 xlrd（教务系统导出的 .xls 课表用）..."
  ./.venv/bin/pip install "xlrd==2.0.1" || \
    echo "[警告] xlrd 补装失败：课表 .xls 导入不可用；可另存为 .xlsx/CSV，或走 .ics/图片/文本通道。"
fi

# 已在运行：直接复用（幂等），避免端口冲突的报错让人误以为启动失败
if _backend_started; then
  # 健康 ≠ 可用：旧进程（按旧路径启动、数据库句柄已失效）仍会返回 200，但业务接口全 500。
  # 顺手探一个业务接口，能读到数据库才算真的可用。
  if curl -sf --max-time 5 http://127.0.0.1:8800/api/auth/status >/dev/null 2>&1; then
    echo "StudyOS 后端已在运行：本机 http://127.0.0.1:8800"
    [ -n "$LAN_IP" ] && echo "局域网访问（Windows 等其它设备）：http://$LAN_IP:8800"
    exit 0
  fi
  echo "[*] 8800 端口上的旧服务读不到数据库（页面会报 Internal Server Error），自动重启为当前目录的版本..."
  # 优先交给 stop.sh：先 SIGTERM 优雅退出，必要时才 SIGKILL，避免残留下半死不活的进程
  if [ -x "$ROOT/scripts/stop.sh" ]; then
    "$ROOT/scripts/stop.sh" || true
  else
    pkill -f "uvicorn app.api.main:app.*8800" 2>/dev/null || true
  fi
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    _backend_started || break
    sleep 1
  done
fi

echo "启动 StudyOS 后端 → http://127.0.0.1:8800"
[ -n "$LAN_IP" ] && echo "局域网访问（Windows 等其它设备）：http://$LAN_IP:8800"

if [ "$DAEMON" = "1" ]; then
  # 后台常驻：nohup 脱离终端（stdout/stderr 落盘到 logs/），PID 写入 logs/studyos.pid
  echo "[*] 后台常驻模式：终端关掉也不影响，停止请运行 ./scripts/stop.sh"
  printf '\n===== %s 后台启动 =====\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$ROOT/logs/backend.out.log"
  nohup ./.venv/bin/uvicorn app.api.main:app --host 0.0.0.0 --port 8800 \
    >>"$ROOT/logs/backend.out.log" 2>>"$ROOT/logs/backend.err.log" &
  pid=$!
  echo "$pid" >"$PID_FILE"
  for _ in $(seq 1 30); do
    _backend_started && break
    sleep 1
  done
  if _backend_started; then
    # 注意：不要把 $pid 塞进中文字符串里输出。在 C.UTF-8 等 UTF-8 locale 下，bash
    # 展开紧跟全角标点的变量会丢掉首字节，把 PID 打成乱码/非法 UTF-8（已实测复现；
    # 把 LC_ALL=C 只加在 echo 前也无效，必须在解释器启动前生效）。故 PID 单独一行纯 ASCII 输出。
    echo "StudyOS backend ready (PID $pid) - logs/studyos.pid"
    echo "StudyOS 后端已就绪，PID 已写入 logs/studyos.pid"
    echo "查看日志：tail -f logs/backend.out.log"
    exit 0
  fi
  echo "[错误] 后端 30 秒内未就绪，启动失败。logs/backend.err.log 末尾：" >&2
  tail -n 20 "$ROOT/logs/backend.err.log" >&2 || true
  rm -f "$PID_FILE"
  exit 1
fi

echo "（Ctrl+C 停止；关掉本终端窗口后端也会停止，想常驻请用 ./scripts/start.sh -d）"
exec ./.venv/bin/uvicorn app.api.main:app --host 0.0.0.0 --port 8800
