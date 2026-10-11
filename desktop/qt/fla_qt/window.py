"""主窗口 (qfluentwidgets FluentWindow)"""
from __future__ import annotations

import os
import threading
import webbrowser

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QSystemTrayIcon,
                             QTableWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (Action, BodyLabel, CheckBox, ComboBox, CaptionLabel, CardWidget, FluentWindow, IconWidget, InfoBar, Slider,
                            InfoBarPosition, LineEdit, MessageBox, NavigationItemPosition, PasswordLineEdit,
                            PrimaryPushButton, ProgressBar, PushButton, RoundMenu, ScrollArea, SearchLineEdit,
                            StrongBodyLabel, SubtitleLabel, SwitchButton, TableWidget, TitleLabel)

from . import core
from .core import cfg


class _Sig(QObject):
    done = pyqtSignal(object, object)


def open_web(to: str = ""):
    """用浏览器打开网页端; 已登录则带 token 免登录"""
    import urllib.parse
    base = cfg.server.rstrip("/")
    if cfg.token:
        webbrowser.open(f"{base}/#/auth?token={urllib.parse.quote(cfg.token)}&to={urllib.parse.quote(to)}")
    else:
        webbrowser.open(f"{base}/#/{to}" if to else base)


def run_async(fn, cb=None):
    """在线程里跑 fn(), 完成后回到 UI 线程调用 cb(result)"""
    s = _Sig()

    def finish(r, _s):
        _keep.discard(_s)
        if cb:
            cb(r)
    s.done.connect(finish)
    _keep.add(s)

    def work():
        try:
            r = fn()
        except Exception as e:  # noqa
            r = (0, {"detail": str(e)})
        s.done.emit(r, s)
    threading.Thread(target=work, daemon=True).start()


_keep: set = set()


