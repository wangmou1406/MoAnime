# -*- coding: utf-8 -*-
"""全局配置：路径定义与可持久化设置项。"""
import copy
import json
import os
import threading

# ---------------------------------------------------------------- 路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)

# 允许用环境变量把数据/日志/下载目录挪出去，方便 Docker 挂卷：
#   AD_DATA_DIR / AD_LOG_DIR / AD_DOWNLOAD_DIR
DATA_DIR = os.environ.get("AD_DATA_DIR") or os.path.join(ROOT_DIR, "data")
LOG_DIR = os.environ.get("AD_LOG_DIR") or os.path.join(ROOT_DIR, "logs")
DOWNLOAD_DIR = os.environ.get("AD_DOWNLOAD_DIR") or os.path.join(ROOT_DIR, "downloads")
STATIC_DIR = os.path.join(ROOT_DIR, "static")
TEMPLATE_DIR = os.path.join(ROOT_DIR, "templates")

SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
# 数据库文件名与项目名保持一致（anime-downloader）
DATABASE_FILE = os.path.join(DATA_DIR, "anime-downloader.db")

# 局域网访问通过后种的 cookie 名（放在这里是为了让 app 与 settings 路由都能用，
# 避免它们互相 import）
TOKEN_COOKIE = "moanime_token"
# 旧版本用的库名：首次启动若只发现旧库，会自动改名，避免丢掉已有数据
LEGACY_DATABASE_FILE = os.path.join(DATA_DIR, "moanime.db")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

def _ensure_dirs() -> None:
    """建好数据/日志目录。目录不可用时只告警，不让程序起不来。"""
    for name, path in (("数据", DATA_DIR), ("日志", LOG_DIR)):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            import sys
            print("警告：%s目录不可用（%s）：%s" % (name, path, exc), file=sys.stderr)


_ensure_dirs()


def _migrate_database_name() -> None:
    """把旧库文件名迁移成新名字（连同 WAL/SHM 一起，保证数据不丢）。"""
    if os.path.exists(DATABASE_FILE) or not os.path.exists(LEGACY_DATABASE_FILE):
        return
    import shutil
    for suffix in ("", "-wal", "-shm"):
        src = LEGACY_DATABASE_FILE + suffix
        if os.path.exists(src):
            try:
                shutil.move(src, DATABASE_FILE + suffix)
            except OSError:
                pass


_migrate_database_name()

# 这个版本号同时用作静态资源的缓存清除参数（templates/index.html 里的 ?v=），
# 前端有重要改动时顺手加一，确保用户一定拿到新文件而不是缓存的旧版。
VERSION = "3.2.1"
APP_NAME = "动漫下载机"                 # 界面显示用中文名
APP_NAME_EN = "Anime Downloader"        # 对应英文名

# ---------------------------------------------------------------- 默认设置
# 免责声明的条款版本。修改条款正文时把这个数字 +1，
# 已同意的用户会被要求重新确认。
DISCLAIMER_VERSION = 1

