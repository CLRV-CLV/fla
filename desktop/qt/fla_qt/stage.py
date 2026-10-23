"""放映舞台: 悬浮工具盒 / 全屏批注画布 / 笔与橡皮设置弹窗 / 计时器

关键安全设计: 画布窗口用 setMask 把工具盒、弹窗、计时器所在区域"挖空",
无论窗口叠放顺序如何, 这些区域的鼠标点击都穿透到下面的工具栏, 画笔不可能画到工具栏上。
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import time

from PyQt5.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QColor, QCursor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF, QRegion)
from PyQt5.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (BodyLabel, PrimaryPushButton, PushButton, SegmentedWidget, ToolTipFilter,
                            ToolTipPosition, TransparentToggleToolButton, TransparentToolButton)

from . import core
from .screen_layout import physical_rect_to_logical
from .touch_swipe import TouchSwipeTracker, classify_swipe

COLORS = ["#111111", "#E53935", "#1E88E5", "#43A047", "#FDD835", "#FB8C00", "#8E24AA", "#FFFFFF"]
PEN_WIDTHS = [("细", 3), ("中", 6), ("粗", 12)]
ERASER_SIZES = [("小", 12), ("中", 28), ("大", 60)]


def _screen_rect(screen=None, available=False):
    screen = screen or QApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 800, 600)
    return QRect(screen.availableGeometry() if available else screen.geometry())


def _screen_for_rect(rect):
    screen = QApplication.screenAt(rect.center()) if rect is not None and not rect.isNull() else None
    return screen or QApplication.primaryScreen()


def _screen_key(screen):
    return (screen.name() or str(id(screen))) if screen is not None else ""


def _clamp_rect(rect, bounds, margin=8):
    """Keep a floating widget fully reachable inside a screen/work area."""
    rect, bounds = QRect(rect), QRect(bounds)
    if bounds.isEmpty():
        return rect
    mx = min(max(0, int(margin)), max(0, (bounds.width() - 1) // 2))
    my = min(max(0, int(margin)), max(0, (bounds.height() - 1) // 2))
    width = min(max(1, rect.width()), max(1, bounds.width() - 2 * mx))
    height = min(max(1, rect.height()), max(1, bounds.height() - 2 * my))
    x = min(max(rect.x(), bounds.left() + mx), bounds.right() - mx - width + 1)
    y = min(max(rect.y(), bounds.top() + my), bounds.bottom() - my - height + 1)
    return QRect(x, y, width, height)


class _MouseHookPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MouseHookData(ctypes.Structure):
    _fields_ = [("pt", _MouseHookPoint), ("mouseData", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _NativeRect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _NativeRect),
                ("rcWork", _NativeRect), ("dwFlags", ctypes.c_ulong),
                ("szDevice", ctypes.c_wchar * 32)]


def _native_monitor_at(x, y):
    """Return (device name, physical bounds) for the monitor at a Win32 point."""
    if not core.IS_WIN:
        return None
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [_MouseHookPoint, ctypes.c_ulong]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfoEx)]
        user32.GetMonitorInfoW.restype = ctypes.c_int
        monitor = user32.MonitorFromPoint(_MouseHookPoint(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        rect = info.rcMonitor
        return info.szDevice.rstrip("\\0"), (
            int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))
    except Exception:
        return None


def _device_key(name):
    return str(name or "").replace("/", "\\").strip().rstrip("\\0").casefold().split("\\")[-1]


class _TouchSwipeHook(QObject):
    """Observe Windows' touch-generated mouse compatibility stream without consuming it."""
    gesture = pyqtSignal(int, int, int, int, float)
    _WH_MOUSE_LL = 14
    _WM_MOUSEMOVE = 0x0200
    _WM_LBUTTONDOWN = 0x0201
    _WM_LBUTTONUP = 0x0202
    _LLMHF_INJECTED = 0x00000001
    _SM_DIGITIZER = 94
    _NID_TOUCH_MASK = 0x00000003

    def __init__(self):
        super().__init__()
        self._hook = None
        self._callback = None
        self._user32 = None
        self._tracker = TouchSwipeTracker()
        self._touch_digitizer_present = False
        self.last_error = ""

    @classmethod
    def _has_touch_digitizer(cls, user32):
        try:
            user32.GetSystemMetrics.argtypes = [ctypes.c_int]
            user32.GetSystemMetrics.restype = ctypes.c_int
            metrics = int(user32.GetSystemMetrics(cls._SM_DIGITIZER))
            return bool(metrics & cls._NID_TOUCH_MASK)
        except Exception:
            return False

    def start(self):
        if not core.IS_WIN:
            return False
        if self._hook:
            return True
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._touch_digitizer_present = self._has_touch_digitizer(user32)
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, ctypes.c_void_p, ctypes.c_uint]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.UnhookWindowsHookEx.restype = ctypes.c_int
            kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            self._user32 = user32
            self._callback = callback_type(self._hook_proc)
            self._hook = user32.SetWindowsHookExW(
                self._WH_MOUSE_LL, self._callback, kernel32.GetModuleHandleW(None), 0)
            if not self._hook:
                self.last_error = f"SetWindowsHookExW failed: {ctypes.get_last_error()}"
                self._callback = None
                self._user32 = None
                return False
            self.last_error = ""
            return True
        except Exception as exc:
            self._hook = None
            self._callback = None
            self._user32 = None
            self.last_error = str(exc)
            return False

    def stop(self):
        if self._hook and self._user32:
            try:
                self._user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
        self._hook = None
        self._callback = None
        self._user32 = None
        self._tracker.reset()

    def _hook_proc(self, code, message, data_ptr):
        if code >= 0:
            try:
                msg = int(message)
                data = ctypes.cast(data_ptr, ctypes.POINTER(_MouseHookData)).contents
                point = (int(data.pt.x), int(data.pt.y))
                now = time.monotonic()
                if msg == self._WM_LBUTTONDOWN:
                    # A tablet's compatibility down event is often *not* tagged
                    # with MI_WP_SIGNATURE even when later events originate from touch.
                    # In that case accept unmarked drags only when Windows reports a
                    # touch digitizer, or when the OS marks the event as injected.
                    allow_unmarked = (self._touch_digitizer_present
                                      or bool(int(data.flags) & self._LLMHF_INJECTED))
                    self._tracker.begin(point, int(data.dwExtraInfo), now,
                                        allow_unmarked=allow_unmarked)
                elif msg == self._WM_MOUSEMOVE:
                    # Some touch drivers omit the touch signature on intermediate moves.
                    self._tracker.move(now)
                elif msg == self._WM_LBUTTONUP:
                    gesture = self._tracker.end(point, now)
                    if gesture:
                        self.gesture.emit(*gesture)
            except Exception:
                self._tracker.reset()
        if self._user32:
            return self._user32.CallNextHookEx(self._hook, code, message, data_ptr)
        return 0

    def __del__(self):
        self.stop()


class _Card(QWidget):
    """白色圆角浮动卡片 (可拖动); 移动/显示/隐藏时发 changed 信号, 让画布重算挖空区域"""
    changed = pyqtSignal()

    def __init__(self, radius=14, parent=None, draggable=True):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._r = radius
        self._drag = None
        self._draggable = draggable

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.setBrush(QColor(255, 255, 255, 248))
        p.drawRoundedRect(rect, self._r, self._r)

    def mousePressEvent(self, e):
        if self._draggable and e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def moveEvent(self, e):
        self.changed.emit()

    def showEvent(self, e):
        self.changed.emit()

    def hideEvent(self, e):
        self.changed.emit()

    def resizeEvent(self, e):
        self.changed.emit()


# ================================================================== 笔迹落盘
def _ink_dir():
    d = os.path.join(core.data_dir(), "ink")
    os.makedirs(d, exist_ok=True)
    return d


