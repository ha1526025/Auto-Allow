@echo off
rem Build Kiro-Auto-Allow.zip and run the extract-and-launch test.
rem Double-click this file if the packaging step needs to be run manually.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "_pack.ps1"
echo.
echo ---- result ----
if exist "_pack_log.txt" type "_pack_log.txt"
echo.
pause
