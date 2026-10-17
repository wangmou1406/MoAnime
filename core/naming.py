# -*- coding: utf-8 -*-
"""命名规则：把动漫名 / 剧集名转换成下载路径。

支持的命名方案（naming_scheme）：
  simple    : 动漫名/第01集.mp4
  jellyfin  : 动漫名 (年份)/Season 01/动漫名 S01E01.mp4
  emby      : 同 jellyfin（Emby 兼容）
  plex      : 动漫名/Season 01/动漫名 - S01E01.mp4
  fnos      : 动漫名/Season 1/动漫名 S1E1.mp4
  anime     : 动漫名/动漫名 - 第01集.mp4
"""
import os

from .config import config
from .utils import episode_number, sanitize_filename

SCHEMES = ("simple", "jellyfin", "emby", "plex", "fnos", "anime")
_SCHEME_LABELS = {
    "simple": "简洁：动漫名/第01集.mp4",
    "jellyfin": "Jellyfin：动漫名/Season 01/动漫名 S01E01.mp4",
    "emby": "Emby：动漫名/Season 01/动漫名 S01E01.mp4",
    "plex": "Plex：动漫名/Season 01/动漫名 - S01E01.mp4",
    "fnos": "飞牛影视：动漫名/Season 1/动漫名 S1E1.mp4",
    "anime": "追番：动漫名/动漫名 - 第01集.mp4",
}


def scheme_labels() -> dict:
    return dict(_SCHEME_LABELS)


def normalize_scheme(scheme: str) -> str:
    scheme = (scheme or "simple").strip().lower()
    return scheme if scheme in SCHEMES else "simple"


def episode_ordinal(episode_name: str, fallback: float = 1.0):
    """返回 (序号文本, 是否为特殊集)。"""
    number = episode_number(episode_name, 0)
    if number <= 0:
        return None, True
    if abs(number - round(number)) < 1e-6:
        return int(round(number)), False
    return number, False


def build_episode_filename(anime_name: str, episode_name: str, scheme: str = None,
                           season: int = None, ext: str = "mp4", max_length: int = None) -> str:
    scheme = normalize_scheme(scheme or config.get("naming_scheme"))
    season = int(season if season is not None else config.get("default_season", 1) or 1)
    max_length = int(max_length or config.get("filename_max_length", 120) or 120)
    anime = sanitize_filename(anime_name, max_length)
    episode = sanitize_filename(episode_name, 60)
    ordinal, special = episode_ordinal(episode_name)
    ext = (ext or "mp4").lstrip(".")

    if special or ordinal is None:
        return sanitize_filename("%s - %s.%s" % (anime, episode, ext), max_length + 20)

    if scheme in ("jellyfin", "emby"):
        return "%s S%02dE%s.%s" % (anime, season, _pad(ordinal), ext)
    if scheme == "plex":
        return "%s - S%02dE%s.%s" % (anime, season, _pad(ordinal), ext)
    if scheme == "fnos":
        return "%s S%dE%s.%s" % (anime, season, _num(ordinal), ext)
    if scheme == "anime":
        return "%s - 第%s集.%s" % (anime, _pad(ordinal), ext)
    return "第%s集.%s" % (_pad(ordinal), ext)


def _pad(ordinal) -> str:
    if isinstance(ordinal, int):
        return "%02d" % ordinal
    return "%g" % ordinal


def _num(ordinal) -> str:
    return str(ordinal) if isinstance(ordinal, int) else ("%g" % ordinal)


def build_relative_path(anime_name: str, episode_name: str, ext: str = "mp4",
                        scheme: str = None, season: int = None) -> str:
    """相对于下载根目录的保存路径。"""
    scheme = normalize_scheme(scheme or config.get("naming_scheme"))
    season = int(season if season is not None else config.get("default_season", 1) or 1)
    max_length = int(config.get("filename_max_length", 120) or 120)
    anime = sanitize_filename(anime_name, max_length)
    organize = bool(config.get("organize_by_anime", True))
    filename = build_episode_filename(anime_name, episode_name, scheme, season, ext, max_length)

    parts = []
    if organize:
        parts.append(anime)
        if scheme in ("jellyfin", "emby", "plex"):
            parts.append("Season %02d" % season)
        elif scheme == "fnos":
            parts.append("Season %d" % season)
    parts.append(filename)
    return os.path.join(*parts)


def build_download_path(anime_name: str, episode_name: str, ext: str = "mp4",
                        base_dir: str = None, scheme: str = None, season: int = None) -> str:
    base = base_dir or config.download_dir()
    return os.path.join(base, build_relative_path(anime_name, episode_name, ext, scheme, season))


def ensure_parent_dir(path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return parent


def guess_ext_from_url(url: str, default: str = "mp4") -> str:
    url = (url or "").split("?")[0].lower()
    for ext in (".mp4", ".mkv", ".avi", ".flv", ".ts", ".m4v", ".webm", ".mov", ".rmvb"):
        if url.endswith(ext):
            return ext.lstrip(".")
    return default


def resolve_existing_path(anime_name: str, episode_name: str, base_dir: str = None) -> str:
    """检查该集是否已下载（跨命名方案/后缀），返回已存在的文件路径，找不到返回空串。"""
    base = base_dir or config.download_dir()
    season = int(config.get("default_season", 1) or 1)
    schemes = [config.get("naming_scheme")] + [s for s in SCHEMES if s != config.get("naming_scheme")]
    for scheme in schemes:
        for ext in ("mp4", "mkv", "ts", "avi", "flv", "webm", "m4v"):
            candidate = build_download_path(anime_name, episode_name, ext, base, scheme, season)
            if os.path.exists(candidate) and os.path.getsize(candidate) > 1024:
                return candidate
    return ""
