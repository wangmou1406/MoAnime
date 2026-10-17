# -*- coding: utf-8 -*-
"""通用可配置源：不写代码，只填“网址 + 选择器/正则”即可接入任意动漫站。

配置结构（保存在数据库 sources.config 里）::

    {
      "base_url": "https://example.com",
      "encoding": "utf-8",                 # 留空自动探测
      "headers": {"Referer": "..."},
      "request": {"method": "GET", "data": {}, "json": {}},
      "search": {
        "mode": "path",                     # path | query | template
        "url": "/search/{keyword}",         # template 模式下可含 {page}
        "query_param": "wd",                # mode=query 时使用
        "path": "/search/{keyword}",        # mode=path 时使用
        "method": "GET",
        "pages": 1,
        "item": "div.item",
        "item_url": "a@href",
        "name": "h3",
        "cover": "img@data-src",
        "update": "span.time",
        "url_pattern": ""                   # 只保留匹配该正则的详情页
      },
      "episodes": {
        "item": "ul.list li a",
        "name": "",                          # 留空取链接文本
        "url": "@href",
        "url_regex": "/(\\d+)\\.html",
        "reverse": false                     # 站点是倒序时勾选
      },
      "video": {
        "type": "auto",                      # auto | m3u8 | mp4 | playlist | browser
        "patterns": ["\"url\":\"(.*?\\.m3u8.*?)\""],
        "playlist": {"item": "div.tab a", "name": "", "url": "@data-url"},
        "headers": {"Referer": "{episode_url}"},
        "browser": false,                    # 勾选后：正则抓不到时改用无头浏览器嗅探
        "sniff_wait": 12000                  # 浏览器最多等多久收请求（毫秒）
      }
    }

取流顺序
--------
``type=auto``（默认）时：先按正则/选择器抓；**抓不到且勾了 ``browser``**
则用无头浏览器打开剧集页、监听网络请求截获真实视频地址。
这样普通站点走 HTTP（快），只有需要执行站点 JS 的站才开浏览器（慢但通用）。

占位符：``{keyword}`` ``{page}`` ``{base}`` ``{anime_url}`` ``{episode_url}``
"""
import re
from typing import List
from urllib.parse import quote, urljoin

from core.selector import extract_by_regex, select_all, select_one, select_url
from core.utils import absolute_url, clean_text, dedupe, episode_number

from .base import SourceError, SourcePlugin, register

COMMON_HEADERS_SCHEMA = [
    {"key": "headers", "label": "自定义请求头(JSON)", "type": "json", "default": {},
     "placeholder": "{\"Referer\": \"https://example.com\"}"},
]

VIDEO_HINT = ("抓不到视频地址时，请在浏览器 F12 → Network 里过滤 m3u8，"
              "把请求地址的正则片段填到“视频地址正则”里。")


@register("generic", display_name="通用可配置源（选择器/正则）",
          description="用 CSS 选择器或正则配置检索、剧集、视频地址，适配任意 HTML 动漫站。",
          config_schema=COMMON_HEADERS_SCHEMA)
