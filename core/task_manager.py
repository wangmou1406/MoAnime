# -*- coding: utf-8 -*-
"""下载管理器：任务队列、并发调度、暂停/继续/取消、进度广播。"""
import heapq
import os
import re
import threading
import time
from typing import List, Optional

from core.config import config
from core.database import db
from core.download_engine import DownloadEngine, TaskControl
from core.logging_setup import get_logger
from core.naming import resolve_existing_path
from core.utils import now_str, stable_id
from sources.base import UnresolvableError

# 自动换源的最多尝试次数（避免在某个动漫上反复来回）
AUTO_FALLBACK_LIMIT = 3

logger = get_logger("moanime.tasks")

_MEDIA_SUFFIXES = (".m3u8", ".mp4", ".flv", ".ts", ".mkv", ".m4v", ".webm", ".mov", ".avi")


def _looks_like_media(url: str) -> bool:
    """判断链接是否像可直接下载的媒体地址（而不是播放页/分享页）。"""
    path = (url or "").split("?")[0].split("#")[0].lower()
    return path.endswith(_MEDIA_SUFFIXES)


def _same_episode(name_a: str, name_b: str) -> bool:
    """判断两个剧集名是否指同一集（比较其中的数字，兼容“第01集/第1话/E01”）。"""
    if not name_a or not name_b:
        return False
    nums_a = re.findall(r"\d+", name_a)
    nums_b = re.findall(r"\d+", name_b)
    if nums_a and nums_b:
        return int(nums_a[0]) == int(nums_b[0])
    return name_a.strip() == name_b.strip()


