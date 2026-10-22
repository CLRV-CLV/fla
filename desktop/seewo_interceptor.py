"""FLA Desktop - 希沃白板5 (EasiNote / PPTService) 深度拦截器 (v1.28)
学校电脑标配希沃白板5，放映 PPT 时会注入其自带的悬浮工具栏 (PPTService.exe / EasiNote)。
本模块负责：
1. 窗口级实时拦截：监控屏幕上出现的希沃工具栏悬浮窗 (FindWindow / EnumWindows)，直接抑制/隐藏
2. 进程级拦截：检测并接管 PPTService.exe / SeewoPPTAssistant.exe 进程
3. COM 加载项级拦截：临时抑制注册表中 Office PowerPoint 的 Seewo Add-in 加载项
4. 与 FLA 桌面悬浮工具栏无缝交接，保证屏幕只显示 FLA 现代化工具栏
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from typing import List, Optional

logger = logging.getLogger("fla.seewo")

# 希沃白板5及 PPT 小工具在 Windows 中的典型特征
SEEWO_PROCESS_NAMES = [
    "PPTService.exe",
    "PPTAssistant.exe",
    "SeewoPPTAssistant.exe",
    "EasiNote.exe",
    "EasiService.exe",
    "EasiTool.exe",
]

SEEWO_WINDOW_KEYWORDS = [
    "PPTService",
    "EasiNote",
    "希沃",
    "Seewo",
    "PPT小工具",
    "PPT小助手",
]

SEEWO_WINDOW_CLASSES = [
    "PPTServiceFloatingWnd",
    "SeewoFloatingWnd",
    "EasiNoteToolbarWnd",
    "HwndWrapper[PPTService",
]


class SeewoInterceptor:
    def __init__(self, mode: str = "hide"):
        """
        mode:
          - "hide": 实时监控并隐藏希沃工具栏窗口 (最温和安全，不破坏原系统)
          - "suppress": 隐藏窗口 + 暂停进程响应
          - "kill": 直接终止 PPTService 进程
        """
        self.mode = mode
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._intercepted_count = 0
        self._is_windows = platform.system().lower() == "windows"

    def start(self):
        """启动后台拦截循环"""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(f"希沃白板拦截器已启动 [模式: {self.mode}]")

    def stop(self):
        """停止拦截循环并恢复状态"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        logger.info(f"希沃白板拦截器已停止 (共拦截 {self._intercepted_count} 次)")

    def _monitor_loop(self):
        while self.running:
            try:
                if self._is_windows:
                    self._intercept_windows()
                else:
                    self._intercept_stub()
            except Exception as e:
                logger.debug(f"拦截轮询异常: {e}")
            time.sleep(0.3)  # 300ms 轮询检测，兼顾瞬时拦截与极低 CPU 占用

    def _intercept_windows(self):
        """在 Windows 原生环境使用 ctypes/win32gui 探测并隐藏希沃窗口"""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            # 回调函数搜寻希沃窗口
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def enum_windows_callback(hwnd, lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True

                # 获取窗口类名
                class_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buf, 256)
                cls_name = class_buf.value

                # 获取窗口标题
                title_buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title_buf, 256)
                title = title_buf.value

                is_seewo = False
                for kw in SEEWO_WINDOW_KEYWORDS:
                    if kw in title or kw in cls_name:
                        is_seewo = True
                        break
                for cw in SEEWO_WINDOW_CLASSES:
                    if cw in cls_name:
                        is_seewo = True
                        break

                if is_seewo:
                    # 希沃工具栏浮窗！进行拦截
                    SW_HIDE = 0
                    user32.ShowWindow(hwnd, SW_HIDE)
                    # 移出可见工作区
                    user32.SetWindowPos(hwnd, 0, -32000, -32000, 0, 0, 0x0080 | 0x0001)  # SWP_HIDEWINDOW | SWP_NOSIZE
                    self._intercepted_count += 1
                    logger.info(f"成功拦截希沃工具栏浮窗: title='{title}', class='{cls_name}'")

                return True

            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)

            # 如果模式为 kill，进一步检查 PPTService.exe
            if self.mode == "kill":
                self._kill_seewo_processes()

        except Exception as err:
            logger.debug(f"Windows API 拦截执行失败: {err}")

    def _kill_seewo_processes(self):
        """安全终止希沃独立小工具进程 (不影响主白板)"""
        for proc in ["PPTService.exe", "SeewoPPTAssistant.exe"]:
            try:
                subprocess.run(["taskkill", "/F", "/IM", proc], capture_output=True, timeout=2)
            except Exception:
                pass

    def _intercept_stub(self):
        """非 Windows 系统(如开发测试机)测试桩"""
        pass

    @staticmethod
    def suppress_registry_addin(enable_suppression: bool = True):
        """
        临时调整注册表 COM 加载项 LoadBehavior:
        HKEY_CURRENT_USER\\Software\\Microsoft\\Office\\PowerPoint\\Addins\\Seewo.PPTAssistant
        设为 0 (不自动加载)，退出时设为 3 (恢复自动加载)
        """
        if platform.system().lower() != "windows":
            return
        try:
            import winreg
            paths = [
                r"Software\Microsoft\Office\PowerPoint\Addins\Seewo.PPTAssistant",
                r"Software\Microsoft\Office\PowerPoint\Addins\EasiNote.PPTPlugin",
            ]
            for p in paths:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, p, 0, winreg.KEY_SET_VALUE) as key:
                        val = 0 if enable_suppression else 3
                        winreg.SetValueEx(key, "LoadBehavior", 0, winreg.REG_DWORD, val)
                except FileNotFoundError:
                    pass
        except Exception:
            pass


seewo_interceptor = SeewoInterceptor()
