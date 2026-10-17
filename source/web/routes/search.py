# -*- coding: utf-8 -*-
"""检索链路：聚合检索、检索历史、剧集列表、播放地址解析

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

from flask import request
from core.config import config
from core.database import db
from sources.base import SourceError
from sources.manager import source_manager

# 跨模块引用
from ._base import api, body, fail, logger, ok

# ------------------------------------------------------------------ 检索
@api.route("/search", methods=["GET", "POST"])
def search():
    """检索接口。

    POST：前端正常调用（JSON body）。
    GET ：兼容直接在地址栏/脚本里用 `?keyword=xxx` 查询，避免只支持 POST 时返回 405。
    """
    data = body()
    keyword = (data.get("keyword") or request.args.get("keyword") or "").strip()
    source_ids = data.get("source_ids")
    if isinstance(source_ids, str):
        source_ids = [int(part) for part in source_ids.split(",") if part.strip().isdigit()]
    page = int(data.get("page") or request.args.get("page") or 1)
    mode = (data.get("mode") or request.args.get("mode") or "aggregate").lower()

    # 诊断日志：请求体丢失时能一眼看出来（前端用 GET 就不会有这个问题）
    if not keyword:
        raw = request.get_data(cache=True, as_text=True) or ""
        logger.warning(
            "检索请求缺少关键字 | method=%s ctype=%s args=%s form=%s body长度=%d body前120字=%r",
            request.method, request.content_type, dict(request.args),
            list(request.form.keys()), len(raw), raw[:120])
        return fail("请输入检索关键字（POST JSON 的 keyword，或 URL 参数 ?keyword=）")
    try:
        db.add_search_history(keyword)
        if mode == "single" and source_ids:
            result = source_manager.search_one(int(source_ids[0]), keyword, page)
            group = result
            payload = {"keyword": keyword, "total": result.get("count", 0),
                       "elapsed": result.get("elapsed", 0), "groups": [group],
                       "errors": [] if result.get("ok") else
                       [{"source": result.get("source"), "error": result.get("error")}],
                       "source_count": 1}
        else:
            payload = source_manager.aggregate(keyword, source_ids=source_ids, page=page)
    except SourceError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("检索失败：%s", exc, exc_info=True)
        return fail("检索失败：%s" % exc, 500)

    return ok(payload)


@api.get("/search/history")
def search_history():
    return ok(db.list_search_history(int(config.get("search_history_limit", 30) or 30)))


@api.delete("/search/history")
def clear_search_history():
    db.clear_search_history()
    return ok({"cleared": True})


@api.route("/episodes", methods=["GET", "POST"])
def episodes():
    data = body()
    source_id = data.get("source_id")
    anime_url = data.get("anime_url") or data.get("url")
    anime_name = data.get("anime_name") or ""
    if not source_id or not anime_url:
        raw = request.get_data(cache=True, as_text=True) or ""
        logger.warning(
            "取剧集缺少参数 | method=%s ctype=%s args=%s form=%s json_body=%r 原始body长度=%d",
            request.method, request.content_type, dict(request.args),
            list(request.form.keys()), raw[:200], len(raw))
        return fail("缺少源或动漫地址")
    try:
        result = source_manager.episodes(int(source_id), anime_url, anime_name)
    except SourceError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("获取剧集失败：%s", exc, exc_info=True)
        return fail("获取剧集失败：%s" % exc, 500)

    result["anime_name"] = anime_name
    return ok(result)


@api.route("/video/resolve", methods=["GET", "POST"])
def resolve_video():
    data = body()
    source_id = data.get("source_id")
    episode_url = data.get("episode_url") or data.get("url")
    if not source_id or not episode_url:
        return fail("缺少源或剧集地址")
    try:
        url = source_manager.resolve_video_url(int(source_id), episode_url,
                                               data.get("anime_url") or "",
                                               data.get("episode_name") or "")
        options = source_manager.playlist_options(int(source_id), episode_url,
                                                  data.get("anime_url") or "")
        return ok({"url": url, "is_hls": ".m3u8" in (url or "").lower(), "options": options})
    except SourceError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail("解析视频地址失败：%s" % exc, 500)