def fmt_size(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


class Page(ScrollArea):
    def __init__(self, name, title, sub=""):
        super().__init__()
        self.setObjectName(name)
        self.view = QWidget()
        self.view.setObjectName("view")
        self.lay = QVBoxLayout(self.view)
        self.lay.setContentsMargins(36, 28, 36, 28)
        self.lay.setSpacing(14)
        self.lay.addWidget(TitleLabel(title))
        if sub:
            c = CaptionLabel(sub)
            c.setTextColor("#666666", "#aaaaaa")
            self.lay.addWidget(c)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()


class StatusCard(CardWidget):
    def __init__(self, icon, title, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        iw = IconWidget(icon)
        iw.setFixedSize(28, 28)
        h.addWidget(iw)
        v = QVBoxLayout()
        v.setSpacing(2)
        self.t = StrongBodyLabel(title)
        self.d = CaptionLabel("检测中…")
        v.addWidget(self.t)
        v.addWidget(self.d)
        h.addLayout(v, 1)

    def set(self, text, ok=True):
        self.d.setText(text)
        self.d.setTextColor("#2e7d32" if ok else "#c62828", "#81c784" if ok else "#ef9a9a")


# ================================================================== 首页
class HomePage(Page):
    def __init__(self, win):
        super().__init__("home", "FLA", f"轻量 · 高效 · 帮助　v{core.VERSION}")
        self.win = win
        self.c_server = StatusCard(FIF.GLOBE, "服务器")
        self.c_bridge = StatusCard(FIF.LINK, "网页联动 (127.0.0.1:8307)")
        self.c_office = StatusCard(FIF.PROJECTOR, "放映软件")
        self.c_user = StatusCard(FIF.PEOPLE, "账号")
        for c in (self.c_server, self.c_bridge, self.c_office, self.c_user):
            self.lay.addWidget(c)
        row = QHBoxLayout()
        b1 = PrimaryPushButton(FIF.EDIT, "打开放映工具盒")
        b1.clicked.connect(win.stage.start)
        b2 = PushButton(FIF.GLOBE, "打开网页端")
        b2.clicked.connect(lambda: open_web(""))
        b3 = PushButton(FIF.STOP_WATCH, "计时器")
        b3.clicked.connect(win.stage.timer.popup)
        b4 = PushButton(FIF.PHONE, "手机投屏 / 观看")
        b4.clicked.connect(lambda: (win.stage.start(), win.stage.handle("phone")))
        for b in (b1, b4, b2, b3):
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)
        row2 = QHBoxLayout()
        for ic, t, to in ((FIF.LIBRARY, "课件库", "library"), (FIF.MESSAGE, "聊天", "chat"),
                          (FIF.CHAT, "论坛", "forum"), (FIF.PEOPLE, "个人中心", "profile")):
            b = PushButton(ic, t)
            b.clicked.connect(lambda _=False, to=to: win.switchTo(getattr(win, to)))
            row2.addWidget(b)
        row2.addStretch(1)
        self.lay.addLayout(row2)
        self.lay.addStretch(1)

    def refresh(self, bridge_ok):
        exe, kind = core.find_presenter()
        self.c_office.set(f"{kind}：{exe}" if exe else "未找到 PowerPoint / WPS，将用系统默认程序打开", bool(exe))
        self.c_bridge.set("已开启，网页端可一键调起本地放映" if bridge_ok else
                          "端口 8307 被占用，网页端一键放映不可用", bridge_ok)
        self.c_user.set(f"已登录：{cfg.user}" if cfg.token else "未登录（在「设置」中登录后可浏览云端课件）",
                        bool(cfg.token))
        self.c_server.set(f"{cfg.server} · 连接中…", True)

        def done(r):
            st, _ = r
            self.c_server.set(f"{cfg.server} · {'在线' if st and st < 500 else '无法连接'}", bool(st and st < 500))
        run_async(lambda: core.api("GET", "/api/health", timeout=10), done)


# ================================================================== 课件库
class LibraryPage(Page):
    def __init__(self, win):
        super().__init__("library", "课件库")
        self.win = win
        self.files = []
        bar = QHBoxLayout()
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("搜索课件")
        self.search.textChanged.connect(self.fill)
        self.search.setFixedWidth(260)
        rb = PushButton(FIF.SYNC, "刷新")
        rb.clicked.connect(self.load)
        ub = PrimaryPushButton(FIF.CLOUD, "上传课件")
        ub.clicked.connect(self.pick_upload)
        bar.addWidget(self.search)
        bar.addStretch(1)
        bar.addWidget(ub)
        bar.addWidget(rb)
        self.setAcceptDrops(True)
        self._uploading = False
        self.lay.addLayout(bar)
        self.tip = CaptionLabel("")
        self.lay.addWidget(self.tip)
        self.table = TableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["名称", "类型", "大小", "页数"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setMinimumHeight(420)
        self.table.cellDoubleClicked.connect(self.open_row)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.menu)
        self.lay.addWidget(self.table, 1)
        self.prog = ProgressBar()
        self.prog.hide()
        self.lay.addWidget(self.prog)

    def load(self):
        if not cfg.token:
            self.files = []
            self.fill()
            self.tip.setText("请先到「设置」登录账号")
            return
        self.tip.setText("加载中…")

        def done(r):
            st, data = r
            if st == 401:
                self.tip.setText("登录已失效，请到「设置」重新登录")
                return
            if st != 200 or not isinstance(data, list):
                self.tip.setText("加载失败：" + str((data or {}).get("detail", "无法连接服务器") if isinstance(data, dict) else st))
                return
            self.files = data
            self.tip.setText(f"共 {len(data)} 个课件" if data else "云端还没有课件 — 点右上「上传课件」或把文件拖进来")
            self.fill()
        run_async(lambda: core.api("GET", "/api/files"), done)

    # ---------- 上传 ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isfile(p)]
        if paths:
            self.upload(paths)

    def pick_upload(self):
        from PyQt5.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要上传的课件", os.path.expanduser("~"),
            "课件 (*.ppt *.pptx *.pps *.ppsx *.pdf *.doc *.docx *.xls *.xlsx *.key *.odp *.png *.jpg *.jpeg *.mp4 *.mp3);;所有文件 (*.*)")
        if paths:
            self.upload(paths)

    def upload(self, paths):
        if not cfg.token:
            self.tip.setText("请先到「设置」登录账号再上传")
            return
        if self._uploading:
            self.tip.setText("正在上传，请稍候…")
            return
        self._uploading = True
        state = {"i": 0, "done": 0, "total": 1, "ok": 0, "err": []}
        self.prog.setValue(0)
        self.prog.show()
        timer = QTimer(self)

        def tick():
            i = min(state["i"], len(paths) - 1)
            pct = int(state["done"] * 100 / max(1, state["total"]))
            self.prog.setValue(pct)
            self.tip.setText(f"上传中 ({i + 1}/{len(paths)}) {os.path.basename(paths[i])} … {pct}%")
        timer.timeout.connect(tick)
        timer.start(200)

        def prog(d, t):
            state["done"], state["total"] = d, t

        def work():
            for i, p in enumerate(paths):
                state["i"], state["done"] = i, 0
                st, data = core.upload(p, prog)
                if st == 200:
                    state["ok"] += 1
                else:
                    det = data.get("detail") if isinstance(data, dict) else st
                    state["err"].append(f"{os.path.basename(p)}：{det or '无法连接服务器'}")
            return state

        def done(_r):
            timer.stop()
            self._uploading = False
            self.prog.hide()
            ok, err = state["ok"], state["err"]
            msg = f"已上传 {ok} 个课件" + (f"，{len(err)} 个失败" if err else "")
            (InfoBar.success if not err else InfoBar.warning)(
                "上传完成" if not err else "部分失败", msg + ("\n" + "\n".join(err[:5]) if err else ""),
                parent=self.win, position=InfoBarPosition.TOP, duration=6000 if err else 3000)
            self.load()
        run_async(work, done)

    def visible(self):
        q = self.search.text().strip().lower()
        return [f for f in self.files if not q or q in (f.get("name") or "").lower()]

    def fill(self):
        rows = self.visible()
        self.table.setRowCount(len(rows))
        for i, f in enumerate(rows):
            for j, v in enumerate([f.get("name", ""), (f.get("ext") or "").upper(), fmt_size(f.get("size")),
                                   str(f.get("pages") or "")]):
                it = QTableWidgetItem(v)
                if j:
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, j, it)

    def menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        m = RoundMenu(parent=self)
        m.addAction(Action(FIF.PLAY, "放映", triggered=lambda: self.open_row(row, 0)))
        m.addAction(Action(FIF.GLOBE, "在网页端查看", triggered=lambda: webbrowser.open(cfg.server)))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def open_row(self, row, _col):
        rows = self.visible()
        if 0 <= row < len(rows):
            f = rows[row]
            self.win.open_file({"url": f"/api/files/{f['id']}/download", "name": f.get("name") or "课件",
                                "token": cfg.token, "fid": str(f["id"])})


