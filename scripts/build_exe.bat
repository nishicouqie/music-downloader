@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   打包「音乐下载器」为 exe
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [x] 没找到 Python，请先安装 Python 3 并勾选 Add to PATH
    pause & exit /b 1
)

echo [1/3] 检查依赖...
python -c "import mutagen" 2>nul || python -m pip install mutagen
python -c "import webview" 2>nul || python -m pip install pywebview
python -c "import PyInstaller" 2>nul || python -m pip install pyinstaller

echo.
echo [2/3] 生成图标...
python make_icon.py

echo.
echo [3/3] 开始打包（约 1 分钟）...
python -m PyInstaller --noconfirm --clean --windowed ^
  --name "音乐下载器" --icon icon.ico ^
  --add-data "ui;ui" --hidden-import clr ^
  --exclude-module tkinter --exclude-module numpy ^
  --exclude-module PIL --exclude-module matplotlib ^
  --exclude-module pandas --exclude-module unittest ^
  app.py
if errorlevel 1 (
    echo [x] 打包失败
    pause & exit /b 1
)

echo.
echo ============================================================
echo   打包完成： dist\音乐下载器\
echo.
echo   建议复制到普通目录再运行（不要放在特殊工作目录下）
echo ============================================================
pause
