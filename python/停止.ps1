# ============================================================
#  动漫下载机 停止服务：按 settings.json 里的端口找到进程并结束
#  用法：powershell -ExecutionPolicy Bypass -File 停止.ps1
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

$port = 8848
try {
    $settings = Get-Content -LiteralPath 'data\settings.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($settings.web_port) { $port = [int]$settings.web_port }
} catch { }

$conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $conn) {
    Write-Host "端口 $port 上没有正在运行的 动漫下载机 服务。" -ForegroundColor Yellow
    Read-Host "按回车键退出"
    exit 0
}

$pid0 = $conn.OwningProcess | Select-Object -First 1
# venv 里的 python.exe 进程路径会指向基础解释器，所以用「命令行是否包含本目录 / run.py」判定
$cmdline = ""
try {
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid0" -ErrorAction SilentlyContinue).CommandLine
    if (-not $cmdline) { $cmdline = "" }
} catch { }
$isMoanime = ($cmdline -like "*$ProjectRoot*") -or ($cmdline -like '*run.py*')

Write-Host "发现监听 $port 的进程：PID $pid0"
if ($cmdline) {
    Write-Host "  命令行：$($cmdline.Substring(0, [Math]::Min(120, $cmdline.Length)))"
}
if (-not $isMoanime) {
    $answer = Read-Host "该进程看起来不是 动漫下载机（命令行未包含本目录），仍要结束它吗？(y/N)"
    if ($answer -ne 'y') {
        Write-Host "已取消。" -ForegroundColor DarkGray
        Read-Host "按回车键退出"
        exit 0
    }
}

try {
    Stop-Process -Id $pid0 -Force -ErrorAction Stop
    Start-Sleep -Seconds 2
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "进程已结束，但端口仍被占用，请稍后重试。" -ForegroundColor Yellow
    } else {
        Write-Host "动漫下载机 已停止（端口 $port 已释放）。" -ForegroundColor Green
    }
} catch {
    Write-Host "停止失败：$($_.Exception.Message)" -ForegroundColor Red
}
Read-Host "按回车键退出"
