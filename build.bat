@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ╔══════════════════════════════════════════════╗
echo ║         Photo Cleaner - 一键构建工具          ║
echo ╚══════════════════════════════════════════════╝
echo.

:: ── Step 1: Check / Install Python ──
echo [1/4] 检查 Python 环境...

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo    Python 未安装，正在自动下载 Python 3.12...
    echo    下载约 25MB，请稍候...

    :: Download Python embeddable package
    powershell -Command "& {Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip' -OutFile '%TEMP%\python.zip'}"

    if %errorlevel% neq 0 (
        echo ❌ 自动下载失败！
        echo    请手动下载: https://www.python.org/downloads/
        echo    安装时请勾选 "Add Python to PATH"
        pause
        exit /b 1
    )

    :: Extract to a local folder
    powershell -Command "& {Expand-Archive -Path '%TEMP%\python.zip' -DestinationPath '%~dp0\python-embed' -Force}"

    :: Create a helper to use this python
    echo @echo off > python-run.cmd
    echo %~dp0python-embed\python.exe %%* >> python-run.cmd

    :: Fix _pth file for pip support
    if exist "%~dp0python-embed\python312._pth" (
        powershell -Command "& {(Get-Content '%~dp0python-embed\python312._pth') -replace '#import site','import site' | Set-Content '%~dp0python-embed\python312._pth'}"
    )

    :: Download get-pip.py and install pip
    powershell -Command "& {Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\get-pip.py'}"
    %~dp0python-embed\python.exe "%TEMP%\get-pip.py"

    set PYTHON=%~dp0python-embed\python.exe
    echo    Python 3.12 已安装到本地文件夹
) else (
    set PYTHON=python
    python --version
)

:: ── Step 2: Install uv (fast package manager) ──
echo.
echo [2/4] 安装 uv (高速包管理器)...
where uv >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "& {Invoke-WebRequest -Uri 'https://astral.sh/uv/install.ps1' -OutFile '%TEMP%\install-uv.ps1'; & '%TEMP%\install-uv.ps1'}" >nul 2>&1
    :: Add uv to PATH for this session
    set PATH=%USERPROFILE%\.cargo\bin;%PATH%
)
echo    uv 就绪

:: ── Step 3: Build exe with uv ──
echo.
echo [3/3] 正在打包为 exe（首次会下载依赖，约 2-3 分钟）...

:: Use uv to create env and install deps without venv
uv venv --python 3.12 >nul 2>&1
uv pip install -r requirements.txt --python 3.12 >nul 2>&1

:: Run pyinstaller via uv
uv run pyinstaller build.spec --noconfirm 2>&1

if %errorlevel% neq 0 (
    echo ❌ 打包失败
    echo.
    echo 请尝试手动方式：
    echo 1. 安装 Python 3.8+ (https://www.python.org/downloads/)
    echo 2. 双击 run.bat 直接运行（不打包）
    pause
    exit /b 1
)

echo.
echo ╔══════════════════════════════════════════════╗
echo ║          ✅ 打包成功！                          ║
echo ║                                               ║
echo ║  可执行文件位于:                               ║
echo ║      %~dp0dist\PhotoCleaner\PhotoCleaner.exe   ║
echo ║                                               ║
echo ║  直接双击运行即可，然后打开浏览器访问:          ║
echo ║      http://localhost:5800                     ║
echo ║                                               ║
echo ║  你也可以直接双击 run.bat（不打包）来运行       ║
echo ╚══════════════════════════════════════════════╝
echo.

pause
