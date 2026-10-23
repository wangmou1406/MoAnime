# -*- coding: utf-8 -*-
"""番剧自动整理：把下载目录里散乱的文件按命名规则归档。

典型场景：
  * 早期用「简洁」规则下的，现在想改用 Jellyfin 的 Season 结构
  * 文件直接堆在下载根目录，没有番剧文件夹
  * 文件名带一堆 [字幕组] / [1080p] 标签，跟命名规则不符
  * 同一集存在两份（不同码率/不同来源）

处理原则：
  * **只移动/改名，绝不删除**。任何一步失败只记录错误，不中断整批。
  * 先出计划（dry run），用户确认后才真正动文件。
  * 只处理下载目录内的文件；`.tmp`（正在下载）和隐藏目录一律跳过。
  * 外挂字幕跟着视频一起改名，不会脱节。
  * 已经符合规则的文件不碰（避免无意义的 IO）。
"""
import os
import re
import shutil

from .config import config
from .database import db
from .naming import build_relative_path, normalize_scheme
from .utils import clean_text, episode_number, sanitize_filename

VIDEO_EXTS = {".mp4", ".mkv", ".ts", ".avi", ".flv", ".webm", ".m4v", ".rmvb", ".mov"}
SUB_EXTS = {".ass", ".srt", ".ssa", ".vtt", ".sub", ".smi"}

# 这些目录不参与整理：.tmp 是下载中的分片，其余是程序自己的数据/内核
_SKIP_DIRS = {".tmp", "_pwt", "webview2", "node_modules", "data", "logs"}

# 未下载完的残留后缀
_PARTIAL_SUFFIX = (".part", ".aria2", ".!qb", ".download", ".crdownload", ".tmp")

_SEASON_DIR = re.compile(r"^(?:season|s)\s*0*(\d{1,2})$|^第\s*0*(\d{1,2})\s*季$|^0*(\d{1,2})\s*季$", re.I)

# 开头 / 结尾的方括号标签：[字幕组] … [1080p][CHS]
_LEAD_TAG = re.compile(r"^\s*(?:\[[^\]]*\]|【[^】]*】)\s*")
_TAIL_TAG = re.compile(r"(?:\s*(?:\[[^\]]*\]|【[^】]*】))+\s*$")

# 同一集多份时我们自己加的序号「 (2)」；解析前要剥掉，否则每次整理都会再加一层
_DUP_SUFFIX = re.compile(r"\s*\(\d{1,2}\)\s*$")

# 从文件名里拆出「番名 + 季 + 集」
# 顺序有讲究：越具体的越先匹配，避免「- 01」把「S01E01」拆错
_STEM_PATTERNS = (
    # 番名 - S01E01 / 番名 S01E01
    (re.compile(r"^(?P<name>.+?)\s*[-–—_]\s*[Ss](?P<season>\d{1,2})[.\s_-]?[Ee][Pp]?(?P<ep>\d{1,4}(?:\.\d+)?)"), True),
    (re.compile(r"^(?P<name>.+?)\s+[Ss](?P<season>\d{1,2})[.\s_-]?[Ee][Pp]?(?P<ep>\d{1,4}(?:\.\d+)?)"), True),
    # 番名 - 第01集 / 番名 第01话
    (re.compile(r"^(?P<name>.+?)\s*[-–—_]\s*第\s*(?P<ep>\d{1,4}(?:\.\d+)?)\s*[集话話]"), False),
    (re.compile(r"^(?P<name>.+?)\s+第\s*(?P<ep>\d{1,4}(?:\.\d+)?)\s*[集话話]"), False),
    # 番名 - EP01
    (re.compile(r"^(?P<name>.+?)\s*[-–—_]\s*[Ee][Pp]\.?\s*(?P<ep>\d{1,4}(?:\.\d+)?)"), False),
    # 番名 - 01 / 番名.01 / 番名 01
    (re.compile(r"^(?P<name>.+?)\s*[-–—_.]\s*(?P<ep>\d{1,4}(?:\.\d+)?)\s*(?:[vV]\d)?$"), False),
    (re.compile(r"^(?P<name>.+?)\s+(?P<ep>\d{1,4}(?:\.\d+)?)\s*(?:[vV]\d)?$"), False),
)

# 只有集数、没有番名的文件名（第01集 / 01 / EP01）
_BARE_EPISODE = (
    re.compile(r"^第\s*(\d{1,4}(?:\.\d+)?)\s*[集话話]$"),
    re.compile(r"^[Ee][Pp]\.?\s*(\d{1,4}(?:\.\d+)?)$"),
    re.compile(r"^(\d{1,4}(?:\.\d+)?)$"),
)


