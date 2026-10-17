# Python 版

直接在本机跑 Python，**不使用 Docker、不打包成 exe**。适合想改代码、想看日志、
或者机器上已经装好 Python 的人。

## 目录里有什么

| 文件 | 作用 |
| --- | --- |
| `启动.bat` | **推荐入口**：双击即可（首次会自动建 `.venv` 并装依赖） |
| `launcher.ps1` | 上面的实际逻辑：找 Python → 建虚拟环境 → 装依赖 → 启动 |
| `后台启动.ps1` | 后台常驻，关掉窗口也继续跑 |
| `停止.ps1` | 按 `settings.json` 里的端口找到进程并结束 |

这些脚本会用 `$ProjectRoot = Split-Path -Parent $PSScriptRoot` 定位项目根，
所以**必须保持在本目录**（脚本在 `python/`，`.venv`、`run.py`、`data/` 都在上一级）。

## 快速开始

```powershell
# 方式 1：双击（推荐）
python\启动.bat

# 方式 2：后台常驻
powershell -ExecutionPolicy Bypass -File python\后台启动.ps1
powershell -ExecutionPolicy Bypass -File python\停止.ps1

# 方式 3：直接用虚拟环境的解释器
.venv\Scripts\python.exe run.py
.venv\Scripts\python.exe run.py --port 9000 --no-browser
.venv\Scripts\python.exe run.py --host 0.0.0.0        # 局域网可访问
```

启动后打开 <http://127.0.0.1:8848>。

## 首次运行做了什么

1. 找可用的 Python（≥3.10）：优先项目内 `.venv` → `py` 启动器 → `PATH` → 常见安装位置
2. 没有 `.venv` 就创建，并按 `requirements.txt` 装依赖
3. 提示 ffmpeg 是否就位（没有的话可在界面「设置 → 外部依赖」一键下载）
4. 启动服务

## 依赖

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

关键运行时依赖：

| 依赖 | 用途 |
| --- | --- |
| `Flask` / `Flask-SocketIO` | Web 服务与实时进度推送 |
| `requests` / `beautifulsoup4` / `lxml` | 抓取与解析资源站 |
| `m3u8` / `pycryptodome` | HLS 分片与 AES-128 解密 |
| `rapidocr-onnxruntime` | 文字广告体检的画面 OCR（会带入 onnxruntime / opencv / numpy） |

`ffmpeg` 不在 pip 里：放 `tools/ffmpeg.exe` 或装在系统 PATH 即自动启用。

## 和另外两版的区别

| | Python 版 | Docker 版 | 桌面版 |
| --- | --- | --- | --- |
| 需要装 Python | 是（或用它自带的 `.venv`） | 否 | 否 |
| 需要自己开浏览器 | 是 | 是 | **否**（自带应用窗口） |
| 数据位置 | 项目 `data/` | 挂卷 `/data` | `%LOCALAPPDATA%\AnimeDownloader\data` |
| 适合 | 改代码 / 调试 | NAS / 服务器 | 普通用户 |
