# -*- coding: utf-8 -*-
"""MacCMS（苹果 CMS v10）资源站接口源。

这类接口是给播放器和采集器用的，稳定、无需逆向，返回的 ``vod_play_url``
里直接包含每一集的 m3u8 地址，因此「检索 → 剧集 → 下载」可以全链路直连。

**支持两种响应格式，自动识别**：

* JSON（默认路径，或 ``/at/json``）—— 直接取 ``list[].vod_*`` 字段；
* XML（``/at/xml``，实为标准 RSS）—— 由 :func:`parse_maccms_xml` 转成与
  JSON 版**同构**的字典，上层逻辑完全不用关心来源格式。

同一个站常常两种都提供，例如 ``cj.ffzyapi.com`` 的 JSON 与 ``/at/xml/``
返回的是同一份数据（实测番剧名、集数、首集地址完全一致）。
JSONP（``callback({...})``）也一并兼容。

配置::

    {
      "base_url": "https://cj.lziapi.com",           # 主接口（可只填一个）
      "apis": [                                       # 多线路，按顺序自动切换
        {"name": "量子资源", "api": "https://cj.lziapi.com/api.php/provide/vod/"},
        {"name": "暴风资源", "api": "https://bfzyapi.com/api.php/provide/vod/"}
      ],
      "api": "https://cj.lziapi.com/api.php/provide/vod/",   # 兼容单接口写法
      "request_timeout": 20,
      "prefer_hd": true
    }
"""
import json
import re
from typing import List

from core.config import config as app_config
from core.utils import build_session, clean_text, dedupe, episode_number

from .base import (DEFAULT_TEST_KEYWORD, NoResultError, SourceError,
                   SourcePlugin, UnresolvableError, register)

_M3U8 = re.compile(r"\.m3u8", re.I)
_DIRECT_MEDIA = re.compile(r"\.(m3u8|mp4|mkv|flv|ts|m4v|mov|webm)(\?|$)", re.I)

# XML 字段 → JSON 字段（MacCMS 的 at/xml 返回 RSS，标签名与 JSON 键不同）
_XML_FIELD_MAP = {
    "id": "vod_id", "name": "vod_name", "type": "type_name", "pic": "vod_pic",
    "note": "vod_remarks", "year": "vod_year", "last": "vod_time",
    "actor": "vod_actor", "director": "vod_director", "area": "vod_area",
    "lang": "vod_lang", "state": "vod_state", "subname": "vod_sub",
    "des": "vod_content", "score": "vod_score", "hits": "vod_hits",
    "serial": "vod_serial", "letter": "vod_letter", "tid": "type_id",
}


def _unescape_cdata(text: str) -> str:
    """去掉 CDATA 包裹标记并还原 HTML 实体。

    注意：真实接口里 CDATA 标记是**留在文本里**的（例如
    ``<dd flag="x"><![CDATA[第01集$url#第02集$url]]></dd>``），
    如果只做实体还原而不剥掉 ``<![CDATA[`` / ``]]>``，
    被 ``#`` 切开后第一集名会变成 ``<![CDATA[第01集``——这是实测踩过的坑。
    """
    if not text:
        return ""
    text = text.strip()
    # 反复剥，兼容个别站的多层嵌套
    for _ in range(3):
        before = text
        if text.startswith("<![CDATA["):
            text = text[len("<![CDATA["):]
        if text.endswith("]]>"):
            text = text[:-len("]]>")]
        text = text.strip()
        if text == before:
            break
    # 中间的残余标记也清掉（少见，但清了无害）
    text = text.replace("<![CDATA[", "").replace("]]>", "")
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
                         ("&quot;", '"'), ("&#039;", "'"), ("&apos;", "'")):
        text = text.replace(entity, char)
    return text


