@echo off
rem Kiro Auto Allow を 1 つの .exe にビルドする
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 仮想環境を作成します...
    python -m venv .venv || goto :error
)

echo [1/3] ライブラリを導入します...
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt --disable-pip-version-check || goto :error

echo [2/3] UI Automation ラッパーを生成します...
".venv\Scripts\python.exe" -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll')" || goto :error

echo [3/3] exe をビルドします...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean KiroAutoAllow.spec || goto :error

echo.
echo 完成: %cd%\dist\KiroAutoAllow.exe
pause
exit /b 0

:error
echo.
echo ビルドに失敗しました。
pause
exit /b 1
