# Docker 版

镜像内**自带 ffmpeg 与 Chromium 内核**，宿主机不需要装任何依赖。

## 目录里有什么

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 镜像构建脚本：装 ffmpeg、Node、Playwright 的 Linux Chromium |
| `docker-compose.yml` | 编排：端口、挂载、环境变量（**路径都在这里改**） |
| `.env.example` | compose 变量模板，复制成 `.env` 用 |

> `.dockerignore` 在**项目根**（不是这里）。Docker 只认构建上下文根目录下的
> `.dockerignore`，而上下文必须是项目根（那里才有 `core/ web/ static/`）。

## 快速开始

```bash
cd docker

# 一条命令构建并启动
docker compose up -d --build

# 打开 http://localhost:8848
```

**国内加速**（拉 Chromium 用，约 36 秒 vs 50 分钟）：

```bash
# 在 docker-compose.yml 里取消注释：
#   PLAYWRIGHT_DOWNLOAD_HOST: "https://cdn.npmmirror.com/binaries/playwright"
docker compose up -d --build
```

## 改下载路径

```bash
cp .env.example .env
# 编辑 .env：
#   AD_DOWNLOAD_HOST=/volume1/video/anime     ← NAS
#   AD_DOWNLOAD_HOST=D:/Anime                 ← Windows
docker compose up -d          # 改完必须重建容器
```

只想改容器内路径时用 `AD_IN_DOWNLOAD`。临时目录会自动跟随下载目录
（`<下载目录>/.tmp`），**不要**再单独设 `AD_TEMP_DIR`。

## 挂载

| 宿主机默认 | 容器内 | 内容 |
| --- | --- | --- |
| `../docker-data` | `/data` | 数据库、设置、日志、缩略图缓存 |
| `../downloads` | `/downloads` | 视频与字幕 |

> 相对路径是相对**本目录**（`docker/`）解析的，所以默认值写成 `../downloads`。

## ⚠️ 端口冲突

容器默认绑 **8848**。如果本地 Python 版也在跑，两个都会"启动成功"但请求会串。
同时使用时给容器换端口：

```bash
echo "AD_HOST_PORT=18848" >> .env
docker compose up -d
```

## 常用命令

```bash
docker compose config        # 看展开后的最终配置（改完路径先跑这个）
docker compose up -d --build # 构建 + 启动/更新
docker compose up -d         # 只重建容器（改 .env 后用）
docker compose logs -f       # 跟日志
docker compose ps            # 状态（含 healthy）
docker compose down          # 停止（挂卷数据不动）
```

## 离线分发

不想让对方自己构建，可以直接给镜像归档（在项目 `dist/` 下）：

```bash
docker load -i anime-downloader-3.1.0.tar
docker run -d --name anime -p 8848:8848 \
  -v /volume1/docker/anime-data:/data \
  -v /volume1/video/anime:/downloads \
  --shm-size 1gb --restart unless-stopped \
  anime-downloader:latest
```
