# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
版本号在 `source/core/config.py` 的 `VERSION` 里，改版本时记得同步
`desktop/installer.nsi`、`desktop/version_info.txt` 与 `desktop/webview2-host/*.csproj`。

## [3.1.0] - 2026-10-17

三种交付形态：**Docker 版 / Python 版 / Windows 桌面版**（共用一份源码）。

### 新增

* **桌面版**：System WebView 方案 —— C# 宿主调用系统自带的 WebView2 渲染界面，
  **不打包任何浏览器内核**（省约 540 MB）；关窗口即结束后端进程树。
* **NSIS 安装包**：可装到 `C:\Program Files`（提权），或指定任意目录；
  带卸载程序、公共桌面/开始菜单快捷方式、注册到「应用和功能」。
* **应用图标**：`desktop/make_icon.py` 用 PIL 生成多尺寸 `app.ico`（16~256），
  同时用于宿主 exe、后端 exe、安装包与运行期窗口。
* **Python 版 Linux 支持**：`python/启动.sh` / `停止.sh`，
  在 `python:3.12-slim` 容器里实测通过（检索→解析 1180 集→下载）。
* 广告体检两条通道：时长对比（众数基准）+ 画面文字（RapidOCR），跑完自动排序。
* 单实例保护：重复启动会提示「已经在运行」，不再抢同一个日志文件。

### 修复

* `web/routes/ads.py` 定位 `tools/` 的目录层级算错（`web/routes_api.py` 拆成
  `web/routes/*.py` 时漏改），导致文字广告体检报
  `No module named 'ad_detect'`。改用 `config.ROOT_DIR` 这个单一真源。
* `config.download_dir()` / `temp_dir()` 只会 `makedirs`，路径不可写时直接抛
  `PermissionError` 把程序带崩。改成「创建 + 真实写入探测」，失败退到备用目录并告警。
* `clear_logs()` 只清内存环形缓冲、不清磁盘文件（恢复出厂设置后旧日志仍在）。
* 日志疯长：`urllib3` 对每个跳过证书校验的请求打 `InsecureRequestWarning`，
  实测一天能写 750 MB，把真正的错误埋掉。已静音。
* `python/launcher.ps1` 有 11 行注释漏了 `#`，整个脚本语法错误 —— 即
  「双击 启动.bat」这条推荐入口之前根本跑不起来。
* Docker：移除 Dockerfile 里写死的 `AD_TEMP_DIR`，让临时目录正确跟随
  `AD_DOWNLOAD_DIR`（否则自定义挂载时分会写到不存在的路径）。
* Docker：ffmpeg 转封装前补 `ensure_parent_dir()`（输出目录不存在时直接失败）。
* NSIS：安装包不再把运行期 `data/`、`webview2/` 打进去
  （会把开发机配置带给用户，且 SQLite 被占用时构建直接失败）。
* 桌面版数据目录三级回退（`%LOCALAPPDATA%` → `%APPDATA%` → `%TEMP%` → 程序目录），
  `%LOCALAPPDATA%` 不可写时不再 Fatal 退出。
* WebView2 的 `UserDataFolder` 跟随已解析的数据目录（否则窗口闪一下就关）。

### 变更

* 目录重组：根目录只剩 `source/` + `docker/` + `python/` + `desktop/`。
  运行期数据收进 `python/`，构建产物各回自己版本的 `dist/`。
* 新增 Linux 启动脚本；`settings.json` 里存的是绝对路径，重组时已同步迁移。

## [3.0.0] - 2026-10-13

* 代码拆分为 `core/ web/routes/ sources/ tools/` 与前端 `static/js/app/`，补注释。
* 剧集面板竖向排列 + 预览图；在线预览播放。
* 深色模式 / 配色 / 背景图 / 标题 / 动效等外观设置。
* 首次使用免责声明；「恢复出厂设置」。

## [2.x]

* 多源聚合检索（MacCMS / 通用选择器 / JSON 接口 / JSON 订阅 / anime1）。
* HLS 分片并发下载 + AES-128 解密 + ffmpeg 转封装 mp4。
* 字幕下载（assrt / opensubtitles 等）。
* 浏览器取流（Playwright + Node 执行站点 JS）。

## [1.0.0]

* 初版：单源检索与下载。
