# -*- coding: utf-8 -*-
"""番剧去重：同一部番的同一集有多份时，只留一份。

重复从哪来
    自动整理遇到「目标已存在」时会加 ` (2)` 而不是覆盖 —— 这保住了数据，
    但重下/换源之后就会堆出多份。手动复制也会。

判定
    同一部番（顶层目录）+ 同一个集号 = 重复。跟文件名细节无关，所以
    `第29集.mp4` 和 `第29集 (2).mp4`、`第29集.mkv` 都会被认成一组。

留哪一份
    体积最大的那份。残片（没下完）通常只有正常文件的一小截，这条规则能自动
    挑出完整的。体积相同时优先没有 ` (2)` 后缀、名字更短的那个。

怎么处理
    默认**只移动不删除**：挪到 `_重复/` 目录下（保留原文件名，平铺时会加番剧目录），
    随时能拿回来。要直接删除得显式传 delete。
"""
import os
import shutil

from .config import config
from .utils import episode_number

DUPLICATE_DIR = "_重复"

VIDEO_EXTS = {".mp4", ".mkv", ".ts", ".avi", ".flv", ".webm", ".m4v", ".rmvb", ".mov"}
_SKIP_DIRS = {".tmp", "_pwt", "webview2", "node_modules", "data", "logs", DUPLICATE_DIR}


def _is_skipped(name: str) -> bool:
    return name.startswith(".") or name in _SKIP_DIRS


def _collect(base: str) -> list:
    """遍历下载目录，收集 (番名, 集号, 路径, 大小, 相对路径)。"""
    items = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not _is_skipped(d))
        rel = os.path.relpath(root, base)
        parts = [] if rel == "." else rel.split(os.sep)
        for name in sorted(files):
            if _is_skipped(name):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            stem = os.path.splitext(name)[0]
            number = episode_number(stem, 0)
            if number <= 0:
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size <= 0:
                continue
            # 顶层目录当番名；散在根目录的文件用空串，按文件名里的番名归组更麻烦，
            # 这里就把它们归到「(根目录)」一组，同集号仍能比出来。
            anime = parts[0] if parts else ""
            items.append({
                "anime": anime,
                "episode": number,
                "path": full,
                "size": size,
                "rel": os.path.relpath(full, base),
                "name": name,
            })
    return items


def _keeper_key(item: dict) -> tuple:
    """排序用，越小越该保留。

    1. 体积大的优先 —— 残片通常只有正常文件的一小截。
    2. 体积相同时，层级更深的优先 —— 已经归档进季目录的那份留着，
       根目录里新落下的那份挪走，省得又得整理一遍。
    3. 再没有 (2) 这类后缀的、名字短的优先。
    """
    depth = item["rel"].count(os.sep)
    has_suffix = 1 if "(" in item["name"] else 0
    return (-item["size"], -depth, has_suffix, len(item["name"]), item["name"])


def scan(download_dir: str = None) -> dict:
    """找出所有重复组（只看不动文件）。"""
    base = os.path.abspath(download_dir or config.download_dir())
    if not os.path.isdir(base):
        return {"download_dir": base, "groups": [], "stats": {
            "groups": 0, "duplicates": 0, "bytes": 0, "files": 0}}

    grouped = {}
    for item in _collect(base):
        key = (item["anime"], int(item["episode"]))
        grouped.setdefault(key, []).append(item)

    groups = []
    for (anime, episode), items in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if len(items) < 2:
            continue
        items.sort(key=_keeper_key)
        keeper = items[0]
        extras = items[1:]
        groups.append({
            "anime": anime or "(根目录)",
            "episode": episode,
            "keep": {
                "rel": keeper["rel"], "name": keeper["name"], "size": keeper["size"],
            },
            "extras": [
                {"rel": item["rel"], "name": item["name"], "size": item["size"]}
                for item in extras
            ],
            "reclaim": sum(item["size"] for item in extras),
        })

    return {
        "download_dir": base,
        "groups": groups,
        "stats": {
            "groups": len(groups),
            "duplicates": sum(len(g["extras"]) for g in groups),
            "bytes": sum(g["reclaim"] for g in groups),
            "files": sum(1 for _ in _collect(base)),
        },
    }


def apply(action: str = "move", download_dir: str = None, dry_run: bool = False) -> dict:
    """执行去重。

    action = "move"（默认，挪到 _重复/）或 "delete"（真删，调用方必须显式要求）。
    """
    plan = scan(download_dir)
    base = plan["download_dir"]
    action = "delete" if str(action).lower() == "delete" else "move"

    moved, failed = [], []
    bytes_done = 0

    for group in plan["groups"]:
        for extra in group["extras"]:
            src = os.path.join(base, extra["rel"])
            if not os.path.exists(src):
                continue
            if dry_run:
                moved.append({"rel": extra["rel"], "size": extra["size"], "action": action})
                bytes_done += extra["size"]
                continue
            try:
                if action == "delete":
                    os.remove(src)
                else:
                    target_dir = os.path.join(base, DUPLICATE_DIR)
                    if group["anime"] and group["anime"] != "(根目录)":
                        target_dir = os.path.join(target_dir, group["anime"])
                    os.makedirs(target_dir, exist_ok=True)
                    target = os.path.join(target_dir, extra["name"])
                    if os.path.exists(target):
                        stem, ext = os.path.splitext(extra["name"])
                        index = 2
                        while os.path.exists(target):
                            target = os.path.join(target_dir, "%s (%d)%s" % (stem, index, ext))
                            index += 1
                    shutil.move(src, target)
                moved.append({"rel": extra["rel"], "size": extra["size"], "action": action})
                bytes_done += extra["size"]
            except Exception as exc:  # noqa: BLE001
                failed.append({"rel": extra["rel"], "error": str(exc)[:160]})

    if not dry_run:
        _prune_empty(base)

    return {
        "action": action,
        "moved": moved,
        "failed": failed,
        "stats": {
            "handled": len(moved),
            "failed": len(failed),
            "bytes": bytes_done,
        },
    }


def _prune_empty(base: str) -> int:
    """删掉因去重变空的目录（只删空目录）。"""
    removed = 0
    for root, _dirs, _files in os.walk(base, topdown=False):
        if os.path.abspath(root) == os.path.abspath(base):
            continue
        if os.path.basename(root) == DUPLICATE_DIR:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
                removed += 1
        except OSError:
            continue
    return removed
