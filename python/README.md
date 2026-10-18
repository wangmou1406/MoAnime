# Python 版

直接在本机跑 Python，**不使用 Docker、不打包成 exe**。适合想改代码、想看日志，
或者机器上已经装好 Python 的人。

**Windows ✅ · Linux ✅ · macOS ⚠️（未实测）**

## 目录里有什么

| 文件 | 作用 | 平台 |
| --- | --- | --- |
| `启动.bat` | **Windows 推荐入口**：双击即可（首次自动建 `.venv` 装依赖） | Windows |
| `launcher.ps1` | 上面的实际逻辑 | Windows |
| `后台启动.ps1` | 后台常驻，关窗口也继续跑 | Windows |
| `停止.ps1` | 按 `settings.json` 里的端口找进程并结束 | Windows |
| `启动.sh` | **Linux / macOS 入口**，和 `启动.bat` 等价 | Linux / macOS |
| `停止.sh` | 按端口停止 | Linux / macOS |
| `build-portable.ps1` | 打便携版 zip | Windows |

启动脚本都用「脚本目录的上一级」定位项目根，所以**必须留在这个目录里**
（`.venv`、`run.py`、`data/`、`downloads/` 都在上一级）。

## Windows

```powershell
# 方式 1：双击（推荐）
python\启动.bat

# 方式 2：后台常驻
powershell -ExecutionPolicy Bypass -File python\后台启动.ps1
powershell -ExecutionPolicy Bypass -File python\停止.ps1

# 方式 3：直接用解释器
.venv\Scripts\python.exe ..\source\run.py
.venv\Scripts\python.exe ..\source\run.py --port 9000 --no-browser
```

## Linux / macOS

```bash
chmod +x python/*.sh          # 首次给执行权限

# 方式 1：前台（推荐先这么试）
./python/启动.sh

# 指定端口 / 允许局域网访问
./python/启动.sh --port 9000
./python/启动.sh --host 0.0.0.0

# 方式 2：后台
nohup ./python/启动.sh > /dev/null 2>&1 &
./python/停止.sh

# 方式 3：直接用解释器（已经建好 venv 时）
.venv/bin/python run.py
```

### Linux 上的两个系统依赖（不是 pip 包）

```bash
# Debian / Ubuntu（含 WSL）
sudo apt install python3 python3-venv python3-pip ffmpeg fonts-noto-cjk

# Fedora
sudo dnf install python3 python3-pip ffmpeg google-noto-sans-cjk-fonts

# macOS
brew install python@3.12 ffmpeg
```

| 缺什么 | 后果 |
| --- | --- |
| **ffmpeg** | m3u8 → mp4 退化成 `.ts` 分段合并；界面会提示 |
| **中文字体** | 文字广告体检报「没找到可用字体，无法生成测试图」（不会静默判成"没广告"） |
| `python3-venv` | 建 `.venv` 直接失败（Debian 默认不装） |

### 实测（`python:3.12-slim` 容器）

```
Python 3.12.15 / Linux
pip install -r requirements.txt → 完成
python run.py --port 8899      → 2 秒就绪
GET /                          → HTTP 200，47795 bytes
GET /api/sources               → 13 个源（11 启用）
检索「示例」                    → 命中 20 条 → 解析 1180 集
下载 示例番 第01集              → 进度 0 → 10.6%（60 秒）
分片                           → /app/downloads/.tmp/示例番_第01集/seg_*.ts
清晰度                         → 1080x608
```

## 下载位置

| 内容 | 位置 |
| --- | --- |
| 下载的视频 | `<项目根>/downloads/` |
| 临时分片 | `<下载目录>/.tmp/`（跟随下载目录，只改一处） |
| 数据库 / 设置 / 日志 | `<项目根>/data/` |

改下载位置：界面【设置 → 下载位置】，或设环境变量 `AD_DOWNLOAD_DIR`。
**不要单独设 `AD_TEMP_DIR`** —— 那会让临时目录不再跟随下载目录。

## 浏览器取流在 Linux 上的限制

`data/_pwt` 里那份内核只有 **Windows 版**（chrome-win64 + node.exe）。
所以在 Linux 上跑 `.py` 版时，「浏览器取流」会自动退化成纯 HTTP 取流 ——
能检索、能下载，但对「视频地址由 JS 动态生成」的站点会失败。

需要完整能力就用 **Docker 版**（镜像里自带 Linux 版 Chromium + Node）。

## 和另外两版的区别

| | Python 版 | Docker 版 | 桌面版 |
| --- | --- | --- | --- |
| 平台 | Windows + Linux | Linux | 仅 Windows |
| 需要装 Python | 是 | 否 | 否 |
| 需要自己开浏览器 | 是 | 是 | 否（自带窗口） |
| 浏览器取流 | Windows ✅ / Linux ❌ | ✅ | ✅ |
| 适合 | 改代码 / 调试 | NAS / 服务器 | 普通用户 |

---

## 平台与下载位置

**支持：Windows ✅ · Linux ✅ · macOS ⚠️（未实测）**

| 内容 | 位置 | 怎么改 |
| --- | --- | --- |
| 下载的视频 | `<项目根>/downloads/` | 界面【设置 → 下载位置】或 `AD_DOWNLOAD_DIR` |
| 临时分片 | `<下载目录>/.tmp/` | 跟随下载目录，别单独设 `AD_TEMP_DIR` |
| 数据（库/设置/日志） | `<项目根>/data/` | 环境变量 `AD_DATA_DIR` |

Linux 实测（`python:3.12-slim` 容器）：2 秒就绪、首页 HTTP 200、13 个源、
检索解析 1180 集、下载进度 0 → 10.6%、分片落在 `/app/downloads/.tmp/`。
