# 桌面版

双击即用的 Windows 程序，**自带 ffmpeg，界面用系统 WebView2 渲染**，
不需要装 Python、不需要开浏览器、**不打包任何浏览器内核**。

## 架构：System WebView

```
AnimeDownloader.exe  ← 宿主：只负责开窗口（约 1 MB + .NET 运行时）
      │  1. 选一个空闲端口
      │  2. 拉起 backend\动漫下载机.exe --headless --port <port>
      │  3. 轮询 /api/meta 直到就绪
      │  4. 用一个 WebView2 窗口加载 http://127.0.0.1:<port>/
      │  5. 关窗口 → 结束后端进程树
      ▼
backend\动漫下载机.exe  ← Python/Flask 后端：全部业务逻辑（PyInstaller onedir）
```

界面靠 **Windows 自带的 WebView2 运行时**（Win10/11 预装）渲染，
和 Tauri / Wails / Neutralino 是同一个思路 —— 省掉整套 Chromium（约 540 MB）。

| 方案 | 是否打包浏览器 | 体积 | 依赖 |
| --- | --- | --- | --- |
| 打包 Chromium（pywebview/CEF） | 是 | +540 MB | 无 |
| **System WebView2（本方案）** | **否** | **+0** | 系统自带 WebView2 运行时 |
| 外部浏览器 `--app=` | 否 | +0 | 需 Edge/Chrome，且是独立进程 |

> 为什么不用 pywebview：它在 Windows 上靠 pythonnet 调 .NET，而 pythonnet 的原生
> 加载器**无法从含中文或空格的路径加载 `Python.Runtime.dll`**（本项目路径是
> `项目文件\DeepSeek Harness\软件\...`，实测 `import clr` 直接失败）。
> 直接用 C# 写宿主就没有这个问题。

## 目录里有什么

| 文件 | 作用 |
| --- | --- |
| `desktop_main.py` | Python 后端入口（`--headless` 时只起服务、不开窗口） |
| `app.spec` | PyInstaller 打包配置（onedir，带 static/templates/tools/内核） |
| `webview2-host/` | C# 宿主工程（.NET 8 + Microsoft.Web.WebView2） |
| `installer.nsi` | NSIS 安装包脚本 |
| `build.ps1` | 一键：后端 + 宿主 + 安装包 |
| `version_info.txt` | 后端 exe 的版本信息 |

## 一键构建

```powershell
# 在项目根执行
powershell -ExecutionPolicy Bypass -File desktop\build.ps1
```

三步都会跑：PyInstaller 后端 → dotnet 发布宿主 → NSIS 打安装包。

常用参数：

```powershell
-SkipBackend            # 后端没改，跳过 PyInstaller
-SkipHost               # 宿主没改，跳过 dotnet
-FrameworkDependent     # 宿主改为框架依赖（约 2MB，但目标机要装 .NET 8 桌面运行时）
```

产物：

```
dist\desktop\动漫下载机\                ← 免安装版（整个文件夹拷走就能跑）
desktop\dist\动漫下载机-3.1.0-Setup.exe ← 安装包（约 342 MB）
```

## 安装包做了什么

* **按用户安装**到 `%LOCALAPPDATA%\Programs\AnimeDownloader`，**不需要管理员权限**
  （装 Program Files 会写不了数据目录）
* 桌面 + 开始菜单快捷方式，指向 `AnimeDownloader.exe`
* 注册到「应用和功能」，带卸载程序
* 卸载时先结束宿主和后端进程，再删文件；
  并单独询问是否删除用户数据（数据库/设置/日志），**已下载的视频不会被删**

## 数据放在哪

| 内容 | 位置 |
| --- | --- |
| 数据库 / 设置 / 日志 | `%LOCALAPPDATA%\AnimeDownloader\data` |
| WebView2 缓存 | `%LOCALAPPDATA%\AnimeDownloader\webview2` |
| 下载的视频 | 首次运行默认 `%USERPROFILE%\Downloads\AnimeDownloader`，之后在界面里改 |

程序目录里**不写任何东西**，卸载/升级都不会丢配置。
`AD_DATA_DIR` 可覆盖数据目录（便携部署用）。

## 出问题看哪

| 文件 | 内容 |
| --- | --- |
| `data\logs\host.log` | 宿主步骤：后端路径、端口、是否就绪 |
| `data\logs\backend.log` | 后端 stdout/stderr（由宿主重定向） |
| `data\logs\app.log` | 应用自己的日志（5 MB × 3 轮转） |
| `data\logs\crash.log` | **未捕获异常**的完整 traceback |

后端是 `console=False` 打包的，没有控制台；所以 `desktop_main.py` 装了
`sys.excepthook` / `threading.excepthook`，异常一律落到 `crash.log`。
`--headless` 模式下崩溃还会**跳过 PyInstaller 的阻塞对话框**直接退出，
让宿主立刻报错并收尾（否则用户只会看到"打不开"）。

## 实测记录

```
安装      942 文件 / 1101 MB（含自带 .NET 运行时）
运行      宿主拉起后端 → 后端就绪约 1 秒 → 窗口打开
          API 正常（13 个源、0 任务）
          msedgewebview2 进程 12 个 ← 系统 WebView 确实在用
关窗口    WM_CLOSE 后 2 秒内宿主与后端全部退出
卸载      目录彻底清除
```

## 打包时踩过的坑（已修，留给后来人）

1. **`ValueError: Invalid async_mode specified`**
   engineio 的异步后端是运行时按名字动态导入的，PyInstaller 静态分析看不见。
   必须把 `engineio.async_drivers.threading` 写进 `hiddenimports`。

2. **`_bundle_dir()` 层级算错**
   开发态本文件在 `<项目根>\desktop\`，往上退**两级**才是项目根。
   （早先在 `packaging\desktop\` 下，退三级；搬家后忘了改。）

3. **框架依赖 vs 自包含**
   框架依赖只有 2 MB，但 Windows **不自带** .NET 8 桌面运行时，用户装了会打不开。
   所以默认发布**自包含**（+147 MB），安装包 +46 MB，
   换来"双击就能用，不需要任何前置条件"。

4. **WebView2 的 UserDataFolder 必须显式指定**
   默认是 exe 同级目录，装在只读位置时首次创建缓存就失败。固定放数据目录下。

5. **`PrintWindow` 抓不到 WebView2 画面**
   GPU 合成的窗口截不到图 —— 调试时的坑，不影响功能。

---

## 平台与下载位置

**支持：仅 Windows 10/11（64 位）** —— 依赖系统自带的 WebView2 运行时。

| 内容 | 位置 | 怎么改 |
| --- | --- | --- |
| 下载的视频 | `%USERPROFILE%\Downloads\AnimeDownloader`（首次运行时的默认值） | 界面【设置 → 下载位置】 |
| 临时分片 | `<下载目录>\.tmp\` | 跟随下载目录，只改一处 |
| 数据（库/设置/日志） | `%LOCALAPPDATA%\AnimeDownloader\data` | 环境变量 `AD_DATA_DIR` |

> 下载目录只在**首次运行**时写入默认值；之后以界面设置为准，
> 不会被环境变量压住（否则用户改了不生效）。
