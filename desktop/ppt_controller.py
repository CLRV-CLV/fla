"""FLA Desktop - 本地 PPT/WPS 播放器自动识别与 COM 级放映控制器 (v1.28)
功能：
1. 自动探测系统默认的 PPT 播放器 / 办公套件 (Microsoft Office PowerPoint 或 金山 WPS 演示)
2. 通过 Windows COM 接口深度挂接放映窗口 (SlideShowWindow)
3. 实时毫秒级精准追踪当前 PPT 放映页码 (View.CurrentShowPosition) 与总页数
4. 支持双向驱动：键盘/翻页笔翻页时回调通知，亦可接收手机遥控器或 FLA 悬浮栏指令执行 Next() / Previous() / Goto()
5. 驱动「画布随 PPT 移动」：向绘图层分发页码变更事件，让各页批注与对应幻灯片深度绑定
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger("fla.ppt_ctrl")


class PPTController:
    def __init__(self):
        self.app_name = "unknown"  # "powerpoint", "wps", "system"
        self.app_com = None
        self.presentation = None
        self.slideshow_window = None
        self.current_page = 1
        self.total_pages = 1
        self.is_playing = False

        self.on_page_change_callbacks: List[Callable[[int, int], None]] = []
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._is_windows = platform.system().lower() == "windows"

    def detect_default_player(self) -> str:
        """识别系统默认的 PPT 播放器 (PowerPoint 或 WPS)"""
        if not self._is_windows:
            return "system"

        try:
            import winreg

            # 1. 检查 UserChoice (.pptx 扩展名用户关联)
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pptx\UserChoice"
                ) as key:
                    prog_id, _ = winreg.QueryValueEx(key, "ProgId")
                    prog_lower = (prog_id or "").lower()
                    if "powerpoint" in prog_lower:
                        self.app_name = "powerpoint"
                        return "powerpoint"
                    if "wps" in prog_lower or "wpp" in prog_lower:
                        self.app_name = "wps"
                        return "wps"
            except FileNotFoundError:
                pass

            # 2. 检查 HKEY_CLASSES_ROOT\\.pptx
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r".pptx") as key:
                    assoc, _ = winreg.QueryValueEx(key, "")
                    assoc_lower = (assoc or "").lower()
                    if "powerpoint" in assoc_lower:
                        self.app_name = "powerpoint"
                        return "powerpoint"
                    if "wps" in assoc_lower or "wpp" in assoc_lower:
                        self.app_name = "wps"
                        return "wps"
            except FileNotFoundError:
                pass

            # 3. 检查已安装软件
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application") as key:
                    self.app_name = "powerpoint"
                    return "powerpoint"
            except FileNotFoundError:
                pass

            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"KWPP.Application") as key:
                    self.app_name = "wps"
                    return "wps"
            except FileNotFoundError:
                pass

        except Exception as e:
            logger.debug(f"探测默认 PPT 播放器异常: {e}")

        self.app_name = "powerpoint"
        return "powerpoint"

    def open_presentation(self, file_path: str, auto_slideshow: bool = True) -> bool:
        """在本地调用 PowerPoint 或 WPS 打开并播放幻灯片"""
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            logger.error(f"课件文件不存在: {abs_path}")
            return False

        player = self.detect_default_player()
        logger.info(f"正在使用系统默认办公套件 [{player}] 打开: {abs_path}")

        if not self._is_windows:
            # 跨平台降级: 使用系统默认关联打开
            try:
                if platform.system() == "Darwin":
                    subprocess.run(["open", abs_path])
                else:
                    subprocess.run(["xdg-open", abs_path])
                self.is_playing = True
                return True
            except Exception as e:
                logger.error(f"非 Windows 打开失败: {e}")
                return False

        # Windows 原生 COM 自动化
        try:
            import win32com.client

            if player == "wps":
                # WPS 演示 COM ProgID: kwpp.application 或 wpp.application
                try:
                    self.app_com = win32com.client.Dispatch("kwpp.application")
                except Exception:
                    self.app_com = win32com.client.Dispatch("wpp.application")
            else:
                # Microsoft Office PowerPoint
                self.app_com = win32com.client.Dispatch("PowerPoint.Application")

            self.app_com.Visible = True
            # 打开演示文稿
            self.presentation = self.app_com.Presentations.Open(abs_path, WithWindow=True)
            self.total_pages = self.presentation.Slides.Count

            if auto_slideshow:
                # 开启放映
                settings = self.presentation.SlideShowSettings
                self.slideshow_window = settings.Run()
                self.is_playing = True
                self.current_page = 1
            else:
                self.is_playing = False

            # 启动页码监视循环
            self._start_tracking()
            return True

        except Exception as err:
            logger.warning(f"COM 打开课件失败, 尝试系统 Shell 降级: {err}")
            try:
                os.startfile(abs_path)
                self.is_playing = True
                self._start_tracking()
                return True
            except Exception as err2:
                logger.error(f"系统 Shell 打开亦失败: {err2}")
                return False

    def next_step(self):
        """动画步进 / 下一页"""
        if not self._is_windows or not self.slideshow_window:
            return
        try:
            self.slideshow_window.View.Next()
        except Exception as e:
            logger.debug(f"Next() 异常: {e}")

    def prev_step(self):
        """动画步退 / 上一页"""
        if not self._is_windows or not self.slideshow_window:
            return
        try:
            self.slideshow_window.View.Previous()
        except Exception as e:
            logger.debug(f"Previous() 异常: {e}")

    def goto_slide(self, index: int):
        """精准跳页"""
        if not self._is_windows or not self.slideshow_window:
            return
        try:
            target = max(1, min(self.total_pages, index))
            self.slideshow_window.View.GotoSlide(target)
            self.current_page = target
        except Exception as e:
            logger.debug(f"GotoSlide() 异常: {e}")

    def toggle_black_screen(self):
        """黑屏切换"""
        if not self._is_windows or not self.slideshow_window:
            return
        try:
            # 3 = ppSlideShowBlackScreen
            state = self.slideshow_window.View.State
            self.slideshow_window.View.State = 1 if state == 3 else 3
        except Exception:
            pass

    def exit_presentation(self):
        """退出放映"""
        self._running = False
        if not self._is_windows or not self.slideshow_window:
            return
        try:
            self.slideshow_window.View.Exit()
            self.is_playing = False
        except Exception:
            pass

    def add_page_listener(self, cb: Callable[[int, int], None]):
        """注册页码变更监听器 (驱动画布自动随页切换)"""
        self.on_page_change_callbacks.append(cb)

    def _start_tracking(self):
        self._running = True
        self._poll_thread = threading.Thread(target=self._tracking_loop, daemon=True)
        self._poll_thread.start()

    def _tracking_loop(self):
        """高灵敏轮询当前 PPT 放映窗口的页码变动"""
        last_page = -1
        while self._running:
            try:
                if self._is_windows and self.slideshow_window:
                    try:
                        cur = self.slideshow_window.View.CurrentShowPosition
                        if cur != last_page:
                            self.current_page = cur
                            last_page = cur
                            # 通知所有监听器: 画布自动翻页！
                            for cb in self.on_page_change_callbacks:
                                try:
                                    cb(self.current_page, self.total_pages)
                                except Exception:
                                    pass
                    except Exception:
                        # 放映窗口可能已关闭
                        pass
            except Exception:
                pass
            time.sleep(0.12)  # 120ms 灵敏检测


ppt_controller = PPTController()
