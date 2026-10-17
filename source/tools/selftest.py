# -*- coding: utf-8 -*-
"""端到端集成测试：启动真实服务，验证完整链路。

覆盖：内置源就位 → 源管理（增删改/排序/勾选/测试） → 聚合检索（真实资源接口）
→ 剧集 → 视频地址 → 任务创建 → 任务控制 → 设置与命名 → 媒体库 → 日志
→ 自动探测 → 导入导出 → 页面与静态资源。

用法： python tools/selftest.py [端口] [--download]
  --download  额外执行一次真实下载验证（较慢，约 1~3 分钟）
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT = int(POSITIONAL[0]) if POSITIONAL else 8899
DO_DOWNLOAD = "--download" in sys.argv
HOST = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def request(path, method="GET", payload=None, timeout=180):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(HOST + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": "HTTP %s: %s" % (exc.code, body[:180])}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def status_of(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(HOST + path), timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  [%s] %s %s" % ("PASS" if condition else "FAIL", name, detail))


def wait_server(timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HOST + "/api/meta", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.6)
    return False


def main():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "run.py"), "--port", str(PORT), "--no-browser"],
        cwd=BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_server():
            print("服务启动失败")
            return 2

        print("\n== 1. 基础信息 ==")
        meta = request("/api/meta")
        check("meta 接口", meta.get("ok"),
              "python=%s ffmpeg=%s" % (meta["data"]["python"], meta["data"]["ffmpeg"]))
        types = [t["type"] for t in meta["data"]["source_types"]]
        check("源类型注册", {"maccms", "generic", "jsonapi", "jsonfeed"} <= set(types), str(types))

        print("\n== 2. 内置动漫源 ==")
        sources = request("/api/sources")["data"]["sources"]
        enabled = [s for s in sources if s["enabled"]]
        check("内置源已写入", len(sources) >= 10, "共 %d 个，启用 %d 个" % (len(sources), len(enabled)))
        check("已移除演示源", not any("演示源" in s["name"] for s in sources))
        # 允许已注册的源类型；本意是「没有混进演示/占位源」，不是「只能是某种类型」。
        # anime1 是需要浏览器取流的站（见「浏览器取流」章节）。
        allowed_types = {"maccms", "anime1", "2rk", "generic", "jsonapi", "jsonfeed"}
        check("内置源类型正确", all(s["type"] in allowed_types for s in sources),
              "、".join(sorted({s["type"] for s in sources})))
        check("默认启用可用源", len(enabled) >= 8, "、".join(s["name"] for s in enabled[:6]))

        print("\n== 3. 源管理 ==")
        # 清理上次运行可能残留的测试源，保证可重复执行
        for record in request("/api/sources")["data"]["sources"]:
            if record["name"].startswith("测试源-") or record["name"] == "导入测试源":
                request("/api/sources/%d" % record["id"], "DELETE")

        created = request("/api/sources", "POST", {
            "name": "测试源-可删除", "type": "maccms",
            "base_url": "https://cj.lziapi.com/api.php/provide/vod/",
            "config": {"api": "https://cj.lziapi.com/api.php/provide/vod/", "test_keyword": "海贼"},
        })
        check("Web 新增源", created.get("ok"), str(created.get("error") or created["data"]["name"]))
        new_id = created["data"]["id"] if created.get("ok") else None
        if new_id:
            updated = request("/api/sources/%d" % new_id, "PUT", {"note": "集成测试创建"})
            check("编辑源", updated.get("ok") and updated["data"]["note"] == "集成测试创建")

        order_items = [{"id": new_id, "enabled": False}]
        order_items += [{"id": s["id"], "enabled": s["enabled"]} for s in sources
                        if s["id"] != new_id]
        reorder = request("/api/sources/reorder", "POST", {"items": order_items})
        check("拖拽排序+勾选保存",
              reorder.get("ok") and new_id is not None
              and reorder["data"]["sources"][0]["id"] == new_id
              and reorder["data"]["sources"][0]["enabled"] is False,
              "首位=%s 勾选=%s" % (reorder["data"]["sources"][0]["name"],
                                  reorder["data"]["sources"][0]["enabled"]))

        if new_id:
            test_one = request("/api/sources/%d/test" % new_id, "POST", {"keyword": "海贼"})
            apis = (test_one.get("data") or {}).get("apis") or []
            check("单源测试接口", test_one.get("ok") and bool(apis),
                  str(test_one.get("error") or ("可用接口 %d 个" % len(apis))))

        print("\n== 4. 聚合检索（真实资源接口） ==")
        search = request("/api/search", "POST", {"keyword": "海贼"})
        check("聚合检索成功", search.get("ok") and search["data"]["total"] > 0,
              "命中 %s 条，来源 %s 个，耗时 %ss" % (
                  search.get("data", {}).get("total"), search.get("data", {}).get("source_count"),
                  search.get("data", {}).get("elapsed")))
        groups = search.get("data", {}).get("groups", [])
        hit_groups = [g for g in groups if g.get("count")]
        check("多源并发返回", len(hit_groups) >= 3,
              "%d 个源有结果：%s" % (len(hit_groups), "、".join(g["source"] for g in hit_groups[:5])))
        check("结果按源优先级排序",
              [g["source_id"] for g in groups] ==
              sorted([g["source_id"] for g in groups],
                     key=lambda sid: next((i for i, s in enumerate(sources) if s["id"] == sid), 999)),
              "共 %d 个分组" % len(groups))

        if not hit_groups:
            raise SystemExit("没有源返回结果，后续用例无法执行")
        group = hit_groups[0]
        anime = group["results"][0]
        check("检索字段完整", bool(anime.get("name") and anime.get("url")),
              "%s（%s 集）" % (anime["name"], anime.get("episode_count")))

        print("\n== 5. 剧集与视频地址 ==")
        # 不同资源站对同一部动漫的地址形态不同（直链 / 分享页），逐个源挑选真正可解析的条目
        resolved, episode_ref, episodes = None, None, []
        picked_group, picked_anime = None, None
        for candidate_group in hit_groups[:6]:
            for candidate in candidate_group["results"][:3]:
                resp = request("/api/episodes", "POST", {
                    "source_id": candidate_group["source_id"], "anime_url": candidate["url"],
                    "anime_name": candidate["name"]})
                if not resp.get("ok"):
                    continue
                episode_list = resp["data"]["episodes"]
                if not episodes:
                    episodes = episode_list
                for episode in episode_list[:4]:
                    got = request("/api/video/resolve", "POST", {
                        "source_id": candidate_group["source_id"], "episode_url": episode["url"],
                        "anime_url": candidate["url"], "episode_name": episode["name"]})
                    url = (got.get("data") or {}).get("url") if got.get("ok") else ""
                    if url:
                        resolved, episode_ref = got["data"], episode
                        picked_group, picked_anime = candidate_group, candidate
                        episodes = episode_list
                        break
                if resolved and picked_anime is candidate:
                    break
            if resolved:
                break
        check("获取剧集", bool(episodes), "共 %d 集" % len(episodes))
        check("解析视频地址", bool(resolved),
              "%s → %s" % (picked_anime["name"] if picked_anime else "?",
                           (resolved or {}).get("url", "所有候选条目都未解析出地址")[:96]))
        check("地址可用于下载",
              bool(resolved) and (".m3u8" in resolved["url"]
                                  or resolved["url"].lower().split("?")[0].endswith(
                                      (".mp4", ".flv", ".ts"))),
              (resolved or {}).get("url", "")[:80])
        if not resolved:
            raise SystemExit("没有解析出可下载地址，后续用例无法执行")
        group, anime = picked_group, picked_anime

        print("\n== 6. 下载任务 ==")
        task = request("/api/download/episode", "POST", {
            "anime_name": anime["name"], "episode_name": episode_ref["name"],
            "source_id": group["source_id"], "source": group["source"],
            "episode_url": episode_ref["url"], "anime_url": anime["url"]})
        check("创建下载任务", task.get("ok"),
              str(task.get("error") or task.get("data", {}).get("status")))
        task_id = (task.get("data") or {}).get("id")

        if task_id and DO_DOWNLOAD:
            print("     等待真实下载（最多 240 秒）...")
            final = None
            for _ in range(240):
                time.sleep(1)
                current = next((t for t in request("/api/tasks")["data"]["tasks"]
                                if t["id"] == task_id), None)
                if current and current["status"] in ("completed", "failed", "canceled"):
                    final = current
                    break
            check("真实下载完成", bool(final) and final["status"] == "completed",
                  "%s %s" % (final and final["status"], (final or {}).get("error", "")))
            if final and final.get("save_path"):
                exists = os.path.exists(final["save_path"])
                size = os.path.getsize(final["save_path"]) if exists else 0
                check("文件已落盘", exists and size > 1024 * 1024,
                      "%s（%.1f MB）" % (os.path.basename(final["save_path"]),
                                         size / 1048576.0))
        elif task_id:
            check("任务控制（暂停/继续）",
                  request("/api/tasks/%s/pause" % task_id, "POST").get("ok")
                  and request("/api/tasks/%s/resume" % task_id, "POST").get("ok"))
            request("/api/tasks/%s/cancel" % task_id, "POST")

        stats_now = request("/api/tasks")
        check("任务统计", stats_now.get("ok"), str(stats_now["data"]["stats"]))

        print("\n== 7. 设置与命名 ==")
        target_dir = os.path.join(BASE_DIR, "downloads", "自定义目录")
        validate = request("/api/settings/validate-dir", "POST", {"path": target_dir})
        check("目录可写校验", validate.get("ok"),
              str(validate.get("error") or validate["data"]["path"]))
        saved = request("/api/settings", "POST", {
            "download_dir": target_dir, "concurrent_tasks": 2, "segment_threads": 6,
            "naming_scheme": "jellyfin", "default_season": 2, "organize_by_anime": True})
        check("保存设置", saved.get("ok") and saved["data"]["settings"]["download_dir"] == target_dir,
              saved["data"]["settings"]["naming_scheme"] if saved.get("ok") else "")
        from core.naming import build_download_path
        path = build_download_path("海贼王", "第3集", "mp4")
        check("命名规则生效", "Season 02" in path and path.endswith("海贼王 S02E03.mp4"),
              os.path.relpath(path, BASE_DIR))
        request("/api/settings/reset", "POST")
        check("设置重置", request("/api/settings")["data"]["settings"]["naming_scheme"] == "simple")

        print("\n== 8. 媒体库 / 日志 / 系统 ==")
        library = request("/api/library")
        check("媒体库扫描", library.get("ok"),
              "%d 部 / %.1f MB" % (len(library["data"]["items"]),
                                   library["data"]["total_size"] / 1048576.0))
        logs = request("/api/logs")
        check("日志接口", logs.get("ok") and logs["data"]["logs"],
              "%d 条" % len(logs["data"]["logs"]))
        stats = request("/api/stats")
        check("系统状态", stats.get("ok") and "disk" in stats["data"],
              "源 %s 个（启用 %s）" % (stats["data"]["sources_total"],
                                      stats["data"]["sources_enabled"]))

        print("\n== 9. 自动探测 ==")
        detect = request("/api/sources/detect", "POST", {"url": "https://www.agedm.io"})
        check("自动探测接口", detect.get("ok") and detect["data"].get("config"),
              str(detect.get("error") or ("结果容器=%s 剧集规则=%s" % (
                  detect["data"].get("search", {}).get("item", ""),
                  detect["data"].get("episodes", {}).get("item", ""))))[:88])

        print("\n== 10. 导出 / 导入 ==")
        exported = request("/api/sources/export?include_builtin=1")
        check("导出源配置", exported.get("ok") and len(exported["data"]["sources"]) >= 10,
              "%d 个源" % len(exported["data"].get("sources", [])))
        imported = request("/api/sources/import", "POST", {
            "sources": [{"name": "导入测试源", "type": "maccms",
                         "base_url": "https://bfzyapi.com/api.php/provide/vod/",
                         "config": {"api": "https://bfzyapi.com/api.php/provide/vod/"}}]})
        check("导入源配置", imported.get("ok") and imported["data"]["created"] == 1)

        if new_id:
            check("删除源", request("/api/sources/%d" % new_id, "DELETE").get("ok"))
        for record in request("/api/sources")["data"]["sources"]:
            if record["name"] in ("导入测试源", "测试源-可删除"):
                request("/api/sources/%d" % record["id"], "DELETE")

        print("\n== 11. 页面与静态资源 ==")
        with urllib.request.urlopen(HOST + "/", timeout=10) as resp:
            html = resp.read().decode("utf-8", "ignore")
        check("首页可访问", "Anime Downloader" in html, "%d 字节" % len(html))
        check("首页含中文站名", "动漫下载机" in html)
        for asset in ("/static/css/app.css", "/static/js/util.js",
                      "/static/js/views.js", "/static/vendor/Sortable.min.js"):
            check("静态资源 %s" % asset.split("/")[-1], status_of(asset) == 200)
        # 主逻辑已按职责拆到 static/js/app/ 下，逐个确认能取到
        for name in ("boot", "appearance", "settings", "sources", "search",
                     "player", "subtitles", "tasks", "events", "main"):
            check("主逻辑模块 %s" % name,
                  status_of("/static/js/app/%s.js" % name) == 200)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\n" + "=" * 62)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for name in FAIL:
        print("  - %s" % name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