def parse_maccms_xml(text: str) -> dict:
    """把 MacCMS 的 ``at/xml``（RSS 格式）响应转成与 JSON 版**同构**的字典。

    这样上层的 search / episodes / playlist_options 一行都不用改。

    XML 长这样::

        <rss version="5.1"><list page="1" recordcount="20">
          <video>
            <id>21681</id><name><![CDATA[海贼王]]></name><type>日韩动漫</type>
            <pic>https://...jpg</pic><year>1999</year>
            <note><![CDATA[更新至第1180集]]></note>
            <dl>
              <dd flag="feifan"><![CDATA[第01集$http://a/1.m3u8#第02集$http://a/2.m3u8]]></dd>
              <dd flag="ffm3u8"><![CDATA[...]]></dd>
            </dl>
          </video>
        </list></rss>

    关键映射：
      * ``<dl>`` 下每个 ``<dd flag="X">`` = 一条线路 → 拼成 ``vod_play_url``
        （多线路用 ``$$$`` 连接），``flag`` 拼接成 ``vod_play_from``；
      * 其余标签按 :data:`_XML_FIELD_MAP` 改名成 ``vod_*``。
    """
    if not text:
        return {"list": []}

    # 通用取值：兼容 <tag>文本</tag> 与 <tag><![CDATA[文本]]></tag>
    def _tag_value(block: str, tag: str) -> str:
        m = re.search(r"<%s>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</%s>" % (tag, tag),
                      block, re.S | re.I)
        if not m:
            return ""
        return _unescape_cdata(m.group(1) if m.group(1) is not None else (m.group(2) or ""))

    items = []
    for block in re.findall(r"<video>(.*?)</video>", text, re.S | re.I):
        item = {}
        # 1) 普通字段 → 改成 JSON 版的 vod_* 键名
        for tag, key in _XML_FIELD_MAP.items():
            value = _tag_value(block, tag)
            if value:
                item[key] = value

        # 2) 剧集：<dl><dd flag="线路名"><![CDATA[第01集$url#...]]></dd></dl>
        dl = re.search(r"<dl>(.*?)</dl>", block, re.S | re.I)
        if dl:
            inner = dl.group(1)
            groups = [_unescape_cdata(x)
                      for x in re.findall(r"<dd[^>]*>(.*?)</dd>", inner, re.S | re.I)]
            groups = [g for g in groups if g]
            flags = re.findall(r'<dd[^>]*flag=["\']([^"\']*)["\']', inner, re.I)
            if groups:
                item["vod_play_url"] = "$$$".join(groups)
                item["vod_play_from"] = "$$$".join(flags) if flags else ""
            else:
                # 兜底：<dl> 里直接是文本，没有 <dd>
                plain = _unescape_cdata(re.sub(r"<[^>]+>", "", inner))
                if "$" in plain:
                    item["vod_play_url"] = plain

        if item.get("vod_name"):
            items.append(item)

    page_info = {}
    m = re.search(r"<list([^>]*)>", text, re.I)
    if m:
        for key, value in re.findall(r'(\w+)=["\']([^"\']*)["\']', m.group(1)):
            page_info[key.lower()] = value

    return {"list": items, "page": page_info.get("page"),
            "pagecount": page_info.get("pagecount"),
            "total": page_info.get("recordcount")}


def looks_like_xml(text: str) -> bool:
    """判断响应是不是 XML（而不是被劫持的 HTML 或 JSON）。"""
    head = (text or "").lstrip()[:200].lower()
    return head.startswith("<?xml") or head.startswith("<rss") or "<rss" in head



def _is_direct_media(url: str) -> bool:
    """剧集地址是否本身就是可下载的媒体文件（而不是 share/play 分享页）。"""
    return bool(_DIRECT_MEDIA.search((url or "").split("#")[0]))


def hls_duration(url: str, timeout: int = 20) -> float:
    """读取 m3u8 的总时长（累加 #EXTINF）。

    用途：同一集在不同源上的时长应当接近；某个源明显更长（例如多了 30 秒）
    通常意味着那一段是**插播的广告**（盗版压制常见的推广贴片）。
    """
    from core.utils import build_session
    session = build_session()
    session.verify = False
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        text = resp.text or ""
    except Exception as exc:  # noqa: BLE001
        raise SourceError("读取播放列表失败：%s" % exc) from exc

    # 多清晰度：取第一个变体再看子列表
    if "#EXT-X-STREAM-INF" in text:
        variants = re.findall(r"#EXT-X-STREAM-INF:[^\n]+\n([^\n#]+)", text)
        if variants:
            from urllib.parse import urljoin
            sub = urljoin(url, variants[0].strip())
            try:
                sub_text = session.get(sub, timeout=timeout).text
                if "#EXTINF" in sub_text:
                    text = sub_text
            except Exception:  # noqa: BLE001
                pass

    total = 0.0
    for value in re.findall(r"#EXTINF:\s*([\d.]+)", text):
        try:
            total += float(value)
        except ValueError:
            continue
    return round(total, 1)


