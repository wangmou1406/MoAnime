# -*- coding: utf-8 -*-
"""动漫源插件协议与注册表。

要让程序认识一种新“源类型”，只需在 sources/ 目录里放一个模块，并让它
继承 :class:`SourcePlugin`，然后用 ``@register("type_name")`` 装饰即可，
Web 界面会自动把该类型列入“添加动漫源”的候选类型。
"""
from typing import Any, Dict, List, Optional

_REGISTRY: Dict[str, type] = {}
_META: Dict[str, dict] = {}

# 测试源时若没单独配置 test_keyword，用这个通用词（不用具体作品名，避免默认搜某部番）
DEFAULT_TEST_KEYWORD = "动画"


class SourceError(Exception):
    """源操作失败（网络、解析、反爬等）。"""


class UnresolvableError(SourceError):
    """该源给的是分享页/播放页，解析不出可下载的视频地址。

    单独成类，便于上层捕获后自动换源重下。
    """


class NoResultError(SourceError):
    """接口访问正常，只是没有匹配该关键字的内容——这不是故障。"""


class SourcePlugin:
    """所有动漫源的基类。

    子类至少要实现 :meth:`search`、:meth:`episodes`、:meth:`resolve_video_url`。
    """

    #: 类型标识（与注册名一致）
    type_name = "base"
    #: Web 界面显示名
    display_name = "基础源"
    #: 该类型需要的配置字段（由 Web 界面渲染表单）
    config_schema: List[dict] = []
    #: 简单说明
    description = ""

    def __init__(self, record: dict, manager=None):
        self.record = dict(record or {})
        self.manager = manager
        self.id = self.record.get("id")
        self.name = self.record.get("name") or self.type_name
        self.config = dict(self.record.get("config") or {})
        self.base_url = (self.record.get("base_url") or self.config.get("base_url") or "").rstrip("/")
        self.logger = None
        self._session = None
        #: resolve_video_url 的附带信息（如 Referer、Cookie、取流方式）。
        #: 浏览器嗅探出的地址往往要带播放页 Referer 与**会话 Cookie** 才能下载
        #: （例：Anime1 的 CDN 校验一个路径绑定的 JWT cookie，缺了必然 403）。
        #: 由 task_manager 读走并传给下载器。
        self.last_resolve_meta: Dict[str, Any] = {}

    # ------------------------------------------------------------ 基础
    @property
    def session(self):
        if self._session is None:
            from core.utils import build_session
            self._session = build_session()
        return self._session

    def log(self, message: str, level: str = "info") -> None:
        from core.logging_setup import get_logger
        logger = self.logger or get_logger("moanime.source.%s" % self.type_name)
        getattr(logger, level if level in ("info", "warning", "error", "debug") else "info")(
            "[%s] %s", self.name, message)

    def cfg(self, key: str, default=None):
        value = self.config.get(key)
        if value in (None, "", [], {}):
            return self.record.get(key, default) if key in ("base_url",) else default
        return value

    # ------------------------------------------------------------ 接口
    def search(self, keyword: str, page: int = 1) -> List[dict]:
        """返回 [{name, url, cover, update_info, source, ...}]"""
        raise NotImplementedError

    def episodes(self, anime_url: str, anime_name: str = "") -> List[dict]:
        """返回 [{name, url, episode}]"""
        raise NotImplementedError

    def resolve_video_url(self, episode_url: str, anime_url: str = "",
                          episode_name: str = "") -> Optional[str]:
        """解析出真实的视频地址（m3u8 / mp4）。"""
        raise NotImplementedError

    def playlist_options(self, episode_url: str, anime_url: str = "") -> List[dict]:
        """可选：返回多个线路 [{name, url}]，默认单线路。"""
        url = self.resolve_video_url(episode_url, anime_url)
        return [{"name": "默认线路", "url": url}] if url else []

    # ------------------------------------------------------------ 辅助
    def normalize_search_item(self, item: dict) -> dict:
        item = dict(item or {})
        item.setdefault("source", self.name)
        item.setdefault("source_id", self.id)
        item.setdefault("source_type", self.type_name)
        item["name"] = (item.get("name") or "").strip()
        return item

    def normalize_episode(self, item: dict) -> dict:
        from core.utils import episode_number
        item = dict(item or {})
        item.setdefault("episode", episode_number(item.get("name"), 0))
        item.setdefault("source", self.name)
        return item

    def test(self) -> dict:
        """自检：用配置里的测试关键字搜索一次（没配则用一个通用词）。"""
        keyword = self.cfg("test_keyword") or DEFAULT_TEST_KEYWORD
        results = self.search(keyword, 1)
        return {"ok": bool(results), "count": len(results), "keyword": keyword,
                "sample": results[:3]}


def register(type_name: str, **meta):
    """注册源类型。"""
    def decorator(cls):
        cls.type_name = type_name
        _REGISTRY[type_name] = cls
        _META[type_name] = {
            "type": type_name,
            "display_name": meta.get("display_name") or getattr(cls, "display_name", type_name),
            "description": meta.get("description") or getattr(cls, "description", ""),
            "config_schema": meta.get("config_schema") or getattr(cls, "config_schema", []),
            "builtin_sample": bool(meta.get("sample")),
        }
        return cls
    return decorator


def get_plugin_class(type_name: str):
    return _REGISTRY.get(type_name)


def list_types() -> List[dict]:
    return [dict(_META[key]) for key in sorted(_REGISTRY)]


def create_plugin(record: dict, manager=None) -> SourcePlugin:
    type_name = (record or {}).get("type") or "generic"
    cls = _REGISTRY.get(type_name)
    if cls is None:
        raise SourceError("未知的源类型: %s" % type_name)
    return cls(record, manager=manager)


def registered_count() -> int:
    return len(_REGISTRY)
