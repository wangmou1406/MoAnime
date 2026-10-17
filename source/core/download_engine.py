# -*- coding: utf-8 -*-
"""下载引擎：支持 HLS(m3u8 分片) 与普通直链下载，含暂停 / 继续 / 取消。

设计要点
  * 每个任务一个 TaskControl（事件 + 进度），由 DownloadManager 持有并对外暴露。
  * HLS 分片多线程下载 -> 本地缓存 -> ffmpeg 合并/转封装（无 ffmpeg 时对 ts 直接拼接）。
  * 普通直链支持 Range 断点续传。
"""
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import m3u8
import requests

from .config import config
from .logging_setup import get_logger
from .naming import build_download_path, ensure_parent_dir, guess_ext_from_url
from .utils import build_session, human_size, sanitize_filename

logger = get_logger("moanime.download")

_FFMPEG = {"checked": False, "path": None}


# ------------------------------------------------------------------ ffmpeg
def find_ffmpeg() -> str:
    """查找 ffmpeg：内置 tools/ -> 项目 bin/ -> 系统 PATH。

    按平台选可执行文件名：Windows 用 ffmpeg.exe，Linux/macOS 用 ffmpeg。
    Docker 镜像里装的是系统 ffmpeg（走 PATH），不会被 tools/ffmpeg.exe 干扰。
    """
    if _FFMPEG["checked"]:
        return _FFMPEG["path"]
    _FFMPEG["checked"] = True
    from .config import ROOT_DIR

    is_windows = os.name == "nt"
    exe = "ffmpeg.exe" if is_windows else "ffmpeg"

    candidates = [
        os.path.join(ROOT_DIR, "tools", exe),
        os.path.join(ROOT_DIR, "tools", "ffmpeg", "bin", exe),
        os.path.join(ROOT_DIR, "bin", exe),
    ]
    # 非 Windows 上额外找一个不带后缀的版本，方便手工放到 tools/
    if not is_windows:
        candidates.append(os.path.join(ROOT_DIR, "tools", "ffmpeg"))
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            _FFMPEG["path"] = path
            return path

    found = shutil.which("ffmpeg")
    if not found and is_windows:
        for extra in (r"C:\ffmpeg\bin\ffmpeg.exe",
                      os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")):
            if os.path.exists(extra):
                found = extra
                break
    _FFMPEG["path"] = found
    return found


def ffmpeg_available() -> bool:
    return bool(find_ffmpeg())


# ------------------------------------------------------------------ 控制
class TaskControl:
    """下载任务的控制与进度容器（线程安全）。"""

    def __init__(self, task_id: str, meta: dict = None):
        self.id = task_id
        self.meta = dict(meta or {})
        self.pause_event = threading.Event()
        self.pause_event.set()          # set = 运行中
        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        self.status = "queued"
        self.progress = 0.0
        self.total_bytes = 0
        self.done_bytes = 0
        self.segments_total = 0
        self.segments_done = 0
        self.speed = 0.0
        self.message = "等待开始"
        self.error = ""
        self.save_path = ""
        self.started_at = ""
        self.finished_at = ""
        self.retries = 0
        self.phase = "idle"          # idle / direct / segments / merging
        self._last_bytes = 0
        self._last_time = time.time()
        self._listeners = []

    # -------------------------------------------------------- 监听
    def add_listener(self, callback):
        self._listeners.append(callback)

    def _notify(self):
        for callback in list(self._listeners):
            try:
                callback(self)
            except Exception:
                pass

    # -------------------------------------------------------- 状态
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "progress": round(self.progress, 2),
                "total_bytes": self.total_bytes,
                "done_bytes": self.done_bytes,
                "segments_total": self.segments_total,
                "segments_done": self.segments_done,
                "speed": self.speed,
                "message": self.message,
                "error": self.error,
                "save_path": self.save_path,
                "retries": self.retries,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }

    def set(self, **fields):
        with self._lock:
            for key, value in fields.items():
                setattr(self, key, value)
        self._notify()

    def update_bytes(self, delta: int):
        if delta <= 0:
            return
        with self._lock:
            self.done_bytes += delta
            now = time.time()
            elapsed = now - self._last_time
            if elapsed >= 0.5:
                self.speed = (self.done_bytes - self._last_bytes) / elapsed
                self._last_bytes = self.done_bytes
                self._last_time = now
                # 只在直链下载阶段用字节估算进度。
                # HLS 分片阶段由 update_segments 按分片数算，合并阶段固定 92%，
                # 若这里也改 progress，字节估算一旦偏高就会把进度顶到 99% 再被打回 92%，
                # 看起来就是"进度条时不时跳到 100%"。
                if self.phase == "direct" and self.total_bytes > 0:
                    self.progress = min(99.5, self.done_bytes * 100.0 / self.total_bytes)
        self._notify()

    def update_segments(self, done: int, total: int):
        with self._lock:
            self.segments_done = done
            self.segments_total = total
            if total and self.phase != "direct":
                # 分片阶段只占 90%，合并阶段留 10%
                self.progress = min(90.0, done * 90.0 / total)
        self._notify()

    # -------------------------------------------------------- 控制
    def pause(self):
        self.pause_event.clear()
        self.set(status="paused", message="已暂停")

    def resume(self):
        self.pause_event.set()
        if self.status == "paused":
            self.set(status="running", message="继续下载")

    def is_paused(self) -> bool:
        return not self.pause_event.is_set()

    def cancel(self):
        self.stop_event.set()
        self.pause_event.set()
        self.set(status="canceled", message="已取消")

    def wait_if_paused(self) -> bool:
        """阻塞直到恢复；返回 False 表示需要中止。"""
        while not self.pause_event.wait(timeout=0.5):
            if self.stop_event.is_set():
                return False
        return not self.stop_event.is_set()


