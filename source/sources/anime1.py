# -*- coding: utf-8 -*-
"""Anime1.me 源插件（台湾站，繁中，不收录国漫）。

这个站有两个特点，决定了它不能只用 generic 的选择器引擎：

1. **一集一个页面**：``https://anime1.me/16270`` 是「海賊王女 [12]」这一集。
   但搜索结果里每条都带 WordPress 分类（``category-929``），
   而分类归档页 ``?cat=929`` 会列出**同系列全部集数** —— 用它把「一集一页」
   聚合成正常的「一部番 → 多集」结构。

2. **视频地址要执行站点 JS**：详情页里只有
   ``<video data-apireq="<编码后的会话令牌>">``，令牌里的 ``t``（时间戳）与
   ``s``（签名）是页面 JS 现场生成的，直接 POST 接口会 403。
   所以取流走 :mod:`core.browser_resolver`：无头浏览器打开播放页，
   监听网络请求截获真实地址（实测能拿到 ``*.mp4``）。

繁简差异：站内是繁体，搜索时同时用简体和繁体各试一次能显著提高命中率。
"""
import re
from typing import List, Optional

from .base import NoResultError, SourceError, SourcePlugin, register

# 站内是繁体，常见简繁差异字做个小映射（只覆盖动漫标题高频字）
_S2T = {
    "红": "紅", "剑": "劍", "龙": "龍", "猫": "貓", "术": "術", "师": "師",
    "统": "統", "进": "進", "击": "擊", "贼": "賊", "战": "戰", "语": "語",
    "无": "無", "职": "職", "转": "轉", "异": "異", "学": "學", "园": "園",
    "时": "時", "间": "間", "风": "風", "线": "線", "缘": "緣", "剧": "劇",
    "场": "場", "话": "話", "国": "國", "画": "畫", "游": "遊", "戏": "戲",
    "爱": "愛", "恋": "戀", "声": "聲", "乐": "樂", "队": "隊", "会": "會",
    "员": "員", "长": "長", "门": "門", "开": "開", "关": "關", "电": "電",
    "视": "視", "湾": "灣", "总": "總", "结": "結", "实": "實", "验": "驗",
    "写": "寫", "读": "讀", "书": "書", "车": "車", "马": "馬", "鱼": "魚",
    "鸟": "鳥", "飞": "飛", "机": "機", "数": "數", "据": "據", "网": "網",
    "络": "絡", "这": "這", "个": "個", "为": "為", "与": "與", "从": "從",
}


def _to_traditional(text: str) -> str:
    return "".join(_S2T.get(ch, ch) for ch in (text or ""))


@register("anime1", display_name="Anime1.me（台湾，需浏览器取流）",
          description="台湾动画站，一集一页；用分类归档聚合成番剧，"
                      "视频地址由无头浏览器嗅探获得（需 Node + Edge/Chrome）。")
