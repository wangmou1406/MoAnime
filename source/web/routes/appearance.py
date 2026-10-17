# -*- coding: utf-8 -*-
"""外观：自定义背景图的上传、读取与清除

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import os
from flask import send_file
from core.config import DATA_DIR, config

# 跨模块引用
from ._base import api, body, fail, logger, ok

ALLOWED_BG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


MAX_BG_BYTES = 8 * 1024 * 1024      # 背景图上限 8 MB


def _bg_dir() -> str:
    path = os.path.join(DATA_DIR, "backgrounds")
    os.makedirs(path, exist_ok=True)
    return path


@api.route("/appearance/background", methods=["GET", "POST"])
def upload_background():
    """保存自定义背景图。

    支持两种提交方式（都走 JSON，避免 multipart 在中间层被拦）：
      * image_base64: data:image/png;base64,....（前端拖拽/选择文件后读出来的）
      * image_url:    http(s) 地址（仅校验协议，不下载）
    """
    import base64 as _b64
    import hashlib
    import time as _time

    data = body()

    image_url = (data.get("image_url") or "").strip()
    if image_url:
        if not image_url.lower().startswith(("http://", "https://")):
            return fail("背景图地址必须以 http:// 或 https:// 开头")
        config.update({"bg_image": image_url})
        logger.info("背景图设置为远程地址：%s", image_url[:120])
        return ok({"bg_image": image_url, "kind": "url"})

    raw = (data.get("image_base64") or "").strip()
    if not raw:
        return fail("缺少 image_base64 或 image_url")
    # 去掉 data URL 前缀
    if raw.startswith("data:"):
        head, _, raw = raw.partition(",")
        if "base64" not in head:
            return fail("只支持 base64 编码的图片")
    try:
        blob = _b64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001
        return fail("base64 解析失败：%s" % str(exc)[:80])
    if len(blob) > MAX_BG_BYTES:
        return fail("背景图过大（%.1f MB），上限 %d MB" % (len(blob) / 1048576, MAX_BG_BYTES // 1048576))
    if len(blob) < 64:
        return fail("图片内容为空或不完整")

    # 用魔术字节判断真实类型，不信任前端传来的后缀
    magic = blob[:12]
    if magic.startswith(b"\xff\xd8\xff"):
        ext = ".jpg"
    elif magic.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = ".png"
    elif magic.startswith(b"GIF8"):
        ext = ".gif"
    elif magic.startswith(b"BM"):
        ext = ".bmp"
    elif magic[0:4] == b"RIFF" and magic[8:12] == b"WEBP":
        ext = ".webp"
    else:
        return fail("无法识别的图片格式（支持 jpg / png / webp / gif / bmp）")
    if ext not in ALLOWED_BG_EXT:
        return fail("不支持的图片格式：%s" % ext)

    name = "bg_%s_%s%s" % (int(_time.time()),
                           hashlib.md5(blob[:4096]).hexdigest()[:8], ext)
    path = os.path.join(_bg_dir(), name)
    with open(path, "wb") as handle:
        handle.write(blob)

    # 旧的自定义背景图清理掉，避免越积越多
    for old in os.listdir(_bg_dir()):
        if old != name and old.startswith("bg_"):
            try:
                os.remove(os.path.join(_bg_dir(), old))
            except OSError:
                pass

    config.update({"bg_image": name})
    logger.info("背景图已保存：%s（%.1f KB）", name, len(blob) / 1024)
    return ok({"bg_image": name, "kind": "file", "size": len(blob)})


@api.get("/appearance/background/file/<name>")
def get_background(name: str):
    """读取已上传的背景图（限制在 backgrounds 目录内）。"""
    safe = os.path.basename(name or "")
    path = os.path.join(_bg_dir(), safe)
    if not os.path.isfile(path):
        return fail("背景图不存在", 404)
    return send_file(path, max_age=86400)


@api.route("/appearance/background/clear", methods=["GET", "POST"])
def clear_background():
    """清除自定义背景图（恢复纯色背景）。"""
    for old in os.listdir(_bg_dir()):
        if old.startswith("bg_"):
            try:
                os.remove(os.path.join(_bg_dir(), old))
            except OSError:
                pass
    config.update({"bg_image": ""})
    logger.info("已清除自定义背景图")
    return ok({"bg_image": ""})
