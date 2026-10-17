# -*- coding: utf-8 -*-
"""播放器嗅探：用无头浏览器打开播放页，监听网络请求截获真实视频地址。

为什么需要它
------------
部分动漫站的视频地址由站点自己的混淆 JS 现场算出（会话签名、AES 解密），
**无法用「HTTP + 正则」复刻**：

* **会话签名**：Anime1 的播放器只有 ``<video data-apireq="<编码令牌>">``，
  令牌里的时间戳 ``t`` 与会话签名 ``s`` 由页面 JS 现场生成，直接 POST 接口 403；
* **混淆解密**：次元城把地址藏在混淆函数里，是字符串数组混淆的；
* **客户端渲染**：次元城首页只返回 1729 字节空壳，内容全靠 JS 拉。

与其逐站逆向，不如换个思路：

    不需要知道地址是怎么算出来的 —— 只要看浏览器最终请求了哪个视频地址。

站点 JS 在浏览器里正常执行，我们只在旁边监听网络请求，截获
``.m3u8`` / ``.mp4``。这一招**不需要为每个站写适配**。

与「Node 桥接」的对比
--------------------
有的同类软件（如 XiaoMoAnime）为每个站写一份 Node mock 脚本，把网站的混淆
JS 拿到本地跑。那条路可行，但要**每站 13~56KB 手工代码**，网站改版就得重做。
这里用浏览器嗅探：一次实现覆盖所有站，代价是需要一个 Chromium 内核。

实现
----
``tools/player_sniffer.js`` 是 Node 常驻进程：保持一个浏览器实例，
通过 stdin 收任务、stdout 回 JSON。这里负责启动、发任务、收结果、
缓存、超时，以及进程崩掉后自动重启。

依赖
----
Node.js + playwright-core + 一个 Chromium 内核（Edge/Chrome 均可，不需额外下载）。
缺失时 :func:`available` 返回不可用，调用方回退到纯 HTTP 方式。
"""
import json
import os
import shutil
import subprocess
import threading
import time

from core.config import config as app_config
from core.logging_setup import get_logger

logger = get_logger("browser")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT_DIR, "tools", "player_sniffer.js")

# 结果缓存：同一播放页短期内重复解析（重试、换线路）直接命中
CACHE_TTL = 600
_CACHE = {}
_CACHE_LOCK = threading.Lock()

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]

# ---------------------------------------------------------------- 内置内核
# 程序自带一份 Chromium 与 Node，开箱即用，不再依赖用户机器上装没装 Edge。
# 找不到内置的才退回系统浏览器。
#
# 目录可用 AD_BUNDLED_DIR 覆盖：
#   * 本地 Windows 版放在 data/_pwt（默认）；
#   * Docker 镜像里 data/ 是挂载卷，放那儿会被卷遮住，
#     所以镜像把它装在 /opt/pwt 并用 AD_BUNDLED_DIR 指过去。
BUNDLED_DIR = (os.environ.get("AD_BUNDLED_DIR") or "").strip() \
    or os.path.join(ROOT_DIR, "data", "_pwt")
BROWSERS_DIR = os.path.join(BUNDLED_DIR, "browsers")