class Anime1Source(SourcePlugin):
    type_name = "anime1"
    display_name = "Anime1.me"
    description = "台湾动画站，繁中；取流需无头浏览器"
    config_schema = [
        {"key": "base_url", "label": "站点地址", "type": "text",
         "default": "https://anime1.me",
         "placeholder": "https://anime1.me"},
        {"key": "sniff_wait", "label": "嗅探等待（毫秒）", "type": "number",
         "default": 12000,
         "hint": "打开播放页后等多久收网络请求；站点慢可调大（8000~20000）"},
    ]

    # ------------------------------------------------------------ 内部
    def _get(self, url: str, timeout: int = 25) -> str:
        resp = self.session.get(
            url, timeout=timeout,
            headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
            verify=False)
        resp.raise_for_status()
        # WordPress 站点可能是 big5/utf-8，交给 apparent_encoding
        if not resp.encoding or resp.encoding.lower() in ("iso-8859-1",):
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    @staticmethod
    def _articles(html: str) -> List[dict]:
        """从搜索结果/归档页里取出文章（每篇 = 一集）。

        WordPress 主题结构：
          <article class="... category-929">
            <h2 class="entry-title"><a href="https://anime1.me/16270">海賊王女 [12]</a></h2>
        """
        out = []
        pattern = re.compile(
            r'<article[^>]*class="([^"]*?)"[^>]*>.*?'
            r'<h2[^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*'
            r'<a[^>]+href="(https?://[^"]*?/(\d+))"[^>]*>(.*?)</a>',
            re.S)
        for m in pattern.finditer(html or ""):
            classes, url, post_id, raw_title = m.group(1), m.group(2), m.group(3), m.group(4)
            title = re.sub(r"<[^>]+>", "", raw_title)
            title = (title.replace("&#8211;", "-").replace("&amp;", "&")
                          .replace("&#039;", "'").replace("&quot;", '"').strip())
            if not title:
                continue
            cat = re.search(r"category-(\d+)", classes)
            out.append({
                "url": url.rstrip("/"),
                "post_id": post_id,
                "title": title,
                "category_id": cat.group(1) if cat else "",
            })
        return out

    @staticmethod
    def _series_name(title: str) -> str:
        """把「海賊王女 [12]」压成「海賊王女」——去掉集数尾巴。"""
        name = re.sub(r"\s*[\[\(【]\s*\d+(\.\d+)?\s*[\]\)】]\s*$", "", title or "")
        name = re.sub(r"\s*第\s*\d+(\.\d+)?\s*[集話话]\s*$", "", name)
        return name.strip() or (title or "").strip()

    @staticmethod
    def _ep_num(title: str) -> Optional[float]:
        m = re.search(r"[\[\(【]\s*(\d+(?:\.\d+)?)\s*[\]\)】]", title or "")
        if m:
            return float(m.group(1))
        m = re.search(r"第\s*(\d+(?:\.\d+)?)\s*[集話话]", title or "")
        return float(m.group(1)) if m else None

    # ------------------------------------------------------------ 检索
    def search(self, keyword: str, page: int = 1) -> List[dict]:
        from urllib.parse import quote

        keyword = (keyword or "").strip()
        if not keyword:
            return []
        base = self.base_url or "https://anime1.me"

        # 简繁各试一次：站内繁体，用户多半输入简体
        forms = [keyword]
        trad = _to_traditional(keyword)
        if trad != keyword:
            forms.append(trad)

        articles = []
        for word in forms:
            try:
                html = self._get("%s/?s=%s" % (base, quote(word)))
            except Exception as exc:  # noqa: BLE001
                self.log("搜索「%s」失败：%s" % (word, exc), "warning")
                continue
            found = self._articles(html)
            if found:
                articles = found
                self.log("「%s」命中 %d 条" % (word, len(found)))
                break

        if not articles:
            raise NoResultError("Anime1 没有匹配「%s」的内容" % keyword)

        # 按系列聚合：同一 category 的多个条目 = 同一部番的多集
        series = {}
        order = []
        for art in articles:
            key = art["category_id"] or art["post_id"]
            if key not in series:
                series[key] = {"name": self._series_name(art["title"]),
                               "category_id": art["category_id"], "episodes": []}
                order.append(key)
            series[key]["episodes"].append(art)

        results = []
        for key in order:
            item = series[key]
            item["episodes"].sort(
                key=lambda a: self._ep_num(a["title"]) or 0, reverse=True)
            newest = item["episodes"][0]
            results.append(self.normalize_search_item({
                "name": item["name"],
                "url": "anime1://%s" % item["category_id"],
                "cover": "",
                "update_info": "最新 %s" % (newest.get("title") or "")[-8:].strip(),
                "category_id": item["category_id"],
                "episode_count": len(item["episodes"]),
                "playable": True,       # 取流可用（走浏览器嗅探）
            }))
        return results

    # ------------------------------------------------------------ 剧集
    def episodes(self, anime_url: str, anime_name: str = "") -> List[dict]:
        base = self.base_url or "https://anime1.me"
        m = re.match(r"^anime1://(\d+)$", anime_url or "")
        cat = m.group(1) if m else ""
        if not cat:
            raise SourceError("剧集地址格式不对（应为 anime1://<分类ID>）")

        html = self._get("%s/?cat=%s" % (base, cat))
        articles = self._articles(html)
        if not articles:
            raise SourceError("分类 %s 下没有解析到剧集（站点结构可能改了）" % cat)

        same = [a for a in articles if a.get("category_id") == cat] or articles
        same.sort(key=lambda a: self._ep_num(a["title"]) or 0)

        out = []
        for art in same:
            num = self._ep_num(art["title"])
            out.append(self.normalize_episode({
                "name": ("第%g集" % num) if num else art["title"],
                "url": art["url"],
                "title": art["title"],
            }))
        self.log("系列 %s 取得 %d 集" % (cat, len(out)))
        return out

    # ------------------------------------------------------------ 取流
    def resolve_video_url(self, episode_url: str, anime_url: str = "",
                          episode_name: str = "") -> Optional[str]:
        """用无头浏览器打开播放页，截获真实视频地址。

        anime1 把会话令牌放在 ``<video data-apireq>``，令牌里的时间戳与签名由
        页面 JS 生成，纯 HTTP 复刻会被 403；所以这里必须让浏览器自己跑。
        """
        from core import browser_resolver

        info = browser_resolver.available()
        if not info["usable"]:
            why = ("已在设置中关闭" if not info["enabled"]
                   else (info["reason"] or "环境不满足"))
            raise SourceError(
                "Anime1 需要浏览器取流，但当前不可用（%s）。"
                "请在「设置 → 浏览器取流」查看依赖与开关。" % why)

        try:
            wait_ms = int(self.cfg("sniff_wait") or 12000)
        except (TypeError, ValueError):
            wait_ms = 12000

        result = browser_resolver.resolve_with_browser(episode_url, wait_ms=wait_ms)
        if not result.get("ok") or not result.get("video"):
            raise SourceError("浏览器没截获到视频地址：%s"
                              % (result.get("error") or "页面上没有视频请求"))

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
        return result["video"]

    def test(self) -> dict:
        keyword = self.cfg("test_keyword") or "海賊王"
        results = self.search(keyword, 1)
        return {"ok": bool(results), "count": len(results), "keyword": keyword,
                "sample": results[:3]}
