# Docker 版 · 交付包

本目录是一个**完整可分发**的 Docker 版，不需要看别的地方。

## 里面有什么

| 文件 | 作用 |
| --- | --- |
| `anime-downloader-3.2.0.tar` | **镜像归档**（`docker save` 产物，约 677 MB） |
| `anime-downloader-3.2.0.tar.sha256` | 校验值 |
| `Dockerfile` | 想自己构建时用（上下文是项目根） |
| `docker-compose.yml` | 编排文件（改下载路径就在这里） |
| `.env.example` | compose 变量模板 |

## 用法 A：直接用镜像（推荐，不用构建）

```bash
docker load -i anime-downloader-3.2.0.tar

docker run -d --name anime-downloader \
  -p 8848:8848 \
  -v /volume1/docker/anime-data:/data \
  -v /volume1/video/anime:/downloads \
  --shm-size 1gb \
  --restart unless-stopped \
  anime-downloader:latest
```

打开 <http://localhost:8848>。

**Windows 路径写法**：

```powershell
docker run -d --name anime-downloader -p 8848:8848 `
  -v "D:/Anime/data:/data" -v "D:/Anime/videos:/downloads" `
  --shm-size 1gb --restart unless-stopped anime-downloader:latest
```

## 用法 B：用 compose（要改路径时）

```bash
cp .env.example .env
# 编辑 .env：AD_DOWNLOAD_HOST=/volume1/video/anime
docker compose up -d
```

> `docker-compose.yml` 里的构建上下文写的是 `../source`，也就是**代码目录**。
> 如果这个交付包被单独拷走（没有源码），只能用下面的「用法 C」。

## 用法 C：从源码构建

构建上下文是仓库的 `source/` 目录（不是项目根）：

```bash
docker build -t anime-downloader:latest -f docker/Dockerfile source
```

**国内加速**（拉 Chromium 用，约 36 秒 vs 50 分钟）：

```bash
docker compose build --build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
```

## 校验镜像归档

```bash
sha256sum -c anime-downloader-3.2.0.tar.sha256     # Linux / NAS
Get-FileHash anime-downloader-3.2.0.tar -Algorithm SHA256   # Windows
```

## 注意

* 镜像内已自带 **ffmpeg 7.1.5 + Chromium 156 + Node 22**，宿主机不需要装任何东西。
* 默认端口 **8848**；和本地 Python 版同时跑时要改，见 `.env.example` 里的 `AD_HOST_PORT`。
* `--shm-size 1gb`：Chromium 在容器里需要足够的 `/dev/shm`，不然可能启动失败。
* 只测过 **linux/amd64**；ARM（如树莓派、M 系列 Mac）需要另行验证。

---

## 平台与下载位置

**支持：Linux**（镜像 linux/amd64）

| 内容 | 容器内 | 宿主机（你挂的） |
| --- | --- | --- |
| 下载的视频 | `/downloads/` | `-v 你的路径:/downloads` |
| 临时分片 | `/downloads/.tmp/` | 同上（自动跟随） |
| 数据 | `/data/` | `-v 你的路径:/data` |
