# -*- coding: utf-8 -*-
"""自动探测：给一个站点首页，尽力猜出“检索规则 / 剧集规则 / 视频规则”草稿。"""
import re
from collections import Counter
from urllib.parse import parse_qs, urljoin, urlparse

from core.utils import absolute_url, clean_text

from .generic import GenericSource

_EP_TEXT = re.compile(r"(第\s*\d+\s*[集话話]|EP?\s*\d+|更新至|全集|線路|线路)", re.I)
_EP_HREF = re.compile(r"(\d+\.html?$|/vodplay/|/play/|/watch/|/ep/|/episode/|/v/|/player/)", re.I)
_CLASS_HINT = re.compile(r"(list|ep|play|detail|vod|item|result|search|card|box|ul)", re.I)


def _classes(node) -> str:
    value = node.get("class") or []
    if isinstance(value, str):
        value = value.split()
    return ".".join(value)


def _selector_for(node) -> str:
    tag = node.name or "a"
    classes = _classes(node)
    if classes:
        return "%s.%s" % (tag, classes.split(".")[0]) if classes else tag
    return tag


def _group_key(node, index: int) -> int:
    """同一容器下的链接视为同一组。"""
    parent = node.find_parent()
    return id(parent) if parent is not None else index


def _path_patterns(url: str):
    """把 URL 变换成若干“同族链接模板”，用 \x00 表示被抹掉的数字。"""
    path = urlparse(url).path.rstrip("/")
    patterns = []
    if "+" in path:                                    # /vod-play-1-2.html
        patterns.append(re.sub(r"\d+(?=[^\d]*$)", "\x00", path))
    nums = list(re.finditer(r"\d+", path))
    if len(nums) >= 2:                                 # /demo/play/1/1101
        last = nums[-1]
        patterns.append(path[:last.start()] + "\x00" + path[last.end():])
    if nums:
        patterns.append(re.sub(r"\d+", "\x00", path))
    return patterns


