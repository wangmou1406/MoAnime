# Python 便携版 · 交付包

本目录是一个**完整可分发**的 Python 版，不需要看别的地方。

## 里面有什么

| 文件 | 作用 |
| --- | --- |
| `动漫下载机-python.zip` | **便携包**（约 298 MB）：解压即用 |
| `动漫下载机-python/` | 上面 zip 的展开版（想直接跑这个目录也行） |
| `README.md` | 本文件 |

## 怎么用

1. 解压 `动漫下载机-python.zip` 到任意目录（路径里有中文/空格也可以）
2. 双击里面的 **`启动.bat`**
3. 首次运行会自己建 `.venv` 并装依赖（需要联网，大约 2~5 分钟）
4. 装好后自动启动，浏览器打开 <http://127.0.0.1:8848>

## 前置条件

**目标机器需要 Python ≥ 3.10。**

脚本会依次找：项目内 `.venv` → `py` 启动器 → `PATH` 里的 `python` → 常见安装位置。
找不到会给出明确提示。

> 为什么要现场装依赖而不是把 `.venv` 打包进去：
> 虚拟环境**不是可重定位的** —— `pyvenv.cfg` 和 `Scripts\*.exe` 里写死了绝对路径，
> 换个目录或换台机器基本都会坏。所以便携版只带源码 + 启动脚本。

## 里面已经带了什么

| 内容 | 说明 |
| --- | --- |
| 完整源码 | `core/ web/ sources/ static/ templates/ tools/` |
| **ffmpeg.exe** | 161 MB，在 `tools/` 下，HLS 转 mp4 用 |
| **浏览器内核** | 539 MB，在 `data/_pwt/` 下，「浏览器取流」用（Chromium + Node） |

所以注册用户装依赖时不用再单独下 ffmpeg / 内核。

## 常用命令

```powershell
# 前台启动（关掉窗口 = 停止服务）
python\启动.bat

# 后台常驻
powershell -ExecutionPolicy Bypass -File python\后台启动.ps1
powershell -ExecutionPolicy Bypass -File python\停止.ps1

# 直接用解释器
.venv\Scripts\python.exe run.py
.venv\Scripts\python.exe run.py --port 9000 --no-browser
.venv\Scripts\python.exe run.py --host 0.0.0.0        # 局域网可访问
```

## 和另外两版的区别

| | Python 版 | Docker 版 | 桌面版 |
| --- | --- | --- | --- |
| 需要装 Python | 是 | 否 | 否 |
| 需要自己开浏览器 | 是 | 是 | 否（自带窗口） |
| 需要联网装依赖 | 首次是 | 否 | 否 |
| 适合 | 改代码 / 调试 | NAS / 服务器 | 普通用户 |
