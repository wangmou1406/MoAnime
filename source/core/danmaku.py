# -*- coding: utf-8 -*-
"""弹幕下载：从视频网站把某一集的弹幕抓下来，存成播放器能读的文件。

现在只有 B 站（哔哩哔哩）能自动化
    弹幕接口是公开的、不需要登录：给定 cid 就能拿到整集的弹幕。
    番剧名 → 搜季 → 每集 cid，这条链路和「校验集数」用的是同一套匹配逻辑。

其它平台（实测结论，不是没写）
    爱奇艺：老的 ``cmts.iqiyi.com/bullet/<id>/<x>/<tvId>/<tvId>_<时长>_<段>.z``
            已经下线，各种拼接法都返回 404；现有接口要登录态和签名。
    腾讯视频：``dm.video.qq.com/barrage/...`` 需要一个 11 位 vid，而腾讯的搜索页是
            前端渲染的，静态拿不到 vid。
    优酷：同样要 mtop 签名。

    所以 ``PROVIDERS`` 里留了注册位：以后哪家放出可用接口，加一个函数就能接上，
    不用动下载流程。

存成什么
    ``.xml``：B 站原生格式，弹弹play / PotPlayer / mpv 插件都认，通用性最好（默认）。
    ``.ass``：想直接把弹幕压进视频时用。
    文件名跟视频同名：``第01集.mp4`` → ``第01集.xml`` / ``第01集.danmaku.ass``，
    播放器打开视频时会自动找同名字幕文件。
"""
import html
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import bili
from .config import config
from .logging_setup import get_logger

logger = get_logger("moanime.danmaku")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
API_LIST = "https://api.bilibili.com/x/v1/dm/list.so"     # 老接口：整集一次给完
API_SEG = "https://api.bilibili.com/x/v2/dm/web/seg.so"    # 新接口：按 360 秒分段
MAX_SEGMENTS = 30          # 一集最多拉多少个分段（360s × 30 = 3 小时）
TIMEOUT = 20
_FORMATS = ("xml", "ass", "both")

_lock = threading.Lock()
_last_call = [0.0]
_MIN_INTERVAL = 0.35       # 别把 B 站惹毛

# provider 注册表：{id: {name, note, auto}}；auto=True 表示能按番剧名自动匹配
PROVIDERS = [
    {"id": "bilibili", "name": "哔哩哔哩", "auto": True,
     "note": "按番剧名自动匹配，每集弹幕完整"},
    {"id": "service", "name": "外部弹幕服务", "auto": True,
     "note": "配好 danmu_api 地址后可用，一次覆盖爱优腾芒等平台"},
    {"id": "iqiyi", "name": "爱奇艺", "auto": False, "note": "要 Brotli + protobuf，未接入"},
    {"id": "tencent", "name": "腾讯视频", "auto": False, "note": "要专辑→剧集映射，未接入"},
    {"id": "youku", "name": "优酷", "auto": False, "note": "要 mtop 签名，未接入"},
]
DEFAULT_PROVIDER = "bilibili"


class DanmakuError(Exception):
    """弹幕抓取失败（对外显示这个提示）。"""


def list_providers() -> list:
    """能自动抓的平台；外部服务没配地址时不算在内。"""
    out = []
    for item in PROVIDERS:
        if not item.get("auto"):
            continue
        if item["id"] == "service" and not service_base():
            continue
        out.append(dict(item))
    return out


# ---------------------------------------------------------------- 外部弹幕服务

