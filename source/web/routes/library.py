# -*- coding: utf-8 -*-
"""番剧库与播放：库扫描、字幕搜索下载、在线播放流、缩略图、删除

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import os
import re
import subprocess
from flask import Response, request, send_file
from core.config import DATA_DIR, config
from core.database import db
from core.download_engine import find_ffmpeg
from sources.manager import source_manager
from core.subtitle import SubtitleError
from core import organizer
from core import subtitle

# 跨模块引用
from ._base import _SUB_EXT, api, body, fail, logger, ok
from .search import episodes

_SUBTITLE_EXTS = (".ass", ".srt", ".ssa", ".vtt", ".sub", ".smi")


def _find_subtitles(video_path: str) -> list:
    """找出与视频同目录、同主名的外挂字幕（含 第01集.zh.ass 这类语言后缀）。"""
    if not video_path:
        return []
    folder = os.path.dirname(video_path)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    if not os.path.isdir(folder):
        return []
    out = []
    try:
        for name in sorted(os.listdir(folder)):
            base, ext = os.path.splitext(name)
            if ext.lower() not in _SUBTITLE_EXTS:
                continue
            # 主名一致，或 主名.语言（如 第01集.zh）
            if base == stem or base.startswith(stem + "."):
                full = os.path.join(folder, name)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                out.append({"name": name, "path": full, "size": size})
    except OSError:
        return []
    return out


@api.get("/library")
def library():
    base = config.download_dir()
    history = db.list_history()
    known = {}
    for item in history:
        key = item.get("anime_name") or "未分类"
        entry = known.setdefault(key, {"anime_name": key, "episodes": [], "size": 0,
                                       "source": item.get("source")})
        episode = dict(item)
        episode["exists"] = os.path.exists(item.get("path") or "")
        episode["size"] = episode.get("size") or (os.path.getsize(item["path"])
                                                  if episode["exists"] else 0)
        episode["subtitles"] = _find_subtitles(item.get("path") or "")
        entry["episodes"].append(episode)
        entry["size"] += episode["size"] or 0

    # 扫描下载目录里存在但历史库没有的文件夹
    scanned = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if not os.path.isdir(path) or name.startswith("."):
                continue
            files = []
            for root, _dirs, filenames in os.walk(path):
                for filename in filenames:
                    if filename.startswith("."):
                        continue
                    if os.path.splitext(filename)[1].lower() in (
                            ".mp4", ".mkv", ".ts", ".avi", ".flv", ".webm", ".m4v", ".rmvb"):
                        full = os.path.join(root, filename)
                        try:
                            files.append({"name": filename, "path": full,
                                          "size": os.path.getsize(full),
                                          "mtime": os.path.getmtime(full)})
                        except OSError:
                            continue
            if not files:
                continue
            scanned.append({
                "anime_name": name,
                "path": path,
                "episodes": [{"episode_name": os.path.splitext(f["name"])[0], "path": f["path"],
                              "size": f["size"], "exists": True, "added_at": f["mtime"],
                              "subtitles": _find_subtitles(f["path"])}
                             for f in sorted(files, key=lambda x: x["name"])],
                "size": sum(f["size"] for f in files),
                "scanned": True,
            })

    merged = {}
    for entry in list(known.values()) + scanned:
        key = entry["anime_name"]
        # 过滤掉「文件已不在磁盘」的历史记录，避免媒体库里出现幽灵条目
        entry["episodes"] = [e for e in entry["episodes"] if e.get("exists")]
        if not entry["episodes"]:
            continue
        entry["size"] = sum(e.get("size") or 0 for e in entry["episodes"])
        if key in merged:
            have = {e["path"] for e in merged[key]["episodes"]}
            for episode in entry["episodes"]:
                if episode["path"] not in have:
                    merged[key]["episodes"].append(episode)
            merged[key]["size"] = max(merged[key]["size"], entry["size"])
        else:
            merged[key] = entry

    items = sorted(merged.values(), key=lambda item: item["anime_name"])
    for item in items:
        item["episode_count"] = len(item["episodes"])
        item["episodes"].sort(key=lambda e: str(e.get("episode_name")))
    return ok({"download_dir": base, "items": items,
               "total_size": sum(item["size"] for item in items)})


@api.route("/library/clean-missing", methods=["GET", "POST"])
def library_clean_missing():
    """清理「文件已被删除」的下载历史记录。"""
    removed = db.clear_history_missing()
    logger.info("已清理 %d 条失效的下载历史", removed)
    return ok({"removed": removed})


@api.route("/library/organize/plan", methods=["GET", "POST"])
def library_organize_plan():
    """生成番剧整理计划（只看不动文件）。"""
    data = body()
    plan = organizer.build_plan(
        scheme=data.get("scheme"),
        season=data.get("season"),
        history_paths=organizer.history_index(),
    )
    return ok(plan)


@api.route("/library/organize/apply", methods=["POST"])
def library_organize_apply():
    """执行整理：重新算一遍计划再动文件，不接受前端传来的路径。

    重新算一遍而不是用前端回传的计划，是为了避免客户端塞进
    下载目录之外的目标路径。
    """
    data = body()
    plan = organizer.build_plan(
        scheme=data.get("scheme"),
        season=data.get("season"),
        history_paths=organizer.history_index(),
    )
    if not plan["stats"]["move"]:
        logger.info("自动整理：没有需要移动的文件")
        return ok({"moved": [], "failed": [], "skipped": [],
                   "pruned_dirs": 0, "stats": plan["stats"]})

    result = organizer.apply_plan(plan)
    stats = result["stats"]
    logger.info("自动整理完成：移动 %d 个、失败 %d 个、跳过 %d 个，清理空目录 %d 个",
                stats["moved"], stats["failed"], stats["skipped"], result["pruned_dirs"])
    return ok(result)


@api.route("/subtitle/sources", methods=["GET", "POST"])
def subtitle_sources():
    """列出可用的字幕源。"""
    return ok({"sources": subtitle.list_sources(), "default": subtitle.DEFAULT_SOURCE})


@api.route("/subtitle/search", methods=["GET", "POST"])
def subtitle_search():
    data = body()
    keyword = (data.get("keyword") or "").strip()
    source = (data.get("source") or "").strip()
    lang = (data.get("lang") or "").strip()
    limit = max(1, min(int(data.get("limit") or 20), 60))
    try:
        return ok({"results": subtitle.search(keyword, limit=limit, source=source, lang=lang),
                   "source": source or subtitle.DEFAULT_SOURCE})
    except SubtitleError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("字幕搜索失败：%s", exc, exc_info=True)
        return fail("字幕搜索失败：%s" % exc, 500)


@api.route("/subtitle/files", methods=["GET", "POST"])
def subtitle_files():
    data = body()
    # 兼容两种调用：直接给条目对象，或只给 url（opensubtitles 这类直接可下）
    entry = data.get("entry")
    if not isinstance(entry, dict):
        url = (data.get("url") or "").strip()
        if not url:
            return fail("缺少字幕条目地址")
        entry = {"url": url, "source": (data.get("source") or "").strip(),
                 "download_url": (data.get("download_url") or "").strip(),
                 "file_name": (data.get("file_name") or "").strip()}
    try:
        return ok({"files": subtitle.list_files(entry)})
    except SubtitleError as exc:
        return fail(str(exc))


@api.route("/subtitle/download", methods=["GET", "POST"])
def subtitle_download():
    """下载一个字幕文件；给定动漫/剧集名时按集号保存到视频同目录。"""
    data = body()
    url = (data.get("url") or "").strip()
    if not url:
        return fail("缺少字幕文件地址")
    anime_name = (data.get("anime_name") or "").strip()
    episode_name = (data.get("episode_name") or "").strip()
    suffix = (data.get("suffix") or "").strip()
    # 语言标签映射成文件名后缀（.zh / .zh-Hant / .en …），避免多语言互相覆盖
    lang_label = ""
    code = (data.get("lang") or "").lower()
    if code:
        lang_label = {"chi": ".zh", "chs": ".zh", "zht": ".zh-Hant",
                      "eng": ".en", "jpn": ".ja"}.get(code, "")
    try:
        raw = subtitle.fetch(url)
        if anime_name and episode_name:
            target = subtitle.resolve_target_path(anime_name, episode_name)
            saved = subtitle.save_as(raw, target, suffix, episode_name, lang_label)
        else:
            folder = os.path.join(config.download_dir(), "字幕")
            name = os.path.basename(url.split("?")[0]) or "subtitle.srt"
            saved = os.path.join(folder, name)
            os.makedirs(folder, exist_ok=True)
            with open(saved, "wb") as fh:
                fh.write(raw)
        logger.info("字幕已保存：%s", saved)
        return ok({"path": saved, "size": os.path.getsize(saved)})
    except SubtitleError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("字幕下载失败：%s", exc, exc_info=True)
        return fail("字幕下载失败：%s" % exc, 500)


@api.route("/subtitle/auto", methods=["GET", "POST"])
def subtitle_auto():
    """按番剧名搜索字幕并逐集匹配保存。

    episodes: [{name, number}]；不给则只按集号在字幕文件名里匹配。
    """
    data = body()
    keyword = (data.get("keyword") or data.get("anime_name") or "").strip()
    anime_name = (data.get("anime_name") or keyword).strip()
    episodes = data.get("episodes") or []
    limit = max(1, min(int(data.get("limit") or 40), 200))
    if not keyword:
        return fail("缺少字幕关键字")

    saved, failed, skipped = [], [], []
    source = (data.get("source") or "").strip()
    lang = (data.get("lang") or "").strip()
    # 默认先试中文动漫字幕最全的 assrt，没有结果再退到 opensubtitles
    sources_to_try = [source] if source and source != "all" else [subtitle.ASSRT, subtitle.OPENSUB]
    entries = []
    for key in sources_to_try:
        try:
            found = subtitle.search(keyword, limit=limit, source=key, lang=lang)
        except SubtitleError as exc:
            logger.warning("字幕源 %s 搜索失败：%s", key, exc)
            continue
        if found:
            entries.extend(found)
            logger.info("字幕源 %s 找到 %d 个条目", key, len(found))
    if not entries:
        return fail("没有搜到「%s」的字幕" % keyword)

    targets = []
    for index, episode in enumerate(episodes[:limit]):
        name = (episode.get("name") if isinstance(episode, dict) else str(episode)) or ""
        number = episode.get("number") if isinstance(episode, dict) else None
        if number is None:
            number = subtitle.episode_number_of(name)
        targets.append({"name": name, "number": number, "index": index})
    if not targets:
        return fail("没有指定要匹配的剧集")

    want = {t["number"] for t in targets if t["number"] is not None}
    known = {t["name"] for t in targets}
    done_eps = set()

    def relevant(title: str) -> bool:
        """排除明显不是正片字幕的合集（OPED、字幕组工具包、K值合集等）。"""
        bad = ("OPED", "NCOP", "NCED", "K值", "AEGISUB", "字体", "工具", "模板", "合集",
               "OP&ED", "OP/ED", "OP.ED")
        upper = (title or "").upper()
        if any(word in upper for word in bad):
            return False
        # 标题里只提 OP/ED 的一般是片头片尾字幕合集
        if re.search(r"(?:^|[^A-Z])(?:OP|ED)(?:\d*)(?:[^A-Z]|$)", upper) and "第" not in title:
            return False
        return True

    # 先收集候选（含覆盖率），再按匹配度从高到低处理，避免被无关合集抢先
    candidates = []
    for entry in entries:
        if not relevant(entry.get("title")):
            logger.info("跳过关联合集：%s", entry.get("title")[:40])
            continue
        try:
            files = subtitle.list_files(entry)
        except Exception:  # noqa: BLE001
            continue
        for item in files:
            candidates.append({"entry": entry, "item": item, "cover": 0})

    # 预估覆盖率：压缩包看包内集号，单文件看文件名集号
    # 超大压缩包（整站合集，动辄几十 MB）直接跳过，避免白下载
    max_archive = 12 * 1024 * 1024
    for candidate in candidates:
        item = candidate["item"]
        number = subtitle.episode_number_of(subtitle._clean_release_name(item["name"]))
        if item["ext"] in _SUB_EXT:
            candidate["cover"] = 1 if number in want else 0
            continue
        size = subtitle.content_length(item["url"])
        if size and size > max_archive:
            logger.info("跳过 %s（单个压缩包 %.1f MB，可能是整站合集）",
                        item["name"][:40], size / 1048576.0)
            candidate["skip"] = True
            continue
        try:
            raw = subtitle.fetch(item["url"])
            members = subtitle.list_archive_members(raw)
        except Exception:  # noqa: BLE001
            continue
        numbers = {subtitle.episode_number_of(subtitle._clean_release_name(m)) for m in members}
        numbers = {n for n in numbers if n is not None}
        candidate["raw"] = raw
        candidate["numbers"] = numbers
        candidate["cover"] = len(numbers & want)
    candidates = [c for c in candidates if not c.get("skip")]
    candidates.sort(key=lambda c: c["cover"], reverse=True)

    for candidate in candidates:
        if len(done_eps) >= len(targets):
            break
        if not candidate["cover"]:
            continue
        entry, item = candidate["entry"], candidate["item"]

        if item["ext"] in _SUB_EXT:
            number = subtitle.episode_number_of(subtitle._clean_release_name(item["name"]))
            target = next((t for t in targets
                           if t["number"] == number and t["name"] not in done_eps), None)
            if not target:
                continue
            try:
                raw = candidate.get("raw") or subtitle.fetch(item["url"])
                base = subtitle.resolve_target_path(anime_name, target["name"])
                path = subtitle.save_as(raw, base, episode_name=target["name"])
                saved.append({"episode": target["name"], "path": path, "from": item["name"]})
                done_eps.add(target["name"])
            except Exception as exc:  # noqa: BLE001
                failed.append({"episode": target["name"], "error": str(exc)[:80]})
            continue

        # 压缩包
        raw = candidate.get("raw") or subtitle.fetch(item["url"])
        numbers = candidate.get("numbers")
        if numbers is None:
            try:
                numbers = {subtitle.episode_number_of(subtitle._clean_release_name(m))
                           for m in subtitle.list_archive_members(raw)}
            except Exception as exc:  # noqa: BLE001
                failed.append({"episode": item["name"], "error": str(exc)[:80]})
                continue
        numbers = {n for n in numbers if n is not None}

        if len(numbers & want) > 1:
            # 整季包：一次解出所有能对上的集
            try:
                packed = subtitle.extract_season(raw, anime_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("字幕包解压失败 %s: %s", item["name"][:40], exc)
                failed.append({"episode": item["name"], "error": str(exc)[:80]})
                continue
            logger.info("字幕包 %s 解出 %d 个字幕文件", item["name"][:40], len(packed))
            for row in packed:
                if row["episode"] in done_eps or row["episode"] not in known:
                    continue
                saved.append(row)
                done_eps.add(row["episode"])
            continue

        for target in targets:
            if target["name"] in done_eps or target["number"] is None:
                continue
            if target["number"] not in numbers:
                continue
            try:
                base = subtitle.resolve_target_path(anime_name, target["name"])
                path = subtitle.save_as(raw, base, episode_name=target["name"])
                saved.append({"episode": target["name"], "path": path, "from": item["name"]})
                done_eps.add(target["name"])
            except Exception as exc:  # noqa: BLE001
                failed.append({"episode": target["name"], "error": str(exc)[:80]})

    for target in targets:
        if target["name"] not in done_eps:
            skipped.append(target["name"])
    logger.info("字幕匹配：成功 %d，未匹配 %d", len(saved), len(skipped))
    return ok({"saved": saved, "failed": failed, "skipped": skipped,
               "entries": len(entries)})


@api.route("/video/stream", methods=["GET"])
def video_stream():
    """把远程 m3u8 / 分片代理给浏览器，用于**在线预览播放**。

    为什么需要代理：视频站的分片不允许跨域（CORS），浏览器里 hls.js 直接拉会被拦；
    而且播放列表里是相对地址。这里统一：拉取 → 重写播放列表里的 URL → 回传给前端。
    """
    import mimetypes
    import urllib.parse

    from core.utils import build_session

    target = (request.args.get("url") or "").strip()
    if not target or not target.lower().startswith(("http://", "https://")):
        return fail("缺少 url 参数")

    session = build_session()
    session.verify = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    # 把浏览器带来的 Range 透传下去，分片拖动/续传才不会整段重下
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    try:
        upstream = session.get(target, headers=headers, timeout=25, stream=True)
    except Exception as exc:  # noqa: BLE001
        return fail("拉取失败：%s" % str(exc)[:120], 502)

    content_type = (upstream.headers.get("Content-Type") or "").lower()
    is_playlist = (".m3u8" in target.lower() or "mpegurl" in content_type
                   or target.lower().endswith(".m3u"))

    def proxy_url(raw: str) -> str:
        absolute = urllib.parse.urljoin(target, raw.strip())
        return "/api/video/stream?url=" + urllib.parse.quote(absolute, safe="")

    # ---------------- 播放列表：重写里面的地址 ----------------
    if is_playlist:
        text = upstream.content.decode("utf-8", "ignore")
        out_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                out_lines.append(line)
                continue
            if stripped.startswith("#"):
                # 关键帧/加密信息里也可能带 URI（例如 EXT-X-KEY 的密钥地址）
                if "URI=" in stripped:
                    def _swap(match):
                        return 'URI="%s"' % proxy_url(match.group(1))
                    stripped = re.sub(r'URI="([^"]+)"', _swap, stripped)
                out_lines.append(stripped)
                continue
            out_lines.append(proxy_url(stripped))
        body = "\n".join(out_lines).encode("utf-8")
        return Response(body, mimetype="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store",
                                 "Access-Control-Allow-Origin": "*"})

    # ---------------- 普通分片：流式回传 ----------------
    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    resp = Response(generate(), status=upstream.status_code,
                    mimetype=mimetypes.guess_type(target.split("?")[0])[0] or "video/mp2t")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Accept-Ranges"] = "bytes"
    for name in ("Content-Length", "Content-Range"):
        if upstream.headers.get(name):
            resp.headers[name] = upstream.headers[name]
    return resp


@api.route("/video/thumbnail", methods=["GET", "POST"])
def video_thumbnail():
    """抽取某集的首帧作为预览图（秒级返回，不下载整集）。

    参数：source_id / episode_url（必填）；anime_url、time（默认 3 秒）、width。
    如果传了本地文件路径 path，则直接对该文件抽帧。
    """
    from core.download_engine import find_ffmpeg

    data = body()
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return fail("没有 ffmpeg，无法生成预览图")

    # 缩略图统一放在数据目录，按 url 哈希缓存，避免重复抽帧
    import hashlib
    cache_dir = os.path.join(DATA_DIR, "thumbs")
    os.makedirs(cache_dir, exist_ok=True)

    local_path = (data.get("path") or "").strip()
    if local_path:
        if not os.path.exists(local_path):
            return fail("文件不存在：%s" % local_path, 404)
        source = local_path
        key = hashlib.md5(("file:" + local_path).encode()).hexdigest()[:16]
    else:
        source_id = data.get("source_id")
        episode_url = (data.get("episode_url") or "").strip()
        if not source_id or not episode_url:
            return fail("缺少 source_id 或 episode_url")
        try:
            video_url = source_manager.resolve_video_url(
                int(source_id), episode_url, data.get("anime_url") or "", "")
        except Exception as exc:  # noqa: BLE001
            return fail("解析视频地址失败：%s" % str(exc)[:120])
        source = video_url
        key = hashlib.md5(video_url.encode()).hexdigest()[:16]

    out = os.path.join(cache_dir, "%s.jpg" % key)
    if os.path.exists(out) and os.path.getsize(out) > 512:
        return ok({"file": out, "url": "/api/video/thumbnail/file/" + os.path.basename(out),
                   "cached": True})

    seconds = max(0, min(int(data.get("time") or 3), 600))
    width = max(120, min(int(data.get("width") or 480), 1280))
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", str(seconds), "-i", source, "-frames:v", "1",
           "-vf", "scale=%d:-2" % width, "-q:v", "4", out]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    except Exception as exc:  # noqa: BLE001
        return fail("抽帧失败：%s" % exc)
    if proc.returncode != 0 or not os.path.exists(out):
        # 首帧可能取不到（例如开头是空白），退回到第 1 秒再试一次
        cmd[cmd.index("-ss") + 1] = "1"
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        except Exception:  # noqa: BLE001
            pass
    if not os.path.exists(out) or os.path.getsize(out) < 512:
        return fail("抽帧失败：%s" % proc.stderr.decode("utf-8", "ignore")[-160:])
    return ok({"file": out, "url": "/api/video/thumbnail/file/" + os.path.basename(out),
               "cached": False})


@api.get("/video/thumbnail/file/<name>")
def video_thumbnail_file(name: str):
    """读取已生成的预览图（限制在 thumbs 目录内）。"""
    safe = os.path.basename(name or "")
    path = os.path.join(DATA_DIR, "thumbs", safe)
    if not safe.lower().endswith(".jpg") or not os.path.isfile(path):
        return fail("预览图不存在", 404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@api.post("/library/delete")
def library_delete():
    data = body()
    paths = data.get("paths") or ([data["path"]] if data.get("path") else [])
    delete_file = bool(data.get("delete_file", False))
    removed = 0
    for path in paths:
        path = os.path.abspath(path)
        if not _inside_download_dir(path):
            return fail("只能删除下载目录内的文件：%s" % path, 403)
        if delete_file and os.path.isfile(path):
            try:
                os.remove(path)
                removed += 1
            except OSError as exc:
                logger.warning("删除文件失败 %s: %s", path, exc)
    db_paths = [os.path.abspath(p) for p in paths]
    if db_paths:
        db.execute("DELETE FROM history WHERE path IN (%s)" %
                   ", ".join("?" for _ in db_paths), tuple(db_paths))
    return ok({"removed": removed})


def _inside_download_dir(path: str) -> bool:
    base = os.path.abspath(config.download_dir())
    target = os.path.abspath(path)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:
        return False


@api.get("/library/stream")
def library_stream():
    """在浏览器里预览已下载的视频（支持 Range）。"""
    path = request.args.get("path") or ""
    if not path or not os.path.isfile(path):
        return fail("文件不存在", 404)
    ext = os.path.splitext(path)[1].lower()
    mimetype = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".ts": "video/mp2t",
                ".webm": "video/webm", ".flv": "video/x-flv", ".avi": "video/x-msvideo",
                ".m4v": "video/mp4"}.get(ext, "application/octet-stream")
    return send_file(path, mimetype=mimetype, conditional=True)
