# -*- coding: utf-8 -*-
"""弹幕模块单测：解析与两种输出格式（不联网）。

网络部分（B 站接口）单独放在 test_danmaku_live.py，需要真实网络。
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import danmaku  # noqa: E402

# 一条滚动、一条顶部、一条底部、一条彩色的
SAMPLES = [
    {"time": 1.5, "mode": 1, "size": 25, "color": 16777215, "text": "第一条滚动弹幕"},
    {"time": 2.25, "mode": 5, "size": 25, "color": 16711680, "text": "顶部弹幕"},
    {"time": 3.0, "mode": 4, "size": 25, "color": 255, "text": "底部弹幕"},
    {"time": 4.75, "mode": 1, "size": 25, "color": 65280, "text": "带 <尖括号> & 符号"},
]

XML_SAMPLE = (
    '<?xml version="1.0" encoding="UTF-8"?><i><chatid>123</chatid>'
    '<d p="1.500,1,25,16777215,1500,0,0,0">第一条</d>'
    '<d p="2.250,5,25,16711680,2250,0,0,0">顶部的</d>'
    '<d p="3.000,4,25,255,3000,0,0,0">底部的</d>'
    '<d p="4.000,1,25,16777215,4000,0,0,0"></d>'
    '</i>'
).encode("utf-8")


def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field(number, payload, wire=2):
    if wire == 2:
        return varint(number << 3 | 2) + varint(len(payload)) + payload
    return varint(number << 3 | 0) + varint(payload)


def build_seg(items):
    """手工拼一段 DanmakuElem（field 1,2,3,4,5,7）的 protobuf。"""
    body = b""
    for item in items:
        elem = b""
        elem += field(1, 1000, wire=0)
        elem += field(2, int(item["time"] * 1000), wire=0)
        elem += field(3, item["mode"], wire=0)
        elem += field(4, item["size"], wire=0)
        elem += field(5, item["color"], wire=0)
        elem += field(7, item["text"].encode("utf-8"))
        body += field(1, elem)
    return body


def main():
    all_ok = True

    print("== 1) 解析 B 站 XML ==")
    items = danmaku._parse_xml(XML_SAMPLE, 123)
    checks = [
        ("解析条数", len(items), 3),
        ("第一条时间", items[0]["time"], 1.5),
        ("第一条模式", items[0]["mode"], 1),
        ("第一条颜色", items[0]["color"], 16777215),
        ("第二条是顶部", items[1]["mode"], 5),
        ("第三条是底部", items[2]["mode"], 4),
        ("空内容的丢掉", len([i for i in items if not i["text"]]), 0),
    ]
    for label, got, want in checks:
        ok = got == want
        all_ok = all_ok and ok
        print("   %s %-16s %s" % ("✓" if ok else "✗", label, got))

    print("\n== 2) 解析 protobuf 分段 ==")
    seg = danmaku._parse_seg(build_seg(SAMPLES), 123)
    checks = [
        ("解析条数", len(seg), 4),
        ("时间换算到秒", seg[0]["time"], 1.5),
        ("模式", seg[1]["mode"], 5),
        ("颜色", seg[2]["color"], 255),
        ("中文完整", seg[3]["text"], "带 <尖括号> & 符号"),
    ]
    for label, got, want in checks:
        ok = got == want
        all_ok = all_ok and ok
        print("   %s %-16s %s" % ("✓" if ok else "✗", label, got))

    print("\n== 3) 输出 XML ==")
    xml = danmaku.to_xml(SAMPLES, cid=999)
    checks = [
        ("有 xml 声明", xml.startswith('<?xml version="1.0"'), True),
        ("chatid 用 cid", "<chatid>999</chatid>" in xml, True),
        ("<d> 条数", len(re.findall(r"<d p=", xml)), 4),
        ("转义了尖括号", "&lt;尖括号&gt;" in xml, True),
        ("转义了 & 符号", "&amp; 符号" in xml, True),
        ("时间保留三位小数", '"1.500,1,25' in xml, True),
    ]
    for label, got, want in checks:
        ok = got == want
        all_ok = all_ok and ok
        print("   %s %-18s %s" % ("✓" if ok else "✗", label, got))

    print("\n== 4) 输出 ASS ==")
    ass = danmaku.to_ass(SAMPLES)
    checks = [
        ("有 Script Info", "[Script Info]" in ass, True),
        ("有 PlayRes", "PlayResX: 1920" in ass, True),
        ("有样式行", "Style: Danmaku" in ass, True),
        ("滚动用 \\move", "\\move(" in ass, True),
        ("顶部用 \\an8", "\\an8" in ass, True),
        ("底部用 \\an2", "\\an2" in ass, True),
        ("颜色转成 BBGGRR", "&H0000FF&" in ass, True),      # #FF0000 -> 蓝绿红反转
        ("事件行数", len([l for l in ass.splitlines() if l.startswith("Dialogue:")]), 4),
        ("中文原样", "第一条滚动弹幕" in ass, True),
    ]
    for label, got, want in checks:
        ok = got == want
        all_ok = all_ok and ok
        print("   %s %-18s %s" % ("✓" if ok else "✗", label, got))

    print("\n== 5) 落盘（按播放器规范命名）==")
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        video = os.path.join(folder, "第01集.mp4")
        open(video, "wb").close()
        files_xml = danmaku.save(SAMPLES, video, fmt="xml", cid=999)
        files_ass = danmaku.save(SAMPLES, video, fmt="ass", cid=999)
        files_both = danmaku.save(SAMPLES, video, fmt="both", cid=999)
        names = [os.path.basename(f) for f in files_xml + files_ass + files_both]
        checks = [
            ("xml 跟视频同名", "第01集.xml" in names, True),
            ("ass 跟视频同名（不多后缀）", "第01集.ass" in names, True),
            ("不再生成 .danmaku.ass", "第01集.danmaku.ass" in names, False),
            ("both 写出两个文件", len(set(files_both)), 2),
            ("文件非空", all(os.path.getsize(f) > 0 for f in files_both), True),
            # 旧命名的残留会被清掉
            ("清掉旧的 .danmaku.ass", os.path.isfile(
                os.path.join(folder, "第01集.danmaku.ass")), False),
        ]
        for label, got, want in checks:
            ok = got == want
            all_ok = all_ok and ok
            print("   %s %-24s %s" % ("✓" if ok else "✗", label, got))

    print("\n== 6) 外部弹幕服务（danmu_api 兼容格式）==")
    payload = {
        "count": 3,
        "comments": [
            {"cid": 1, "p": "12.5,1,16777215,abc", "m": "第一条滚动"},
            {"cid": 2, "p": "20.0,5,25,16711680,abc", "m": "第二条顶部"},
            {"cid": 3, "m": "无 p 的写法", "time": 30, "mode": 4, "color": 255},
        ],
    }
    service_items = danmaku._parse_service_comments(payload)
    checks = [
        ("解析条数", len(service_items), 3),
        ("时间", service_items[0]["time"], 12.5),
        ("3 段 p 的颜色", service_items[0]["color"], 16777215),
        ("5 段 p 的颜色", service_items[1]["color"], 16711680),
        ("5 段 p 的模式", service_items[1]["mode"], 5),
        ("无 p 的兜底", service_items[2]["time"], 30.0),
        ("按时间排序", [i["time"] for i in service_items] == [12.5, 20.0, 30.0], True),
        ("空文本丢掉", len(danmaku._parse_service_comments(
            {"comments": [{"p": "1,1,16777215,x", "m": "   "}]})), 0),
        # 标题清理：danmu_api 的标题带年份/分类/来源后缀
        ("清理标题", danmaku._clean_service_title("狐妖小红娘(2015)【国漫】from tencent"),
         "狐妖小红娘"),
        ("抽取平台", danmaku._service_platform("狐妖小红娘(2015)【国漫】from tencent"),
         "tencent"),
        ("集号 第3集", danmaku._episode_number_from_title("第3集"), 3),
        ("集号 03", danmaku._episode_number_from_title("03"), 3),
        ("集号 EP12", danmaku._episode_number_from_title("EP12"), 12),
        ("集号取不到", danmaku._episode_number_from_title("预告"), 0),
    ]
    for label, got, want in checks:
        ok = got == want
        all_ok = all_ok and ok
        print("   %s %-22s %s" % ("✓" if ok else "✗", label, got))

    print("\n  总结论: %s" % ("全部通过 ✓" if all_ok else "有失败项 ✗"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
