# -*- coding: utf-8 -*-
"""设置：读写配置、校验目录、清理临时文件、免责声明、恢复出厂

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import os
from flask import make_response, request
from core.config import DATA_DIR, DEFAULT_SETTINGS, DISCLAIMER_VERSION, TOKEN_COOKIE, config
from core.database import db
from core.logging_setup import clear_logs
from core.naming import scheme_labels
from core.task_manager import download_manager
from sources.manager import source_manager

# 跨模块引用
from ._base import api, body, fail, logger, ok
from .meta import _disk_free

# ------------------------------------------------------------------ 设置
@api.get("/settings")
def get_settings():
    payload = config.all()
    payload["_download_dir_exists"] = os.path.isdir(payload.get("download_dir") or "")
    return ok({"settings": payload, "schemes": scheme_labels(), "defaults": DEFAULT_SETTINGS})


@api.route("/settings", methods=["GET", "POST"])
def update_settings():
    data = body()
    if not isinstance(data, dict):
        return fail("参数格式不正确")
    before_dir = config.get("download_dir")
    config.update(data)
    if "download_dir" in data and data["download_dir"] != before_dir:
        logger.info("下载目录已更新为：%s", data["download_dir"])
    if {"concurrent_tasks", "segment_threads"} & set(data.keys()):
        download_manager.apply_settings()

    payload = ok({"settings": config.all()})
    # 远程客户端刚设了令牌：顺手给它种上 cookie，否则保存完下一次请求就被拦了
    token = (config.get("access_token") or "").strip()
    if "access_token" in data and token:
        response = make_response(payload)
        response.set_cookie(TOKEN_COOKIE, token, max_age=86400 * 30,
                            httponly=True, samesite="Lax")
        return response
    return payload


@api.post("/settings/reset")
def reset_settings():
    config.reset()
    download_manager.apply_settings()
    return ok({"settings": config.all()})


@api.route("/settings/validate-dir", methods=["GET", "POST"])
def validate_dir():
    data = body()
    path = (data.get("path") or "").strip()
    if not path:
        return fail("请填写目录")
    path = os.path.abspath(os.path.expanduser(path))
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".anime-downloader-write-test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
    except Exception as exc:  # noqa: BLE001
        return fail("目录不可写：%s" % exc)
    return ok({"path": path, "free": _disk_free(path)})


@api.post("/settings/clean-temp")
def clean_temp():
    """清理临时分片。

    注意：正在下载的任务也在用这个目录。之前直接 rmtree，
    会把活跃任务的目录一起删掉，导致它接下来每个分片都报
    「No such file or directory」而失败。这里改成两步：
    先把待清理项**原子改名**到隔离目录（改名对活跃任务无害，
    它下一片会自己重建目录），再统一删除。
    """
    import shutil
    import time as _time

    temp_dir = config.temp_dir()
    removed = 0
    size = 0
    if not os.path.isdir(temp_dir):
        return ok({"removed": 0, "freed": 0, "temp_dir": temp_dir})

    staging = os.path.join(temp_dir, "_cleanup_%d" % int(_time.time()))
    moved = []
    for name in os.listdir(temp_dir):
        if name.startswith("_cleanup_"):
            continue
        target = os.path.join(temp_dir, name)
        try:
            if os.path.isdir(target):
                for root, _dirs, files in os.walk(target):
                    for filename in files:
                        try:
                            size += os.path.getsize(os.path.join(root, filename))
                        except OSError:
                            pass
            else:
                size += os.path.getsize(target)
            os.makedirs(staging, exist_ok=True)
            os.replace(target, os.path.join(staging, name))
            moved.append(name)
        except OSError as exc:
            logger.warning("清理临时文件失败 %s: %s", target, exc)

    for name in moved:
        try:
            path = os.path.join(staging, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            removed += 1
        except OSError:
            pass
    shutil.rmtree(staging, ignore_errors=True)

    # 顺手清掉历史遗留的隔离目录
    for name in os.listdir(temp_dir):
        if name.startswith("_cleanup_"):
            shutil.rmtree(os.path.join(temp_dir, name), ignore_errors=True)

    logger.info("清理临时分片：移除 %d 项，释放 %.1f MB", removed, size / 1048576)
    return ok({"removed": removed, "freed": size, "temp_dir": temp_dir})


@api.post("/settings/reset-tasks")
def reset_tasks():
    count = db.purge_unfinished_tasks()
    return ok({"removed": count})


@api.route("/disclaimer", methods=["GET", "POST"])
def disclaimer():
    """免责声明：首次使用必须同意。

    GET  返回是否已同意、条款版本；
    POST {"accept": true} 记录同意。

    条款版本号提高后会要求重新确认（改条款内容时才需要动它）。
    """
    if request.method == "POST":
        data = body()
        if not data.get("accept"):
            return fail("需要同意条款才能使用")
        config.update({"disclaimer_accepted": True,
                       "disclaimer_version": DISCLAIMER_VERSION})
        logger.info("用户已同意免责声明（版本 %s）", DISCLAIMER_VERSION)
        return ok({"accepted": True, "version": DISCLAIMER_VERSION})
    accepted = bool(config.get("disclaimer_accepted"))
    version = int(config.get("disclaimer_version") or 0)
    return ok({"accepted": accepted and version >= DISCLAIMER_VERSION,
               "version": version,
               "current_version": DISCLAIMER_VERSION})


@api.route("/settings/factory-reset", methods=["GET", "POST"])
def factory_reset():
    """清除用户配置，恢复到出厂状态。

    清掉：设置项、任务记录、下载历史、检索历史、自定义背景图与预览图缓存、
          带广告标记（源本身保留，只是恢复为默认的启用/顺序）。
    保留：已下载的视频文件（绝不删你的片子）、动漫源列表本身。

    必须显式传 confirm="RESET" 才会执行，避免误点。
    """
    import shutil

    data = body()
    if (data.get("confirm") or "").strip().upper() != "RESET":
        return fail('这是危险操作，需要确认（confirm=RESET）')

    removed = {}

    # 1) 设置恢复默认（包含外观、下载目录等全部回到默认值）
    config.reset()
    download_manager.apply_settings()
    removed["settings"] = True

    # 2) 任务 + 历史 + 检索历史
    #    注意顺序：reset_all() 会先停下载再清库，避免正在跑的任务把记录写回来
    removed["tasks"] = download_manager.reset_all()
    removed["history"] = db.purge_history()
    removed["search_history"] = db.purge_search_history()

    # 3) 源：恢复成默认的启用状态与顺序（但不删除用户自己加的源）
    try:
        for record in db.list_sources():
            db.update_source(record["id"], {"ad_flag": False, "ad_kind": ""})
        source_manager.demote_ad_sources()
        removed["sources_reset"] = len(db.list_sources())
    except Exception as exc:  # noqa: BLE001
        logger.warning("重置源状态失败：%s", exc)
        removed["sources_reset"] = 0

    # 4) 背景图、预览图缓存，以及下载残留的临时分片
    cleanup_dirs = (
        (os.path.join(DATA_DIR, "backgrounds"), "backgrounds"),
        (os.path.join(DATA_DIR, "thumbs"), "thumbnails"),
        (config.temp_dir(), "temp_segments"),
    )
    for folder, label in cleanup_dirs:
        count = 0
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                target = os.path.join(folder, name)
                try:
                    if os.path.isdir(target):
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        os.remove(target)
                    count += 1
                except OSError:
                    pass
        removed[label] = count

    # 5) 日志也清一遍，免得里面还留着旧的关键字
    try:
        clear_logs()
    except Exception:  # noqa: BLE001
        pass

    logger.info("已清除用户配置：%s", removed)
    return ok({"removed": removed, "settings": config.all()})
