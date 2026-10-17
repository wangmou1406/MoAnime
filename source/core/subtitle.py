# -*- coding: utf-8 -*-
"""字幕搜索与下载（多源）。

动漫资源站的接口本身不提供字幕，m3u8 里也没有字幕轨道，
所以外挂字幕单独走这里的流程：

    搜索字幕条目 -> 列出包内文件 -> 下载并解压 -> 按集号匹配重命名到视频同目录

命名规则与视频一致（`第01集.srt` / `第01集.zh.ass` 等），播放器会自动加载同名外挂字幕。

当前支持的字幕源：

============  ================================================================
``assrt``     assrt.net（射手网伪）。中文动漫字幕最全，支持整季 rar 包。
``opensub``   opensubtitles.org 老 REST 接口。中文/多语言，返回 gzip 压缩的 srt。
============  ================================================================

新增一个源只需：实现 `search` + `list_files`（或让条目自带 download_url），
然后在 `SOURCES` 里登记。
"""
import contextlib
import gzip
import html as html_module
import os
import re
import shutil
import zipfile
from io import BytesIO
from typing import List, Optional
from urllib.parse import quote, unquote, urljoin

from core.config import config
from core.logging_setup import get_logger
from core.utils import build_session, clean_text

logger = get_logger("moanime.subtitle")

ASSRT = "assrt"
OPENSUB = "opensub"

#: 所有可用字幕源及显示名
SOURCES = {
    ASSRT: "assrt.net（射手网伪）· 中文动漫字幕最全",
    OPENSUB: "opensubtitles.org · 多语言（含简繁中文）",
}
DEFAULT_SOURCE = ASSRT

_ASSRT_SITE = "https://assrt.net"
_OPENSUB_API = "https://rest.opensubtitles.org"
_OPENSUB_LANGS = {
    "chi": "简体中文", "zht": "繁体中文", "chs": "简体中文",
    "eng": "英文", "jpn": "日文",
}
_SUBTITLE_EXT = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


class SubtitleError(Exception):
    """字幕搜索/下载失败。"""


def _session(referer: str = "") -> "object":
    session = build_session()
    session.verify = False
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    session.headers.update(headers)
    return session


# ==================================================================== 源：assrt

def _assrt_search(keyword: str, limit: int, lang: str = "") -> List[dict]:
    session = _session(_ASSRT_SITE + "/")
    url = "%s/sub/?searchword=%s" % (_ASSRT_SITE, quote(keyword))
    try:
        resp = session.get(url, timeout=int(config.get("source_timeout", 20) or 20))
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise SubtitleError("assrt 访问失败：%s" % exc) from exc

    text = resp.text or ""
    results, seen = [], set()
    # 条目形如：<a href="/xml/sub/717/717455.xml">标题</a>
    for match in re.finditer(r'<a[^>]+href="(/xml/sub/\d+/\d+\.xml)"[^>]*>(.*?)</a>',
                             text, re.S | re.I):
        detail = match.group(1)
        title = clean_text(re.sub(r"<[^>]+>", "", match.group(2)))
        if not title or detail in seen:
            continue
        seen.add(detail)
        sid = re.search(r"/(\d+)\.xml$", detail)
        results.append({
            "source": ASSRT,
            "id": sid.group(1) if sid else detail,
            "title": title,
            "url": urljoin(_ASSRT_SITE, detail),
            "lang": "",
            "lang_label": "",
        })
        if len(results) >= limit:
            break
    return results


def _assrt_files(entry: dict) -> List[dict]:
    session = _session(_ASSRT_SITE + "/")
    try:
        resp = session.get(entry["url"], timeout=int(config.get("source_timeout", 20) or 20))
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise SubtitleError("打开 assrt 详情失败：%s" % exc) from exc

    text = resp.text or ""
    files, seen = [], set()
    for match in re.finditer(r'href="(/download/[^"]+)"', text, re.I):
        path = html_module.unescape(match.group(1))
        if path in seen:
            continue
        seen.add(path)
        name = unquote(path.rsplit("/", 1)[-1])
        files.append({"name": name, "url": urljoin(_ASSRT_SITE, path),
                      "ext": os.path.splitext(name)[1].lower()})
    files.sort(key=lambda f: (f["ext"] not in _SUBTITLE_EXT,
                              "pack" in f["name"].lower()))
    return files


# ================================================================ 源：opensubtitles

