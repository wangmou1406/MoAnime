# ============================================================
#  动漫下载机 后台启动：服务在后台常驻，关掉本窗口也继续运行
#  用法：powershell -ExecutionPolicy Bypass -File 后台启动.ps1
#  停止：powershell -ExecutionPolicy Bypass -File 停止.ps1
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 目录布局：
#   python\      ← 本脚本所在（运行根：.venv / data / logs / downloads）
#   ..\source\   ← 代码根（run.py / core / web / tools）
# 程序的数据目录默认按「代码位置」推算，所以这里用环境变量把它钉到 python\ 下，
# 否则数据库/日志会跑到 source\data 里去。
$ProjectRoot = $PSScriptRoot
$SourceRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'source'
Set-Location -Path $ProjectRoot

$env:AD_DATA_DIR = Join-Path $ProjectRoot 'data'
$env:AD_LOG_DIR = Join-Path $ProjectRoot 'logs'
$env:AD_BUNDLED_DIR = Join-Path $ProjectRoot 'data\_pwt'
# 下载目录：只在首次运行（还没有 settings.json）时给默认值，
# 之后以界面设置为准 —— 否则环境变量会永久压住用户的选择。
if (-not (Test-Path (Join-Path $ProjectRoot 'data\settings.json'))) {
    $env:AD_DOWNLOAD_DIR = Join-Path $ProjectRoot 'downloads'
}

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "没有找到 .venv，请先双击 启动.bat 完成初始化。" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

# 读取设置里的端口
$port = 8848
try {
    $settings = Get-Content -LiteralPath 'data\settings.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($settings.web_port) { $port = [int]$settings.web_port }
} catch { }
$url = "http://127.0.0.1:$port/"

$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "服务已经在运行（端口 $port，PID $($listening.OwningProcess)）" -ForegroundColor Yellow
    Write-Host "请在浏览器打开：$url" -ForegroundColor Green
    Read-Host "按回车键退出"
    exit 0
}

$outLog = Join-Path $ProjectRoot 'logs\service.out.log'
$errLog = Join-Path $ProjectRoot 'logs\service.err.log'
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot 'logs') | Out-Null

# 这两个文件装的是「本次运行」的原始 stdout / stderr，**没有轮转**，
# 程序一直跑就会一直涨（实测曾一天涨到 750 MB）。
# 真正的日志是 logs\app.log，它由 RotatingFileHandler 管着（5 MB × 3 份）。
# 所以每次启动前先把上一次的输出清掉，保证它最多只有一"轮"的量。
Remove-Item $outLog, $errLog -Force -ErrorAction SilentlyContinue

# 注意：项目路径含空格，run.py 的路径必须自带引号，否则会被拆成多个参数
$runScript = Join-Path $SourceRoot 'run.py'
$proc = Start-Process -FilePath $python -ArgumentList "`"$runScript`"" `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru

Start-Sleep -Seconds 6
$alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if (-not $alive) {
    Write-Host "启动失败，请看日志：$errLog" -ForegroundColor Red
    Get-Content $errLog -Tail 15 -Encoding UTF8 -ErrorAction SilentlyContinue
    Read-Host "按回车键退出"
    exit 1
}

Write-Host "动漫下载机 已在后台运行" -ForegroundColor Green
Write-Host "  请在浏览器打开：$url" -ForegroundColor Green
Write-Host "  进程：PID $($proc.Id)（关掉本窗口也继续运行）"
Write-Host "  日志：$errLog"
Write-Host "  停止：powershell -ExecutionPolicy Bypass -File 停止.ps1" -ForegroundColor DarkGray
Read-Host "按回车键退出"