DEFAULT_SETTINGS = {
    # 下载（Docker 里可用 AD_DOWNLOAD_DIR 指定，例如挂到 /downloads）
    "download_dir": DOWNLOAD_DIR,
    "temp_dir": os.path.join(DOWNLOAD_DIR, ".tmp"),
    "naming_scheme": "simple",          # simple / jellyfin / emby / plex / fnos
    "default_season": 1,
    "organize_by_anime": True,          # 每部动漫一个文件夹
    "skip_existing": True,              # 已存在文件自动跳过
    "filename_max_length": 120,

    # 并发与网络
    "concurrent_tasks": 3,              # 同时下载的任务(剧集)数
    "segment_threads": 8,               # 单个任务的分片并发数
    "max_retries": 3,
    "request_timeout": 15,
    "chunk_size": 262144,               # 普通直链下载块大小
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "proxy": "",                        # 例：http://127.0.0.1:7890
    "verify_ssl": False,

    # 源与搜索
    "search_concurrency": 5,            # 聚合搜索时同时请求的源数量
    "source_timeout": 15,
    "merge_duplicates": False,          # 聚合结果里同名动漫是否合并
    "search_history_limit": 30,
    "auto_detect_sources": True,        # 允许“自动扫描”生成源配置

    # 下载后处理
    "remux_to_mp4": True,               # 存在 ffmpeg 时将 ts 合并为 mp4
    "delete_ts_after_remux": True,
    "keep_temp_segments": False,
    "verify_video": True,               # 合并后校验文件
    # 整理完自动去重：同一集有多份时把多余的挪到 _重复/（不删除）
    "dedup_on_organize": True,
    # 弹幕存成什么：xml（播放器通用）/ ass（可直接压进视频）/ both
    "danmaku_format": "xml",
    # 外部弹幕服务（danmu_api 之类，弹弹play 兼容接口）。填了就优先用它 ——
    # 它能一次覆盖爱优腾芒等十几个平台；留空则只用 B 站。
    # 形如 http://192.168.1.7:9321/87654321（末段是 token 路径，默认值可省）
    "danmaku_service": "",

    # 外观与服务
    "theme": "light",                   # light | dark
    "bg_image": "",                     # 自定义背景图：上传后的文件名，或 http(s) 地址
    "bg_blur": 0,                       # 背景模糊（px，0~40）
    "bg_dim": 0,                        # 背景遮罩强度（0~100，越大越暗/越不抢眼）
    "bg_fit": "cover",                  # cover | contain | repeat
    "bg_panel_alpha": 88,               # 面板不透明度（0~100，越小越能透出背景）
    "title_text": "",                   # 自定义标题（留空用默认 动漫下载机）
    "title_color": "",                  # 自定义标题颜色（留空跟随主题）
    # 页面颜色（留空则跟随亮/暗主题；填了就不分主题，始终生效）
    "palette": "default",               # 配色方案预设 key（default/ink/paper/... 或 custom）
    "accent_color": "",                 # 主色：按钮、选中态、进度条、链接
    "bg_color": "",                     # 页面底色
    "panel_color": "",                  # 卡片/面板底色
    "panel_2_color": "",                # 次级底色：表头、统计格、日志框
    # 动效
    "animations": "full",               # full 全部 | subtle 仅状态 | off 关闭
    # 浏览器取流（播放器嗅探）：只对声明了需要的源生效；
    # 关掉后这些源会退回纯 HTTP 方式（抓不到就报错），普通源不受影响
    "browser_resolver": True,
    "web_host": "127.0.0.1",
    "web_port": 8848,
    # 局域网访问令牌：留空 = 不校验（只适合完全可信的网络）。
    # 非空时，来自本机以外的请求必须带对令牌，否则只能看到提示页。
    "access_token": "",
    # 默认不自动打开浏览器：只打印地址，避免去驱动你正在用的浏览器实例
    #（那个实例若以管理员权限运行，Windows 会弹「是否用普通权限重启」的对话框）
    "auto_open_browser": False,
    "log_level": "INFO",
    "max_log_lines": 500,
    # 首次使用必须同意免责声明；用户点「同意」后写入 True
    "disclaimer_accepted": False,
    "disclaimer_version": 0,            # 条款版本号；改条款内容时可提高以强制重新确认
}

_LOCK = threading.RLock()

# 目录不可用时的告警去重集合（见 Config._warn_once）
_WARNED = set()


