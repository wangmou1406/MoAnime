# -*- coding: utf-8 -*-
"""REST API 包。

原来全部塞在 web/routes_api.py 一个文件里（2400 多行），按职责拆成：

  _base.py    公共地基：蓝图对象、统一响应格式（ok/fail）、请求体解析、通用参数校验
  meta.py     元信息与文件系统：版本信息、目录浏览、打开/定位路径
  search.py   检索链路：聚合检索、检索历史、剧集列表、播放地址解析
  downloads.py 下载任务：提交下载、批量下载、任务列表与暂停/继续/重试/删除
  appearance.py 外观：自定义背景图的上传、读取与清除
  sources.py  源管理：增删改查、排序、测活/测源、广告标记、导入导出、源类型
  settings.py 设置：读写配置、校验目录、清理临时文件、免责声明、恢复出厂
  library.py  番剧库与播放：库扫描、字幕搜索下载、在线播放流、缩略图、删除
  ads.py      广告体检：时长通道 + 画面文字(OCR)通道，跑完自动排序
  system.py   系统与依赖：日志、统计、打开路径、ffmpeg 下载安装、浏览器取流状态

所有模块共用 _base.py 里的同一个 `api` 蓝图，路由前缀一致（/api）。
import 顺序与拆分前保持一致，路由注册次序不变。
"""

# 先建立蓝图与响应助手，再逐个导入各模块（它们会在 import 时注册路由）
from ._base import api, body, fail, ok  # noqa: F401

from . import meta         # noqa: F401,E402
from . import search       # noqa: F401,E402
from . import downloads    # noqa: F401,E402
from . import appearance   # noqa: F401,E402
from . import sources      # noqa: F401,E402
from . import settings     # noqa: F401,E402
from . import library      # noqa: F401,E402
from . import ads          # noqa: F401,E402
from . import system       # noqa: F401,E402

__all__ = ["api", "ok", "fail", "body"]