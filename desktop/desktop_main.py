# -*- coding: utf-8 -*-
"""动漫下载机 · 桌面版入口。

和 Python 版（run.py）的区别
----------------------------
| | Python 版 run.py | 桌面版 desktop_main.py |
|---|---|---|
| 界面 | 自己开浏览器，手动输 127.0.0.1:8848 | 自动弹出**无地址栏的应用窗口** |
| 数据目录 | 程序目录下 data/ | %LOCALAPPDATA%\\AnimeDownloader\\data（升级不丢） |
| 退出 | Ctrl+C / 关浏览器标签（服务还在跑） | 关窗口即退出，会停掉下载管理器 |

为什么用 Edge/Chrome 的 ``--app=`` 而不是 pywebview
--------------------------------------------------
pywebview 在 Windows 上靠 pythonnet 调 .NET WebView2，而 pythonnet 的
原生加载器**无法从含中文/空格的路径加载 Python.Runtime.dll**（本项目路径是
``项目文件\\DeepSeek Harness\\软件\\...``，实测 ``import clr`` 直接失败）。

``--app=`` 方式没有这个问题：
  * Edge 是 Windows 10/11 自带，不需要额外运行库；
  * 用**独立的 --user-data-dir**，不会碰用户正在用的浏览器会话；
  * 窗口没有地址栏/标签栏，看起来就是个应用；
  * 进程生命周期明确 —— 关掉最后一个窗口，浏览器进程退出，我们也跟着收尾。

如果将来要换成真正的原生窗口，把 :func:`_open_app_window` 换成 pywebview
即可，其余逻辑不用动。
"""
import os
import subprocess
import sys
import threading
import time

APP_NAME = "AnimeDownloader"