def _opensub_search(keyword: str, limit: int, lang: str = "chi,zht") -> List[dict]:
    """查询 opensubtitles 老 REST 接口（该接口每次只接受一种语言，需逐个查）。

    每条结果直接带 SubDownloadLink（gzip 压缩的 srt），
    所以「条目」与「文件」是一一对应的。
    """
    session = _session()
    query = quote(keyword.strip())
    langs = [x.strip() for x in (lang or "chi,zht").split(",") if x.strip()] or ["chi", "zht"]
    results, seen = [], set()
    errors = []
    for code in langs[:4]:
        url = "%s/search/query-%s/sublanguageid-%s" % (_OPENSUB_API, query, code)
        try:
            resp = session.get(url, timeout=int(config.get("source_timeout", 20) or 20))
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (code, str(exc)[:60]))
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            link = item.get("SubDownloadLink") or ""
            if not link or link in seen:
                continue
            seen.add(link)
            name = item.get("SubFileName") or ""
            results.append({
                "source": OPENSUB,
                "id": str(item.get("IDSubtitleFile") or item.get("IDSubtitle") or ""),
                "title": "%s [%s]" % (item.get("MovieReleaseName") or name,
                                      _OPENSUB_LANGS.get(code, code)),
                "url": link,
                "lang": code,
                "lang_label": _OPENSUB_LANGS.get(code, code),
                # 这类源条目本身就是文件，不再需要二次解析
                "download_url": link,
                "file_name": name,
            })
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    if not results and errors:
        raise SubtitleError("opensubtitles 查询失败：%s" % "；".join(errors[:2]))
    return results


# ==================================================================== 对外接口

def list_sources() -> List[dict]:
    return [{"id": key, "label": label} for key, label in SOURCES.items()]


