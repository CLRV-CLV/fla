@echo off
chcp 65001 >nul
title FLA 桌面客户端一键打包 EXE
echo ===================================================
echo     FLA 桌面助手 - 一键编译生成独立 EXE 安装包
echo ===================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 系统未检测到 Python 运行环境！
    pause
    exit /b 1
)

echo [1/3] 安装打包工具 PyInstaller 与运行依赖...
pip install pyinstaller pywin32 pystray pillow requests -q

echo.
echo [2/3] 正在使用 PyInstaller 编译 EXE (耗时约 30-60 秒)...
python build_exe.py

if %errorlevel% neq 0 (
    echo.
    echo [打包失败] 请检查上方报错信息。
    pause
    exit /b 1
)

echo.
echo ===================================================
echo [3/3] 打包成功！
echo 生成的独立 EXE 位于: desktop\dist\FLA-Desktop.exe
echo 您可以直接复制到任意 Windows 电脑双击运行！
echo ===================================================
pause