def _bundle_dir() -> str:
    """程序资源所在目录（PyInstaller onedir 下是 _internal/）。

    开发态：本文件在 ``<项目根>\\desktop\\desktop_main.py``，
    所以往上退**两级**就是项目根。
    （早先这个文件在 ``packaging/desktop/`` 下，退三级才对；搬家后忘了改，
      结果 sys.path 指到了项目上级目录，import core 直接失败 —— 已修正。）
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pick_data_dir() -> str:
    """挑一个**确实能写**的数据目录。

    按优先级试几个位置：某些环境（受限账户、漫游配置文件、企业 ACL）
    会让 %LOCALAPPDATA% 下的子目录建不出来，直接崩掉太难看，所以逐级回退。
    """
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    roaming = os.environ.get("APPDATA")
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if local:
        candidates.append(os.path.join(local, APP_NAME, "data"))
    if roaming:
        candidates.append(os.path.join(roaming, APP_NAME, "data"))
    if temp:
        candidates.append(os.path.join(temp, APP_NAME, "data"))
    # 最后的兜底：放在程序旁边（便携式使用时合理）
    candidates.append(os.path.join(_bundle_dir(), "data"))

    for path in candidates:
        try:
            os.makedirs(os.path.join(path, "logs"), exist_ok=True)
            probe = os.path.join(path, ".write-probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return path
        except Exception:  # noqa: BLE001  换下一个候选
            continue
    return candidates[0]


def _prepare_env() -> None:
    """在 import core.config **之前**把目录定好（config 在 import 时会读这些变量）。"""
    root = _bundle_dir()
    if root not in sys.path:
        sys.path.insert(0, root)

    data_dir = _pick_data_dir()
    log_dir = os.path.join(data_dir, "logs")
    os.environ.setdefault("AD_DATA_DIR", data_dir)
    os.environ.setdefault("AD_LOG_DIR", log_dir)

    # 下载目录：只在**首次运行**时给个可写的默认值。
    # 一旦 settings.json 存在就不再设 —— 否则环境变量会永久压住界面里的
    # 「下载位置」设置，用户改了也不生效。
    settings_file = os.path.join(data_dir, "settings.json")
    if not os.path.exists(settings_file) and not os.environ.get("AD_DOWNLOAD_DIR"):
        os.environ["AD_DOWNLOAD_DIR"] = os.path.join(
            os.path.expanduser("~"), "Downloads", APP_NAME)


def _pick_port(host: str, preferred: int = 8848) -> int:
    import socket
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    return preferred


def _wait_ready(url: str, timeout: float = 40.0) -> bool:
    import json
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/meta", timeout=2) as resp:
                if json.loads(resp.read().decode("utf-8", "ignore")).get("data"):
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def _find_browser() -> str:
    """找一个支持 --app= 的 Chromium 系浏览器（Edge 优先，系统自带）。"""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    # 最后退回 PATH 里找
    import shutil
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _open_app_window(url: str, profile_dir: str, logger) -> int:
    """用浏览器开一个「无地址栏的应用窗口」，阻塞到窗口关闭。"""
    browser = _find_browser()
    if not browser:
        # 没有 Chromium 系浏览器就退回默认浏览器（此时只能靠关掉控制台退出）
        logger.warning("没找到 Edge/Chrome，改用系统默认浏览器打开：%s", url)
        try:
            os.startfile(url)  # noqa: S606  Windows 专用
        except Exception as exc:  # noqa: BLE001
            logger.error("打开浏览器失败：%s", exc)
        # 没有窗口可等，就保持服务运行直到进程被杀
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
        return 0

    os.makedirs(profile_dir, exist_ok=True)
    args = [
        browser,
        "--app=" + url,
        "--user-data-dir=" + profile_dir,       # 独立配置，不碰用户的浏览器会话
        "--window-size=1320,880",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-extensions",
    ]
    logger.info("启动应用窗口：%s", os.path.basename(browser))
    try:
        proc = subprocess.Popen(args, close_fds=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("启动浏览器失败：%s", exc)
        return 1
    try:
        proc.wait()          # 窗口关闭 → 进程退出 → 这里返回
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    return 0


def _crash_log_path() -> str:
    """崩溃日志放在 app.log 旁边；拿不到就退到临时目录。

    优先用 AD_LOG_DIR —— 那才是程序实际选定的日志目录（_pick_data_dir 已经
    验证过它可写）。早先这里硬编码 %LOCALAPPDATA%，一旦该路径不可写，
    崩溃日志会静默丢失，等于没有。
    """
    candidates = []
    log_dir = os.environ.get("AD_LOG_DIR")
    if log_dir:
        candidates.append(log_dir)
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    candidates.append(os.path.join(local, APP_NAME, "data", "logs"))
    candidates.append(os.environ.get("TEMP") or os.path.expanduser("~"))
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            return os.path.join(base, "crash.log")
        except Exception:  # noqa: BLE001
            continue
    return "crash.log"


def _write_crash(exc_type, exc, tb) -> None:
    """把异常写进 crash.log。

    桌面版是按 console=False 打包的 —— 没有控制台，异常只能靠这个文件看，
    否则双击后「什么都没发生」，完全没法排查。
    """
    import traceback
    try:
        with open(_crash_log_path(), "a", encoding="utf-8") as fh:
            fh.write("\n==== %s ====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            fh.write("frozen=%s  exe=%s  cwd=%s\n"
                     % (getattr(sys, "frozen", False), sys.executable, os.getcwd()))
            traceback.print_exception(exc_type, exc, tb, file=fh)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    _prepare_env()

    from core.config import VERSION, config
    from core.logging_setup import get_logger
    from web.app import create_app, init_runtime, shutdown_runtime, socketio

    logger = get_logger("moanime.desktop")
    logger.info("=" * 62)
    logger.info("动漫下载机 %s 桌面版启动中...", VERSION)

    host = "127.0.0.1"
    port = _pick_port(host, int(config.get("web_port", 8848) or 8848))
    url = "http://%s:%d" % (host, port)

    app = create_app()
    init_runtime(app)

    def serve():
        try:
            socketio.run(app, host=host, port=port, debug=False,
                         allow_unsafe_werkzeug=True, use_reloader=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("Web 服务退出：%s", exc)

    threading.Thread(target=serve, name="web-server", daemon=True).start()

    if not _wait_ready(url):
        logger.error("Web 服务 40 秒内没有就绪，退出")
        try:
            shutdown_runtime()
        except Exception:  # noqa: BLE001
            pass
        return 1

    logger.info("界面地址：%s", url)
    logger.info("数据目录：%s", os.environ.get("AD_DATA_DIR"))

    # 浏览器配置放数据目录旁边，卸载时可一并删掉
    profile_dir = os.path.join(os.path.dirname(
        os.environ.get("AD_DATA_DIR", ".")), "browser-profile")
    code = _open_app_window(url, profile_dir, logger)

    logger.info("窗口已关闭，正在停止后台任务…")
    try:
        shutdown_runtime()
    except Exception:  # noqa: BLE001
        pass
    return code


if __name__ == "__main__":
    # 桌面版没有控制台，任何未捕获异常都必须落盘，否则双击后「静默失败」
    sys.excepthook = _write_crash
    try:
        import threading as _threading
        _threading.excepthook = lambda a: _write_crash(
            a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:  # noqa: BLE001  Python < 3.8
        pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        _write_crash(*sys.exc_info())
        raise
