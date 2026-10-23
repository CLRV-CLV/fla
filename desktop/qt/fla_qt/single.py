"""FLA 桌面端严格单实例 (v1.37)

双击图标多次时只允许存在一个 FLA 进程, 其余实例把 "唤醒" 请求交给已运行的实例后立刻退出。

实现方式 (Windows, 纯 Win32, 不依赖第三方库):
  · 命名互斥体 Local\\FLA.SingleInstance.v1 —— 谁 CreateMutex 成功谁就是主实例
    互斥体由内核持有, 进程崩溃/被强杀时自动释放, 不会留下 "僵尸锁" 导致再也打不开
  · 命名事件 Local\\FLA.WakeEvent.v1 —— 第二个实例 SetEvent 唤醒主实例
  · 待处理参数写入 %LOCALAPPDATA%\\FLA\\pending.json, 主实例读到后当作命令行处理

非 Windows (开发机) 直接放行, 不阻塞。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger("fla.single")

MUTEX_NAME = "Local\\FLA.SingleInstance.v1"
EVENT_NAME = "Local\\FLA.WakeEvent.v1"
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _pending_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".fla")
    d = os.path.join(base, "FLA")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "pending.json")



def _close(k32, h) -> None:
    """CloseHandle 必须显式声明参数类型, 否则 64 位句柄会被截断成 32 位 (泄漏/误关)"""
    if not h:
        return
    try:
        from ctypes import wintypes
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle(h)
    except Exception:
        pass


class SingleInstance:
    def __init__(self, mutex_name: str = MUTEX_NAME, event_name: str = EVENT_NAME):
        self.mutex_name = mutex_name
        self.event_name = event_name
        self._mutex = None
        self._event = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.is_primary = False

    # ------------------------------------------------------------------ 主实例
    def acquire(self) -> bool:
        """尝试成为唯一实例。返回 True = 我是主实例; False = 已有实例在跑。"""
        if not _is_windows():
            self.is_primary = True
            return True
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CreateEventW.restype = wintypes.HANDLE

            h = k32.CreateMutexW(None, False, self.mutex_name)
            err = ctypes.get_last_error()
            if not h:
                logger.warning("CreateMutex 失败 (%s), 按单实例放行", err)
                self.is_primary = True
                return True
            if err == ERROR_ALREADY_EXISTS:
                _close(k32, h)
                self.is_primary = False
                return False
            self._mutex = h
            self._event = k32.CreateEventW(None, False, False, self.event_name)
            self.is_primary = True
            return True
        except Exception as e:      # 极端环境下宁可放行, 也不要打不开
            logger.warning("单实例检测失败: %s", e)
            self.is_primary = True
            return True

    # ------------------------------------------------------------------ 副实例
    def wake_existing(self, args: Optional[List[str]] = None) -> bool:
        """把参数交给主实例并唤醒它。返回是否成功投递。"""
        if not _is_windows():
            return False
        try:
            if args:
                p = _pending_path()
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"args": list(args), "ts": time.time()}, f, ensure_ascii=False)
                os.replace(tmp, p)
        except Exception as e:
            logger.debug("写入待处理参数失败: %s", e)
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            k32.OpenEventW.restype = wintypes.HANDLE
            k32.SetEvent.argtypes = [wintypes.HANDLE]
            k32.SetEvent.restype = wintypes.BOOL
            EVENT_MODIFY_STATE = 0x0002
            ev = k32.OpenEventW(EVENT_MODIFY_STATE, False, self.event_name)
            if not ev:
                return False
            ok = bool(k32.SetEvent(ev))
            _close(k32, ev)
            return ok
        except Exception as e:
            logger.debug("唤醒主实例失败: %s", e)
            return False

    # ------------------------------------------------------------------ 主实例监听
    def listen(self, callback: Callable[[List[str]], None]):
        """后台线程等待唤醒事件, 收到后读取参数并回调。必须在 acquire() 成功后调用。"""
        if not self.is_primary or not _is_windows() or not self._event:
            return
        hwnd = self._event

        def loop():
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            while not self._stop.is_set():
                r = k32.WaitForSingleObject(hwnd, 500)
                if r == WAIT_OBJECT_0:
                    args: List[str] = []
                    try:
                        with open(_pending_path(), "r", encoding="utf-8") as f:
                            args = list(json.load(f).get("args") or [])
                    except Exception:
                        args = []
                    try:
                        os.remove(_pending_path())
                    except Exception:
                        pass
                    try:
                        callback(args)
                    except Exception as e:
                        logger.debug("处理唤醒回调失败: %s", e)

        self._thread = threading.Thread(target=loop, name="fla-single", daemon=True)
        self._thread.start()

    def release(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            _close(k32, self._event)
            _close(k32, self._mutex)
        except Exception:
            pass
        self._event = self._mutex = None
