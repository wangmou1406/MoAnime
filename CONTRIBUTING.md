# 贡献指南

感谢愿意帮忙。这个项目是「多源动漫检索 + 下载」的本地工具，改动前请先看下面几条，
能省掉很多来回。

## 先读这个：项目边界

* 本仓库**不提供、不托管、不分发任何影视内容**。它只是一个从第三方资源站
  抓取公开接口/页面的客户端。
* 请**不要**在 Issue / PR / 截图里张贴受版权保护的资源链接、番剧下载地址、
  或任何站点凭据。
* 不要提交任何凭据（Cookie、token、账号密码）、别人的站点密钥。

## 提 Issue 之前

1. 先搜一下已有 Issue，避免重复。
2. 用对应的模板（Bug / 功能建议）。
3. **带上日志**，这是最有用的一条：
   * 桌面版：`%LOCALAPPDATA%\AnimeDownloader\data\logs\`（`app.log` / `host.log` / `crash.log`）
   * Python 版：`python/logs/` 与 `python/data/logs/`
   * Docker 版：`docker compose logs --tail=200`
   * 贴之前请**删掉真实站点域名、Cookie、路径里的个人用户名**。
4. 「某个源搜不到 / 下不了」这类问题，请附上**源名称 + 检索关键词 + 时间**，
   否则没法复现（站点接口经常变）。

## 提 PR

* 一个 PR 只做一件事，说明里写清「为什么」而不只是「改了什么」。
* 改完请本地跑一遍：

  ```bash
  # 语法
  python -m compileall -q source

  # 单元测试（URL 拼装 / XML 解析，纯本地，不需要网络）
  python source/tools/test_urls.py
  python source/tools/test_xml.py

  # 端到端自检（47 项，会起一个临时服务）
  python source/tools/selftest.py

  # 前端脚本能否正常加载（需要 Node）
  node source/tools/check_frontend.cjs
  ```

* 代码风格：`flake8 --max-line-length=100`（只要求不新增告警）。
* 注释写**为什么**，而不是把代码翻译一遍。踩过的坑值得留一句。

## 目录别搞混

```
source/    共享源码（唯一真源）—— 改代码只改这里
python/    运行根：.venv / data / logs / downloads + 启动脚本
docker/    Docker 版：Dockerfile / compose / dist
desktop/   桌面版：C# 宿主 + PyInstaller spec + NSIS 脚本 + dist
```

* **改代码只改 `source/`**，三个版本共用它。
* 各版本的启动脚本里：`$ProjectRoot` 是**运行根**，`$SourceRoot` 是 `../source`。
* 运行期数据（`python/data`、`downloads`、`.venv`、各 `dist/`）都已在
  `.gitignore` 里排除，**不要强行加进来**。

## 加一个新动漫源

`source/sources/` 下按已有插件的结构加一个文件，用 `@register` 注册，
写好 `search()` / `episodes()` / `resolve_stream()` 即可，无需改别处。
参考 `sources/maccms.py`（最完整）或 `sources/generic.py`（可配置）。

## 提交信息

用一句话说清「改了什么、为什么」，不要写 `update` / `fix bug` 这种。

## 许可

提交 PR 即表示你同意以本仓库的 [MIT License](LICENSE) 授权你的贡献。
