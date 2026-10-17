#!/usr/bin/env bash
# ============================================================================
#  动漫下载机 Anime Downloader —— Linux / macOS 启动脚本
#
#  和 Windows 上的 启动.bat 对应：自动找 Python、建 .venv、装依赖、启动服务。
#
#  用法：
#      ./python/启动.sh                # 前台运行（Ctrl+C 停止）
#      ./python/启动.sh --port 9000    # 指定端口
#      ./python/启动.sh --host 0.0.0.0 # 允许局域网访问
#
#  后台运行：
#      nohup ./python/启动.sh > /dev/null 2>&1 &
#      ./python/停止.sh                # 停止（按端口找进程）
# ============================================================================
set -euo pipefail

# 脚本在 python/ 子目录，项目根是它的上一级
# 目录布局：
#   python/      ← 本脚本所在（运行根：.venv / data / logs / downloads）
#   ../source/   ← 代码根（run.py / core / web / tools）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$ROOT/../source" && pwd)"
# 把数据目录钉在 python/ 下（否则程序会按 config.py 的位置写到 source/data）
export AD_DATA_DIR="$ROOT/data"
export AD_LOG_DIR="$ROOT/logs"
export AD_BUNDLED_DIR="$ROOT/data/_pwt"
# 下载目录只在首次运行时给默认值，之后以界面设置为准
if [ ! -f "$ROOT/data/settings.json" ]; then
    export AD_DOWNLOAD_DIR="$ROOT/downloads"
fi

cd "$ROOT"

echo "============================================================"
echo " 动漫下载机 Anime Downloader"
echo " 项目目录: $ROOT"
echo "============================================================"

# ---------------------------------------------------------------- 1) 找 Python
PY=""
for cand in "$ROOT/.venv/bin/python" python3 python3.13 python3.12 python3.11 python3.10 python; do
    if [ "$cand" = "$ROOT/.venv/bin/python" ]; then
        [ -x "$cand" ] || continue
    else
        command -v "$cand" >/dev/null 2>&1 || continue
    fi
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
    major="${ver%%.*}"; minor="${ver##*.}"
    if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
        PY="$cand"; break
    fi
done

if [ -z "$PY" ]; then
    echo "✗ 没找到 Python ≥ 3.10。"
    echo "  Debian/Ubuntu:  sudo apt install python3 python3-venv python3-pip"
    echo "  Fedora:         sudo dnf install python3 python3-pip"
    echo "  macOS:          brew install python@3.12"
    exit 1
fi
echo "使用 Python: $PY  ($("$PY" -V 2>&1))"

# ---------------------------------------------------------------- 2) 虚拟环境
if [ ! -d "$ROOT/.venv" ]; then
    echo "创建虚拟环境 .venv ..."
    "$PY" -m venv "$ROOT/.venv" || {
        echo "✗ 创建 venv 失败。Debian/Ubuntu 需要先装 python3-venv："
        echo "    sudo apt install python3-venv"
        exit 1
    }
fi
PY="$ROOT/.venv/bin/python"

# ---------------------------------------------------------------- 3) 依赖
if ! "$PY" -c "import flask, flask_socketio, m3u8, bs4, lxml, Crypto" >/dev/null 2>&1; then
    echo "安装依赖（首次约 2~5 分钟）..."
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$PY" -m pip install -r "$SRC/requirements.txt" || {
        echo "✗ 依赖安装失败。若只是 OCR 相关包失败，可以先把"
        echo "  requirements.txt 里的 rapidocr-onnxruntime 注释掉再试"
        echo "  （那样文字广告体检会明确报「画面 OCR 不可用」）。"
        exit 1
    }
fi

# ---------------------------------------------------------------- 4) ffmpeg
FFMPEG="$(command -v ffmpeg || true)"
if [ -z "$FFMPEG" ]; then
    if [ -x "$SRC/tools/ffmpeg" ]; then
        FFMPEG="$SRC/tools/ffmpeg"
        echo "使用项目自带 ffmpeg: $FFMPEG"
    else
        echo "⚠ 没找到 ffmpeg —— m3u8 转 mp4 会退化成 .ts 分段合并，建议安装："
        echo "    Debian/Ubuntu: sudo apt install ffmpeg"
        echo "    Fedora:        sudo dnf install ffmpeg"
        echo "    macOS:         brew install ffmpeg"
    fi
else
    echo "使用系统 ffmpeg: $FFMPEG"
fi

# ---------------------------------------------------------------- 5) 启动
echo "启动中 ... （Ctrl+C 停止）"
exec "$PY" "$SRC/run.py" "$@"
