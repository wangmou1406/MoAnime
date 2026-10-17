# -*- coding: utf-8 -*-
"""组装 Python 便携版到 dist\\python\\，并打成 zip。

便携版的定位：**不需要安装，解压即用**，但目标机器要有 Python ≥ 3.10
（脚本会自己建 .venv 装依赖）。

为什么不直接把 .venv 打进去：
    虚拟环境**不是可重定位的** —— pyvenv.cfg 和 Scripts\\*.exe 里写死了绝对路径，
    换个目录/换台机器基本都会坏。所以便携版只带源码 + 启动脚本，
    首次运行时由 launcher.ps1 现场创建 .venv。

用法（项目根执行）：
    powershell -ExecutionPolicy Bypass -File python\\build-portable.ps1
    powershell -ExecutionPolicy Bypass -File python\\build-portable.ps1 -NoKernel   # 不含浏览器内核（体积小很多）
    powershell -ExecutionPolicy Bypass -File python\\build-portable.ps1 -NoZip      # 只出文件夹
"""
param(
    [switch]$NoKernel,
    [switch]$NoZip
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$PyDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $PyDir
$OutDir = Join-Path $ProjectRoot 'dist\python'
$PkgDir = Join-Path $OutDir '动漫下载机-python'

function Write-Step($msg) {
    Write-Host ""
    Write-Host "=== $msg" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- 清理
Write-Step "准备输出目录"
if (Test-Path -LiteralPath $PkgDir) { Remove-Item -LiteralPath $PkgDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PkgDir | Out-Null
Write-Host "    $PkgDir"

# ---------------------------------------------------------------- 源码
Write-Step "复制源码"
$codeDirs = @('core', 'sources', 'web', 'static', 'templates', 'tools')
foreach ($d in $codeDirs) {
    $src = Join-Path $ProjectRoot $d
    if (-not (Test-Path -LiteralPath $src)) { continue }
    Copy-Item -LiteralPath $src -Destination $PkgDir -Recurse -Force
    Write-Host "    $d"
}
foreach ($f in @('run.py', 'requirements.txt', 'README.md', '.gitignore')) {
    $src = Join-Path $ProjectRoot $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $PkgDir $f) -Force
        Write-Host "    $f"
    }
}

# 启动脚本单独放一层 python\，和仓库里的结构保持一致
Write-Step "复制启动脚本"
$pySrc = Join-Path $ProjectRoot 'python'
$pyDst = Join-Path $PkgDir 'python'
New-Item -ItemType Directory -Force -Path $pyDst | Out-Null
foreach ($f in @('启动.bat', '后台启动.ps1', '停止.ps1', 'launcher.ps1', 'README.md')) {
    $src = Join-Path $pySrc $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $pyDst $f) -Force
        Write-Host "    python\$f"
    }
}

# 便携版里把「双击入口」也放到最外层，少点几层
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
    $pwt = Join-Path $ProjectRoot 'data\_pwt'
    if (Test-Path -LiteralPath $pwt) {
        Write-Step "复制内置浏览器内核（约 540 MB，需要一点时间）"
        $dst = Join-Path $PkgDir 'data'
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        Copy-Item -LiteralPath $pwt -Destination $dst -Recurse -Force
        Write-Host "    data\_pwt"
    } else {
        Write-Host "    项目里没有 data\_pwt，跳过内核" -ForegroundColor Yellow
    }
}

# 清掉拷贝进来的缓存目录
Get-ChildItem $PkgDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
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
