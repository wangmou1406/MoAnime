# -*- coding: utf-8 -*-
"""文字广告检测：只下开头 20 秒 → 抽帧 → OCR → 删除临时文件。

针对「在片头/片中添加额外字幕」这类广告：它**不改变视频时长**，
所以「时长对比」体检测不出来，只能看画面。

流程（尽量省流量、省磁盘）：
  1. 只取开头若干分片（够 20 秒就停，不下载整集；自动处理 AES-128 解密）
  2. ffmpeg 抽帧（默认每 2 秒一帧）
  3. OCR 识别文字 → 匹配广告关键词
  4. **删除**临时分片与帧

OCR 引擎：优先 RapidOCR（纯 Python，随依赖装好即用，中文准）；
        退化到 Windows 自带 OCR（tools/win_ocr.ps1，零依赖但受限环境不可用）。

用法:
  python tools/ad_detect.py --video <本地视频>
  python tools/ad_detect.py --source-id 110 --keyword 海贼王
  python tools/ad_detect.py --check-ocr            # 只做 OCR 自检
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
FFMPEG = os.path.join(BASE, 'tools', 'ffmpeg.exe')
OCR_PS1 = os.path.join(BASE, 'tools', 'win_ocr.ps1')
API = 'http://127.0.0.1:8848'

# 广告特征词。来自实际样本：
#   「本片由 41905.com 赞助发布  主营：棋牌 体育 真人娱乐 电子 捕鱼」
AD_KEYWORDS = [
    '赞助', '本片由', '主营', '冠名', '出品方',
    '棋牌', '捕鱼', '真人娱乐', '电子游艺', '娱乐城', '博彩', '投注', '彩票',
    '六合', '时时彩', '澳门', '赌场', '体育下注',
    '官网', '永久域名', '最新地址', '发布页', '防走失', '请记住', '收藏本页',
    '加群', '微信号', 'QQ群', '客服', '代理',
    '资源站', '独家', '首发', '免费观看', '高清资源',
]
AD_PATTERNS = [
    (r'\d{4,6}\s*[.．,，℃c0]\s*[o0]?\s*m', '可疑域名'),
    (r'(?:www\.)?[a-z0-9\-]{3,20}\s*[.．]\s*(?:com|net|cc|vip|top|xyz|club|c0m)\b', '域名'),
    (r'1[3-9]\d{9}', '手机号'),
    (r'[Qq]{2}\s*[:：]?\s*\d{5,}', 'QQ号'),
    (r'备\s*案\s*编\s*号', '备案号'),
]

# ---------------------------------------------------------------- OCR 引擎
_RAPID = {'tried': False, 'engine': None, 'error': ''}


def _rapid_engine():
    """惰性初始化 RapidOCR（首次约 1 秒，之后复用）。"""
    if _RAPID['tried']:
        return _RAPID['engine']
    _RAPID['tried'] = True
    try:
        from rapidocr_onnxruntime import RapidOCR
        _RAPID['engine'] = RapidOCR()
    except Exception as exc:  # noqa: BLE001
        _RAPID['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:80])
        _RAPID['engine'] = None
    return _RAPID['engine']


def ocr_one(png):
    """识别一张图，返回文字（换行分隔）。优先 RapidOCR，退化到 Windows OCR。"""
    engine = _rapid_engine()
    if engine is not None:
        try:
            result, _elapse = engine(png)
            if result:
                return '\n'.join(str(item[1]) for item in result)
            return ''
        except Exception:  # noqa: BLE001
            pass
    # 退化：Windows 自带 OCR（受限环境下不可用，故只作备选）
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-File', OCR_PS1, '-Path', png],
            capture_output=True, text=True, encoding='utf-8', timeout=90)
        return (out.stdout or '').strip()
    except Exception:  # noqa: BLE001
        return ''


def ocr_many(paths, workers=3):
    if not paths:
        return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(paths))) as pool:
        return list(pool.map(ocr_one, paths))


def _find_font() -> str:
    """找一个能画中文的字体文件。

    为什么不能写死路径：Windows 用 msyh/simhei，Linux 发行版各不相同
    （Debian 装 fonts-noto-cjk 后是 /usr/share/fonts/opentype/noto/*.ttc，
    没有 dejavu）。写死路径会「找不到字体 → 画出空白图 → OCR 读不到字 →
    误报 OCR 不可用」，把一个字体问题伪装成引擎问题。实测在 Docker 里踩过。
    """
    # 1) 明确已知的路径（最快）
    for f in (
        r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyh.ttf',
        r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\simsun.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/System/Library/Fonts/PingFang.ttc',
    ):
        if os.path.exists(f):
            return f
    # 2) 让 fontconfig 告诉我们（Linux 上最可靠）
    try:
        out = subprocess.run(['fc-match', '-f', '%{file}', 'sans-serif:lang=zh'],
                             capture_output=True, text=True, timeout=10)
        path = (out.stdout or '').strip()
        if path and os.path.exists(path):
            return path
    except Exception:  # noqa: BLE001
        pass
    # 3) 兜底：在常见字体目录里扫一遍
    import glob
    for pattern in (
        '/usr/share/fonts/**/*.ttc', '/usr/share/fonts/**/*.ttf',
        '/usr/local/share/fonts/**/*.tt[cf]',
        r'C:\Windows\Fonts\*.tt*',
        '/System/Library/Fonts/*.ttc',
    ):
        try:
            hits = sorted(glob.glob(pattern, recursive=True))
        except Exception:  # noqa: BLE001
            continue
        for hit in hits:
            if os.path.isfile(hit) and os.path.getsize(hit) > 10000:
                return hit
    return ''