class Config:
    """线程安全的设置读写器（JSON 落盘）。"""

    def __init__(self, path: str = SETTINGS_FILE):
        self.path = path
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        self.load()
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """环境变量优先级最高，专供 Docker / 容器部署使用。

        在容器里通常直接把卷挂到 /data、/downloads，用环境变量覆盖最省事，
        不需要进界面改设置（改了也会被下次启动覆盖，符合容器"声明式"的习惯）。
        """
        mapping = {
            "AD_DOWNLOAD_DIR": "download_dir",
            "AD_TEMP_DIR": "temp_dir",
            "AD_HOST": "web_host",
            "AD_PORT": "web_port",
            "AD_CONCURRENT_TASKS": "concurrent_tasks",
            "AD_SEGMENT_THREADS": "segment_threads",
            "AD_LOG_LEVEL": "log_level",
            "AD_USER_AGENT": "user_agent",
            "AD_PROXY": "proxy",
            "AD_THEME": "theme",
        }
        for env_key, setting_key in mapping.items():
            raw = os.environ.get(env_key)
            if raw is None or raw == "":
                continue
            if setting_key in ("web_port", "concurrent_tasks", "segment_threads"):
                try:
                    self._data[setting_key] = int(raw)
                    continue
                except ValueError:
                    continue
            if setting_key == "verify_ssl":
                self._data[setting_key] = raw.lower() in ("1", "true", "yes", "on")
                continue
            self._data[setting_key] = raw

        # 只指定了下载目录时，临时目录跟着它走（否则分片会落到别处）
        if os.environ.get("AD_DOWNLOAD_DIR") and not os.environ.get("AD_TEMP_DIR"):
            self._data["temp_dir"] = os.path.join(self._data["download_dir"], ".tmp")

    # ------------------------------------------------------------ 基础
    def load(self) -> dict:
        with _LOCK:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        saved = json.load(fh)
                    if isinstance(saved, dict):
                        for key, value in saved.items():
                            self._data[key] = value
                except Exception:
                    pass
            elif os.path.exists(os.path.join(ROOT_DIR, "settings.json")):
                # 兼容旧版 settings.json
                try:
                    with open(os.path.join(ROOT_DIR, "settings.json"), "r", encoding="utf-8") as fh:
                        legacy = json.load(fh)
                    if isinstance(legacy, dict):
                        if legacy.get("download_folder"):
                            self._data["download_dir"] = os.path.abspath(
                                os.path.join(ROOT_DIR, legacy["download_folder"]))
                        if legacy.get("naming_scheme"):
                            self._data["naming_scheme"] = legacy["naming_scheme"]
                        if legacy.get("max_threads"):
                            self._data["segment_threads"] = legacy["max_threads"]
                        if legacy.get("concurrent_episodes"):
                            self._data["concurrent_tasks"] = legacy["concurrent_episodes"]
                except Exception:
                    pass
            return dict(self._data)

    def save(self) -> None:
        with _LOCK:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ------------------------------------------------------------ 访问
    def get(self, key: str, default=None):
        with _LOCK:
            return self._data.get(key, DEFAULT_SETTINGS.get(key, default))

    def __getitem__(self, key):
        return self.get(key)

    def set(self, key: str, value, persist: bool = True) -> None:
        with _LOCK:
            self._data[key] = value
        if persist:
            self.save()

    def update(self, values: dict, persist: bool = True) -> dict:
        with _LOCK:
            for key, value in (values or {}).items():
                if key in DEFAULT_SETTINGS or key.startswith("custom_"):
                    self._data[key] = value
        if persist:
            self.save()
        return dict(self._data)

    def reset(self) -> dict:
        with _LOCK:
            self._data = copy.deepcopy(DEFAULT_SETTINGS)
        self.save()
        return dict(self._data)

    def all(self) -> dict:
        with _LOCK:
            return copy.deepcopy(self._data)

    # ------------------------------------------------------------ 派生
    def _usable_dir(self, path: str, label: str, fallback: str) -> str:
        """确保目录存在**且可写**，不行就退到 fallback。

        为什么不能只 makedirs：
            Windows 上目录"能创建"不等于"能写" —— 只读网络盘、受控文件夹访问、
            企业 ACL 都可能让你建好了却写不进去。更糟的是 makedirs 本身就会抛
            PermissionError（实测：装到只读位置时 %USERPROFILE%\\Downloads 被拒），
            而这个函数是在启动流程里被调的，一抛就把整个程序带崩。
        所以这里做「创建 + 真实写入探测」，失败就换备用目录并告警，绝不抛。
        """
        candidates = [path, fallback]
        last_error = None
        for candidate in candidates:
            try:
                os.makedirs(candidate, exist_ok=True)
                probe = os.path.join(candidate, ".write-probe")
                with open(probe, "w", encoding="utf-8") as fh:
                    fh.write("ok")
                os.remove(probe)
                if candidate != path:
                    self._warn_once(
                        "%s 不可用（%s），已临时改用 %s。"
                        "请在【设置 → 下载位置】里改成一个可写的路径。"
                        % (label, last_error, candidate))
                return candidate
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        # 连备用目录都不可写（几乎不可能），退到临时目录，仍然不抛
        import tempfile
        self._warn_once("%s 与备用目录都不可用，退到系统临时目录：%s" % (label, last_error))
        return tempfile.gettempdir()

    def _warn_once(self, message: str) -> None:
        """同一条告警只打一次，避免每集下载都刷屏。"""
        try:
            if message in _WARNED:
                return
            _WARNED.add(message)
            from .logging_setup import get_logger
            get_logger("moanime.config").warning(message)
        except Exception:  # noqa: BLE001
            pass

    def download_dir(self) -> str:
        path = self.get("download_dir") or DEFAULT_SETTINGS["download_dir"]
        return self._usable_dir(path, "下载目录",
                                os.path.join(DATA_DIR, "downloads"))

    def temp_dir(self) -> str:
        path = self.get("temp_dir") or DEFAULT_SETTINGS["temp_dir"]
        return self._usable_dir(path, "临时目录",
                                os.path.join(DATA_DIR, "temp"))

    def proxies(self):
        proxy = (self.get("proxy") or "").strip()
        return {"http": proxy, "https": proxy} if proxy else None


config = Config()