def _is_skipped_dir(name: str) -> bool:
    return name.startswith(".") or name in _SKIP_DIRS


def _is_skipped_file(name: str) -> bool:
    if name.startswith("."):
        return True
    lowered = name.lower()
    return lowered.endswith(_PARTIAL_SUFFIX)


def _season_of(name: str):
    """目录名 -> 季号（Season 01 / S2 / 第二季），不像季目录返回 None。"""
    if not name:
        return None
    match = _SEASON_DIR.match(name.strip())
    if not match:
        return None
    for group in match.groups():
        if group:
            try:
                return int(group)
            except ValueError:
                return None
    return None


def _strip_tags(stem: str) -> str:
    """去掉开头结尾的 [字幕组] / [1080p] 标签，以及我们自己加的「 (2)」序号。"""
    text = clean_text(stem)
    for _ in range(6):                     # 标签可能有多层，循环剥
        before = text
        text = _LEAD_TAG.sub("", text)
        text = _TAIL_TAG.sub("", text)
        text = _DUP_SUFFIX.sub("", text)
        text = text.strip(" -–—_.")
        if text == before:
            break
    return text


def _bare_episode(text: str):
    """纯集数（没有番名）时返回集号。"""
    for pattern in _BARE_EPISODE:
        match = pattern.match(text.strip())
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def parse_stem(stem: str):
    """从文件名主干解析出 (番名, 季号, 集号)。解析不出番名时番名为空串。

    集号返回 float，找不到返回 None。
    """
    text = _strip_tags(stem)
    if not text:
        return "", None, None

    bare = _bare_episode(text)
    if bare is not None:
        return "", None, bare

    for pattern, has_season in _STEM_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        name = (match.group("name") or "").strip(" -–—_.")
        try:
            episode = float(match.group("ep"))
        except (TypeError, ValueError):
            continue
        if episode <= 0:
            continue
        season = None
        if has_season:
            try:
                season = int(match.group("season"))
            except (TypeError, ValueError):
                season = None
        # 番名本身如果就是个集数（如「01 - 02」），判定为解析失败
        if not name or _bare_episode(name) is not None:
            return "", season, episode
        return name, season, episode

    # 实在拆不出来，退回通用集号解析；番名留空由调用方按目录名兜底
    number = episode_number(text, 0)
    if number > 0 and number != float(int(number)) or number >= 1:
        return "", None, number if number > 0 else None
    return "", None, None


def _episode_name(number: float) -> str:
    """集号 -> 第01集 这样的名字（交给 naming 再解析一次）。"""
    if abs(number - round(number)) < 1e-6:
        return "第%02d集" % int(round(number))
    return "第%s集" % ("%g" % number)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _find_sidecars(video_path: str) -> list:
    """同目录下与视频同主名的外挂字幕（含 .zh / .chs 这类语言后缀）。"""
    folder = os.path.dirname(video_path)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for name in names:
        base, ext = os.path.splitext(name)
        if ext.lower() not in SUB_EXTS:
            continue
        if base == stem:
            out.append({"path": os.path.join(folder, name), "suffix": ext.lower()})
        elif base.startswith(stem + "."):
            # 第01集.zh.ass -> 后缀保留 .zh.ass
            out.append({"path": os.path.join(folder, name), "suffix": base[len(stem):] + ext.lower()})
    return out


def collect_videos(download_dir: str) -> list:
    """遍历下载目录，返回每个视频文件的原始信息（不做解析）。"""
    items = []
    if not os.path.isdir(download_dir):
        return items
    for root, dirs, files in os.walk(download_dir):
        dirs[:] = sorted(d for d in dirs if not _is_skipped_dir(d))
        rel = os.path.relpath(root, download_dir)
        parts = [] if rel == "." else rel.split(os.sep)
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTS or _is_skipped_file(name):
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size <= 0:
                continue
            items.append({"path": full, "parts": parts, "filename": name, "ext": ext, "size": size})
    return items


def _resolve(item: dict, default_season: int):
    """决定这条文件属于哪部番、第几季、第几集。"""
    parts = item["parts"]
    stem = os.path.splitext(item["filename"])[0]

    name_from_file, season_from_file, episode = parse_stem(stem)

    anime = ""
    season = None

    if parts:
        # 有目录：第一层当番名；如果第一层其实是「Season 01」，往下取一层
        anime = parts[0]
        if _season_of(anime) is not None and len(parts) > 1:
            anime = parts[1]
        for part in parts[1:]:
            found = _season_of(part)
            if found is not None:
                season = found
                break
        # 文件名里带 SxxExx 时优先用文件名里的季
        if season is None:
            season = season_from_file

    if not anime:
        anime = name_from_file

    # 番名里如果还带着「第01集」，说明拆错了，丢掉
    if anime and _bare_episode(anime) is not None:
        anime = ""

    if episode is None and parts:
        # 目录结构明确但文件名没有集数：退回通用解析
        number = episode_number(stem, 0)
        episode = number if number > 0 else None

    return anime.strip(), int(season if season is not None else default_season), episode


