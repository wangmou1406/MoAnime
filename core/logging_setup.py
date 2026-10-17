# -*- coding: utf-8 -*-
"""日志：控制台 + 文件 + 内存环形缓冲（供 Web 实时查看）。"""
import collections
import logging
import os
import threading
import time
import warnings

# ---------------------------------------------------------------- 静音噪音警告
# urllib3 会对**每一个**「跳过证书校验」的请求打一条 InsecureRequestWarning。
# 本程序访问的资源站证书经常不合规（自签、过期、域名不匹配），默认 verify_ssl=False，
# 于是每下载一个分片就刷一条警告 —— 一集 382 个分片就是 382 条。
# 实测：这套警告一天能往 stderr 里写 750 MB，把真正的错误彻底埋掉。
#
# 这类警告在这里属于**预期行为**，不是问题，所以统一静音。
# 需要看 TLS 细节时，把 log_level 调到 DEBUG 或临时注释掉这一段即可。
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings("ignore",
                            category=urllib3.exceptions.InsecureRequestWarning)
except Exception:  # noqa: BLE001  没装 urllib3 或版本不同，忽略即可
    pass

from .config import LOG_FILE, config

_RING = collections.deque(maxlen=800)
_RING_LOCK = threading.RLock()
_SEQ = [0]


class RingHandler(logging.Handler):
    """把日志写入内存环形缓冲，并通过 broadcaster 推送到前端。"""

    def __init__(self):
        super().__init__()
        self.broadcaster = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        with _RING_LOCK:
            _SEQ[0] += 1
            entry = {
                "id": _SEQ[0],
                "time": time.strftime("%H:%M:%S"),
                "level": record.levelname,
                "name": record.name,
                "message": message,
            }
            _RING.append(entry)
        if self.broadcaster:
            try:
                self.broadcaster(entry)
            except Exception:
                pass


def recent_logs(limit: int = 300, after_id: int = 0, level: str = None):
    with _RING_LOCK:
        items = list(_RING)
    if after_id:
        items = [item for item in items if item["id"] > after_id]
    if level and level.upper() != "ALL":
        levels = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
        min_level = levels.get(level.upper(), 20)
        items = [item for item in items if levels.get(item["level"], 20) >= min_level]
    return items[-limit:]


def clear_logs() -> None:
    """清空日志：**内存环形缓冲 + 磁盘文件**。

    只清内存是不够的。恢复出厂设置（factory-reset）要清日志的目的写在
    那条调用的注释里：「免得里面还留着旧的关键字」—— 可是内存清了、
    app.log 里的历史内容还在，还带着 app.log.1/.2/.3 三个轮转备份，
    等于没清干净（实测踩过）。

    磁盘这边不能直接删文件：RotatingFileHandler 正开着它，
    Windows 上删除会失败。所以走「截断已打开的流」这条路。
    """
    with _RING_LOCK:
        _RING.clear()

    # 截断所有文件 handler 的流（console handler 没有 stream 属性，自动跳过）
    targets = []
    for name in ("moanime", ""):
        targets.append(logging.getLogger(name))
    seen = set()
    for logger_obj in targets:
        for handler in list(logger_obj.handlers):
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            stream = getattr(handler, "stream", None)
            if stream is None or not hasattr(stream, "truncate"):
                continue
            try:
                handler.acquire()
                try:
                    stream.seek(0)
                    stream.truncate(0)
                finally:
                    handler.release()
            except Exception:  # noqa: BLE001  单个 handler 失败不影响其它
                pass

    # 顺手删掉轮转备份（app.log.1 / .2 / .3），否则旧内容还能翻出来
    try:
        log_dir = os.path.dirname(LOG_FILE)
        base = os.path.basename(LOG_FILE)
        if os.path.isdir(log_dir):
            for name in os.listdir(log_dir):
                if name.startswith(base + ".") and name != base:
                    try:
                        os.remove(os.path.join(log_dir, name))
                    except OSError:
                        pass
    except Exception:  # noqa: BLE001
        pass


_configured = False


def setup_logging(name: str = "moanime") -> logging.Logger:
    """初始化根日志：控制台 + 滚动文件 + 环形缓冲。"""
    global _configured
    root = logging.getLogger(name)
    if _configured:
        return root
    _configured = True

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    level = getattr(logging, str(config.get("log_level", "INFO")).upper(), logging.INFO)
    root.setLevel(logging.DEBUG)
    root.propagate = False

    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(name)-14s %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024,
                                           backupCount=3, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:
        pass

    ring = RingHandler()
    ring.setLevel(logging.DEBUG)
    ring.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(ring)

    # 降低第三方库噪音
    for noisy in ("urllib3", "werkzeug", "requests", "engineio", "socketio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._ring_handler = ring  # type: ignore[attr-defined]
    return root


def get_logger(name: str = "moanime") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


def set_broadcaster(callback) -> None:
    """由 Web 层注入：日志 -> Socket.IO 广播。"""
    root = setup_logging()
    handler = getattr(root, "_ring_handler", None)
    if handler is not None:
        handler.broadcaster = callback
