# 桌面版 · 交付包

本目录是一个**完整可分发**的 Windows 桌面版，不需要看别的地方。

## 里面有什么

| 文件 | 作用 |
| --- | --- |
| `动漫下载机-3.1.0-Setup.exe` | **安装包**（约 342 MB）：双击安装，带卸载程序 |
| `动漫下载机/` | **免安装版**：整个文件夹拷走、双击 `AnimeDownloader.exe` 就能跑 |
| `README.md` | 本文件 |

## 怎么用

### 安装版（推荐）

双击 `动漫下载机-3.1.0-Setup.exe` →
按向导装完 → 桌面/开始菜单出现「动漫下载机」→ 双击打开。

* **按用户安装**到 `%LOCALAPPDATA%\Programs\AnimeDownloader`，**不需要管理员权限**
* 自带卸载程序（「设置 → 应用」里也能卸载）
* 卸载时会单独问是否删除用户数据，**已下载的视频不会动**

### 免安装版

把 `动漫下载机/` 整个文件夹拷到任意位置，双击里面的 `AnimeDownloader.exe`。

> 拷走后请保持目录结构完整（`backend\` 和 `_internal\` 不能少）。

## 它长什么样

启动后是一个**没有地址栏和标签栏的原生窗口**，界面用 Windows 自带的
**WebView2** 渲染 —— 不打包任何浏览器内核。

```
AnimeDownloader.exe   ← 宿主（C#，约 148 KB 代码 + .NET 运行时）
   │ 选端口 → 拉起 backend\动漫下载机.exe --headless --port N
   │ 等 /api/meta 就绪 → 开 WebView2 窗口
   ▼ 关窗口 → 结束后端进程树
backend\动漫下载机.exe ← Python/Flask 后端：全部业务逻辑
```

* **不需要装 Python**（后端已打包）
* **不需要装浏览器**（用系统的 WebView2）
* **不需要装 .NET**（宿主自包含，约 147 MB 运行时已打包进去）

## 数据放在哪

| 内容 | 位置 |
| --- | --- |
| 数据库 / 设置 / 日志 | `%LOCALAPPDATA%\AnimeDownloader\data` |
| WebView2 缓存 | `%LOCALAPPDATA%\AnimeDownloader\webview2` |
| 下载的视频 | 首次运行默认 `%USERPROFILE%\Downloads\AnimeDownloader`，之后在界面里改 |

程序目录里**不写任何东西**，所以升级/重装不会丢配置。

## 出问题看哪

| 文件 | 内容 |
| --- | --- |
| `data\logs\host.log` | 宿主步骤：后端路径、端口、是否就绪 |
| `data\logs\backend.log` | 后端的 stdout / stderr |
| `data\logs\app.log` | 应用日志（5 MB × 3 轮转） |
| `data\logs\crash.log` | 未捕获异常的完整 traceback |

（都在 `%LOCALAPPDATA%\AnimeDownloader\data\logs\` 下）

## 实测记录

```
安装      942 文件 / 1101 MB
运行      宿主拉起后端 → 约 1 秒就绪 → 窗口打开
          API 正常（13 个源、0 任务），msedgewebview2 进程 12 个
关窗口    2 秒内宿主与后端全部退出
卸载      目录彻底清除
```

## 系统要求

* Windows 10 / 11（64 位）
* WebView2 运行时（Win10 1803+ 一般预装；被精简过的系统需单独装：
  <https://developer.microsoft.com/microsoft-edge/webview2/>）