def _target_relative(anime: str, episode: float, ext: str, scheme: str,
                     season: int, default_season: int) -> str:
    """按命名规则算出相对路径。

    有一处特例：simple / anime 这两种方案本身不体现季号，如果文件属于
    非默认季，直接压平会让「S02E06」和「S01E06」撞名。这种情况保留一层
    Season 目录，宁可路径不够"标准"，也别把两季混成一季。
    """
    relative = build_relative_path(anime, _episode_name(episode), ext, scheme, season)
    if scheme in ("simple", "anime") and season != default_season:
        parts = relative.split(os.sep)
        if len(parts) >= 2:                       # 番名/文件
            parts.insert(1, "Season %02d" % season)
            relative = os.path.join(*parts)
    return relative


def build_plan(download_dir: str = None, scheme: str = None, season: int = None,
               history_paths: dict = None) -> dict:
    """生成整理计划。

    history_paths: {旧路径: (动漫名, 集名)}，用于给「文件名看不出番名」的
    文件补一个准确归属（下载历史里记着它本来是哪个番）。
    """
    base = os.path.abspath(download_dir or config.download_dir())
    scheme = normalize_scheme(scheme or config.get("naming_scheme"))
    default_season = int(season if season is not None else config.get("default_season", 1) or 1)
    organize = bool(config.get("organize_by_anime", True))

    by_path = {}
    for old, value in (history_paths or {}).items():
        if old:
            by_path[_path_key(old)] = value

    # ---- 第一遍：把每个文件解析成「哪部番、第几季、第几集」
    resolved = []
    for video in collect_videos(base):
        anime, resolved_season, episode = _resolve(video, default_season)
        note = ""

        # 下载历史里有记录就用记录里的番名/集名，最准
        hist = by_path.get(_path_key(video["path"]))
        if hist:
            hist_anime, hist_episode = hist
            if hist_anime:
                anime = hist_anime
            if hist_episode:
                number = episode_number(hist_episode, 0)
                if number > 0:
                    episode = number

        resolved.append({
            "video": video,
            "anime": anime.strip() if anime else "",
            "season": int(resolved_season),
            "episode": episode,
            "note": note,
            "from_history": bool(hist),
        })

    # ---- 第二遍：分配目标路径
    # 同一集有多份时，体积大的保留标准名，其余的加「 (2)」——
    # 通常体积大的是码率更高的那份，所以按体积从大到小分配。
    occupied = {}           # 规范化目标路径 -> 占用次数
    items = []

    resolved.sort(key=lambda e: (-(e["video"]["size"] or 0), e["video"]["path"]))

    for entry in resolved:
        video = entry["video"]
        anime = entry["anime"]
        episode = entry["episode"]

        if not anime:
            items.append(_skip_item(video, base, "无法识别番名", entry["from_history"]))
            continue
        if episode is None:
            items.append(_skip_item(video, base, "无法识别集数", entry["from_history"]))
            continue

        ext = video["ext"].lstrip(".")
        relative = _target_relative(anime, episode, ext, scheme,
                                    entry["season"], default_season)
        target = os.path.join(base, relative)
        reason = ""

        key = _path_key(target)
        taken = key in occupied or (os.path.exists(target) and not _same_path(target, video["path"]))
        if taken:
            index = 1
            stem_t, ext_t = os.path.splitext(target)
            while True:
                index += 1
                candidate = "%s (%d)%s" % (stem_t, index, ext_t)
                candidate_key = _path_key(candidate)
                if candidate_key not in occupied and (
                        not os.path.exists(candidate) or _same_path(candidate, video["path"])):
                    target = candidate
                    key = candidate_key
                    break
            reason = "目标已存在，加序号"

        occupied[key] = occupied.get(key, 0) + 1

        if _same_path(target, video["path"]):
            action = "skip"
            reason = "已符合命名规则"
        else:
            action = "move"
            if not reason and _path_key(target) == _path_key(video["path"]):
                reason = "仅大小写不同"

        sidecars = []
        for sub in _find_sidecars(video["path"]):
            sub_target = os.path.join(
                os.path.dirname(target),
                os.path.splitext(os.path.basename(target))[0] + sub["suffix"])
            if not _same_path(sub_target, sub["path"]):
                sidecars.append({"src": sub["path"], "dst": sub_target})

        items.append({
            "src": video["path"],
            "src_rel": os.path.relpath(video["path"], base),
            "dst": target,
            "dst_rel": os.path.relpath(target, base),
            "action": action,
            "reason": reason,
            "anime": anime,
            "season": entry["season"],
            "episode": episode,
            "size": video["size"],
            "subtitles": sidecars,
            "from_history": entry["from_history"],
        })

    items.sort(key=lambda i: i["src_rel"])

    will_move = [i for i in items if i["action"] == "move"]
    stats = {
        "total": len(items),
        "move": len(will_move),
        "skip": len(items) - len(will_move),
        "bytes": sum(i["size"] for i in will_move),
        "conflicts": sum(1 for i in will_move if "加序号" in (i["reason"] or "")),
        "unparsed": sum(1 for i in items if i["action"] == "skip" and "无法识别" in (i["reason"] or "")),
        "case_only": sum(1 for i in will_move if "大小写" in (i["reason"] or "")),
        "animes": len({i["anime"] for i in items if i.get("anime")}),
    }
    return {
        "download_dir": base,
        "scheme": scheme,
        "season": default_season,
        "organize_by_anime": organize,
        "items": items,
        "stats": stats,
    }


