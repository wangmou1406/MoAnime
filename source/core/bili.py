# -*- coding: utf-8 -*-
"""B 站番剧集数查询：拿官方的正片集数和每集编号，用来核对本地下载是否齐全。

为什么只接 B 站
    实测（2026-10）：B 站的搜索和详情都有可用的公开接口，且**都不需要登录**；
    爱奇艺/腾讯/优酷/芒果的搜索要么改版成 404、要么返回纯 JS 外壳、要么 401 要签名，
    静态层拿不到数据。所以这里只做 B 站，别的平台想做只能走浏览器。

两个接口
    搜索  /x/web-interface/wbi/search/type?search_type=media_bangumi&keyword=...
          需要 wbi 签名（w_rid + wts），签名用的 img_key/sub_key 每天轮换，现取现用。
    详情  /pgc/view/web/season?season_id=...
          免签名，直接给 total（正片集数）和 episodes[]（含每集标题/编号）。

一个必须注意的差异
    total 是官方正片集数，episodes 可能比它多（总集篇、特别篇）。
    例如某番 total=100 而 episodes=102，多出来的「总集篇」不该算作缺集，
    所以比对时只认编号落在 1..total 的那些集。
"""
import difflib
import hashlib
import re
import threading
import time
import urllib.parse

from .config import config
from .utils import build_session, clean_text, episode_number

API_SEARCH = "https://api.bilibili.com/x/web-interface/wbi/search/type"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_SEASON = "https://api.bilibili.com/pgc/view/web/season"
API_HOME = "https://www.bilibili.com/"

# wbi 混排表（公开算法，取自 bilibili-API-collect）
_MIXIN_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_TOKEN_TTL = 3600          # wbi key / cookie 的缓存时长（秒）
_MIN_INTERVAL = 0.4        # 两次请求的最小间隔，别把 B 站惹毛
_TIMEOUT = 12
MIN_MATCH = 0.5            # 低于这个相似度就认为搜错了，不拿来做集数基准

_lock = threading.Lock()
_last_call = [0.0]
_cache = {"mixin": "", "mixin_at": 0.0, "cookie": "", "cookie_at": 0.0}


class BiliError(Exception):
    """B 站接口不可用（网络、限流、改版都可能）。"""


def _session():
    session = build_session()
    session.headers.update({
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://www.bilibili.com",
    })
    return session


def _throttle():
    with _lock:
        gap = time.time() - _last_call[0]
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        _last_call[0] = time.time()


def _get(session, url, params=None, cookie=""):
    _throttle()
    headers = {"Cookie": cookie} if cookie else None
    try:
        resp = session.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise BiliError("请求失败：%s" % str(exc)[:90])
    if resp.status_code != 200:
        raise BiliError("HTTP %d" % resp.status_code)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        raise BiliError("返回不是 JSON（可能被拦截）")


def _cookie(session):
    """拿 buvid3。带上它搜索结果更稳，不带也常常能用。"""
    now = time.time()
    if _cache["cookie"] and now - _cache["cookie_at"] < _TOKEN_TTL:
        return _cache["cookie"]
    try:
        _throttle()
        resp = session.get(API_HOME, timeout=_TIMEOUT)
        parts = []
        for raw in resp.headers.get_all("Set-Cookie") or []:
            head = raw.split(";")[0]
            if head.startswith(("buvid3=", "buvid4=", "b_nut=")):
                parts.append(head)
        _cache["cookie"] = "; ".join(parts)
    except Exception:  # noqa: BLE001
        _cache["cookie"] = ""
    _cache["cookie_at"] = now
    return _cache["cookie"]


def _mixin_key(session, cookie):
    """取 wbi 签名用的 32 位 mixin_key。"""
    now = time.time()
    if _cache["mixin"] and now - _cache["mixin_at"] < _TOKEN_TTL:
        return _cache["mixin"]
    data = _get(session, API_NAV, cookie=cookie)
    wbi = ((data.get("data") or {}).get("wbi_img") or {})
    img_key = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub_key = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if len(img_key) < 16 or len(sub_key) < 16:
        raise BiliError("取 wbi key 失败（接口可能改版）")
    raw = img_key + sub_key
    mixin = "".join(raw[i] for i in _MIXIN_TABLE)[:32]
    _cache["mixin"] = mixin
    _cache["mixin_at"] = now
    return mixin


