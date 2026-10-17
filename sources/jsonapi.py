# -*- coding: utf-8 -*-
"""JSON API 源：适配返回 JSON 的自建/第三方接口。

配置示例::

    {
      "base_url": "https://api.example.com",
      "search":  {"url": "/api/search?q={keyword}", "list": "data.list[*]",
                  "name": "name", "url": "url", "cover": "pic", "update": "note"},
      "episodes":{"url": "/api/detail?id={id}", "list": "data.episodes[*]",
                  "name": "title", "url": "url", "id_from": "url"},
      "video":   {"url": "/api/play?id={id}", "path": "data.url"}
    }
"""
import json
import re
from typing import List
from urllib.parse import quote

from core.selector import _json_walk
from core.utils import absolute_url, clean_text, dedupe, episode_number

from .base import SourceError, SourcePlugin, register


def _json_path(content: str, path: str):
    try:
        data = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        raise SourceError("返回内容不是合法 JSON: %s" % exc) from exc
    return _json_walk(data, path) if path else data


def _pick(node, key: str, default=""):
    if not key:
        return default
    value = node
    for token in key.split("."):
        if isinstance(value, dict):
            value = value.get(token)
        elif isinstance(value, list) and token.isdigit():
            index = int(token)
            value = value[index] if 0 <= index < len(value) else None
        else:
            return default
        if value is None:
            return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


@register("jsonapi", display_name="JSON 接口源",
          description="适配返回 JSON 的动漫接口，用点分路径取出字段。")
class JsonApiSource(SourcePlugin):
    type_name = "jsonapi"
    display_name = "JSON 接口源"

    # ------------------------------------------------------------ 请求
    def fetch(self, url: str, params: dict = None, **format_args) -> str:
        from core.config import config as app_config
        if not url:
            raise SourceError("请求地址为空")
        url = url.format(base=self.base_url, **format_args) if "{" in url else url
        if not url.startswith("http"):
            url = absolute_url(self.base_url, url)
        headers = {"User-Agent": app_config.get("user_agent")}
        headers.update(self.cfg("headers") or {})
        try:
            resp = self.session.get(url, params=params, headers=headers,
                                    timeout=int(self.cfg("request_timeout")
                                                or app_config.get("source_timeout", 15)),
                                    verify=bool(app_config.get("verify_ssl", False)))
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise SourceError("请求失败 %s: %s" % (url, exc)) from exc
        return resp.text

    # ------------------------------------------------------------ 检索
    def search(self, keyword: str, page: int = 1) -> List[dict]:
        keyword = (keyword or "").strip()
        config = self.cfg("search") or {}
        if not config:
            raise SourceError("该源还没有配置“检索”规则")
        if not keyword:
            return []
        url = (config.get("url") or "/?keyword={keyword}").format(
            base=self.base_url, keyword=quote(keyword), page=page)
        content = self.fetch(url, keyword=quote(keyword), page=page)
        items = _json_path(content, config.get("list") or "") or []
        if isinstance(items, dict):
            items = [items]
        results = []
        for node in items if isinstance(items, list) else []:
            name = clean_text(_pick(node, config.get("name") or "name"))
            link = _pick(node, config.get("url") or "url")
            if not name or not link:
                continue
            results.append(self.normalize_search_item({
                "name": name,
                "url": absolute_url(self.base_url, str(link)),
                "cover": absolute_url(self.base_url, str(_pick(node, config.get("cover") or "cover") or "")),
                "update_info": clean_text(_pick(node, config.get("update") or "")),
                "raw": node,
            }))
        return dedupe(results, key=lambda item: item["url"])

    # ------------------------------------------------------------ 剧集
    def episodes(self, anime_url: str, anime_name: str = "") -> List[dict]:
        config = self.cfg("episodes") or {}
        if not config:
            raise SourceError("该源还没有配置“剧集”规则")
        ident = anime_url
        id_pattern = config.get("id_from") or ""
        if id_pattern:
            match = re.search(id_pattern, anime_url)
            if match:
                ident = match.group(1) if match.groups() else match.group(0)
            elif id_pattern == "url":
                ident = quote(anime_url, safe="")
        url = (config.get("url") or anime_url).format(
            base=self.base_url, url=quote(anime_url, safe=""), id=ident, anime_url=anime_url)
        content = self.fetch(url, id=ident, anime_url=quote(anime_url, safe=""))
        items = _json_path(content, config.get("list") or "") or []
        if isinstance(items, dict):
            items = [items]
        episodes = []
        for index, node in enumerate(items if isinstance(items, list) else [], start=1):
            name = clean_text(_pick(node, config.get("name") or "name")) or "第%d集" % index
            link = _pick(node, config.get("url") or "url")
            if not link:
                continue
            episodes.append(self.normalize_episode({
                "name": name,
                "url": str(link) if str(link).startswith("http") else absolute_url(self.base_url, str(link)),
            }))
        if not episodes:
            raise SourceError("接口没有返回剧集数据")
        return dedupe(episodes, key=lambda item: item["url"])

    # ------------------------------------------------------------ 视频
    def resolve_video_url(self, episode_url: str, anime_url: str = "",
                          episode_name: str = "") -> str:
        config = self.cfg("video") or {}
        if config.get("path"):
            ident = episode_url
            id_pattern = config.get("id_from") or ""
            if id_pattern:
                match = re.search(id_pattern, episode_url)
                if match:
                    ident = match.group(1) if match.groups() else match.group(0)
            url = (config.get("url") or episode_url).format(
                base=self.base_url, url=quote(episode_url, safe=""), id=ident)
            content = self.fetch(url, id=ident, episode_url=quote(episode_url, safe=""))
            value = _json_path(content, config["path"])
            if isinstance(value, list):
                value = value[0] if value else ""
            if value:
                return str(value)
        if episode_url.lower().endswith((".m3u8", ".mp4", ".flv", ".ts")):
            return episode_url
        raise SourceError("接口没有返回视频地址，请检查“视频”路径配置")
