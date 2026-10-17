# -*- coding: utf-8 -*-
"""通用小工具：时间、文本清洗、URL 处理、HTTP 会话构建。"""
import hashlib
import os
import re
import time
import unicodedata
from html import unescape
from urllib.parse import urljoin, urlparse, urlencode, parse_qs

import requests
from requests.adapters import HTTPAdapter

from .config import config

_ILLEGAL_FS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")
_EP_NUM_PATTERNS = [
    re.compile(r"第\s*(\d+(?:\.\d+)?)\s*[集话話]"),
    re.compile(r"[Ee][Pp]?\.?\s*(\d+(?:\.\d+)?)"),
    re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*[vV]?\s*\]"),
    re.compile(r"(\d+(?:\.\d+)?)\s*[集话話]"),
    re.compile(r"^(\d+(?:\.\d+)?)$"),
]


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return time.strftime(fmt)


def clean_text(value) -> str:
    """去除 HTML 实体、多余空白，得到干净标题。"""
    if value is None:
        return ""
    text = unescape(str(value))
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    return _MULTI_SPACE.sub(" ", text).strip()


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """把任意动漫/剧集名转换为安全的文件名片段。"""
    text = clean_text(name)
    text = _ILLEGAL_FS.sub("_", text)
    text = text.strip(" .")
    text = _MULTI_SPACE.sub(" ", text)
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .")
    return text or "untitled"


def episode_number(text: str, default: float = 0.0) -> float:
    """从剧集标题里解析集数，失败返回 default。"""
    if text is None:
        return default
    value = clean_text(text)
    if not value:
        return default
    for pattern in _EP_NUM_PATTERNS:
        match = pattern.search(value)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                continue
    return default


def episode_tag(text: str, default: float = 0.0) -> str:
    """把集数格式化为 第01集 这样的标签。"""
    number = episode_number(text, default)
    if number <= 0:
        return sanitize_filename(text, 60)
    if abs(number - round(number)) < 1e-6:
        return "第%02d集" % int(round(number))
    return "第%s集" % ("%g" % number)


def _origin(url: str) -> str:
    parts = urlparse(url)
    if not parts.scheme:
        return "/"
    return "%s://%s/" % (parts.scheme, parts.netloc)


def _base_segments(base: str):
    return [seg for seg in urlparse(base or "").path.split("/") if seg]


def _site_subpath(base: str) -> str:
    """提取站点部署的子路径（取第一段），如 https://a.com/demo/anime/1 -> /demo。"""
    segments = [seg for seg in urlparse(base or "").path.split("/") if seg]
    return "/" + segments[0] if segments else ""


def absolute_url(base: str, url: str) -> str:
    """把可能是相对路径的链接补成绝对地址。

    站点部署在子路径（如 https://a.com/demo）时的兼容规则：
      * ``/search``       -> https://a.com/demo/search          （站点根相对 + 子路径）
      * ``/demo/anime/1`` -> https://a.com/demo/anime/1        （避免 /demo/demo/anime/1）
      * ``play/2``        -> 按 RFC 相对当前页面解析
      * ``?page=2``       -> 保留当前地址，只替换查询串
    """
    if not url:
        return ""
    url = clean_text(url)
    if url.startswith("//"):
        scheme = urlparse(base).scheme or "https"
        return "%s:%s" % (scheme, url)
    if url.startswith(("http://", "https://")):
        return url
    if not base:
        return url

    sub = _site_subpath(base)                      # 站点部署子路径，例如 /demo

    if url.startswith("#"):
        return urljoin(base, url)

    if url.startswith("/"):
        path = urlparse(url).path
        if sub and (path == sub or path.startswith(sub + "/")):
            path = path[len(sub):] or "/"          # 链接里重复写了子路径
        resolved = urljoin(_origin(base), sub + path)
    else:
        # 其他相对路径一律按 RFC 相对当前页面解析（与浏览器一致）
        resolved = urljoin(base, url)

    # 兜底：子路径被重复叠加时去掉一层（/demo/demo/xxx -> /demo/xxx）
    if sub:
        parts = urlparse(resolved)
        segments = [seg for seg in parts.path.split("/") if seg]
        if parts.path.startswith(sub + "/") and len(segments) > 1 \
                and segments[1] == sub.lstrip("/"):
            resolved = parts._replace(path=parts.path[len(sub):]).geturl()
    return resolved


def same_host(url_a: str, url_b: str) -> bool:
    try:
        return urlparse(url_a).netloc == urlparse(url_b).netloc
    except Exception:
        return False


def dedupe(items, key=None):
    seen = set()
    out = []
    for item in items:
        marker = key(item) if key else item
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def stable_id(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def build_session(user_agent: str = None, proxy: str = None, retries: int = 3) -> requests.Session:
    """构造带重试、代理、UA 的 requests 会话。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent or config.get("user_agent"),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    adapter = HTTPAdapter(
        pool_connections=32, pool_maxsize=32, max_retries=0,
        pool_block=False,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    proxy = proxy if proxy is not None else config.get("proxy")
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def human_size(num_bytes) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return ("%.1f %s" % (size, unit)) if unit != "B" else ("%d B" % size)
        size /= 1024.0
    return "-"


def human_speed(bps) -> str:
    if not bps:
        return "-"
    return human_size(bps) + "/s"


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def merge_query(url: str, extra: dict = None, override: dict = None) -> str:
    """向 URL 追加/覆盖查询参数（用于分页）。"""
    if not extra and not override:
        return url
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    for key, value in (extra or {}).items():
        query.setdefault(key, [str(value)])
    for key, value in (override or {}).items():
        query[key] = [str(value)]
    flat = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in query.items()}
    return parts._replace(query=urlencode(flat, doseq=True)).geturl()
