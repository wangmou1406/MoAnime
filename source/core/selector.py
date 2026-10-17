# -*- coding: utf-8 -*-
"""通用抽取引擎：CSS 选择器 / 正则 / JSON 路径，服务于“通用可配置源”。

选择器书写格式（字符串）：
    "h2.title a"                 取文本
    "h2.title a@href"            取属性
    "a@href|re:/(\\d+)\\.html"    取属性后再用正则提取第一个捕获组
    "re:<div class=\\"url\\">(.+?)</div>"   直接对整段内容跑正则
    "json:data.list[*].name"     JSON 路径（支持 * 通配与 [0] 下标）

返回：``select_all`` -> list，``select_one`` -> str。
"""
import json
import re
from html import unescape
from typing import List

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

from core.utils import absolute_url, clean_text

_RE_PREFIX = "re:"
_JSON_PREFIX = "json:"


def _soup(content: str):
    if BeautifulSoup is None:
        raise RuntimeError("缺少 beautifulsoup4 依赖")
    try:
        return BeautifulSoup(content, "lxml")
    except Exception:
        return BeautifulSoup(content, "html.parser")


def _json_walk(data, path: str):
    """按点分路径取 JSON 值，支持 * 通配与 [n] 下标。"""
    tokens = []
    for part in path.split("."):
        part = part.strip()
        if not part:
            continue
        while part:
            match = re.match(r"^([^\[\]]+)((\[[^\]]*\])*)", part)
            if not match:
                break
            tokens.append(match.group(1))
            indexes = re.findall(r"\[([^\]]*)\]", match.group(2) or "")
            tokens.extend(indexes)
            part = part[match.end():]

    current = [data]
    for token in tokens:
        nxt = []
        for node in current:
            if token == "*":
                if isinstance(node, dict):
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
                continue
            if token == "":
                continue
            if isinstance(node, dict):
                if token in node:
                    nxt.append(node[token])
            elif isinstance(node, list):
                try:
                    index = int(token)
                    if -len(node) <= index < len(node):
                        nxt.append(node[index])
                except ValueError:
                    if token == "length":
                        nxt.append(len(node))
        current = nxt
    if len(current) == 1:
        value = current[0]
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value
    out = []
    for value in current:
        if isinstance(value, (dict, list)):
            out.append(json.dumps(value, ensure_ascii=False))
        else:
            out.append(value)
    return out


def _apply_post(value, post: str):
    """post: "re:pattern" 取第一个捕获组或整段匹配。"""
    if not post:
        return value
    if post.startswith("re:"):
        match = re.search(post[3:], str(value), re.S)
        if not match:
            return ""
        return match.group(1) if match.groups() else match.group(0)
    if post.startswith("slice:"):
        spec = post[6:].split(":")
        try:
            if len(spec) == 1:
                return str(value)[int(spec[0]):]
            return str(value)[int(spec[0]):int(spec[1])]
        except Exception:
            return value
    if post.startswith("replace:"):
        raw = post[8:]
        if "=>" in raw:
            old, new = raw.split("=>", 1)
            return str(value).replace(old, new)
        return str(value)
    return value


def _split_selector(selector: str):
    selector = (selector or "").strip()
    attr = None
    if "@" in selector and not selector.startswith(_RE_PREFIX) and not selector.startswith(_JSON_PREFIX):
        head, _, tail = selector.rpartition("@")
        if head and " " not in tail and '"' not in tail and "'" not in tail:
            selector, attr = head.strip(), tail.strip()
    post = None
    if "|re:" in selector:
        selector, _, post = selector.partition("|re:")
        post = "re:" + post
    elif "|slice:" in selector:
        selector, _, post = selector.partition("|slice:")
        post = "slice:" + post
    elif "|replace:" in selector:
        selector, _, post = selector.partition("|replace:")
        post = "replace:" + post
    return selector.strip(), attr, post


def select_all(content: str, selector: str, base_url: str = "") -> List[str]:
    """按选择器抽取所有匹配值。"""
    if not selector or content is None:
        return []
    text = content if isinstance(content, str) else str(content)

    if selector.startswith(_RE_PREFIX):
        pattern = selector[len(_RE_PREFIX):]
        try:
            flags = re.S | re.I
            matches = re.findall(pattern, text, flags)
        except re.error:
            return []
        out = []
        for match in matches:
            value = match
            if isinstance(match, tuple):
                value = next((m for m in match if m), "")
            value = clean_text(value)
            if value:
                out.append(value)
        return out

    if selector.startswith(_JSON_PREFIX):
        path = selector[len(_JSON_PREFIX):]
        try:
            data = json.loads(text)
        except Exception:
            return []
        value = _json_walk(data, path)
        if value is None:
            return []
        if isinstance(value, list):
            return [clean_text(v) for v in value if str(v).strip()]
        return [clean_text(value)]

    css, attr, post = _split_selector(selector)
    if not css:
        return []
    try:
        soup = _soup(text)
        nodes = soup.select(css)
    except Exception:
        return []
    out = []
    for node in nodes:
        if attr:
            if attr == "text":
                value = node.get_text(" ", strip=True)
            elif attr == "html":
                value = node.decode_contents() if hasattr(node, "decode_contents") else str(node)
            else:
                value = node.get(attr, "")
                if not value and attr in ("src", "href", "data-src"):
                    value = node.get("data-src") or node.get("data-original") or node.get("data-echo") or ""
        else:
            value = node.get_text(" ", strip=True)
        value = clean_text(value)
        if post:
            value = _apply_post(value, post)
        if value:
            out.append(value)
    return out


def select_one(content: str, selector: str, default: str = "", base_url: str = "") -> str:
    if not selector:
        return default
    values = select_all(content, selector, base_url)
    return values[0] if values else default


def select_url(content: str, selector: str, base_url: str, default: str = "") -> str:
    value = select_one(content, selector, default, base_url)
    if not value:
        return default
    return absolute_url(base_url, value) if value else default


def extract_by_regex(content: str, patterns, base_url: str = "", attr: str = "") -> str:
    """按优先级依次尝试多个正则。patterns 可以是字符串或列表。

    attr 为空时返回捕获组/整段匹配文本；attr='url' 时把结果转成绝对地址。
    """
    if not patterns:
        return ""
    if isinstance(patterns, str):
        patterns = [patterns]
    text = content or ""
    for pattern in patterns:
        if not pattern:
            continue
        try:
            match = re.search(pattern, text, re.S | re.I)
        except re.error:
            continue
        if not match:
            continue
        value = match.group(1) if match.groups() else match.group(0)
        value = clean_text(unescape(value))
        if not value:
            continue
        if attr == "url":
            return absolute_url(base_url, value)
        return value
    return ""
