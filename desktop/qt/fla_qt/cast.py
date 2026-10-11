"""手机投屏 / 手机观看
- 电脑 → 手机: 定时截屏 (含批注) 压成 JPEG 上传到 /api/remote/{sid}/frame/pc, 手机 cast.html 实时观看
- 手机 → 电脑: 手机打开摄像头/照片, 上传到 frame/phone, 电脑全屏显示 (可继续用批注工具)
- 手机批注: 手机端画笔/荧光/激光/橡皮/翻页 通过 /action 发来, 电脑画布同步绘制
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (BodyLabel, CaptionLabel, PrimaryPushButton, PushButton, SubtitleLabel,
                            TransparentToolButton)

from . import core
from .core import cfg
from .link import Link
from .stage import _Card, _screen_rect


def qr_pixmap(text: str, size: int = 230) -> QPixmap:
    """本地生成二维码 (局域网地址不经服务器)"""
    try:
        import qrcode  # noqa
    except ImportError:
        import os
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "server", "vendor"))
        import qrcode  # noqa
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    cell = max(1, size // n)
    pm = QPixmap(cell * n, cell * n)
    pm.fill(QColor("#FFFFFF"))
    p = QPainter(pm)
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                p.fillRect(x * cell, y * cell, cell, cell, QColor("#000000"))
    p.end()
    return pm


def _http(method, path, data=None, ctype="application/json", timeout=10):
    req = urllib.request.Request(core.full_url(path), data=data, method=method,
                                 headers={"Content-Type": ctype, "User-Agent": f"FLA-Desktop/{core.VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout, context=core._CTX) as r:
        return r.status, r.headers, r.read()


class _Sig(QObject):
    event = pyqtSignal(dict)
    created = pyqtSignal(object)
    qr = pyqtSignal(bytes)
    phone_frame = pyqtSignal(bytes)
    status = pyqtSignal(str)


class PhoneView(QWidget):
    """全屏显示手机画面 (位于批注画布之下)"""

    def __init__(self, on_close):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.pix = None
        self.on_close = on_close
        self.btn = TransparentToolButton(FIF.CLOSE, self)
        self.btn.setFixedSize(44, 44)
        self.btn.setToolTip("关闭手机画面")
        self.btn.clicked.connect(on_close)
        self.tip = CaptionLabel("等待手机画面…", self)
        self.tip.setStyleSheet("color:#bbb;")

    def set_frame(self, data: bytes):
        img = QImage.fromData(data, "JPG")
        if not img.isNull():
            self.pix = QPixmap.fromImage(img)
            self.tip.hide()
            self.update()

    def popup(self):
        self.setGeometry(_screen_rect())
        self.btn.move(self.width() - 60, 16)
        self.tip.move(self.width() // 2 - 60, self.height() // 2)
        self.show()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111111"))
        if self.pix:
            s = self.pix.size().scaled(self.size(), Qt.KeepAspectRatio)
            x, y = (self.width() - s.width()) // 2, (self.height() - s.height()) // 2
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(x, y, s.width(), s.height(), self.pix)


class CastBall(_Card):
    """投屏悬浮球: 面板最小化后常驻置顶, 可拖动; 单击展开面板, 右键菜单可结束投屏"""

    def __init__(self, ctl):
        super().__init__(28)
        self.ctl = ctl
        self._press = None
        self.setFixedSize(56, 56)
        self.setToolTip("投屏悬浮球：单击展开 · 拖动移动 · 右键结束投屏")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        p.setBrush(QColor("#09090b"))
        p.drawEllipse(r)
        p.setPen(QColor("#ffffff"))
        f = p.font()
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "投屏")

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()

    def mouseReleaseEvent(self, e):
        moved = self._press is not None and (e.globalPos() - self._press).manhattanLength() > 4
        self._press = None
        super().mouseReleaseEvent(e)
        if not moved and e.button() == Qt.LeftButton:
            self.ctl.restore_panel()

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction("展开投屏面板", self.ctl.restore_panel)
        m.addAction("显示手机画面", self.ctl.show_phone)
        m.addSeparator()
        m.addAction("结束投屏", self.ctl.stop)
        m.exec_(self.mapToGlobal(pos))


class CastPanel(_Card):
    def __init__(self, ctl):
        super().__init__(16)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 18)
        lay.setSpacing(8)
        lay.setSizeConstraint(QVBoxLayout.SetFixedSize)
        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("手机投屏 / 观看"))
        top.addStretch(1)
        mn = TransparentToolButton(FIF.MINIMIZE, self)
        mn.setToolTip("最小化为悬浮球（投屏继续，置顶可见）")
        mn.clicked.connect(ctl.minimize_panel)
        top.addWidget(mn)
        lay.addLayout(top)
        from qfluentwidgets import SegmentedWidget
        self.seg = SegmentedWidget(self)
        self.seg.addItem("lan", "同一 Wi-Fi（极速）", lambda: ctl.set_qr_mode("lan"))
        self.seg.addItem("net", "任意网络", lambda: ctl.set_qr_mode("net"))
        self.seg.setCurrentItem("net")
        self.seg.setFixedWidth(300)
        lay.addWidget(self.seg, 0, Qt.AlignHCenter)
        self.qr = QLabel("正在创建会话…")
        self.qr.setFixedSize(240, 240)
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setStyleSheet("background:#f5f5f5;border-radius:8px;")
        lay.addWidget(self.qr, 0, Qt.AlignHCenter)
        lay.addSpacing(6)
        self.code = QLabel("配对码 ----")
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setStyleSheet("font-size:20px;font-weight:600;color:#111;")
        self.code.setFixedHeight(32)
        lay.addWidget(self.code)
        hint = BodyLabel("① 手机看电脑：扫码后手机实时显示电脑桌面/PPT，在手机上画，电脑桌面同步出现笔迹\n"
                         "② 手机投到电脑：安卓装「FLA 手机端」App 可整屏投屏（PPT/相册/任何 App）；\n"
                         "    iPhone/鸿蒙用网页版投摄像头/照片")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.state = CaptionLabel("")
        lay.addWidget(self.state)
        row = QHBoxLayout()
        self.stop_btn = PushButton(FIF.CLOSE, "结束投屏")
        self.stop_btn.clicked.connect(ctl.stop)
        self.view_btn = PrimaryPushButton(FIF.PHONE, "显示手机画面")
        self.view_btn.clicked.connect(ctl.show_phone)
        row.addWidget(self.view_btn)
        row.addWidget(self.stop_btn)
        lay.addLayout(row)
        self.apk = CaptionLabel("手机端 App 下载：网站首页 → 手机端中心（App 内可直接扫上方二维码连接）")
        self.apk.setWordWrap(True)
        self.apk.setFixedWidth(300)
        lay.addWidget(self.apk)
        hint.setFixedWidth(300)
        self.adjustSize()

    def popup(self):
        r = _screen_rect()
        self.adjustSize()
        self.move(r.right() - self.width() - 24, r.top() + 60)
        self.show()
        self.raise_()


class CastController(QObject):
    def __init__(self, stage):
        super().__init__()
        self.stage = stage
        self.sig = _Sig()
        self.sid = ""
        self.code = ""
        self.active = False
        self.gen = 0
        self.uploading = False
        self.last_hash = ""
        self.last_upload = 0.0
        self.changed_at = 0.0
        self.hq_hash = None
        self.panel = CastPanel(self)
        self.ball = CastBall(self)
        self.phone_view = PhoneView(self.hide_phone)
        stage.add_floating(self.panel)
        stage.add_floating(self.ball)
        self.ball.hide()
        # 投屏期间持续把面板/悬浮球抬到最上层: 手机全屏画面或批注层被点击后不会再把控制面板盖住
        self.top_timer = QTimer(self, interval=600, timeout=self._keep_top)
        self.sig.event.connect(self._on_event)
        self.sig.created.connect(self._on_created)
        self.sig.qr.connect(self._on_qr)
        self.sig.phone_frame.connect(self.phone_view.set_frame)
        self.sig.status.connect(lambda t: self.panel.state.setText(t))
        self.grab_timer = QTimer(self, interval=70, timeout=self._grab)
        self.link = Link()
        self.link.message.connect(self._on_event)
        self.link.phone_frame.connect(self._on_phone_frame)
        self.link.status.connect(lambda t: self.panel.state.setText(t))
        self.qr_mode = "net"
        self.net_qr = None
        self.http_frame_at = 0.0

    # ---- 会话
    def toggle_panel(self):
        if not self.active:
            self.start()
        elif self.panel.isVisible():
            self.minimize_panel()
        else:
            self.restore_panel()

    def minimize_panel(self):
        """面板 → 悬浮球 (投屏继续)"""
        if not self.ball.isVisible():
            r = _screen_rect()
            self.ball.move(r.right() - self.ball.width() - 28, r.bottom() - self.ball.height() - 160)
        self.panel.hide()
        self.ball.show()
        self.ball.raise_()

    def restore_panel(self):
        self.ball.hide()
        self.panel.popup()

    def _keep_top(self):
        if not self.active:
            return
        for w in (self.panel, self.ball):
            if w.isVisible():
                w.raise_()

    def start(self):
        self.active = True
        self.gen += 1
        gen = self.gen
        self.panel.qr.setText("正在创建会话…")
        self.panel.code.setText("配对码 ----")
        self.panel.state.setText("")
        self.panel.popup()
        self.top_timer.start()

        def work():
            try:
                st, _, raw = _http("POST", "/api/remote/create",
                                   json.dumps({"title": "FLA 桌面投屏"}).encode())
                d = json.loads(raw.decode())
                self.sig.created.emit((gen, d))
            except Exception as e:  # noqa
                self.sig.created.emit((gen, {"error": str(e)}))
        threading.Thread(target=work, daemon=True).start()

    def _on_created(self, arg):
        gen, d = arg
        if gen != self.gen or not self.active:
            return
        if not d.get("session_id"):
            self.panel.qr.setText("创建失败\n请检查网络/服务器")
            self.panel.state.setText(str(d.get("error") or d.get("detail") or ""))
            self.active = False
            [d.phone_btn.setChecked(False) for d in (self.stage.desk_dock, self.stage.ppt_dock)]
            return
        self.sid, self.code = d["session_id"], d["code"]
        self.panel.code.setText(f"配对码 {self.code}")
        self.panel.state.setText("等待手机连接…")
        [d.phone_btn.setChecked(True) for d in (self.stage.desk_dock, self.stage.ppt_dock)]
        self.last_hash = ""
        self.grab_timer.start()
        sid, code = self.sid, self.code
        self.link.start(sid, code)
        if self.link.lan_urls:
            urls = [u["ws"] for u in self.link.lan_urls]
            threading.Thread(target=lambda: self._safe(lambda: _http(
                "POST", f"/api/remote/{sid}/lan?code={code}", json.dumps({"urls": urls}).encode())), daemon=True).start()
            # 默认二维码走服务器 (任何网络都能扫); 手机与电脑同网时 App/网页会自动改走局域网直连
            self.panel.seg.setCurrentItem(self.qr_mode)
        else:
            self.panel.seg.setEnabled(False)
        threading.Thread(target=self._poll_loop, args=(gen, sid), daemon=True).start()
        threading.Thread(target=self._phone_loop, args=(gen, sid, code), daemon=True).start()

        def qr():
            try:
                q = urllib.parse.urlencode({"kind": "cast", "base": cfg.server})
                _, _, raw = _http("GET", f"/api/remote/{sid}/qr?{q}")
                self.sig.qr.emit(raw)
            except Exception:
                pass
        threading.Thread(target=qr, daemon=True).start()

    def _on_qr(self, raw):
        pm = QPixmap()
        if pm.loadFromData(raw):
            self.net_qr = pm.scaled(230, 230, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if self.qr_mode == "net":
                self.panel.qr.setPixmap(self.net_qr)
            self.panel.adjustSize()

    def set_qr_mode(self, mode):
        self.qr_mode = mode
        if mode == "lan" and self.link.lan_urls and self.link.lan_urls[0].get("page"):
            u = self.link.lan_urls[0]
            try:
                self.panel.qr.setPixmap(qr_pixmap(u["page"], 230))
            except Exception as e:  # noqa
                self.panel.qr.setText("二维码生成失败\n" + str(e)[:40])
            self.panel.state.setText(f"手机连同一 Wi-Fi 后扫码（{u['ip']}）。首次使用请在 Windows 防火墙弹窗中点“允许”")
        else:
            if self.net_qr is not None:
                self.panel.qr.setPixmap(self.net_qr)
            self.panel.state.setText("手机用任意网络扫码（经服务器中转）")

    def stop(self):
        self.active = False
        self.gen += 1
        self.grab_timer.stop()
        self.top_timer.stop()
        self.link.stop()
        self.panel.seg.setEnabled(True)
        self.panel.hide()
        self.ball.hide()
        self.hide_phone()
        [d.phone_btn.setChecked(False) for d in (self.stage.desk_dock, self.stage.ppt_dock)]
        sid = self.sid
        if sid:
            threading.Thread(target=lambda: self._safe(lambda: _http(
                "POST", f"/api/remote/{sid}/action", json.dumps({"action": "session_end"}).encode())),
                daemon=True).start()
        self.sid = ""

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            pass

    # ---- 电脑画面 → 手机
    def _grab(self):
        """有接收方空闲才截屏编码: 端到端 ack 流控, 画面不变不发"""
        if not self.active or not self.sid:
            return
        now = time.time()
        hub_ready = self.link.any_ready()
        http_due = (not self.link.targets()) and not self.uploading and now - self.http_frame_at > 1.0
        if not hub_ready and not http_due:
            return
        pm = self._screen_pixmap()
        if pm.isNull():
            return
        geo = _screen_rect()
        scr = QApplication.primaryScreen()
        dpr = scr.devicePixelRatio() or 1.0
        # 多显示器(扩展到投影仪)时 grabWindow(0) 可能是整个虚拟桌面 → 只裁主屏, 与手机坐标一致
        vg = scr.virtualGeometry()
        if pm.width() > geo.width() * dpr + 2 or pm.height() > geo.height() * dpr + 2:
            pm = pm.copy(int((geo.x() - vg.x()) * dpr), int((geo.y() - vg.y()) * dpr),
                         int(geo.width() * dpr), int(geo.height() * dpr))
        scale = pm.width() / max(1, geo.width())
        ov = self.stage.ov
        og = ov.area or geo                     # 笔迹坐标相对批注窗口 (PPT 模式 = 放映区域)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if ov.board:
            p.fillRect(QRectF((og.x() - geo.x()) * scale, (og.y() - geo.y()) * scale, og.width() * scale, og.height() * scale), QColor("#FFFFFF"))
        p.translate((og.x() - geo.x()) * scale, (og.y() - geo.y()) * scale)
        ov.paint_ink(p, scale)
        p.end()
        lan = self.link.has_lan_viewer()
        # 变化检测: 缩略图哈希, 比整帧 JPEG 便宜得多
        thumb = pm.scaledToWidth(160, Qt.FastTransformation).toImage()
        h = hashlib.md5(bytes(thumb.constBits().asstring(thumb.byteCount()))).hexdigest()
        if h != self.last_hash:
            self.changed_at, self.hq_hash = now, None
        hq = False
        if h == self.last_hash:
            # 画面静止 0.35s → 补发一帧高清 (原分辨率/高质量), 手机端文字清晰; 之后不再重复发
            if self.hq_hash != h and now - self.changed_at > 0.35:
                hq = True
            elif now - self.last_upload < 3:
                return
        if hq:
            maxw, q = 2560, 88
        else:
            maxw, q = (1920, 75) if lan else (1600, 62)
        if pm.width() > maxw:
            pm = pm.scaledToWidth(maxw, Qt.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        pm.save(buf, "JPG", q)
        data = bytes(ba)
        sent = self.link.send_frame(data) if hub_ready else 0
        if sent:
            self.last_hash = h
            self.last_upload = now
            if hq:
                self.hq_hash = h
        if http_due:
            # 兜底: 手机若没连上 WebSocket, 仍可通过 HTTP 拉取 (慢速)
            self.last_hash = h
            self.http_frame_at = now
            self.uploading = True
            sid, code = self.sid, self.code

            def up():
                try:
                    _http("POST", f"/api/remote/{sid}/frame/pc?code={code}", data, "image/jpeg", timeout=15)
                    self.last_upload = time.time()
                except Exception:
                    pass
                finally:
                    self.uploading = False
            threading.Thread(target=up, daemon=True).start()

    @staticmethod
    def _screen_pixmap():
        return QApplication.primaryScreen().grabWindow(0)

    def _on_phone_frame(self, data: bytes):
        if not self.phone_view.isVisible():
            self.show_phone()
        self.phone_view.set_frame(data)

    # ---- 手机指令
    def _poll_loop(self, gen, sid):
        after = 0
        first = True
        while self.active and gen == self.gen:
            try:
                _, _, raw = _http("GET", f"/api/remote/{sid}/poll?after={after}", timeout=10)
                d = json.loads(raw.decode())
                evs = d.get("events") or []
                for e in evs:
                    after = max(after, int(e.get("idx") or 0))
                    if not first and not (e.get("via") == "hub" and self.link.server_ok):
                        self.sig.event.emit(e)
                if first:
                    first = False
                    after = max(after, int(d.get("latest") or 0))
                time.sleep(1.0 if self.link.server_ok else (0.12 if evs else 0.3))
            except Exception:
                time.sleep(1.5)

    def _on_event(self, e: dict):
        if not self.active:
            return
        t = e.get("type")
        if t == "hello" and e.get("role") != "desktop":
            self.panel.state.setText("手机已连接 ✓")
            return
        if t == "bye":
            return
        a = e.get("action") or ""
        d = e.get("data") or {}
        if e.get("sender") == "desktop":
            return
        ov = self.stage.ov
        if a in ("next", "prev", "first", "last", "black", "white", "board", "undo", "clear", "timer"):
            self.stage.handle(a)
        elif a == "ink":
            ov.remote_ink(d)
        elif a == "erase":
            ov.remote_erase(d)
        elif a == "laser":
            ov.remote_laser(d)
        elif a == "cast_start":
            self.show_phone()
        elif a == "cast_stop":
            self.hide_phone()
        elif a == "hello_phone":
            self.panel.state.setText("手机已连接 ✓")

    # ---- 手机画面 → 电脑
    def _phone_loop(self, gen, sid, code):
        seq = 0
        while self.active and gen == self.gen:
            if self.link.server_ok or self.link.lan_targets:
                time.sleep(0.5)
                continue
            try:
                st, hdr, raw = _http("GET", f"/api/remote/{sid}/frame/phone?code={code}&after={seq}", timeout=15)
                if st == 200 and raw:
                    seq = int(hdr.get("X-Seq") or seq + 1)
                    self.sig.phone_frame.emit(raw)
                    time.sleep(0.05)
                else:
                    time.sleep(0.35)
            except Exception:
                time.sleep(1.5)

    def show_phone(self):
        self.phone_view.popup()
        ov = self.stage.ov
        if ov.isVisible():
            ov.raise_()
        self.stage.refresh_holes()
        for w in self.stage.floating:
            if w.isVisible():
                w.raise_()
        self._keep_top()
        if not self.stage.dock.isVisible() and not self.stage.dock.mini.isVisible():
            self.stage.start()

    def open_mirror(self):
        """Windows 自带 Miracast 接收端: 安卓/华为/小米等手机的「无线投屏」可直接把整屏投到本机"""
        from qfluentwidgets import MessageBox
        self.stage.handle("mouse")
        self.main.bring_up()
        box = MessageBox(
            "手机整屏镜像到电脑",
            "1. 在即将打开的「投影到此电脑」设置中，开启“所有位置都可用”\n"
            "   （首次使用需点“可选功能”安装「无线显示器」，约 1 分钟）\n"
            "2. 点“启动‘连接’应用以投影到此电脑”\n"
            "3. 手机下拉控制中心 →「无线投屏 / 多屏互动 / Smart View」→ 选择本电脑\n\n"
            "· 手机与电脑需在同一 Wi-Fi；iPhone 的 AirPlay 不被 Windows 原生支持，请用上方扫码投屏。",
            self.main)
        box.yesButton.setText("打开设置")
        box.cancelButton.setText("取消")
        if box.exec() and core.IS_WIN:
            import os
            for uri in ("ms-settings:project",):
                try:
                    os.startfile(uri)  # noqa
                except Exception:
                    pass
            try:
                import subprocess
                subprocess.Popen(["explorer.exe", "shell:AppsFolder\\Microsoft.Windows.SecondaryTileExperience_cw5n1h2txyewy!App"])
            except Exception:
                pass

    def hide_phone(self):
        self.phone_view.hide()
