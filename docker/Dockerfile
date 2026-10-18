# ============================================================
#  动漫下载机 Anime Downloader —— Docker 镜像
#
#  镜像内**自带**两样东西，容器起来即可用，不需要宿主机提供：
#    * ffmpeg   —— apt 装的 Linux 版，HLS 分片转封装 mp4
#    * Chromium —— Playwright 的 Linux 构建，供「浏览器取流」抓真实视频地址
#      （连同 Node.js 运行时一起打进 /opt/pwt，data/ 是挂载卷不能放内核）
#
#  构建：
#    docker compose up -d --build
#    # 或
#    docker build -t anime-downloader:latest .
#
#  运行后访问 http://localhost:8848
# ============================================================
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Anime Downloader" \
      org.opencontainers.image.description="动漫下载机：多源聚合检索 + m3u8 转封装 mp4 + 字幕下载 + 广告体检（内置 ffmpeg 与 Chromium）" \
      org.opencontainers.image.source="https://github.com/196632094/XiaoMoAnime"

ARG NODE_VERSION=22.11.0
ARG PLAYWRIGHT_VERSION=1.64.1

# 国内加速（均可选，默认走官方源；构建时用 --build-arg 覆盖）：
#   APT_MIRROR          apt 镜像域名（不含协议、不含 /debian 路径），如 mirrors.ustc.edu.cn
#                       （自动把 deb.debian.org 换成 <域名>/debian、security.debian.org 换成 <域名>/debian-security）
#   PIP_INDEX_URL       pip 源，如 https://pypi.tuna.tsinghua.edu.cn/simple
#   NODE_DOWNLOAD_HOST  nodejs.org 的镜像，如 https://cdn.npmmirror.com/binaries/node
ARG APT_MIRROR=
ARG PIP_INDEX_URL=
ARG NODE_DOWNLOAD_HOST=

# Chromium 下载源。默认走 Playwright 官方 CDN；
# 国内网络慢的话可以换成镜像（目录结构与官方一致）：
#   docker build --build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright .
#   docker compose build --build-arg PLAYWRIGHT_DOWNLOAD_HOST=...
ARG PLAYWRIGHT_DOWNLOAD_HOST=

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8

# ---------------------------------------------------------------- 系统依赖
# ffmpeg             : HLS 分片转封装 mp4（核心依赖，打进镜像）
# p7zip-full         : 解压 assrt 的整季 rar 字幕包
# curl / ca-certs    : 下载 Node、健康检查、HTTPS
# xz-utils           : 解压 node 的 .tar.xz
# libgomp1           : onnxruntime（文字广告体检的 OCR）依赖
# libglib2.0-0/libgl1: opencv 依赖
# fonts-noto-cjk     : 浏览器渲染中文（缺字体页面会显示成方块，影响取流判断）
# 其余 libnss3 等    : Chromium 的运行库
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}/debian-security|g" \
        /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      p7zip-full \
      curl \
      ca-certificates \
      xz-utils \
      libgomp1 \
      libglib2.0-0 \
      libgl1 \
      fonts-noto-cjk \
      fonts-liberation \
      libnss3 \
      libatk1.0-0 \
      libatk-bridge2.0-0 \
      libcups2 \
      libdrm2 \
      libxkbcommon0 \
      libxcomposite1 \
      libxdamage1 \
      libxfixes3 \
      libxrandr2 \
      libgbm1 \
      libpango-1.0-0 \
      libcairo2 \
      libasound2 \
      libatspi2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------- 内置内核
# 装在 /opt/pwt，**不能**放 /app/data：
#   data/ 在容器里是挂载卷（docker-compose 会把宿主目录挂上来），
#   放进去的内核会被卷遮住，程序就找不到浏览器了。
# 目录结构与本地版一致（browsers/ + node/），
# core/browser_resolver.py 通过 AD_BUNDLED_DIR 指过来即可直接识别。
ENV AD_BUNDLED_DIR=/opt/pwt \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pwt/browsers