# ------------------------------------------------------------------ 下载器
class DownloadEngine:
    def __init__(self, session: requests.Session = None):
        self.session = session
        self._local = threading.local()

    # -------------------------------------------------------- 会话
    @property
    def http(self) -> requests.Session:
        """每个线程独立会话，避免连接池争用。"""
        if self.session is not None:
            return self.session
        session = getattr(self._local, "session", None)
        if session is None:
            session = build_session(retries=int(config.get("max_retries", 3)))
            self._local.session = session
        return session

    def _timeout(self):
        return (int(config.get("request_timeout", 15)), 60)

    def _headers(self, referer: str = "", extra: dict = None, override: dict = None) -> dict:
        headers = {"User-Agent": config.get("user_agent")}
        if referer:
            headers["Referer"] = referer
        if extra:
            headers.update({k: v for k, v in extra.items() if v})
        if override:
            headers.update({k: v for k, v in override.items() if v})
        return headers

    # -------------------------------------------------------- 入口
    def download(self, control: TaskControl, url: str, anime_name: str, episode_name: str,
                 referer: str = "", headers: dict = None, ext: str = None) -> str:
        """执行下载，返回最终文件路径；失败抛异常。"""
        if not url:
            raise ValueError("下载地址为空")

        control.set(status="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    message="解析下载地址", phase=("segments" if self._is_hls(url) else "direct"))
        ext = ext or ("mp4" if self._is_hls(url) else guess_ext_from_url(url))
        target = build_download_path(anime_name, episode_name, ext)
        # 注意：这里**不**提前建目录。否则任务失败时会在下载目录里留下空文件夹
        # （用户会看到"没下载过的番剧却有空目录"）。目录在真正要写文件前才创建。
        control.set(save_path=target)
        temp_root = os.path.join(config.temp_dir(), sanitize_filename(
            "%s_%s" % (anime_name, episode_name), 80))

        try:
            if self._is_hls(url):
                result = self._download_hls(control, url, target, temp_root, referer, headers, ext)
            else:
                result = self._download_direct(control, url, target, referer, headers)
            if result:
                # 合并阶段可能改变输出后缀（例如无 ffmpeg 时 ts 直接落地）
                control.set(save_path=result)
                control.set(status="completed", progress=100.0, message="下载完成",
                            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"), speed=0)
                try:
                    size = os.path.getsize(result)
                    control.total_bytes = size
                    control.done_bytes = size
                except OSError:
                    pass
            return result
        except _Canceled:
            control.set(status="canceled", message="已取消",
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.error("下载失败 %s %s: %s", anime_name, episode_name, exc)
            control.set(status="failed", error=str(exc), message="下载失败: %s" % exc,
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            raise
        finally:
            if not config.get("keep_temp_segments", False):
                shutil.rmtree(temp_root, ignore_errors=True)

    # -------------------------------------------------------- 判断
    @staticmethod
    def _is_hls(url: str) -> bool:
        return ".m3u8" in (url or "").lower()

    # -------------------------------------------------------- 直链
    def _download_direct(self, control: TaskControl, url: str, target: str,
                         referer: str, headers: dict) -> str:
        part = target + ".part"
        resume_at = os.path.getsize(part) if os.path.exists(part) else 0
        request_headers = self._headers(referer, headers)
        if resume_at:
            request_headers["Range"] = "bytes=%d-" % resume_at

        logger.info("开始下载直链: %s -> %s", url, target)
        with self.http.get(url, headers=request_headers, stream=True, timeout=self._timeout(),
                           verify=bool(config.get("verify_ssl", False)), allow_redirects=True) as resp:
            if resp.status_code == 416 and resume_at:
                os.replace(part, target)
                return target
            resp.raise_for_status()
            if resp.status_code == 206:
                total = resume_at + int(resp.headers.get("Content-Length") or 0)
            else:
                resume_at = 0
                total = int(resp.headers.get("Content-Length") or 0)
            control.set(total_bytes=total, message="下载中 %s" % human_size(total))

            chunk_size = int(config.get("chunk_size", 262144))
            mode = "ab" if resume_at else "wb"
            ensure_parent_dir(target)      # 真正写文件时才建目录
            with open(part, mode) as fh:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if control.stop_event.is_set():
                        raise _Canceled()
                    if not control.wait_if_paused():
                        raise _Canceled()
                    if chunk:
                        fh.write(chunk)
                        control.update_bytes(len(chunk))

        if control.stop_event.is_set():
            raise _Canceled()
        final = self._finalize(part, target, control)
        return final

    # -------------------------------------------------------- HLS
    def _download_hls(self, control: TaskControl, url: str, target: str, temp_root: str,
                      referer: str, headers: dict, ext: str) -> str:
        os.makedirs(temp_root, exist_ok=True)
        playlist, final_url = self._load_playlist(url, referer, headers)
        control.set(message="共 %d 个分片" % len(playlist.segments) if playlist.segments else "解析播放列表")

        segments = playlist.segments
        if not segments:
            raise ValueError("播放列表中没有分片")

        key_cache = {}
        total_segments = len(segments)
        control.update_segments(0, total_segments)

        # 预估总大小（按前若干分片均值推算）
        sample = segments[: min(len(segments), 12)]
        if sample:
            estimated = sum(int(seg.duration or 4) for seg in sample)
            if estimated:
                per_second = 100 * 1024  # 约 800kbps
                total_seconds = sum(int(seg.duration or 4) for seg in segments)
                control.set(total_bytes=int(total_seconds * per_second))

        results = {}
        done = [0]
        failed = []
        lock = threading.RLock()
        workers = max(1, min(int(config.get("segment_threads", 8)), total_segments))
        max_retries = int(config.get("max_retries", 3))

        def fetch(index: int, segment, extra_headers: dict = None) -> bool:
            if control.stop_event.is_set():
                return False
            if not control.wait_if_paused():
                return False
            seg_path = os.path.join(temp_root, "seg_%05d.ts" % index)
            if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                with lock:
                    results[index] = seg_path
                return True
            seg_url = segment.absolute_uri or urljoin(final_url, segment.uri)
            for attempt in range(max_retries + 1):
                if control.stop_event.is_set():
                    return False
                try:
                    data = self._fetch_segment_bytes(
                        seg_url, referer, headers, extra_headers, control)
                    if segment.key and getattr(segment.key, "method", None) == "AES-128":
                        data = self._decrypt(segment, data, key_cache, final_url, referer, headers)
                    # 每次写分片前都确保目录在：临时目录可能被「清理临时分片」、
                    # 「清除用户配置」或启动清理删掉，而任务还在跑，
                    # 之前只在开始时建一次目录，于是出现 Errno 2 一直重试直到失败
                    os.makedirs(temp_root, exist_ok=True)
                    with open(seg_path, "wb") as fh:
                        fh.write(data)
                    control.update_bytes(len(data))
                    with lock:
                        results[index] = seg_path
                    return True
                except _Canceled:
                    return False
                except Exception as exc:  # noqa: BLE001
                    control.retries = attempt + 1
                    if attempt >= max_retries:
                        logger.warning("分片下载失败 (%s/%s): %s", index + 1, total_segments, exc)
                        return False
                    time.sleep(min(2.0 * (attempt + 1), 5.0))
            return False

        def run_pass(indexes, extra_headers=None, label=""):
            """跑一轮下载；返回失败的下标。"""
            todo = list(indexes)
            if not todo:
                return []
            still = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(fetch, idx, segments[idx], extra_headers): idx
                           for idx in todo}
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        ok = future.result()
                    except Exception:
                        ok = False
                    with lock:
                        if ok:
                            done[0] += 1
                        else:
                            still.append(index)
                        control.update_segments(done[0], total_segments)
                        control.set(message="%s分片 %d/%d" % (label, done[0], total_segments))
                    if control.stop_event.is_set():
                        break
            return sorted(still)

        failed = run_pass(range(total_segments))

        # 第二轮：补下载失败的分片，带上 Referer/Origin（有些 CDN 对无来源的请求返回 404）
        if failed and not control.stop_event.is_set():
            origin = ""
            try:
                from urllib.parse import urlparse
                parts = urlparse(final_url)
                origin = "%s://%s/" % (parts.scheme, parts.netloc)
            except Exception:
                origin = ""
            logger.info("补下载 %d 个失败分片（带 Referer/Origin 重试）", len(failed))
            failed = run_pass(failed, {"Referer": origin, "Origin": origin.rstrip("/")}, label="补下 ")

        if control.stop_event.is_set():
            raise _Canceled()

        missing = total_segments - len(results)
        if missing:
            # 资源站的 CDN 常有少量缺片，缺太多才判失败；少量缺失照样出片（播放时会跳一下）
            ratio = missing / float(total_segments)
            if ratio > 0.05:
                raise RuntimeError("分片缺失 %d/%d（超过 5%%）" % (missing, total_segments))
            gaps = [i for i in range(total_segments) if i not in results]
            control.meta["missing_segments"] = gaps
            control.meta["missing_ratio"] = round(ratio * 100, 2)
            logger.warning("有 %d/%d 个分片缺失（%.1f%%）：%s",
                           missing, total_segments, ratio * 100,
                           "、".join(str(i) for i in gaps[:12]))
            control.set(message="缺失 %d 个分片，继续合并" % missing)

        control.set(progress=92.0, message="合并分片", phase="merging")
        # 只按已下载到的分片顺序合并（缺片时不能按下标全量取，否则 KeyError）
        ordered = [results[index] for index in sorted(results)]
        if not ordered:
            raise RuntimeError("没有任何分片下载成功")
        return self._merge_segments(control, ordered, target, temp_root, ext, referer)

    def _fetch_segment_bytes(self, seg_url: str, referer: str, headers: dict,
                             extra_headers: dict, control) -> bytes:
        """下载一个分片。

        正常站点：一次 GET 读完（快路径）。

        有限速的站点：有些 CDN 会对**每个连接**在传输一定量后断开
        （实测 2rk 的 /video/ 路径：分片声明 6MB，每个连接只给 ~65KB 就断，
        而同一站的 HTML 页面完整返回）。这时普通 GET 会抛 IncompleteRead。

        对这种站点改走**分块 Range**：主动按 64KB 逐块请求，每块都完整返回，
        实测比「等断流再用 Range 续传」快 3 倍多（139 KB/s vs 41 KB/s），
        因为不用每轮都等一次连接超时。

        策略：先探一块 64KB，若服务器返回 206 且总长很大（说明确实按连接限速），
        就直接切到分块模式把剩余部分一次拿完；否则退回普通读取。
        """
        req_headers = self._headers(referer, headers, extra_headers)
        verify = bool(config.get("verify_ssl", False))
        chunk_size = int(config.get("segment_range_chunk", 65536))

        # ---- 探测：第一块用 Range 请求，顺便拿到总长度 ----
        probe_headers = dict(req_headers)
        probe_headers["Range"] = "bytes=0-%d" % (chunk_size - 1)
        probe = self.http.get(seg_url, headers=probe_headers, timeout=self._timeout(),
                              verify=verify, stream=True)
        status = probe.status_code
        content_range = probe.headers.get("Content-Range") or ""
        first = self._read_stream(probe, control)
        total = None
        m = re.search(r"/(\d+)\s*$", content_range)
        if m:
            try:
                total = int(m.group(1))
            except ValueError:
                total = None
        if total is None:
            cl = probe.headers.get("Content-Length")
            try:
                total = int(cl) if cl else None
            except (TypeError, ValueError):
                total = None

        # 服务器不支持 Range（返回 200 而非 206）→ 就是普通读取，直接返回
        if status != 206:
            return first

        buf = bytearray(first)
        # 小分片（一次就够）或服务器没给总长 → 不再分块
        if total is None or len(buf) >= total:
            return bytes(buf)

        # ---- 分块续取 ----
        rounds = 0
        max_rounds = int(config.get("segment_range_max_rounds", 2000))
        misses = 0
        while len(buf) < total and rounds < max_rounds:
            rounds += 1
            start = len(buf)
            end = min(start + chunk_size - 1, total - 1)
            hdrs = dict(req_headers)
            hdrs["Range"] = "bytes=%d-%d" % (start, end)
            try:
                resp = self.http.get(seg_url, headers=hdrs, timeout=self._timeout(),
                                     verify=verify, stream=True)
                if resp.status_code not in (200, 206):
                    break
                data = self._read_stream(resp, control)
            except _Canceled:
                raise
            except Exception:  # noqa: BLE001
                misses += 1
                if misses > 3:
                    break
                continue
            if not data:
                misses += 1
                if misses > 3:
                    break
                continue
            misses = 0
            buf.extend(data)
        if len(buf) < total:
            logger.warning("分块续传未取满（%d/%d 字节）", len(buf), total)
        return bytes(buf)

    def _read_stream(self, resp, control) -> bytes:
        """直读响应体，连接被掐断时保留已收到的部分。

        不能用 ``resp.iter_content()``：它内部会先缓冲，断流时缓冲里的数据
        会随 ChunkedEncodingError 一起丢掉（实测服务端已发 65KB 却一个字节都没交出来）。
        ``raw.stream()`` 逐块直读，断流前收到的都在手里。
        """
        out = bytearray()
        try:
            for chunk in resp.raw.stream(32768, decode_content=False):
                if control.stop_event.is_set():
                    raise _Canceled()
                if chunk:
                    out.extend(chunk)
        except _Canceled:
            raise
        except Exception as exc:  # noqa: BLE001
            salvage = self._salvage_partial(exc)
            if salvage:
                out.extend(salvage)
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        return bytes(out)

    @staticmethod
    def _salvage_partial(exc) -> bytes:
        """从 IncompleteRead / ChunkedEncodingError 里取出已收到的部分数据。

        这些异常会把部分内容带在参数里（IncompleteRead.partial），
        取出来就能少一次 Range 往返。
        """
        for obj in (exc, getattr(exc, "args", [None])[0] if exc.args else None):
            if obj is None:
                continue
            partial = getattr(obj, "partial", None)
            if isinstance(partial, (bytes, bytearray)) and partial:
                return bytes(partial)
        # ChunkedEncodingError 包装时数据在 args[0].args[0].partial
        try:
            inner = exc.args[0]
            inner2 = getattr(inner, "args", (None,))[0]
            partial = getattr(inner2, "partial", None)
            if isinstance(partial, (bytes, bytearray)) and partial:
                return bytes(partial)
        except Exception:  # noqa: BLE001
            pass
        return b""

    # -------------------------------------------------------- 播放列表
    def _load_playlist(self, url: str, referer: str, headers: dict):
        resp = self.http.get(url, headers=self._headers(referer, headers),
                             timeout=self._timeout(),
                             verify=bool(config.get("verify_ssl", False)))
        resp.raise_for_status()
        text = resp.text
        playlist = m3u8.loads(text, uri=url)
        if playlist.is_variant:
            variants = [p for p in playlist.playlists if p.uri]
            if not variants:
                raise ValueError("主播放列表中没有可用的清晰度")
            variants.sort(key=lambda p: (p.stream_info.bandwidth or 0), reverse=True)
            best = variants[0]
            best_url = urljoin(url, best.uri)
            logger.info("选择清晰度: %s", getattr(best.stream_info, "resolution", None) or best.stream_info.bandwidth)
            return self._load_playlist(best_url, referer, headers)
        return playlist, url

    # -------------------------------------------------------- 解密
    def _decrypt(self, segment, data: bytes, key_cache: dict, base_url: str,
                 referer: str, headers: dict) -> bytes:
        from Crypto.Cipher import AES

        key_uri = segment.key.absolute_uri or urljoin(base_url, segment.key.uri)
        key = key_cache.get(key_uri)
        if key is None:
            resp = self.http.get(key_uri, headers=self._headers(referer, headers),
                                 timeout=self._timeout(),
                                 verify=bool(config.get("verify_ssl", False)))
            resp.raise_for_status()
            key = resp.content
            key_cache[key_uri] = key
        iv = segment.key.iv
        if iv:
            iv_bytes = bytes.fromhex(iv.replace("0x", "").replace("0X", ""))
        else:
            iv_bytes = (segment.media_sequence or 0).to_bytes(16, "big")
        cipher = AES.new(key, AES.MODE_CBC, iv_bytes)
        decrypted = cipher.decrypt(data)
        if decrypted:
            pad = decrypted[-1]
            if 1 <= pad <= 16 and decrypted.endswith(bytes([pad]) * pad):
                decrypted = decrypted[:-pad]
        return decrypted

    # -------------------------------------------------------- 合并
    def _merge_segments(self, control: TaskControl, segment_paths, target: str,
                        temp_root: str, ext: str, referer: str) -> str:
        """优先 ffmpeg 转封装；否则对 ts 直接字节拼接。"""
        want_ffmpeg = bool(config.get("remux_to_mp4", True)) and (ext == "mp4" or config.get("delete_ts_after_remux", True))
        ffmpeg = find_ffmpeg() if want_ffmpeg else None

        # 输出目录必须先建好：ffmpeg 不会自己创建父目录，缺了会直接
        # 报 "Error opening output ...: No such file or directory"（退出码 254），
        # 然后白白退回到 .ts 拼接。Windows 上因为目录常常已存在没暴露，
        # 全新环境的 Docker 卷必然踩到（实测踩过）。
        ensure_parent_dir(target)
        ffmpeg_failed = None

        if ffmpeg:
            list_file = os.path.join(temp_root, "concat.txt")
            with open(list_file, "w", encoding="utf-8") as fh:
                for path in segment_paths:
                    fh.write("file '%s'\n" % os.path.abspath(path).replace("\\", "/"))
            output = target if ext == "mp4" else target
            cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                   "-f", "concat", "-safe", "0", "-i", list_file,
                   "-c", "copy"]
            if ext == "mp4":
                cmd += ["-bsf:a", "aac_adtstoasc", "-movflags", "+faststart"]
            cmd.append(output)
            logger.info("使用 ffmpeg 合并 %d 个分片 -> %s", len(segment_paths), output)
            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      timeout=3600)
                if proc.returncode == 0 and os.path.exists(output) and os.path.getsize(output) > 1024:
                    control.set(progress=99.0, message="转封装完成")
                    return output
                ffmpeg_failed = proc.stderr.decode("utf-8", "ignore")[-400:]
                logger.warning("ffmpeg 合并失败(退出码 %s)，改用直接拼接: %s",
                               proc.returncode, ffmpeg_failed)
            except Exception as exc:  # noqa: BLE001
                ffmpeg_failed = str(exc)
                logger.warning("ffmpeg 调用异常，改用直接拼接: %s", exc)

        # 回退：直接拼接（ts 拼接后仍是可播放的 ts 流）
        if ext == "mp4":
            target = os.path.splitext(target)[0] + ".ts"
            # 分清「没装 ffmpeg」和「ffmpeg 跑了但失败」——原来的提示一律写
            # 「未找到 ffmpeg」，会把「转封装失败」误导成「环境缺依赖」，很难排查。
            if ffmpeg_failed is not None:
                logger.warning("转封装失败，输出为 .ts 文件: %s（原因见上面的 ffmpeg 输出）", target)
            else:
                logger.warning("未使用 ffmpeg（未安装或已关闭转封装），输出为 .ts 文件: %s", target)
        ensure_parent_dir(target)
        part = target + ".part"
        with open(part, "wb") as out:
            for index, path in enumerate(segment_paths):
                if control.stop_event.is_set():
                    raise _Canceled()
                with open(path, "rb") as fh:
                    shutil.copyfileobj(fh, out, 1024 * 1024)
                control.set(progress=92.0 + 7.0 * (index + 1) / len(segment_paths),
                            message="拼接分片 %d/%d" % (index + 1, len(segment_paths)))
        return self._finalize(part, target, control)

    @staticmethod
    def clean_orphan_files() -> dict:
        """清理下载目录里的无用残留文件。

        1. 同名视频已有 .mp4 时，遗留的 .ts 视为旧的落盘产物（早期没装 ffmpeg 时生成），删掉；
        2. 空的 .part 断点文件删掉；
        3. 删除因此变空的目录。
        """
        import glob
        base = config.download_dir()
        if not os.path.isdir(base):
            return {"ts": 0, "part": 0, "freed": 0}
        removed_ts, removed_part, freed = 0, 0, 0
        videos = (".mp4", ".mkv", ".m4v", ".avi", ".webm", ".mov")
        for root, _dirs, files in os.walk(base):
            if os.path.basename(root) == ".tmp":
                continue
            names = {os.path.splitext(f)[0].lower(): f for f in files}
            for filename in files:
                full = os.path.join(root, filename)
                stem, ext = os.path.splitext(filename)
                low_ext = ext.lower()
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if low_ext == ".ts" and any(
                        os.path.exists(os.path.join(root, stem + v)) for v in videos):
                    # 已有 mp4 等成品，这个 ts 是旧的中间产物
                    try:
                        os.remove(full)
                        removed_ts += 1
                        freed += size
                        logger.info("已删除旧的 ts 产物：%s", full)
                    except OSError:
                        pass
                elif low_ext == ".part" and size == 0:
                    try:
                        os.remove(full)
                        removed_part += 1
                        logger.info("已删除空的断点文件：%s", full)
                    except OSError:
                        pass
        # 清掉因此变空的目录
        for root, _dirs, files in os.walk(base, topdown=False):
            if os.path.abspath(root) == os.path.abspath(base) or os.path.basename(root) == ".tmp":
                continue
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                continue
        return {"ts": removed_ts, "part": removed_part, "freed": freed}

    # -------------------------------------------------------- 收尾
    def _finalize(self, part: str, target: str, control: TaskControl) -> str:
        if not os.path.exists(part):
            raise RuntimeError("临时文件不存在，下载可能被中断")
        size = os.path.getsize(part)
        if size <= 0:
            raise RuntimeError("下载文件为空")
        if os.path.exists(target):
            os.remove(target)
        os.replace(part, target)
        if bool(config.get("verify_video", True)) and size < 1024 * 20:
            logger.warning("输出文件偏小(%s)，可能不完整: %s", human_size(size), target)
        control.set(total_bytes=size, done_bytes=size, progress=100.0,
                    message="完成 %s" % human_size(size))
        return target


class _Canceled(Exception):
    """内部信号：任务被取消。"""
