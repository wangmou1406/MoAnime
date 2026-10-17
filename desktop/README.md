# 桌面版

双击即用的 Windows 程序，**自带 ffmpeg 与浏览器内核，不需要装 Python、不需要开浏览器**。
附带安装包（安装/卸载/快捷方式一应俱全）。

## 目录里有什么

| 文件 | 作用 |
| --- | --- |
| `desktop_main.py` | 桌面入口：后台起 Flask，再弹一个无地址栏的应用窗口 |
| `app.spec` | PyInstaller 打包配置（onedir，带上 static/templates/tools/内核） |
| `installer.nsi` | NSIS 安装包脚本（按用户安装、带卸载程序） |
| `version_info.txt` | exe 的版本信息（资源管理器「属性」里能看到） |
| `build.ps1` | 一键：打包 + 做安装包 |

## 一键构建

```powershell
# 在项目根执行
powershell -ExecutionPolicy Bypass -File desktop\build.ps1
```

产物：

```
dist\desktop\动漫下载机\               ← 免安装版（整个文件夹拷走就能跑）
dist\desktop\动漫下载机-3.1.0-Setup.exe ← 安装包
```

只重打安装包（程序已打包过）：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build.ps1 -SkipPyInstaller
```

## 安装包做了什么

* **按用户安装**到 `%LOCALAPPDATA%\Programs\AnimeDownloader`，**不需要管理员权限**
  （装在 Program Files 会写不了数据目录，所以刻意不这么做）
* 桌面 + 开始菜单快捷方式
* 注册到「应用和功能」，带卸载程序
* 卸载时单独询问是否删除用户数据（数据库/设置/日志），**已下载的视频不会被删**

## 数据放在哪

| 内容 | 位置 |
| --- | --- |
| 数据库 / 设置 / 日志 | `%LOCALAPPDATA%\AnimeDownloader\data` |
| 浏览器配置（独立） | `%LOCALAPPDATA%\AnimeDownloader\browser-profile` |
| 下载的视频 | 首次运行默认 `%USERPROFILE%\Downloads\AnimeDownloader`，之后在界面里改 |

程序目录里**不写任何数据**，所以卸载/升级都不会丢配置（除非你主动选删除）。

## 为什么窗口是 Edge 而不是原生控件

pywebview 在 Windows 上靠 pythonnet 调 .NET 的 WebView2，而 pythonnet 的原生加载器
**无法从含中文或空格的路径加载 `Python.Runtime.dll`**（本项目路径是
`项目文件\DeepSeek Harness\软件\...`，实测 `import clr` 直接失败，换成纯 ASCII
路径也一样不行）。所以改用 Chromium 的 `--app=` 应用窗口：

* Edge 是 Windows 10/11 自带，零额外依赖；
* `--user-data-dir` 用独立配置，**不碰你正在用的浏览器**；
* 没有地址栏/标签栏，看起来就是个应用；
* 关掉窗口 → 浏览器进程退出 → 程序自动停下载并退出（实测 2 秒内）。

想换成真正的原生窗口，只需把 `desktop_main.py` 里的 `_open_app_window()`
换成 pywebview 实现，其余逻辑不用动。

## 崩溃怎么查

桌面版是 `console=False` 打包的，没有控制台。所以：

* 正常日志：`%LOCALAPPDATA%\AnimeDownloader\data\logs\app.log`
* **崩溃日志**：同目录 `crash.log`（没有就说明没崩）

`desktop_main.py` 里装了 `sys.excepthook` 和 `threading.excepthook`，
任何未捕获异常都会带完整 traceback 落到 `crash.log`。

## 打包时踩过的两个坑（已修，留给后来人）

1. **`ValueError: Invalid async_mode specified`**
   engineio 的异步后端是运行时按名字动态导入的，PyInstaller 静态分析看不见。
   必须把 `engineio.async_drivers.threading` 写进 `hiddenimports`。

2. **`_bundle_dir()` 层级算错**
   开发态本文件在 `<项目根>\desktop\`，往上退**两级**才是项目根。
   早先它在 `packaging\desktop\`（退三级），搬家后忘了改，导致 `sys.path`
   指到项目上级目录、`import core` 失败。
