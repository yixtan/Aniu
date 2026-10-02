#!/usr/bin/env bash
# start.sh — 在 macOS 上后台运行 Aniu，启动后可以关闭终端
#
# 第一次使用前先运行一次 ./install.sh 完成安装。
#
# 用法（在项目根目录执行）：
#   ./start.sh            启动。后台运行，关掉终端也不停；意外崩溃会在几秒内自动重启
#   ./start.sh stop       停止
#   ./start.sh restart    重启
#   ./start.sh status     查看是否在运行、运行了多久、日志在哪
#   ./start.sh logs       实时看日志（Ctrl+C 只退出查看，不会停掉 Aniu）
#   ./start.sh dev        临时开前端开发服务器 http://localhost:5173（Ctrl+C 结束）
#
# 访问地址：本机 http://localhost:8000，手机走 Tailscale（和本机看到的是同一份构建）。
#
# 后台运行交给 macOS 自带的 launchd。它的配置文件放在 .aniu/local/launchd/，
# 不放进 ~/Library/LaunchAgents，所以不会开机自启：重启电脑或注销后，
# 需要再运行一次 ./start.sh。
#
# 不用 --reload：launchd 直接看着真正干活的进程，它一崩溃就重新拉起。
# 用 --reload 的话，崩溃的是子进程，盯着文件的父进程毫无察觉，端口也还占着，
# 服务就这么停着。代价是更新代码后要 ./start.sh restart 才生效。
#
# 日志：.aniu/local/backend.log，由后端自己写，满 10 MB 换一个文件，
# 最多留 5 份旧的；重启不清空。启动阶段崩溃的报错另记在 backend-crash.log。
#
# 默认打开定时任务（ANIU_ENABLE_SCHEDULER=1）。临时关闭：
#   ANIU_ENABLE_SCHEDULER=0 ./start.sh restart

set -Eeuo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  printf '这个脚本只支持 macOS（用的是系统自带的 launchd）。Linux 请用 ./install.sh 或 Docker。\n' >&2
  exit 1
fi

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
LOCAL_DIR="$ROOT_DIR/.aniu/local"
VENV_PY="$LOCAL_DIR/.venv/bin/python"
BACKEND_LOG="$LOCAL_DIR/backend.log"
CRASH_LOG="$LOCAL_DIR/backend-crash.log"
LAUNCHD_DIR="$LOCAL_DIR/launchd"
LABEL="local.aniu.backend"
PLIST="$LAUNCHD_DIR/$LABEL.plist"
TS_HOST_CACHE="$LOCAL_DIR/tailscale-host"
HOST="${ANIU_HOST:-127.0.0.1}"
PORT="${ANIU_PORT:-8000}"
SCHEDULER="${ANIU_ENABLE_SCHEDULER:-1}"
READY_URL="http://$HOST:$PORT/health/ready"
READY_TIMEOUT_SECONDS=60
DOMAIN="gui/$(id -u)"
# launchd 启动的程序拿不到终端里的 PATH，这里写全。
JOB_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

say() { printf '==> %s\n' "$*"; }
fail() { printf '错误：%s\n' "$*" >&2; exit 1; }

is_loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

# 这几个查询「查不到」是正常结果，不能让严格模式当成出错退出。
job_pid() {
  { launchctl print "$DOMAIN/$LABEL" 2>/dev/null || true; } \
    | awk '$1 == "pid" && $2 == "=" { print $3; exit }'
}

# 服务名是全局的：同一台电脑上另一个目录的 Aniu 也叫这个名字。
job_plist() {
  { launchctl print "$DOMAIN/$LABEL" 2>/dev/null || true; } \
    | awk '$1 == "path" && $2 == "=" { print $3; exit }'
}

# 在跑的是另一个目录的 Aniu 时，说清楚并退出，免得把它当成自己的。
refuse_foreign_job() {
  local other
  other="$(job_plist)"
  if [ -n "$other" ] && [ "$other" != "$PLIST" ]; then
    printf '错误：正在运行的是另一个目录的 Aniu（%s）。\n' "${other%/.aniu/local/launchd/*}" >&2
    printf '请先到那个目录运行 ./start.sh stop。\n' >&2
    exit 1
  fi
}

is_ready() { curl -s -o /dev/null -f --max-time 3 "$READY_URL"; }

port_owner() { { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true; } | head -1; }

