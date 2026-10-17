# -*- coding: utf-8 -*-
"""源管理器：把数据库里的源记录变成可调用的插件实例，并负责聚合检索。"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from core.config import config
from core.database import db
from core.logging_setup import get_logger
from core.utils import dedupe

from . import base as source_base
from .base import (DEFAULT_TEST_KEYWORD, SourceError, SourcePlugin,
                   create_plugin, list_types)

logger = get_logger("moanime.sources")


class SourceManager:
    """负责源的增删改查、排序、实例缓存与聚合检索。"""

    def __init__(self):
        self._cache = {}
        self._lock = threading.RLock()
        #: 最近一次 resolve_video_url 的附带信息（如浏览器嗅探的 Referer/Cookie）
        self.last_resolve_meta = {}

    # ------------------------------------------------------------ 查询
    def list_sources(self, include_hidden: bool = True) -> List[dict]:
        records = db.list_sources(include_hidden=include_hidden)
        for record in records:
            record["type_label"] = self._type_label(record.get("type"))
            # 前端随时能拿到质量分/健康状态，用于展示与「智能排序」预览
            record["health"] = self.health_of(record)
            record["quality"] = self.quality_score(record)
        return records

    def get_record(self, source_id: int = None, name: str = None):
        return db.get_source(source_id=source_id, name=name)

    def get_plugin(self, source_id: int = None, name: str = None):
        record = self.get_record(source_id=source_id, name=name)
        if not record:
            return None
        return self.build_plugin(record)

    def build_plugin(self, record: dict):
        """按记录构造（并缓存）插件实例。"""
        key = (record.get("id"), record.get("updated_at"))
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        plugin = create_plugin(record, manager=self)
        with self._lock:
            stale = [k for k in self._cache if k[0] == record.get("id")]
            for item in stale:
                self._cache.pop(item, None)
            self._cache[key] = plugin
        return plugin

    def enabled_plugins(self) -> List:
        """按用户拖拽顺序（sort_order）返回启用的源（勾选 = enabled）。"""
        plugins = []
        for record in self.list_sources(include_hidden=False):
            if not record.get("enabled"):
                continue
            try:
                plugins.append(self.build_plugin(record))
            except SourceError as exc:
                logger.error("加载源失败 %s: %s", record.get("name"), exc)
        return plugins

    # ------------------------------------------------------------ 增删改
    def create_source(self, payload: dict) -> dict:
        name = (payload.get("name") or "").strip()
        if not name:
            raise SourceError("源名称不能为空")
        if db.get_source(name=name):
            raise SourceError("已存在同名动漫源：%s" % name)
        type_name = payload.get("type") or "generic"
        if source_base.get_plugin_class(type_name) is None:
            raise SourceError("未知的源类型：%s" % type_name)
        config_data = payload.get("config") or {}
        source_id = db.add_source({
            "name": name,
            "type": type_name,
            "base_url": payload.get("base_url") or config_data.get("base_url") or "",
            "config": config_data,
            "enabled": bool(payload.get("enabled", True)),
            "note": payload.get("note") or "",
        })
        return db.get_source(source_id=source_id)

    def update_source(self, source_id: int, payload: dict) -> dict:
        record = db.get_source(source_id=source_id)
        if not record:
            raise SourceError("源不存在")
        new_name = (payload.get("name") or record["name"]).strip()
        duplicate = db.get_source(name=new_name)
        if duplicate and duplicate["id"] != source_id:
            raise SourceError("已存在同名动漫源：%s" % new_name)
        data = {
            "name": new_name,
            "note": payload.get("note", record.get("note")),
        }
        if "type" in payload:
            data["type"] = payload["type"]
        if "config" in payload and payload["config"] is not None:
            data["config"] = payload["config"]
            data["base_url"] = payload.get("base_url") or payload["config"].get("base_url") or record["base_url"]
        elif "base_url" in payload:
            data["base_url"] = payload["base_url"]
        if "enabled" in payload:
            data["enabled"] = bool(payload["enabled"])
        if "hidden" in payload:
            data["hidden"] = bool(payload["hidden"])
        if "ad_flag" in payload:
            data["ad_flag"] = bool(payload["ad_flag"])
        db.update_source(source_id, data)
        return db.get_source(source_id=source_id)

    def set_ad_flag(self, source_id: int, flagged: bool, ad_kind: str = "") -> dict:
        """标记/取消标记「该源有问题」，标记后会被排到最后。

        ad_kind 用于区分原因（``ad``=疑似插广告，``incomplete``=疑似残缺），
        会写进 note 便于在列表里看清原因。
        """
        changes = {"ad_flag": bool(flagged)}
        if ad_kind:
            changes["ad_kind"] = ad_kind
        db.update_source(source_id, changes)
        return db.get_source(source_id=source_id)

    def demote_ad_sources(self) -> dict:
        """把所有标了广告的源移到最后（在各自组内保持相对顺序）。"""
        records = self.list_sources()
        clean = [r["id"] for r in records if not r.get("ad_flag")]
        flagged = [r["id"] for r in records if r.get("ad_flag")]
        if not flagged:
            return {"moved": 0, "sources": self.list_sources()}
        db.reorder_sources(clean + flagged)
        return {"moved": len(flagged), "sources": self.list_sources()}

    # ------------------------------------------------------------ 排序
    @staticmethod
    def health_of(record: dict) -> str:
        """测活状态：error（有报错）/ ok（测过且正常）/ untested（没测过）。"""
        if record.get("last_error"):
            return "error"
        return "ok" if record.get("ok_count") else "untested"

    @classmethod
    def quality_score(cls, record: dict) -> float:
        """0~100 的质量分，用于「智能排序」。

        组成（各自独立，互不掩盖）：
          - 可用性 40 分：测活状态 + 置信修正后的成功率。
            用 (ok+1)/(ok+fail+2) 而不是 ok/(ok+fail)，避免"只测过 1 次成功"
            就拿到满分，样本越多越接近真实值。
          - 内容量 25 分：累计检索命中数，对数缩放（100 条≈满分，避免大站碾压一切）。
          - 响应速度 20 分：最近一次测活耗时，≤300ms 满分，≥5s 归零。
          - 干净度 15 分：标了广告/残缺的源直接归零。
        """
        if record.get("ad_flag"):
            return 0.0

        ok = int(record.get("ok_count") or 0)
        fail = int(record.get("fail_count") or 0)
        rate = (ok + 1.0) / (ok + fail + 2.0)
        if fail and not ok:
            rate = 0.0                      # 只有失败记录，别给虚假的 0.33

        score = 40.0 * rate

        hits = int(record.get("hit_count") or 0)
        if hits > 0:
            import math
            score += 25.0 * min(1.0, math.log10(hits + 1) / 2.0)   # 100 条命中→满分

        ms = int(record.get("last_ms") or 0)
        if ms > 0:
            if ms <= 300:
                score += 20.0
            elif ms < 5000:
                score += 20.0 * (5000 - ms) / 4700.0

        if not record.get("last_error"):
            score += 15.0
        return round(min(100.0, score), 1)

    def sort_by(self, mode: str = "smart") -> dict:
        """按指定方式重排源列表，并把顺序持久化。

        mode: smart（质量分）/ health（可用优先）/ name（名称）/ enabled（启用优先）
              / hits（命中多优先）/ ad（广告沉底）/ manual（恢复为内置/创建顺序）
        """
        records = self.list_sources()
        if not records:
            return {"mode": mode, "count": 0, "sources": []}

        for r in records:
            r["quality"] = self.quality_score(r)
            r["health"] = self.health_of(r)

        created = sorted(records, key=lambda r: (r.get("created_at") or "", r["id"]))
        if mode == "manual":
            # 恢复为内置源的原始顺序：先 builtin 再自定义，各自按创建时间
            ordered = sorted(records, key=lambda r: (not r.get("builtin"), r["id"]))
        elif mode == "name":
            ordered = sorted(created, key=lambda r: (
                (r.get("name") or "").casefold(), r["id"]))
        elif mode == "enabled":
            # 广告源无论如何沉底；其余启用的在前
            ordered = sorted(created, key=lambda r: (
                bool(r.get("ad_flag")), not bool(r.get("enabled")), r["id"]))
        elif mode == "health":
            rank = {"ok": 0, "untested": 1, "error": 2}
            ordered = sorted(created, key=lambda r: (
                bool(r.get("ad_flag")), rank.get(r["health"], 3), -r["quality"], r["id"]))
        elif mode == "hits":
            ordered = sorted(created, key=lambda r: (
                bool(r.get("ad_flag")), -int(r.get("hit_count") or 0), r["id"]))
        elif mode == "ad":
            ordered = sorted(created, key=lambda r: (
                bool(r.get("ad_flag")), r["id"]))
        else:  # smart
            ordered = sorted(created, key=lambda r: (
                -r["quality"], bool(r.get("ad_flag")), r["id"]))

        db.reorder_sources([r["id"] for r in ordered])
        db.set_sort_mode(mode)
        return {
            "mode": mode,
            "count": len(ordered),
            "quality": {r["name"]: r["quality"] for r in records},
            "sources": self.list_sources(),
        }

    def delete_source(self, source_id: int) -> None:
        db.delete_source(source_id)

    def set_enabled(self, source_id: int, enabled: bool) -> None:
        db.update_source(source_id, {"enabled": bool(enabled)})

    def reorder(self, ordered_ids) -> List[dict]:
        db.reorder_sources(ordered_ids)
        return self.list_sources()

    def bulk_update(self, items: List[dict]) -> List[dict]:
        """按前端拖拽结果一次性保存顺序与勾选状态。"""
        ordered_ids = []
        for item in items or []:
            source_id = item.get("id")
            if source_id is None:
                continue
            ordered_ids.append(int(source_id))
            changes = {}
            if "enabled" in item:
                changes["enabled"] = bool(item["enabled"])
            if "hidden" in item:
                changes["hidden"] = bool(item["hidden"])
            if "ad_flag" in item:
                changes["ad_flag"] = bool(item["ad_flag"])
            if item.get("ad_kind") is not None:
                changes["ad_kind"] = str(item["ad_kind"])[:16]
            if item.get("note") is not None:
                changes["note"] = str(item["note"])[:200]
            if changes:
                db.update_source(int(source_id), changes)
        if ordered_ids:
            db.reorder_sources(ordered_ids)
        return self.list_sources()

    def export_sources(self, include_builtin: bool = False) -> List[dict]:
        out = []
        for record in self.list_sources():
            if record.get("builtin") and not include_builtin:
                continue
            out.append({
                "name": record["name"],
                "type": record["type"],
                "base_url": record.get("base_url") or "",
                "note": record.get("note") or "",
                "enabled": bool(record.get("enabled")),
                "config": record.get("config") or {},
            })
        return out

    def import_sources(self, payload: List[dict], overwrite: bool = True) -> dict:
        created, updated, failed = 0, 0, []
        for item in payload or []:
            try:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                existing = db.get_source(name=name)
                if existing and not overwrite:
                    continue
                if existing:
                    self.update_source(existing["id"], {
                        "name": name,
                        "type": item.get("type") or existing["type"],
                        "base_url": item.get("base_url") or existing.get("base_url"),
                        "config": item.get("config") or existing.get("config"),
                        "note": item.get("note", existing.get("note")),
                        "enabled": item.get("enabled", existing.get("enabled")),
                    })
                    updated += 1
                else:
                    self.create_source(item)
                    created += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"name": item.get("name"), "error": str(exc)})
        return {"created": created, "updated": updated, "failed": failed}

    # ------------------------------------------------------------ 搜索
    def search_one(self, source_id: int, keyword: str, page: int = 1) -> dict:
        record = db.get_source(source_id=source_id)
        if not record:
            raise SourceError("源不存在")
        plugin = self.build_plugin(record)
        start = time.time()
        try:
            results = plugin.search(keyword, page)
            elapsed_ms = int((time.time() - start) * 1000)
            # 检索成功说明源可用：记一次成功（也用于质量分的响应速度）
            db.record_source_result(source_id, True, elapsed_ms=elapsed_ms)
            if results:
                db.record_source_hits(source_id, len(results))
            return {"source": plugin.name, "source_id": source_id,
                    "type": plugin.type_name, "elapsed": round(time.time() - start, 2),
                    "count": len(results), "results": results, "ok": True,
                    "empty": not results}
        except Exception as exc:  # noqa: BLE001
            from sources.base import NoResultError
            empty = isinstance(exc, NoResultError)
            # 关键区分：「这个源没有这个番剧」是正常结果，不是源故障。
            # 早先会把 NoResultError 也记成失败，导致质量分被污染、好源被误判。
            if empty:
                logger.info("源 %s 对「%s」无匹配结果", plugin.name, keyword)
            else:
                db.record_source_result(source_id, False, str(exc))
                logger.warning("源 %s 检索失败：%s", plugin.name, exc)
            return {"source": plugin.name, "source_id": source_id,
                    "type": plugin.type_name, "elapsed": round(time.time() - start, 2),
                    "count": 0, "results": [], "ok": False, "error": str(exc),
                    "empty": empty}

    def search_by_name(self, source_name: str, keyword: str, page: int = 1) -> dict:
        record = db.get_source(name=source_name)
        if not record:
            raise SourceError("源不存在：%s" % source_name)
        return self.search_one(record["id"], keyword, page)

    def aggregate(self, keyword: str, source_ids=None, page: int = 1) -> dict:
        """按优先级顺序并发检索多个源；返回已按优先级重排的聚合结果。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return {"keyword": keyword, "total": 0, "groups": [], "elapsed": 0, "errors": []}

        records = self.list_sources(include_hidden=False)
        if source_ids:
            wanted = {int(sid) for sid in source_ids}
            records = [r for r in records if r["id"] in wanted]
        records = [r for r in records if r.get("enabled")]
        if not records:
            raise SourceError("没有启用任何动漫源，请先在“动漫源管理”里勾选要搜索的源")

        priority = {record["id"]: index for index, record in enumerate(records)}
        workers = max(1, min(int(config.get("search_concurrency", 5) or 5), len(records)))
        start = time.time()
        results = []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.search_one, record["id"], keyword, page): record
                       for record in records}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    record = futures[future]
                    results.append({"source": record["name"], "source_id": record["id"],
                                    "ok": False, "error": str(exc), "results": [], "count": 0,
                                    "elapsed": 0})

        results.sort(key=lambda item: priority.get(item.get("source_id"), 999))

        total = sum(item.get("count", 0) for item in results)
        errors = [{"source": item["source"], "error": item.get("error")}
                  for item in results if not item.get("ok")]
        groups = results

        if config.get("merge_duplicates", False):
            groups = self._merge_groups(groups)

        return {
            "keyword": keyword,
            "total": total,
            "elapsed": round(time.time() - start, 2),
            "groups": groups,
            "errors": errors,
            "source_count": len(records),
        }

    @staticmethod
    def _merge_groups(groups: List[dict]) -> List[dict]:
        merged = {}
        order = []
        for group in groups:
            for item in group.get("results", []):
                key = item.get("name") or item.get("url")
                if key not in merged:
                    merged[key] = {
                        "name": item.get("name"),
                        "url": item.get("url"),
                        "cover": item.get("cover"),
                        "update_info": item.get("update_info"),
                        "sources": [],
                    }
                    order.append(key)
                merged[key]["sources"].append({
                    "source": item.get("source"),
                    "source_id": item.get("source_id"),
                    "url": item.get("url"),
                })
                if not merged[key].get("cover") and item.get("cover"):
                    merged[key]["cover"] = item["cover"]
        items = [merged[key] for key in order]
        return [{"source": "合并结果", "source_id": -1, "ok": True,
                 "count": len(items), "elapsed": 0, "results": items, "merged": True}]

    # ------------------------------------------------------------ 剧集/视频
    def episodes(self, source_id: int, anime_url: str, anime_name: str = "") -> dict:
        record = db.get_source(source_id=source_id)
        if not record:
            raise SourceError("源不存在")
        plugin = self.build_plugin(record)
        start = time.time()
        episodes = plugin.episodes(anime_url, anime_name)
        return {"source": plugin.name, "source_id": source_id,
                "elapsed": round(time.time() - start, 2), "episodes": episodes}

    def resolve_video_url(self, source_id: int, episode_url: str, anime_url: str = "",
                          episode_name: str = "") -> str:
        record = db.get_source(source_id=source_id)
        if not record:
            raise SourceError("源不存在")
        plugin = self.build_plugin(record)
        url = plugin.resolve_video_url(episode_url, anime_url, episode_name)
        # 记录这次解析的附带信息（如浏览器嗅探得到的 Referer/Cookie），
        # 供 task_manager 取走传给下载器；视频 CDN 常校验它们。
        self.last_resolve_meta = dict(getattr(plugin, "last_resolve_meta", {}) or {})
        return url

    def playlist_options(self, source_id: int, episode_url: str, anime_url: str = "") -> List[dict]:
        record = db.get_source(source_id=source_id)
        if not record:
            raise SourceError("源不存在")
        plugin = self.build_plugin(record)
        try:
            return plugin.playlist_options(episode_url, anime_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取线路失败：%s", exc)
            return []

    # ------------------------------------------------------------ 测试
    def test_source(self, source_id: int, keyword: str = None) -> dict:
        record = db.get_source(source_id=source_id)
        if not record:
            raise SourceError("源不存在")
        keyword = (keyword or "").strip()
        plugin = self.build_plugin(record)

        # 插件自带更准确的自检（例如 MacCMS 会逐个接口验证）时优先使用
        if type(plugin).test is not SourcePlugin.test:
            try:
                report = plugin.test()
                if keyword:
                    report["keyword"] = keyword
                report.setdefault("source", record["name"])
                report.setdefault("type", record["type"])
                db.record_source_result(source_id, bool(report.get("ok")),
                                        "" if report.get("ok") else "自检未通过")
                return report
            except Exception as exc:  # noqa: BLE001
                db.record_source_result(source_id, False, str(exc))
                return {"source": record["name"], "type": record["type"],
                        "keyword": keyword or DEFAULT_TEST_KEYWORD, "ok": False, "apis": [],
                        "error": str(exc)[:200]}

        keyword = keyword or DEFAULT_TEST_KEYWORD
        report = {"source": record["name"], "type": record["type"], "keyword": keyword,
                  "steps": []}

        start = time.time()
        try:
            results = plugin.search(keyword, 1)
            report["steps"].append({"step": "检索", "ok": True, "count": len(results),
                                    "elapsed": round(time.time() - start, 2),
                                    "sample": results[:5]})
            db.record_source_result(source_id, True)
        except Exception as exc:  # noqa: BLE001
            report["steps"].append({"step": "检索", "ok": False, "error": str(exc),
                                    "elapsed": round(time.time() - start, 2)})
            db.record_source_result(source_id, False, str(exc))
            report["ok"] = False
            return report

        if not results:
            report["ok"] = False
            report["hint"] = "检索没有返回结果，请检查关键字、检索地址与结果选择器"
            return report

        first = results[0]
        start = time.time()
        try:
            episodes = plugin.episodes(first["url"], first.get("name") or "")
            report["steps"].append({"step": "剧集", "ok": True, "count": len(episodes),
                                    "elapsed": round(time.time() - start, 2),
                                    "sample": episodes[:5]})
        except Exception as exc:  # noqa: BLE001
            report["steps"].append({"step": "剧集", "ok": False, "error": str(exc),
                                    "elapsed": round(time.time() - start, 2)})
            report["ok"] = False
            return report

        if not episodes:
            report["ok"] = False
            return report

        start = time.time()
        episode = episodes[0]
        try:
            url = plugin.resolve_video_url(episode["url"], first["url"], episode.get("name"))
            report["steps"].append({"step": "视频地址", "ok": bool(url), "url": url,
                                    "is_hls": ".m3u8" in (url or "").lower(),
                                    "elapsed": round(time.time() - start, 2)})
            report["ok"] = bool(url)
        except Exception as exc:  # noqa: BLE001
            report["steps"].append({"step": "视频地址", "ok": False, "error": str(exc),
                                    "elapsed": round(time.time() - start, 2)})
            report["ok"] = False
        return report

    def check_source(self, source_id: int) -> dict:
        record = db.get_source(source_id=source_id)
        if not record:
            raise SourceError("源不存在")
        base_url = record.get("base_url") or (record.get("config") or {}).get("base_url")
        if not base_url:
            return {"ok": False, "error": "未填写站点网址"}
        try:
            from core.utils import build_session
            session = build_session()
            start = time.time()
            resp = session.get(base_url, timeout=int(config.get("source_timeout", 15)),
                               verify=bool(config.get("verify_ssl", False)), allow_redirects=True)
            ok = resp.status_code < 400
            elapsed_ms = int((time.time() - start) * 1000)
            if ok:
                db.record_source_result(source_id, True, elapsed_ms=elapsed_ms)
            else:
                db.record_source_result(source_id, False, "HTTP %s" % resp.status_code)
            return {"ok": ok, "status": resp.status_code, "elapsed": round(time.time() - start, 2),
                    "url": resp.url, "length": len(resp.content)}
        except Exception as exc:  # noqa: BLE001
            db.record_source_result(source_id, False, str(exc))
            return {"ok": False, "error": str(exc)}

    def check_all(self) -> List[dict]:
        out = []
        for record in self.list_sources():
            result = self.check_source(record["id"])
            result["source"] = record["name"]
            result["id"] = record["id"]
            out.append(result)
        return out


    def probe_quality(self, keyword: str, progress=None, source_ids=None) -> dict:
        """给每个源采集一次「测活耗时 + 检索命中数」，写入数据库供智能排序使用。

        并发执行（源之间互不影响），keyword 用来统计命中数。
        progress(done, total, row) 用于向前端推送进度，可省略。
        """
        import concurrent.futures

        records = self.list_sources()
        if source_ids:
            wanted = {int(i) for i in source_ids}
            records = [r for r in records if r["id"] in wanted]
        total = len(records)
        if not total:
            return {"total": 0, "rows": []}

        def work(record):
            row = {"id": record["id"], "source": record["name"],
                   "ms": 0, "hits": 0, "ok": False, "note": ""}
            # 1) 测活：拿到响应耗时
            try:
                chk = self.check_source(record["id"])
                row["ok"] = bool(chk.get("ok"))
                row["ms"] = int(float(chk.get("elapsed") or 0) * 1000)
                if not row["ok"]:
                    row["note"] = chk.get("error") or ("HTTP %s" % chk.get("status"))
            except Exception as exc:  # noqa: BLE001
                row["note"] = str(exc)[:80]
            # 2) 抽样检索：拿到命中数
            try:
                res = self.search_one(record["id"], keyword)
                row["hits"] = int(res.get("count") or 0)
                if not res.get("ok") and not row["note"]:
                    row["note"] = "检索失败" if not res.get("empty") else ""
            except Exception as exc:  # noqa: BLE001
                if not row["note"]:
                    row["note"] = str(exc)[:80]
            return row

        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(work, r): r for r in records}
            for index, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001
                    rec = futures[fut]
                    row = {"id": rec["id"], "source": rec["name"], "ms": 0,
                           "hits": 0, "ok": False, "note": str(exc)[:80]}
                rows.append(row)
                if progress:
                    try:
                        progress(index, total, row)
                    except Exception:  # noqa: BLE001
                        pass

        rows.sort(key=lambda r: -r["hits"])
        return {"total": total, "keyword": keyword, "rows": rows,
                "sources": self.list_sources()}


    # ------------------------------------------------------------ 其它
    @staticmethod
    def types() -> List[dict]:
        return list_types()

    @staticmethod
    def _type_label(type_name: str) -> str:
        for item in list_types():
            if item["type"] == type_name:
                return item["display_name"]
        return type_name

    def is_downloaded(self, anime_name: str, episode_name: str) -> bool:
        """该集是否**确实还在磁盘上**。

        数据库历史只作为记录；用户在系统里删掉视频后，历史仍在，
        因此必须先按历史里的路径确认文件存在，再退回按命名规则查找。
        """
        import os
        from core.naming import resolve_existing_path
        record = db.history_map().get((anime_name, episode_name))
        if record:
            return bool(record) and os.path.exists(record)
        return bool(resolve_existing_path(anime_name, episode_name))

    def mark_downloaded_set(self) -> set:
        """返回「文件仍在磁盘上」的 (动漫名, 集名) 集合。

        不能直接用数据库历史：用户删掉已下载的视频后，历史还在，
        会导致程序一直认为“已下载”而跳过（这正是“删除后不会重新下载”的原因）。
        """
        import os
        return {key for key, path in db.history_map().items()
                if path and os.path.exists(path)}


source_manager = SourceManager()
