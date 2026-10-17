# -*- coding: utf-8 -*-
"""Web 层：Flask 应用装配与 Socket.IO 事件。"""
import threading
import time

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

from core.config import APP_NAME, APP_NAME_EN, STATIC_DIR, TEMPLATE_DIR, VERSION, config
from core.database import db
from core.logging_setup import get_logger, recent_logs, set_broadcaster
from core.task_manager import download_manager
from sources.manager import source_manager

logger = get_logger("moanime.web")

socketio = SocketIO(async_mode="threading", cors_allowed_origins="*", logger=False,
                    engineio_logger=False)


def _emit(event: str, payload: dict) -> None:
    """后台线程安全地向所有连接的浏览器推送事件。"""
    try:
        socketio.emit(event, payload)
    except Exception:
        pass


def create_app() -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static",
                template_folder=TEMPLATE_DIR)
    app.config["SECRET_KEY"] = "moanime-local-secret"
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    # 所有 /api 路由都在 web/routes/ 包里按职责分文件注册（共用同一个蓝图）
    from web.routes import api
    app.register_blueprint(api)

    socketio.init_app(app, async_mode="threading", cors_allowed_origins="*",
                      logger=False, engineio_logger=False)

    @app.after_request
    def no_cache(response):
        """接口与前端脚本一律不缓存，避免浏览器用到旧版 JS 去调新接口。"""
        path = request.path
        if path.startswith("/api") or path == "/" or path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/")
    def index():
        return render_template("index.html", version=VERSION, app_name=APP_NAME, app_name_en=APP_NAME_EN)

    @app.get("/favicon.ico")
    def favicon():
        from flask import send_from_directory
        import os
        path = os.path.join(STATIC_DIR, "favicon.svg")
        if os.path.exists(path):
            return send_from_directory(STATIC_DIR, "favicon.svg", mimetype="image/svg+xml")
        return ("", 204)

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api"):
            return jsonify({"ok": False, "error": "接口不存在：%s" % request.path}), 404
        return render_template("index.html", version=VERSION, app_name=APP_NAME, app_name_en=APP_NAME_EN), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        """API 一律返回 JSON，避免前端拿到 HTML 报错页而看不懂。"""
        if request.path.startswith("/api"):
            allowed = ", ".join(sorted(getattr(error, "valid_methods", None) or []))
            return jsonify({
                "ok": False,
                "error": "接口 %s 不支持 %s 方法（可用：%s）" % (
                    request.path, request.method, allowed or "未知"),
            }), 405
        return render_template("index.html", version=VERSION, app_name=APP_NAME, app_name_en=APP_NAME_EN), 405

    @app.errorhandler(500)
    def server_error(error):  # pragma: no cover
        logger.error("服务端异常：%s", error)
        if request.path.startswith("/api"):
            return jsonify({"ok": False, "error": "服务端异常"}), 500
        return "服务端异常", 500

    # ---------------------------------------------------------- Socket.IO
    @socketio.on("connect")
    def on_connect():
        _emit_to(request.sid, "hello", {
            "version": VERSION,
            "tasks": download_manager.statistics(),
            "sources": len(source_manager.list_sources()),
            "logs": recent_logs(limit=80),
        })

    @socketio.on("ping_web")
    def on_ping():
        _emit_to(request.sid, "pong_web", {"time": time.time()})

    return app


def _emit_to(sid: str, event: str, payload: dict) -> None:
    try:
        socketio.emit(event, payload, to=sid)
    except Exception:
        pass


def init_runtime(app: Flask) -> None:
    """初始化运行期：广播器、内置动漫源、下载管理器。"""
    set_broadcaster(lambda entry: _emit("log", entry))
    download_manager.broadcaster = _emit
    try:
        # 注意：要走子模块导入，sources 包本身不导出 seed_sources
        from sources import seed_sources as seed_module
        result = seed_module.seed_sources()
        if result.get("added"):
            logger.info("内置动漫源已补齐 %d 个", len(result["added"]))
    except Exception as exc:  # noqa: BLE001
        logger.error("内置动漫源初始化失败：%s", exc, exc_info=True)
    download_manager.start()


def shutdown_runtime() -> None:
    download_manager.stop()
