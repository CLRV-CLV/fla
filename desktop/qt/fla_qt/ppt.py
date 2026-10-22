"""PowerPoint / WPS 放映联动 (COM 自动化)
- 每 250ms 检测是否有幻灯片正在放映, 读取 当前页 / 总页数 / 放映窗口位置 / 文件路径
- 翻页走 COM View.Next()/Previous(): 与在 PPT 里点击完全一样, 会逐个播放动画
- 所有 COM 调用都在本线程 (STA) 内执行, UI 线程通过 command() 投递指令
"""
from __future__ import annotations

import queue
import sys
import time

from PyQt5.QtCore import QThread, pyqtSignal

PROGIDS = [("PowerPoint.Application", "PowerPoint"), ("Kwpp.Application", "WPS"), ("KWPP.Application", "WPS")]


class PPTWatcher(QThread):
    # dict: {active, app, path, page, total, rect:(l,t,w,h) 物理像素 或 None}
    state = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.q: queue.Queue = queue.Queue()
        self.running = True
        self.last = None

    def command(self, cmd: str, arg=None):
        self.q.put((cmd, arg))

    def stop(self):
        self.running = False

    def run(self):
        if sys.platform != "win32":
            return
        try:
            import pythoncom
            import win32com.client
            import win32gui
        except Exception:
            return
        pythoncom.CoInitialize()
        apps = {}
        idle = 0
        while self.running:
            info = {"active": False}
            view = None
            try:
                for progid, name in PROGIDS:
                    app = apps.get(progid)
                    if app is None:
                        try:
                            app = win32com.client.GetActiveObject(progid)
                            apps[progid] = app
                        except Exception:
                            continue
                    try:
                        n = app.SlideShowWindows.Count
                    except Exception:
                        apps.pop(progid, None)       # 程序已退出
                        continue
                    if n < 1:
                        continue
                    ssw = app.SlideShowWindows(1)
                    view = ssw.View
                    pres = ssw.Presentation
                    try:
                        page = int(view.CurrentShowPosition)
                    except Exception:
                        page = int(view.Slide.SlideIndex)
                    total = int(pres.Slides.Count)
                    rect = None
                    try:
                        hwnd = int(ssw.HWND)
                        l, t, r, b = win32gui.GetWindowRect(hwnd)
                        rect = (l, t, r - l, b - t)
                    except Exception:
                        pass
                    try:
                        path = str(pres.FullName)
                    except Exception:
                        path = str(pres.Name)
                    try:
                        st = int(view.State)        # 1 运行 2 暂停 3 黑屏 4 白屏 5 完成
                    except Exception:
                        st = 1
                    info = {"active": st != 5 or page <= total, "app": name, "path": path, "page": page,
                            "total": total, "rect": rect, "black": st in (3, 4)}
                    break
            except Exception:
                info = {"active": False}
                view = None
            # 执行排队的指令
            try:
                while True:
                    cmd, arg = self.q.get_nowait()
                    if view is None:
                        continue
                    try:
                        if cmd == "next":
                            view.Next()
                        elif cmd == "prev":
                            view.Previous()
                        elif cmd == "first":
                            view.First()
                        elif cmd == "last":
                            view.Last()
                        elif cmd == "goto" and arg:
                            view.GotoSlide(int(arg))
                        elif cmd == "black":
                            view.State = 1 if int(view.State) == 3 else 3
                        elif cmd == "white":
                            view.State = 1 if int(view.State) == 4 else 4
                        elif cmd == "exit":
                            view.Exit()
                    except Exception:
                        pass
                    idle = 0
            except queue.Empty:
                pass
            key = (info.get("active"), info.get("path"), info.get("page"), info.get("total"), info.get("rect"),
                   info.get("black"))
            if key != self.last:
                self.last = key
                self.state.emit(info)
            idle += 1
            time.sleep(0.12 if info.get("active") else 0.6)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