def normalize_api(api: str) -> str:
    api = (api or "").strip().rstrip("/")
    if not api:
        return ""
    if not api.startswith("http"):
        api = "https://" + api
    if "provide/vod" not in api and "api.php" not in api:
        api += "/api.php/provide/vod"
    return api + "/"


def anime_ref(api: str, vod_id: str) -> str:
    """把接口与条目 ID 打包成一个可用作 anime_url 的引用（避免双斜杠）。"""
    return "maccms://%s/%s" % (normalize_api(api), vod_id)


def parse_episodes_of_line(line: str) -> List[dict]:
    """解析单条线路的剧集片段（``第01集$url#第02集$url``）。"""
    episodes = []
    for index, chunk in enumerate((line or "").split("#"), start=1):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "$" in chunk:
            name, url = chunk.split("$", 1)
        else:
            name, url = "", chunk
        name = clean_text(name) or "第%d集" % index
        url = url.strip()
        if not url:
            continue
        episodes.append({"name": name, "url": url, "episode": episode_number(name, index)})
    return episodes


def parse_play_url(play_url: str) -> List[dict]:
    """把 MacCMS 的 vod_play_url 解析成剧集列表。

    格式：``第01集$http://a/1.m3u8#第02集$http://a/2.m3u8``，多线路用 ``$$$`` 分隔。

    很多资源站会给多条线路，其中**前几条常常是分享页/播放页，后面才是直链 m3u8**。
    这里优先返回「全部是可下载直链」的那条线路，避免用户点到只能看不能下的地址。
    """
    if not play_url:
        return []
    groups = [g for g in play_url.split("$$$") if g.strip()]
    if not groups:
        return []
    best = None
    best_score = -1.0
    for group in groups:
        episodes = parse_episodes_of_line(group)
        if not episodes:
            continue
        direct = sum(1 for ep in episodes if _is_direct_media(ep["url"]))
        score = direct / float(len(episodes))
        if score > best_score:
            best_score = score
            best = episodes
    return best or []


def play_lines(play_url: str, play_from: str = "") -> List[dict]:
    """按线路拆分（部分站点提供多条线路可切换）。"""
    names = [clean_text(n) for n in (play_from or "").split("$$$")]
    out = []
    for index, group in enumerate((play_url or "").split("$$$")):
        label = names[index] if index < len(names) and names[index] else "线路%d" % (index + 1)
        out.append({"name": label, "url": group})
    return out


@register("maccms", display_name="MacCMS 资源站接口（推荐）",
          description="苹果 CMS v10 资源接口，支持 JSON 与 XML(/at/xml) 两种格式，"
                      "自动识别，直接返回剧集与可播放 m3u8，支持多接口自动切换。",
          config_schema=[
              {"key": "api", "label": "主接口地址", "type": "text",
               "placeholder": "https://cj.lziapi.com/api.php/provide/vod/"},
              {"key": "apis", "label": "备用接口列表（JSON 数组）", "type": "json",
               "placeholder": '[{"name": "暴风资源", "api": "https://bfzyapi.com/api.php/provide/vod/"}]'},
          ])