# ================================================================== 设置
def dock_tools():
    """工具盒可隐藏的工具 (顺序与工具盒一致)"""
    return [("prev", "上一页/步"), ("next", "下一页/步"), ("mouse", "鼠标"), ("pen", "画笔"),
            ("marker", "荧光笔"), ("laser", "激光笔"), ("eraser", "橡皮"), ("undo", "撤销"),
            ("clear", "清除笔迹"), ("board", "白板"), ("black", "黑屏"), ("timer", "计时器"),
            ("phone", "手机投屏"), ("main", "主窗口")]


class SettingsPage(Page):
    def __init__(self, win):
        super().__init__("settings", "设置")
        self.win = win

        def card(title):
            c = CardWidget()
            v = QVBoxLayout(c)
            v.setContentsMargins(20, 16, 20, 16)
            v.setSpacing(10)
            v.addWidget(SubtitleLabel(title))
            self.lay.addWidget(c)
            return v

        v = card("服务器线路")
        r = QHBoxLayout()
        r.addWidget(BodyLabel("连接到"), 0)
        self.server = ComboBox()
        for url, label in core.SERVERS:
            self.server.addItem(label, userData=url)
        self.server.setCurrentIndex(core.ALLOWED.index(cfg.server))
        self.server.currentIndexChanged.connect(self.save_server)
        self.server.setMinimumWidth(260)
        r.addWidget(self.server)
        r.addStretch(1)
        v.addLayout(r)
        tip = CaptionLabel("两条线路数据互通；某条线路慢或打不开时切换到另一条。")
        tip.setTextColor("#666666", "#aaaaaa")
        v.addWidget(tip)

        v = card("账号")
        self.acc_state = BodyLabel("")
        v.addWidget(self.acc_state)
        self.user = LineEdit()
        self.user.setPlaceholderText("用户名")
        self.pwd = PasswordLineEdit()
        self.pwd.setPlaceholderText("密码")
        self.pwd.returnPressed.connect(self.login)
        r = QHBoxLayout()
        r.addWidget(self.user)
        r.addWidget(self.pwd)
        self.login_btn = PrimaryPushButton("登录")
        self.login_btn.clicked.connect(self.login)
        self.logout_btn = PushButton("退出登录")
        self.logout_btn.clicked.connect(self.logout)
        r.addWidget(self.login_btn)
        r.addWidget(self.logout_btn)
        v.addLayout(r)

        v = card("放映")
        for text, attr in [("屏蔽希沃白板注入的 PPT 悬浮工具条", "seewo"), ("启动时自动显示放映工具盒", "dock_on_start")]:
            r = QHBoxLayout()
            r.addWidget(BodyLabel(text), 1)
            sw = SwitchButton()
            sw.setOnText("开")
            sw.setOffText("关")
            sw.setChecked(getattr(cfg, attr))
            sw.checkedChanged.connect(lambda on, a=attr: (setattr(cfg, a, on), cfg.save()))
            r.addWidget(sw)
            v.addLayout(r)

        v = card("工具盒个性化 (放映工具栏)")
        r = QHBoxLayout()
        r.addWidget(BodyLabel("按钮大小"), 0)
        self.dock_scale = Slider(Qt.Horizontal)
        self.dock_scale.setRange(80, 150)
        self.dock_scale.setValue(int(round(cfg.dock_scale * 100)))
        self.dock_scale_lab = CaptionLabel(f"{int(round(cfg.dock_scale * 100))}%")
        self.dock_scale.valueChanged.connect(self.on_dock_scale)
        r.addWidget(self.dock_scale, 1)
        r.addWidget(self.dock_scale_lab, 0)
        v.addLayout(r)
        v.addWidget(CaptionLabel("勾选的工具会显示在放映工具盒中，取消勾选即隐藏。收起/结束按钮始终显示。"))
        self.dock_checks = {}
        grid = QHBoxLayout()
        for key, title in dock_tools():
            cb = CheckBox(title)
            cb.setChecked(key not in cfg.dock_hidden)
            cb.toggled.connect(lambda on, k=key: self.on_dock_tool(k, on))
            self.dock_checks[key] = cb
            grid.addWidget(cb)
        grid.addStretch(1)
        v.addLayout(grid)

        v = card("存储位置 (缓存 / 下载)")
        self.storage_warn = CaptionLabel("")
        self.storage_warn.setTextColor("#c0392b", "#ff8a80")
        v.addWidget(self.storage_warn)
        self.storage_rows = {}
        for key, title, getter, attr in (
                ("cache", "课件缓存", core.cache_dir, "cache_dir"),
                ("download", "下载保存", core.download_dir, "download_dir")):
            r = QHBoxLayout()
            r.addWidget(BodyLabel(title), 0)
            lab = CaptionLabel("")
            lab.setWordWrap(True)
            r.addWidget(lab, 1)
            mod = PushButton(FIF.EDIT, "修改")
            opn = PushButton(FIF.FOLDER, "打开")
            mod.clicked.connect(lambda _=False, a=attr, t=title: self.change_storage(a, t))
            opn.clicked.connect(lambda _=False, g=getter: self.open_dir(g()))
            r.addWidget(mod)
            r.addWidget(opn)
            v.addLayout(r)
            self.storage_rows[key] = (lab, getter)
        tip = CaptionLabel("建议放在 D 盘等非系统盘：学校电脑的冰点还原只会清空 C 盘。修改后立即生效，已缓存的课件不会自动迁移。")
        tip.setWordWrap(True)
        tip.setTextColor("#666666", "#aaaaaa")
        v.addWidget(tip)
        self.refresh_storage()

        v = card("关于")
        v.addWidget(BodyLabel(f"{core.APP_NAME}  v{core.VERSION}"))
        r = QHBoxLayout()
        ub = PushButton(FIF.UPDATE, "检查更新")
        ub.clicked.connect(lambda: win.check_update(manual=True))
        cb = PushButton(FIF.FOLDER, "打开课件缓存目录")
        r.addWidget(ub)
        r.addStretch(1)
        v.addLayout(r)
        self.lay.addStretch(1)
        self.sync()

    def on_dock_scale(self, val):
        cfg.dock_scale = round(val / 100, 2)
        cfg.save()
        self.dock_scale_lab.setText(f"{val}%")
        self.apply_dock()

    def on_dock_tool(self, key, on):
        hidden = set(cfg.dock_hidden)
        (hidden.discard if on else hidden.add)(key)
        cfg.dock_hidden = sorted(hidden)
        cfg.save()
        self.apply_dock()

    def apply_dock(self):
        stage = getattr(self.win, "stage", None)
        if stage is not None:
            stage.apply_dock_prefs()

    def refresh_storage(self):
        for key, (lab, getter) in self.storage_rows.items():
            lab.setText(getter())
        self.storage_warn.setText(core.storage_warning())
        self.storage_warn.setVisible(bool(core.storage_warning()))

    def change_storage(self, attr, title):
        cur = getattr(cfg, attr) or ""
        d = QFileDialog.getExistingDirectory(self, f"选择{title}位置", cur or core.default_storage_root())
        if not d:
            return
        setattr(cfg, attr, os.path.normpath(d))
        cfg.save()
        self.refresh_storage()
        self.win.toast(f"{title}位置已改为：{d}")

    @staticmethod
    def open_dir(path):
        try:
            if core.IS_WIN:
                os.startfile(path)  # noqa
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def sync(self):
        logged = bool(cfg.token)
        self.acc_state.setText(f"已登录：{cfg.user}" if logged else "未登录")
        for w in (self.user, self.pwd, self.login_btn):
            w.setVisible(not logged)
        self.logout_btn.setVisible(logged)

    def save_server(self, *_):
        url = self.server.currentData() or core.DEFAULT_SERVER
        if url == cfg.server:
            return
        cfg.server = url
        cfg.save()
        self.win.toast(f"已切换到 {url.replace('https://', '')}")
        self.win.refresh_all()

    def login(self):
        u, p = self.user.text().strip(), self.pwd.text()
        if not u or not p:
            self.win.toast("请输入用户名和密码", ok=False)
            return
        self.login_btn.setEnabled(False)

        def done(r):
            self.login_btn.setEnabled(True)
            st, data = r
            if st == 200 and isinstance(data, dict) and data.get("token"):
                cfg.token = data["token"]
                user = data.get("user") or {}
                cfg.user = user.get("nickname") or user.get("username") or u
                cfg.save()
                self.pwd.clear()
                self.win.toast(f"已登录 {cfg.user}")
                self.sync()
                self.win.refresh_all()
            elif st == 0:
                self.win.toast("无法连接服务器，请检查服务器地址", ok=False)
            else:
                self.win.toast("登录失败：" + str((data or {}).get("detail", "账号或密码错误")), ok=False)
        run_async(lambda: core.api("POST", "/api/auth/login", {"username": u, "password": p}), done)

    def logout(self):
        cfg.token = ""
        cfg.user = ""
        cfg.save()
        self.sync()
        self.win.refresh_all()


