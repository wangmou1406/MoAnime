# -*- coding: utf-8 -*-
"""下载任务：提交下载、批量下载、任务列表与暂停/继续/重试/删除

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

from flask import request
from core.task_manager import download_manager

# 跨模块引用
from ._base import api, body, fail, logger, ok

# ------------------------------------------------------------------ 下载
@api.post("/download/episode")
def download_episode():
    data = body()
    try:
        result = download_manager.submit(
            anime_name=data.get("anime_name"),
            episode_name=data.get("episode_name"),
            source_id=data.get("source_id"),
            source_name=data.get("source") or "",
            episode_url=data.get("episode_url") or data.get("url") or "",
            anime_url=data.get("anime_url") or "",
            video_url=data.get("video_url") or "",
            priority=int(data.get("priority") or 0),
        )
        return ok(result)
    except ValueError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("创建下载任务失败：%s", exc, exc_info=True)
        return fail("创建下载任务失败：%s" % exc, 500)


@api.post("/download/batch")
def download_batch():
    data = body()
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return fail("没有可下载的剧集")
    result = download_manager.submit_batch(
        items,
        priority=int(data.get("priority") or 0),
        # 默认不自动换源：用户选了哪个源就只从哪个源下
        auto_fallback=bool(data.get("auto_fallback", False)),
    )
    return ok(result)


@api.get("/tasks")
def tasks():
    status = request.args.get("status")
    return ok({"tasks": download_manager.list_tasks(status=status),
               "stats": download_manager.statistics()})


@api.route("/tasks/<task_id>/<action>", methods=["GET", "POST"])
def task_action(task_id: str, action: str):
    try:
        if action == "pause":
            return ok({"done": download_manager.pause(task_id)})
        if action == "resume":
            return ok({"done": download_manager.resume(task_id)})
        if action == "cancel":
            return ok({"done": download_manager.cancel(task_id)})
        if action == "retry":
            return ok(download_manager.retry(task_id))
        if action == "delete":
            delete_file = bool(body().get("delete_file"))
            return ok({"done": download_manager.delete(task_id, delete_file=delete_file)})
    except ValueError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail("操作失败：%s" % exc, 500)
    return fail("未知操作：%s" % action, 404)


@api.route("/tasks/bulk", methods=["GET", "POST"])
def tasks_bulk():
    data = body()
    action = (data.get("action") or "").lower()
    ids = data.get("ids") or []
    if action == "pause_all":
        return ok({"count": download_manager.pause_all()})
    if action == "resume_all":
        return ok({"count": download_manager.resume_all()})
    if action == "clear":
        return ok({"count": download_manager.clear()})
    if action == "clear_pending":
        return ok({"count": download_manager.clear_pending()})
    if action == "clear_completed":
        return ok({"count": download_manager.clear(("completed",))})
    if action == "clear_failed":
        return ok({"count": download_manager.clear(("failed", "canceled"))})
    if action == "clean_empty_dirs":
        return ok({"count": download_manager.clean_empty_dirs()})
    if action == "clean_orphans":
        return ok(download_manager.clean_orphan_files())
    if action == "clear_all":
        # 全部删除任务记录（进行中的先取消）；delete_file=True 时连文件一起删
        return ok({"count": download_manager.clear_all(
            delete_file=bool(data.get("delete_file")))})
    if action == "retry":
        # 批量重试：只有 failed / canceled 能重试，其余会被 retry() 拒绝。
        # 逐条容错，最后给出「成功几条、跳过几条」，免得一个坏任务拖垮整批。
        done_count = 0
        skipped = []
        for task_id in ids:
            try:
                download_manager.retry(task_id)
                done_count += 1
            except ValueError as exc:
                skipped.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                skipped.append(str(exc)[:80])
        return ok({"count": done_count, "skipped": len(skipped),
                   "errors": list(dict.fromkeys(skipped))[:5]})
    if action == "delete":
        count = 0
        for task_id in ids:
            download_manager.delete(task_id, delete_file=bool(data.get("delete_file")))
            count += 1
        return ok({"count": count})
    return fail("未知批量操作")
