# -*- coding: utf-8 -*-
"""Web 层：Flask 应用装配与 Socket.IO 事件。"""
import hmac
import threading
import time
from ipaddress import ip_address

from flask import Flask, g, jsonify, make_response, render_template, request
from flask_socketio import SocketIO

from core.config import (APP_NAME, APP_NAME_EN, STATIC_DIR, TEMPLATE_DIR, TOKEN_COOKIE,
                         VERSION, config)
from core.database import db
from core.logging_setup import get_logger, recent_logs, set_broadcaster
from core.task_manager import download_manager
from sources.manager import source_manager

logger = get_logger("moanime.web")

socketio = SocketIO(async_mode="threading", cors_allowed_origins="*", logger=False,
                    engineio_logger=False)


def _is_loopback(remote: str) -> bool:
    """判断是不是本机请求（含 IPv4-mapped IPv6）。"""
    if not remote:
        return False
    if remote in ("localhost", "::1"):
        return True
    try:
        addr = ip_address(remote)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _token_page(path: str) -> str:
    """没有令牌时给一个能填令牌的极简页面。"""
    return """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>需要访问令牌</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
      background:#0f1420;color:#e6ebf5;font:14px/1.6 system-ui,"Segoe UI",sans-serif}
 .box{background:#171d2b;border:1px solid #2a3346;border-radius:10px;padding:26px 28px;width:340px}
 h1{font-size:16px;margin:0 0 6px}
 p{color:#93a0b8;font-size:12.5px;margin:0 0 16px}
 input{width:100%%;box-sizing:border-box;padding:8px 10px;border-radius:6px;
       border:1px solid #2a3346;background:#0f1420;color:#e6ebf5;font-size:13px}
 button{margin-top:12px;width:100%%;padding:9px;border:0;border-radius:6px;
        background:#3b82f6;color:#fff;font-size:13px;cursor:pointer}
 code{color:#7dd3fc}
</style></head><body><div class="box">
 <h1>需要访问令牌</h1>
 <p>这台机器的服务开启了局域网访问保护。令牌在服务端设置页的「远程访问」里。</p>
 <form method="get" action="%s">
   <input name="token" placeholder="粘贴访问令牌" autofocus autocomplete="off">
   <button type="submit">进入</button>
 </form>
</div></body></html>""" % path


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

    @app.before_request
    def _check_access_token():
        """局域网访问保护。

        设了 access_token 后，本机以外的请求必须带对令牌；
        从地址栏 ?token=xxx 进来时种一个 cookie 并重定向，令牌不留在地址栏里。
        没设令牌时完全不干预（默认只监听 127.0.0.1，本来就出不了本机）。
        """
        token = (config.get("access_token") or "").strip()
        if not token:
            return None
        if _is_loopback(request.remote_addr or ""):
            return None

        supplied = (request.args.get("token")
                    or request.cookies.get(TOKEN_COOKIE)
                    or request.headers.get("X-Access-Token") or "")
        if supplied and hmac.compare_digest(str(supplied), token):
            # 从地址栏 ?token=xxx 进来：这次照常返回页面，在 after_request 里种 cookie。
            # 不用 302 中转 —— 302 上的 Set-Cookie 在某些客户端会被丢掉，
            # 结果就是「令牌明明对了却还是被拦」。
            if request.args.get("token") and not request.path.startswith("/api"):
                g.set_token_cookie = True
            return None

        if request.path.startswith(("/api", "/socket.io")):
            return jsonify({
                "ok": False,
                "error": "需要访问令牌：请在服务端设置页的「远程访问」里查看，"
                         "或用 http://地址:端口/?token=令牌 打开",
            }), 401
        return make_response(_token_page(request.path), 401)

    @app.after_request
    def _remember_token(response):
        """带上正确令牌访问页面后，种一个 30 天的 cookie，之后就不用再带 token 了。"""
        if getattr(g, "set_token_cookie", False):
            token = (config.get("access_token") or "").strip()
            if token:
                response.set_cookie(TOKEN_COOKIE, token, max_age=86400 * 30,
                                    httponly=True, samesite="Lax")
        return response

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