# ================================================================== 主窗口
class MainWindow(FluentWindow):
    def __init__(self, stage, bridge_ok):
        super().__init__()
        self.stage = stage
        self.bridge_ok = bridge_ok
        from .cast import CastController
        self.cast = CastController(stage)
        stage.cast = self.cast
        self.cast.main = self
        self.setWindowTitle(core.APP_NAME)
        self.resize(940, 680)
        self.home = HomePage(self)
        self.library = LibraryPage(self)
        self.settings = SettingsPage(self)
        from .social import AnnPage, ChatPage, ForumPage, ProfilePage
        from .adminpage import AdminPage
        self.me = {}
        self._bg = None
        self._op = 100
        self.anns = AnnPage(self)
        self.admin = AdminPage(self)
        self.chat = ChatPage(self)
        self.forum = ForumPage(self)
        self.profile = ProfilePage(self)
        self.addSubInterface(self.home, FIF.HOME, "首页")
        self.addSubInterface(self.library, FIF.LIBRARY, "课件库")
        self.addSubInterface(self.chat, FIF.MESSAGE, "聊天")
        self.addSubInterface(self.forum, FIF.CHAT, "论坛")
        self.addSubInterface(self.anns, FIF.MEGAPHONE, "公告")
        self.addSubInterface(self.admin, FIF.CERTIFICATE, "管理", NavigationItemPosition.BOTTOM)
        self.navigationInterface.widget(self.admin.objectName()).setVisible(False)
        self.addSubInterface(self.profile, FIF.PEOPLE, "个人中心", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
        self.navigationInterface.setExpandWidth(180)
        self.stackedWidget.currentChanged.connect(lambda _: self.library.load()
                                                  if self.stackedWidget.currentWidget() is self.library else None)
        self._tray()
        r = QApplication.primaryScreen().availableGeometry()
        self.move(r.center().x() - self.width() // 2, r.center().y() - self.height() // 2)
        self.refresh_all()
        QTimer.singleShot(2500, lambda: self.check_update(manual=False))
        QTimer.singleShot(300, self.load_theme)

    # ---- 托盘: 关窗口 = 最小化到托盘, 桥接继续工作
    def _tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        m = RoundMenu(parent=self)
        m.addAction(Action(FIF.HOME, "打开主窗口", triggered=self.bring_up))
        m.addAction(Action(FIF.EDIT, "放映工具盒", triggered=self.stage.start))
        m.addAction(Action(FIF.STOP_WATCH, "计时器", triggered=self.stage.timer.popup))
        m.addAction(Action(FIF.MOVE, "退出批注（恢复鼠标）", triggered=lambda: self.stage.handle("mouse")))
        m.addSeparator()
        m.addAction(Action(FIF.CLOSE, "退出 FLA", triggered=self.quit_app))
        self.tray_menu = m
        self.tray.setToolTip(core.APP_NAME)
        self.tray.activated.connect(lambda reason: self.bring_up() if reason == QSystemTrayIcon.Trigger
                                    else (m.exec(self.cursor().pos()) if reason == QSystemTrayIcon.Context else None))
        self.tray.show()
        self._tray_hinted = False

    def closeEvent(self, e):
        if self.tray.isVisible():
            e.ignore()
            self.hide()
            if not self._tray_hinted:
                self._tray_hinted = True
                self.tray.showMessage(core.APP_NAME, "FLA 仍在后台运行（网页一键放映可用），右键托盘图标可退出。")
        else:
            e.accept()

    def quit_app(self):
        pend = getattr(self, "pending_update", None)
        if pend and os.path.exists(pend):
            core.apply_update(pend, restart=False)      # 「稍后」的更新: 退出时静默安装, 下次打开即新版
            self.pending_update = None
        if self.cast.active:
            self.cast.stop()
        self.stage.shutdown()
        self.tray.hide()
        QApplication.quit()

    def bring_up(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toast(self, text, ok=True):
        (InfoBar.success if ok else InfoBar.error)("", text, parent=self, position=InfoBarPosition.TOP,
                                                   duration=2500 if ok else 4000)

    # ---- 账号 / 公告 / 外观
    def refresh_me(self):
        if not cfg.token:
            self.me = {}
            self.navigationInterface.widget(self.admin.objectName()).setVisible(False)
            return

        def done(r):
            st, d = r
            self.me = d if st == 200 and isinstance(d, dict) else {}
            self.navigationInterface.widget(self.admin.objectName()).setVisible(self.me.get("role") == "admin")
            self.anns.load(popup=True)
        run_async(lambda: core.api("GET", "/api/auth/me"), done)

    def set_ann_badge(self, n):
        try:
            w = self.navigationInterface.widget(self.anns.objectName())
            w.setText(f"公告 ({n})" if n else "公告")
        except Exception:
            pass

    def load_theme(self):
        def work():
            st, c = core.api("GET", "/api/auth/config")
            if st != 200 or not isinstance(c, dict):
                return None
            bg = c.get("client_bg") or ""
            data = core.fetch_bytes(bg) if bg.startswith(("/", "http")) else None
            return c, bg, data

        def done(r):
            if not r:
                return
            c, bg, data = r
            from PyQt5.QtGui import QColor, QPixmap
            self._op = int(c.get("ui_opacity") or 100)
            self._bg = None
            if data:
                pm = QPixmap()
                if pm.loadFromData(data):
                    self._bg = pm
            elif bg.startswith("#"):
                self._bg = QColor(bg)
            self._apply_bg()
        run_async(work, done)

    def apply_theme_preview(self, op):
        self._op = op
        self._apply_bg()

    def _apply_bg(self):
        on = self._bg is not None or self._op < 100
        self.stackedWidget.setStyleSheet("background: transparent;" if on else "")
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._bg is None:
            return
        from PyQt5.QtGui import QColor, QPainter, QPixmap
        p = QPainter(self)
        r = self.rect()
        if isinstance(self._bg, QPixmap):
            pm = self._bg.scaled(r.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((r.width() - pm.width()) // 2, (r.height() - pm.height()) // 2, pm)
        else:
            p.fillRect(r, self._bg)
        p.fillRect(r, QColor(243, 243, 243, int(255 * self._op / 100)))
        p.end()

    def refresh_all(self):
        self.refresh_me()
        self.home.refresh(self.bridge_ok)
        self.settings.sync()
        if self.stackedWidget.currentWidget() is self.library:
            self.library.load()

    # ---- 下载并放映
    def open_file(self, job: dict):
        name = job.get("name") or "课件.pptx"
        path = core.cache_path(name)
        prog = self.library.prog
        prog.setValue(0)
        prog.show()
        self.tray.showMessage(core.APP_NAME, f"正在下载：{name}")
        sig = _Progress()
        sig.p.connect(lambda v: prog.setValue(v))

        def work():
            def cb(got, total):
                if total:
                    sig.p.emit(int(got * 100 / total))
            return core.download(job["url"], path, job.get("token") or "", cb)

        def done(ok):
            prog.hide()
            _keep.discard(sig)
            if ok is not True:
                MessageBox("课件下载失败", "无法连接服务器，或登录已失效。请检查网络后重试。", self).exec()
                return
            try:
                core.launch_presentation(path)
            except Exception as ex:
                MessageBox("无法打开课件", str(ex), self).exec()
                return
            QTimer.singleShot(1500, self.stage.start)
        _keep.add(sig)
        run_async(work, done)

    # ---- 自更新: 每次启动自动检查 → 后台静默下载 → 下载好后询问立即重启; 选「稍后」则退出时自动安装
    def check_update(self, manual=False):
        def done(r):
            st, data = r
            if st != 200 or not isinstance(data, dict):
                if manual:
                    self.toast("检查更新失败（无法连接服务器）", ok=False)
                return
            ver = data.get("version") or ""
            if not core.version_newer(ver):
                if manual:
                    self.toast(f"已是最新版本 v{core.VERSION}")
                return
            self.do_update(data.get("download_url") or "/api/desktop/download", ver, data, manual)
        run_async(lambda: core.api("GET", "/api/desktop/version", timeout=10), done)

    def do_update(self, url, ver="", data=None, manual=False):
        if not (core.IS_WIN and (core.BUNDLED or getattr(__import__("sys"), "frozen", False))):
            if manual:
                webbrowser.open(core.full_url(url))
            return
        if getattr(self, "_updating", False):
            return
        self._updating = True
        dest = core.update_temp_path()
        if manual:
            self.toast(f"正在后台下载新版本 v{ver}…")

        def fetch():
            ok = core.download(url, dest, timeout=60)
            if ok is True:
                try:
                    with open(dest, "rb") as f:
                        ok = f.read(2) == b"MZ"           # 必须是 Windows 可执行文件
                    size = int((data or {}).get("size") or 0)
                    if ok and size and abs(os.path.getsize(dest) - size) > 16:
                        ok = False
                except Exception:
                    ok = False
            return ok

        def done(ok):
            self._updating = False
            if ok is not True:
                if manual:
                    self.toast("自动更新下载失败，已为你打开下载页", ok=False)
                    webbrowser.open(core.full_url(url))
                return
            self.pending_update = dest
            log = (data or {}).get("changelog") or []
            text = f"新版本 v{ver} 已下载完成（当前 v{core.VERSION}）\n\n" + "\n".join(
                "· " + (x if isinstance(x, str) else str(x)) for x in log[:6]) + "\n\n立即重启完成更新？选择「稍后」将在退出 FLA 时自动安装。"
            box = MessageBox("更新已就绪", text, self)
            box.yesButton.setText("立即重启")
            box.cancelButton.setText("稍后")
            if box.exec() and core.apply_update(dest):
                self.pending_update = None
                self.quit_app()
        run_async(fetch, done)


class _Progress(QObject):
    p = pyqtSignal(int)
