# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（桌面版）。

产物是 **onedir**（一个文件夹 + exe），不是 onefile：
  * 本项目要带 ffmpeg(161MB) 和浏览器内核(539MB)，onefile 每次启动都要把
    700MB 解压到临时目录，启动要几十秒；onedir 直接跑，秒开。
  * onedir 便于 NSIS 做增量安装、也便于用户看日志。

打包结果：dist/desktop/动漫下载机/动漫下载机.exe
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# SPECPATH 是 PyInstaller 注入的变量：本 spec 所在目录（desktop/）
DESKTOP_DIR = SPECPATH
BASE_DIR = os.path.abspath(os.path.join(DESKTOP_DIR, '..'))
# 代码在 source/，运行期资产（内核）在 python/data/ —— 两处都不在 desktop/ 下
SOURCE_ROOT = os.path.join(BASE_DIR, 'source')
PYTHON_ROOT = os.path.join(BASE_DIR, 'python')

datas = [
    (os.path.join(SOURCE_ROOT, 'static'), 'static'),
    (os.path.join(SOURCE_ROOT, 'templates'), 'templates'),
]

# tools/ 里有运行时真正要用的东西：
#   player_sniffer.js  浏览器取流的 Node 脚本
#   win_ocr.ps1        Windows OCR 兜底
#   ad_detect.py       文字广告体检（被 importlib 动态加载）
#   ffmpeg.exe         HLS 转 mp4
tools_src = os.path.join(SOURCE_ROOT, 'tools')
if os.path.isdir(tools_src):
    for name in os.listdir(tools_src):
        if name == '__pycache__':
            continue
        full = os.path.join(tools_src, name)
        if os.path.isfile(full):
            datas.append((full, 'tools'))

# 内置浏览器内核 + Node（可选：目录不在就跳过，功能会退化成纯 HTTP 取流）
pwt = os.path.join(PYTHON_ROOT, 'data', '_pwt')
if os.path.isdir(pwt):
    datas.append((pwt, os.path.join('data', '_pwt')))

# RapidOCR 自带 ONNX 模型，必须作为数据文件带上，否则运行时报找不到模型
datas += collect_data_files('rapidocr_onnxruntime')
# onnxruntime / opencv 的原生 DLL
binaries = collect_dynamic_libs('onnxruntime') + collect_dynamic_libs('cv2')

hiddenimports = [
    'web.routes',
    'web.routes._base', 'web.routes.meta', 'web.routes.search',
    'web.routes.downloads', 'web.routes.appearance', 'web.routes.sources',
    'web.routes.settings', 'web.routes.library', 'web.routes.ads',
    'web.routes.system',
    'flask_socketio', 'socketio', 'engineio', 'simple_websocket',
    # engineio 的异步后端是**运行时按名字动态导入**的，PyInstaller 静态分析
    # 看不到。漏了就会在 create_app() 里抛
    #   ValueError: Invalid async_mode specified
    # 本项目用的是 threading 模式，必须显式带上这些模块。
    'engineio.async_drivers',
    'engineio.async_drivers.threading',
    'engineio.async_drivers._websocket_wsgi',
    'm3u8', 'bs4', 'lxml', 'lxml.etree', 'Crypto', 'Crypto.Cipher.AES',
    'rapidocr_onnxruntime', 'onnxruntime', 'cv2', 'shapely', 'pyclipper',
    'yaml', 'PIL', 'numpy',
    'sources.maccms', 'sources.generic', 'sources.jsonapi',
    'sources.jsonfeed', 'sources.anime1', 'sources.twok',
    'sources.manager', 'sources.auto_detect', 'sources.seed_sources',
    'core.subtitle', 'core.browser', 'core.browser_resolver',
]

a = Analysis(
    [os.path.join(DESKTOP_DIR, 'desktop_main.py')],
    pathex=[SOURCE_ROOT, DESKTOP_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 明确排除用不到的大件，免得把体积撑爆
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'pandas', 'pytest',
        'IPython', 'notebook', 'PyQt5', 'PySide2', 'PySide6',
        'webview', 'pythonnet', 'clr_loader',  # 桌面版不用 pywebview（见 desktop_main 说明）
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='动漫下载机',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 压缩 700MB 数据会非常慢，且容易误伤原生 DLL
    console=False,             # 双击不弹黑框；日志写在数据目录里
    icon=os.path.join(DESKTOP_DIR, 'app.ico')
    if os.path.exists(os.path.join(DESKTOP_DIR, 'app.ico')) else None,
    version=os.path.join(DESKTOP_DIR, 'version_info.txt')
    if os.path.exists(os.path.join(DESKTOP_DIR, 'version_info.txt')) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='动漫下载机',
)