def ocr_selfcheck():
    """检查 OCR 是否真的可用，返回 (ok, 原因, 引擎名)。

    为什么需要：若 OCR 静默失败，检测会把每个源都报成「未发现广告」——
    **静默的假阴性比报错更糟**，所以宁可直接失败并说清原因。
    """
    tmp = tempfile.mkdtemp(prefix='ocrchk_')
    png = os.path.join(tmp, 'check.png')
    text = '广告赞助主营'
    try:
        font = _find_font()
        if not font:
            # 这条必须单独说：否则会让人以为是 OCR 引擎坏了
            return (False,
                    '没找到可用字体，无法生成测试图（Linux 上装 fonts-noto-cjk '
                    '或 fonts-wqy-zenhei 即可）', '')
        cmd = [find_ffmpeg(), '-y', '-v', 'error',
               '-f', 'lavfi', '-i', 'color=c=white:s=900x200:d=1']
        esc = font.replace('\\', '/').replace(':', r'\:')
        cmd += ['-vf', "drawtext=fontfile='%s':text='%s':fontcolor=black:"
                       "fontsize=64:x=20:y=60" % (esc, text)]
        cmd += ['-frames:v', '1', png]
        subprocess.run(cmd, timeout=60, capture_output=True)
        if not (os.path.exists(png) and os.path.getsize(png) > 1000):
            return False, '生成测试图失败（ffmpeg 不可用？）', ''
        got = ocr_one(png)
        if not got:
            why = _RAPID['error'] or 'OCR 没有返回文字'
            return False, why, ''
        compact = re.sub(r'\s+', '', got)
        hit = sum(1 for ch in text if ch in compact)
        if hit < len(text) * 0.5:
            return False, 'OCR 结果异常（识别到 %r）' % compact[:40], ''
        engine = 'RapidOCR' if _rapid_engine() is not None else 'Windows OCR'
        return True, '', engine
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 基础工具
def find_ffmpeg():
    return FFMPEG if os.path.exists(FFMPEG) else 'ffmpeg'


def judge(text):
    """返回命中的广告特征（去重）。"""
    compact = re.sub(r'\s+', '', text or '')
    hits = []
    for k in AD_KEYWORDS:
        if k in compact:
            hits.append(k)
    for pat, label in AD_PATTERNS:
        found = re.findall(pat, compact, re.I)
        if found:
            hits.append('%s:%s' % (label, found[0][:16]))
    return list(dict.fromkeys(hits))


def _safe_url(url):
    """把 URL 里的非 ASCII 部分编码（否则 requests/urllib 会报 ascii 错）。"""
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc,
                                    urllib.parse.quote(p.path, safe='/%'),
                                    urllib.parse.quote(p.query, safe='=&%'),
                                    p.fragment))


def _fetch_bytes(url, referer=None, limit=None):
    import ssl
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/122.0.0.0 Safari/537.36')}
    if referer:
        headers['Referer'] = referer
    with urllib.request.urlopen(urllib.request.Request(_safe_url(url), headers=headers),
                                timeout=30, context=ctx) as r:
        return r.read(limit) if limit else r.read()


def fetch_playlist(url):
    return _fetch_bytes(url, limit=500000).decode('utf-8', 'ignore')


