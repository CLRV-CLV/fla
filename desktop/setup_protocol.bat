@echo off
chcp 65001 >nul
title FLA 协议注册工具 (fla://)
echo 正在注册 fla:// 浏览器一键唤醒协议...
python -c "from local_server import register_fla_protocol; register_fla_protocol()"
if %errorlevel% equ 0 (
    echo [成功] fla:// 协议注册完成！浏览器可直接唤醒本地应用。
) else (
    echo [提示] 如需管理员权限，请右键选择“以管理员身份运行”。
)
pause
