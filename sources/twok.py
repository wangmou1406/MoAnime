# -*- coding: utf-8 -*-
"""二矿动漫（2rk.cc）源插件。

站点结构（实测）::

    搜索    /search?wd=<关键词>            → 结果里是 /detail/<slug>?id=1
    详情    /detail/<slug>?id=1           → 页面里同时列出全集（?id=1,2,3...）
    取流    详情页的 HTML 里**明文**写着 m3u8：
              https://www.2rk.cc/video/<slug>/<集号>/<hash>.m3u8
            （播放器用的是 MSE，但地址在服务端渲染时就注入了，所以不需要浏览器）

为什么必须写专用插件
--------------------
它的「一集」靠查询参数区分：``/detail/<slug>?id=1`` 与 ``?id=2`` 路径部分相同。
通用源按 URL 去重时会把所有集并成一集，所以这里单独处理 ``?id=``。

视频是 HLS + AES-128（``#EXT-X-KEY:METHOD=AES-128,URI=".../saber"``），
下载引擎原生支持该加密，无需额外处理。
"""
import re
import time
from typing import List, Optional
from urllib.parse import quote, urljoin

from .base import NoResultError, SourceError, SourcePlugin, register

_M3U8 = re.compile(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', re.I)
_DETAIL = re.compile(r'/detail/([\w\-]+)(?:\?id=(\d+))?')
_ID_LINK = re.compile(r'/detail/[\w\-]+\?id=(\d+)')


@register("2rk", display_name="二矿动漫（HTML 站，无需浏览器）",
          description="直接解析网页 HTML 拿到 m3u8，纯 HTTP 即可，"
                      "不需要浏览器取流。剧集用 ?id=N 区分。")
class TwoRkSource(SourcePlugin):
    type_name = "2rk"
    display_name = "二矿动漫"
    description = "HTML 站，m3u8 明文在页面里，纯 HTTP"
    config_schema = [
        {"key": "base_url", "label": "站点地址", "type": "text",
         "default": "https://www.2rk.cc", "placeholder": "https://www.2rk.cc"},
    ]

    # ------------------------------------------------------------ 内部
    def _get(self, url: str, timeout: int = 25) -> str:
        """抓页面。

        这个站有个特点：详情页会声明一个比实际发送更大的 Content-Length
        （实测声明 147KB、实际只发 65KB），requests 的 resp.text 会因此抛
        ChunkedEncodingError / IncompleteRead。但**已收到的部分完全够用**
        （剧集列表与 m3u8 都在前 65KB 里），所以这里绕过长度校验，读多少收多少。
        """
        last = None
        for attempt in range(2):
            resp = None
            try:
                resp = self.session.get(url, timeout=timeout, verify=False, stream=True)
                resp.raise_for_status()
                try:
                    resp.raw.decode_content = False
                except Exception:
                    pass
                chunks = []
                try:
                    for chunk in resp.raw.stream(16384, decode_content=False):
                        if chunk:
                            chunks.append(chunk)
                except Exception as exc:
                    if chunks:
                        self.log("响应不完整（%s），使用已收到的 %d 字节"
                                 % (type(exc).__name__, sum(len(c) for c in chunks)),
                                 "warning")
                    else:
                        raise
                raw = b"".join(chunks)
                if len(raw) > 0x1000 and raw[:2] == b"\x1f\x8b":
                    import gzip
                    raw = gzip.decompress(raw)
                text = raw.decode("utf-8", "ignore")
                if text.strip():
                    return text
                last = "空响应"
            except Exception as exc:
                last = exc
                if attempt == 0:
                    time.sleep(0.8)
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
        raise SourceError("抓取页面失败：%s" % str(last)[:80])

    @staticmethod
    def _clean_title(text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text or "")
        for a, b in (("&#8211;", "-"), ("&amp;", "&"), ("&quot;", '"'),
                     ("&#039;", "'"), ("&nbsp;", " ")):
            text = text.replace(a, b)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _slug_of(anime_url: str) -> str:
        m = _DETAIL.search(anime_url or "")
        return m.group(1) if m else ""

    # ------------------------------------------------------------ 检索
    def search(self, keyword: str, page: int = 1) -> List[dict]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        base = self.base_url or "https://www.2rk.cc"
        # 注意：搜索参数是 w（不是 wd），实测 /search?wd= 会返回空结果页
        html = self._get("%s/search?w=%s" % (base, quote(keyword)))

        results = []
        seen = set()
        # 结果卡片：<h2><a href="/detail/<slug>?id=1">标题</a></h2>
        # href 带 ?id=1，正则要允许查询参数
        for m in re.finditer(r'<a[^>]+href="(/detail/[\w\-]+)(?:\?id=\d+)?"[^>]*>(.*?)</a>',
                             html, re.S):
            href, raw = m.group(1), m.group(2)
            name = self._clean_title(raw)
            slug = href.rsplit("/", 1)[-1]
            if not name or not slug or slug in seen:
                continue
            seen.add(slug)
            results.append(self.normalize_search_item({
                "name": name,
                "url": "2rk://%s" % slug,
                "cover": "",
                "update_info": "",
                "slug": slug,
                "playable": True,
            }))
        if not results:
            raise NoResultError("二矿动漫没有匹配「%s」的内容" % keyword)
        self.log("关键字 %s 命中 %d 条" % (keyword, len(results)))
        return results

    # ------------------------------------------------------------ 剧集
    def episodes(self, anime_url: str, anime_name: str = "") -> List[dict]:
        base = self.base_url or "https://www.2rk.cc"
        m = re.match(r"^2rk://([\w\-]+)$", anime_url or "")
        slug = m.group(1) if m else self._slug_of(anime_url)
        if not slug:
            raise SourceError("剧集地址格式不对（应为 2rk://<slug>）")

        # 详情页同时列出全集（?id=1..N），取一次就够
        html = self._get("%s/detail/%s" % (base, slug))

        # 剧集名映射：抓 <a href="/detail/slug?id=N">第NN话</a>
        names = {}
        for m2 in re.finditer(
                r'<a[^>]+href="/detail/%s\?id=(\d+)"[^>]*>(.*?)</a>'
                % re.escape(slug), html, re.S):
            num, raw = m2.group(1), m2.group(2)
            label = self._clean_title(raw)
            if num not in names and label:
                names[num] = label

        ids = sorted({int(x) for x in _ID_LINK.findall(html)})
        if not ids:
            raise SourceError("详情页没有解析到剧集（站点结构可能改了）")

        out = []
        for i in ids:
            out.append(self.normalize_episode({
                "name": names.get(str(i)) or "第%d集" % i,
                "url": "%s/detail/%s?id=%d" % (base, slug, i),
            }))
        self.log("%s 解析到 %d 集" % (slug, len(out)))
        return out

    # ------------------------------------------------------------ 取流
    def resolve_video_url(self, episode_url: str, anime_url: str = "",
                          episode_name: str = "") -> Optional[str]:
        """详情页 HTML 里明文写着 m3u8，直接正则取（不需要浏览器）。"""
        html = self._get(episode_url)
        hits = _M3U8.findall(html)
        if hits:
            return hits[0]
        # 兜底：万一站点改成 JS 注入，交给浏览器嗅探（勾选能力即可用）
        from core import browser_resolver
        if not browser_resolver.available()["usable"]:
            raise SourceError("页面里没有 m3u8，且浏览器取流不可用。"
                              "请在「设置 → 浏览器取流」检查。")
        result = browser_resolver.resolve_with_browser(episode_url, wait_ms=12000)
        if not result.get("ok"):
            raise SourceError("浏览器也没截获到视频地址：%s"
                              % (result.get("error") or "未知"))
        headers = {}
        if result.get("cookies"):
            headers["Cookie"] = result["cookies"]
        self.last_resolve_meta = {"referer": result.get("referer") or episode_url,
                                  "browser_resolved": True}
        if headers:
            self.last_resolve_meta["headers"] = headers
        return result["video"]

    def test(self) -> dict:
        keyword = self.cfg("test_keyword") or "海贼"
        results = self.search(keyword, 1)
        return {"ok": bool(results), "count": len(results), "keyword": keyword,
                "sample": results[:3]}
