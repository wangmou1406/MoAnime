# -*- coding: utf-8 -*-
"""系统与依赖：日志、统计、打开路径、ffmpeg 下载安装、浏览器取流状态

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import os
import time
from flask import request
from core.config import ROOT_DIR, VERSION, config
from core.download_engine import ffmpeg_available, find_ffmpeg
from core.logging_setup import clear_logs, recent_logs
from core.task_manager import download_manager
from sources.manager import source_manager

# 跨模块引用
from ._base import _START_TIME, api, arg_int, fail, logger, ok
from .meta import fs_open

# ------------------------------------------------------------------ 日志与系统
@api.get("/logs")
def logs():
    after = arg_int("after", 0) or 0
    limit = arg_int("limit", 300) or 300
    level = request.args.get("level") or "ALL"
    return ok({"logs": recent_logs(limit=limit, after_id=after, level=level)})


@api.get("/system/network")
def system_network():
    """当前监听状态与可访问地址，供设置页的「远程访问」面板展示。

    任务/源/历史都只存在这一台机器的 SQLite 里，所以「另一台电脑能看到同样的内容」
    靠的是同一个服务被多台机器访问，而不是同步。这里把该给用户看的地址都列出来。
    """
    import socket

    host = str(config.get("web_host") or "127.0.0.1")
    token = (config.get("access_token") or "").strip()

    # 端口以「这次请求实际连上的端口」为准：命令行 --port 覆盖过配置时，
    # 配置里的 web_port 是旧值，照着它拼出来的地址会打不开。
    port = 0
    host_header = request.host or ""
    if ":" in host_header:
        try:
            port = int(host_header.rsplit(":", 1)[1])
        except (TypeError, ValueError):
            port = 0
    port = port or int(config.get("web_port") or 8848)

    def _primary_ip() -> str:
        """默认路由用的那个地址（UDP connect 只是让内核选网卡，不发包）。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return ""
        finally:
            sock.close()

    local_only = host in ("127.0.0.1", "localhost", "::1")
    addresses = []
    if not local_only:
        primary = _primary_ip()
        others = []
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip.startswith(("127.", "169.254.")):
                    continue
                if ip in [a["ip"] for a in addresses] or ip in others:
                    continue
                others.append(ip)
        except Exception as exc:  # noqa: BLE001
            logger.warning("枚举本机地址失败：%s", exc)

        if primary and not primary.startswith("127."):
            addresses.append({"ip": primary, "url": "http://%s:%d/" % (primary, port),
                              "primary": True})
        for ip in others:
            if ip == primary:
                continue
            addresses.append({"ip": ip, "url": "http://%s:%d/" % (ip, port),
                              "primary": False})

    return ok({
        "host": host,
        "port": port,
        "lan_enabled": not local_only,
        "local_url": "http://127.0.0.1:%d/" % port,
        "addresses": addresses,
        "token_enabled": bool(token),
        "platform": os.name,
    })


@api.delete("/logs")
def clear_log():
    clear_logs()
    return ok({"cleared": True})


@api.get("/stats")
def stats():
    import shutil
    base = config.download_dir()
    try:
        usage = shutil.disk_usage(base)
        disk = {"total": usage.total, "free": usage.free, "used": usage.used}
    except Exception:
        disk = None
    return ok({
        "tasks": download_manager.statistics(),
        "disk": disk,
        "download_dir": base,
        "sources_total": len(source_manager.list_sources()),
        "sources_enabled": len([s for s in source_manager.list_sources() if s.get("enabled")]),
        "ffmpeg": ffmpeg_available(),
        "uptime": round(time.time() - _START_TIME, 1),
        "version": VERSION,
    })


@api.post("/system/open-path")
def open_path():
    return fs_open()


FFMPEG_URLS = [
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]


_FFMPEG_JOB = {"running": False, "message": "", "ok": None}


