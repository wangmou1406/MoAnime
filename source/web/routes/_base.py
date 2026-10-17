# -*- coding: utf-8 -*-
"""公共地基：蓝图对象、统一响应格式（ok/fail）、请求体解析、通用参数校验

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import time
from flask import Blueprint, jsonify, request
from core.logging_setup import get_logger

# ============================================================
# 公共地基：其它模块都从这里 import 蓝图与响应助手
# ============================================================
_SUB_EXT = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi")


logger = get_logger("moanime.api")


api = Blueprint("api", __name__, url_prefix="/api")


_START_TIME = time.time()


@api.before_request
def _merge_query_into_form():
    """GET 请求时把查询串并入 request.form。

    这样路由内部统一用 `request.form.get(...)` 也能读到参数，
    不必为「GET 查询串 / POST 表单 / POST JSON」写三套取值代码。
    """
    if request.method in ("GET", "HEAD") and request.args and not request.form:
        request.form = request.args.copy()


@api.errorhandler(405)
def _method_not_allowed(error):  # pragma: no cover - 由 Flask 调用
    allowed = ", ".join(sorted(getattr(error, "valid_methods", None) or []))
    logger.warning("API 方法不允许 | %s %s | 可用=%s | args=%s | UA=%s",
                   request.method, request.path, allowed, dict(request.args),
                   (request.headers.get("User-Agent") or "")[:60])
    return jsonify({"ok": False,
                    "error": "接口 %s 不支持 %s 方法（可用：%s）" % (
                        request.path, request.method, allowed or "未知")}), 405


# ------------------------------------------------------------------ 工具
def ok(data=None, **extra):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def fail(message: str, status: int = 400, **extra):
    payload = {"ok": False, "error": str(message)}
    payload.update(extra)
    return jsonify(payload), status


def body() -> dict:
    """合并请求参数：JSON body / 表单 / 查询串，后者只补前者没有的键。

    这样无论前端是把参数放在 JSON、表单还是 URL 上，接口都能拿到值，
    不会出现“界面上明明填了关键字、后端却说没填”的情况。
    """
    merged = {}
    if request.args:
        merged.update({key: request.args.get(key) for key in request.args})
    if request.form:
        merged.update({key: request.form.get(key) for key in request.form})
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        merged.update(data)
    return merged


def arg_int(name: str, default=None):
    value = request.args.get(name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
