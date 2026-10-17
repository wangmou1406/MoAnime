# -*- coding: utf-8 -*-
"""端到端验证一键排序：逐个模式排一遍，打印顺序与质量分。"""
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
B = 'http://127.0.0.1:8848'


def get(p, t=60):
    return json.loads(urllib.request.urlopen(B + p, timeout=t).read())


def post(p, payload=None):
    d = json.dumps(payload or {}, ensure_ascii=False).encode()
    r = urllib.request.Request(B + p, data=d,
                               headers={'Content-Type': 'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(r, timeout=120).read())


def show(tag, sources, limit=6):
    names = [(s['name'], round(float(s.get('quality') or 0)), s.get('hit_count') or 0) for s in sources]
    head = '  '.join('%s(%s/h%s)' % (n, q, h) for n, q, h in names[:limit])
    tail = ' …' if len(names) > limit else ''
    print('  %-10s %s%s' % (tag, head, tail))


print('=== 排序前（当前顺序）===')
cur = get('/api/sources')['data']['sources']
show('当前', cur)

print()
print('=== 逐个模式排序 ===')
for mode in ('name', 'health', 'hits', 'enabled', 'smart', 'ad', 'manual'):
    r = post('/api/sources/sort', {'mode': mode})
    if not r.get('ok'):
        print('  %-10s 失败: %s' % (mode, r.get('error')))
        continue
    show(mode, r['data']['sources'])

print()
print('=== 智能排序的分数明细 ===')
final = post('/api/sources/sort', {'mode': 'smart'})['data']['sources']
for s in final:
    flags = []
    if s.get('ad_flag'):
        flags.append('广告/残缺')
    if not s.get('enabled'):
        flags.append('未启用')
    if s.get('last_error'):
        flags.append('有报错')
    print('  %-14s 质量 %5.1f | 成功 %-3s 失败 %-3s 命中 %-5s 耗时 %sms %s'
          % (s['name'], float(s.get('quality') or 0), s.get('ok_count'), s.get('fail_count'),
             s.get('hit_count'), s.get('last_ms'), ' '.join(flags)))