class GenericSource(SourcePlugin):
    type_name = "generic"
    display_name = "通用可配置源"

    # ------------------------------------------------------------ 请求
    def _headers(self, extra: dict = None, **format_args) -> dict:
        from core.config import config as app_config
        headers = {"User-Agent": app_config.get("user_agent")}
        merged = {}
        merged.update(self.cfg("headers") or {})
        merged.update(extra or {})
        out = {}
        for key, value in list(headers.items()) + list(merged.items()):
            if isinstance(value, str) and format_args:
                try:
                    value = value.format(**format_args)
                except Exception:
                    pass
            out[key] = value
        return out

    def fetch(self, url: str, method: str = "GET", extra_headers: dict = None,
              data: dict = None, json_body: dict = None, **format_args) -> str:
        from core.config import config as app_config
        if not url:
            raise SourceError("请求地址为空")
        url = url.format(base=self.base_url, **format_args) if "{" in url else url
        method = (method or "GET").upper()
        timeout = int(self.cfg("request_timeout") or app_config.get("source_timeout", 15))
        verify = bool(app_config.get("verify_ssl", False))
        try:
            if method == "POST":
                resp = self.session.post(url, headers=self._headers(extra_headers, **format_args),
                                         data=data or None, json=json_body or None,
                                         timeout=timeout, verify=verify)
            else:
                resp = self.session.get(url, headers=self._headers(extra_headers, **format_args),
                                        timeout=timeout, verify=verify)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise SourceError("请求失败 %s: %s" % (url, exc)) from exc

        encoding = self.cfg("encoding")
        if encoding:
            resp.encoding = encoding
        elif resp.encoding in (None, "ISO-8859-1"):
            match = re.search(r"charset=[\"']?([\w-]+)", resp.text[:2000], re.I)
            resp.encoding = match.group(1) if match else "utf-8"
        return resp.text

    # ------------------------------------------------------------ 检索
    def _search_url(self, keyword: str, page: int) -> str:
        search = self.cfg("search") or {}
        mode = (search.get("mode") or "path").lower()
        template = search.get("url") or ""
        if mode == "template" and template:
            return template.format(base=self.base_url, keyword=quote(keyword),
                                   keyword_raw=keyword, page=page)
        if mode == "query" or (not template and mode == "query"):
            param = search.get("query_param") or "wd"
            path = search.get("path") or "/"
            url = absolute_url(self.base_url, path)
            return "%s%s%s=%s" % (url, "&" if "?" in url else "?", param, quote(keyword))
        path = search.get("path") or template or "/search/{keyword}"
        url = path.format(base=self.base_url, keyword=quote(keyword), keyword_raw=keyword, page=page)
        if page > 1:
            url = self._with_page(url, page, search)
        return absolute_url(self.base_url, url)

    @staticmethod
    def _with_page(url: str, page: int, search: dict) -> str:
        from core.utils import merge_query
        param = search.get("page_param") or "page"
        return merge_query(url, override={param: page})

    def search(self, keyword: str, page: int = 1) -> List[dict]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        search = self.cfg("search") or {}
        if not search:
            raise SourceError("该源还没有配置“检索”规则，请在源编辑里补全")

        pages = 1
        if page <= 1:
            try:
                pages = max(1, min(int(search.get("pages") or 1), 5))
            except (TypeError, ValueError):
                pages = 1
        results: List[dict] = []
        for index in range(pages):
            current_page = index + 1 if page <= 1 else page
            url = self._search_url(keyword, current_page)
            html = self.fetch(url, search.get("method") or "GET", data=search.get("data"),
                              json_body=search.get("json"), keyword=quote(keyword))
            results.extend(self._parse_search(html, url, search))
            if page > 1:
                break
        return dedupe(results, key=lambda item: item.get("url") or item.get("name"))

    def _parse_search(self, html: str, page_url: str, search: dict) -> List[dict]:
        item_selector = search.get("item") or ""
        url_pattern = search.get("url_pattern") or ""
        results = []

        if item_selector:
            try:
                from bs4 import BeautifulSoup
                try:
                    soup = BeautifulSoup(html, "lxml")
                except Exception:
                    soup = BeautifulSoup(html, "html.parser")
                nodes = soup.select(item_selector)
            except Exception as exc:  # noqa: BLE001
                raise SourceError("解析检索结果失败: %s" % exc) from exc

            for node in nodes:
                fragment = str(node)
                item = self._extract_item(fragment, page_url, search)
                if item and item.get("name") and item.get("url"):
                    results.append(item)
        else:
            item = self._extract_item(html, page_url, search)
            if item and item.get("name"):
                results.append(item)

        if url_pattern:
            try:
                results = [r for r in results if re.search(url_pattern, r.get("url") or "")]
            except re.error:
                pass
        return results

    def _extract_item(self, fragment: str, page_url: str, search: dict) -> dict:
        name = select_one(fragment, search.get("name") or "", "")
        url = select_one(fragment, search.get("url") or "a@href", "")
        if not url:
            url = select_one(fragment, "a@href", "")
        if not name:
            name = select_one(fragment, "a@title", "") or select_one(fragment, "a", "")
        cover = select_one(fragment, search.get("cover") or "img@data-src", "")
        if not cover and search.get("cover_fallback", True):
            cover = select_one(fragment, "img@data-original", "") or select_one(fragment, "img@src", "")
        update_info = select_one(fragment, search.get("update") or "", "")
        # 相对链接的基址：默认用搜索结果页，但有些站的结果页路径不等于站点根
        # （例如 /search.html?wd=x 里的 /anime/1.html 应相对站点根解析），
        # 这时在 search 里配 url_base（值写 "base" 表示站点根）即可。
        base_for_links = self._resolve_base(search.get("url_base"), page_url)
        return self.normalize_search_item({
            "name": clean_text(name),
            "url": absolute_url(base_for_links, url),
            "cover": absolute_url(base_for_links, cover) if cover else "",
            "update_info": clean_text(update_info),
        })

    # ------------------------------------------------------------ 剧集
    def episodes(self, anime_url: str, anime_name: str = "") -> List[dict]:
        config = self.cfg("episodes") or {}
        if not config:
            raise SourceError("该源还没有配置“剧集”规则")
        html = self.fetch(anime_url, config.get("method") or "GET", extra_headers=config.get("headers"))
        item_selector = config.get("item") or ""
        url_regex = config.get("url_regex") or ""
        episodes: List[dict] = []

        if item_selector:
            try:
                from bs4 import BeautifulSoup
                try:
                    soup = BeautifulSoup(html, "lxml")
                except Exception:
                    soup = BeautifulSoup(html, "html.parser")
                nodes = soup.select(item_selector)
            except Exception as exc:  # noqa: BLE001
                raise SourceError("解析剧集失败: %s" % exc) from exc

            for node in nodes:
                fragment = str(node)
                name = select_one(fragment, config.get("name") or "", "")
                url = select_one(fragment, config.get("url") or "@href", "")
                if not url:
                    url = select_one(fragment, "a@href", "") or select_one(fragment, "@href", "")
                if not name:
                    name = select_one(fragment, "@title", "") or select_one(fragment, "a", "")
                url = absolute_url(self._resolve_base(config.get("url_base"), anime_url), url)
                if not url or not self._episode_matches(url, url_regex):
                    continue
                episodes.append(self._build_episode(name, url, len(episodes) + 1))
        else:
            # 没有列表容器：直接在详情页里按正则/链接规则找剧集
            candidates = select_all(html, config.get("url") or "a@href")
            for href in candidates:
                url = absolute_url(self._resolve_base(config.get("url_base"), anime_url), href)
                if not self._episode_matches(url, url_regex):
                    continue
                episodes.append(self._build_episode("", url, len(episodes) + 1))

        episodes = dedupe(episodes, key=lambda item: item["url"])
        if config.get("reverse"):
            episodes.reverse()
        for index, episode in enumerate(episodes, start=1):
            if not episode.get("name"):
                episode["name"] = "第%d集" % index
            episode["episode"] = episode_number(episode["name"], index)
        if not episodes:
            raise SourceError("没有解析到剧集，请检查“剧集”选择器%s" % (
                "或正则 " + url_regex if url_regex else ""))
        return episodes

    def _resolve_base(self, url_base, fallback: str) -> str:
        """把 url_base 配置解析成实际基址。

        约定：``"base"`` / ``"{base}"`` 表示站点根（self.base_url）；
        其余字符串按占位符格式化；留空则用 fallback（通常是当前页面地址）。
        """
        if not url_base:
            return fallback
        if url_base in ("base", "{base}"):
            return self.base_url
        try:
            return str(url_base).format(base=self.base_url, keyword="", page="",
                                        episode_url="", anime_url="")
        except Exception:  # noqa: BLE001
            return fallback

    @staticmethod
    def _episode_matches(url: str, pattern: str) -> bool:
        if not pattern:
            return True
        try:
            return bool(re.search(pattern, url))
        except re.error:
            return True

    def _build_episode(self, name: str, url: str, index: int) -> dict:
        name = clean_text(name)
        if not name:
            match = re.search(r"/(\d+(?:\.\d+)?)(?:\.html?|/)?$", url)
            name = "第%s集" % match.group(1) if match else "第%d集" % index
        return self.normalize_episode({"name": name, "url": url})

    # ------------------------------------------------------------ 视频地址
    def resolve_video_url(self, episode_url: str, anime_url: str = "",
                          episode_name: str = "") -> str:
        config = self.cfg("video") or {}
        patterns = config.get("patterns") or []
        vtype = (config.get("type") or "auto").lower()
        need_fetch = bool(patterns) or vtype != "direct"
        html = ""
        if need_fetch:
            try:
                html = self.fetch(episode_url, config.get("method") or "GET",
                                  extra_headers=config.get("headers"),
                                  episode_url=episode_url, anime_url=anime_url)
            except Exception as exc:  # noqa: BLE001
                # 抓页面失败不该直接判死：也许浏览器能打开（反爬/需要 JS）
                self.log("抓取剧集页失败（稍后可能用浏览器取流）：%s" % exc, "warning")
                html = ""
        fetchers = [html, episode_url]
        for source in fetchers:
            value = extract_by_regex(source, patterns, base_url=episode_url, attr="url")
            if value:
                return value
        if vtype == "direct" and episode_url.lower().endswith(
                (".mp4", ".m3u8", ".flv", ".mkv", ".ts")):
            return episode_url

        # 正则抓不到 —— 若该源声明了用浏览器取流，就用无头浏览器嗅探。
        # 这一步专治「地址由站点 JS 现场算出」的站（会话签名、混淆解密、
        # 客户端渲染），纯 HTTP 无论怎么配正则都拿不到。
        if config.get("browser"):
            return self._resolve_by_browser(episode_url, config)

        raise SourceError("未能解析出视频地址。%s" % VIDEO_HINT)

    def _resolve_by_browser(self, episode_url: str, config: dict) -> str:
        """用无头浏览器打开剧集页，监听网络请求截获真实视频地址。"""
        from core import browser_resolver

        info = browser_resolver.available()
        if not info["usable"]:
            why = ("已在设置中关闭" if not info["enabled"]
                   else (info["reason"] or "环境不满足"))
            raise SourceError(
                "该源需要浏览器取流，但当前不可用（%s）。"
                "请在「设置 → 浏览器取流」查看依赖与开关。" % why)

        try:
            wait_ms = int(config.get("sniff_wait") or 12000)
        except (TypeError, ValueError):
            wait_ms = 12000

        result = browser_resolver.resolve_with_browser(episode_url, wait_ms=wait_ms)
        if not result.get("ok") or not result.get("video"):
            raise SourceError("浏览器没截获到视频地址：%s"
                              % (result.get("error") or "页面上没有视频请求"))

        # 把 Referer 与会话 Cookie 交给上层：视频 CDN 常校验它们
        # （Anime1 的 cookie 里 p 是路径绑定的 JWT，缺了直连一律 403）。
        headers = {}
        if result.get("cookies"):
            headers["Cookie"] = result["cookies"]
        if result.get("ua"):
            headers["User-Agent"] = result["ua"]
        self.last_resolve_meta = {
            "referer": result.get("referer") or episode_url,
            "browser_resolved": True,
            "kind": result.get("kind") or "",
        }
        if headers:
            self.last_resolve_meta["headers"] = headers
        self.log("浏览器嗅探取流成功（%s）" % (result.get("kind") or "?"))
        return result["video"]

    def playlist_options(self, episode_url: str, anime_url: str = "") -> List[dict]:
        config = (self.cfg("video") or {}).get("playlist") or {}
        if config.get("item"):
            html = self.fetch(episode_url, episode_url=episode_url, anime_url=anime_url)
            try:
                from bs4 import BeautifulSoup
                try:
                    soup = BeautifulSoup(html, "lxml")
                except Exception:
                    soup = BeautifulSoup(html, "html.parser")
                nodes = soup.select(config["item"])
            except Exception:
                nodes = []
            options = []
            for node in nodes:
                fragment = str(node)
                name = select_one(fragment, config.get("name") or "", "") or "线路%d" % (len(options) + 1)
                url = select_one(fragment, config.get("url") or "@href", "")
                url = url.format(episode_url=episode_url, anime_url=anime_url)
                options.append({"name": clean_text(name),
                                "url": absolute_url(episode_url, url) if not url.startswith("http") else url})
            if options:
                return options
        return super().playlist_options(episode_url, anime_url)