# Node.js 运行时（官方 Linux x64 压缩包，解出来是 node/bin/node）
RUN mkdir -p /opt/pwt/node \
 && curl -fsSL "${NODE_DOWNLOAD_HOST:-https://nodejs.org/dist}/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    | tar -xJ --strip-components=1 -C /opt/pwt/node \
 && /opt/pwt/node/bin/node --version

ENV PATH=/opt/pwt/node/bin:$PATH \
    AD_PLAYWRIGHT_DIR=/opt/pwt/node_modules/playwright-core

# playwright-core + Chromium（--no-shell：只装完整版内核，不额外装 headless shell）
# 注意：ARG 声明的 PLAYWRIGHT_DOWNLOAD_HOST 会自动作为环境变量进入 RUN，
# 非空时 playwright 就改从镜像下载（省得再写 export）。
RUN set -eux; \
    cd /opt/pwt; \
    /opt/pwt/node/bin/npm init -y >/dev/null 2>&1; \
    /opt/pwt/node/bin/npm install --no-audit --no-fund "playwright-core@${PLAYWRIGHT_VERSION}"; \
    /opt/pwt/node/bin/node /opt/pwt/node_modules/playwright-core/cli.js install --no-shell chromium; \
    rm -rf /root/.npm /tmp/playwright*; \
    rm -rf /opt/pwt/browsers/ffmpeg-*; \
    ls -d /opt/pwt/browsers/chromium-* >/dev/null; \
    /opt/pwt/browsers/chromium-*/chrome-linux*/chrome --version

# ---------------------------------------------------------------- Python 依赖
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r /app/requirements.txt; \
    else \
      pip install --no-cache-dir -r /app/requirements.txt; \
    fi

# ---------------------------------------------------------------- 应用代码
# 注意 .dockerignore 已排除 data/ downloads/ logs/ .venv/ 与 tools/ffmpeg.exe，
# 所以构建上下文只有源码，不会把宿主机上的 Windows 内核（500MB+）带进来。
COPY . /app

# 兜底：万一有人去掉 .dockerignore 里的排除项，也别把 Windows 专用二进制带进镜像
RUN rm -f /app/tools/ffmpeg.exe \
 && rm -rf /app/data/_pwt /app/.venv

# ---------------------------------------------------------------- 运行用户
# 不用 root 跑下载。Chromium 启动时 player_sniffer.js 已带 --no-sandbox。
RUN useradd -m -u 1000 -s /bin/bash anime \
 && mkdir -p /data /downloads /tmp/sniffer \
 && chown -R anime:anime /app /data /downloads /tmp/sniffer \
 && chmod -R a+rX /opt/pwt
USER anime

# ---------------------------------------------------------------- 目录与端口
# /data       : 数据库、设置、日志、缩略图缓存
# /downloads  : 下载好的视频与字幕（可用 AD_DOWNLOAD_DIR 改容器内路径）
# /opt/pwt    : 内置 Node + Chromium（只读即可）
#
# 注意 **不要在这里设 AD_TEMP_DIR**：
# 程序在「只给了 AD_DOWNLOAD_DIR」时会自动把临时目录算成 <下载目录>/.tmp。
# 一旦这里写死 AD_TEMP_DIR，那条自动跟随逻辑就被跳过 ——
# 于是自定义下载目录时临时分片仍落在 /downloads/.tmp（可能根本没挂载），
# 下载会失败。实测踩过。
ENV AD_DATA_DIR=/data \
    AD_LOG_DIR=/data/logs \
    AD_DOWNLOAD_DIR=/downloads \
    AD_SNIFFER_TMP=/tmp/sniffer \
    AD_HOST=0.0.0.0 \
    AD_PORT=8848

VOLUME ["/data", "/downloads"]
EXPOSE 8848

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8848/api/meta > /dev/null || exit 1

# 容器内必须监听 0.0.0.0 才能被外部访问
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8848", "--no-browser"]
