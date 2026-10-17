# -*- coding: utf-8 -*-
"""下载前端依赖到 static/vendor，使 Web 界面完全离线可用。"""
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(BASE, "static", "vendor")

ASSETS = [
    ("bootstrap.min.css", [
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css",
        "https://unpkg.com/bootstrap@5.3.3/dist/css/bootstrap.min.css",
        "https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css",
    ]),
    ("bootstrap.bundle.min.js", [
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
        "https://unpkg.com/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
        "https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/js/bootstrap.bundle.min.js",
    ]),
    ("bootstrap-icons.css", [
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
        "https://unpkg.com/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
    ]),
    ("fonts/bootstrap-icons.woff2", [
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2",
        "https://unpkg.com/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2",
    ]),
    ("fonts/bootstrap-icons.woff", [
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff",
        "https://unpkg.com/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff",
    ]),
    ("Sortable.min.js", [
        "https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js",
        "https://unpkg.com/sortablejs@1.15.6/Sortable.min.js",
        "https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.6/Sortable.min.js",
    ]),
    ("socket.io.min.js", [
        "https://cdn.jsdelivr.net/npm/socket.io-client@4.7.5/dist/socket.io.min.js",
        "https://unpkg.com/socket.io-client@4.7.5/dist/socket.io.min.js",
        "https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js",
    ]),
    # HLS 播放：站点的视频都是 m3u8，浏览器原生播不了，需要 hls.js
    ("hls.min.js", [
        "https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js",
        "https://unpkg.com/hls.js@1.5.17/dist/hls.min.js",
        "https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.17/hls.min.js",
    ]),
]


def fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MoAnime-vendor-fetch"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def main() -> int:
    os.makedirs(os.path.join(VENDOR, "fonts"), exist_ok=True)
    failed = []
    for name, urls in ASSETS:
        target = os.path.join(VENDOR, name.replace("/", os.sep))
        if os.path.exists(target) and os.path.getsize(target) > 512:
            print(f"[skip] {name} 已存在 ({os.path.getsize(target)} bytes)")
            continue
        ok = False
        for url in urls:
            try:
                data = fetch(url)
                if len(data) < 200:
                    continue
                with open(target, "wb") as f:
                    f.write(data)
                print(f"[ok]   {name} <- {url} ({len(data)} bytes)")
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[miss] {name} <- {url} : {exc}")
        if not ok:
            failed.append(name)
    if failed:
        print("以下资源下载失败，界面将回退到 CDN：" + ", ".join(failed))
        return 1
    print("全部前端资源已本地化完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