# 手机经 Tailscale 访问时，后端要认得本机的 tailnet 域名，否则返回 400。
# 读不到时（比如刚开机 Tailscale 还没起来）用上一次读到的值。
tailscale_host() {
  local name=""
  if command -v tailscale >/dev/null 2>&1; then
    name="$(tailscale status --json 2>/dev/null \
      | "$VENV_PY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' \
      2>/dev/null || true)"
  fi
  if [ -n "$name" ]; then
    printf '%s\n' "$name" > "$TS_HOST_CACHE"
  elif [ -f "$TS_HOST_CACHE" ]; then
    name="$(cat "$TS_HOST_CACHE")"
  fi
  printf '%s' "$name"
}

xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

write_plist() {
  local allowed="$1"
  mkdir -p "$LAUNCHD_DIR"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml_escape "$VENV_PY")</string>
    <string>-m</string>
    <string>backend.serve</string>
    <string>--host</string>
    <string>$(xml_escape "$HOST")</string>
    <string>--port</string>
    <string>$(xml_escape "$PORT")</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml_escape "$ROOT_DIR")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$JOB_PATH</string>
    <key>ANIU_ENABLE_SCHEDULER</key>
    <string>$(xml_escape "$SCHEDULER")</string>
    <key>ANIU_ALLOWED_HOSTS</key>
    <string>$(xml_escape "$allowed")</string>
    <key>ANIU_LOG_FILE</key>
    <string>$(xml_escape "$BACKEND_LOG")</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>StandardOutPath</key>
  <string>/dev/null</string>
  <key>StandardErrorPath</key>
  <string>$(xml_escape "$CRASH_LOG")</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null || fail "生成的服务配置有误：$PLIST"
}

print_addresses() {
  local ts
  ts="$(cat "$TS_HOST_CACHE" 2>/dev/null || true)"
  printf '    本机：http://localhost:%s\n' "$PORT"
  if [ -n "$ts" ]; then
    printf '    手机：https://%s/\n' "$ts"
  fi
}

cmd_start() {
  [ -x "$VENV_PY" ] || fail "找不到 Python 环境 ${VENV_PY}。第一次使用请先运行 ./install.sh 完成安装。"
  refuse_foreign_job

  if is_loaded && is_ready; then
    say "Aniu 已经在运行（进程 $(job_pid)），不用重复启动。"
    print_addresses
    return 0
  fi
  if is_loaded; then
    # 登记了但没在正常服务（比如上次启动失败、正在反复重启），先清掉再来。
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    sleep 1
  fi

  local owner
  owner="$(port_owner)"
  if [ -n "$owner" ]; then
    printf '错误：端口 %s 已被别的程序占用：\n' "$PORT" >&2
    ps -o pid=,command= -p "$owner" >&2 || true
    printf '可能是用旧方法启动的 Aniu 还开着。请先在那个终端按 Ctrl+C 停掉，再运行 ./start.sh。\n' >&2
    exit 1
  fi

  mkdir -p "$LOCAL_DIR"
  local ts allowed
  ts="$(tailscale_host)"
  if [ -n "$ts" ]; then
    allowed="$ts,localhost,127.0.0.1"
  else
    allowed="localhost,127.0.0.1"
    say "没读到 Tailscale 域名，这次只允许本机访问；Tailscale 连上后运行 ./start.sh restart。"
  fi
  if [ -n "${ANIU_ALLOWED_HOSTS:-}" ]; then
    allowed="$ANIU_ALLOWED_HOSTS"
  fi

  write_plist "$allowed"

  say "正在启动 Aniu（定时任务：$([ "$SCHEDULER" = "1" ] && echo 开 || echo 关)）"
  launchctl bootstrap "$DOMAIN" "$PLIST" || fail "后台服务登记失败，运行 ./start.sh status 查看。"

  local waited=0
  until is_ready; do
    if [ "$waited" -ge "$READY_TIMEOUT_SECONDS" ]; then
      printf '错误：等了 %s 秒还没就绪，已停止，免得它在后台反复重启。最近的日志：\n' "$READY_TIMEOUT_SECONDS" >&2
      launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
      tail -n 20 "$BACKEND_LOG" 2>/dev/null | pretty_logs >&2 || true
      if [ -s "$CRASH_LOG" ]; then
        printf '\n启动报错（%s）：\n' "$CRASH_LOG" >&2
        tail -n 20 "$CRASH_LOG" >&2 || true
      fi
      exit 1
    fi
    sleep 1
    waited=$((waited + 1))
  done

  say "Aniu 已启动（进程 $(job_pid)），现在可以关闭这个终端。"
  print_addresses
  printf '    停止：./start.sh stop    状态：./start.sh status    日志：./start.sh logs\n'
}

