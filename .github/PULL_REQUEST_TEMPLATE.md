## 这个 PR 做了什么

<!-- 一句话说清。修 bug 就写「修了什么现象」，加功能就写「解决什么场景」 -->

## 为什么要改

<!-- 说明动机。如果能链接 Issue，写 Fixes #123 -->

## 改到哪部分

- [ ] `source/` — 公共逻辑（三个版本都受影响）
- [ ] `python/` — Python 版启动脚本 / 便携打包
- [ ] `docker/` — Dockerfile / compose
- [ ] `desktop/` — C# 宿主 / PyInstaller spec / NSIS 安装包
- [ ] 文档（README / CHANGELOG / 注释）

## 自测

- [ ] `python -m compileall -q source`
- [ ] `python source/tools/test_urls.py`
- [ ] `python source/tools/test_xml.py`
- [ ] `python source/tools/selftest.py`
- [ ] 手工验证了改动涉及的界面/流程

如果改了 `desktop/` 或 `docker/`，请说明是否真的构建/运行过：

<!--
  例：用 desktop/build.ps1 打了安装包并装到 C:\Program Files 验证；
      用 docker compose up -d --build 起了容器并下载一集。
  没验证也照实写，比写"应该没问题"有用。
-->

## 检查项

- [ ] 一个 PR 只做一件事
- [ ] 没有提交凭据 / Cookie / 受版权保护的资源链接
- [ ] 没有把运行期数据或构建产物加进仓库（`python/data`、`downloads`、`.venv`、各 `dist/`）
- [ ] 新代码有说明「为什么」的注释，而不是把代码翻译一遍
