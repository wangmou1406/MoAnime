#!/usr/bin/env bash
# ============================================================================
#  动漫下载机 —— Linux / macOS 停止脚本
#
#  按 settings.json 里配置的端口找到监听进程并结束（和 Windows 的 停止.ps1 对应）。
#
#  用法：
#      ./python/停止.sh            # 用 settings.json 里的端口
#      ./python/停止.sh 9000       # 指定端口
# ============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${1:-}"
if [ -z "$PORT" ]; then
    PORT="8848"
    # 从 settings.json 里读端口（用 python 解析，避免依赖 jq）
    if [ -f "$ROOT/data/settings.json" ]; then
        PY="$ROOT/.venv/bin/python"
        [ -x "$PY" ] || PY="$(command -v python3 || true)"
        if [ -n "$PY" ]; then
            PORT="$("$PY" - "$ROOT/data/settings.json" <<'PYEOF'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("web_port") or 8848)
except Exception:
    print(8848)
PYEOF
)"
        fi
    fi
fi

echo "查找监听 $PORT 端口的动漫下载机进程 ..."

PIDS=""
# 优先用 lsof，其次 ss，最后 /proc
if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
elif command -v ss >/dev/null 2>&1; then
    PIDS="$(ss -lptnH "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)"
fi
if [ -z "$PIDS" ] && [ -d /proc ]; then
    for d in /proc/[0-9]*; do
        pid="${d#/proc/}"
        if [ -r "$d/cmdline" ] && tr '\0' ' ' < "$d/cmdline" 2>/dev/null | grep -q "run.py"; then
            PIDS="$PIDS $pid"
        fi
    done
fi

# 只结束确实是本程序的进程（命令行里含 run.py 或项目路径）
KILLED=0
for pid in $PIDS; do
    [ -r "/proc/$pid/cmdline" ] || true
    CMD=""
    if [ -r "/proc/$pid/cmdline" ]; then
        CMD="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    fi
    if echo "$CMD" | grep -q "run.py" || echo "$CMD" | grep -q "$ROOT"; then
        kill "$pid" 2>/dev/null && {
            echo "  已停止 PID $pid"
            KILLED=$((KILLED + 1))
        }
    else
        echo "  ⚠ 跳过 PID $pid（不是本程序：$CMD）"
    fi
done

if [ "$KILLED" -eq 0 ]; then
    echo "  没有找到本程序在监听 $PORT（可能已经停了）"
    exit 0
fi

sleep 1
echo "完成。"
