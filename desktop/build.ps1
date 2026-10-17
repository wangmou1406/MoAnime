# -*- coding: utf-8 -*-
"""一键构建桌面版 + 安装包。

用法（在项目根执行）：
    powershell -ExecutionPolicy Bypass -File desktop\build.ps1

做两件事：
  1. PyInstaller 按 app.spec 打包 → dist\\desktop\\动漫下载机\\
  2. NSIS 用 installer.nsi 打安装包 → dist\\desktop\\动漫下载机-<版本>-Setup.exe

NSIS 用的是便携版 makensis（不装系统组件），默认在
    %LOCALAPPDATA%\\ad-tools\\nsis\\nsis-3.11\\makensis.exe
可以用 -Makensis 参数指定别的路径。
"""
param(
    [string]$Makensis = "$env:LOCALAPPDATA\ad-tools\nsis\nsis-3.11\makensis.exe",
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$DesktopDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $DesktopDir
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

function Write-Step($msg) {
    Write-Host ""
    Write-Host "=== $msg" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- 前置检查
if (-not (Test-Path -LiteralPath $Python)) {
    throw "找不到虚拟环境解释器：$Python`n请先运行 python\启动.bat 完成初始化。"
}

# ---------------------------------------------------------------- 1) PyInstaller
if (-not $SkipPyInstaller) {
    Write-Step "1/2 打包桌面程序（PyInstaller）"
    Push-Location $ProjectRoot
    try {
        & $Python -m PyInstaller --noconfirm --clean `
            --distpath (Join-Path $ProjectRoot 'dist\desktop') `
            --workpath (Join-Path $ProjectRoot 'data\_pyi') `
            (Join-Path $DesktopDir 'app.spec')
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）" }
    } finally {
        Pop-Location
    }
}

$AppDir = Join-Path $ProjectRoot 'dist\desktop\动漫下载机'
if (-not (Test-Path -LiteralPath (Join-Path $AppDir '动漫下载机.exe'))) {
    throw "打包产物不完整，没有找到 动漫下载机.exe：$AppDir"
}
$size = (Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("    程序目录：{0}" -f $AppDir)
Write-Host ("    体积：{0:N0} MB" -f ($size / 1MB))

# ---------------------------------------------------------------- 2) NSIS
Write-Step "2/2 制作安装包（NSIS）"
if (-not (Test-Path -LiteralPath $Makensis)) {
    throw "找不到 makensis：$Makensis`n用 -Makensis 指定路径，或按 README 下载 NSIS 便携版。"
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
if ($setup) {
    Write-Step "完成"
    Write-Host ("    安装包：{0}" -f $setup.FullName) -ForegroundColor Green
    Write-Host ("    体积：{0:N0} MB" -f ($setup.Length / 1MB))
} else {
    throw "没有生成安装包"
}