cmd_stop() {
  refuse_foreign_job
  if ! is_loaded; then
    say "Aniu 没有在运行。"
    local owner
    owner="$(port_owner)"
    if [ -n "$owner" ]; then
      printf '注意：端口 %s 上有别的程序（不是这个脚本启动的）：\n' "$PORT"
      ps -o pid=,command= -p "$owner" || true
    fi
    return 0
  fi
  say "正在停止 Aniu"
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  local waited=0
  while [ -n "$(port_owner)" ] && [ "$waited" -lt 20 ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if [ -n "$(port_owner)" ]; then
    fail "已发出停止指令，但端口 $PORT 仍被占用，运行 ./start.sh status 查看。"
  fi
  say "已停止。"
}

cmd_status() {
  refuse_foreign_job
  if ! is_loaded; then
    say "Aniu 没有在运行。启动：./start.sh"
  else
    local pid uptime
    pid="$(job_pid)"
    if [ -z "$pid" ]; then
      say "Aniu 已登记，但进程不在（可能刚崩溃，正在等待自动重启）。看日志：./start.sh logs"
    else
      uptime="$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
      if is_ready; then
        say "Aniu 正在运行：进程 ${pid}，已运行 ${uptime:-未知}，服务正常。"
      else
        say "Aniu 进程 $pid 在，但服务还没就绪（刚启动或正在重新加载）。"
      fi
      local sched
      sched="$(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:ANIU_ENABLE_SCHEDULER' "$PLIST" 2>/dev/null || echo '?')"
      case "$sched" in
        1) sched="开" ;;
        0) sched="关" ;;
        *) sched="未知" ;;
      esac
      printf '    定时任务：%s\n' "$sched"
      print_addresses
    fi
  fi
  if [ -f "$BACKEND_LOG" ]; then
    printf '    日志：%s（%s）\n' "$BACKEND_LOG" "$(du -h "$BACKEND_LOG" | cut -f1 | tr -d ' ')"
  fi
  if [ -s "$CRASH_LOG" ]; then
    printf '    启动报错记录：%s（最后修改 %s）\n' "$CRASH_LOG" "$(date -r "$CRASH_LOG" '+%m-%d %H:%M')"
  fi
}

# 日志是一行一个 JSON，这里翻成「北京时间 级别 内容」好读一些。
pretty_logs() {
  "$VENV_PY" -u -c '
import json, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo("Asia/Shanghai")
skip = {"timestamp", "level", "logger", "message", "exception"}
for line in sys.stdin:
    line = line.rstrip("\n")
    try:
        d = json.loads(line)
    except ValueError:
        print(line, flush=True)
        continue
    try:
        t = datetime.fromisoformat(d["timestamp"]).astimezone(tz).strftime("%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        t = "?"
    extra = " ".join(f"{k}={v}" for k, v in d.items() if k not in skip)
    level = d.get("level", "")
    message = d.get("message", "")
    tail = f"  [{extra}]" if extra else ""
    print(f"{t} {level:7} {message}{tail}", flush=True)
    if d.get("exception"):
        print(d["exception"], flush=True)
'
}

cmd_logs() {
  [ -f "$BACKEND_LOG" ] || fail "还没有日志：$BACKEND_LOG"
  say "实时日志（北京时间），Ctrl+C 退出查看，不会停掉 Aniu"
  tail -n 40 -F "$BACKEND_LOG" | pretty_logs
}

cmd_dev() {
  is_ready || fail "Aniu 后端没在运行，先运行 ./start.sh。"
  say "前端开发服务器：http://localhost:5173（Ctrl+C 结束，不影响后端）"
  exec npm --prefix "$ROOT_DIR/frontend" run dev
}

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
}

case "${1:-start}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  dev) cmd_dev ;;
  -h|--help|help) usage ;;
  *) printf '不认识的命令：%s\n\n' "$1" >&2; usage >&2; exit 2 ;;
esac
