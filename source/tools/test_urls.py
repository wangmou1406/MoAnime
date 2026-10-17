# -*- coding: utf-8 -*-
"""URL 解析单元测试：确保子路径站点的链接拼接无误。"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core.utils import absolute_url  # noqa: E402

CASES = [
    # (base, url, expected) —— 以“与浏览器一致”为准
    ("https://a.com/demo", "/search", "https://a.com/demo/search"),
    ("https://a.com/demo", "/demo/anime/1", "https://a.com/demo/anime/1"),
    ("https://a.com/demo", "demo/anime/1", "https://a.com/demo/anime/1"),
    ("https://a.com/demo/", "anime/1", "https://a.com/demo/anime/1"),
    ("https://a.com/demo", "anime/1", "https://a.com/anime/1"),
    ("https://a.com/demo", "/anime/1", "https://a.com/demo/anime/1"),
    ("https://a.com/demo/anime/1", "demo/play/1/1", "https://a.com/demo/anime/demo/play/1/1"),
    ("https://a.com/demo/anime/1", "/demo/play/1/1", "https://a.com/demo/play/1/1"),
    ("https://a.com/demo/anime/1", "play/2", "https://a.com/demo/anime/play/2"),
    ("https://a.com/demo/anime/1", "../play/2", "https://a.com/demo/play/2"),
    ("https://a.com/demo/anime/", "play/2", "https://a.com/demo/anime/play/2"),
    ("https://a.com/demo/anime/1", "detail/2", "https://a.com/demo/anime/detail/2"),
    ("https://a.com/demo/search?wd=x", "/demo/anime/1", "https://a.com/demo/anime/1"),
    ("https://a.com/demo/p/1", "?page=2", "https://a.com/demo/p/1?page=2"),
    ("https://a.com/demo/p/1", "#top", "https://a.com/demo/p/1#top"),
    ("https://a.com/demo", "//cdn.a.com/i.png", "https://cdn.a.com/i.png"),
    ("https://a.com/demo", "https://other.com/x", "https://other.com/x"),
    ("https://a.com", "/vod/play/1-2.html", "https://a.com/vod/play/1-2.html"),
    ("https://a.com/vod", "/vod/play/1-2.html", "https://a.com/vod/play/1-2.html"),
    ("https://a.com/vod", "play/1-2.html", "https://a.com/play/1-2.html"),
    ("https://a.com/demo/demo/anime/1", "/demo/anime/2", "https://a.com/demo/anime/2"),
    ("https://a.com/demo", "", ""),
]


def main() -> int:
    failed = 0
    for base, url, expected in CASES:
        got = absolute_url(base, url)
        flag = "PASS" if got == expected else "FAIL"
        if got != expected:
            failed += 1
        print("[%s] %-34s + %-18s -> %s" % (flag, base, url, got))
        if got != expected:
            print("        期望: %s" % expected)
    print("\n%d/%d 通过" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