def search(keyword: str, limit: int = 20, source: str = "", lang: str = "") -> List[dict]:
    """按源搜索字幕条目。source 为空时用默认源（assrt）。"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise SubtitleError("请输入字幕关键字")
    source = (source or DEFAULT_SOURCE).strip()
    if source == "all":
        out = []
        for key in SOURCES:
            try:
                out.extend(search(keyword, limit, key, lang))
            except SubtitleError as exc:
                logger.warning("字幕源 %s 搜索失败：%s", key, exc)
        return out
    if source not in SOURCES:
        raise SubtitleError("未知的字幕源：%s" % source)
    if source == OPENSUB:
        return _opensub_search(keyword, limit, lang)
    return _assrt_search(keyword, limit, lang)


def list_files(entry: dict) -> List[dict]:
    """列出条目内的字幕文件；条目自带下载地址时直接返回它。"""
    if entry.get("download_url"):
        name = entry.get("file_name") or "subtitle.srt"
        return [{"name": name, "url": entry["download_url"],
                 "ext": os.path.splitext(name)[1].lower() or ".srt"}]
    source = entry.get("source") or DEFAULT_SOURCE
    if source == ASSRT:
        return _assrt_files(entry)
    raise SubtitleError("该字幕源不支持列出文件：%s" % source)


def content_length(url: str) -> Optional[int]:
    """用 HEAD/Range 试探文件大小，避免把超大合集整个下下来。"""
    session = _session()
    try:
        resp = session.head(url, timeout=15, allow_redirects=True)
        length = resp.headers.get("Content-Length")
        if length:
            return int(length)
    except Exception:  # noqa: BLE001
        pass
    try:
        resp = session.get(url, timeout=15, headers={"Range": "bytes=0-0"}, stream=True)
        content_range = resp.headers.get("Content-Range") or ""
        match = re.search(r"/(\d+)$", content_range)
        if match:
            return int(match.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def fetch(url: str, timeout: int = 60) -> bytes:
    """下载字幕内容（自动解开 gzip）。"""
    session = _session()
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise SubtitleError("字幕下载失败：%s" % exc) from exc
    data = resp.content
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
            logger.info("已解开 gzip 字幕：%d 字节", len(data))
        except Exception as exc:  # noqa: BLE001
            logger.warning("gzip 解压失败：%s", exc)
    return data


# ==================================================================== 落盘

def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore")


def _write_text(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return path


def _looks_like_archive(data: bytes) -> bool:
    head = data[:8]
    return head.startswith(b"Rar!") or head.startswith(b"7z\xbc\xaf") or head.startswith(b"MSCF")


def _find_7z() -> Optional[str]:
    """找 7z 可执行文件：项目 tools 目录优先，其次系统安装路径。"""
    import shutil as _shutil
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools", "7z.exe"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    for name in ("7z", "7za", "7zr"):
        found = _shutil.which(name)
        if found:
            return found
    return None


@contextlib.contextmanager
def _extracted_archive(data: bytes):
    """解压 rar/7z 到临时目录，在 with 块内可用，退出时自动清理。"""
    import subprocess
    import tempfile
    archive = _find_7z()
    if not archive:
        raise SubtitleError(
            "这是 rar/7z 压缩包，需要 7-Zip 才能解压；"
            "请安装 7-Zip，或把 7z.exe 放到 tools 目录后重试")
    work = tempfile.mkdtemp(prefix="moanime-sub-")
    try:
        raw_path = os.path.join(work, "sub.rar")
        with open(raw_path, "wb") as fh:
            fh.write(data)
        out_dir = os.path.join(work, "out")
        os.makedirs(out_dir, exist_ok=True)
        try:
            proc = subprocess.run([archive, "x", "-y", "-bso0", "-bsp0", "-o" + out_dir, raw_path],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
        except Exception as exc:  # noqa: BLE001
            raise SubtitleError("解压失败：%s" % exc) from exc
        if proc.returncode != 0:
            raise SubtitleError("解压失败：%s" % proc.stderr.decode("utf-8", "ignore")[-160:])
        found = []
        for root, _dirs, files in os.walk(out_dir):
            for name in files:
                if os.path.splitext(name)[1].lower() in _SUBTITLE_EXT:
                    full = os.path.join(root, name)
                    found.append((name, full, os.path.getsize(full)))
        yield found
    finally:
        shutil.rmtree(work, ignore_errors=True)


def list_archive_members(data: bytes) -> List[str]:
    """列出压缩包里的字幕文件名（zip 用标准库，rar/7z 用 7-Zip）。"""
    if zipfile.is_zipfile(BytesIO(data)):
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return [n for n in archive.namelist()
                    if os.path.splitext(n)[1].lower() in _SUBTITLE_EXT]
    if _looks_like_archive(data):
        with _extracted_archive(data) as found:
            return [name for name, _full, _size in found]
    return []


def _language_suffix(name: str) -> str:
    """从字幕文件名里判断语言标签，用于区分简繁（避免互相覆盖）。"""
    text = name.upper()
    if re.search(r"CHS|GB|简|(?<![A-Z])SC(?![A-Z])", text) and \
       re.search(r"CHT|BIG5|繁|(?<![A-Z])TC(?![A-Z])", text):
        return ""
    if re.search(r"CHT|BIG5|繁|(?<![A-Z])TC(?![A-Z])", text):
        return ".zh-Hant"
    if re.search(r"CHS|GB|简|(?<![A-Z])SC(?![A-Z])", text):
        return ".zh"
    if re.search(r"ENG|(?<![A-Z])EN(?![A-Z])|英文", text):
        return ".en"
    if re.search(r"(?<![A-Z])JP(?![A-Z])|JPN|日文", text):
        return ".ja"
    return ""


def extract_season(data: bytes, anime_name: str, lang_suffix: str = "") -> List[dict]:
    """把整季字幕包按集号落盘到视频同目录，返回 [{episode, path, from}]。"""
    if zipfile.is_zipfile(BytesIO(data)):
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = [(n, archive.read(n)) for n in archive.namelist()
                       if os.path.splitext(n)[1].lower() in _SUBTITLE_EXT]
    elif _looks_like_archive(data):
        with _extracted_archive(data) as found:
            members = [(name, open(full, "rb").read()) for name, full, _size in found]
    else:
        members = [("", data)]

    saved, used = [], {}
    for name, blob in members:
        base_name = os.path.basename(name)
        if base_name:
            number = episode_number_of(_clean_release_name(base_name))
        else:
            number = None
        if number is None:
            continue
        episode_name = "第%02d集" % number
        suffix = _language_suffix(base_name) or lang_suffix
        key = (episode_name, suffix)
        if key in used:
            continue
        used[key] = True
        ext = os.path.splitext(base_name)[1].lower() or ".srt"
        target = resolve_target_path(anime_name, episode_name)
        path = _write_text("%s%s%s" % (target, suffix, ext), _decode(blob))
        saved.append({"episode": episode_name, "path": path, "from": base_name or episode_name})
    saved.sort(key=lambda item: (episode_number_of(item["episode"]) or 0, item["path"]))
    return saved


def save_as(data: bytes, target_without_ext: str, suffix: str = "",
            episode_name: str = "", lang_label: str = "") -> str:
    """把字节内容存成字幕文件（含压缩包解包）。"""
    if zipfile.is_zipfile(BytesIO(data)):
        return _save_from_zip(data, target_without_ext, suffix, episode_name, lang_label)
    if _looks_like_archive(data):
        return _save_from_archive(data, target_without_ext, suffix, episode_name, lang_label)
    text = _decode(data)
    ext = ".ass" if "[Script Info]" in text[:400] else ".srt"
    if not suffix and lang_label:
        suffix = lang_label
    return _write_text("%s%s%s" % (target_without_ext, suffix, ext), text)


def _save_from_archive(data: bytes, target_without_ext: str, suffix: str,
                       episode_name: str, lang_label: str) -> str:
    with _extracted_archive(data) as found:
        if not found:
            raise SubtitleError("压缩包里没有字幕文件")
        want = episode_number_of(episode_name) if episode_name else None
        picked = None
        if want is not None:
            for name, full, _size in found:
                if episode_number_of(_clean_release_name(name)) == want:
                    picked = (name, full)
                    break
        if not picked:
            found.sort(key=lambda item: item[2], reverse=True)
            picked = (found[0][0], found[0][1])
        content = _decode(open(picked[1], "rb").read())
    ext = os.path.splitext(picked[0])[1].lower() or ".srt"
    logger.info("已从压缩包解出字幕：%s", picked[0])
    return _write_text("%s%s%s" % (target_without_ext, suffix or lang_label, ext), content)


def _save_from_zip(data: bytes, target_without_ext: str, suffix: str,
                   episode_name: str, lang_label: str = "") -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        members = [n for n in archive.namelist()
                   if os.path.splitext(n)[1].lower() in _SUBTITLE_EXT]
        if not members:
            raise SubtitleError("压缩包里没有字幕文件")
        want = episode_number_of(episode_name) if episode_name else None
        chosen = None
        if want is not None:
            for name in members:
                if episode_number_of(_clean_release_name(os.path.basename(name))) == want:
                    chosen = name
                    break
        if not chosen:
            members.sort(key=lambda n: archive.getinfo(n).file_size, reverse=True)
            chosen = members[0]
        content = _decode(archive.read(chosen))
    ext = os.path.splitext(chosen)[1].lower() or ".srt"
    saved = _write_text("%s%s%s" % (target_without_ext, suffix or lang_label, ext), content)
    logger.info("已从压缩包解出字幕：%s", os.path.basename(chosen))
    return saved


# ==================================================================== 集号匹配

def _clean_release_name(name: str) -> str:
    """去掉发布组命名里的编码/分辨率标记，便于提出集号。"""
    text = str(name or "")
    text = re.sub(r"\[[^\]]*(?:Ma10p|1080p|720p|2160p|x264|x265|HEVC|AAC|FLAC|BDRip|WEB)[^\]]*\]",
                  " ", text, flags=re.I)
    return text


def episode_number_of(name: str) -> Optional[int]:
    """从剧集名或字幕文件名里取出集号（第01集 / EP01 / [01] / 01）。

    OP/ED/PV/CM/SP 之类的标记不算集号。
    """
    text = str(name or "")
    if re.search(r"(?:^|[^A-Za-z])(?:OP|ED|NCOP|NCED|PV|CM|SP|MV)(?:\d*)(?:[^A-Za-z]|$)", text):
        cleaned = re.sub(
            r"(?:^|[^A-Za-z])(?:NCOP|NCED|OP|ED|PV|CM|SP|MV)\d*(?=[^A-Za-z]|$)", " ", text)
        if not re.search(r"第\s*\d+\s*[集话話回]", cleaned):
            if not re.search(r"[Ee][Pp]\s*\d+", cleaned) and not re.search(r"\[\d{1,4}\]", cleaned):
                return None
            text = cleaned
    patterns = (r"第\s*(\d+)\s*[集话話回]",
                r"[Ee][Pp]\s*0*(\d{1,4})(?!\d)",
                r"\[(\d{1,4})\]",
                r"(?:^|[^\d])(\d{1,4})(?:[^\d]|$)")
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def resolve_target_path(anime_name: str, episode_name: str) -> str:
    """字幕要保存到的位置（与视频同目录、同主名）。"""
    from core.naming import build_download_path
    video = build_download_path(anime_name, episode_name, "mp4")
    return os.path.splitext(video)[0]


def match_episode(files: List[dict], episode_name: str) -> Optional[dict]:
    """在字幕包文件里挑出与某集匹配的那个。"""
    want = episode_number_of(episode_name)
    if want is None:
        return files[0] if files else None
    for item in files:
        if episode_number_of(item.get("name") or "") == want:
            return item
    return None