def opening_segments(m3u8_url, seconds=20):
    """挑出覆盖前 N 秒的分片，并解析 AES 密钥。

    很多站的分片是 AES-128 加密的，必须解密后再抽帧，
    否则拼出来是密文，ffmpeg 报 Invalid data（实测踩过）。
    返回 (分片URL列表, 覆盖秒数, key信息)。
    """
    import urllib.parse
    text = fetch_playlist(m3u8_url)
    if '<html' in text[:300].lower() or text.lstrip().startswith('<!DOCTYPE'):
        raise RuntimeError('拿到的是网页而不是 m3u8')

    key = None
    km = re.search(r'#EXT-X-KEY:METHOD=AES-128\s*,\s*URI="([^"]+)"(?:[^\n]*IV=0x([0-9A-Fa-f]+))?',
                   text, re.I)
    if km:
        key = {'uri': urllib.parse.urljoin(m3u8_url, km.group(1)), 'iv': km.group(2)}

    durs = [float(x) for x in re.findall(r'#EXTINF:([\d.]+)', text)]
    lines = [l.strip() for l in text.split('\n') if l.strip() and not l.startswith('#')]
    if not lines:
        raise RuntimeError('m3u8 里没有分片')
    if lines[0].endswith('.m3u8'):      # 嵌套播放列表
        return opening_segments(urllib.parse.urljoin(m3u8_url, lines[0]), seconds)

    base = m3u8_url.rsplit('/', 1)[0] + '/'
    picked, total = [], 0.0
    for i, seg in enumerate(lines):
        if total >= seconds:
            break
        picked.append(seg if seg.startswith('http') else urllib.parse.urljoin(base, seg))
        if i < len(durs):
            total += durs[i]
    return (picked or [lines[0]]), total, key


def _decrypt(data, key_bytes, iv_bytes):
    try:
        from Crypto.Cipher import AES
        dec = AES.new(key_bytes, AES.MODE_CBC, iv_bytes).decrypt(data)
        if dec:
            pad = dec[-1]
            if 1 <= pad <= 16 and dec.endswith(bytes([pad]) * pad):
                dec = dec[:-pad]
        return dec
    except Exception:  # noqa: BLE001
        return data


def download_segments(urls, dest_dir, key=None, max_bytes=60 * 1024 * 1024):
    """下载分片、按需解密、拼接成一个临时 ts（限量）。"""
    out = os.path.join(dest_dir, 'open.ts')
    key_bytes = None
    iv_bytes = (0).to_bytes(16, 'big')
    if key:
        try:
            key_bytes = _fetch_bytes(key['uri'])
            if len(key_bytes) not in (16, 24, 32):
                key_bytes = None      # 密钥长度不对就放弃解密
            elif key.get('iv'):
                iv_bytes = bytes.fromhex(key['iv'])
        except Exception:  # noqa: BLE001
            key_bytes = None

    got = 0
    with open(out, 'wb') as fh:
        for u in urls:
            if got >= max_bytes:
                break
            try:
                data = _fetch_bytes(u, referer=u.rsplit('/', 1)[0] + '/')
            except Exception:  # noqa: BLE001
                continue
            if key_bytes and len(data) % 16 == 0:
                data = _decrypt(data, key_bytes, iv_bytes)
            fh.write(data)
            got += len(data)
    return out, got


def extract_frames(video, seconds, step, dest_dir, scale=1000):
    frames = []
    for i, t in enumerate(range(0, max(seconds, step), step)):
        png = os.path.join(dest_dir, 'f%03d_%d.png' % (i, t))
        try:
            subprocess.run([find_ffmpeg(), '-y', '-v', 'error', '-ss', str(t),
                            '-i', video, '-frames:v', '1',
                            '-vf', 'scale=%d:-1' % scale, png],
                           timeout=120, capture_output=True)
        except Exception:  # noqa: BLE001
            continue
        if os.path.exists(png) and os.path.getsize(png) > 1500:
            frames.append((t, png))
    return frames


def detect(video=None, m3u8=None, seconds=20, step=2, keep_temp=False):
    """主流程：只取前 N 秒 → 抽帧 → OCR → 判定 → 删除临时文件。"""
    tmp = tempfile.mkdtemp(prefix='addetect_')
    src_desc = video or m3u8
    try:
        if not video:
            segs, _covered, key = opening_segments(m3u8, seconds)
            video, size = download_segments(segs, tmp, key=key)
            if size < 10000:
                return {'ok': False, 'error': '开头分片下载失败（%d 字节）' % size,
                        'source': src_desc}
        frames = extract_frames(video, seconds, step, tmp)
        if not frames:
            return {'ok': False, 'error': '抽帧失败（分片可能加密未解或格式异常）',
                    'source': src_desc}
        texts = ocr_many([p for _t, p in frames])
        rows = []
        for (t, _png), text in zip(frames, texts):
            rows.append({'t': t, 'text': re.sub(r'\s+', '', text or '')[:80],
                         'hits': judge(text)})
        flagged = [r for r in rows if r['hits']]
        all_hits = list(dict.fromkeys([h for r in flagged for h in r['hits']]))
        return {'ok': True, 'source': src_desc,
                'scanned': len(frames),
                'with_text': len([r for r in rows if r['text']]),
                'flagged': len(flagged), 'hits': all_hits,
                'is_ad': len(flagged) > 0, 'rows': rows}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)[:100], 'source': src_desc}
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print('  临时文件保留在: %s' % tmp)


