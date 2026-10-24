param(
    [switch]$NoKernel,
    [switch]$NoZip
)

# ============================================================================
#  组装 Python 便携版到 python\dist\，并打成 zip
#
#  便携版的定位：不需要安装、解压即用，但目标机器要有 Python >= 3.10
#  （首次运行时 launcher.ps1 会自己建 .venv 装依赖）。
#
#  为什么不把 .venv 打进去：虚拟环境不是可重定位的 —— pyvenv.cfg 和
#  Scripts\*.exe 里写死了绝对路径，换目录/换机器基本都会坏。所以只带源码
#  和启动脚本，首次运行现场创建 .venv。
#
#  包内结构必须和仓库保持一致，launcher.ps1 靠「脚本位置的上一级」找 source：
#      动漫下载机-python\
#          source\        <- run.py / core / web / static / templates / sources / tools
#          python\        <- launcher.ps1 / 后台启动.ps1 / 停止.ps1 ...
#          data\_pwt\     <- 内置浏览器内核（-NoKernel 可跳过）
#          启动.bat
#
#  用法（项目根执行）：
#      powershell -ExecutionPolicy Bypass -File python\build-portable.ps1
#      powershell -ExecutionPolicy Bypass -File python\build-portable.ps1 -NoKernel
#      powershell -ExecutionPolicy Bypass -File python\build-portable.ps1 -NoZip
#
#  注意：param() 必须是脚本里第一条**语句**。早期版本在它前面放了 Python 的
#  """docstring"""，PowerShell 把那段当成字符串表达式，param 块就变成命令调用，
#  -NoKernel / -NoZip 全部失效（还会报 param 不是 cmdlet）。
# ============================================================================

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$PyDir = $PSScriptRoot
$BaseDir = Split-Path -Parent $PyDir
$SourceRoot = Join-Path $BaseDir 'source'
$OutDir = Join-Path $PyDir 'dist'
$PkgDir = Join-Path $OutDir '动漫下载机-python'

function Write-Step($msg) {
    Write-Host ""
    Write-Host "=== $msg" -ForegroundColor Cyan
}

if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot 'run.py'))) {
    Write-Host "找不到 source\run.py，请确认仓库结构（source\ 与 python\ 同级）" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 清理
Write-Step "准备输出目录"
if (Test-Path -LiteralPath $PkgDir) { Remove-Item -LiteralPath $PkgDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PkgDir | Out-Null
Write-Host "    $PkgDir"

# ---------------------------------------------------------------- 源码
Write-Step "复制源码"
$codeDirs = @('core', 'sources', 'web', 'static', 'templates', 'tools')
$pkgSource = Join-Path $PkgDir 'source'
New-Item -ItemType Directory -Force -Path $pkgSource | Out-Null
foreach ($d in $codeDirs) {
    $src = Join-Path $SourceRoot $d
    if (-not (Test-Path -LiteralPath $src)) { continue }
    Copy-Item -LiteralPath $src -Destination $pkgSource -Recurse -Force
    Write-Host "    source\$d"
}
foreach ($f in @('run.py', 'requirements.txt')) {
    $src = Join-Path $SourceRoot $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $pkgSource $f) -Force
        Write-Host "    source\$f"
    }
}
foreach ($f in @('README.md', 'CHANGELOG.md', 'LICENSE', '.gitignore')) {
    $src = Join-Path $BaseDir $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $PkgDir $f) -Force
        Write-Host "    $f"
    }
}

# ---------------------------------------------------------------- 启动脚本
Write-Step "复制启动脚本"
$pyDst = Join-Path $PkgDir 'python'
New-Item -ItemType Directory -Force -Path $pyDst | Out-Null
foreach ($f in @('启动.bat', '启动.sh', '后台启动.ps1', '停止.ps1', '停止.sh', 'launcher.ps1', 'README.md')) {
    $src = Join-Path $PyDir $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $pyDst $f) -Force
        Write-Host "    python\$f"
    }
}

# 最外层也放一个双击入口，少点几层
$rootBat = @'
@echo off
rem 便携版入口：转到 python\ 里执行，脚本会自己建 .venv 并装依赖
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0python\launcher.ps1" %*
'@
Set-Content -Path (Join-Path $PkgDir '启动.bat') -Value $rootBat -Encoding Default
Write-Host "    启动.bat（根目录便捷入口）"

# ---------------------------------------------------------------- 浏览器内核
if ($NoKernel) {
    Write-Host ""
    Write-Host "    跳过浏览器内核（-NoKernel），「浏览器取流」将退化为纯 HTTP 取流" -ForegroundColor Yellow
} else {
    # 内核在 python\data\_pwt（运行根下），不是仓库根的 data\_pwt
    $pwt = Join-Path $PyDir 'data\_pwt'
    if (Test-Path -LiteralPath $pwt) {
        Write-Step "复制内置浏览器内核（约 540 MB，需要一点时间）"
        $dst = Join-Path $PkgDir 'data'
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        Copy-Item -LiteralPath $pwt -Destination $dst -Recurse -Force
        Write-Host "    data\_pwt"
    } else {
        Write-Host "    python\data\_pwt 不存在，跳过内核" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------- 清缓存
Get-ChildItem $PkgDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $PkgDir -Recurse -Directory -Filter '.pytest_cache' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- 统计
$size = (Get-ChildItem $PkgDir -Recurse -File | Measure-Object Length -Sum).Sum
$count = (Get-ChildItem $PkgDir -Recurse -File).Count
Write-Step "打包结果"
Write-Host ("    文件夹：{0}" -f $PkgDir)
Write-Host ("    {0:N0} MB，{1} 个文件" -f ($size / 1MB), $count)

# ---------------------------------------------------------------- 压缩
if (-not $NoZip) {
    Write-Step "压缩成 zip"
    $zip = Join-Path $OutDir '动漫下载机-python.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -Path (Join-Path $PkgDir '*') -DestinationPath $zip -CompressionLevel Optimal
    Write-Host ("    {0}（{1:N0} MB）" -f $zip, ((Get-Item $zip).Length / 1MB)) -ForegroundColor Green
}

Write-Step "完成"
Write-Host "    解压后双击 启动.bat 即可（首次会自动建 .venv 装依赖）"