def _sign(params: dict, mixin: str) -> dict:
    """给参数加 wts 并算出 w_rid。"""
    signed = dict(params)
    signed["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return signed


def _norm(text: str) -> str:
    """去掉标点空格、统一小写，用于相似度比较。"""
    return re.sub(r"[\s\-_:：·・,，。!！?？'\"“”()（）\[\]【】<>《》]", "", (text or "").lower())


def title_similarity(a: str, b: str) -> float:
    left, right = _norm(a), _norm(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    # 一方包含另一方时给个加成：官方标题常带「第二季」「中配版」这类后缀。
    # 但长度差太多就不能算包含关系 —— 否则「完全不存在的番剧」和「完全」
    # 也会被抬到 0.82，把无关结果当成命中。
    if (left in right or right in left) and \
            min(len(left), len(right)) / max(len(left), len(right)) >= 0.5:
        ratio = max(ratio, 0.82)
    return ratio


def _search_keywords(name: str) -> list:
    """把一个番剧名拆成几次搜索用的关键词。

    实测坑：**精确全名反而可能搜不到**。例如「狐妖小红娘」返回 0 条，
    但「狐妖」和「小红娘」都能命中。所以这里从全名逐步退化。
    """
    name = clean_text(name)
    if not name:
        return []
    out = [name]

    # 去掉季度/版本后缀
    base = re.sub(r"(第[一二三四五六七八九十百\d]+季|Season\s*\d+|剧场版|TV版|完整版|全集|中配版|日语版|粤配版)",
                  "", name).strip(" -_·・")
    if base and base != name:
        out.append(base)

    source = base or name
    # 按分隔符切词
    parts = [p for p in re.split(r"[\s·・,，\-_/]+", source) if len(p) >= 2]
    if len(parts) > 1:
        out.append(parts[0])
        out.append(parts[-1])
    # 前 2~4 个字
    for size in (4, 3, 2):
        if len(source) > size:
            out.append(source[:size])

    seen, unique = set(), []
    for item in out:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique[:6]


def search(keyword: str, limit: int = 8) -> list:
    """搜索 B 站番剧，返回候选列表（按标题相似度排序）。"""
    keyword = clean_text(keyword)
    if not keyword:
        return []

    session = _session()
    cookie = _cookie(session)
    mixin = _mixin_key(session, cookie)

    collected = {}
    last_error = None
    for term in _search_keywords(keyword):
        params = _sign({"search_type": "media_bangumi", "keyword": term,
                        "page": 1, "page_size": 10}, mixin)
        try:
            data = _get(session, API_SEARCH, params=params, cookie=cookie)
        except BiliError as exc:
            last_error = exc
            continue
        if data.get("code") not in (0, None):
            last_error = BiliError("code=%s" % data.get("code"))
            continue
        for item in ((data.get("data") or {}).get("result") or []):
            season_id = item.get("season_id")
            title = clean_text(re.sub(r"</?em[^>]*>", "", item.get("title") or ""))
            if not season_id or not title:
                continue
            key = int(season_id)
            score = title_similarity(keyword, title)
            old = collected.get(key)
            if old is None or score > old["score"]:
                collected[key] = {
                    "season_id": key,
                    "title": clean_text(title),
                    "index_show": clean_text(item.get("index_show") or ""),
                    "cover": item.get("cover") or "",
                    "score": round(score, 3),
                }
        # 已经拿到高度相似的就不必继续退化搜索
        if any(v["score"] >= 0.92 for v in collected.values()):
            break

    if not collected and last_error:
        raise last_error
    return sorted(collected.values(), key=lambda v: -v["score"])[:limit]


def season_detail(season_id: int) -> dict:
    """番剧详情：官方总集数 + 每集编号。"""
    session = _session()
    cookie = _cookie(session)
    data = _get(session, API_SEASON, params={"season_id": season_id}, cookie=cookie)
    if data.get("code") not in (0, None):
        raise BiliError("详情接口 code=%s %s" % (data.get("code"), data.get("message") or ""))
    result = data.get("result") or {}

    episodes = []
    for item in result.get("episodes") or []:
        title = clean_text(item.get("title") or "")
        number = episode_number(title, 0)
        episodes.append({
            "title": title,
            "long_title": clean_text(item.get("long_title") or ""),
            "number": number if number > 0 else None,
            "ep_id": item.get("id"),
        })

    total = result.get("total")
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = len([e for e in episodes if e["number"]])

    # PV / 花絮放在 section 里，单独列出来只作参考
    extras = 0
    for section in result.get("section") or []:
        extras += len(section.get("episodes") or [])

    return {
        "season_id": int(season_id),
        "title": clean_text(result.get("title") or ""),
        "season_title": clean_text(result.get("season_title") or ""),
        "total": total,
        "episode_count": len(episodes),
        "extras": extras,
        "episodes": episodes,
        "score": ((result.get("rating") or {}).get("score")),
        "url": "https://www.bilibili.com/bangumi/play/ss%s" % season_id,
    }


def _local_numbers(episode_names) -> list:
    """从本地剧集名里解析出集号。"""
    numbers = []
    for name in episode_names or []:
        number = episode_number(name, 0)
        if number <= 0:
            continue
        numbers.append(number)
    return numbers


def check(anime_name: str, episode_names=None, season_id: int = None) -> dict:
    """核对一部番的集数。

    episode_names 为空时只返回候选与官方信息，不做比对。
    比对区间取 1..total：B 站有些番的后几集标题被写成「总集篇（一）」这类，
    光靠标题解析会漏掉编号，但 total 仍然是对的，所以以 total 为准。
    """
    candidates = search(anime_name)
    if not candidates:
        return {
            "anime_name": clean_text(anime_name),
            "found": False,
            "reason": "B 站没有搜到这部番（可能没上架，或名字差异太大）",
            "candidates": [],
        }

    chosen = None
    if season_id:
        chosen = next((c for c in candidates if c["season_id"] == int(season_id)), None)
    if chosen is None:
        chosen = candidates[0]

    # 相似度太低就别硬凑：退化搜索到短前缀时可能搜出完全不相关的番。
    # 用户手动指定 season_id 的情况不受这条限制。
    if not season_id and chosen["score"] < MIN_MATCH:
        return {
            "anime_name": clean_text(anime_name),
            "found": False,
            "reason": "名字对不上（最接近的是「%s」，相似度 %.0f%%）"
                      % (chosen["title"], chosen["score"] * 100),
            "candidates": [
                {"season_id": c["season_id"], "title": c["title"],
                 "index_show": c["index_show"], "similarity": c["score"]}
                for c in candidates
            ],
        }

    detail = season_detail(chosen["season_id"])
    total = max(int(detail["total"] or 0), 0)

    local_all = sorted({int(n) for n in _local_numbers(episode_names)}) if episode_names else []
    local_set = set(local_all)
    official_range = set(range(1, total + 1)) if total else set()

    local_in_range = [n for n in local_all if n in official_range]
    # 本地一个文件都没有时，「缺 1..N」这种结论没有意义，留空由调用方展示官方信息
    missing = [n for n in range(1, total + 1) if n not in local_set] if (total and local_all) else []
    beyond = [n for n in local_all if total and n > total]

    return {
        "anime_name": clean_text(anime_name),
        "found": True,
        "match": {
            "season_id": detail["season_id"],
            "title": detail["title"],
            "season_title": detail["season_title"],
            "total": total,
            "episode_count": detail["episode_count"],
            "extras": detail["extras"],
            "score": detail["score"],
            "similarity": chosen["score"],
            "url": detail["url"],
        },
        "local_total": len(local_all),
        "local_in_range": len(local_in_range),
        "official_total": total,
        "missing": missing,
        "beyond": beyond,
        "complete": bool(local_in_range) and not missing,
        "candidates": [
            {"season_id": c["season_id"], "title": c["title"],
             "index_show": c["index_show"], "similarity": c["score"]}
            for c in candidates
        ],
    }
