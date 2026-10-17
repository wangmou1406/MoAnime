# -*- coding: utf-8 -*-
"""验证 XML 解析：与同一接口的 JSON 版逐项对照。

这是最有力的验证方式 —— 同一份数据两种格式，解析结果必须一致。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from sources.maccms import MacCmsSource, parse_maccms_xml, looks_like_xml  # noqa: E402

PAIRS = [
    ('非凡资源', 'http://cj.ffzyapi.com/api.php/provide/vod',
     'http://cj.ffzyapi.com/api.php/provide/vod/at/xml/'),
    ('量子资源', 'https://cj.lziapi.com/api.php/provide/vod',
     'https://cj.lziapi.com/api.php/provide/vod/at/xml/'),
]


def make(name, api):
    return MacCmsSource({'id': 0, 'name': name, 'type': 'maccms', 'base_url': '',
                         'config': {'api': api, 'request_timeout': 25}})


def summarize(plugin, label):
    """跑一遍完整链路，返回可比对的摘要。"""
    try:
        results = plugin.search('海贼', 1)
    except Exception as exc:  # noqa: BLE001
        return {'label': label, 'error': str(exc)[:80]}
    if not results:
        return {'label': label, 'error': '搜不到内容'}
    first = results[0]
    out = {'label': label, '命中': len(results), '番剧': first['name'],
           '封面': (first.get('cover') or '')[:40],
           '备注': first.get('update_info') or ''}
    try:
        eps = plugin.episodes(first['url'], first['name'])
        out['集数'] = len(eps)
        out['首集名'] = eps[0]['name'] if eps else ''
        out['首集地址'] = (eps[0]['url'][:70] if eps else '')
    except Exception as exc:  # noqa: BLE001
        out['剧集错误'] = str(exc)[:70]
    return out


def main():
    print('=' * 74)
    print('JSON 与 XML 同接口对照')
    print('=' * 74)
    all_ok = True
    for name, json_api, xml_api in PAIRS:
        print()
        print('── %s ──' % name)
        j = summarize(make(name + '(JSON)', json_api), 'JSON')
        x = summarize(make(name + '(XML)', xml_api), 'XML')
        keys = ['命中', '番剧', '集数', '首集名']
        same = True
        for k in keys:
            jv, xv = j.get(k), x.get(k)
            flag = '✓' if jv == xv else '✗'
            if jv != xv:
                same = False
                all_ok = False
            print('   %s %-6s JSON=%-28s XML=%s' % (flag, k, str(jv)[:28], str(xv)[:40]))
        for k in ('error', '剧集错误'):
            if j.get(k) or x.get(k):
                print('   ! JSON %s=%s' % (k, j.get(k)))
                print('   ! XML  %s=%s' % (k, x.get(k)))
                all_ok = False
        # 播放地址的 CDN 应来自同一域名族
        ju, xu = j.get('首集地址', ''), x.get('首集地址', '')
        if ju and xu:
            print('   %s 首集地址一致' % ('✓' if ju == xu else '≈'))
            if ju != xu:
                print('       JSON: %s' % ju)
                print('       XML : %s' % xu)
        print('   → %s' % ('两种格式解析结果一致 ✓' if same else '存在差异，需检查 ✗'))

    print()
    print('=' * 74)
    print('解析器单元检查')
    print('=' * 74)
    sample = ('<?xml version="1.0"?><rss version="5.1"><list page="1" pagecount="3" recordcount="20">'
              '<video><last>2026-09-27</last><id>21681</id><tid>30</tid>'
              '<name><![CDATA[海贼王]]></name><type>日韩动漫</type><pic>http://x/a.jpg</pic>'
              '<year>1999</year><note><![CDATA[更新至第1180集]]></note>'
              '<dl><dd flag="feifan"><![CDATA[第01集$http://a/1.m3u8#第02集$http://a/2.m3u8]]></dd>'
              '<dd flag="ffm3u8"><![CDATA[第01集$http://b/1.m3u8]]></dd></dl></video>'
              '</list></rss>')
    print('  looks_like_xml(sample) = %s' % looks_like_xml(sample))
    d = parse_maccms_xml(sample)
    it = d['list'][0]
    checks = [
        ('条目数', len(d['list']), 1),
        ('vod_id', it.get('vod_id'), '21681'),
        ('vod_name', it.get('vod_name'), '海贼王'),
        ('type_name', it.get('type_name'), '日韩动漫'),
        ('vod_pic', it.get('vod_pic'), 'http://x/a.jpg'),
        ('vod_year', it.get('vod_year'), '1999'),
        ('vod_remarks', it.get('vod_remarks'), '更新至第1180集'),
        ('线路数(按$$$分)', len(it.get('vod_play_url', '').split('$$$')), 2),
        ('vod_play_from', it.get('vod_play_from'), 'feifan$$$ffm3u8'),
        ('分页 pagecount', d.get('pagecount'), '3'),
    ]
    for label, got, want in checks:
        ok = got == want
        if not ok:
            all_ok = False
        print('   %s %-18s %s' % ('✓' if ok else '✗', label, got))

    print()
    print('  总结论: %s' % ('全部通过 ✓' if all_ok else '有失败项 ✗'))
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
