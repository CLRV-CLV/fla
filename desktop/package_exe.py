"""FLA Desktop 单文件可执行程序 (FLA.exe) 构建脚本
将 Windows 原生 C# 桌面助手 (含 8307 本地HTTP桥接、希沃白板5拦截器、PPT/WPS COM控制器、现代Fluent UI与托盘)
构建为独立的 Windows 64位 原生 PE 可执行程序 (FLA.exe)。
- 彻底消除 "被解压软件识别为空压缩包" 的问题 (纯 PE 结构，无任何 PK 游离压缩标记)
- 零外部运行库依赖 (无需预装 Python，兼容所有标准 Windows 7/8/10/11 多媒体讲台电脑)
- 零误报木马：完全摒弃 shellcode/PEB 遍历与 hidden powershell dropper，采用标准 Win32 导入表 (IAT)
- 双击直接运行，自动常驻托盘，支持网页端一键唤起与自更新
"""
from __future__ import annotations

import sys
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DESKTOP_DIR))

from build_pe import build_native_executable

def build_exe():
    print("Executing standard PE builder...")
    return build_native_executable()

if __name__ == "__main__":
    build_exe()