def save_ink(name: str, pages: dict):
    data = {}
    for k, strokes in pages.items():
        if strokes:
            data[str(k)] = [{**{kk: v for kk, v in st.items() if kk != "pts"},
                             "pts": [[round(p.x(), 1), round(p.y(), 1)] for p in st["pts"]]} for st in strokes]
    try:
        with open(os.path.join(_ink_dir(), name + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_ink(name: str) -> dict:
    try:
        with open(os.path.join(_ink_dir(), name + ".json"), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for k, strokes in data.items():
        key = int(k) if k.isdigit() else k
        out[key] = [{**st, "pts": [QPointF(x, y) for x, y in st.get("pts", [])]} for st in strokes]
    return out


def _icon(kind: str) -> QIcon:
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if kind == "cursor":
        p.setPen(QPen(QColor("#111111"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(QPolygonF([QPointF(14, 8), QPointF(14, 38), QPointF(22, 31), QPointF(28, 42),
                                 QPointF(33, 40), QPointF(27, 29), QPointF(37, 29)]))
    p.end()
    return QIcon(pm)


class Grip(QLabel):
    """显式拖动把手; 子控件接管鼠标时仍能移动完整工具栏/最小化浮窗。"""

    def __init__(self, parent, window=None):
        super().__init__(parent)
        self.window_to_move = window or parent.window()
        self.setFixedSize(22, 40)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("按住拖动工具栏")
        self._drag_offset = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPos() - self.window_to_move.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.window_to_move.move(e.globalPos() - self._drag_offset)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_offset = None
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#9e9e9e"))
        for row in range(3):
            for col in range(2):
                p.drawEllipse(QPointF(7 + col * 8, 12 + row * 8), 2.2, 2.2)


# ================================================================== 画布
def _densify(pts, step=3.0):
    """在稀疏点之间插值, 保证像素橡皮切割精确"""
    if len(pts) < 2:
        return list(pts)
    out = [pts[0]]
    for b in pts[1:]:
        a = out[-1]
        d = math.hypot(b.x() - a.x(), b.y() - a.y())
        n = int(d // step)
        for i in range(1, n):
            t = i / n
            out.append(QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t))
        out.append(b)
    return out


class Overlay(QWidget):
    tool_changed = pyqtSignal(str)
    key_command = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.tool = "mouse"
        self.color = QColor(COLORS[1])
        self.width_ = PEN_WIDTHS[0][1]
        self.eraser_r = ERASER_SIZES[1][1]
        self.eraser_mode = "object"     # object | pixel
        self.page = 1
        self.board = False
        self.pages: dict = {}
        self.cur = None
        self.remote: dict = {}           # 手机端进行中的笔画 id -> stroke
        self.laser: list = []
        self.cursor_pt = None
        self.holes: list = []            # 需挖空的全局矩形 (工具栏等)
        self.area = None                 # PPT 模式: 放映窗口区域 (逻辑坐标), None = 主屏
        self.laser_timer = QTimer(self, interval=30, timeout=self._laser_tick)
        self.setMouseTracking(True)

    # ---- 数据
    def strokes(self) -> list:
        return self.pages.setdefault("board" if self.board else self.page, [])

    def has_ink(self) -> bool:
        return bool(self.strokes()) or bool(self.laser)

    # ---- 状态
    def set_tool(self, tool: str):
        self.tool = tool
        self.setCursor(Qt.CrossCursor if tool in ("pen", "marker") else
                       Qt.BlankCursor if tool == "eraser" else Qt.ArrowCursor)
        self.sync()

    def set_board(self, on: bool):
        self.board = on
        if on and self.tool == "mouse":
            self.tool = "pen"
        self.sync()

    def set_page(self, page: int):
        self.page = max(1, page)
        self.sync()

    def set_holes(self, rects):
        self.holes = [QRect(r) for r in rects]
        self._apply_mask()

    def _apply_mask(self):
        if not self.isVisible():
            return
        g = self.geometry()
        region = QRegion(QRect(0, 0, g.width(), g.height()))
        for r in self.holes:
            region = region.subtracted(QRegion(r.translated(-g.x(), -g.y()).adjusted(-4, -4, 4, 4)))
        self.setMask(region)

    def sync(self):
        """根据工具决定: 隐藏 / 显示并穿透 / 显示并接收输入"""
        geo = self.area or _screen_rect()
        if self.geometry() != geo:
            self.setGeometry(geo)
        passthrough = self.tool == "mouse" and not self.board
        if passthrough and not self.has_ink():
            if self.isVisible():
                self.hide()
            return
        want = bool(self.windowFlags() & Qt.WindowTransparentForInput)
        if want != passthrough or not self.isVisible():
            self.setWindowFlag(Qt.WindowTransparentForInput, passthrough)
            self.show()
        self._apply_mask()
        if not passthrough:
            self.raise_()
            self.activateWindow()
        self.update()

    def clear(self):
        self.strokes().clear()
        self.sync()

    def undo(self):
        if self.strokes():
            self.strokes().pop()
            self.sync()

    # ---- 绘制
    def paint_ink(self, p: QPainter, scale: float = 1.0):
        """画全部笔迹 (也供投屏合成使用)"""
        p.save()
        p.scale(scale, scale)
        for s in self.strokes() + ([self.cur] if self.cur else []):
            self._draw_stroke(p, s)
        now = time.time()
        for (pt, t) in self.laser:
            a = max(0.0, 1 - (now - t) / 0.5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 30, 30, int(210 * a)))
            r = 4 + 6 * a
            p.drawEllipse(QPointF(pt), r, r)
        p.restore()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.board:
            p.fillRect(self.rect(), QColor("#FFFFFF"))
        elif self.tool != "mouse":
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))   # 近乎透明但可接收鼠标
        self.paint_ink(p)
        if self.tool == "eraser" and self.cursor_pt is not None:
            p.setPen(QPen(QColor(80, 80, 80), 1.5, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 120))
            p.drawEllipse(QPointF(self.cursor_pt), self.eraser_r, self.eraser_r)

    @staticmethod
    def _draw_stroke(p: QPainter, s: dict):
        pts = s["pts"]
        if not pts:
            return
        c = QColor(s["color"])
        if s["tool"] == "marker":
            c.setAlpha(90)
        p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        if len(pts) == 1:
            p.drawPoint(pts[0])
            return
        path = QPainterPath(QPointF(pts[0]))
        for i in range(1, len(pts) - 1):
            mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2)
            path.quadTo(QPointF(pts[i]), mid)
        path.lineTo(QPointF(pts[-1]))
        p.drawPath(path)

    # ---- 本地输入
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.set_tool("mouse")
            self.tool_changed.emit("mouse")
            return
        pos = QPointF(e.pos())
        if self.tool in ("pen", "marker"):
            w = self.width_ * (3 if self.tool == "marker" else 1)
            self.cur = {"tool": self.tool, "color": self.color.name(), "w": w, "pts": [pos]}
        elif self.tool == "eraser":
            self.erase(pos, self.eraser_r, self.eraser_mode)
        elif self.tool == "laser":
            self.laser_at(pos)

    def mouseMoveEvent(self, e):
        pos = QPointF(e.pos())
        if self.tool == "eraser":
            self.cursor_pt = pos
            if e.buttons() & Qt.LeftButton:
                self.erase(pos, self.eraser_r, self.eraser_mode)
            self.update()
            return
        if self.tool == "laser":
            self.laser_at(pos)
            return
        if self.cur is not None and e.buttons() & Qt.LeftButton:
            self.cur["pts"].append(pos)
            self.update()

    def mouseReleaseEvent(self, e):
        if self.cur is not None:
            self.cur["pts"] = _densify(self.cur["pts"])
            self.strokes().append(self.cur)
            self.cur = None
            self.update()

    def leaveEvent(self, e):
        self.cursor_pt = None
        self.update()

    def erase(self, pos: QPointF, r: float, mode: str):
        strokes = self.strokes()
        changed = False
        out = []
        for s in strokes:
            rr = r + s["w"] / 2
            hit = [math.hypot(pt.x() - pos.x(), pt.y() - pos.y()) < rr for pt in s["pts"]]
            if not any(hit):
                out.append(s)
                continue
            changed = True
            if mode == "object":
                continue
            run = []
            for pt, h in zip(s["pts"], hit):     # 像素橡皮: 把笔画切成剩余片段
                if h:
                    if len(run) > 1:
                        out.append({**s, "pts": run})
                    run = []
                else:
                    run.append(pt)
            if len(run) > 1:
                out.append({**s, "pts": run})
        if changed:
            strokes[:] = out
            self.update()

    def laser_at(self, pos):
        self.laser.append((QPointF(pos), time.time()))
        if not self.laser_timer.isActive():
            self.laser_timer.start()
        if not self.isVisible():
            self.sync()
        self.update()

    def _laser_tick(self):
        now = time.time()
        self.laser = [x for x in self.laser if now - x[1] < 0.5]
        if not self.laser:
            self.laser_timer.stop()
            if self.tool == "mouse" and not self.board:
                self.sync()
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            if self.board:
                self.board = False
            self.set_tool("mouse")
            self.tool_changed.emit("mouse")
        elif k == Qt.Key_Z and e.modifiers() & Qt.ControlModifier:
            self.undo()
        elif k in (Qt.Key_Right, Qt.Key_Down, Qt.Key_PageDown, Qt.Key_Space, Qt.Key_Return):
            self.key_command.emit("next")
        elif k in (Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Backspace):
            self.key_command.emit("prev")

    # ---- 手机端批注 (坐标为 0~1 归一化)
    def _np(self, xy):
        """手机看到的是整块主屏截图 → 归一化坐标按主屏换算, 再转成本窗口坐标
        (PPT 模式下本窗口只覆盖放映区域, 不能直接按窗口大小换算, 否则位置错开)"""
        sr = _screen_rect()
        g = self.area or _screen_rect()
        return QPointF(sr.x() + float(xy[0]) * sr.width() - g.x(), sr.y() + float(xy[1]) * sr.height() - g.y())

    def remote_ink(self, d: dict):
        sid = str(d.get("id") or "")
        pts = [self._np(p) for p in (d.get("pts") or []) if isinstance(p, (list, tuple)) and len(p) == 2]
        g = _screen_rect()
        s = self.remote.get(sid)
        if s is None:
            tool = "marker" if d.get("tool") == "marker" else "pen"
            w = max(1.0, float(d.get("w") or 0.004) * g.width())
            s = {"tool": tool, "color": str(d.get("color") or "#E53935")[:9], "w": w, "pts": []}
            self.remote[sid] = s
            self.strokes().append(s)
        s["pts"].extend(pts)
        if d.get("end"):
            s["pts"] = _densify(s["pts"])
            self.remote.pop(sid, None)
            if len(self.remote) > 50:
                self.remote.clear()
        self.sync()

    def remote_erase(self, d: dict):
        g = _screen_rect()
        r = max(4.0, float(d.get("r") or 0.02) * g.width())
        self.erase(self._np((d.get("x", 0), d.get("y", 0))), r, "pixel" if d.get("mode") == "pixel" else "object")
        self.sync()

    def remote_laser(self, d: dict):
        self.laser_at(self._np((d.get("x", 0), d.get("y", 0))))


# ================================================================== 笔 / 橡皮 设置弹窗
class PenPopup(_Card):
    def __init__(self, overlay: Overlay):
        super().__init__(12, draggable=False)
        self.ov = overlay
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        lay.addWidget(BodyLabel("颜色"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.swatches = []
        for c in COLORS:
            b = QFrame()
            b.setFixedSize(28, 28)
            b.setCursor(Qt.PointingHandCursor)
            b.mousePressEvent = (lambda _e, c=c: self._color(c))
            self.swatches.append((c, b))
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(BodyLabel("粗细"))
        self.seg = SegmentedWidget(self)
        for label, w in PEN_WIDTHS:
            self.seg.addItem(str(w), label, onClick=lambda w=w: setattr(self.ov, "width_", w))
        self.seg.setCurrentItem(str(self.ov.width_))
        lay.addWidget(self.seg)
        self._paint_swatches()
        self.adjustSize()

    def _paint_swatches(self):
        cur = self.ov.color.name().lower()
        for c, b in self.swatches:
            ring = "3px solid #111" if c.lower() == cur else "1px solid #bbb"
            if c.lower() == cur and c.lower() == "#111111":
                ring = "3px solid #1E88E5"
            b.setStyleSheet(f"background:{c};border:{ring};border-radius:14px;")

    def _color(self, c):
        self.ov.color = QColor(c)
        self._paint_swatches()


class EraserPopup(_Card):
    def __init__(self, overlay: Overlay):
        super().__init__(12, draggable=False)
        self.ov = overlay
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        lay.addWidget(BodyLabel("橡皮类型"))
        self.mode = SegmentedWidget(self)
        self.mode.addItem("object", "对象橡皮（整笔擦除）", onClick=lambda: setattr(self.ov, "eraser_mode", "object"))
        self.mode.addItem("pixel", "像素橡皮（擦到哪算哪）", onClick=lambda: setattr(self.ov, "eraser_mode", "pixel"))
        self.mode.setCurrentItem(self.ov.eraser_mode)
        lay.addWidget(self.mode)
        lay.addWidget(BodyLabel("大小"))
        self.size = SegmentedWidget(self)
        for label, r in ERASER_SIZES:
            self.size.addItem(str(r), label, onClick=lambda r=r: setattr(self.ov, "eraser_r", r))
        self.size.setCurrentItem(str(self.ov.eraser_r))
        lay.addWidget(self.size)
        clr = PushButton(FIF.DELETE, "清除本页全部笔迹")
        clr.clicked.connect(lambda: (self.ov.clear(), self.hide()))
        lay.addWidget(clr)
        self.adjustSize()


# ================================================================== iOS 式时间滚轮
class TimeWheel(QWidget):
    """iOS TimerPicker 风格的三列滚轮 (时 / 分 / 秒), 纯 QPainter 自绘。

    · 拖动: 按住上下滑动, 松手带惯性并吸附到整格
    · 滚轮: 鼠标滚轮逐格滚动 (Shift 加速)
    · 键盘: ↑↓ 调当前列, ←→ 切列, PageUp/PageDown ±5
    · 外部 ±: step(unit, delta) 供「时−/时＋」这类按钮调用
    """

    valueChanged = pyqtSignal(int)

    ITEM_H = 40
    VISIBLE = 3          # 中心格左右各显示几个

    UNITS = ("h", "m", "s")
    MODS = (24, 60, 60)

    def __init__(self, parent=None, value: int = 300):
        super().__init__(parent)
        self._pos = [0.0, 0.0, 0.0]
        self._vel = [0.0, 0.0, 0.0]
        self._lock = False
        self._focus = 1
        self._drag = None
        self._anim = QTimer(self, interval=16, timeout=self._animate)
        self.setMinimumWidth(260)
        self.setFixedHeight(self.ITEM_H * 5 + 10)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.OpenHandCursor)
        self.setMouseTracking(True)
        self.setValue(value, emit=False)

    # ---------------- 数值 ----------------
    def value(self) -> int:
        h, m, s = (int(round(self._pos[i])) % self.MODS[i] for i in range(3))
        return h * 3600 + m * 60 + s

    def setValue(self, sec: int, emit: bool = True):
        sec = max(0, min(86399, int(sec or 0)))
        h, rest = divmod(sec, 3600)
        m, s = divmod(rest, 60)
        self._pos = [float(h), float(m), float(s)]
        self._vel = [0.0, 0.0, 0.0]
        self._anim.stop()
        self.update()
        if emit:
            self.valueChanged.emit(self.value())

    def setDisabled(self, flag: bool):
        self._lock = bool(flag)
        self.setCursor(Qt.ArrowCursor if flag else Qt.OpenHandCursor)
        self.update()

    def step(self, unit: str, delta: int):
        """外部 ± 按钮: unit = h/m/s, delta = ±1 (秒 ±10)"""
        if self._lock:
            return
        try:
            i = self.UNITS.index(unit)
        except ValueError:
            return
        amount = 3600 if i == 0 else (60 if i == 1 else 10)
        total = self.value() + int(delta) * amount
        total = 0 if total < 0 else (86399 if total > 86399 else total)
        self._focus = i
        self.setValue(total)

    # ---------------- 几何 ----------------
    def _col_rect(self, i: int) -> QRect:
        w = self.width() / 3.0
        return QRect(int(i * w), 0, int(w), self.height())

    def _col_at(self, x: int) -> int:
        return max(0, min(2, int(x / max(1, self.width() / 3.0))))

    # ---------------- 交互 ----------------
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._lock:
            return
        i = self._col_at(e.x())
        self._focus = i
        self._anim.stop()
        self._vel = [0.0, 0.0, 0.0]
        self._drag = {"col": i, "y": e.y(), "pos": self._pos[i], "t": time.time(), "v": 0.0}
        self.setCursor(Qt.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        col = self._drag["col"]
        now = time.time()
        dy = e.y() - self._drag["y"]
        newpos = self._drag["pos"] - dy / float(self.ITEM_H)
        dt = max(0.001, now - self._drag["t"])
        self._drag["v"] = (newpos - self._pos[col]) / dt
        self._pos[col] = newpos
        self._drag["t"] = now
        self._sync_emit()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        col = self._drag["col"]
        v = max(-12.0, min(12.0, self._drag.get("v", 0.0) * 0.06))
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)
        self._vel[col] = v if abs(v) > 0.6 else 0.0
        self._anim.start()
        QTimer.singleShot(240, self._settle_if_idle)

    def wheelEvent(self, e):
        if self._lock:
            return
        i = self._col_at(e.x())
        self._focus = i
        notches = e.angleDelta().y() / 120.0
        step = notches * (5 if e.modifiers() & Qt.ShiftModifier else 1)
        self._pos[i] -= step
        self._anim.stop()
        self._snap(i)
        e.accept()

    def keyPressEvent(self, e):
        if self._lock:
            return
        k = e.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            d = -1 if k == Qt.Key_Up else 1
            self._pos[self._focus] += d
            self._snap(self._focus)
        elif k in (Qt.Key_Left, Qt.Key_Right):
            self._focus = (self._focus + (1 if k == Qt.Key_Right else -1)) % 3
            self.update()
        elif k in (Qt.Key_PageUp, Qt.Key_PageDown):
            d = -5 if k == Qt.Key_PageUp else 5
            self._pos[self._focus] += d
            self._snap(self._focus)
        else:
            e.ignore()
            return
        e.accept()

    def _snap(self, i: int):
        self._pos[i] = float(round(self._pos[i]))
        self._sync_emit()
        self.update()

    def _settle_if_idle(self):
        if self._drag is None and all(abs(v) < 0.05 for v in self._vel):
            for i in range(3):
                if abs(self._pos[i] - round(self._pos[i])) > 0.001:
                    self._pos[i] = float(round(self._pos[i]))
            self.update()

    def _animate(self):
        alive = False
        for i in range(3):
            v = self._vel[i]
            if v:
                self._pos[i] += v
                self._vel[i] = v * 0.86
                if abs(self._vel[i]) < 0.03:
                    self._vel[i] = 0.0
                    self._pos[i] = float(round(self._pos[i]))
                alive = True
            elif abs(self._pos[i] - round(self._pos[i])) > 0.002:
                self._pos[i] += (round(self._pos[i]) - self._pos[i]) * 0.35
                alive = True
            else:
                self._pos[i] = float(round(self._pos[i]))
        self._sync_emit()
        self.update()
        if not alive:
            self._anim.stop()

    _last = None

    def _sync_emit(self):
        v = self.value()
        if v != self._last:
            self._last = v
            self.valueChanged.emit(v)

    # ---------------- 绘制 ----------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cy = h / 2.0
        # 背景
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(246, 247, 249))
        p.drawRoundedRect(QRectF(0, 0, w, h), 14, 14)
        # 选中带
        p.setBrush(QColor(17, 17, 17, 14))
        p.drawRoundedRect(QRectF(6, cy - self.ITEM_H / 2.0, w - 12, self.ITEM_H), 10, 10)

        for i in range(3):
            rect = self._col_rect(i)
            # 列表头
            p.setPen(QColor(120, 124, 132) if i != self._focus else QColor(17, 17, 17))
            hf = QFont("Microsoft YaHei UI", 8)
            p.setFont(hf)
            p.drawText(QRect(rect.x(), 2, rect.width(), 16), Qt.AlignCenter, "时分秒"[i])
            # 数字
            base = int(math.floor(self._pos[i]))
            for k in range(-2, 4):
                idx = base + k
                dist = abs(idx - self._pos[i])
                if dist > self.VISIBLE:
                    continue
                y = cy + (idx - self._pos[i]) * self.ITEM_H
                alpha = max(0.10, 1.0 - dist / 2.2)
                if self._lock:
                    alpha *= 0.45
                col = QColor(17, 17, 17)
                col.setAlphaF(min(1.0, alpha))
                size = 26 if dist < 0.55 else 20
                f = QFont("Segoe UI", size)
                f.setBold(dist < 0.55)
                p.setFont(f)
                p.setPen(col)
                txt = self.UNITS[i] == "h" and "%02d" % (idx % 24) or "%02d" % (idx % 60)
                p.drawText(QRectF(rect.x(), y - self.ITEM_H / 2.0, rect.width(), self.ITEM_H),
                           Qt.AlignCenter, txt)
            # 列间分隔
            if i < 2:
                p.setPen(QColor(0, 0, 0, 18))
                p.drawLine(int(rect.right()), 18, int(rect.right()), h - 18)
        # 上下淡出遮罩
        for rect, use_top in ((QRectF(0, 0, w, 16), True), (QRectF(0, h - 16, w, 16), False)):
            g = QLinearGradient(rect.topLeft(), rect.bottomLeft()) if use_top else QLinearGradient(rect.bottomLeft(), rect.topLeft())
            g.setColorAt(0.0, QColor(246, 247, 249, 255))
            g.setColorAt(1.0, QColor(246, 247, 249, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRect(rect)
        if self._lock:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 90))
            p.drawRoundedRect(QRectF(0, 0, w, h), 14, 14)


# ================================================================== 计时器
class TimerWindow(_Card):
    def __init__(self):
        super().__init__(16)
        self.down = True
        self.total = 300
        self.left = 300
        self.running = False
        self.tick = QTimer(self, interval=1000, timeout=self._tick)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 14)
        top = QHBoxLayout()
        self.mode = SegmentedWidget(self)
        self.mode.addItem("down", "倒计时", onClick=lambda: self._set_mode(True))
        self.mode.addItem("up", "正计时", onClick=lambda: self._set_mode(False))
        self.mode.setCurrentItem("down")
        top.addWidget(self.mode)
        top.addStretch(1)
        close = TransparentToolButton(FIF.CLOSE, self)
        close.clicked.connect(self.hide)
        top.addWidget(close)
        lay.addLayout(top)
        self.label = QLabel("05:00")
        f = QFont("Segoe UI", 54)
        f.setBold(True)
        self.label.setFont(f)
        self.label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.label)
        presets = QHBoxLayout()
        for m in (1, 3, 5, 10, 20):
            b = PushButton(f"{m} 分")
            b.clicked.connect(lambda _=False, m=m: self._preset(m * 60))
            presets.addWidget(b)
        lay.addLayout(presets)

        # ---- v1.36: iOS 式滚轮设定 (拖动 / 加减 时·分·秒) ----
        self.wheelToggle = PushButton(FIF.SYNC, "滚轮设定")
        self.wheelToggle.clicked.connect(self._toggle_wheel)
        lay.addWidget(self.wheelToggle)
        self.wheelBox = QWidget(self)
        wl = QVBoxLayout(self.wheelBox)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)
        self.wheel = TimeWheel(self.wheelBox, value=self.total)
        self.wheel.valueChanged.connect(self._on_wheel)
        wl.addWidget(self.wheel)
        steppers = QHBoxLayout()
        steppers.setSpacing(4)
        for unit, name in (("h", "时"), ("m", "分"), ("s", "秒")):
            lab = BodyLabel(name)
            lab.setFixedWidth(18)
            minus = PushButton("−")
            plus = PushButton("＋")
            for b in (minus, plus):
                b.setFixedWidth(34)
            minus.clicked.connect(lambda _=False, u=unit: self.wheel.step(u, -1))
            plus.clicked.connect(lambda _=False, u=unit: self._plus(u))
            steppers.addWidget(lab)
            steppers.addWidget(minus)
            steppers.addWidget(plus)
            steppers.addSpacing(6)
        wl.addLayout(steppers)
        self.wheelBox.setVisible(False)
        lay.addWidget(self.wheelBox)

        acts = QHBoxLayout()
        self.go = PrimaryPushButton(FIF.PLAY, "开始")
        self.go.clicked.connect(self.toggle)
        rst = PushButton(FIF.SYNC, "归零")
        rst.clicked.connect(self.reset)
        acts.addWidget(self.go)
        acts.addWidget(rst)
        lay.addLayout(acts)
        self.setFixedWidth(380)
        self.adjustSize()

    def _set_mode(self, down):
        self.down = down
        self.reset()

    def _toggle_wheel(self):
        vis = not self.wheelBox.isVisible()
        self.wheelBox.setVisible(vis)
        self.wheelToggle.setText("收起滚轮" if vis else "滚轮设定")
        if vis:
            self.wheel.setValue(self.total if self.down else self.left, emit=False)
        self.adjustSize()

    def _plus(self, unit):
        self.wheel.step(unit, 1)

    def _on_wheel(self, sec):
        """拖滚轮 → 立即成为倒计时目标 (滚轮在运行时被禁用, 无需防抖)"""
        if not self.down:
            self.down = True
            self.mode.setCurrentItem("down")
        self.running = False
        self.tick.stop()
        self.go.setText("开始")
        self.go.setIcon(FIF.PLAY)
        self.total = max(0, int(sec))
        self.left = self.total
        self._show()

    def _preset(self, s):
        self.down = True
        self.mode.setCurrentItem("down")
        self.total = s
        self.reset()

    def toggle(self):
        self.running = not self.running
        self.go.setText("暂停" if self.running else "开始")
        self.go.setIcon(FIF.PAUSE if self.running else FIF.PLAY)
        (self.tick.start() if self.running else self.tick.stop())
        self.wheel.setDisabled(self.running)      # 运行中锁住滚轮, 防止误触改时间
        self.wheelToggle.setEnabled(not self.running)

    def reset(self):
        self.running = False
        self.tick.stop()
        self.go.setText("开始")
        self.go.setIcon(FIF.PLAY)
        self.wheel.setDisabled(False)
        self.wheelToggle.setEnabled(True)
        self.left = self.total if self.down else 0
        if self.down:
            self.wheel.setValue(self.total, emit=False)
        self._show()

    def _tick(self):
        if self.down:
            self.left -= 1
            if self.left <= 0:
                self.left = 0
                self.toggle()
                QApplication.beep()
                self.label.setStyleSheet("color:#E53935;")
                QTimer.singleShot(4000, lambda: self.label.setStyleSheet(""))
        else:
            self.left += 1
        self._show()

    def _show(self):
        h, rest = divmod(max(0, int(self.left)), 3600)
        m, s = divmod(rest, 60)
        self.label.setText(f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")

    def popup(self):
        screen = _screen_for_rect(self.frameGeometry()) if self.isVisible() else None
        screen = screen or QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = _screen_rect(screen, available=True)
        target = QRect(area.center().x() - self.width() // 2, area.top() + 80, self.width(), self.height())
        self.setGeometry(_clamp_rect(target, area))
        self.show()
        self.raise_()


# ================================================================== 悬浮工具盒
class Dock(_Card):
    command = pyqtSignal(str)

    def __init__(self, ppt=False):
        super().__init__(14)
        self.ppt = ppt
        self.bar = QHBoxLayout(self)
        self.bar.setContentsMargins(10, 6, 10, 6)
        self.bar.setSpacing(2)
        self.tools = {}
        self.btns = {}
        self.peer = None
        self.button_size = core.cfg.dock_size
        self.preferred_button_size = self.button_size
        self._positioned = False
        self.hidden_cmds = set(core.cfg.dock_hidden)
        self.page_label = QLabel("1")
        self.page_label.setMinimumWidth(44 if ppt else 28)
        self.page_label.setAlignment(Qt.AlignCenter)

        def btn(icon, tip, cmd, toggle=False):
            b = (TransparentToggleToolButton if toggle else TransparentToolButton)(icon, self)
            b.setFixedSize(40, 40)
            b.setToolTip(tip)
            b.installEventFilter(ToolTipFilter(b, 300, ToolTipPosition.TOP))
            b.clicked.connect(lambda _=False: self.command.emit(cmd))
            self.bar.addWidget(b)
            self.btns[cmd] = b
            return b

        def sep():
            f = QFrame()
            f.setFixedSize(1, 26)
            f.setStyleSheet("background:#e3e3e3;")
            self.bar.addSpacing(4)
            self.bar.addWidget(f)
            self.bar.addSpacing(4)

        self.grip = Grip(self, self)
        self.bar.addWidget(self.grip)
        if ppt:
            tag = QLabel("PPT")
            tag.setStyleSheet("background:#111;color:#fff;border-radius:4px;padding:1px 5px;font-size:11px;font-weight:600;")
            self.bar.addWidget(tag)
            self.bar.addSpacing(4)
        prev_tip = "上一步（含动画）；触屏向右滑动上一页" if ppt else "上一页；触屏向右滑动上一页"
        next_tip = "下一步（含动画）；触屏向左滑动下一页" if ppt else "下一页；触屏向左滑动下一页"
        btn(FIF.LEFT_ARROW, prev_tip, "prev")
        self.bar.addWidget(self.page_label)
        btn(FIF.RIGHT_ARROW, next_tip, "next")
        sep()
        for icon, tip, key in [(_icon("cursor"), "鼠标（退出批注，可点击 PPT/电脑）", "mouse"),
                               (FIF.PENCIL_INK, "画笔（再点一次：颜色/粗细）", "pen"),
                               (FIF.HIGHTLIGHT, "荧光笔（再点一次：颜色/粗细）", "marker"),
                               (FIF.CLEAR_SELECTION, "激光笔", "laser"),
                               (FIF.ERASE_TOOL, "橡皮（再点一次：大小/类型）", "eraser")]:
            self.tools[key] = btn(icon, tip, key, toggle=True)
        btn(FIF.RETURN, "撤销 (Ctrl+Z)", "undo")
        btn(FIF.DELETE, "清除本页笔迹", "clear")
        sep()
        self.board_btn = btn(FIF.QUICK_NOTE, "白板", "board", toggle=True)
        btn(FIF.BRIGHTNESS, "黑屏 / 恢复", "black")
        btn(FIF.STOP_WATCH, "计时器", "timer")
        self.phone_btn = btn(FIF.PHONE, "手机投屏 / 观看", "phone", toggle=True)
        sep()
        btn(FIF.HOME, "打开主窗口", "main")
        btn(FIF.SETTING, "自定义工具栏", "dock_settings")
        btn(FIF.MINIMIZE, "收起工具盒", "collapse")
        if ppt:
            btn(FIF.CLOSE, "结束 PPT 放映", "ppt_exit")
        else:
            btn(FIF.CLOSE, "结束放映工具", "end")
        self.page_label.setVisible(ppt)
        self.set_tool("mouse")
        self.adjustSize()

        self.mini = _Card(22)
        ml = QHBoxLayout(self.mini)
        ml.setContentsMargins(4, 4, 4, 4)
        self.mini_grip = Grip(self.mini, self.mini)
        self.mini_grip.setFixedSize(20, 36)
        self.mini_grip.setToolTip("按住拖动最小化工具栏")
        ml.addWidget(self.mini_grip)
        mb = TransparentToolButton(FIF.EDUCATION, self.mini)
        mb.setFixedSize(36, 36)
        mb.setToolTip("展开 FLA 工具盒")
        mb.clicked.connect(self.expand)
        ml.addWidget(mb)
        self.mini.adjustSize()
        self.apply_preferences()
        self.set_button_size(self.button_size, save=False)

    def apply_preferences(self):
        self.hidden_cmds.discard("dock_settings")
        for cmd, b in self.btns.items():
            b.setVisible(cmd not in self.hidden_cmds or cmd == "dock_settings")
        self.page_label.setVisible(self.ppt and not ({"prev", "next"} <= self.hidden_cmds))
        self.adjustSize()

    def set_button_size(self, size, save=True):
        self.button_size = max(32, min(48, int(size)))
        if save:
            self.preferred_button_size = self.button_size
        for b in self.btns.values():
            b.setFixedSize(self.button_size, self.button_size)
        self.grip.setFixedSize(22, self.button_size)
        self.bar.setContentsMargins(10, max(4, (self.button_size - 28) // 2), 10,
                                    max(4, (self.button_size - 28) // 2))
        self.bar.setSpacing(max(1, self.button_size // 20))
        if save:
            core.cfg.dock_size = self.button_size
            core.cfg.save()
        self.adjustSize()

    def open_settings(self):
        menu = QMenu(self)
        sizes = menu.addMenu("工具按钮大小")
        for size in (32, 36, 40, 44, 48):
            action = sizes.addAction(f"{size} × {size}")
            action.setCheckable(True)
            action.setChecked(self.button_size == size)
            action.triggered.connect(lambda _=False, s=size: self._choose_size(s))
        visible = menu.addMenu("显示 / 隐藏工具")
        keep = {"dock_settings", "collapse", "end", "ppt_exit"}
        for cmd, b in self.btns.items():
            if cmd in keep:
                continue
            label = b.toolTip().split("（", 1)[0].split("(", 1)[0].strip()
            action = visible.addAction(label or cmd)
            action.setCheckable(True)
            action.setChecked(cmd not in self.hidden_cmds)
            action.triggered.connect(lambda checked, c=cmd: self._choose_visibility(c, checked))
        menu.addSeparator()
        reset = menu.addAction("恢复默认工具")
        reset.triggered.connect(self._reset_preferences)
        anchor = self.btns.get("dock_settings")
        if anchor:
            menu.exec_(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _choose_size(self, size):
        core.cfg.dock_size = int(size)
        core.cfg.save()
        for dock in (self, self.peer):
            if dock:
                dock.preferred_button_size = max(32, min(48, int(size)))
                dock.set_button_size(size, save=False)

    def _choose_visibility(self, cmd, visible):
        hidden = set(core.cfg.dock_hidden)
        if visible:
            hidden.discard(cmd)
        else:
            hidden.add(cmd)
        core.cfg.dock_hidden = sorted(hidden)
        core.cfg.save()
        for dock in (self, self.peer):
            if dock:
                dock.hidden_cmds = set(hidden)
                dock.apply_preferences()

    def _reset_preferences(self):
        core.cfg.dock_hidden = []
        core.cfg.dock_size = 40
        core.cfg.save()
        for dock in (self, self.peer):
            if dock:
                dock.hidden_cmds = set()
                dock.preferred_button_size = 40
                dock.apply_preferences()
                dock.set_button_size(40, save=False)

    def set_tool(self, tool):
        for k, b in self.tools.items():
            b.setChecked(k == tool)

    def set_board(self, on):
        self.board_btn.setChecked(on)

    def set_page(self, n):
        self.page_label.setText(str(n))

    def fit_to_width(self, max_width):
        """Temporarily compact buttons on narrow displays; preserve the user's preference."""
        preferred = max(32, min(48, int(self.preferred_button_size)))
        candidates = [size for size in (preferred, 44, 40, 36, 32) if size <= preferred]
        for size in candidates:
            self.set_button_size(size, save=False)
            if self.width() <= max_width:
                break

    def popup(self):
        self.adjustSize()
        screen = _screen_for_rect(self.frameGeometry()) if self._positioned else None
        screen = screen or QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = _screen_rect(screen, available=True)
        self.fit_to_width(max(1, area.width() - 16))
        if not self._positioned:
            target = QRect(area.center().x() - self.width() // 2,
                           area.bottom() - self.height() - 56, self.width(), self.height())
            self._positioned = True
        else:
            target = _clamp_rect(self.frameGeometry(), area)
        self.setGeometry(target)
        self.mini.hide()
        self.show()
        self.raise_()

    def collapse(self):
        screen = (_screen_for_rect(self.frameGeometry()) or QApplication.screenAt(QCursor.pos())
                  or QApplication.primaryScreen())
        area = _screen_rect(screen, available=True)
        self.mini.adjustSize()
        target = QRect(area.right() - self.mini.width() - 16,
                       area.bottom() - self.mini.height() - 120,
                       self.mini.width(), self.mini.height())
        self.hide()
        self.mini.setGeometry(_clamp_rect(target, area))
        self.mini.show()
        self.mini.raise_()

    def expand(self):
        screen = _screen_for_rect(self.mini.frameGeometry()) or QApplication.primaryScreen()
        area = _screen_rect(screen, available=True)
        self.fit_to_width(max(1, area.width() - 16))
        self.setGeometry(_clamp_rect(self.frameGeometry(), area))
        self._positioned = True
        self.mini.hide()
        self.show()
        self.raise_()

    def close_all(self):
        self.hide()
        self.mini.hide()


# ================================================================== 舞台控制器
def _native_to_logical(rect):
    """Convert a Win32 physical-pixel rect into the matching QScreen's coordinates.

    The old calculation subtracted QScreen's *logical* origin from a Win32
    physical coordinate. That only works on the primary screen at 100% scaling;
    mixed-DPI and negative-origin monitors drifted after a display/scale change.
    """
    if not rect:
        return None
    l, t, w, h = map(int, rect)
    screens = QApplication.screens()
    if not screens or not core.IS_WIN:
        return QRect(l, t, max(1, w), max(1, h))

    monitor = _native_monitor_at(l + w // 2, t + h // 2)
    screen = None
    native_bounds = None
    if monitor:
        device_name, native_bounds = monitor
        device = _device_key(device_name)
        screen = next((s for s in screens if _device_key(s.name()) == device), None)
        if screen is None:
            # Some Qt platform plugins expose a different display name. Match by
            # the physical/logical pixel dimensions and the per-monitor DPR.
            pw, ph = native_bounds[2:]
            screen = min(
                screens,
                key=lambda s: abs(s.geometry().width() * s.devicePixelRatio() - pw)
                + abs(s.geometry().height() * s.devicePixelRatio() - ph),
            )

    screen = screen or QApplication.primaryScreen() or screens[0]
    logical = screen.geometry()
    scale = float(screen.devicePixelRatio() or 1.0)
    if native_bounds is None:
        native_bounds = (
            round(logical.x() * scale), round(logical.y() * scale),
            round(logical.width() * scale), round(logical.height() * scale),
        )
    x, y, width, height = physical_rect_to_logical(
        (l, t, w, h),
        (logical.x(), logical.y(), logical.width(), logical.height()),
        native_bounds,
        scale,
    )
    return QRect(x, y, width, height)


class Stage:
    def __init__(self, show_main):
        self.show_main = show_main
        self.ov = Overlay()
        self.desk_dock = Dock()
        self.ppt_dock = Dock(ppt=True)
        self.desk_dock.peer = self.ppt_dock
        self.ppt_dock.peer = self.desk_dock
        self.dock = self.desk_dock
        self.pen_pop = PenPopup(self.ov)
        self.eraser_pop = EraserPopup(self.ov)
        self.timer = TimerWindow()
        self.cast = None                       # 由 window 注入 CastController
        self.mode = "desktop"                  # desktop | ppt
        self.ppt = {}
        self.ppt_key = ""
        self.desk_dock_was_visible = False
        self.floating = [self.desk_dock, self.desk_dock.mini, self.ppt_dock, self.ppt_dock.mini,
                         self.pen_pop, self.eraser_pop, self.timer]
        self._floating_screens = {}
        for w in self.floating:
            w.changed.connect(lambda w=w: self._floating_changed(w))
            screen = _screen_for_rect(w.frameGeometry())
            if screen:
                self._floating_screens[id(w)] = _screen_key(screen)
        for d in (self.desk_dock, self.ppt_dock):
            d.command.connect(self.handle)
        self.ov.tool_changed.connect(self._tool_changed)
        self.ov.key_command.connect(self.handle)
        self.ov.pages = load_ink("desktop")
        self._screen_bounds = {_screen_key(s): QRect(s.availableGeometry()) for s in QApplication.screens()}
        self._screen_reflow = QTimer()
        self._screen_reflow.setSingleShot(True)
        self._screen_reflow.setInterval(100)
        self._screen_reflow.timeout.connect(self._refresh_screen_layout)
        self._watch_screen_changes()
        self.touch_swipe = _TouchSwipeHook()
        self.touch_swipe.gesture.connect(self._on_touch_swipe)
        self._touch_warning_shown = False
        # 兜底: 定时把工具栏顶到最上层 + 重算挖空 (防止任何情况下被画布盖住)
        self.guard = QTimer(interval=800, timeout=self._guard)
        self.guard.start()
        # 记录最近的外部前台窗口 (桌面模式翻页要把按键送给它)
        self.last_fg = 0
        self.fg_timer = QTimer(interval=300, timeout=self._track_fg)
        self.fg_timer.start()
        # PPT 联动
        from .ppt import PPTWatcher
        self.watcher = PPTWatcher()
        self.watcher.state.connect(self._on_ppt)
        self.watcher.start()

    # ---- 通用
    def add_floating(self, w):
        if w not in self.floating:
            self.floating.append(w)
            if hasattr(w, "changed"):
                w.changed.connect(lambda w=w: self._floating_changed(w))
        self.refresh_holes()

    def refresh_holes(self):
        self.ov.set_holes([w.frameGeometry() for w in self.floating if w.isVisible()])

    def _floating_changed(self, widget):
        screen = _screen_for_rect(widget.frameGeometry())
        if screen:
            self._floating_screens[id(widget)] = _screen_key(screen)
        self.refresh_holes()

    def _watch_screen_changes(self):
        app = QApplication.instance()
        if app is None:
            return
        self._watched_screen_ids = set()
        for name in ("screenAdded", "primaryScreenChanged"):
            signal = getattr(app, name, None)
            if signal is not None:
                signal.connect(self._schedule_screen_refresh)
        removed = getattr(app, "screenRemoved", None)
        if removed is not None:
            removed.connect(self._screen_removed)
        for screen in QApplication.screens():
            self._watch_screen(screen)

    def _screen_removed(self, screen):
        self._watched_screen_ids.discard(_screen_key(screen))
        self._schedule_screen_refresh()

    def _watch_screen(self, screen):
        key = _screen_key(screen)
        if screen is None or key in self._watched_screen_ids:
            return
        self._watched_screen_ids.add(key)
        for name in ("geometryChanged", "availableGeometryChanged", "logicalDotsPerInchChanged"):
            signal = getattr(screen, name, None)
            if signal is not None:
                signal.connect(self._schedule_screen_refresh)

    def _schedule_screen_refresh(self, *_args):
        app = QApplication.instance()
        for screen in app.screens() if app is not None else ():
            self._watch_screen(screen)
        self._screen_reflow.start()

    @staticmethod
    def _relative_position(value, old_start, old_span, old_size):
        travel = old_span - old_size
        if travel <= 0:
            return 0.5
        return max(0.0, min(1.0, (value - old_start) / travel))

    def _refresh_screen_layout(self):
        screens = QApplication.screens()
        if not screens:
            return
        by_key = {_screen_key(screen): screen for screen in screens}
        primary = QApplication.primaryScreen() or screens[0]
        current_bounds = {_screen_key(screen): QRect(screen.availableGeometry()) for screen in screens}

        for widget in self.floating:
            old_rect = QRect(widget.frameGeometry())
            old_key = self._floating_screens.get(id(widget), "")
            old_bounds = self._screen_bounds.get(old_key)
            screen = by_key.get(old_key)
            if screen is None:
                screen = _screen_for_rect(old_rect) or primary
            new_bounds = QRect(screen.availableGeometry())

            if isinstance(widget, Dock):
                widget.fit_to_width(max(1, new_bounds.width() - 16))
            width, height = widget.width(), widget.height()
            if old_bounds is not None:
                rx = self._relative_position(old_rect.x(), old_bounds.x(), old_bounds.width(), old_rect.width())
                ry = self._relative_position(old_rect.y(), old_bounds.y(), old_bounds.height(), old_rect.height())
                x = new_bounds.x() + round(rx * max(0, new_bounds.width() - width))
                y = new_bounds.y() + round(ry * max(0, new_bounds.height() - height))
                mapped = QRect(x, y, width, height)
            else:
                mapped = QRect(old_rect.topLeft(), old_rect.size())
            widget.setGeometry(_clamp_rect(mapped, new_bounds))
            self._floating_screens[id(widget)] = _screen_key(screen)

        # 重设画布并刷新遮罩；PPTWatcher 随后会用新的原生矩形校正放映区域。
        self.ov.sync()
        self.refresh_holes()
        for widget in self.floating:
            if widget.isVisible():
                widget.raise_()
        self._screen_bounds = current_bounds

    def _guard(self):
        if self.ov.isVisible():
            self.refresh_holes()
            for w in self.floating:
                if w.isVisible():
                    w.raise_()

    @staticmethod
    def _window_process_id(user32, hwnd):
        if not hwnd:
            return 0
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _touch_point_is_content(self, x, y):
        """Reject FLA controls and non-content surfaces before acting on a swipe.

        Do not rely on WindowFromPoint here: Windows can report FLA's full-screen,
        input-transparent annotation overlay as the hit-test target, which would
        discard every otherwise-valid gesture.
        """
        if not core.IS_WIN or self.ov.tool != "mouse" or self.ov.board:
            return False
        if not (self.dock.isVisible() or self.dock.mini.isVisible()):
            return False
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_NativeRect)]
            user32.GetWindowRect.restype = ctypes.c_int
            foreground = user32.GetForegroundWindow()

            def contains(hwnd):
                bounds = _NativeRect()
                if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(bounds)):
                    return False
                return bounds.left <= x < bounds.right and bounds.top <= y < bounds.bottom

            rect = self.ppt.get("rect") if self.mode == "ppt" else None
            if self.mode == "ppt" and rect:
                # COM supplies the slideshow's physical-pixel bounds. Do not reject
                # pointer input just because tablet mode leaves another HWND in front.
                left, top, width, height = map(int, rect)
                if not (left <= x < left + width and top <= y < top + height):
                    return False
            else:
                if (not foreground or self._window_process_id(user32, foreground) == os.getpid()
                        or not contains(foreground)):
                    return False

            # Exclude our toolbar, collapsed handle, timer, and tool popups using
            # native rectangles (the hook coordinates are Windows physical pixels).
            for widget in self.floating:
                if not widget.isVisible():
                    continue
                try:
                    hwnd = ctypes.c_void_p(int(widget.winId()))
                    if contains(hwnd):
                        return False
                except Exception:
                    continue

            if self.mode != "ppt":
                self.last_fg = foreground
            return True
        except Exception:
            return False

    def _on_touch_swipe(self, x0, y0, x1, y1, duration):
        command = classify_swipe((x0, y0), (x1, y1), duration)
        if command is None:
            return
        if not self._touch_point_is_content(x0, y0) or not self._touch_point_is_content(x1, y1):
            return
        # Let the OS deliver the touch-up to the foreground app before injecting
        # the page command; the hook itself always forwards every native event.
        QTimer.singleShot(0, lambda command=command: self.handle(command))

    def _track_fg(self):
        if not core.IS_WIN:
            return
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.GetForegroundWindow.restype = ctypes.c_void_p
            u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
            u.GetWindowThreadProcessId.restype = ctypes.c_ulong
            h = u.GetForegroundWindow()
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if h and pid.value != os.getpid():
                self.last_fg = int(h)
        except Exception:
            pass

    def _send_desktop_key(self, cmd):
        """未开 PPT: 把翻页键送给最近使用的外部窗口 (PDF/网页/图片查看器等)"""
        if not core.IS_WIN:
            return
        try:
            import ctypes
            u = ctypes.windll.user32
            u.IsWindow.argtypes = [ctypes.c_void_p]
            u.IsWindow.restype = ctypes.c_int
            u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            u.SetForegroundWindow.restype = ctypes.c_int
            if self.last_fg and u.IsWindow(ctypes.c_void_p(int(self.last_fg))):
                u.SetForegroundWindow(ctypes.c_void_p(int(self.last_fg)))
        except Exception:
            pass
        core.send_key(cmd)
        if self.ov.tool != "mouse" or self.ov.board:
            QTimer.singleShot(150, self.ov.sync)

    def _tool_changed(self, t):
        self.dock.set_tool(t)
        self.dock.set_board(self.ov.board)
        self.pen_pop.hide()
        self.eraser_pop.hide()

    def start(self):
        self.dock.popup()
        self._start_touch_swipe()
        self.refresh_holes()

    def _start_touch_swipe(self):
        if not core.IS_WIN:
            return False
        started = self.touch_swipe.start()
        if not started:
            print("[FLA] touch swipe hook failed:", self.touch_swipe.last_error, flush=True)
            if not self._touch_warning_shown:
                self._touch_warning_shown = True
                main = getattr(self.cast, "main", None)
                if main is not None:
                    main.toast("触屏翻页钩子未启动；请重启 FLA，或检查安全软件/系统权限是否拦截。", ok=False)
        return started

    # ---- PPT 联动
    def _on_ppt(self, info: dict):
        if info.get("active"):
            if self.mode != "ppt":
                self._enter_ppt(info)
            elif self.ppt.get("path") != info.get("path"):
                self._leave_ppt()
                self._enter_ppt(info)
            self._ppt_update(info)
        elif self.mode == "ppt":
            self._leave_ppt()

    def _ppt_name(self, path):
        import hashlib
        return "ppt_" + hashlib.md5((path or "").lower().encode("utf-8", "ignore")).hexdigest()[:16]

    def _enter_ppt(self, info):
        # 1. 桌面画布落盘并收起桌面工具栏
        save_ink("desktop", self.ov.pages)
        self.desk_dock_was_visible = self.desk_dock.isVisible() or self.desk_dock.mini.isVisible()
        self.desk_dock.close_all()
        self.pen_pop.hide()
        self.eraser_pop.hide()
        # 2. 载入本课件以前的批注 (按页)
        self.mode = "ppt"
        self.ppt = dict(info)
        self.ppt_key = self._ppt_name(info.get("path"))
        self.ov.board = False
        self.ov.pages = load_ink(self.ppt_key)
        self.ov.page = int(info.get("page") or 1)
        self.ov.tool = "mouse"
        self.ov.set_tool("mouse")
        # 3. 打开与 PPT 同步的工具栏
        self.dock = self.ppt_dock
        self.dock.set_tool("mouse")
        self.dock.set_board(False)
        self.dock.popup()
        self._start_touch_swipe()
        self.refresh_holes()

    def _ppt_update(self, info):
        self.ppt = dict(info)
        rect = info.get("rect")
        self.ov.area = _native_to_logical(rect) if rect else None
        page, total = int(info.get("page") or 1), int(info.get("total") or 0)
        self.ppt_dock.set_page(f"{page}/{total}" if total else page)
        if page != self.ov.page:
            save_ink(self.ppt_key, self.ov.pages)
            self.ov.cur = None
        self.ov.set_page(page)       # 画布随 PPT 页码切换 (无论从哪里翻页)
        if self.ov.area is not None:
            a = self.ov.area
            d = self.ppt_dock
            d.fit_to_width(max(1, a.width() - 16))
            if not a.contains(d.frameGeometry().center()):
                d.move(a.center().x() - d.width() // 2, a.bottom() - d.height() - 40)
            d.setGeometry(_clamp_rect(d.frameGeometry(), a))

    def _leave_ppt(self):
        save_ink(self.ppt_key, self.ov.pages)
        self.mode = "desktop"
        self.ppt_dock.close_all()
        self.pen_pop.hide()
        self.eraser_pop.hide()
        self.ov.area = None
        self.ov.board = False
        self.ov.pages = load_ink("desktop")
        self.ov.page = 1
        self.ov.set_tool("mouse")
        self.dock = self.desk_dock
        self.dock.set_tool("mouse")
        if self.desk_dock_was_visible:
            self.dock.popup()
            self._start_touch_swipe()
        else:
            self.touch_swipe.stop()
        self.refresh_holes()

    def shutdown(self):
        self.touch_swipe.stop()
        try:
            save_ink(self.ppt_key if self.mode == "ppt" else "desktop", self.ov.pages)
            self.watcher.stop()
            self.watcher.wait(1500)
        except Exception:
            pass

    def _popup_above(self, pop, cmd):
        pop.adjustSize()
        b = self.dock.btns[cmd]
        c = b.mapToGlobal(b.rect().center())
        g = self.dock.frameGeometry()
        scr = QApplication.screenAt(c) or QApplication.primaryScreen()
        sr = _screen_rect(scr, available=True)
        x = c.x() - pop.width() // 2
        y = g.top() - pop.height() - 10
        if y < sr.top():
            y = g.bottom() + 10
        pop.setGeometry(_clamp_rect(QRect(x, y, pop.width(), pop.height()), sr))
        pop.show()
        pop.raise_()

    def handle(self, cmd: str):
        ov = self.ov
        if cmd in ("next", "prev", "first", "last"):
            if ov.board:
                return
            if self.mode == "ppt":
                self.watcher.command(cmd)          # COM: 与点击 PPT 一致, 逐步播放动画
            else:
                self._send_desktop_key(cmd)
        elif cmd in ("mouse", "pen", "marker", "laser", "eraser"):
            pop = {"pen": self.pen_pop, "marker": self.pen_pop, "eraser": self.eraser_pop}.get(cmd)
            again = ov.tool == cmd
            ov.set_tool(cmd)
            self.dock.set_tool(cmd)
            for p in (self.pen_pop, self.eraser_pop):
                if p is not pop:
                    p.hide()
            if pop is not None:
                if again and pop.isVisible():
                    pop.hide()
                elif again or not pop.isVisible():
                    self._popup_above(pop, cmd)
        elif cmd == "undo":
            ov.undo()
        elif cmd == "clear":
            ov.clear()
        elif cmd == "board":
            ov.set_board(not ov.board)
            self.dock.set_board(ov.board)
            self.dock.set_tool(ov.tool)
        elif cmd in ("black", "white"):
            if self.mode == "ppt":
                self.watcher.command(cmd)
            else:
                core.send_key(cmd)
        elif cmd == "ppt_exit":
            self.watcher.command("exit")
        elif cmd == "timer":
            self.timer.hide() if self.timer.isVisible() else self.timer.popup()
        elif cmd == "phone":
            if self.cast:
                self.cast.toggle_panel()
            for d in (self.desk_dock, self.ppt_dock):
                d.phone_btn.setChecked(bool(self.cast and self.cast.active))
        elif cmd == "main":
            self.handle("mouse")
            self.show_main()
        elif cmd == "dock_settings":
            self.dock.open_settings()
        elif cmd == "collapse":
            self.pen_pop.hide()
            self.eraser_pop.hide()
            self.dock.collapse()
        elif cmd == "dock_toggle":
            if self.dock.isVisible():
                self.dock.collapse()
            else:
                self.dock.popup()
                self._start_touch_swipe()
        elif cmd == "end":
            ov.board = False
            ov.set_tool("mouse")
            save_ink("desktop", ov.pages)
            ov.hide()
            self.pen_pop.hide()
            self.eraser_pop.hide()
            self.dock.close_all()
            self.touch_swipe.stop()
        self.refresh_holes()
        for w in self.floating:
            if w.isVisible():
                w.raise_()