def _install_ffmpeg() -> None:
    import io
    import urllib.request
    import zipfile
    from core.config import ROOT_DIR

    tools_dir = os.path.join(ROOT_DIR, "tools")
    target = os.path.join(tools_dir, "ffmpeg.exe")
    os.makedirs(tools_dir, exist_ok=True)
    for url in FFMPEG_URLS:
        try:
            _FFMPEG_JOB["message"] = "正在下载 %s" % url.split("/")[2]
            request = urllib.request.Request(url, headers={"User-Agent": "AnimeDownloader/1.0"})
            with urllib.request.urlopen(request, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                buffer = io.BytesIO()
                read = 0
                while True:
                    chunk = resp.read(1024 * 512)
                    if not chunk:
                        break
                    buffer.write(chunk)
                    read += len(chunk)
                    _FFMPEG_JOB["message"] = "已下载 %.1f MB%s" % (
                        read / 1048576.0,
                        "/%.1f MB" % (total / 1048576.0) if total else "")
            buffer.seek(0)
            with zipfile.ZipFile(buffer) as archive:
                member = next((name for name in archive.namelist()
                               if os.path.basename(name).lower() == "ffmpeg.exe"), None)
                if not member:
                    _FFMPEG_JOB["message"] = "压缩包内没有 ffmpeg.exe"
                    continue
                with archive.open(member) as src, open(target, "wb") as dst:
                    import shutil as _shutil
                    _shutil.copyfileobj(src, dst, 1024 * 1024)
            _FFMPEG_JOB.update({"ok": True, "message": "已安装到 %s" % target})
            logger.info("ffmpeg 安装完成：%s", target)
            return
        except Exception as exc:  # noqa: BLE001
            _FFMPEG_JOB["message"] = "下载失败：%s" % exc
            logger.warning("下载 ffmpeg 失败（%s）：%s", url, exc)
    _FFMPEG_JOB["ok"] = False
    if not _FFMPEG_JOB["message"]:
        _FFMPEG_JOB["message"] = "自动下载失败，请手动把 ffmpeg.exe 放到 tools 目录"


@api.get("/system/ffmpeg")
def ffmpeg_status():
    return ok({
        "available": ffmpeg_available(),
        "path": find_ffmpeg() or "",
        "job": dict(_FFMPEG_JOB),
        "mirrors": FFMPEG_URLS,
    })


@api.get("/system/browser-resolver")
def browser_resolver_status():
    """浏览器取流能力状态（设置页显示）。

    有些站的视频地址要执行站点自己的 JS 才算得出来（会话签名、混淆解密、
    客户端渲染）。本程序用无头浏览器打开播放页、监听网络请求截获真实地址。
    这里报告 Node / Chromium / playwright-core 是否就绪，以及总开关状态。
    """
    from core import browser_resolver
    info = browser_resolver.available()
    return ok({
        "ok": info["ok"],
        "enabled": info["enabled"],
        "usable": info["usable"],
        "reason": info["reason"],
        "node": info["node"],
        "browser": info["browser"],
        "playwright": info["playwright"],
        "builtin_browser": info.get("builtin_browser", False),
        "builtin_node": info.get("builtin_node", False),
        "cache": browser_resolver.cache_size(),
    })


@api.post("/system/browser-resolver/clear-cache")
def clear_browser_cache():
    from core import browser_resolver
    before = browser_resolver.cache_size()
    browser_resolver.clear_cache()
    return ok({"cleared": before})


@api.post("/system/ffmpeg/install")
def ffmpeg_install():
    if _FFMPEG_JOB["running"]:
        return fail("正在安装中，请稍候")
    if ffmpeg_available():
        return ok({"message": "ffmpeg 已可用", "path": find_ffmpeg()})
    # 这个「一键安装」下载的是 Windows 版 ffmpeg.exe；
    # Linux / Docker 镜像里已通过包管理器装好系统 ffmpeg，不该再走这里。
    if os.name != "nt":
        return fail("当前系统不是 Windows，无法使用内置下载。"
                    "Docker 镜像已自带 ffmpeg；其它 Linux 请执行 apt install ffmpeg。")

    import threading
    _FFMPEG_JOB.update({"running": True, "ok": None, "message": "准备下载…"})

    def worker():
        try:
            _install_ffmpeg()
        finally:
            _FFMPEG_JOB["running"] = False
            try:
                from web.app import socketio
                socketio.emit("ffmpeg_status", dict(_FFMPEG_JOB))
            except Exception:
                pass

    threading.Thread(target=worker, name="ffmpeg-install", daemon=True).start()
    return ok({"message": "已开始下载 ffmpeg（约 100MB，取决于网络）"})
