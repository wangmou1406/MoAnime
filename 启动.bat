@echo off
rem  MoAnime launcher - delegates to launcher.ps1 (handles Unicode paths correctly)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" %*
