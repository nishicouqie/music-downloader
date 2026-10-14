@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM ============================================================
REM  命令行版音乐下载器（界面版请直接双击「音乐下载器.exe」）
REM  1) 把歌名/网易云链接拖到本文件上
REM  2) 或双击本文件按提示输入
REM ============================================================

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py" )
if not defined PY (
    echo [x] 没找到 Python。日常使用请直接双击「音乐下载器.exe」，不需要 Python。
    pause & exit /b 1
)

echo ============================================================
echo   命令行版音乐下载器
echo ============================================================
echo.

if "%~1"=="" (
    echo 输入歌名（可带歌手），或直接粘贴网易云歌曲链接。
    echo 留空直接回车 = 进入批量模式（读取 歌单.txt）
    echo.
    set /p "Q=歌名: "
) else (
    set "Q=%~1"
)

if "%Q%"=="" (
    if not exist "歌单.txt" (
        echo [x] 没找到 歌单.txt，请新建一个，每行写一首歌。
        pause & exit /b 1
    )
    %PY% "%~dp0music_dl.py" --batch "歌单.txt"
) else (
    %PY% "%~dp0music_dl.py" "%Q%"
)

echo.
echo ------------------------------------------------------------
echo 完成。文件在输出目录（默认 D:\音乐下载，可在界面里改）
echo ------------------------------------------------------------
pause
