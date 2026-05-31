@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ╔══════════════════════════════════════════════╗
echo ║         Photo Cleaner - 智能照片清理工具       ║
echo ╚══════════════════════════════════════════════╝
echo.

:: ── Find Python ──
set PYTHON=python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    :: Check if we have embedded python from build
    if exist "%~dp0python-embed\python.exe" (
        set PYTHON=%~dp0python-embed\python.exe
    ) else (
        echo ⚠️ 未检测到 Python
        echo.
        echo 有两种方式继续：
        echo.
        echo   方式1（推荐）：双击 build.bat 一键打包为 exe
        echo       打包后直接运行 exe 即可，无需安装 Python
        echo.
        echo   方式2：安装 Python 3.8+
        echo       下载: https://www.python.org/downloads/
        echo       安装时勾选 "Add Python to PATH"
        echo.
        pause
        exit /b 1
    )
)

:: ── Check if already built ──
if exist "dist\PhotoCleaner\PhotoCleaner.exe" (
    echo 发现已编译的 exe 文件，直接启动...
    echo.
    start "" "dist\PhotoCleaner\PhotoCleaner.exe"
    echo    ✅ 已启动！
    echo    请在你的浏览器中访问：http://localhost:5800
    echo.
    pause
    exit /b 0
)

:: ── Run from source ──
echo 正在以源码模式启动（首次运行会自动安装依赖）...
echo.

:: Install uv if needed
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在安装 uv (高速包管理器)...
    powershell -Command "& {Invoke-WebRequest -Uri 'https://astral.sh/uv/install.ps1' -OutFile '%TEMP%\install-uv.ps1'; & '%TEMP%\install-uv.ps1'}" >nul 2>&1
    set PATH=%USERPROFILE%\.cargo\bin;%PATH%
)

echo 正在检查依赖...
uv venv --python 3.12 >nul 2>&1
uv pip install -r requirements.txt --python 3.12 >nul 2>&1
echo 依赖检查完成
echo.
echo    ✅ Photo Cleaner 已启动！
echo    请在你的浏览器中访问：
echo.
echo       🌐 http://localhost:5800
echo.
echo    按 Ctrl+C 停止服务
echo.

uv run python app.py

pause
