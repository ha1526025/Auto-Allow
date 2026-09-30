@echo off
rem ============================================================
rem  Build and verify the distribution package.
rem
rem   1) verify the command-to-Japanese converter
rem   2) build KiroAutoAllow.exe with PyInstaller
rem   3) refresh the release folder (exe + README.txt only)
rem   4) create Kiro-Auto-Allow.zip
rem   5) extract to a separate folder and launch from there
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found. Run build.bat first.
    pause
    exit /b 1
)

echo [1/5] verifying the converter ...
".venv\Scripts\python.exe" verify_explain.py
if errorlevel 1 goto failed

echo [2/5] building exe ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean KiroAutoAllow.spec > build_pyinstaller.log 2>&1
if errorlevel 1 goto failed
if not exist "dist\KiroAutoAllow.exe" goto failed

echo [3-5/5] packaging and launch test ...
powershell -NoProfile -ExecutionPolicy Bypass -File "package.ps1"

echo.
echo ================ CONVERTER RESULT ================
if exist "verify_result.txt" type "verify_result.txt"
echo.
echo ================ PACKAGE RESULT ==================
if exist "package_result.txt" type "package_result.txt"
echo.
echo Done.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. See build_pyinstaller.log and verify_result.txt
pause
exit /b 1