def service_base() -> str:
    """外部弹幕服务的根地址（含 token 路径）；没配置返回空串。"""
    raw = (config.get("danmaku_service") or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    return raw.rstrip("/")


def _service_http(path: str, params: dict = None, timeout: int = 20) -> dict:
    base = service_base()
    if not base:
        raise DanmakuError("没有配置外部弹幕服务地址")
    url = base + path
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        raise DanmakuError("外部弹幕服务连不上：%s" % str(exc)[:80]) from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise DanmakuError("外部弹幕服务返回的不是 JSON") from exc


def service_test(url: str = None, keyword: str = "测试") -> dict:
    """测外部服务通不通（设置页的「测试」按钮用）。临时改配置，测完还原。"""
    old = config.get("danmaku_service")
    if url is not None:
        config.set("danmaku_service", url)
    try:
        base = service_base()
        if not base:
            return {"ok": False, "error": "没有填地址"}
        started = time.time()
        data = _service_http("/api/v2/search/episodes", {"anime": keyword}, timeout=15)
        animes = data.get("animes") or []
        return {"ok": True, "base": base,
                "ms": round((time.time() - started) * 1000),
                "animes": len(animes),
                "sample": (animes[0].get("animeTitle") if animes else "")}
    except DanmakuError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if url is not None:
            config.set("danmaku_service", old)


def _clean_service_title(title: str) -> str:
    """去掉外服标题里的装饰，方便比番名。

    danmu_api 的标题长这样：「狐妖小红娘(2015)【动漫】from 360」，
    直接跟「狐妖小红娘」比相似度会被后缀拉到 0.43，清理后才是 1.0。
    """
    text = str(title or "")
    text = re.sub(r"\bfrom\s+[\w\-]+\s*$", "", text, flags=re.I)
    text = re.sub(r"[（(]\s*(19|20)\d{2}\s*[)）]", "", text)
    text = re.sub(r"【[^】]*】", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -·:：")
    return text


def _service_platform(title: str) -> str:
    """从标题尾巴里抽出弹幕来源平台（danmu_api 会写成 …from tencent）。"""
    match = re.search(r"\bfrom\s+([\w\-]+)\s*$", str(title or ""), flags=re.I)
    return match.group(1).lower() if match else ""


def service_search(anime_name: str, episode_number: int = None, limit: int = 6) -> list:
    """在外部服务里搜番剧，返回带 episodeId 的候选（按番名相似度排序）。

    不把 episode 参数交给服务端：它只做「有没有这一集」的过滤，排序未必按番名，
    实测「狐妖小红娘」会被匹配到另一部「狐妖小红娘 黄风岭篇」。
    这里自己清理标题后按相似度排，调用方再校验。
    """
    name = (anime_name or "").strip()
    data = _service_http("/api/v2/search/episodes", {"anime": name})
    scored = []
    for anime in (data.get("animes") or [])[:20]:
        raw_title = anime.get("animeTitle") or ""
        episodes = anime.get("episodes") or []
        if not episodes:
            continue
        score = bili.title_similarity(name, _clean_service_title(raw_title))
        scored.append((score, raw_title, episodes))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, raw_title, episodes in scored[:limit]:
        platform = _service_platform(raw_title)
        for index, ep in enumerate(episodes, start=1):
            number = _episode_number_from_title(ep.get("episodeTitle") or "") or index
            out.append({"anime_title": raw_title, "score": round(score, 3),
                        "platform": platform, "number": number,
                        "episode_id": ep.get("episodeId"),
                        "episode_title": ep.get("episodeTitle") or ""})
    return out


def _episode_number_from_title(title: str) -> int:
    """从「第3集」「03」「EP03」这类标题里取集号，取不到返回 0。"""
    text = str(title or "")
    for pattern in (r"第\s*(\d{1,4})\s*[集话話回]", r"[Ee][Pp]?\s*(\d{1,4})", r"^\s*(\d{1,4})\s*$"):
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return 0


def service_pick(anime_name: str, number: int, min_score: float = 0.6) -> dict:
    """在外部服务里挑「番名够像 + 集号对得上」的那一集。"""
    candidates = service_search(anime_name, number)
    if not candidates:
        return {}
    best = candidates[0]
    if best.get("score", 0) < min_score:
        logger.info("外服匹配度太低（%.2f < %.2f）：%s", best.get("score", 0), min_score,
                    best.get("anime_title"))
        return {}
    for item in candidates:
        if item.get("score", 0) < min_score:
            continue
        if int(item.get("number") or 0) == int(number):
            return item
    # 集号对不上时退回同名番的第 N 集（按列表顺序）
    same = [c for c in candidates if c.get("score", 0) >= min_score
            and c.get("anime_title") == best.get("anime_title")]
    if 0 < int(number) <= len(same):
        return same[int(number) - 1]
    return {}


def _parse_service_comments(payload: dict) -> list:
    """把弹弹play 格式的弹幕转成内部结构。

    两种写法都认：``{"p": "时间,模式,颜色,用户", "m": "文本"}`` 和对象字段。
    p 的段数各家不一样（有的带字号），所以按「哪个数像颜色」来判断位置。
    """
    items = []
    for comment in payload.get("comments") or []:
        if not isinstance(comment, dict):
            continue
        text = str(comment.get("m") or comment.get("content") or "").strip()
        if not text:
            continue
        time_s, mode, color = 0.0, 1, 16777215
        p = comment.get("p")
        if isinstance(p, str) and p:
            parts = p.split(",")
            try:
                time_s = float(parts[0])
            except (IndexError, ValueError):
                time_s = 0.0
            nums = []
            for token in parts[1:5]:
                try:
                    nums.append(int(float(token)))
                except ValueError:
                    nums.append(None)
            # [模式, 颜色] 还是 [模式, 字号, 颜色]：看哪个数落在颜色范围内
            if len(nums) >= 2 and nums[1] is not None and nums[1] > 0xFFFF:
                mode, color = nums[0] or 1, nums[1]
            elif len(nums) >= 3 and nums[2] is not None and nums[2] > 0xFFFF:
                mode, color = nums[0] or 1, nums[2]
            elif nums and nums[0] is not None:
                mode = nums[0]
        else:
            time_s = float(comment.get("time") or 0)
            mode = int(comment.get("mode") or 1)
            color = int(comment.get("color") or 16777215)
        items.append({"time": round(max(0.0, time_s), 3), "mode": mode,
                      "size": 25, "color": color, "text": text})
    items.sort(key=lambda x: (x["time"], x["mode"]))
    return items


def service_fetch(episode_id, timeout: int = 60) -> dict:
    """按 episodeId 从外部服务取一集弹幕。"""
    payload = _service_http("/api/v2/comment/%s" % episode_id,
                            {"format": "json", "duration": "true"}, timeout=timeout)
    items = _parse_service_comments(payload)
    return {"count": len(items), "items": items,
            "duration": payload.get("videoDuration") or 0,
            "source": payload.get("source") or ""}


# ---------------------------------------------------------------- 匹配番剧

def match(anime_name: str, limit: int = 5) -> list:
    """按番剧名搜 B 站，返回季候选。"""
    name = (anime_name or "").strip()
    if not name:
        return []
    try:
        return bili.search(name, limit=limit)
    except bili.BiliError as exc:
        raise DanmakuError("搜索 B 站失败：%s" % exc) from exc


def episodes(season_id: int) -> dict:
    """该季每一集的 cid：{集号: {cid, bvid, title, url}}。"""
    try:
        detail = bili.season_detail(int(season_id))
    except bili.BiliError as exc:
        raise DanmakuError("读取 B 站季详情失败：%s" % exc) from exc

    result = {}
    for index, item in enumerate(detail.get("episodes") or [], start=1):
        number = item.get("number") or index
        cid = item.get("cid")
        if not cid:
            continue
        result[int(number)] = {
            "cid": int(cid),
            "bvid": item.get("bvid") or "",
            "title": item.get("long_title") or item.get("title") or "",
            "url": ("https://www.bilibili.com/bangumi/play/ep%s" % item["ep_id"]
                    if item.get("ep_id") else ""),
        }
    return {"season_id": int(season_id), "title": detail.get("title") or "",
            "total": detail.get("total") or len(result), "episodes": result}


# ---------------------------------------------------------------- 抓取弹幕

def _session_headers() -> dict:
    return {"User-Agent": UA, "Referer": "https://www.bilibili.com/",
            "Accept": "*/*", "Cache-Control": "no-cache", "Pragma": "no-cache"}


def _get(url: str, params: dict = None, cache_bust: bool = False) -> bytes:
    params = dict(params or {})
    if cache_bust:
        # 分段接口不加这个会被 CDN 回 304（拿不到内容）
        params["_"] = int(time.time() * 1000)
    url = url + "?" + urllib.parse.urlencode(params)
    with _lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()

    req = urllib.request.Request(url, headers=_session_headers())
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
        body = resp.read()
    # 老接口用 deflate 压过
    if body[:2] == b"\x78\x9c":
        try:
            body = zlib.decompress(body)
        except zlib.error:
            try:
                body = zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error:
                pass
    return body


def _parse_xml(body: bytes, cid: int) -> list:
    """B 站 XML：<d p="时间,模式,字号,颜色,时间戳,池,用户,行号">内容</d>"""
    text = body.decode("utf-8", "ignore")
    items = []
    for match in re.finditer(r'<d p="([^"]*)"[^>]*>(.*?)</d>', text, re.S):
        fields = match.group(1).split(",")
        if len(fields) < 4:
            continue
        try:
            progress = float(fields[0])
            mode = int(float(fields[1]))
            size = int(float(fields[2]))
            color = int(float(fields[3]))
        except ValueError:
            continue
        content = html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
        if not content:
            continue
        items.append({"time": round(progress, 3), "mode": mode, "size": size,
                      "color": color, "text": content})
    return items


def _parse_seg(body: bytes, cid: int) -> list:
    """seg.so 是 protobuf：DmSegMobileReply{ repeated DanmakuElem elems = 1 }。

    只解需要的几个字段：progress(2, 毫秒) / mode(3) / fontsize(4) / color(5) / content(7)。
    手写 varint 解析，免得为一个接口引入 protobuf 依赖。
    """
    items = []

    def read_varint(buf, pos):
        shift = 0
        value = 0
        while pos < len(buf):
            byte = buf[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, pos
            shift += 7
            if shift > 63:
                break
        return None, pos

    pos = 0
    while pos < len(body):
        key, pos = read_varint(body, pos)
        if key is None:
            break
        field, wire = key >> 3, key & 0x07
        if wire == 2:
            length, pos = read_varint(body, pos)
            if length is None or length < 0:
                break
            chunk = body[pos:pos + length]
            pos += length
            if field != 1:
                continue
            # 解析内层 DanmakuElem
            inner = 0
            elem = {"time": 0.0, "mode": 1, "size": 25, "color": 16777215, "text": ""}
            while inner < len(chunk):
                ikey, inner = read_varint(chunk, inner)
                if ikey is None:
                    break
                ifield, iwire = ikey >> 3, ikey & 0x07
                if iwire == 0:
                    value, inner = read_varint(chunk, inner)
                    if value is None:
                        break
                    if ifield == 2:
                        elem["time"] = round(value / 1000.0, 3)
                    elif ifield == 3:
                        elem["mode"] = int(value)
                    elif ifield == 4:
                        elem["size"] = int(value)
                    elif ifield == 5:
                        elem["color"] = int(value)
                elif iwire == 2:
                    length, inner = read_varint(chunk, inner)
                    if length is None:
                        break
                    data = chunk[inner:inner + length]
                    inner += length
                    if ifield == 7:
                        elem["text"] = data.decode("utf-8", "ignore").strip()
                elif iwire == 1:
                    inner += 8
                elif iwire == 5:
                    inner += 4
                else:
                    break
            if elem["text"]:
                items.append(elem)
        elif wire == 0:
            _, pos = read_varint(body, pos)
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            break
    return items


def fetch(cid: int, max_segments: int = MAX_SEGMENTS) -> dict:
    """抓一集的全部弹幕。

    先走分段接口（长番也能拿全），拿到空结果再退回老接口。
    B 站对「超出范围的分段」回 304，不是出错 —— 当作分段到头处理。
    """
    cid = int(cid)
    if cid <= 0:
        raise DanmakuError("cid 无效")

    items = []
    for index in range(1, max_segments + 1):
        try:
            body = _get(API_SEG, {"type": 1, "oid": cid, "segment_index": index}, cache_bust=True)
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                break                     # 没有更多分段了
            logger.warning("分段 %d 拉取失败（cid=%s）：%s", index, cid, exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("分段 %d 拉取失败（cid=%s）：%s", index, cid, exc)
            break
        if not body:
            break
        chunk = _parse_seg(body, cid)
        if not chunk:
            break
        items.extend(chunk)

    if not items:
        try:
            body = _get(API_LIST, {"oid": cid})
            items = _parse_xml(body, cid)
        except Exception as exc:  # noqa: BLE001
            raise DanmakuError("拉取弹幕失败：%s" % exc) from exc

    items.sort(key=lambda x: (x["time"], x["mode"]))
    return {"cid": cid, "count": len(items), "items": items}


# ---------------------------------------------------------------- 落盘格式

def _escape_xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def to_xml(items: list, cid: int = 0, chat_id: int = 0) -> str:
    """B 站原生 XML（弹弹play / PotPlayer / mpv 都认）。"""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             "<i>",
             "<chatserver>chat.bilibili.com</chatserver>",
             "<chatid>%d</chatid>" % (chat_id or cid),
             "<mission>0</mission>",
             "<maxlimit>%d</maxlimit>" % max(len(items), 1000),
             "<state>0</state>",
             "<real_name>0</real_name>",
             "<source>k-v</source>"]
    for item in items:
        p = "%0.3f,%d,%d,%d,%d,0,0,0" % (
            item["time"], item.get("mode", 1), item.get("size", 25),
            item.get("color", 16777215), int(item["time"] * 1000))
        lines.append('<d p="%s">%s</d>' % (p, _escape_xml(item["text"])))
    lines.append("</i>")
    return "\n".join(lines) + "\n"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    secs = seconds % 60
    return "%d:%02d:%05.2f" % (hours, minutes, secs)


def _ass_color(rgb: int) -> str:
    """B 站是 0xRRGGBB，ASS 是 &HBBGGRR&。"""
    r = (rgb >> 16) & 0xFF
    g = (rgb >> 8) & 0xFF
    b = rgb & 0xFF
    return "&H%02X%02X%02X&" % (b, g, r)


def to_ass(items: list, width: int = 1920, height: int = 1080,
           duration: float = 0.0, title: str = "") -> str:
    """转成 ASS：滚动弹幕用 \\move，顶部/底部用 \\an8 / \\an2。

    时长按最后一条弹幕往后留 6 秒（不知道视频真实长度时的保守估计）。
    """
    if duration <= 0:
        duration = (max((i["time"] for i in items), default=0) + 6) or 60
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: %d" % width,
        "PlayResY: %d" % height,
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        "Style: Danmaku,Microsoft YaHei,42,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for item in items:
        start = float(item["time"])
        mode = int(item.get("mode", 1))
        text = item["text"].replace("\\", "\\\\").replace("{", "(").replace("}", ")")
        text = text.replace("\n", " ")
        color = _ass_color(item.get("color", 16777215))
        tag = "\\c%s" % color
        if mode in (4, 5):                     # 底部 / 顶部
            align = 2 if mode == 4 else 8
            effect = "{\\an%d%s}" % (align, tag)
            end = min(start + 4.0, duration)
        elif mode == 7:                        # 高级弹幕：按普通滚动处理
            effect = "{\\move(%d,%d,%d,%d)%s}" % (width, height - 60, 0, height - 60, tag)
            end = min(start + 8.0, duration)
        elif mode == 8:                        # 脚本弹幕：跳过
            continue
        else:                                  # 滚动
            y = 40 + (hash(item["text"]) % max(1, (height - 120))) // 1
            effect = "{\\move(%d,%d,0,%d)%s}" % (width, y, y, tag)
            end = min(start + 8.0, duration)
        lines.append("Dialogue: 0,%s,%s,Danmaku,,0,0,0,,%s%s" % (
            _ass_time(start), _ass_time(end), effect, text))
    return "\n".join(lines) + "\n"


def _target(video_path: str, fmt: str) -> str:
    """弹幕文件该叫什么。

    按播放器/媒体服务器的通用规范来：**视频主名 + 弹幕扩展名**，同目录。
    飞牛影视、Kodi、Plex、Jellyfin 都按这个规则找外挂弹幕，多一段后缀
    （早期的 `.danmaku.ass`）它们就不认了。
    """
    stem = os.path.splitext(video_path)[0]
    return stem + (".xml" if fmt == "xml" else ".ass")


def fetch_for_episode(anime_name: str, number: int, cid=None,
                      prefer: str = "auto") -> dict:
    """取一集弹幕：配了外部服务就先用它，拿不到再退回 B 站。

    prefer: auto（默认）/ service（只用外服）/ bilibili（只用 B 站）
    返回 {"count", "items", "source"}；source 记录这集实际用的是谁。
    """
    errors = []
    if prefer in ("auto", "service") and service_base():
        try:
            picked = service_pick(anime_name, number)
            episode_id = picked.get("episode_id")
            if episode_id:
                result = service_fetch(episode_id)
                if result["items"]:
                    return {"count": result["count"], "items": result["items"],
                            "source": "service",
                            "detail": "%s %s" % (picked.get("anime_title") or "",
                                                 picked.get("episode_title") or "")}
                errors.append("外部服务这集没有弹幕")
            else:
                errors.append("外部服务没匹配到这一集")
        except DanmakuError as exc:
            errors.append(str(exc)[:80])
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:80])
        if prefer == "service":
            raise DanmakuError(errors[-1] if errors else "外部服务没给数据")

    if prefer in ("auto", "bilibili") and cid:
        result = fetch(cid)
        if result["items"]:
            return {"count": result["count"], "items": result["items"],
                    "source": "bilibili", "detail": ""}
        errors.append("B 站这一集没有弹幕")

    if errors:
        raise DanmakuError("；".join(errors[:2]))
    raise DanmakuError("既没有外部服务地址，也没有 B 站 cid")


def save(items: list, video_path: str, fmt: str = None, cid: int = 0) -> list:
    """把弹幕写到视频旁边，返回写出的文件路径。

    fmt 留空时读设置里的 danmaku_format（默认 xml）。
    """
    if not fmt:
        fmt = config.get("danmaku_format") or "xml"
    fmt = fmt if fmt in _FORMATS else "xml"
    written = []
    folder = os.path.dirname(video_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)

    if fmt in ("xml", "both"):
        path = _target(video_path, "xml")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(to_xml(items, cid=cid))
        written.append(path)

    if fmt in ("ass", "both"):
        path = _target(video_path, "ass")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(to_ass(items))
        written.append(path)

    # 清掉早期命名留下的副本（第01集.danmaku.ass），否则播放器旁边会躺两份
    legacy = os.path.splitext(video_path)[0] + ".danmaku.ass"
    if os.path.isfile(legacy) and legacy not in written:
        try:
            os.remove(legacy)
            logger.info("已移除旧命名的弹幕文件：%s", os.path.basename(legacy))
        except OSError:
            pass

    return written


# ---------------------------------------------------------------- 整部批量

def download_for_anime(anime_name: str, episodes_map: dict, season_id: int = None,
                       fmt: str = None, progress=None) -> dict:
    """给已下载的剧集抓弹幕。

    episodes_map：{集号: 视频绝对路径}（由调用方从番剧库扫描得到）
    season_id：可指定 B 站季；不指定就按番剧名搜，取相似度最高且集数对得上的那个
    """
    fmt = fmt or config.get("danmaku_format") or "xml"
    if fmt not in _FORMATS:
        fmt = "xml"
    if not episodes_map:
        raise DanmakuError("没有已下载的剧集")

    if season_id:
        season = episodes(int(season_id))
    else:
        try:
            candidates = match(anime_name, limit=5)
        except DanmakuError as exc:
            if not service_base():
                raise
            logger.warning("B 站搜索失败（%s），改用外部服务", exc)
            candidates = []
        # 选集数覆盖得最多的那个季
        matched = None
        best = None
        for item in candidates:
            if item.get("score", 0) < bili.MIN_MATCH:
                continue
            try:
                info = episodes(int(item["season_id"]))
            except DanmakuError as exc:
                logger.warning("读取季 %s 失败：%s", item.get("season_id"), exc)
                continue
            covered = len(set(info["episodes"]) & set(episodes_map))
            score = (covered, item.get("score", 0))
            if best is None or score > best[0]:
                best = (score, item)
                matched = info
        if matched is None:
            if not service_base():
                raise DanmakuError("B 站没有匹配到《%s》的分季" % anime_name)
            # 配了外服：B 站对不上也能继续，逐集交给外服去匹配
            logger.info("B 站没有匹配到《%s》，只用外部弹幕服务", anime_name)
            matched = {"season_id": 0, "title": "", "episodes": {}}
        season = matched

    remote = season.get("episodes") or {}
    done, skipped, failed = [], [], []
    total = len(episodes_map)
    for index, (number, path) in enumerate(sorted(episodes_map.items()), start=1):
        if progress:
            try:
                progress(index, total, number)
            except Exception:  # noqa: BLE001
                pass
        info = remote.get(int(number))
        try:
            # 配了外部服务就先用它（覆盖爱优腾芒），拿不到再退回 B 站；
            # 只有 B 站完全没有这一集、外服也没数据时才记为跳过
            result = fetch_for_episode(anime_name, number,
                                       cid=(info or {}).get("cid") or None)
            if not result["items"]:
                failed.append({"number": number, "error": "这一集没有弹幕"})
                continue
            files = save(result["items"], path, fmt=fmt,
                         cid=(info or {}).get("cid") or 0)
            done.append({"number": number, "count": result["count"],
                         "source": result.get("source") or "",
                         "files": files})
            logger.info("弹幕 %s 第%s集：%d 条（%s）→ %s", anime_name, number,
                        result["count"], result.get("source") or "?",
                        ", ".join(os.path.basename(f) for f in files))
        except DanmakuError as exc:
            if not info:
                skipped.append({"number": number, "reason": "B 站没有这一集"})
            else:
                failed.append({"number": number, "error": str(exc)[:120]})
            logger.warning("弹幕 %s 第%s集失败：%s", anime_name, number, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("第 %s 集弹幕失败：%s", number, exc)
            failed.append({"number": number, "error": str(exc)[:120]})

    return {"season_id": season.get("season_id"), "season_title": season.get("title") or "",
            "format": fmt, "done": done, "skipped": skipped, "failed": failed,
            "downloaded": len(done),
            "count": sum(item["count"] for item in done)}