def _cluster_candidates(episode_links):
    """把剧集链接按“路径族 + 最近公共容器”聚类，得到可用的剧集选择器。"""
    if not episode_links:
        return []

    groups = {}
    for node, absolute, text in episode_links:
        for pattern in _path_patterns(absolute):
            groups.setdefault(pattern, []).append((node, absolute, text))

    clusters = []
    for pattern, items in groups.items():
        if len(items) < 2:
            continue
        nodes = [item[0] for item in items]
        common = _common_ancestor(nodes)
        if common is None:
            continue
        # 收敛到“只包含这些剧集链接”的最小容器
        while len(common.find_all("a", href=True)) > len(items) * 2:
            children = [child for child in common.find_all(recursive=False) if child.name]
            candidate = None
            for child in children:
                if child.find("a", href=True) is not None:
                    candidate = child
                    break
            if candidate is None:
                break
            common = candidate
        selector = _selector_of(common)
        link_tag = items[0][0].name or "a"
        regex = "^" + re.escape(pattern).replace("\x00", r"(\d+)") + r"(?:\.html?)?/?$"
        clusters.append({
            "container": selector,
            "item": "%s a" % selector,
            "count": len(items),
            "url_regex": regex,
            "link_selector": "%s@href" % link_tag,
            "samples": [{"name": item[2] or "(无文本)", "url": item[1]} for item in items[:3]],
        })
    clusters.sort(key=lambda item: item["count"], reverse=True)
    # 同一容器只保留一个候选（保留最先命中的、更精确的正则）
    unique, seen = [], set()
    for cluster in clusters:
        key = (cluster["container"], cluster["item"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(cluster)
    return unique[:6]


def _selector_of(node) -> str:
    tag = node.name or "div"
    classes = _classes(node)
    if classes:
        return "%s.%s" % (tag, classes.split(".")[0])
    return tag


def _common_ancestor(nodes):
    """自根向下比较，返回所有节点的最近公共祖先。"""
    if not nodes:
        return None
    chains = []
    for node in nodes:
        chain = []
        current = node
        while current is not None:
            chain.append(current)
            current = current.parent
        chains.append(list(reversed(chain)))
    common = None
    for level in zip(*chains):
        if len(set(id(item) for item in level)) == 1:
            common = level[0]
        else:
            break
    if common is None or getattr(common, "name", None) is None:
        return None
    return common


def _fetch_text(url: str) -> str:
    plugin = GenericSource({"name": "auto-detect", "type": "generic", "base_url": url, "config": {}})
    return plugin.fetch(url)


def detect(base_url: str, page_url: str = "") -> dict:
    base_url = (base_url or "").strip().rstrip("/")
    page_url = (page_url or "").strip() or base_url
    try:
        html = _fetch_text(page_url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "抓取页面失败：%s" % exc}
    if not html:
        return {"ok": False, "error": "抓取页面失败，请确认网址可访问"}

    try:
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
    except ImportError:
        return {"ok": False, "error": "缺少 beautifulsoup4 依赖"}

    report = {
        "ok": True,
        "base_url": base_url,
        "page_url": page_url,
        "page_title": clean_text(soup.title.get_text()) if soup.title else "",
        "search": {},
        "candidates": [],
        "warnings": [],
        "confidence": {},
    }

    # ---------------------------------------------------------- 检索表单
    search = {}
    best_form = None
    best_score = -1
    for form in soup.find_all("form"):
        score = 0
        action = (form.get("action") or "").lower()
        if "search" in action or "so" in action:
            score += 3
        inputs = form.find_all(["input"])
        for node in inputs:
            name = (node.get("name") or "").lower()
            if name in ("wd", "keyword", "kw", "q", "search", "s", "key", "searchword"):
                score += 2
        if form.find("input", {"type": "submit"}):
            score += 1
        if score > best_score:
            best_score, best_form = score, form

    if best_form is not None:
        action = best_form.get("action") or page_url
        if action.lower() in ("", "#", "javascript:;", "javascript:void(0)"):
            action = page_url
        param = ""
        for node in best_form.find_all("input"):
            name = (node.get("name") or "").strip()
            if name and name.lower() not in ("submit", "search", "button"):
                param = name
                break
        method = (best_form.get("method") or "get").upper()
        search = {
            "mode": "query" if method == "GET" else "template",
            "path": urlparse(urljoin(page_url, action)).path or "/search",
            "query_param": param or "wd",
            "method": "GET" if method == "GET" else "POST",
        }
        report["confidence"]["search_form"] = "high"
    else:
        search = {"mode": "query", "path": "/search", "query_param": "wd", "method": "GET"}
        report["warnings"].append("没有找到检索表单，已按常见约定（/search?wd=关键字）给出草稿，请自行修正")
        report["confidence"]["search_form"] = "low"

    # 结果列表容器：优先找常见 class
    list_container = None
    for selector in ("div.vodlist", "ul.vodlist", "div.list", "ul.list", "div.search-result",
                     "div.result-list", "div.module-list", "div.public-list", "ul.stui-vodlist",
                     "div.anime-list", "div.card-list"):
        node = soup.select_one(selector)
        if node is not None and node.find("a"):
            list_container = (selector, node)
            break
    if list_container:
        selector, node = list_container
        first_link = node.select_one("a[href]")
        item_selector = selector
        report["search"].update({
            "item": item_selector,
            "url": (("%s@href" % _selector_for(first_link)) if first_link else "a@href"),
            "name": "a@title",
            "cover": "img@data-src",
        })
        report["confidence"]["search_list"] = "high"
    else:
        report["search"].update({"item": "", "url": "a@href", "name": "a", "cover": "img@data-src"})
        report["warnings"].append("未能确认检索结果容器，请用浏览器“检查元素”后填写结果选择器")
        report["confidence"]["search_list"] = "low"

    report["search"] = {**search, **report["search"], "pages": 1}

    # ---------------------------------------------------------- 剧集分组
    anchors = soup.find_all("a", href=True)
    episode_links = []
    for index, node in enumerate(anchors):
        href = node["href"]
        absolute = urljoin(page_url, href)
        text = clean_text(node.get_text(" ", strip=True) or node.get("title") or "")
        if not (_EP_TEXT.search(text) or _EP_HREF.search(absolute)
                or _EP_TEXT.search(node.get("title") or "")):
            continue
        episode_links.append((node, absolute, text))

    report["candidates"] = _cluster_candidates(episode_links)
    report["single_link_hints"] = []

    # ---------------------------------------------------------- 默认剧集规则
    if report["candidates"]:
        top = report["candidates"][0]
        report["episodes"] = {
            "item": top["item"],
            "name": "",
            "url": top["link_selector"],
            "url_regex": top["url_regex"],
        }
        report["confidence"]["episodes"] = "high" if top["count"] >= 4 else "medium"
    else:
        report["episodes"] = {"item": "", "name": "", "url": "a@href",
                              "url_regex": r"/play/\d+/\d+"}
        if episode_links:
            report["warnings"].append(
                "页面上有单条剧集链接但没有成组出现。动漫站的剧集列表通常在详情页里，"
                "建议把某个动漫的详情页地址填到“抓取页面”再探测一次。")
        else:
            report["warnings"].append("没有找到剧集链接，请换一个详情页或手动填写剧集选择器")
        report["confidence"]["episodes"] = "low"

    # ---------------------------------------------------------- 视频规则
    video_patterns = [
        r'"url"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r"(https?://[^\"'\s<>]+\.m3u8[^\"'\s<>]*)",
        r'url:\s*[\'"](https?://[^\'"]+\.m3u8[^\'"]*)[\'"]',
        r'"(https?://[^"]+\.mp4[^"]*)"',
    ]
    if re.search(r"\.m3u8", html, re.I):
        report["warnings"].append("当前页面直接出现了 m3u8 地址，说明视频地址可能就在详情/播放页里")
    report["video"] = {"type": "auto", "patterns": video_patterns}

    # ---------------------------------------------------------- 汇总草稿
    config = {
        "base_url": base_url,
        "encoding": "",
        "search": report["search"],
        "episodes": report["episodes"],
        "video": report["video"],
    }
    report["config"] = config
    return report
