# 动漫下载机 · Anime Downloader

[![CI](https://github.com/wangmou1406/MoAnime/actions/workflows/ci.yml/badge.svg)](https://github.com/wangmou1406/MoAnime/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-3.2.0-22c55e)](CHANGELOG.md)

Python + Flask 写的动漫下载软件，全部操作在网页里完成：检索、下载、整理、削字幕。
动漫源可以在页面上添加、拖拽排序、勾选启用，不用改代码。首次启动内置 13 个实测可用的源（11 个默认启用）。

> ⚠️ 本项目不提供、不托管、不分发任何影视内容。仅用于学习与研究，请遵守当地法律和站点条款。
> 详见 [免责声明](#免责声明)。

---

## 功能

| 功能 | 说明 |
| --- | --- |
| **多源聚合检索** | 内置源并发检索，结果按番剧分组；同一部番会给出来源、集数、质量分 |
| **下载** | HLS（m3u8 → 分片 → ffmpeg 转封装 mp4）与直链；支持暂停 / 继续 / 断点续传 / 失败重试 |
| **番剧库** | 按动漫折叠、逐集预览、在线播放、下载字幕、整部删除 |
| **整理** | 按命名规则（简洁 / Jellyfin / Emby / Plex / 飞牛）自动归档；按季整理可自定义季目录名 |
| **去重** | 同一部番同一集有多份时留体积最大的，其余删除 |
| **集数校验** | 对比 B 站官方集数，看漏了哪几集 |
| **字幕** | 按集号搜索并下载（assrt / OpenSubtitles） |
| **广告体检** | 时长比对 + 画面文字 OCR 两路检测，标记插广告的源并降权 |
| **速度面板** | 60 秒速度折线图，每行任务显示剩余时间 |
| **自定义外观** | 深浅主题、配色、背景、标题、动效开关 |

六个页面：检索 / 动漫源 / 下载任务 / 番剧库 / 设置 / 系统日志。

## 快速开始

三个版本共用 `source/` 里同一份代码，只是启动方式不同。

### Python 版（Windows / Linux）

```powershell
# Windows：双击
python\启动.bat

# Linux / macOS
cd python && ./启动.sh
```

首次运行会自动创建 `.venv` 并装依赖，然后浏览器打开 <http://127.0.0.1:8848>。
需要有 Python ≥ 3.10；Linux 上另需系统包 `ffmpeg` 与中文字体（`fonts-noto-cjk`）。

想让它在后台常驻（关掉窗口也继续跑）：`python\后台启动.ps1`。

### Docker

镜像内自带 ffmpeg + Chromium + Node，宿主机不用装任何依赖。

```bash
# 直接用现成镜像
docker run -d --name anime-downloader -p 8848:8848 \
  -v /volume1/docker/anime-data:/data \
  -v /volume1/video/anime:/downloads \
  --shm-size 1gb --restart unless-stopped \
  ghcr.io/wangmou1406/moanime:3.2.0
```

或者用 compose，把两个路径改成你自己的：

```yaml
services:
  anime-downloader:
    image: ghcr.io/wangmou1406/moanime:3.2.0
    container_name: anime-downloader
    restart: unless-stopped
    ports:
      - "8848:8848"             # 左边被占用就改成 18848:8848
    volumes:
      - ./anime-data:/data       # 数据库 / 设置 / 日志
      - ./anime:/downloads       # 视频与字幕；NAS 写 /volume1/video/anime，Windows 写 D:/Anime
    environment:
      AD_DOWNLOAD_DIR: /downloads
      AD_CONCURRENT_TASKS: "3"   # 同时下载的剧集数
    shm_size: "1gb"              # Chromium 需要足够的 /dev/shm
```

```bash
docker compose up -d
```

从源码构建用仓库里那份，构建上下文和镜像加速参数都写好了：

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

离线环境用 [Releases](https://github.com/wangmou1406/MoAnime/releases) 里的镜像归档 `docker load -i`。

### Windows 桌面版

下载 `MoAnime-3.2.0-Setup.exe` 安装即可。界面用系统自带的 WebView2 渲染，
不打包浏览器内核；Python 后端已打包进去，不需要另装 Python。

## 内置动漫源

首次启动写入 13 个 MacCMS 资源站接口源，其中 11 个默认启用、实测能直接给出 m3u8；
另外 2 个（极速资源、红牛资源）首集是分享页，程序会进页面再解析，默认不启用。
这些接口的 `vod_play_url` 字段里就带每一集的播放地址，所以「检索 → 剧集 → 下载」全程不需要逆向。

接口失效时在「动漫源」页取消勾选即可，不影响其它源。
删掉数据库重启会自动补齐内置源；源配置可以整体导出成 JSON 备份或分享。

想接**网页型**站点（不是资源接口），用「添加源 → 通用可配置源」或「自动探测」。

## 目录结构

```
MoAnime/
├── source/     共享源码（唯一真源，三个版本共用）
│   ├── run.py             启动入口
│   ├── core/              配置 / 数据库 / 下载引擎 / 队列 / 命名 / 字幕
│   ├── sources/           源插件（maccms / generic / jsonapi / jsonfeed …）
│   ├── web/               Flask 路由（按职责分文件）
│   ├── static/ templates/ 前端（Bootstrap、hls.js 均已本地化）
│   └── tools/             自检脚本、ffmpeg.exe
├── python/     Python 版运行根：启动脚本、.venv、data、logs、downloads
├── docker/     Dockerfile、compose、镜像归档
└── desktop/    WebView2 宿主工程、PyInstaller spec、NSIS 安装包脚本
```

**改代码只需要动 `source/`。**

## 数据在哪

| 内容 | 位置 |
| --- | --- |
| 源、任务、下载历史 | `python/data/anime-downloader.db` |
| 设置 | `python/data/settings.json` |
| 日志 | `python/logs/app.log` |
| 下载的视频 | 界面「设置 → 下载位置」里配置，默认 `python/downloads` |

删掉数据库会重置所有源与任务记录。

## 常见问题

**端口 8848 被占用？**
在「设置 → 界面与服务」改端口，或启动时加 `--port 9000`。

**某个源搜不到东西？**
到「动漫源 → 测试」看报告：MacCMS 源会逐个接口报条目数和是否直链，能直接看出是接口挂了还是该条目没资源。

**任务报「无法解析视频地址」？**
程序会自动重试 3 次。仍失败时看错误详情：是源站限流（稍后重试或换源）、源站数据缺失（换源），
还是任务缺少源信息（在检索结果里重新点一次下载）。

**下载下来的文件是 .ts 而不是 .mp4？**
没装 ffmpeg。装好后在「设置 → 外部依赖」里指定路径，或让程序自动下载。

**想让局域网里的其他设备访问？**
在「设置」里把监听地址改成 `0.0.0.0`，然后用 `http://本机IP:8848` 打开。

## 开发与自检

```bash
python source/tools/test_urls.py    # URL 拼装单测
python source/tools/test_xml.py     # XML / JSON 解析单测
python source/tools/selftest.py     # 端到端自检（起临时服务，47 项）
node   source/tools/check_frontend.cjs   # 前端脚本加载检查
```

CI 会在 Ubuntu 与 Windows 上跑以上全部，并检查没有把构建产物提交进仓库。
加新源请看 [CONTRIBUTING.md](.github/CONTRIBUTING.md)。

## 许可证

[MIT License](LICENSE) © MoAnime contributors

随程序附带的第三方组件各自遵循其许可：Bootstrap、bootstrap-icons、hls.js、SortableJS（前端库）、
FFmpeg（LGPL/GPL，按构建而定）、Chromium（BSD 系）、.NET（MIT）、WebView2（微软许可）。

## 免责声明

本项目是本地下载与整理工具，本身：

* 不提供、不存储、不分发任何视频、字幕或图片内容；
* 不做转码意义上的“破解”，也不绕过付费墙或 DRM；
* 视频全部来自你自行配置或内置的第三方站点，其合法性与可用性由那些站点负责；
* 与任何资源站、动画公司、发行方没有任何关联，也未获其授权或认可。

使用时请自行确认你所在地区关于下载/缓存流媒体的规定、你访问的站点条款是否允许，
以及下载的内容只用于个人观看、不要二次分发。

本项目按“现状”提供，不对任何直接或间接损失负责。如果你不接受以上条款，请不要使用。
