@echo off
chcp 65001 >nul
title FLA 桌面客户端启动程序
echo ===================================================
echo     FLA 桌面助手 (希沃白板5拦截 + 画布随PPT移动)
echo ===================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 系统未检测到 Python 运行环境！
    echo 请访问 https://www.python.org 安装 Python 3.8+ 并勾选 Add to PATH。
    pause
    exit /b 1
)

echo [1/2] 正在检查依赖 (pywin32, pystray, pillow)...
pip install pywin32 pystray pillow requests -q >nul 2>&1

echo [2/2] 正在启动 FLA 桌面助手守护进程...
python main.py

pause