class DownloadManager:
    """把“下载请求”变成可观测、可控制的任务。"""

    def __init__(self, engine: DownloadEngine = None, broadcaster=None):
        self.engine = engine or DownloadEngine()
        self.broadcaster = broadcaster
        self._controls = {}
        self._queue = []
        self._counter = 0
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._workers = []
        self._worker_count = 0
        self._running = False

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        with self._lock:
            if self._running:
                self._resize_workers()
                return
            self._running = True
            # 上次退出时未完成的任务标记为已中断，避免“僵尸运行中”
            stale = db.list_tasks(status="running")
            for task in stale:
                db.update_task(task["id"], {"status": "failed", "error": "程序重启，任务中断",
                                            "message": "程序重启，任务中断"})
            self._resize_workers()
        logger.info("下载管理器已启动（并发 %d）", self._worker_count)
        resumed = self.resume_pending()
        if resumed:
            logger.info("已从数据库恢复 %d 个排队任务", resumed)
        # 顺手清理上次失败/取消留下的空文件夹
        cleaned = self.clean_empty_dirs()
        if cleaned:
            logger.info("已清理 %d 个空文件夹", cleaned)
        # 开启了「自动清理多余 .ts 产物」时，顺便清掉旧的 ts 与空断点文件
        if bool(config.get("delete_ts_after_remux", True)):
            result = self.clean_orphan_files()
            if result.get("ts") or result.get("part"):
                logger.info("已清理 %d 个旧 ts、%d 个空断点文件，释放 %s",
                            result["ts"], result["part"], result.get("freed") or 0)

    def resume_pending(self) -> int:
        """把数据库里仍处于“排队中”的任务重新装回内存队列。

        任务队列是内存堆，重启后必须重新装载；否则这些任务会永远停在“排队中”
        （界面显示排队，但没有任何工作线程会去取它）。
        """
        try:
            records = db.list_tasks(status="queued")
        except Exception as exc:  # noqa: BLE001
            logger.error("恢复排队任务失败：%s", exc, exc_info=True)
            return 0
        count = 0
        for record in records:
            task_id = record.get("id")
            if not task_id:
                continue
            with self._lock:
                if task_id in self._controls:
                    continue
            control = TaskControl(task_id, {
                "anime_name": record.get("anime_name") or "",
                "episode_name": record.get("episode_name") or "",
                "source": record.get("source") or "",
                "source_id": record.get("source_id"),
                "episode_url": record.get("url") or "",
                "anime_url": record.get("anime_url") or "",
                "video_url": "",
            })
            control.add_listener(self._on_control_event)
            control.status = "queued"
            control.meta["episode_num"] = record.get("episode_num") or 0
            with self._lock:
                self._controls[task_id] = control
                self._counter += 1
                heapq.heappush(self._queue,
                               (-int(record.get("priority") or 0), self._counter, task_id))
                self._cond.notify_all()
            count += 1
        return count

    def stop(self) -> None:
        with self._lock:
            self._running = False
            controls = list(self._controls.values())
            self._cond.notify_all()
        for control in controls:
            if control.status in ("queued", "running", "paused"):
                control.stop_event.set()
                control.pause_event.set()
        for worker in self._workers:
            worker.join(timeout=2)
        with self._lock:
            self._workers = []
            self._worker_count = 0
        logger.info("下载管理器已停止")

    def _resize_workers(self) -> None:
        """按设置调整工作线程数量（动态伸缩）。"""
        desired = max(1, int(config.get("concurrent_tasks", 3) or 1))
        while self._worker_count < desired:
            worker = threading.Thread(target=self._worker_loop, name="dl-%d" % self._worker_count,
                                      daemon=True)
            worker.start()
            self._workers.append(worker)
            self._worker_count += 1
        # 线程过多时通过“多余的退出”机制回收
        while self._worker_count > desired:
            self._extra_stop = getattr(self, "_extra_stop", 0) + 1
            self._worker_count -= 1
            self._cond.notify_all()

    def apply_settings(self) -> None:
        with self._lock:
            self._resize_workers()

    # ------------------------------------------------------------ 入队
    def submit(self, anime_name: str, episode_name: str, source_id: int = None,
               source_name: str = "", episode_url: str = "", anime_url: str = "",
               video_url: str = "", priority: int = 0, task_id: str = None,
               **kwargs) -> dict:
        """创建下载任务并排队。video_url 为空时会在执行阶段实时解析。

        注意：这里**不做**「文件已存在就跳过」的判断——用户点了下载就下载，
        同一集重复下载会覆盖原文件（想让程序跳过时，勾选集数即可）。
        """
        anime_name = (anime_name or "").strip()
        episode_name = (episode_name or "").strip()
        if not anime_name or not episode_name:
            raise ValueError("动漫名与剧集名不能为空")

        task_id = task_id or stable_id(anime_name, episode_name, source_name or source_id,
                                       time.time())
        control = TaskControl(task_id, {
            "anime_name": anime_name,
            "episode_name": episode_name,
            "source": source_name or "",
            "source_id": source_id,
            "episode_url": episode_url,
            "anime_url": anime_url,
            "video_url": video_url,
            # 默认 False：只从用户选的那个源下载，失败就报错
            "auto_fallback": bool(kwargs.get("auto_fallback", False)),
        })
        control.add_listener(self._on_control_event)
        control.set(status="queued", message="已加入队列")

        record = {
            "id": task_id,
            "source": source_name or "",
            "anime_name": anime_name,
            "episode_name": episode_name,
            "episode_num": 0,
            "url": video_url or episode_url,
            "status": "queued",
            "priority": int(priority),
            "progress": 0,
            "message": "已加入队列",
            "created_at": now_str(),
        }
        db.add_task(record)
        control.meta["episode_num"] = record["episode_num"]

        with self._lock:
            self._controls[task_id] = control
            self._counter += 1
            heapq.heappush(self._queue, (-int(priority), self._counter, task_id))
            self._cond.notify_all()
        self._broadcast("task_update", control.snapshot())
        logger.info("任务入队：%s %s", anime_name, episode_name)
        return {"id": task_id, "status": "queued", "message": "已加入队列"}

    def submit_batch(self, items: List[dict], priority: int = 0,
                     auto_fallback: bool = False) -> dict:
        created, skipped, failed = [], [], []
        for item in items or []:
            try:
                result = self.submit(
                    anime_name=item.get("anime_name") or item.get("anime"),
                    episode_name=item.get("episode_name") or item.get("episode"),
                    source_id=item.get("source_id"),
                    source_name=item.get("source") or "",
                    episode_url=item.get("episode_url") or item.get("url") or "",
                    anime_url=item.get("anime_url") or "",
                    video_url=item.get("video_url") or "",
                    priority=item.get("priority", priority),
                    auto_fallback=item.get("auto_fallback", auto_fallback),
                )
                if result.get("status") == "skipped":
                    skipped.append({"anime_name": item.get("anime_name"), **result})
                else:
                    created.append(result)
            except Exception as exc:  # noqa: BLE001
                failed.append({"anime_name": item.get("anime_name"), "error": str(exc)})
        return {"created": created, "skipped": skipped, "failed": failed,
                "created_count": len(created), "skipped_count": len(skipped)}

    # ------------------------------------------------------------ 工作线程
    def _worker_loop(self) -> None:
        while True:
            with self._lock:
                while True:
                    if not self._running:
                        return
                    has_extra = getattr(self, "_extra_stop", 0) > 0
                    if has_extra:
                        self._extra_stop -= 1
                        return
                    task_id = self._pop_runnable()
                    if task_id:
                        break
                    self._cond.wait(timeout=0.5)
            control = self._controls.get(task_id)
            if control is None:
                continue
            if control.stop_event.is_set():
                continue
            try:
                self._run_task(control)
            except UnresolvableError as exc:
                # 该源只能看不能下（分享页）。是否自动换源由任务开关决定：
                # 默认**不换** —— 用户在剧集卡片刻意选了某个源，
                # 静默换源会换掉清晰度/字幕，比直接报错更难排查。
                if not control.meta.get("auto_fallback", False):
                    control.set(status="failed", error=str(exc),
                                message="解析失败：%s（未开启自动换源）" % exc)
                elif not self._auto_fallback(control, str(exc)):
                    control.set(status="failed", error=str(exc), message="解析失败: %s" % exc)
            except Exception as exc:  # noqa: BLE001
                logger.error("任务执行异常 %s: %s", task_id, exc, exc_info=True)
                control.set(status="failed", error=str(exc), message="任务异常: %s" % exc)

    def _auto_fallback(self, control: "TaskControl", reason: str) -> bool:
        """解析不出视频地址时，换用其它源重排同一集。

        返回 True 表示已重新入队（原任务标记为失败但附带说明）。
        """
        meta = control.meta
        tried = list(meta.get("tried_sources") or [])
        current = meta.get("source") or ""
        if current and current not in tried:
            tried.append(current)
        if len(tried) >= AUTO_FALLBACK_LIMIT:
            control.set(status="failed", error=reason,
                        message="换源 %d 次仍失败，请手动选择其它源" % len(tried))
            return False

        anime_name = meta.get("anime_name") or ""
        episode_name = meta.get("episode_name") or ""
        try:
            from sources.manager import source_manager
            records = source_manager.list_sources()
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动换源失败（无法读取源列表）：%s", exc)
            return False

        for record in records:
            if not record.get("enabled"):
                continue
            if record.get("name") in tried:
                continue
            try:
                result = source_manager.search_one(record["id"], anime_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("换源检索 %s 失败：%s", record.get("name"), exc)
                continue
            if not result.get("ok") or not result.get("results"):
                continue

            for item in result["results"][:3]:
                try:
                    episodes = source_manager.episodes(record["id"], item["url"], item.get("name") or "")
                except Exception:  # noqa: BLE001
                    continue
                picked = None
                for episode in episodes:
                    if _same_episode(episode.get("name") or "", episode_name):
                        picked = episode
                        break
                if not picked and len(episodes) == 1:
                    picked = episodes[0]
                if not picked:
                    continue

                logger.info("自动换源：%s → %s（%s）", current or "原源", record.get("name"),
                            picked.get("name"))
                try:
                    self.submit(
                        anime_name=anime_name,
                        episode_name=picked.get("name") or episode_name,
                        source_id=record["id"],
                        source_name=record.get("name") or "",
                        episode_url=picked.get("url") or "",
                        anime_url=item.get("url") or "",
                        priority=int(meta.get("priority") or 0) + 1,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("换源入队失败：%s", exc)
                    continue
                control.set(
                    status="failed", error=reason,
                    message="已自动换源：%s（原源 %s 只能看不能下）" % (
                        record.get("name"), current or "未知"))
                return True

        control.set(status="failed", error=reason, message="没有其它可下载的源，请手动换源")
        return False

    def _pop_runnable(self) -> Optional[str]:
        """取出一个未暂停的排队任务（保持优先级顺序）。"""
        deferred = []
        picked = None
        while self._queue:
            item = heapq.heappop(self._queue)
            task_id = item[2]
            control = self._controls.get(task_id)
            if control is None:
                continue
            if control.stop_event.is_set():
                continue
            if control.is_paused():
                deferred.append(item)
                continue
            picked = task_id
            break
        for item in deferred:
            heapq.heappush(self._queue, item)
        return picked

    def _run_task(self, control: TaskControl) -> None:
        meta = control.meta
        anime_name = meta["anime_name"]
        episode_name = meta["episode_name"]
        control.set(status="running", message="准备下载")
        db.update_task(control.id, {"status": "running", "started_at": now_str()})

        video_url = meta.get("video_url")
        if not video_url:
            source_id = meta.get("source_id")
            episode_url = meta.get("episode_url") or ""
            if source_id:
                from sources.manager import source_manager
                try:
                    video_url = source_manager.resolve_video_url(
                        source_id, episode_url, meta.get("anime_url") or "", episode_name)
                    # 取流方式可能带回 Referer/Cookie（浏览器嗅探那类），
                    # 存下来给下载器用
                    resolve_meta = dict(getattr(source_manager, "last_resolve_meta", {}) or {})
                    if resolve_meta:
                        meta.update({k: v for k, v in resolve_meta.items() if v})
                except Exception as exc:  # noqa: BLE001
                    # 有些资源站的剧集地址本身就是可下载的 m3u8/直链，解析失败时可以直接用；
                    # 但如果是分享页/播放页（HTML），直接当视频下载只会得到一个网页文件，必须报错。
                    if _looks_like_media(episode_url):
                        logger.warning("解析视频地址失败，改用剧集地址重试：%s", exc)
                        video_url = episode_url
                    else:
                        raise UnresolvableError(
                            "该源给的是分享页、解析不出视频地址；"
                            "结果列表里带「直链可下载」标记的源可以正常下载"
                        ) from exc
            else:
                video_url = episode_url
        if not video_url:
            raise RuntimeError("无法解析视频地址")

        meta["video_url"] = video_url
        db.update_task(control.id, {"url": video_url})
        # 取流时可能带回必须的请求头：Referer 与会话 Cookie。
        # 例如 Anime1 的视频 CDN 会校验一个路径绑定的 JWT cookie，
        # 不带它直连一律 403（只改 Referer 无效，实测过）。
        resolve_headers = dict(meta.get("headers") or {})
        referer = meta.get("referer") or ""
        if not referer and meta.get("browser_resolved") and meta.get("episode_url"):
            referer = meta.get("episode_url")
        result = self.engine.download(control, video_url, anime_name, episode_name,
                                      referer=referer,
                                      headers=resolve_headers or None)

        if result:
            size = os.path.getsize(result) if os.path.exists(result) else 0
            missing = control.meta.get("missing_segments") or []
            if missing:
                # 源站 CDN 缺片：文件已经出来，但必须明确告知，并提示可以换源重下完整版
                note = ("完成，但源站缺 %d 个分片（%s）——换一个源重下可得完整版"
                        % (len(missing), "、".join(str(i) for i in missing[:6])))
                db.update_task(control.id, {
                    "status": "completed", "progress": 100, "save_path": result,
                    "total_bytes": size, "done_bytes": size, "speed": 0,
                    "message": note, "finished_at": now_str(), "error": "",
                })
                logger.warning("下载完成但不完整：%s %s 缺 %d 个分片",
                               anime_name, episode_name, len(missing))
            else:
                db.update_task(control.id, {
                    "status": "completed", "progress": 100, "save_path": result,
                    "total_bytes": size, "done_bytes": size, "speed": 0,
                    "message": "下载完成", "finished_at": now_str(), "error": "",
                })
            db.add_history(meta.get("source") or "", anime_name, episode_name, result, size)
            logger.info("下载完成：%s %s -> %s", anime_name, episode_name, result)
        elif control.status == "canceled":
            db.update_task(control.id, {"status": "canceled", "message": "已取消",
                                        "finished_at": now_str()})

    def _on_control_event(self, control: TaskControl) -> None:
        snapshot = control.snapshot()
        try:
            db.update_task(control.id, {
                "status": snapshot["status"],
                "progress": snapshot["progress"],
                "total_bytes": snapshot["total_bytes"],
                "done_bytes": snapshot["done_bytes"],
                "speed": snapshot["speed"],
                "segments_total": snapshot["segments_total"],
                "segments_done": snapshot["segments_done"],
                "message": snapshot["message"],
                "error": snapshot["error"],
                "save_path": snapshot["save_path"],
                "retries": snapshot["retries"],
            })
        except Exception:
            pass
        self._broadcast("task_update", snapshot)

    # ------------------------------------------------------------ 控制
    def pause(self, task_id: str) -> bool:
        control = self._controls.get(task_id)
        if not control:
            return False
        control.pause()
        with self._lock:
            self._cond.notify_all()
        db.update_task(task_id, {"status": "paused", "message": "已暂停"})
        return True

    def resume(self, task_id: str) -> bool:
        control = self._controls.get(task_id)
        if not control:
            return False
        control.resume()
        with self._lock:
            if not any(item[2] == task_id for item in self._queue) and control.status == "paused":
                self._counter += 1
                heapq.heappush(self._queue, (0, self._counter, task_id))
            self._cond.notify_all()
        db.update_task(task_id, {"status": "queued" if control.status == "queued" else "running",
                                 "message": "继续下载"})
        return True

    def cancel(self, task_id: str) -> bool:
        control = self._controls.get(task_id)
        if not control:
            return False
        control.cancel()
        with self._lock:
            self._cond.notify_all()
        db.update_task(task_id, {"status": "canceled", "message": "已取消", "finished_at": now_str()})
        return True

    def retry(self, task_id: str) -> dict:
        record = db.get_task(task_id)
        if not record:
            raise ValueError("任务不存在")
        if record["status"] not in ("failed", "canceled"):
            raise ValueError("只有失败或已取消的任务可以重试")
        meta = self._controls.get(task_id)
        episode_url = ""
        anime_url = ""
        video_url = ""
        source_id = None
        source_name = record.get("source") or ""
        if meta is not None:
            episode_url = meta.meta.get("episode_url") or ""
            anime_url = meta.meta.get("anime_url") or ""
            source_id = meta.meta.get("source_id")
        # 视频地址通常带时效，重试时重新解析更可靠
        db.delete_task(task_id)
        with self._lock:
            self._controls.pop(task_id, None)
        result = self.submit(record["anime_name"], record["episode_name"], source_id=source_id,
                             source_name=source_name, episode_url=episode_url,
                             anime_url=anime_url, video_url=video_url)
        return result

    def pause_all(self) -> int:
        count = 0
        for task_id, control in list(self._controls.items()):
            if control.status in ("running", "queued"):
                self.pause(task_id)
                count += 1
        return count

    def resume_all(self) -> int:
        count = 0
        for task_id, control in list(self._controls.items()):
            if control.status == "paused":
                self.resume(task_id)
                count += 1
        return count

    def delete(self, task_id: str, delete_file: bool = False) -> bool:
        record = db.get_task(task_id)
        control = self._controls.get(task_id)
        if control and control.status in ("running", "queued", "paused"):
            control.cancel()
        if delete_file and record and record.get("save_path") and os.path.exists(record["save_path"]):
            try:
                os.remove(record["save_path"])
                logger.info("已删除文件：%s", record["save_path"])
            except OSError as exc:
                logger.warning("删除文件失败：%s", exc)
        db.delete_task(task_id)
        with self._lock:
            self._controls.pop(task_id, None)
        self._broadcast("task_removed", {"id": task_id})
        return True

    def clear(self, statuses=("completed", "failed", "canceled"), remove_memory: bool = True) -> int:
        count = db.clear_tasks(statuses)
        if remove_memory:
            with self._lock:
                for task_id, control in list(self._controls.items()):
                    if control.status in statuses:
                        self._controls.pop(task_id, None)
        self._broadcast("tasks_cleared", {"count": count})
        return count

    def clear_pending(self) -> int:
        """取消并删除所有“排队中/已暂停”的任务（清空待下载队列）。"""
        records = db.list_tasks(status="queued") + db.list_tasks(status="paused")
        removed = 0
        for record in records:
            task_id = record.get("id")
            control = self._controls.get(task_id)
            try:
                if control:
                    control.cancel()
            except Exception:  # noqa: BLE001
                pass
            try:
                db.delete_task(task_id)
                removed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除排队任务 %s 失败：%s", task_id, exc)
            with self._lock:
                self._controls.pop(task_id, None)
        self._broadcast("tasks_cleared", {"count": removed})
        logger.info("已清空 %d 个排队/暂停任务", removed)
        return removed

    def reset_all(self) -> int:
        """出厂重置用：**先彻底停下载，再清空任务**，最后清库。

        顺序很关键：如果先清库、后停任务，正在跑的 worker 会在结束时把
        「running/canceled」状态写回数据库（db.update_task 会重建记录），
        于是任务在重置后又冒出来。所以这里按 停 → 清 → 删 的顺序做。
        """
        # 1) 停掉 worker 与所有活动任务
        try:
            self.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("重置时停止下载管理器失败：%s", exc)

        # 2) 清空内存队列与任务控制表
        with self._lock:
            self._queue = []
            self._controls = {}

        # 3) 最后清数据库，确保没有 worker 再来写
        removed = db.purge_all_tasks()
        self._broadcast("tasks_cleared", {"count": removed})

        # 4) 重新拉起 worker（用户还能继续下载）
        try:
            self.apply_settings()
        except Exception as exc:  # noqa: BLE001
            logger.warning("重置后重启 worker 失败：%s", exc)
        logger.info("出厂重置：已停止下载并清空 %d 条任务记录", removed)
        return removed

    def clear_all(self, delete_file: bool = False) -> int:
        """删除**全部**任务记录（进行中的先取消）。

        delete_file=False（默认）只清任务记录，不动磁盘上已经下载好的视频文件；
        delete_file=True 时连记录里 ``save_path`` 指向的文件一起删（文件不存在就跳过）。
        """
        records = db.list_tasks()
        removed = 0
        files_removed = 0
        for record in records:
            task_id = record.get("id")
            control = self._controls.get(task_id)
            if control:
                try:
                    control.cancel()
                except Exception:  # noqa: BLE001
                    pass
            if delete_file:
                path = record.get("save_path")
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        files_removed += 1
                    except OSError as exc:
                        logger.warning("删除文件失败 %s：%s", path, exc)
            try:
                db.delete_task(task_id)
                removed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除任务 %s 失败：%s", task_id, exc)
            with self._lock:
                self._controls.pop(task_id, None)
        self._broadcast("tasks_cleared", {"count": removed})
        logger.info("已删除全部 %d 条任务记录（文件 %d 个）", removed, files_removed)
        return removed

    @staticmethod
    def clean_orphan_files() -> dict:
        """清理旧的 .ts 产物与空断点文件（转由引擎实现）。"""
        return DownloadEngine.clean_orphan_files()

    @staticmethod
    def clean_empty_dirs() -> int:
        """删除下载目录里的空文件夹（任务失败/取消时可能残留）。

        只删「一层层都是空的」目录，绝不动含视频文件的目录。
        """
        base = config.download_dir()
        if not os.path.isdir(base):
            return 0
        removed = 0
        # 深度优先，先删最里层，再回头看父目录是否也空了
        for root, dirs, files in os.walk(base, topdown=False):
            if os.path.abspath(root) == os.path.abspath(base):
                continue
            if os.path.basename(root).startswith("."):
                continue
            try:
                if not os.listdir(root):
                    os.rmdir(root)
                    removed += 1
                    logger.info("已删除空目录：%s", root)
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------ 查询
    def snapshot(self, task_id: str) -> Optional[dict]:
        control = self._controls.get(task_id)
        if control:
            data = control.snapshot()
            data.update({k: control.meta.get(k) for k in
                         ("anime_name", "episode_name", "source", "episode_url")})
            return data
        record = db.get_task(task_id)
        if record:
            record["is_history"] = True
        return record

    def list_tasks(self, status: str = None) -> List[dict]:
        records = db.list_tasks(status=status)
        for record in records:
            control = self._controls.get(record["id"])
            if control:
                record.update(control.snapshot())
        return records

    def statistics(self) -> dict:
        records = db.list_tasks()
        stats = {"total": len(records), "queued": 0, "running": 0, "paused": 0,
                 "completed": 0, "failed": 0, "canceled": 0,
                 "total_bytes": 0, "speed": 0.0}
        for record in records:
            status = record.get("status") or "queued"
            if status in stats:
                stats[status] += 1
            stats["total_bytes"] += int(record.get("total_bytes") or 0)
        for control in self._controls.values():
            if control.status == "running":
                stats["speed"] += control.speed or 0
        return stats

    def active_ids(self) -> List[str]:
        return [task_id for task_id, control in self._controls.items()
                if control.status in ("running", "queued", "paused")]

    # ------------------------------------------------------------ 广播
    def _broadcast(self, event: str, payload: dict) -> None:
        if not self.broadcaster:
            return
        try:
            self.broadcaster(event, payload)
        except Exception:
            pass


download_manager = DownloadManager()
