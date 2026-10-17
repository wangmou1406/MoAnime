# -*- coding: utf-8 -*-
"""源管理：增删改查、排序、测活/测源、广告标记、导入导出、源类型

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import json
import time
from flask import Response, request
from core.config import VERSION, config
from core.database import db
from sources import auto_detect
from sources.base import SourceError
from sources.manager import source_manager

# 跨模块引用
from ._base import api, body, fail, logger, ok
from .search import episodes

# ------------------------------------------------------------------ 源管理
@api.get("/sources")
def list_sources():
    return ok({"sources": source_manager.list_sources(),
               "types": source_manager.types()})


@api.route("/sources", methods=["GET", "POST"])
def create_source():
    data = body()
    try:
        record = source_manager.create_source(data)
    except SourceError as exc:
        return fail(str(exc))
    return ok(record)


@api.get("/sources/<int:source_id>")
def get_source(source_id: int):
    record = source_manager.get_record(source_id=source_id)
    if not record:
        return fail("源不存在", 404)
    return ok(record)


@api.put("/sources/<int:source_id>")
@api.route("/sources/<int:source_id>", methods=["GET", "POST"])
def update_source(source_id: int):
    data = body()
    try:
        record = source_manager.update_source(source_id, data)
    except SourceError as exc:
        return fail(str(exc))
    return ok(record)


@api.delete("/sources/<int:source_id>")
def delete_source(source_id: int):
    record = source_manager.get_record(source_id=source_id)
    if not record:
        return fail("源不存在", 404)
    source_manager.delete_source(source_id)
    return ok({"deleted": source_id})


@api.route("/sources/<int:source_id>/ad-flag", methods=["GET", "POST"])
def toggle_ad_flag(source_id: int):
    """标记/取消「该源的视频里插播广告」。标记后会排到列表最后。"""
    record = source_manager.get_record(source_id=source_id)
    if not record:
        return fail("源不存在", 404)
    data = body()
    flagged = data.get("ad_flag")
    if flagged is None:
        flagged = not record.get("ad_flag")
    kind = (data.get("ad_kind") or "").strip()
    record = source_manager.set_ad_flag(source_id, bool(flagged), kind)
    if record and record.get("ad_flag"):
        source_manager.demote_ad_sources()
    logger.info("源 %s 广告标记 = %s", record.get("name") if record else source_id, bool(flagged))
    return ok({"source": record, "sources": source_manager.list_sources()})


@api.route("/sources/ad-flags", methods=["GET", "POST"])
def bulk_ad_flags():
    """批量设置广告标记（文字广告体检用：一次标记多个源）。

    请求体：{"items": [{"id": 110, "ad_flag": true, "ad_kind": "text"}, ...]}
    """
    data = body()
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return fail("缺少 items")
    changed = 0
    for item in items:
        try:
            sid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        record = source_manager.get_record(source_id=sid)
        if not record:
            continue
        kind = (item.get("ad_kind") or "").strip()
        flag = item.get("ad_flag")
        source_manager.set_ad_flag(sid, bool(flag) if flag is not None else True, kind)
        changed += 1
    if changed:
        source_manager.demote_ad_sources()
    logger.info("批量广告标记：%d 个源", changed)
    return ok({"changed": changed, "sources": source_manager.list_sources()})


@api.route("/search/stream", methods=["GET", "POST"])
def search_stream():
    """流式聚合检索：**每个源一返回就推一行**，前端作业面板可滚动显示进度。

    与 /api/search 的区别只在传输方式：结果内容一致，
    最后一行 done 里带完整的聚合 payload（前端直接用，不用再请求一次）。
    """
    data = body()
    keyword = (data.get("keyword") or request.args.get("keyword") or "").strip()
    source_ids = data.get("source_ids")
    if isinstance(source_ids, str):
        source_ids = [int(p) for p in source_ids.split(",") if p.strip().isdigit()]
    if source_ids:
        source_ids = [int(i) for i in source_ids]
    page = int(data.get("page") or request.args.get("page") or 1)

    if not keyword:
        return fail("请输入检索关键字")
    try:
        db.add_search_history(keyword)
    except Exception:  # noqa: BLE001
        pass

    def generate():
        import json as _json

        def emit(obj):
            return _json.dumps(obj, ensure_ascii=False) + "\n"

        try:
            records = source_manager.list_sources(include_hidden=False)
            if source_ids:
                wanted = set(source_ids)
                records = [r for r in records if r["id"] in wanted]
            records = [r for r in records if r.get("enabled")]
            if not records:
                yield emit({"type": "error",
                            "error": "没有启用任何动漫源，请先在“动漫源管理”里勾选要搜索的源"})
                return

            priority = {r["id"]: i for i, r in enumerate(records)}
            total = len(records)
            yield emit({"type": "start", "total": total, "keyword": keyword})

            from concurrent.futures import ThreadPoolExecutor, as_completed

            workers = max(1, min(int(config.get("search_concurrency", 5) or 5), total))
            results = []
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(source_manager.search_one, r["id"], keyword, page): r
                           for r in records}
                for future in as_completed(futures):
                    record = futures[future]
                    try:
                        item = future.result()
                    except Exception as exc:  # noqa: BLE001
                        item = {"source": record["name"], "source_id": record["id"],
                                "ok": False, "error": str(exc), "results": [],
                                "count": 0, "elapsed": 0}
                    results.append(item)
                    done += 1
                    # 注意：search_one 把 NoResultError 记为 ok=False + empty=True。
                    # 从「源是否健康」看它其实是好的（能连上，只是没这个片名），
                    # 所以这里把 empty 归一成 ok=True，免得把好源统计成「失败源」。
                    empty = bool(item.get("empty"))
                    ok_source = bool(item.get("ok")) or empty
                    yield emit({"type": "source", "done": done, "total": total,
                                "source": item.get("source"),
                                "count": item.get("count", 0),
                                "elapsed": item.get("elapsed", 0),
                                "ok": ok_source,
                                "empty": empty,
                                "error": ("" if empty else
                                          (str(item.get("error"))[:120]
                                           if item.get("error") else ""))})

            results.sort(key=lambda it: priority.get(it.get("source_id"), 999))
            total_hits = sum(it.get("count", 0) for it in results)
            # 失败源只算真正的异常；「无匹配」不是失败
            errors = [{"source": it["source"], "error": it.get("error")}
                      for it in results
                      if not it.get("ok") and not it.get("empty")]
            groups = results
            if config.get("merge_duplicates", False):
                groups = source_manager._merge_groups(groups)  # noqa: SLF001

            yield emit({"type": "done", "payload": {
                "keyword": keyword,
                "total": total_hits,
                "groups": groups,
                "errors": errors,
                "source_count": len(records),
            }})
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式检索失败")
            yield emit({"type": "error", "error": str(exc)})

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@api.route("/sources/sort", methods=["GET", "POST"])
def sort_sources():
    """一键排序源列表。

    mode: smart 智能（质量分）/ health 可用优先 / name 名称 / enabled 启用优先
          / hits 命中多优先 / ad 广告沉顶 / manual 恢复默认顺序
    """
    data = body()
    mode = (data.get("mode") or request.args.get("mode") or "smart").strip()
    allowed = {"smart", "health", "name", "enabled", "hits", "ad", "manual"}
    if mode not in allowed:
        return fail("不支持的排序方式：%s" % mode, 400)
    return ok(source_manager.sort_by(mode))


@api.route("/sources/probe-quality", methods=["GET", "POST"])
def probe_source_quality():
    """质量采样：并发给每个源测活并抽样检索，把耗时/命中数写入库。

    返回 NDJSON 流（每行一个 JSON），前端可实时显示进度。
    智能排序的「响应速度」「内容量」两项就靠这次采样获得。
    """
    import json as _json

    data = body()
    keyword = (data.get("keyword") or request.args.get("keyword") or "海贼王").strip()
    source_ids = data.get("source_ids") or None
    if source_ids:
        source_ids = [int(i) for i in source_ids]

    def generate():
        queue = []

        def progress(done, total, row):
            queue.append({"type": "row", "index": done, "total": total, "row": row})

        import threading
        result_holder = {}

        def run():
            try:
                result_holder["data"] = source_manager.probe_quality(
                    keyword, progress=progress, source_ids=source_ids)
            except Exception as exc:  # noqa: BLE001
                result_holder["error"] = str(exc)
            finally:
                queue.append({"type": "__end__"})

        worker = threading.Thread(target=run, daemon=True)
        worker.start()

        first = True
        while True:
            if queue:
                item = queue.pop(0)
                if item.get("type") == "__end__":
                    break
                if first:
                    yield _json.dumps({"type": "start", "total": item.get("total"),
                                       "keyword": keyword}, ensure_ascii=False) + "\n"
                    first = False
                yield _json.dumps(item, ensure_ascii=False) + "\n"
            else:
                time.sleep(0.15)

        if "error" in result_holder:
            yield _json.dumps({"type": "error", "error": result_holder["error"]},
                              ensure_ascii=False) + "\n"
        else:
            result = result_holder.get("data") or {}
            yield _json.dumps({"type": "done", "total": result.get("total", 0),
                               "keyword": keyword, "rows": result.get("rows", []),
                               "sources": result.get("sources", [])},
                              ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-store"})


@api.route("/sources/demote-ads", methods=["GET", "POST"])
def demote_ads():
    """把所有标了广告的源一键移到最后。"""
    result = source_manager.demote_ad_sources()
    logger.info("已把 %d 个带广告的源移到最后", result["moved"])
    return ok(result)


@api.route("/sources/reorder", methods=["GET", "POST"])
def reorder_sources():
    data = body()
    items = data.get("items")
    if items is None:
        ids = data.get("ids") or []
        return ok({"sources": source_manager.reorder([int(i) for i in ids])})
    return ok({"sources": source_manager.bulk_update(items)})


@api.route("/sources/<int:source_id>/toggle", methods=["GET", "POST"])
def toggle_source(source_id: int):
    data = body()
    record = source_manager.get_record(source_id=source_id)
    if not record:
        return fail("源不存在", 404)
    enabled = data.get("enabled")
    if enabled is None:
        enabled = not record.get("enabled")
    source_manager.set_enabled(source_id, bool(enabled))
    return ok(source_manager.get_record(source_id=source_id))


@api.route("/sources/<int:source_id>/test", methods=["GET", "POST"])
def test_source(source_id: int):
    data = body()
    keyword = data.get("keyword")
    try:
        report = source_manager.test_source(source_id, keyword)
    except SourceError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("测试源失败：%s", exc, exc_info=True)
        return fail("测试失败：%s" % exc, 500)
    return ok(report)


@api.route("/sources/<int:source_id>/check", methods=["GET", "POST"])
def check_source(source_id: int):
    return ok(source_manager.check_source(source_id))


@api.get("/sources/export")
def export_sources():
    include_builtin = request.args.get("include_builtin") in ("1", "true", "yes")
    data = source_manager.export_sources(include_builtin=include_builtin)
    if request.args.get("download"):
        payload = json.dumps({"version": VERSION, "sources": data}, ensure_ascii=False, indent=2)
        return Response(payload, mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=anime-downloader-sources.json"})
    return ok({"sources": data})


@api.post("/sources/import")
def import_sources():
    data = body()
    items = data.get("sources")
    if items is None and isinstance(data.get("payload"), (list, dict)):
        items = data["payload"]
        items = items.get("sources") if isinstance(items, dict) else items
    if not isinstance(items, list):
        return fail("导入内容格式不正确，应为 {\"sources\": [...]}")
    result = source_manager.import_sources(items, overwrite=bool(data.get("overwrite", True)))
    return ok(result)


@api.route("/sources/detect", methods=["GET", "POST"])
def detect_source():
    data = body()
    url = (data.get("url") or "").strip()
    if not url:
        return fail("请输入站点网址")
    if not url.startswith("http"):
        url = "https://" + url
    if not config.get("auto_detect_sources", True):
        return fail("自动探测已在设置中关闭")
    try:
        report = auto_detect.detect(url, data.get("page_url") or "")
    except Exception as exc:  # noqa: BLE001
        logger.error("自动探测失败：%s", exc, exc_info=True)
        return fail("自动探测失败：%s" % exc, 500)
    return ok(report)


@api.route("/sources/preview", methods=["GET", "POST"])
def preview_source():
    """用未保存的草稿配置直接试跑：检索 / 剧集 / 视频地址。"""
    from sources.base import DEFAULT_TEST_KEYWORD, create_plugin
    data = body()
    draft = data.get("source") or {}
    step = (data.get("step") or "search").lower()
    keyword = data.get("keyword") or (draft.get("config") or {}).get("test_keyword") \
        or DEFAULT_TEST_KEYWORD
    record = {
        "id": -1,
        "name": draft.get("name") or "未保存的源",
        "type": draft.get("type") or "generic",
        "base_url": draft.get("base_url") or (draft.get("config") or {}).get("base_url") or "",
        "config": draft.get("config") or {},
    }
    try:
        plugin = create_plugin(record, manager=source_manager)
    except SourceError as exc:
        return fail(str(exc))
    start = time.time()
    try:
        if step == "search":
            results = plugin.search(keyword, 1)
            return ok({"step": "search", "count": len(results), "results": results[:20],
                       "elapsed": round(time.time() - start, 2)})
        if step == "episodes":
            anime_url = data.get("anime_url")
            if not anime_url:
                return fail("请先填写动漫详情页地址")
            episodes = plugin.episodes(anime_url, data.get("anime_name") or "")
            return ok({"step": "episodes", "count": len(episodes), "episodes": episodes[:60],
                       "elapsed": round(time.time() - start, 2)})
        if step == "video":
            episode_url = data.get("episode_url")
            if not episode_url:
                return fail("请先填写剧集地址")
            url = plugin.resolve_video_url(episode_url, data.get("anime_url") or "",
                                           data.get("episode_name") or "")
            return ok({"step": "video", "url": url, "is_hls": ".m3u8" in (url or "").lower(),
                       "elapsed": round(time.time() - start, 2)})
    except SourceError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("源预览失败：%s", exc, exc_info=True)
        return fail("预览失败：%s" % exc, 500)
    return fail("未知步骤：%s" % step)


@api.get("/source-types")
def source_types():
    return ok({"types": source_manager.types()})
