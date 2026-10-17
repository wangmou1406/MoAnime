# ============================================================
#  动漫下载机 后台启动：服务在后台常驻，关掉本窗口也继续运行
#  用法：powershell -ExecutionPolicy Bypass -File 后台启动.ps1
#  停止：powershell -ExecutionPolicy Bypass -File 停止.ps1
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
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

$outLog = Join-Path $PSScriptRoot 'logs\service.out.log'
$errLog = Join-Path $PSScriptRoot 'logs\service.err.log'
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'logs') | Out-Null

# 注意：项目路径含空格，run.py 的路径必须自带引号，否则会被拆成多个参数
$runScript = Join-Path $PSScriptRoot 'run.py'
$proc = Start-Process -FilePath $python -ArgumentList "`"$runScript`"" `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
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
