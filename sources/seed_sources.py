# -*- coding: utf-8 -*-
"""内置动漫源：首次启动时自动写入一批实测可用的 MacCMS 资源接口。

这些接口是苹果 CMS(v10) 资源站对外提供的公开采集接口，稳定、无需逆向，
``vod_play_url`` 里直接带每一集的 m3u8，所以「检索 → 剧集 → 下载」可全程直连。
接口地址可随时在网页上编辑；失效的源取消勾选即可（不会影响其他源）。
"""
import threading

# 实测能直接给出 m3u8 的接口（m3u8 直链随接口一起返回，下载最稳）
MACCMS_SOURCES = [
    ("量子资源", "https://cj.lziapi.com/api.php/provide/vod/",
     "实测直链 m3u8，剧集完整"),
    ("暴风资源", "https://bfzyapi.com/api.php/provide/vod/",
     "实测直链 m3u8，剧集命名规整"),
    ("最大资源", "https://api.zuidapi.com/api.php/provide/vod/",
     "实测直链 m3u8"),
    ("无尽资源", "https://api.wujinapi.com/api.php/provide/vod/",
     "实测直链 m3u8"),
    ("虎牙资源", "https://www.huyaapi.com/api.php/provide/vod/",
     "实测直链 m3u8，更新快"),
    ("金鹰资源", "https://jyzyapi.com/api.php/provide/vod/",
     "实测直链 m3u8"),
    ("飘零资源", "https://p2100.net/api.php/provide/vod/",
     "实测直链 m3u8"),
    ("360资源", "https://360zy.com/api.php/provide/vod/",
     "实测直链 m3u8"),
    ("魔都资源", "https://www.mdzyapi.com/api.php/provide/vod/",
     "实测直链 m3u8"),
    ("百度资源", "https://api.apibdzy.com/api.php/provide/vod/",
     "实测直链 m3u8"),
    ("非凡资源", "https://api.ffzyapi.com/api.php/provide/vod/",
     "多线路，程序自动选直链线路；动漫量大（实测 1180 集）"),
]

# 首集地址是分享页，程序会自动进页面再解析 m3u8（可用但成功率略低），默认不启用
OPTIONAL_SOURCES = [
    ("极速资源", "https://jszyapi.com/api.php/provide/vod/",
     "分享页地址，下载时会自动进页面解析 m3u8"),
    ("红牛资源", "https://www.hongniuzy2.com/api.php/provide/vod/",
     "分享页地址，下载时会自动进页面解析 m3u8"),
]

_LOCK = threading.Lock()


def build_config(api: str, note: str = "", apis=None) -> dict:
    return {
        "base_url": api.rstrip("/"),
        "api": api,
        "apis": apis or [],
        "request_timeout": 20,
        # 测试源时用的关键字：留空则用 sources.base.DEFAULT_TEST_KEYWORD（通用词，不指定具体作品）
        "test_keyword": "",
        "note": note,
    }


def seed_sources(force: bool = False) -> dict:
    """补齐内置源（幂等）。

    * 已存在的同名源不会被覆盖（用户改过的配置得以保留）
    * 不依赖任何“已初始化”标记 —— 删掉 data/moanime.db 后重启会自动重新补齐
    """
    from core.database import db
    from core.logging_setup import get_logger

    logger = get_logger("moanime.seed")
    result = {"added": [], "skipped": [], "removed_demo": 0}

    with _LOCK:
        for name, api, note in MACCMS_SOURCES:
            if db.get_source(name=name):
                result["skipped"].append(name)
                continue
            db.add_source({
                "name": name,
                "type": "maccms",
                "base_url": api.rstrip("/"),
                "config": build_config(api, note),
                "enabled": True,
                "builtin": True,
                "note": note,
            })
            result["added"].append(name)

        for name, api, note in OPTIONAL_SOURCES:
            if db.get_source(name=name):
                result["skipped"].append(name)
                continue
            db.add_source({
                "name": name,
                "type": "maccms",
                "base_url": api.rstrip("/"),
                "config": build_config(api, note),
                "enabled": False,
                "builtin": True,
                "note": note,
            })
            result["added"].append(name)

        # 移除历史遗留的内置演示源（功能已由真实源覆盖）
        demo = db.get_source(name="演示源(内置)")
        if demo:
            db.delete_source(demo["id"])
            result["removed_demo"] = 1

        # 没有任何可用源时兜底：把内置源全部启用，避免用户面对空界面
        if force or not any(row.get("enabled") for row in db.list_sources()):
            builtin_ids = [row["id"] for row in db.list_sources() if row.get("builtin")]
            if builtin_ids:
                db.reorder_sources(builtin_ids +
                                   [row["id"] for row in db.list_sources()
                                    if row["id"] not in builtin_ids])
                for source_id in builtin_ids[:len(MACCMS_SOURCES)]:
                    db.update_source(source_id, {"enabled": True})
                result["reenabled"] = len(builtin_ids)

    if result["added"]:
        logger.info("已补齐 %d 个内置动漫源：%s", len(result["added"]),
                    "、".join(result["added"][:6]))
    if result["removed_demo"]:
        logger.info("已移除内置演示源")
    return result
