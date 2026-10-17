# -*- coding: utf-8 -*-
"""JSON 订阅源：按结构化 JSON 描述抓取任意站点（便于批量导入/分享源配置）。

与“通用可配置源”读取同一套配置结构，区别是它会把配置里声明的
``headers`` 与 ``request`` 更严格地应用，并且支持 ``search.template``
直接书写完整 URL（含 {page} 与 {keyword}），方便对接 RSS/JSON 检索接口。
"""
from typing import List

from core.utils import absolute_url, clean_text, dedupe

from .base import SourceError, register
from .generic import GenericSource


@register("jsonfeed", display_name="JSON 订阅源（分享用）",
          description="与通用源同结构，适合从 JSON 文件批量导入/分享源配置。")
class JsonFeedSource(GenericSource):
    type_name = "jsonfeed"
    display_name = "JSON 订阅源"

    def search(self, keyword: str, page: int = 1) -> List[dict]:
        return super().search(keyword, page)

    def test(self) -> dict:
        result = super().test()
        result["type"] = self.type_name
        return result
