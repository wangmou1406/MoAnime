  # ============================================================
   # 动漫下载机 启动器
   # 用法： 双击 启动.bat   或   powershell -ExecutionPolicy Bypass -File launcher.ps1
   # 可选参数： --port 9000  --host 0.0.0.0  --no-browser
  # ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 目录布局（重组后）：
#   python\       ← 本脚本所在（运行根：.venv / data / logs / downloads）
#   ..\source\    ← 代码根（run.py / core / web / tools）
# 程序的数据目录默认按「代码位置」推算，所以这里用环境变量把它钉到 python\ 下，
# 否则数据库/日志会跑到 source\data 里去。
$ProjectRoot = $PSScriptRoot
$SourceRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'source'
Set-Location -Path $ProjectRoot

$env:AD_DATA_DIR = Join-Path $ProjectRoot 'data'
$env:AD_LOG_DIR = Join-Path $ProjectRoot 'logs'
$env:AD_BUNDLED_DIR = Join-Path $ProjectRoot 'data\_pwt'
# 下载目录：只在首次运行（还没有 settings.json）时给默认值，
# 之后以界面里的设置为准 —— 否则环境变量会永久压住用户的选择。
if (-not (Test-Path (Join-Path $ProjectRoot 'data\settings.json'))) {
    $env:AD_DOWNLOAD_DIR = Join-Path $ProjectRoot 'downloads'
}
$OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Title {
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host "  动漫下载机器" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host ""
}

  # 判断某个 python.exe 是否可用且版本 >= 3.10
function Test-PythonExe {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    if (-not (Test-Path -LiteralPath $Exe)) { return $false }
    try {
        $version = & $Exe -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        return ([int]$version -ge 310)
    } catch { return $false }
}

  # 依赖是否齐全
function Test-Dependencies {
    param([string]$Exe)
    try {
        & $Exe -c "import flask, flask_socketio, requests, bs4, m3u8" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

Write-Title

$venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$python = $null

  # ---------------- 1) 优先使用项目自带的虚拟环境 ----------------
if (Test-PythonExe $venvPython) {
    if (Test-Dependencies $venvPython) {
        $python = $venvPython
        Write-Host "[1/2] 使用已有虚拟环境 .venv" -ForegroundColor Green
    } else {
        Write-Host "[1/2] .venv 存在但缺少依赖，正在安装 ..." -ForegroundColor Yellow
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -r requirements.txt
        if (Test-Dependencies $venvPython) {
            $python = $venvPython
        } else {
            Write-Host "      依赖安装失败，请检查网络或代理设置" -ForegroundColor Red
        }
    }
}

  # ---------------- 2) 没有虚拟环境时，自动探测可用的 Python ----------------
if (-not $python) {
    Write-Host "[1/2] 未找到可用的 .venv，正在查找系统 Python ..." -ForegroundColor Yellow
    $candidates = @()

      # 2.1 py 启动器
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $found = & py -3 -c "import sys;print(sys.executable)" 2>$null
            if ($found) { $candidates += $found }
        } catch { }
    }

      # 2.2 PATH 里的 python（跳过微软商店占位程序）
    Get-Command python -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -and $_.Source -notlike '*WindowsApps*' } |
        ForEach-Object { $candidates += $_.Source }

      # 2.3 常见安装位置
    foreach ($ver in @('314', '313', '312', '311', '310')) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$ver\python.exe")
        $candidates += "C:\Python$ver\python.exe"
        $candidates += "C:\Program Files\Python$ver\python.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-PythonExe $candidate) { $python = $candidate; break }
    }
}

  # ---------------- 3) 仍然找不到：给出明确的解决办法 ----------------
if (-not $python) {
    Write-Host ""
    Write-Host "  没有找到可用的 Python 3.10+，本目录里也没有可用的 .venv。" -ForegroundColor Red
    Write-Host ""
    Write-Host "  解决办法（任选其一）：" -ForegroundColor Yellow
    Write-Host "    A. 安装 Python 3.10+，安装时务必勾选 “Add python.exe to PATH”"
    Write-Host "       https://www.python.org/downloads/windows/"
    Write-Host "    B. 把别处已经建好的 .venv 整个目录拷贝到本目录（连目录一起拷）"
    Write-Host "       拷贝后直接双击本脚本即可，无需再装 Python"
    Write-Host "    C. 用任意一个已装好的 python.exe 直接跑："
    Write-Host "       <你的python.exe路径> run.py --port 8848"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}

Write-Host "      Python：$python"

  # ---------------- 4) 已经在运行就不再重复启动 ----------------
function Test-PortListening {
    param([int]$Port)
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    } catch { return $false }
}

$port = 8848
try {
    $settings = Get-Content -LiteralPath 'data\settings.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($settings.web_port) { $port = [int]$settings.web_port }
} catch { }
$url = "http://127.0.0.1:$port/"

if (Test-PortListening $port) {
    Write-Host ""
    Write-Host "服务已经在运行：$url" -ForegroundColor Green
    Write-Host "无需重复启动，直接在浏览器打开上面的地址即可。" -ForegroundColor Green
    Write-Host "如需停止：powershell -ExecutionPolicy Bypass -File .\停止.ps1" -ForegroundColor DarkGray
    Read-Host "按回车键退出"
    exit 0
}

  # ---------------- 5) 首次运行：建虚拟环境 + 装依赖 ----------------
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[1/2] 创建虚拟环境并安装依赖（首次较慢，请耐心等待）..." -ForegroundColor Yellow
    & $python -m venv '.venv'
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      创建虚拟环境失败" -ForegroundColor Red
        Read-Host "按回车键退出"; exit 1
    }
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt
    if (-not (Test-Dependencies $venvPython)) {
        Write-Host "      依赖安装失败，请检查网络后重试" -ForegroundColor Red
        Read-Host "按回车键退出"; exit 1
    }
    $python = $venvPython
}

  # ---------------- 5) ffmpeg 提示 ----------------
if (-not (Test-Path -LiteralPath 'tools\ffmpeg.exe') -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "[提示] 未检测到 ffmpeg：HLS 分片会直接拼接为 .ts 文件" -ForegroundColor Yellow
    Write-Host "       想要标准 mp4 时，可在网页「设置 - 外部依赖」一键下载安装"
}

Write-Host ""
Write-Host "[2/2] 启动服务，请在浏览器打开：$url" -ForegroundColor Green
Write-Host "      关闭本窗口即可停止程序"
Write-Host "      想让它常驻（关掉窗口也继续跑）：用 .\后台启动.ps1" -ForegroundColor DarkGray
Write-Host ""

& $python "$SourceRoot\run.py" @args

Write-Host ""
Write-Host "程序已退出（退出码 $LASTEXITCODE）"
Read-Host "按回车键退出"