def _bundled_browser() -> str:
    """内置 Chromium 的可执行文件（目录名带版本号，所以扫描而非写死）。"""
    if not os.path.isdir(BROWSERS_DIR):
        return ""
    # 先看环境变量（便于测试别的构建）
    env = (os.environ.get("AD_BUNDLED_BROWSER") or "").strip()
    if env and os.path.exists(env):
        return env
    try:
        names = sorted(os.listdir(BROWSERS_DIR))
    except OSError:
        return ""
    rels = (
        # Windows（旧版叫 chrome-win，新版叫 chrome-win64）
        os.path.join("chrome-win64", "chrome.exe"),
        os.path.join("chrome-win", "chrome.exe"),
        # Linux：新版 Playwright 解出来是 chrome-linux64，旧版是 chrome-linux
        os.path.join("chrome-linux64", "chrome"),
        os.path.join("chrome-linux", "chrome"),
        # macOS
        os.path.join("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
        os.path.join("chrome-mac", "Google Chrome for Testing.app",
                     "Contents", "MacOS", "Google Chrome for Testing"),
    )
    for name in names:
        if not name.startswith("chromium-"):
            continue
        for rel in rels:
            path = os.path.join(BROWSERS_DIR, name, rel)
            if os.path.exists(path):
                return path
    return ""


def _bundled_node() -> str:
    """内置 Node 可执行文件。

    两种放法都认：
      * node/node.exe 或 node/node       —— 直接拷一个可执行文件进去（Windows 版）；
      * node/bin/node                    —— 官方 Linux 压缩包解出来的结构（Docker 版）。
    """
    for rel in (("node", "node.exe"), ("node", "node"), ("node", "bin", "node")):
        path = os.path.join(BUNDLED_DIR, *rel)
        if os.path.exists(path):
            return path
    return ""


def find_browser() -> str:
    env = (os.environ.get("AD_BROWSER_PATH") or "").strip()
    if env and os.path.exists(env):
        return env
    bundled = _bundled_browser()
    if bundled:                      # 内置优先：不依赖用户装没装 Edge
        return bundled
    for path in BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def find_node() -> str:
    env = (os.environ.get("AD_NODE_PATH") or "").strip()
    if env and os.path.exists(env):
        return env
    bundled = _bundled_node()
    if bundled:                      # 内置优先：不依赖用户装没装 Node
        return bundled
    for name in ("node", "nodejs", "node.exe"):
        found = shutil.which(name)
        if found:
            return found
    for path in (r"C:\Program Files\nodejs\node.exe",
                 r"C:\Program Files (x86)\nodejs\node.exe"):
        if os.path.exists(path):
            return path
    return ""


def is_bundled_browser(path: str) -> bool:
    """这个路径是不是程序自带的内核（界面用来显示「内置」还是「系统」）。"""
    if not path:
        return False
    try:
        return os.path.abspath(path).startswith(os.path.abspath(BROWSERS_DIR))
    except (OSError, ValueError):
        return False


def playwright_dir() -> str:
    """playwright-core 所在目录（可放项目内 data/_pwt，或用环境变量指定）。"""
    env = (os.environ.get("AD_PLAYWRIGHT_DIR") or "").strip()
    if env and os.path.exists(env):
        return env
    local = os.path.join(ROOT_DIR, "data", "_pwt", "node_modules", "playwright-core")
    return local if os.path.exists(local) else ""


def enabled() -> bool:
    """总开关：用户在设置里关掉后，所有源都退回纯 HTTP 方式。"""
    value = app_config.get("browser_resolver")
    if value is None:
        return True          # 默认开启；环境不满足时会自动判定不可用
    return bool(value)


def available() -> dict:
    """检查嗅探能力是否就绪，返回详情供界面显示。"""
    node = find_node()
    browser = find_browser()
    pw = playwright_dir()
    ok = bool(node and browser and pw and os.path.exists(SCRIPT))
    reason = ""
    if not node:
        reason = "未找到 Node.js（内置与系统均无）"
    elif not browser:
        reason = "未找到 Chromium 内核（内置与系统 Edge/Chrome 均无）"
    elif not pw:
        reason = "未安装 playwright-core"
    elif not os.path.exists(SCRIPT):
        reason = "缺少 tools/player_sniffer.js"
    return {"ok": ok, "enabled": enabled(), "node": node, "browser": browser,
            "playwright": pw, "reason": reason,
            "builtin_browser": is_bundled_browser(browser),
            "builtin_node": bool(node) and _bundled_node() == node,
            "usable": ok and enabled()}


class _Sniffer:
    """常驻嗅探进程的包装（单例使用）。"""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._seq = 0
        self._results = {}
        self._events = {}
        self._reader = None
        self._ready = threading.Event()
        self._error = ""
        self._last_used = 0.0

    # ---------------------------------------------------------- 生命周期
    def _start(self) -> bool:
        if self._proc and self._proc.poll() is None:
            return True
        info = available()
        if not info["ok"]:
            self._error = info["reason"]
            return False
        try:
            self._proc = subprocess.Popen(
                [info["node"], SCRIPT, "--browser", info["browser"]],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                bufsize=1, cwd=ROOT_DIR,
                env={**os.environ, "AD_PLAYWRIGHT_DIR": info["playwright"]},
            )
        except Exception as exc:  # noqa: BLE001
            self._error = "启动嗅探进程失败：%s" % exc
            logger.warning(self._error)
            return False
        self._ready.clear()
        self._reader = threading.Thread(target=self._read_loop, args=(self._proc,),
                                        daemon=True)
        self._reader.start()
        # 等 ready（首次启动浏览器较慢）
        if not self._ready.wait(timeout=30):
            self._error = self._error or "嗅探进程启动超时"
            return False
        return self._proc.poll() is None

    def _read_loop(self, proc):
        try:
            for line in proc.stdout:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if obj.get("type") == "ready":
                    self._ready.set()
                    continue
                if obj.get("type") == "error":
                    self._error = obj.get("error") or "嗅探进程报错"
                    self._ready.set()
                    continue
                rid = obj.get("id")
                if rid is None:
                    continue
                self._results[rid] = obj
                ev = self._events.get(rid)
                if ev:
                    ev.set()
        except Exception:  # noqa: BLE001
            pass
        finally:
            # 进程退出：唤醒所有等待者，避免调用方卡死
            self._ready.set()
            for ev in list(self._events.values()):
                ev.set()

    def stop(self):
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    def sniff(self, url: str, wait_ms: int = 12000, timeout: float = 60.0) -> dict:
        with self._lock:
            if not self._start():
                return {"ok": False, "error": self._error or "嗅探能力不可用"}
            self._seq += 1
            rid = self._seq
            ev = threading.Event()
            self._events[rid] = ev
            try:
                self._proc.stdin.write(json.dumps(
                    {"id": rid, "url": url, "wait": wait_ms}) + "\n")
                self._proc.stdin.flush()
            except Exception as exc:  # noqa: BLE001
                self._events.pop(rid, None)
                self.stop()
                return {"ok": False, "error": "写入嗅探进程失败：%s" % exc}

        got = ev.wait(timeout=timeout)
        result = self._results.pop(rid, None)
        self._events.pop(rid, None)
        self._last_used = time.time()
        if not got or result is None:
            return {"ok": False, "error": "嗅探超时（%.0fs）" % timeout}
        return result


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def _sniffer() -> _Sniffer:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = _Sniffer()
        return _INSTANCE


def resolve_with_browser(page_url: str, wait_ms: int = 12000,
                         use_cache: bool = True) -> dict:
    """用无头浏览器打开 page_url，返回截获到的视频地址。

    返回 ``{"ok": True, "video": "...", "kind": "m3u8|mp4",
    "referer": ..., "cookies": ..., "ua": ...}`` 或 ``{"ok": False, "error": ...}``。
    """
    if not page_url:
        return {"ok": False, "error": "缺少播放页地址"}

    if not enabled():
        return {"ok": False, "error": "浏览器取流已在设置中关闭"}

    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(page_url)
            if hit and time.time() - hit["at"] < CACHE_TTL:
                return hit["value"]

    info = available()
    if not info["ok"]:
        return {"ok": False, "error": info["reason"]}

    result = _sniffer().sniff(page_url, wait_ms=wait_ms)
    if use_cache and result.get("ok"):
        with _CACHE_LOCK:
            _CACHE[page_url] = {"at": time.time(), "value": result}
    logger.info("嗅探 %s -> %s", page_url[:70],
                (result.get("video") or result.get("error") or "")[:90])
    return result


def shutdown():
    """服务退出时调用，关掉浏览器进程。"""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is not None:
            _INSTANCE.stop()
            _INSTANCE = None


def cache_size() -> int:
    with _CACHE_LOCK:
        return len(_CACHE)


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()
