@echo off
chcp 65001 >nul
title 大理大学第一附属医院印章管理系统

echo ============================================================
echo   大理大学第一附属医院印章管理系统 v3.6
echo   Python Flask + SQLite 后端
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [OK] Python 已就绪

:: Check and install Flask
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装 Flask...
    pip install flask -q
    if errorlevel 1 (
        echo [错误] Flask 安装失败，请手动执行: pip install flask
        pause
        exit /b 1
    )
    echo [OK] Flask 安装完成
) else (
    echo [OK] Flask 已安装
)

:: Kill any existing server
echo [检查] 关闭旧的服务进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5100.*LISTENING" 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
    echo [OK] 已关闭旧进程 PID:%%a
)
timeout /t 1 /nobreak >nul

echo [启动] 正在启动服务器...
echo.

:: Start server and open browser
start "" http://localhost:5100
python server.py

pause
