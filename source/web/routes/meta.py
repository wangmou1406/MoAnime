# -*- coding: utf-8 -*-
"""元信息与文件系统：版本信息、目录浏览、打开/定位路径

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import os
import platform
import subprocess
import sys
from flask import request
from core.config import DATA_DIR, ROOT_DIR, VERSION, config
from core.download_engine import ffmpeg_available, find_ffmpeg
from core.naming import scheme_labels
from sources.manager import source_manager

# 跨模块引用
from ._base import api, body, fail, ok

# ------------------------------------------------------------------ 元信息
@api.get("/meta")
def meta():
    return ok({
        "version": VERSION,
        "python": sys.version.split()[0],
        "platform": "%s %s" % (platform.system(), platform.release()),
        "ffmpeg": ffmpeg_available(),
        "ffmpeg_path": find_ffmpeg() or "",
        "root_dir": ROOT_DIR,
        "data_dir": DATA_DIR,
        "download_dir": config.download_dir(),
        "naming_schemes": scheme_labels(),
        "source_types": source_manager.types(),
    })


@api.get("/fs/list")
def fs_list():
    """文件夹选择器：列出盘符或某目录下的子目录。"""
    path = (request.args.get("path") or "").strip()
    if not path:
        if platform.system() == "Windows":
            import string
            from ctypes import windll
            drives = []
            try:
                bitmask = windll.kernel32.GetLogicalDrives()
                for index, letter in enumerate(string.ascii_uppercase):
                    if bitmask & (1 << index):
                        drives.append({"name": "%s:\\" % letter, "path": "%s:\\" % letter,
                                       "is_dir": True})
            except Exception:
                drives = [{"name": "%s:\\" % c, "path": "%s:\\" % c, "is_dir": True}
                          for c in "CDEFG"]
            return ok({"path": "", "parent": "", "is_drive_list": True, "entries": drives})
        path = os.path.expanduser("~")

    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return fail("目录不存在：%s" % path, 404)
    entries = []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                try:
                    if entry.is_dir() and not entry.name.startswith("$"):
                        entries.append({"name": entry.name, "path": entry.path, "is_dir": True})
                except OSError:
                    continue
    except OSError as exc:
        return fail("无法读取目录：%s" % exc, 403)
    entries.sort(key=lambda item: item["name"].lower())
    parent = os.path.dirname(path.rstrip("\\/")) or (path[:3] if platform.system() == "Windows" else "/")
    if platform.system() == "Windows" and len(path) <= 3:
        parent = ""
    return ok({"path": path, "parent": parent, "entries": entries,
               "writable": os.access(path, os.W_OK), "free": _disk_free(path)})


def _disk_free(path: str):
    try:
        import shutil
        usage = shutil.disk_usage(path)
        return {"total": usage.total, "free": usage.free, "used": usage.used}
    except Exception:
        return None


@api.route("/fs/open", methods=["GET", "POST"])
def fs_open():
    """在系统文件管理器里打开目录。"""
    data = body()
    path = data.get("path") or config.download_dir()
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return fail("目录不存在：%s" % path, 404)
    try:
        if platform.system() == "Windows":
            os.startfile(path)  # noqa: S606
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return ok({"path": path})
    except Exception as exc:  # noqa: BLE001
        return fail("打开目录失败：%s" % exc)


@api.route("/fs/reveal", methods=["GET", "POST"])
def fs_reveal():
    """在文件管理器里定位某个文件（打开所在目录并选中它）。"""
    data = body()
    path = data.get("path") or ""
    path = os.path.abspath(os.path.expanduser(path)) if path else ""
    if not path or not os.path.exists(path):
        return fail("文件不存在：%s" % path, 404)
    # 只允许下载目录内的路径，避免被当成任意文件打开器
    base = os.path.abspath(config.download_dir())
    if not path.startswith(base):
        return fail("只能定位下载目录内的文件", 403)
    try:
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", path])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        return ok({"path": path})
    except Exception as exc:  # noqa: BLE001
        return fail("定位文件失败：%s" % exc)