def _skip_item(video: dict, base: str, reason: str, from_history: bool = False) -> dict:
    return {
        "src": video["path"],
        "src_rel": os.path.relpath(video["path"], base),
        "dst": "",
        "dst_rel": "",
        "action": "skip",
        "reason": reason,
        "anime": "",
        "season": None,
        "episode": None,
        "size": video["size"],
        "subtitles": [],
        "from_history": from_history,
    }


def _move_file(src: str, dst: str) -> None:
    """移动文件；目标与源只差大小写时用中转名绕开 Windows 的限制。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if _path_key(src) == _path_key(dst):
        if src == dst:
            return
        temp = src + ".tmprename"
        os.replace(src, temp)
        os.replace(temp, dst)
        return
    shutil.move(src, dst)


def _prune_empty_dirs(base: str) -> int:
    """删掉整理后变空的目录（只删空目录，绝不碰有文件的）。"""
    removed = 0
    for root, dirs, files in os.walk(base, topdown=False):
        if os.path.abspath(root) == os.path.abspath(base):
            continue
        if files:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
                removed += 1
        except OSError:
            continue
    return removed


def apply_plan(plan: dict) -> dict:
    """执行整理计划。返回逐条结果，失败不抛异常。"""
    base = plan.get("download_dir")
    moved, failed, skipped = [], [], []
    bytes_moved = 0

    for item in plan.get("items") or []:
        if item.get("action") != "move" or not item.get("dst"):
            skipped.append({"src_rel": item.get("src_rel"), "reason": item.get("reason") or "跳过"})
            continue
        src, dst = item["src"], item["dst"]
        try:
            _move_file(src, dst)
        except Exception as exc:  # noqa: BLE001
            failed.append({"src_rel": item.get("src_rel"), "error": str(exc)[:160]})
            continue

        # 字幕跟着走；失败只记录，不影响视频本身
        sub_errors = []
        for sub in item.get("subtitles") or []:
            try:
                _move_file(sub["src"], sub["dst"])
            except Exception as exc:  # noqa: BLE001
                sub_errors.append("%s: %s" % (os.path.basename(sub["src"]), str(exc)[:80]))

        try:
            db.update_history_path(src, dst)
        except Exception:  # noqa: BLE001
            pass

        moved.append({
            "src_rel": item.get("src_rel"),
            "dst_rel": item.get("dst_rel"),
            "size": item.get("size") or 0,
            "subtitles": len(item.get("subtitles") or []),
            "subtitle_errors": sub_errors,
        })
        bytes_moved += item.get("size") or 0

    pruned = _prune_empty_dirs(base) if base else 0

    return {
        "moved": moved,
        "failed": failed,
        "skipped": skipped,
        "pruned_dirs": pruned,
        "stats": {
            "moved": len(moved),
            "failed": len(failed),
            "skipped": len(skipped),
            "bytes": bytes_moved,
        },
    }


def history_index() -> dict:
    """{(动漫名, 集名): 路径} 反转成 {路径: (动漫名, 集名)}，供 build_plan 用。"""
    out = {}
    for (anime, episode), path in (db.history_map() or {}).items():
        if path:
            out[path] = (anime, episode)
    return out
