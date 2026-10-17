# -*- coding: utf-8 -*-
"""动漫下载机（Anime Downloader）启动入口：python run.py [--host 0.0.0.0] [--port 8848] [--no-browser]"""
import argparse
import os
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def parse_args():
    parser = argparse.ArgumentParser(description="动漫下载机 Anime Downloader")
    parser.add_argument("--host", default=None, help="监听地址（默认取设置）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认取设置）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器（默认行为）")
    parser.add_argument("--open-browser", action="store_true",
                        help="启动后自动打开浏览器（使用独立浏览器配置，不影响你正在用的浏览器）")
    parser.add_argument("--debug", action="store_true", help="开启调试模式")
    return parser.parse_args()


def _port_available(host: str, port: int) -> bool:
    import socket
    bind_host = "" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((bind_host, port))
            return True
        except OSError:
            return False


def _port_used_by_us(port: int) -> bool:
    """端口已被占用时，判断是不是另一个动漫下载机实例（是的话提示去用已有界面）。"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/meta" % port, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        return bool(data.get("data", {}).get("version"))
    except Exception:
        return False


def _ensure_port_free(port: int, host: str, logger) -> int:
    """端口空闲则直接用；被别的程序占用则从 port+1 起顺延，最多试 20 个。"""
    if _port_available(host, port):
        return port
    if _port_used_by_us(port):
        logger.warning("端口 %d 上已经有一个动漫下载机实例在运行，"
                       "请直接打开 http://127.0.0.1:%d/（无需重复启动）", port, port)
        logger.warning("如果确实要再开一个实例，请用 --port 指定其他端口")
    for candidate in range(port + 1, port + 21):
        if _port_available(host, candidate):
            logger.warning("端口 %d 已被占用，自动改用 %d", port, candidate)
            return candidate
    logger.error("从 %d 起连续 20 个端口都被占用，请用 --port 手动指定", port)
    return port


def main() -> int:
    args = parse_args()

    from core.config import VERSION, config
    from core.logging_setup import get_logger
    from web.app import create_app, init_runtime, socketio

    logger = get_logger("moanime")
    logger.info("=" * 62)
    logger.info("动漫下载机 %s 启动中...", VERSION)

    # 优先级：命令行参数 > settings.json > 默认值
    # 注意：命令行参数只影响本次运行，绝不写回设置（避免把临时端口变成永久默认）
    host = args.host or config.get("web_host", "127.0.0.1")
    port = int(args.port or config.get("web_port", 8848) or 8848)

    app = create_app()
    init_runtime(app)

    port = _ensure_port_free(port, host, logger)   # 端口被占用时自动顺延
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = "http://%s:%d" % (url_host, port)
    logger.info("下载目录：%s", config.download_dir())
    logger.info("临时目录：%s", config.temp_dir())
    logger.info("Web 界面：%s", url)

    # 浏览器行为：
    #   默认（auto_open_browser=false）只打印地址，由你自己点开 —— 和参考项目一致，
    #   不会去动你正在用的浏览器实例，因此不会触发「现有实例以提升的权限运行」那种弹窗。
    #   想让它自动打开时，用 --open-browser（走独立配置目录，同样不碰你现有的实例）。
    should_open = args.open_browser or (
        not args.no_browser and bool(config.get("auto_open_browser", False)))
    if should_open:
        def open_later():
            time.sleep(1.0)
            from core.browser import open_url
            open_url(url)

        threading.Thread(target=open_later, daemon=True).start()
    else:
        logger.info("请用浏览器访问：%s（如需自动打开，加 --open-browser）", url)

    try:
        socketio.run(app, host=host, port=port, debug=args.debug,
                     allow_unsafe_werkzeug=True, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出...")
    except OSError as exc:
        logger.error("启动失败：%s（端口 %s 可能被占用）", exc, port)
        return 1
    finally:
        from web.app import shutdown_runtime
        shutdown_runtime()
    return 0


if __name__ == "__main__":
    sys.exit(main())
