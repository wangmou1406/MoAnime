# 安全策略

## 报告安全问题

**请不要开公开 Issue。** 请通过 GitHub 的
[私密漏洞报告](https://docs.github.com/zh/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
（仓库 → Security → Report a vulnerability）提交。

请附上：影响版本、复现步骤、影响范围。我会尽快回。

## 这个项目关心哪些安全问题

按严重程度排：

1. **任意文件写入 / 路径穿越** —— 动漫名、剧集名、字幕文件名都来自第三方站点，
   会参与拼路径。如果能把 `../` 注进文件名，就能写到下载目录之外。
   相关代码：`core/utils.py: sanitize_filename`、`core/naming.py`。
2. **命令注入** —— 程序会调用 `ffmpeg`、`node`、`powershell`。
   凡是把外部字符串拼进命令行的位置都算风险点。
   相关代码：`core/download_engine.py`（ffmpeg）、`core/browser_resolver.py`（node）。
3. **凭据泄露** —— 程序会在日志里记录请求；站点 Cookie / 代理密码不许落盘到日志。
4. **SSRF / 本地文件读取** —— 用户可配置「动漫源」地址与选择器，
   理论上能让程序去请求内网地址。这是**功能设计允许的**（本地工具、用户自己负责），
   不算漏洞，但如果你发现能借它读到本机文件，那算。

## 已知的、不算漏洞的行为

* **不校验证书**（`verify_ssl=False`）：多数资源站证书不合规，这是刻意的默认值，
  可在界面里打开。
* **随程序附带 ffmpeg**：由第三方提供，安全问题请报给 FFmpeg 项目。
* **可以访问内网地址**：本地工具，用户配的源地址照做。

## 支持范围

只对 `main` 分支的最新版本修安全问题。
