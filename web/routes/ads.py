# -*- coding: utf-8 -*-
"""广告体检：时长通道 + 画面文字(OCR)通道，跑完自动排序

（从原 web/routes_api.py 按职责拆出；路由仍注册在同一个 api 蓝图上。）
"""

import os
import sys
from flask import Response, stream_with_context
from core.config import config
from sources.manager import source_manager
from core import subtitle

# 跨模块引用
from ._base import api, body, fail, logger, ok
from .search import episodes

@api.route("/tools/duration-compare", methods=["GET", "POST"])
def duration_compare():
    """对比同一集在各源的时长，帮助识别插播广告的源。

    盗版资源的推广贴片（例如把片头替换成「澳门新葡京」广告）会让该源的这一集
    明显比别的源长（常见多 15~60 秒）。这里只读取播放列表时长，不下载视频。
    """
    from concurrent.futures import ThreadPoolExecutor
    from sources.maccms import hls_duration

    data = body()
    anime_name = (data.get("anime_name") or "").strip()
    episode_name = (data.get("episode_name") or "").strip()
    if not anime_name:
        return fail("缺少动漫名")
    want = subtitle.episode_number_of(episode_name) if episode_name else None

    def probe(record):
        row = {"source": record["name"], "source_id": record["id"], "ok": False}
        try:
            result = source_manager.search_one(record["id"], anime_name)
            items = result.get("results") or []
            if not items:
                row["note"] = "无匹配"
                return row
            item = items[0]
            payload = source_manager.episodes(record["id"], item["url"], item.get("name") or "")
            episodes = payload.get("episodes") or []
            picked = None
            if want is not None:
                for episode in episodes:
                    if subtitle.episode_number_of(episode.get("name") or "") == want:
                        picked = episode
                        break
            if not picked and episodes:
                picked = episodes[0]
            if not picked:
                row["note"] = "无剧集"
                return row
            url = picked.get("url") or ""
            if ".m3u8" not in url.lower():
                row["note"] = "非 m3u8 地址"
                return row
            row["seconds"] = hls_duration(url, timeout=int(config.get("source_timeout", 20) or 20))
            row["episode"] = picked.get("name")
            row["ok"] = True
        except Exception as exc:  # noqa: BLE001
            row["note"] = str(exc)[:60]
        return row

    records = [r for r in source_manager.list_sources() if r.get("enabled")]
    if not records:
        return fail("没有启用的源")
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(records)))) as pool:
        rows = list(pool.map(probe, records))

    ok_rows = [r for r in rows if r.get("ok") and r.get("seconds")]
    median = 0.0
    if ok_rows:
        lengths = sorted(r["seconds"] for r in ok_rows)
        median = lengths[len(lengths) // 2]
        for row in ok_rows:
            diff = round(row["seconds"] - median, 1)
            row["diff"] = diff
            # 明显更长 → 疑似插播广告；明显更短 → 疑似残缺/被裁剪
            if diff >= 15:
                row["verdict"] = "疑似插广告"
            elif diff <= -15:
                row["verdict"] = "疑似残缺"
            else:
                row["verdict"] = "正常"
            row["suspicious"] = row["verdict"] != "正常"
    rows.sort(key=lambda r: (0 if r.get("suspicious") else 1,
                             -abs(r.get("diff") or 0)))
    return ok({"anime": anime_name, "episode": episode_name, "median": round(median, 1),
               "rows": rows,
               "clean": [r["source"] for r in ok_rows if not r.get("suspicious")]})


@api.route("/tools/duration-stream", methods=["GET", "POST"])
def duration_stream():
    """广告体检（流式）：逐个源检查完就把结果推给前端，右下角能看到实时进度。

    输出格式为 NDJSON（每行一个 JSON 对象）：
      {"type":"start","total":11}
      {"type":"row","index":1,"total":11,"row":{...}}
      {"type":"done","median":1234.5,"rows":[...]}
    """
    import json as _json
    import queue
    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor
    from sources.maccms import hls_duration

    data = body()
    anime_name = (data.get("anime_name") or "").strip()
    episode_name = (data.get("episode_name") or "").strip()
    if not anime_name:
        return fail("缺少动漫名")
    want = subtitle.episode_number_of(episode_name) if episode_name else None
    records = [r for r in source_manager.list_sources() if r.get("enabled")]
    if not records:
        return fail("没有启用的源")

    def probe(record):
        row = {"source": record["name"], "source_id": record["id"], "ok": False}
        try:
            result = source_manager.search_one(record["id"], anime_name)
            items = result.get("results") or []
            if not items:
                row["note"] = "无匹配"
                return row
            item = items[0]
            payload = source_manager.episodes(record["id"], item["url"], item.get("name") or "")
            episodes = payload.get("episodes") or []
            picked = None
            if want is not None:
                for episode in episodes:
                    if subtitle.episode_number_of(episode.get("name") or "") == want:
                        picked = episode
                        break
            if not picked and episodes:
                picked = episodes[0]
            if not picked:
                row["note"] = "无剧集"
                return row
            url = picked.get("url") or ""
            if ".m3u8" not in url.lower():
                row["note"] = "非 m3u8 地址"
                return row
            row["seconds"] = hls_duration(url, timeout=int(config.get("source_timeout", 20) or 20))
            row["episode"] = picked.get("name")
            row["ok"] = True
        except Exception as exc:  # noqa: BLE001
            row["note"] = str(exc)[:60]
        return row

    def generate():
        yield _json.dumps({"type": "start", "total": len(records)},
                          ensure_ascii=False) + "\n"
        rows = []
        # 逐个源推送（并发查询，但按完成顺序推送，进度条能真实走动）
        with ThreadPoolExecutor(max_workers=min(6, len(records))) as pool:
            futures = {pool.submit(probe, r): r for r in records}
            for index, future in enumerate(futures, start=1):
                try:
                    row = future.result()
                except Exception as exc:  # noqa: BLE001
                    row = {"source": "?", "ok": False, "note": str(exc)[:60]}
                rows.append(row)
                yield _json.dumps({"type": "row", "index": index,
                                   "total": len(records), "row": row},
                                  ensure_ascii=False) + "\n"
        ok_rows = [r for r in rows if r.get("ok") and r.get("seconds")]
        median = 0.0
        if ok_rows:
            lengths = sorted(r["seconds"] for r in ok_rows)
            median = lengths[len(lengths) // 2]
            for row in ok_rows:
                diff = round(row["seconds"] - median, 1)
                row["diff"] = diff
                row["verdict"] = ("疑似插广告" if diff >= 15
                                  else ("疑似残缺" if diff <= -15 else "正常"))
                row["suspicious"] = row["verdict"] != "正常"
        rows.sort(key=lambda r: (0 if r.get("suspicious") else 1, -abs(r.get("diff") or 0)))
        yield _json.dumps({"type": "done", "median": round(median, 1), "rows": rows,
                           "clean": [r["source"] for r in ok_rows if not r.get("suspicious")]},
                          ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-store",
                             "X-Accel-Buffering": "no"})


@api.route("/tools/text-ad-stream", methods=["GET", "POST"])
def text_ad_stream():
    """文字广告体检（流式）。

    治的是「在片头/片中添加额外字幕」那类广告 —— 它**不改变视频时长**，
    所以时长对比完全测不出来，只能看画面：

        只下载开头 20 秒的分片 → ffmpeg 抽帧 → Windows 自带 OCR 识别文字
        → 匹配广告关键词（赞助/主营/棋牌/捕鱼/真人娱乐…）→ **删除临时文件**

    输出 NDJSON：
      {"type":"start","total":11,"seconds":20}
      {"type":"row","index":1,"total":11,"row":{...}}
    """
    import json as _json
    import queue
    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor

    data = body()
    anime_name = (data.get("anime_name") or "").strip()
    episode_name = (data.get("episode_name") or "").strip()
    try:
        seconds = int(data.get("seconds") or 20)
    except (TypeError, ValueError):
        seconds = 20
    try:
        step = int(data.get("step") or 2)
    except (TypeError, ValueError):
        step = 2
    if not anime_name:
        return fail("缺少动漫名")

    records = [r for r in source_manager.list_sources() if r.get("enabled")]
    if not records:
        return fail("没有启用的源")

    # 惰性导入检测器（依赖 ffmpeg 与 PowerShell OCR，只在用到时加载）
    try:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tools_dir = os.path.join(root_dir, "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import importlib
        detector = importlib.import_module("ad_detect")
    except Exception as exc:  # noqa: BLE001
        return fail("无法加载文字广告检测器：%s" % str(exc)[:80])

    want = subtitle.episode_number_of(episode_name) if episode_name else None

    # 先自检 OCR 是否真的可用。
    # 这一步很重要：如果 OCR 静默失败（例如缺依赖或受限环境下调不起来），
    # 检测会把每个源都报成「未发现广告」——**静默的假阴性比报错更糟**，
    # 所以宁可直接失败并说清原因。
    ocr_ok, ocr_why, ocr_engine = detector.ocr_selfcheck()
    if not ocr_ok:
        return fail("画面 OCR 不可用：%s" % ocr_why)
    logger.info("文字广告体检：OCR 引擎 = %s", ocr_engine)

    out_queue = queue.Queue()

    def probe(record):
        row = {"source": record["name"], "source_id": record["id"], "ok": False}
        try:
            result = source_manager.search_one(record["id"], anime_name)
            items = result.get("results") or []
            if not items:
                row["note"] = "无匹配"
                return row
            item = items[0]
            payload = source_manager.episodes(record["id"], item["url"],
                                              item.get("name") or "")
            episodes = payload.get("episodes") or []
            if not episodes:
                row["note"] = "无剧集"
                return row
            picked = None
            if want is not None:
                for episode in episodes:
                    if subtitle.episode_number_of(episode.get("name") or "") == want:
                        picked = episode
                        break
            if not picked:
                picked = episodes[0]
            url = picked.get("url") or ""
            if ".m3u8" not in url.lower():
                row["note"] = "非 m3u8 地址（需浏览器取流的源暂不支持）"
                return row
            det = detector.detect(m3u8=url, seconds=seconds, step=step)
            row["episode"] = picked.get("name")
            if not det.get("ok"):
                row["note"] = det.get("error") or "检测失败"
                return row
            row["ok"] = True
            row["is_ad"] = bool(det.get("is_ad"))
            row["hits"] = det.get("hits") or []
            row["scanned"] = det.get("scanned")
            row["with_text"] = det.get("with_text")
            row["samples"] = [r for r in (det.get("rows") or []) if r.get("hits")][:2]
        except Exception as exc:  # noqa: BLE001
            row["note"] = str(exc)[:70]
        return row

    def worker():
        try:
            out_queue.put({"type": "start", "total": len(records),
                           "seconds": seconds, "step": step})
            done = 0
            with ThreadPoolExecutor(max_workers=min(3, max(1, len(records)))) as pool:
                for row in pool.map(probe, records):
                    done += 1
                    out_queue.put({"type": "row", "index": done,
                                   "total": len(records), "row": row})
            out_queue.put({"type": "end"})
        except Exception as exc:  # noqa: BLE001
            out_queue.put({"type": "error", "error": str(exc)[:120]})

    _threading.Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            try:
                item = out_queue.get(timeout=900)
            except queue.Empty:
                break
            if item.get("type") == "end":
                break
            if item.get("type") == "error":
                yield _json.dumps(item, ensure_ascii=False) + "\n"
                break
            yield _json.dumps(item, ensure_ascii=False) + "\n"

    return Response(stream_with_context(generate()),
                    mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


def _mode_within(values, tol=5.0):
    """求「众数」：数值按 tol 聚类，返回最大簇的代表值（簇内均值）。

    与统计学众数不同：这里为浮点时长而写。同一集在不同源会有零点几到
    一两秒差异，硬比相等会得到一堆「众数 1」，所以按容差聚类，
    取元素最多的那一簇。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    best_center, best_count = ordered[0], 0
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] - ordered[i] <= tol:
            j += 1
        count = j - i + 1
        if count > best_count:
            best_count = count
            best_center = sum(ordered[i:j + 1]) / float(count)
        i = j + 1
    return best_center


@api.route("/tools/ad-check-stream", methods=["GET", "POST"])
def ad_check_stream():
    """广告体检（合并版）：一次跑两种检测，结束后自动排序。

    两条通道互补，缺一不可：

      1. **时长通道**：对比各源同一集的 m3u8 总时长。
         治「整段替换/插播广告片」「内容残缺」——这类会改变时长。
      2. **文字通道**：只下开头 20 秒 → 抽帧 → OCR 看画面文字。
         治「在片头/片中叠字幕广告」——这类**不改变时长**，时长测不出来。

    只跑其中一种都会漏：只跑时长会漏掉字幕广告，只跑文字会漏掉插片广告。

    结束后按结果给问题源打 `ad_flag` 并**自动排到列表末尾**（复用现成机制）。

    输出 NDJSON：
      {"type":"start","total":11,"seconds":20}
      {"type":"row","index":1,"total":11,"row":{...}}
      {"type":"done","rows":[...],"flagged":[...],"clean":[...],"sources":[...]}
    """
    import json as _json
    import queue
    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor

    data = body()
    anime_name = (data.get("anime_name") or "").strip()
    episode_name = (data.get("episode_name") or "").strip()
    try:
        seconds = int(data.get("seconds") or 20)
    except (TypeError, ValueError):
        seconds = 20
    try:
        step = int(data.get("step") or 2)
    except (TypeError, ValueError):
        step = 2
    skip_text = bool(data.get("skip_text"))          # 只跑时长通道
    if not anime_name:
        return fail("缺少动漫名")

    records = [r for r in source_manager.list_sources() if r.get("enabled")]
    if not records:
        return fail("没有启用的源")

    # 文字通道需要 OCR；不可用时降级为「只跑时长」，但要在结果里说清楚，
    # 免得用户以为字幕广告也查过了
    text_ok, text_why, text_engine = True, "", ""
    detector = None
    if not skip_text:
        try:
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tools_dir = os.path.join(root_dir, "tools")
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)
            import importlib
            detector = importlib.import_module("ad_detect")
            text_ok, text_why, text_engine = detector.ocr_selfcheck()
        except Exception as exc:  # noqa: BLE001
            text_ok, text_why = False, "无法加载检测器：%s" % str(exc)[:60]

    want = subtitle.episode_number_of(episode_name) if episode_name else None
    from sources.maccms import hls_duration
    out_queue = queue.Queue()

    def probe(record):
        """对单个源跑两条通道。"""
        row = {"source": record["name"], "source_id": record["id"], "ok": False,
               "text_ok": False, "is_ad": False, "is_incomplete": False,
               "reasons": [], "hits": [], "seconds": None, "diff": None}
        try:
            result = source_manager.search_one(record["id"], anime_name)
            items = result.get("results") or []
            if not items:
                row["note"] = "无匹配"
                return row
            item = items[0]
            payload = source_manager.episodes(record["id"], item["url"],
                                              item.get("name") or "")
            episodes = payload.get("episodes") or []
            if not episodes:
                row["note"] = "无剧集"
                return row
            picked = None
            if want is not None:
                for episode in episodes:
                    if subtitle.episode_number_of(episode.get("name") or "") == want:
                        picked = episode
                        break
            # 只有真正匹配到「同一集」的源，时长才有可比性；
            # 没匹配上而回退到第 1 集的源，时长对比毫无意义
            # （实测：不同集混在一起会让跨度高达 858s）。
            row["same_episode"] = picked is not None
            if not picked:
                picked = episodes[0]
            row["episode"] = picked.get("name")
            url = picked.get("url") or ""

            # ---- 通道 1：时长 ----
            if ".m3u8" in url.lower():
                try:
                    row["seconds"] = hls_duration(
                        url, timeout=int(config.get("source_timeout", 20) or 20))
                except Exception as exc:  # noqa: BLE001
                    row["note"] = "时长探测失败：%s" % str(exc)[:40]

            # ---- 通道 2：文字（只下开头 N 秒）----
            if not skip_text and detector is not None and text_ok:
                if ".m3u8" in url.lower():
                    det = detector.detect(m3u8=url, seconds=seconds, step=step)
                    if det.get("ok"):
                        row["text_ok"] = True
                        row["hits"] = det.get("hits") or []
                        row["samples"] = [r for r in (det.get("rows") or [])
                                          if r.get("hits")][:2]
                    else:
                        row["text_note"] = det.get("error") or "文字检测失败"
                else:
                    row["text_note"] = "非 m3u8 地址（暂不支持）"

            row["ok"] = row.get("seconds") is not None or row.get("text_ok")
            return row
        except Exception as exc:  # noqa: BLE001
            row["note"] = str(exc)[:70]
            return row

    def worker():
        try:
            out_queue.put({"type": "start", "total": len(records),
                           "seconds": seconds, "step": step,
                           "text_ok": text_ok, "text_why": text_why,
                           "text_engine": text_engine})
            rows = []
            done = 0
            workers = min(3, max(1, len(records)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for row in pool.map(probe, records):
                    rows.append(row)
                    done += 1
                    out_queue.put({"type": "row", "index": done,
                                   "total": len(records), "row": row})

            # ---- 汇总判定 ----
            # 时长通道：以**众数**（最多源给出的时长 = 大家都认的那个片长）为基准。
            # 为什么不用中位数：实测同一集在各源之间本来就有一两分钟的编码差异，
            # 中位数 ±15s 一刀切会把十几个正常源全判成插广告
            # （实测踩过：19 个源里误标 15 个）。众数抓的是「大家共同的片长」，
            # 只有真正偏离它的才是异常源。
            measured = [r for r in rows
                        if r.get("seconds") and r.get("same_episode")]
            skipped_same = [r for r in rows
                            if r.get("seconds") and not r.get("same_episode")]
            baseline = 0.0
            spread_ok = True
            spread_note = ""
            if len(measured) < 3:
                spread_ok = False
                spread_note = ("只有 %d 个源给出了同一集，不足以做时长对比"
                               % len(measured))
            else:
                vals = [r["seconds"] for r in measured]
                baseline = _mode_within(vals, tol=5.0)
                span = max(vals) - min(vals)
                # 安全阀：同一集的各源时长仍散得很开（跨度 > 90s）时，
                # 说明它们给的其实不是同一版本，任何「基准」都不可信。
                # 宁可不下结论，也不要误标一堆正常源为广告源（实测踩过：
                # 19 个源里误标 15 个）。
                if span > 90:
                    spread_ok = False
                    spread_note = ("同一集各源时长跨度 %.0fs，差异过大"
                                   "（可能不是同一版本），本次不做时长判定" % span)
                else:
                    for row in measured:
                        row["diff"] = round(row["seconds"] - baseline, 1)
                        if row["diff"] >= 20:
                            row["is_ad"] = True
                            row["reasons"].append(
                                "比常见片长多 %+.0fs（疑插播广告）" % row["diff"])
                        elif row["diff"] <= -20:
                            row["is_incomplete"] = True
                            row["reasons"].append(
                                "比常见片长少 %+.0fs（疑内容残缺）" % row["diff"])
            for row in skipped_same:
                row["note"] = ((row.get("note") or "") +
                               "；该源没有这一集，未参与时长对比").strip("；")

            for row in rows:
                if row.get("hits"):
                    row["is_ad"] = True
                    row["reasons"].append("片头有广告字幕：%s" % "、".join(row["hits"][:6]))

            # ---- 自动排序：问题源打标记并沉底 ----
            items = []
            for row in rows:
                if not (row["is_ad"] or row["is_incomplete"]):
                    continue
                kind = ("text" if row.get("hits") else
                        ("incomplete" if row["is_incomplete"] else "ad"))
                items.append({"id": row["source_id"], "ad_flag": True, "ad_kind": kind})
            items = []
            cleared = []
            for row in rows:
                if not row.get("ok"):
                    continue          # 没有结论的源不动它的标记
                if row["is_ad"] or row["is_incomplete"]:
                    kind = ("text" if row.get("hits") else
                            ("incomplete" if row["is_incomplete"] else "ad"))
                    items.append({"id": row["source_id"], "ad_flag": True,
                                  "ad_kind": kind})
                else:
                    # 本次检查判定正常 —— 清掉可能残留的旧标记，
                    # 否则历次体检的标记会不断累积（实测踩过：19 个源积到 17 个，
                    # 其中备注还写着「正常」）。
                    #
                    # 但注意非对称：字幕广告（text）**只有文字通道能发现**。
                    # 若本次是「仅时长（快）」没跑文字通道，就不能因为时长正常
                    # 就把已有的 text 标记清掉 —— 那是丢失真实结论。
                    cur = next((r for r in source_manager.list_sources()
                                if r["id"] == row["source_id"]), None)
                    if not cur or not cur.get("ad_flag"):
                        continue
                    if cur.get("ad_kind") == "text" and skip_text:
                        continue          # 没跑文字通道，保留字幕广告结论
                    cleared.append(row["source_id"])
            moved = 0
            if items or cleared:
                for item in items:
                    source_manager.set_ad_flag(item["id"], True, item["ad_kind"])
                for sid in cleared:
                    source_manager.set_ad_flag(sid, False, "")
                source_manager.demote_ad_sources()
                moved = len(items)

            # 实际比的是哪一集、多少源参与 —— 报告里要写清楚，
            # 否则用户不知道这个结论是基于什么得出的
            used_episodes = [r.get("episode") for r in measured if r.get("episode")]
            used_episode = ""
            if used_episodes:
                counts = {}
                for name in used_episodes:
                    counts[name] = counts.get(name, 0) + 1
                used_episode = max(counts.items(), key=lambda kv: kv[1])[0]

            flagged = [r["source"] for r in rows if r["is_ad"] or r["is_incomplete"]]
            clean = [r["source"] for r in rows
                     if r.get("ok") and not (r["is_ad"] or r["is_incomplete"])]
            out_queue.put({"type": "done", "rows": rows, "median": round(baseline, 1),
                           "spread_ok": spread_ok, "spread_note": spread_note,
                           "used_episode": used_episode,
                           "measured_count": len(measured),
                           "skipped_same_count": len(skipped_same),
                           "requested_episode": episode_name,
                           "flagged": flagged, "clean": clean, "moved": moved,
                           "cleared": len(cleared),
                           "text_ok": text_ok, "text_why": text_why,
                           "text_engine": text_engine,
                           "sources": source_manager.list_sources()})
        except Exception as exc:  # noqa: BLE001
            out_queue.put({"type": "error", "error": str(exc)[:120]})

    _threading.Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            try:
                item = out_queue.get(timeout=1800)
            except queue.Empty:
                break
            if item.get("type") == "error":
                yield _json.dumps(item, ensure_ascii=False) + "\n"
                break
            yield _json.dumps(item, ensure_ascii=False) + "\n"
            if item.get("type") == "done":
                break

    return Response(stream_with_context(generate()),
                    mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})
