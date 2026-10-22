"""FLA Desktop - Windows 单文件可执行程序 (EXE) 打包脚本 (v1.28)
一键打包生成: dist/FLA-Desktop.exe
支持携带 Windows 清单 (as-invoker / UAC 正常权限), 内嵌图标与协议注册
"""
import os
import subprocess
import sys


def build_exe():
    print("=" * 60)
    print("正在构建 FLA-Desktop.exe (Windows 桌面端)")
    print("=" * 60)

    try:
        import PyInstaller
    except ImportError:
        print("未检测到 PyInstaller，正在安装…")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", "FLA-Desktop",
        "--add-data", "desktop:desktop",
        os.path.join(os.path.dirname(__file__), "main.py")
    ]
    print("执行命令:", " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n>>> 打包成功！输出目录: dist/FLA-Desktop")
    else:
        print("\n>>> 打包失败，退出码:", res.returncode)


if __name__ == "__main__":
    build_exe()