def resolve_source(sid, keyword):
    """走 API 拿到某源某番某集的 m3u8。"""
    import urllib.parse
    import urllib.request

    def post(path, payload):
        d = json.dumps(payload, ensure_ascii=False).encode()
        r = urllib.request.Request(API + path, data=d,
                                   headers={'Content-Type': 'application/json'},
                                   method='POST')
        return json.loads(urllib.request.urlopen(r, timeout=300).read())

    d = json.loads(urllib.request.urlopen(
        API + '/api/search?keyword=' + urllib.parse.quote(keyword)
        + '&mode=single&source_ids=' + str(sid), timeout=300).read())['data']
    g = d['groups'][0]
    if not g['results']:
        raise RuntimeError('该源没有匹配「%s」的内容' % keyword)
    item = g['results'][0]
    e = post('/api/episodes', {'source_id': sid, 'anime_url': item['url'],
                               'anime_name': item['name']})['data']
    eps = e['episodes']
    if not eps:
        raise RuntimeError('没有剧集')
    ep = eps[0]
    rv = post('/api/video/resolve', {'source_id': sid, 'episode_url': ep['url'],
                                     'anime_url': item['url'],
                                     'episode_name': ep['name']})
    url = (rv.get('data') or {}).get('url') or ''
    if '.m3u8' not in url.lower():
        raise RuntimeError('不是 m3u8 地址')
    return item['name'], ep['name'], url


def main():
    ap = argparse.ArgumentParser(description='文字广告检测（下载 20 秒→OCR→删除）')
    ap.add_argument('--video', help='本地视频文件')
    ap.add_argument('--source-id', type=int, help='按源 id 检测（需 --keyword）')
    ap.add_argument('--keyword', help='番剧关键词')
    ap.add_argument('--seconds', type=int, default=20, help='检测开头多少秒（默认 20）')
    ap.add_argument('--step', type=int, default=2, help='每隔几秒抽一帧（默认 2）')
    ap.add_argument('--keep-temp', action='store_true', help='保留临时文件')
    ap.add_argument('--check-ocr', action='store_true', help='只做 OCR 自检')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    if args.check_ocr:
        ok, why, engine = ocr_selfcheck()
        print(json.dumps({'ok': ok, 'engine': engine, 'reason': why},
                         ensure_ascii=False) if args.json
              else ('  OCR 可用（引擎：%s）' % engine if ok else '  OCR 不可用：%s' % why))
        return 0 if ok else 1

    if args.source_id:
        if not args.keyword:
            print('需要 --keyword')
            return 1
        try:
            anime, ep, m3u8 = resolve_source(args.source_id, args.keyword)
            print('  来源: %s / %s' % (anime, ep))
        except Exception as exc:  # noqa: BLE001
            print('  解析失败: %s' % exc)
            return 1
        result = detect(m3u8=m3u8, seconds=args.seconds, step=args.step,
                        keep_temp=args.keep_temp)
    elif args.video:
        if not os.path.exists(args.video):
            print('文件不存在: %s' % args.video)
            return 1
        result = detect(video=args.video, seconds=args.seconds, step=args.step,
                        keep_temp=args.keep_temp)
    else:
        ap.print_help()
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if not result.get('ok'):
        print('  检测失败: %s' % result.get('error'))
        return 1
    print('  扫描前 %d 秒，每 %d 秒一帧：%d 帧（%d 帧有文字）'
          % (args.seconds, args.step, result['scanned'], result['with_text']))
    print()
    for r in result['rows']:
        if not r['text']:
            continue
        print('  %s %3ds  %s' % ('⚠' if r['hits'] else ' ', r['t'], r['text'][:74]))
        if r['hits']:
            print('           命中: %s' % '、'.join(r['hits']))
    print()
    if result['is_ad']:
        print('  → 检测到广告文字（%d 帧命中）: %s'
              % (result['flagged'], '、'.join(result['hits'])))
    else:
        print('  → 未检测到广告文字')
    return 0


if __name__ == '__main__':
    sys.exit(main())