class MacCmsSource(SourcePlugin):
    type_name = "maccms"
    display_name = "MacCMS 资源站接口"

    # ------------------------------------------------------------ 接口列表
    def _apis(self) -> List[dict]:
        raw = []
        single = self.cfg("api") or self.base_url
        if single:
            raw.append({"name": self.cfg("api_name") or "主接口", "api": single})
        extra = self.cfg("apis") or []
        if isinstance(extra, dict):
            extra = [extra]
        for item in extra:
            if isinstance(item, str):
                raw.append({"name": "", "api": item})
            elif isinstance(item, dict):
                raw.append({"name": item.get("name") or "", "api": item.get("api") or item.get("url")})
        result, seen = [], set()
        for item in raw:
            api = normalize_api(item.get("api"))
            if not api or api in seen:
                continue
            seen.add(api)
            result.append({"name": item.get("name") or api.split("/")[2], "api": api})
        if not result:
            raise SourceError("该源没有配置任何 MacCMS 接口地址")
        return result

    # ------------------------------------------------------------ 请求
    def _http(self):
        """返回带重试的会话（沿用基类的 session 属性缓存）。"""
        return self.session

    def _request(self, api: str, params: dict):
        timeout = int(self.cfg("request_timeout") or app_config.get("source_timeout", 20))
        headers = {"User-Agent": app_config.get("user_agent"),
                   "Accept": "application/json, text/plain, */*"}
        headers.update(self.cfg("headers") or {})
        resp = self._http().get(api, params=params, headers=headers, timeout=timeout,
                                verify=bool(app_config.get("verify_ssl", False)))
        resp.raise_for_status()
        text = (resp.text or "").strip()
        if not text:
            raise SourceError("接口返回空内容")

        # 自动识别响应格式：苹果 CMS 的接口既有 JSON（默认 / 或 at/json），
        # 也有 XML（/at/xml）。两者数据结构一一对应，这里统一转成 JSON 形状，
        # 上层逻辑就不用关心来源格式了。
        if looks_like_xml(text):
            parsed = parse_maccms_xml(text)
            if parsed.get("list"):
                return parsed
            raise SourceError("XML 接口没有解析出条目（结构可能不同）")

        try:
            data = resp.json()
        except ValueError as exc:
            # 有些站用 JSONP 包裹：callback({...})
            m = re.match(r"^[\w$]+\((.*)\)\s*;?\s*$", text, re.S)
            if m:
                try:
                    return json.loads(m.group(1))
                except ValueError:
                    pass
            raise SourceError("接口返回的不是 JSON 也不是 XML"
                              "（可能被劫持或接口已失效）") from exc
        # JSONP 被 requests 解析成字符串的情况
        if isinstance(data, str):
            m = re.match(r"^[\w$]+\((.*)\)\s*;?\s*$", data.strip(), re.S)
            if m:
                try:
                    return json.loads(m.group(1))
                except ValueError:
                    pass
            raise SourceError("接口返回的是文本而非结构化数据（可能被劫持）")
        return data

    def _query(self, params: dict, need_episodes: bool = True):
        """依次尝试各接口，返回 (数据, 命中的接口)。"""
        errors = []
        empty_only = True
        for entry in self._apis():
            try:
                data = self._request(entry["api"], params)
            except Exception as exc:  # noqa: BLE001
                errors.append("%s: %s" % (entry["name"], str(exc)[:60]))
                empty_only = False
                continue
            items = data.get("list") or []
            if not items:
                errors.append("%s: 无结果" % entry["name"])
                continue
            if need_episodes and not any(item.get("vod_play_url") for item in items):
                errors.append("%s: 缺少剧集数据" % entry["name"])
                empty_only = False
                continue
            return data, entry
        if empty_only:
            # 所有接口都访问成功、只是没有这个关键字的内容 —— 这不算故障
            raise NoResultError("该源没有匹配「%s」的内容" % (params.get("wd") or params.get("ids") or ""))
        raise SourceError("所有接口都没有返回可用数据（%s）" % "；".join(errors[:4]))

    # ------------------------------------------------------------ 检索
    def search(self, keyword: str, page: int = 1) -> List[dict]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        data, entry = self._query({"ac": "detail", "wd": keyword, "pg": page or 1})
        results = []
        for item in data.get("list") or []:
            name = clean_text(item.get("vod_name"))
            if not name:
                continue
            vod_id = str(item.get("vod_id") or "")
            episodes = parse_play_url(item.get("vod_play_url") or "")
            results.append(self.normalize_search_item({
                "name": name,
                "url": anime_ref(entry["api"], vod_id),
                "cover": item.get("vod_pic") or "",
                "update_info": clean_text(item.get("vod_remarks") or item.get("vod_year") or ""),
                "vod_id": vod_id,
                "api": entry["api"],
                "api_name": entry["name"],
                "episode_count": len(episodes),
                "type_name": clean_text(item.get("type_name") or ""),
                # 只有真正给出 m3u8/mp4 直链的源才算「直链可下载」；
                # 分享页（share/xxx、play/xxx）解析不出来，如实标记为 False
                "playable": any(_is_direct_media(ep["url"]) for ep in episodes[:5]),
            }))
        self.log("关键字 %s 命中 %d 条（接口：%s）" % (keyword, len(results), entry["name"]))
        return dedupe(results, key=lambda item: item["url"])

    # ------------------------------------------------------------ 剧集
    @staticmethod
    def _split_ref(anime_url: str):
        match = re.match(r"^maccms://(.+?)/([^/]+)$", anime_url or "")
        if match:
            return match.group(1), match.group(2)
        # 兼容直接传入 vod_id
        return "", anime_url

    def episodes(self, anime_url: str, anime_name: str = "") -> List[dict]:
        api, vod_id = self._split_ref(anime_url)
        params = {"ac": "detail", "ids": vod_id}
        if not vod_id:
            raise SourceError("无效的动漫标识：%s" % anime_url)
        data = None
        if api:
            try:
                data = self._request(normalize_api(api), params)
            except Exception as exc:  # noqa: BLE001
                self.log("指定接口取剧集失败，改用其他接口：%s" % exc)
        if data is None:
            data, _entry = self._query(params)
        items = data.get("list") or []
        if not items:
            raise SourceError("接口没有返回该动漫的详情")
        item = items[0]
        episodes = parse_play_url(item.get("vod_play_url") or "")
        if not episodes:
            raise SourceError("该条目没有可用的播放地址（可能是预告或已下架）")
        self.log("%s 解析到 %d 集" % (item.get("vod_name") or anime_name, len(episodes)))
        return [self.normalize_episode(ep) for ep in episodes]

    def playlist_options(self, episode_url: str, anime_url: str = "") -> List[dict]:
        """多线路站点可切换线路。"""
        api, vod_id = self._split_ref(anime_url)
        if not vod_id:
            return super().playlist_options(episode_url, anime_url)
        try:
            data, _entry = self._query({"ac": "detail", "ids": vod_id})
        except SourceError:
            return super().playlist_options(episode_url, anime_url)
        items = data.get("list") or []
        if not items:
            return super().playlist_options(episode_url, anime_url)
        item = items[0]
        options = []
        for line in play_lines(item.get("vod_play_url") or "", item.get("vod_play_from") or ""):
            episodes = parse_play_url(line["url"])
            if episodes:
                options.append({"name": line["name"], "url": episodes[0]["url"],
                                "count": len(episodes)})
        return options or super().playlist_options(episode_url, anime_url)

    # ------------------------------------------------------------ 视频地址
    def resolve_video_url(self, episode_url: str, anime_url: str = "",
                          episode_name: str = "") -> str:
        url = (episode_url or "").strip()
        if not url:
            raise SourceError("剧集地址为空")
        if _M3U8.search(url) or url.lower().split("?")[0].endswith((".mp4", ".flv", ".ts", ".mkv")):
            return url
        # 非直链（分享页）：尝试从页面里再抠一次
        try:
            resp = self._http().get(url, headers={"User-Agent": app_config.get("user_agent")},
                                    timeout=int(self.cfg("request_timeout") or 20),
                                    verify=bool(app_config.get("verify_ssl", False)))
            found = re.search(r'(https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*)', resp.text)
            if found:
                return found.group(1)
        except Exception:  # noqa: BLE001
            pass
        raise UnresolvableError(
            "该资源站给的是分享页，页面里解析不出 m3u8/mp4 地址（%s）" % url[:70])

    # ------------------------------------------------------------ 自检
    def test(self) -> dict:
        keyword = self.cfg("test_keyword") or DEFAULT_TEST_KEYWORD
        report = {"keyword": keyword, "apis": []}
        for entry in self._apis():
            row = {"name": entry["name"], "api": entry["api"], "ok": False}
            try:
                data = self._request(entry["api"], {"ac": "detail", "wd": keyword})
                items = data.get("list") or []
                episodes = parse_play_url((items[0].get("vod_play_url") if items else "") or "")
                row.update({
                    "ok": bool(items and episodes),
                    "count": len(items),
                    "episodes": len(episodes),
                    "m3u8": bool(episodes and _M3U8.search(episodes[0]["url"])),
                })
            except Exception as exc:  # noqa: BLE001
                row["error"] = str(exc)[:120]
            report["apis"].append(row)
        report["ok"] = any(row["ok"] and row.get("m3u8") for row in report["apis"])
        return report
