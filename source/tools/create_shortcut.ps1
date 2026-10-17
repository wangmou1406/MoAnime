# ============================================================
#  可选：在桌面创建 / 更新 “动漫下载机 Anime Downloader器” 快捷方式
#  用法：powershell -ExecutionPolicy Bypass -File tools\create_shortcut.ps1
#  说明：快捷方式直接指向 启动.bat，双击即启动服务并自动打开浏览器。
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

$target  = Join-Path $PWD '启动.bat'
$iconSrc = Join-Path $PWD '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $target)) {
    Write-Host "没有找到启动.bat，请在项目根目录下运行本脚本" -ForegroundColor Red
    exit 1
}

$desktop  = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop '动漫下载机 Anime Downloader器.lnk'

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath       = $target
$link.WorkingDirectory = $PWD.Path
$link.Description      = '动漫下载机 Anime Downloader器（Python + Web 界面）'
$link.WindowStyle      = 1
if (Test-Path -LiteralPath $iconSrc) { $link.IconLocation = "$iconSrc,0" }
$link.Save()

Write-Host "已创建桌面快捷方式：" -ForegroundColor Green
Write-Host "  $linkPath"
Write-Host ""
Write-Host "双击它即可启动服务（黑窗口请保持打开，关掉窗口 = 停止服务）。"
