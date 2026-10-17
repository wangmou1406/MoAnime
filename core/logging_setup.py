# -*- coding: utf-8 -*-
"""日志：控制台 + 文件 + 内存环形缓冲（供 Web 实时查看）。"""
import collections
import logging
import os
import threading
import time

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
    with _RING_LOCK:
        _RING.clear()


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
