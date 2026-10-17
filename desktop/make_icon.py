# -*- coding: utf-8 -*-
"""生成桌面版图标 desktop/app.ico。

设计：圆角方块 + 蓝紫渐变 + 白色下载箭头（下方托盘）。
选这个造型的理由：
  * 下载箭头是最通用、在 16px 下也能认出来的形状；
  * 渐变方块在小尺寸托盘图标里比细线条更醒目；
  * 不塞汉字 —— 16px 下中文会糊成一团。

用 4 倍超采样绘制再缩小，边缘才不毛糙。
"""
import os
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, 'desktop', 'app.ico')

SS = 4                # 超采样倍数
SIZE = 256            # 目标最大尺寸
S = SIZE * SS

TOP = (91, 124, 250)      # #5B7CFA 蓝
BOTTOM = (124, 77, 255)   # #7C4DFF 紫
WHITE = (255, 255, 255)


def rounded_mask(size, radius):
    """圆角矩形遮罩（灰度图，用于裁切渐变）。"""
    mask = Image.new('L', (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def gradient(size, top, bottom):
    """竖直渐变。"""
    img = Image.new('RGB', (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=color)
    return img


def build():
    # 背景：渐变 + 圆角
    bg = gradient(S, TOP, BOTTOM).convert('RGBA')
    bg.putalpha(rounded_mask(S, int(S * 0.22)))

    # 前景：白色下载箭头 + 托盘
    fg = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)

    cx = S // 2
    stem_w = int(S * 0.115)
    stem_top = int(S * 0.235)
    stem_bot = int(S * 0.505)

    # 竖杆（两端圆角）
    d.rounded_rectangle(
        [cx - stem_w // 2, stem_top, cx + stem_w // 2, stem_bot],
        radius=stem_w // 2, fill=WHITE)

    # 箭头（等腰三角形，顶点朝下）
    half = int(S * 0.175)
    tip_y = int(S * 0.685)
    base_y = stem_bot - int(S * 0.012)
    d.polygon([(cx - half, base_y), (cx + half, base_y), (cx, tip_y)], fill=WHITE)

    # 托盘（底部横条，两端圆角）
    tray_w = int(S * 0.40)
    tray_h = int(S * 0.088)
    tray_y = int(S * 0.745)
    d.rounded_rectangle(
        [cx - tray_w // 2, tray_y, cx + tray_w // 2, tray_y + tray_h],
        radius=tray_h // 2, fill=WHITE)

    icon = Image.alpha_composite(bg, fg)
    icon = icon.resize((SIZE, SIZE), Image.LANCZOS)
    return icon


def main():
    icon = build()
    app_dir = os.path.dirname(OUT)
    os.makedirs(app_dir, exist_ok=True)

    # 先存一张 PNG 便于目视检查
    png = os.path.join(app_dir, 'app-icon-preview.png')
    icon.save(png, 'PNG')

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.save(OUT, format='ICO', sizes=sizes)
    print('  ✓ %s  (%d 字节)' % (OUT, os.path.getsize(OUT)))
    print('    内含尺寸: %s' % ', '.join('%dx%d' % s for s in sizes))
    print('    预览图: %s' % png)


if __name__ == '__main__':
    main()
