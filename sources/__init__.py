# -*- coding: utf-8 -*-
"""动漫源包：导入本模块即完成所有源类型的注册。"""
from . import anime1, base, generic, jsonapi, jsonfeed, maccms, twok  # noqa: F401
from .base import (DEFAULT_TEST_KEYWORD, NoResultError, SourceError,
                   SourcePlugin, UnresolvableError, create_plugin,
                   get_plugin_class, list_types, register,
                   registered_count)  # noqa: F401

__all__ = [
    "SourceError", "SourcePlugin", "UnresolvableError", "NoResultError",
    "DEFAULT_TEST_KEYWORD", "create_plugin", "get_plugin_class", "list_types",
    "register", "registered_count",
    "anime1", "generic", "jsonapi", "jsonfeed", "maccms", "twok",
]
