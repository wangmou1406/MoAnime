# -*- coding: utf-8 -*-
"""打开浏览器的小工具。

为什么需要它：
    直接 `Start-Process <url>` / `webbrowser.open()` 会去"喂给"系统里已有的浏览器实例。
    如果你平时用的 Edge/Chrome 恰好是**以管理员权限**启动的，普通权限的程序就无法接入，
    Windows 会弹出「现有实例正在以提升的权限运行，是否用普通权限重启」的对话框。

这里改成：用**独立的浏览器配置目录**（`data/browser-profile`）启动一个独立实例，
从不接触你可能正在用的那个实例，因此不会再弹这个窗口；而且即使系统里没有浏览器，
也只是安静地跳过，不影响服务。
"""
import os
import platform
import subprocess
import sys
from urllib.parse import urlparse

from .config import DATA_DIR
from .logging_setup import get_logger

logger = get_logger("moanime.browser")

PROFILE_DIR = os.path.join(DATA_DIR, "browser-profile")

_WINDOWS_BROWSERS = [
    (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "msedge"),
    (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "msedge"),
    (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
    (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome"),
]


def _find_browser():
    """返回 (可执行文件路径, 是否为 Chromium 系)。"""
    system = platform.system()
    if system == "Windows":
        for path, _name in _WINDOWS_BROWSERS:
            if os.path.exists(path):
                return path, True
        return None, False
    if system == "Darwin":
        for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
            if os.path.exists(path):
                return path, True
        return None, False
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate, True
    return None, False


def open_url(url: str, isolated: bool = True) -> bool:
    """打开网址。isolated=True 时用独立配置目录，避免与已有浏览器实例冲突。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        logger.warning("拒绝打开非 http(s) 地址：%s", url)
        return False

    path, is_chromium = _find_browser()
    args = [url]
    if path and is_chromium and isolated:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        args = [
            "--user-data-dir=%s" % PROFILE_DIR,
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            url,
        ]

    try:
        if path:
            subprocess.Popen([path] + args, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("已打开浏览器：%s", url)
            return True
        return _fallback_open(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("打开浏览器失败（不影响使用，请手动访问 %s）：%s", url, exc)
        return False


def _fallback_open(url: str) -> bool:
    """按系统默认方式打开（macOS / Linux / 没有找到已知浏览器时）。"""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", url], close_fds=True)
        elif system == "Windows":
            os.startfile(url)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", url], close_fds=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法自动打开浏览器（请手动访问 %s）：%s", url, exc)
        return False


if __name__ == "__main__":  # pragma: no cover
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8848/"
    print("打开：", target, "->", open_url(target))
