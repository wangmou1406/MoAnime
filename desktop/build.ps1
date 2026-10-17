# ============================================================================
#  一键构建桌面版 + 安装包
#
#  用法（在项目根执行）：
#      powershell -ExecutionPolicy Bypass -File desktop\build.ps1
#
#  三步：
#    1. PyInstaller 打包 Python 后端   -> dist\desktop\动漫下载机\backend\
#    2. dotnet 发布 System WebView 宿主 -> dist\desktop\动漫下载机\（根目录）
#    3. NSIS 打安装包                   -> dist\desktop\动漫下载机-<版本>-Setup.exe
#
#  架构（System WebView 方案）：
#      宿主 AnimeDownloader.exe 只是个小壳子，界面靠**系统自带的 WebView2** 渲染，
#      不打包任何浏览器内核 —— 和 Tauri / Wails / Neutralino 一个思路。
#      Python 后端（Flask）负责全部业务逻辑，由宿主拉起、随窗口关闭而结束。
#
#  参数：
#      -SkipBackend          跳过 PyInstaller（后端没改时省时间）
#      -SkipHost             跳过 dotnet 发布
#      -FrameworkDependent   宿主改为框架依赖发布（约 2MB，但目标机需装 .NET 8）
#      -Makensis             指定 makensis 路径
#
#  注意：param() 必须是脚本里第一条**语句**，前面只能有注释。
#        早期版本在这里误放了 Python 的 """docstring"""，被 PowerShell 当成
#        字符串表达式，导致 param 块变成命令调用、所有参数绑定失败
#        （$Makensis 变 null，-SkipBackend 之类的开关全失效）。
# ============================================================================
param(
    [switch]$SkipBackend,
    [switch]$SkipHost,
    [switch]$FrameworkDependent,
    [string]$Makensis = "$env:LOCALAPPDATA\ad-tools\nsis\nsis-3.11\makensis.exe"
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$DesktopDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $DesktopDir
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

$AppRoot = Join-Path $ProjectRoot 'dist\desktop\动漫下载机'
$BackendDir = Join-Path $AppRoot 'backend'
$HostDir = Join-Path $DesktopDir 'webview2-host'

function Write-Step($msg) {
    Write-Host ""
    Write-Host "=== $msg" -ForegroundColor Cyan
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "找不到虚拟环境解释器：$Python`n请先运行 python\启动.bat 完成初始化。"
}
if ([string]::IsNullOrWhiteSpace($Makensis)) {
    $Makensis = "$env:LOCALAPPDATA\ad-tools\nsis\nsis-3.11\makensis.exe"
}

# ---------------------------------------------------------------- 1) 后端
if (-not $SkipBackend) {
    Write-Step "1/3 打包 Python 后端（PyInstaller）"
    $staging = Join-Path $ProjectRoot 'dist\desktop\_pyi_staging'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Push-Location $ProjectRoot
    try {
        & $Python -m PyInstaller --noconfirm --clean `
            --distpath $staging `
            --workpath (Join-Path $ProjectRoot 'data\_pyi') `
            (Join-Path $DesktopDir 'app.spec')
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）" }
    } finally {
        Pop-Location
    }
    # PyInstaller 产出 <staging>\动漫下载机\，摊平到 backend\ 下
    if (Test-Path -LiteralPath $BackendDir) { Remove-Item -LiteralPath $BackendDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $BackendDir | Out-Null
    Move-Item (Join-Path $staging '动漫下载机\*') $BackendDir -Force
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "    -> $BackendDir"
}

# ---------------------------------------------------------------- 2) 宿主
if (-not $SkipHost) {
    Write-Step "2/3 发布 System WebView 宿主（dotnet）"
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
    $out = Join-Path $HostDir 'publish'
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }

    $selfContained = if ($FrameworkDependent) { 'false' } else { 'true' }
    Push-Location $HostDir
    try {
        & dotnet publish -c Release -r win-x64 `
            --self-contained $selfContained `
            -o $out --nologo
        if ($LASTEXITCODE -ne 0) { throw "dotnet publish 失败（退出码 $LASTEXITCODE）" }
    } finally {
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath $AppRoot)) {
        New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
    }
    Copy-Item (Join-Path $out '*') $AppRoot -Recurse -Force
    # 去掉调试符号和 XML 文档，安装包里用不上
    Get-ChildItem $AppRoot -Filter '*.xml' -File | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem $AppRoot -Filter '*.pdb' -File | Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Host "    -> $AppRoot  (自包含=$(-not $FrameworkDependent))"
}

# ---------------------------------------------------------------- 校验
$hostExe = Join-Path $AppRoot 'AnimeDownloader.exe'
$backendExe = Join-Path $BackendDir '动漫下载机.exe'
foreach ($p in @($hostExe, $backendExe)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "缺少打包产物：$p" }
}
$size = (Get-ChildItem $AppRoot -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ""
Write-Host ("    宿主：{0}" -f $hostExe)
Write-Host ("    后端：{0}" -f $backendExe)
Write-Host ("    合计：{0:N0} MB" -f ($size / 1MB))

# ---------------------------------------------------------------- 3) 安装包
Write-Step "3/3 制作安装包（NSIS）"

# 打包前清掉运行期产物：这些是程序跑起来才生成的，
# 不该进安装包（会把开发机上的配置/日志带给别人）。
# installer.nsi 里也做了 /x 排除，这里是双保险 —— 顺便避免
# 「SQLite 正被运行中的程序占用，NSIS 读不了」导致构建失败（实测踩过）。
foreach ($junk in @('data', 'webview2')) {
    $p = Join-Path $AppRoot $junk
    if (Test-Path -LiteralPath $p) {
        try {
            Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop
            Write-Host "    已清理运行期目录：$junk"
        } catch {
            Write-Host "    ⚠ 清不掉 $junk（程序可能还在运行），安装包会跳过它" -ForegroundColor Yellow
        }
    }
}

if (-not (Test-Path -LiteralPath $Makensis)) {
    throw "找不到 makensis：$Makensis`n用 -Makensis 指定路径，或下载 NSIS 便携版。"
}
Push-Location $DesktopDir
try {
    & $Makensis 'installer.nsi'
    if ($LASTEXITCODE -ne 0) { throw "NSIS 失败（退出码 $LASTEXITCODE）" }
} finally {
    Pop-Location
}

$setup = Get-ChildItem (Join-Path $ProjectRoot 'dist\desktop') -Filter '*Setup.exe' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) { throw "没有生成安装包" }

Write-Step "完成"
Write-Host ("    安装包：{0}" -f $setup.FullName) -ForegroundColor Green
Write-Host ("    体积：{0:N0} MB" -f ($setup.Length / 1MB))
