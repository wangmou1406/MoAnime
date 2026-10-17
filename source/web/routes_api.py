# -*- coding: utf-8 -*-
"""**兼容层**：真正的实现在 :mod:`web.routes` 包里。

原来 2400 多行的 ``web/routes_api.py`` 已按职责拆成多个文件::

    web/routes/
    ├── __init__.py     包入口（按原顺序 import，保证路由注册次序不变）
    ├── _base.py        蓝图、ok/fail/body、通用参数校验
    ├── meta.py         元信息与文件系统
    ├── search.py       检索、剧集、地址解析
    ├── downloads.py    下载任务
    ├── appearance.py   自定义背景
    ├── sources.py      源管理
    ├── settings.py     设置与维护
    ├── library.py      番剧库、字幕、播放流
    ├── ads.py          广告体检
    └── system.py       日志、统计、ffmpeg、浏览器取流

保留这个文件只是为了让旧的 ``from web.routes_api import api`` 继续能用，
新代码请直接 ``from web.routes import api``。
"""
from web.routes import api, body, fail, ok  # noqa: F401

__all__ = ["api", "ok", "fail", "body"]
