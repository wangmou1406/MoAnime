# -*- coding: utf-8 -*-
"""下载 Windows 版 ffmpeg 到 tools/ 目录（用于 HLS 分片转封装为 mp4）。

用法：
    python tools/fetch_ffmpeg.py            # 自动下载并解压
    python tools/fetch_ffmpeg.py --check    # 只检查是否可用
"""
import io
import os
import shutil
import sys
import urllib.request
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
TARGET = os.path.join(TOOLS_DIR, "ffmpeg.exe")

SOURCES = [
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]


def already_available() -> bool:
    if os.path.exists(TARGET):
        return True
    found = shutil.which("ffmpeg")
    return bool(found)


def download(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MoAnime/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        chunks = []
        read = 0
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            chunks.append(chunk)
            read += len(chunk)
            if total:
                sys.stdout.write("\r下载中 %.1f%% (%.1f MB)" % (
                    read * 100.0 / total, read / 1048576.0))
            else:
                sys.stdout.write("\r下载中 %.1f MB" % (read / 1048576.0))
            sys.stdout.flush()
        print()
        return b"".join(chunks)


def extract(data: bytes) -> bool:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        print("下载内容不是 zip 压缩包，可能被网络拦截")
        return False
    target_name = None
    for name in archive.namelist():
        base = os.path.basename(name).lower()
        if base in ("ffmpeg.exe", "ffmpeg"):
            target_name = name
            break
    if not target_name:
        print("压缩包里没有找到 ffmpeg 可执行文件")
        return False
    os.makedirs(TOOLS_DIR, exist_ok=True)
    with archive.open(target_name) as src, open(TARGET, "wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    print("已安装：%s (%.1f MB)" % (TARGET, os.path.getsize(TARGET) / 1048576.0))
    return True


def main() -> int:
    if "--check" in sys.argv:
        print("ffmpeg 可用：%s" % (TARGET if os.path.exists(TARGET) else shutil.which("ffmpeg")))
        return 0 if already_available() else 1
    if already_available():
        print("ffmpeg 已可用，无需下载：%s" % (TARGET if os.path.exists(TARGET)
                                              else shutil.which("ffmpeg")))
        return 0
    for url in SOURCES:
        print("尝试下载：%s" % url)
        try:
            data = download(url)
        except Exception as exc:  # noqa: BLE001
            print("下载失败：%s" % exc)
            continue
        if extract(data):
            return 0
    print("自动下载失败，请手动把 ffmpeg.exe 放到 %s" % TARGET)
    return 1


if __name__ == "__main__":
    sys.exit(main())
