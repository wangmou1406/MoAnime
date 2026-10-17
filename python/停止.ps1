# ============================================================
#  动漫下载机 停止服务：按 settings.json 里的端口找到进程并结束
#  用法：powershell -ExecutionPolicy Bypass -File 停止.ps1
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 脚本位于 python\ 子目录，项目根是它的上一级（.venv / run.py / data 都在那里）
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot

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
