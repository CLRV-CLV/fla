"""主窗口 (qfluentwidgets FluentWindow)"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import webbrowser

from PyQt5.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPalette
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                             QListWidgetItem, QSystemTrayIcon, QTableWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (Action, BodyLabel, CheckBox, ComboBox, CaptionLabel, CardWidget, FluentWindow,
                            IconWidget, InfoBar, InfoBarPosition, LineEdit, ListWidget, MessageBox,
                            NavigationItemPosition, PasswordLineEdit, PlainTextEdit, PrimaryPushButton, ProgressBar, PushButton,
                            RoundMenu, ScrollArea, SearchLineEdit, StrongBodyLabel, SubtitleLabel, SwitchButton,
                            TableWidget, TextBrowser, TitleLabel, TransparentToolButton)

from . import core, experiment_extensions
from .core import cfg
from .screen_layout import fit_window_rect


class _Sig(QObject):
    done = pyqtSignal(object, object)


class _ExtensionProgress(QObject):
    changed = pyqtSignal(int, int)


def classroom_url(tab: str = "") -> str:
    """课堂工具页地址: 优先用随程序分发的本地文件 (断网也能用), 否则走服务器"""
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (getattr(sys, "_MEIPASS", ""), os.path.join(here, "..", "..", "web"),
                 os.path.join(here, "..", "..", "..", "web")):
        if base:
            p = os.path.join(base, "classroom.html")
            if os.path.exists(p):
                u = QUrl.fromLocalFile(os.path.abspath(p))
                if tab:
                    u.setFragment(tab)
                return u.toString()
    u = QUrl(cfg.server.rstrip("/") + "/classroom.html")
    if tab:
        u.setFragment(tab)
    return u.toString()


def open_classroom(tab: str = ""):
    QDesktopServices.openUrl(QUrl(classroom_url(tab)))


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
        b5 = PrimaryPushButton(FIF.PEOPLE, "课堂工具")
        b5.clicked.connect(lambda: open_classroom("pick"))
        for b in (b1, b5, b4, b2, b3):
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)
        row2 = QHBoxLayout()
        for ic, t, to in ((FIF.LIBRARY, "课件库", "library"), (FIF.EDUCATION, "AI 助教", "ai"),
                          (FIF.EDUCATION, "课堂工具", "experiments"), (FIF.MESSAGE, "聊天", "chat"),
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


# ================================================================== AI 助教
class _AIEvents(QObject):
    event = pyqtSignal(object)


class _AIInput(PlainTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            event.accept()
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class _AIMessageBubble(QWidget):
    """Theme-aware message card; assistant content uses Qt's native Markdown renderer."""

    def __init__(self, role, text="", parent=None):
        super().__init__(parent)
        self.role = role
        self.content = ""
        self._fitting = False
        self.setObjectName("aiMessageRow")

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 3, 2, 3)
        row.setSpacing(10)
        self.avatar = BodyLabel("AI" if role == "assistant" else "我", self)
        self.avatar.setAlignment(Qt.AlignCenter)
        self.avatar.setFixedSize(34, 34)

        self.card = CardWidget(self)
        self.card.setObjectName("aiBubbleAssistant" if role == "assistant" else "aiBubbleUser")
        self.card.setMaximumWidth(720)
        self.card.setMinimumWidth(180 if role == "assistant" else 76)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 10, 14, 12)
        card_layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(6)
        name = StrongBodyLabel("FLA 助教" if role == "assistant" else "你", self.card)
        header.addWidget(name)
        header.addStretch(1)
        self.copy_btn = TransparentToolButton(FIF.COPY, self.card)
        self.copy_btn.setToolTip("复制内容")
        self.copy_btn.setFixedSize(28, 28)
        self.copy_btn.clicked.connect(lambda _=False: QApplication.clipboard().setText(self.content))
        header.addWidget(self.copy_btn)
        card_layout.addLayout(header)

        self.body = TextBrowser(self.card)
        self.body.setReadOnly(True)
        self.body.setOpenLinks(False)
        self.body.setOpenExternalLinks(False)
        self.body.setFrameShape(QFrame.NoFrame)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.setMinimumHeight(26)
        self.body.setMinimumWidth(70 if role == "user" else 220)
        self.body.document().setDocumentMargin(0)
        self.body.anchorClicked.connect(self._open_link)
        card_layout.addWidget(self.body)

        if role == "assistant":
            row.addWidget(self.avatar, 0, Qt.AlignTop)
            row.addWidget(self.card, 1, Qt.AlignTop)
        else:
            row.addStretch(1)
            row.addWidget(self.card, 0, Qt.AlignTop)
            row.addWidget(self.avatar, 0, Qt.AlignTop)
        self._apply_palette()
        self.set_content(text, markdown=(role == "assistant"))

    def _apply_palette(self):
        palette = QApplication.palette()
        base = palette.color(QPalette.Base).name()
        alternate = palette.color(QPalette.AlternateBase).name()
        text = palette.color(QPalette.Text).name()
        muted = palette.color(QPalette.PlaceholderText).name()
        border = palette.color(QPalette.Mid).name()
        accent = palette.color(QPalette.Highlight).name()
        accent_text = palette.color(QPalette.HighlightedText).name()
        if self.role == "assistant":
            bg, fg, outline = base, text, border
            avatar_bg, avatar_fg = alternate, text
        else:
            bg, fg, outline = accent, accent_text, accent
            avatar_bg, avatar_fg = alternate, text
        self.card.setStyleSheet(
            f"QWidget#{self.card.objectName()} {{ background-color:{bg}; color:{fg}; "
            f"border:1px solid {outline}; border-radius:12px; }}"
            f"QWidget#{self.card.objectName()} QLabel {{ color:{fg}; background:transparent; border:0; }}"
        )
        self.avatar.setStyleSheet(
            f"QLabel {{ background-color:{avatar_bg}; color:{avatar_fg}; border-radius:17px; "
            f"font-weight:600; }}"
        )
        self.body.setPalette(palette)
        self.body.setStyleSheet(f"QTextBrowser {{ background:transparent; border:0; color:{fg}; }}")
        self.body.document().setDefaultStyleSheet(
            f"body {{ color:{fg}; font-family:'Microsoft YaHei UI','Segoe UI',sans-serif; font-size:14px; }}"
            f"p {{ margin:0 0 8px 0; }} h1,h2,h3 {{ margin:10px 0 6px 0; }}"
            f"a {{ color:{accent}; text-decoration:none; }}"
            f"pre, code {{ font-family:Consolas,'Cascadia Code',monospace; }}"
            f"pre {{ background:{alternate}; padding:8px; border-radius:6px; }}"
            f"blockquote {{ color:{muted}; border-left:3px solid {border}; margin:6px 0; padding-left:9px; }}"
            f"th,td {{ border:1px solid {border}; padding:4px 7px; }}"
        )

    def set_content(self, text, markdown=True):
        self.content = str(text or "")
        if markdown and self.role == "assistant":
            self.body.setMarkdown(self.content)
        else:
            self.body.setPlainText(self.content)
        self._fit_body()
        QTimer.singleShot(0, self._fit_body)

    def _fit_body(self):
        if self._fitting or not hasattr(self, "body"):
            return
        self._fitting = True
        try:
            width = max(80, self.body.viewport().width())
            self.body.document().setTextWidth(width)
            height = int(self.body.document().documentLayout().documentSize().height()) + 12
            target = max(28, height)
            if self.body.height() != target:
                self.body.setFixedHeight(target)
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_body)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self._apply_palette()

    def _open_link(self, url):
        if url.scheme().lower() in ("http", "https"):
            QDesktopServices.openUrl(url)


class AIPage(QWidget):
    """原生 FLA AI 工作台，复用服务端的模型权限、历史对话和 SSE 流式接口。"""

    SUGGESTIONS = (
        "帮我设计一节 40 分钟的课程",
        "用学生能听懂的话解释一个概念",
        "根据课文生成 5 道随堂小测",
    )

    def __init__(self, win):
        super().__init__()
        self.setObjectName("ai")
        self.win = win
        self.models = []
        self.conversations = []
        self.conversation_id = None
        self._loaded_conversation_id = None
        self._pending_model_id = None
        self._load_generation = 0
        self._conversation_generation = 0
        self._model_generation = 0
        self._message_widgets = []
        self.current_bubble = None
        self.current_prompt = ""
        self.transcript = ""
        self.answer = ""
        self.busy = False
        self.loading_history = False
        self.stream_error = False
        self.events = _AIEvents()
        self.events.event.connect(self._on_stream_event)
        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(50)
        self.flush_timer.timeout.connect(self._flush_answer)

        root = QHBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        # 左侧会话导航：使用与 FLA 其他页面相同的 Fluent 卡片和列表控件。
        self.sidebar = CardWidget(self)
        self.sidebar.setMinimumWidth(190)
        self.sidebar.setMaximumWidth(245)
        self.sidebar.setFixedWidth(220)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 14, 14, 12)
        side.setSpacing(10)
        side_head = QHBoxLayout()
        side_head.addWidget(StrongBodyLabel("对话", self.sidebar))
        side_head.addStretch(1)
        self.refresh_history_btn = TransparentToolButton(FIF.SYNC, self.sidebar)
        self.refresh_history_btn.setToolTip("刷新历史对话")
        self.refresh_history_btn.clicked.connect(self.refresh_conversations)
        side_head.addWidget(self.refresh_history_btn)
        side.addLayout(side_head)
        self.new_btn = PrimaryPushButton(FIF.ADD, "新对话", self.sidebar)
        self.new_btn.clicked.connect(self.new_conversation)
        side.addWidget(self.new_btn)
        self.conv_list = ListWidget(self.sidebar)
        self.conv_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.conv_list.setWordWrap(True)
        self.conv_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conv_list.currentRowChanged.connect(self._pick_conversation)
        self.conv_list.customContextMenuRequested.connect(self._conversation_menu)
        side.addWidget(self.conv_list, 1)
        self.history_tip = CaptionLabel("历史对话会保存到当前账号。", self.sidebar)
        self.history_tip.setWordWrap(True)
        side.addWidget(self.history_tip)
        root.addWidget(self.sidebar)

        main = QVBoxLayout()
        main.setSpacing(10)
        root.addLayout(main, 1)
        main_head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.addWidget(TitleLabel("AI 助教"))
        title_col.addWidget(CaptionLabel("FLA 原生工作台 · 支持 Markdown 与流式回复"))
        main_head.addLayout(title_col)
        main_head.addStretch(1)
        self.status = CaptionLabel("登录后使用 AI 助教")
        self.status.setWordWrap(True)
        main_head.addWidget(self.status, 0, Qt.AlignRight | Qt.AlignVCenter)
        main.addLayout(main_head)

        controls = CardWidget(self)
        controls_row = QHBoxLayout(controls)
        controls_row.setContentsMargins(14, 9, 14, 9)
        controls_row.setSpacing(8)
        controls_row.addWidget(BodyLabel("模型", controls))
        self.model = ComboBox(controls)
        self.model.setMinimumWidth(150)
        self.model.setMaximumWidth(300)
        self.model.addItem("选择 AI 模型", userData=None)
        self.model.currentIndexChanged.connect(self._sync_controls)
        controls_row.addWidget(self.model, 1)
        self.search_check = CheckBox("联网搜索", controls)
        self.search_check.setToolTip("仅本轮问题会发送给 DDGS 聚合的第三方搜索引擎；请勿搜索个人或敏感信息。")
        controls_row.addWidget(self.search_check)
        self.model_refresh = TransparentToolButton(FIF.SYNC, controls)
        self.model_refresh.setToolTip("刷新可用模型")
        self.model_refresh.clicked.connect(self.refresh_models)
        controls_row.addWidget(self.model_refresh)
        main.addWidget(controls)
        self.search_notice = CaptionLabel(
            "默认关闭联网搜索；开启后，本轮问题会发送给 DDGS 聚合的第三方搜索引擎。请勿输入个人或敏感信息。"
        )
        self.search_notice.setWordWrap(True)
        self.search_notice.setTextColor("#666666", "#aaaaaa")
        main.addWidget(self.search_notice)

        title_row = QHBoxLayout()
        self.current_title = SubtitleLabel("新对话")
        title_row.addWidget(self.current_title)
        title_row.addStretch(1)
        self.message_hint = CaptionLabel("Enter 发送 · Shift+Enter 换行")
        self.message_hint.setTextColor("#666666", "#aaaaaa")
        title_row.addWidget(self.message_hint)
        main.addLayout(title_row)

        self.history_scroll = ScrollArea(self)
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.enableTransparentBackground()
        self.history_canvas = QWidget()
        self.message_layout = QVBoxLayout(self.history_canvas)
        self.message_layout.setContentsMargins(4, 4, 8, 8)
        self.message_layout.setSpacing(8)
        self.empty_row = QWidget(self.history_canvas)
        empty_row_layout = QHBoxLayout(self.empty_row)
        empty_row_layout.setContentsMargins(0, 0, 0, 0)
        empty_row_layout.addStretch(1)
        self.empty_card = CardWidget(self.empty_row)
        self.empty_card.setMaximumWidth(620)
        empty = QVBoxLayout(self.empty_card)
        empty.setContentsMargins(22, 20, 22, 20)
        empty.setSpacing(9)
        empty.addWidget(StrongBodyLabel("你好，我是 FLA 助教", self.empty_card))
        welcome = BodyLabel("可以协助备课、解释知识点、整理材料或生成课堂练习。选择模型后，输入问题即可开始。", self.empty_card)
        welcome.setWordWrap(True)
        empty.addWidget(welcome)
        suggestions = QVBoxLayout()
        suggestions.setSpacing(6)
        for prompt in self.SUGGESTIONS:
            button = PushButton(prompt, self.empty_card)
            button.setToolTip("填入输入框，不会自动发送")
            button.clicked.connect(lambda _=False, p=prompt: self._use_suggestion(p))
            suggestions.addWidget(button)
        empty.addLayout(suggestions)
        empty_row_layout.addWidget(self.empty_card, 1)
        empty_row_layout.addStretch(1)
        self.message_layout.addWidget(self.empty_row)
        self.message_layout.addStretch(1)
        self.history_scroll.setWidget(self.history_canvas)
        self.history_scroll.setMinimumHeight(170)
        main.addWidget(self.history_scroll, 1)

        self.composer = CardWidget(self)
        composer_lay = QVBoxLayout(self.composer)
        composer_lay.setContentsMargins(12, 11, 12, 10)
        composer_lay.setSpacing(7)
        self.input = _AIInput(self.composer)
        self.input.setObjectName("aiPromptEditor")
        self.input.setPlaceholderText("向 FLA 助教提问…（Enter 发送，Shift+Enter 换行）")
        self.input.setMinimumHeight(76)
        self.input.setMaximumHeight(132)
        self._apply_input_style()
        self.input.textChanged.connect(self._update_char_count)
        self.input.send_requested.connect(self.send)
        composer_lay.addWidget(self.input)
        composer_footer = QHBoxLayout()
        self.composer_tip = CaptionLabel("回答会以 Markdown 显示；对话会保存到当前账号。", self.composer)
        self.composer_tip.setTextColor("#666666", "#aaaaaa")
        composer_footer.addWidget(self.composer_tip)
        composer_footer.addStretch(1)
        self.char_count = CaptionLabel("0 / 8000", self.composer)
        composer_footer.addWidget(self.char_count)
        self.send_btn = PrimaryPushButton(FIF.SEND, "发送", self.composer)
        self.send_btn.setToolTip("Enter 发送；Shift+Enter 换行")
        self.send_btn.clicked.connect(self.send)
        composer_footer.addWidget(self.send_btn)
        composer_lay.addLayout(composer_footer)
        main.addWidget(self.composer)

        self._sync_controls()
        self.refresh()

    # ---- UI 状态与主题
    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            for bubble in self._message_widgets:
                bubble._apply_palette()
            self._apply_input_style()

    def _apply_input_style(self):
        """Match the prompt editor to FLA's Fluent text fields in both themes."""
        if not hasattr(self, "input"):
            return
        palette = QApplication.palette()
        base = palette.color(QPalette.Base).name()
        text = palette.color(QPalette.Text).name()
        border = palette.color(QPalette.Mid).name()
        accent = palette.color(QPalette.Highlight).name()
        selected = palette.color(QPalette.HighlightedText).name()
        muted = palette.color(QPalette.PlaceholderText).name()
        editor_palette = self.input.palette()
        editor_palette.setColor(QPalette.Base, palette.color(QPalette.Base))
        editor_palette.setColor(QPalette.Text, palette.color(QPalette.Text))
        editor_palette.setColor(QPalette.PlaceholderText, palette.color(QPalette.PlaceholderText))
        self.input.setPalette(editor_palette)
        self.input.setStyleSheet(
            "QPlainTextEdit#aiPromptEditor {"
            f"background-color:{base}; color:{text}; border:1px solid {border}; "
            "border-radius:8px; padding:10px 12px; "
            f"selection-background-color:{accent}; selection-color:{selected}; }}"
            "QPlainTextEdit#aiPromptEditor:focus {"
            f"border:1px solid {accent}; }}"
            "QPlainTextEdit#aiPromptEditor:disabled {"
            f"color:{muted}; }}"
        )

    def _sync_controls(self, *_args):
        logged = bool(cfg.token)
        ready = logged and self.model.currentData() is not None and bool(self.models)
        self.send_btn.setEnabled(ready and not self.busy and not self.loading_history
                                 and bool(self.input.toPlainText().strip()))
        self.input.setEnabled(not self.busy and not self.loading_history)
        self.model.setEnabled(logged and not self.busy and not self.loading_history)
        self.model_refresh.setEnabled(logged and not self.busy and not self.loading_history)
        self.search_check.setEnabled(not self.busy)
        self.new_btn.setEnabled(not self.busy)
        self.conv_list.setEnabled(logged and not self.busy)
        self.refresh_history_btn.setEnabled(logged and not self.busy)
        self.send_btn.setText("生成中…" if self.busy else "发送")

    def _update_char_count(self):
        length = len(self.input.toPlainText())
        self.char_count.setText(f"{length} / 8000")
        self._sync_controls()

    def _set_status(self, text):
        self.status.setText(text)

    # ---- 模型与历史
    def refresh(self):
        self.refresh_models()
        self.refresh_conversations()

    def refresh_models(self, *_args):
        self._model_generation += 1
        generation = self._model_generation
        if not cfg.token:
            self.models = []
            self._pending_model_id = None
            self.model.clear()
            self.model.addItem("登录后加载模型", userData=None)
            self._set_status("请先到「设置」登录账号")
            self._sync_controls()
            return
        preferred = self.model.currentData()
        if preferred is not None:
            self._pending_model_id = preferred
        self.model.clear()
        self.model.addItem("正在加载模型…", userData=None)
        self.model_refresh.setEnabled(False)
        self._set_status("正在加载可用模型…")

        def done(result):
            if generation != self._model_generation:
                return
            status, data = result if isinstance(result, tuple) else (0, {})
            if status != 200 or not isinstance(data, dict):
                self.models = []
                self.model.clear()
                self.model.addItem("模型加载失败", userData=None)
                detail = (data or {}).get("detail", "无法连接服务器") if isinstance(data, dict) else "无法连接服务器"
                self._set_status(str(detail))
                self._sync_controls()
                return
            self.models = data.get("items") or []
            self.model.clear()
            if not data.get("enabled"):
                self.model.addItem("AI 功能已关闭", userData=None)
                self._set_status("AI 功能已由管理员关闭")
                self._sync_controls()
                return
            if not self.models:
                self.model.addItem("暂无可用模型", userData=None)
                self._set_status("管理员尚未为你的用户组开放模型")
                self._sync_controls()
                return
            available = set()
            for item in self.models:
                model_id = item.get("id")
                available.add(model_id)
                label = item.get("name") or item.get("model") or "AI 模型"
                if item.get("channel"):
                    label += " · " + str(item["channel"])
                self.model.addItem(label, userData=model_id)
            desired = self._pending_model_id
            if desired not in available:
                desired = self._models_for_current_conversation()
            index = self.model.findData(desired) if desired is not None else -1
            self.model.setCurrentIndex(index if index >= 0 else 0)
            self._pending_model_id = None
            self._set_status("模型已就绪 · 对话支持流式输出")
            self._sync_controls()
        run_async(lambda: core.api("GET", "/api/ai/models"), done)

    def _models_for_current_conversation(self):
        return getattr(self, "_conversation_model_id", None)

    def _select_model(self, model_id):
        if model_id is None:
            return
        idx = self.model.findData(model_id)
        if idx >= 0:
            self.model.setCurrentIndex(idx)
        else:
            self._pending_model_id = model_id

    def refresh_conversations(self, *_args, select_id=None):
        self._conversation_generation += 1
        generation = self._conversation_generation
        if not cfg.token:
            self._load_generation += 1
            self.loading_history = False
            self.busy = False
            self.flush_timer.stop()
            self.current_bubble = None
            self.conversation_id = None
            self._loaded_conversation_id = None
            self._conversation_model_id = None
            self.current_prompt = ""
            self.answer = ""
            self.transcript = ""
            self.input.clear()
            self._clear_messages()
            self.current_title.setText("新对话")
            self.conversations = []
            self.conv_list.clear()
            self.history_tip.setText("登录后可查看已保存的对话。")
            self._sync_controls()
            return
        selected = self.conversation_id if select_id is None else select_id
        self.history_tip.setText("正在加载历史对话…")

        def done(result):
            if generation != self._conversation_generation:
                return
            status, data = result if isinstance(result, tuple) else (0, {})
            if status != 200 or not isinstance(data, dict):
                self.history_tip.setText("历史对话暂不可用")
                return
            self.conversations = data.get("items") or []
            self.conv_list.blockSignals(True)
            self.conv_list.clear()
            selected_row = -1
            for i, item in enumerate(self.conversations):
                title = item.get("title") or "新对话"
                updated = str(item.get("updated_at") or "").replace("T", " ")[:16]
                row = QListWidgetItem(title + ("\n" + updated if updated else ""))
                row.setData(Qt.UserRole, int(item["id"]))
                row.setToolTip(title)
                row.setSizeHint(QSize(188, 54))
                self.conv_list.addItem(row)
                if selected is not None and int(item["id"]) == int(selected):
                    selected_row = i
            self.conv_list.setCurrentRow(selected_row)
            self.conv_list.blockSignals(False)
            self.history_tip.setText("最近 200 条 · 自动保存") if self.conversations else self.history_tip.setText("还没有历史对话")
        run_async(lambda: core.api("GET", "/api/ai/conversations"), done)

    def _pick_conversation(self, row):
        item = self.conv_list.item(row) if row >= 0 else None
        if item is None:
            return
        conversation_id = int(item.data(Qt.UserRole))
        if conversation_id == self._loaded_conversation_id:
            return
        if self.busy:
            self._select_conversation_in_list(self.conversation_id)
            return
        self._load_conversation(conversation_id)

    def _select_conversation_in_list(self, conversation_id):
        self.conv_list.blockSignals(True)
        row = -1
        for i in range(self.conv_list.count()):
            if self.conv_list.item(i).data(Qt.UserRole) == conversation_id:
                row = i
                break
        self.conv_list.setCurrentRow(row)
        self.conv_list.blockSignals(False)

    def _load_conversation(self, conversation_id):
        self._load_generation += 1
        generation = self._load_generation
        self.conversation_id = int(conversation_id)
        self._loaded_conversation_id = None
        self.loading_history = True
        self._clear_messages()
        self.current_title.setText("正在加载对话…")
        self._set_status("正在恢复对话历史…")
        self._sync_controls()

        def done(result):
            if generation != self._load_generation:
                return
            status, data = result if isinstance(result, tuple) else (0, {})
            if status != 200 or not isinstance(data, dict):
                self.loading_history = False
                detail = (data or {}).get("detail", "无法加载对话") if isinstance(data, dict) else "无法连接服务器"
                self.current_title.setText("对话加载失败")
                self._set_status(str(detail))
                self._sync_controls()
                return
            self.loading_history = False
            self.conversation_id = int(data["id"])
            self._loaded_conversation_id = self.conversation_id
            self._conversation_model_id = data.get("model_id")
            self.current_title.setText(data.get("title") or "新对话")
            self._select_model(data.get("model_id"))
            messages = data.get("messages") or []
            for message in messages:
                role = "assistant" if message.get("role") == "assistant" else "user"
                self._append_message(role, message.get("content") or "", scroll=False)
            self.empty_row.setVisible(not messages)
            self._set_status("对话已恢复 · 继续提问即可")
            self._scroll_to_bottom()
            self._sync_controls()
        run_async(lambda: core.api("GET", f"/api/ai/conversations/{conversation_id}"), done)

    def _conversation_menu(self, pos):
        item = self.conv_list.itemAt(pos)
        if item is None or self.busy:
            return
        conversation_id = int(item.data(Qt.UserRole))
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FIF.DELETE, "删除对话…", triggered=lambda: self._delete_conversation(conversation_id)))
        menu.exec(self.conv_list.mapToGlobal(pos))

    def _delete_conversation(self, conversation_id):
        if MessageBox("删除对话", "此操作会永久删除该对话及其消息，无法撤销。确定继续吗？", self.win).exec():
            run_async(lambda: core.api("DELETE", f"/api/ai/conversations/{conversation_id}"),
                      lambda result: self._conversation_deleted(result, conversation_id))

    def _conversation_deleted(self, result, conversation_id):
        status, data = result if isinstance(result, tuple) else (0, {})
        if status != 200:
            detail = (data or {}).get("detail", "删除失败") if isinstance(data, dict) else "无法连接服务器"
            self.win.toast(str(detail), ok=False)
            return
        if self.conversation_id == conversation_id:
            self.new_conversation()
        self.refresh_conversations()
        self.win.toast("对话已删除")

    def new_conversation(self, *_args):
        if self.busy:
            return
        self._load_generation += 1
        self.conversation_id = None
        self._loaded_conversation_id = None
        self._conversation_model_id = None
        self.loading_history = False
        self._pending_model_id = None
        self.current_bubble = None
        self.current_prompt = ""
        self.answer = ""
        self.transcript = ""
        self._clear_messages()
        self.empty_row.show()
        self.current_title.setText("新对话")
        self._set_status("选择模型后即可开始新对话")
        self._select_conversation_in_list(None)
        self.input.setFocus()
        self._sync_controls()

    def _clear_messages(self):
        for bubble in self._message_widgets:
            self.message_layout.removeWidget(bubble)
            bubble.deleteLater()
        self._message_widgets.clear()
        self.empty_row.show()
        self._scroll_to_bottom()

    # ---- 消息显示与流式回复
    def _append_message(self, role, text, scroll=True):
        bubble = _AIMessageBubble(role, text, self.history_canvas)
        self.message_layout.insertWidget(self.message_layout.count() - 1, bubble)
        self._message_widgets.append(bubble)
        self.empty_row.hide()
        if scroll:
            self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self):
        QTimer.singleShot(0, lambda: self.history_scroll.verticalScrollBar().setValue(
            self.history_scroll.verticalScrollBar().maximum()))

    def _use_suggestion(self, text):
        self.input.setPlainText(text)
        self.input.setFocus()

    def send(self, *_args):
        if self.busy or self.loading_history:
            return
        if not cfg.token:
            self.win.toast("请先登录后使用 AI 助教", ok=False)
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        if len(text) > 8000:
            self.win.toast("单条消息最多 8000 字", ok=False)
            return
        model_id = self.model.currentData()
        if model_id is None:
            self.win.toast("请选择可用的 AI 模型", ok=False)
            return
        self.input.clear()
        self.busy = True
        self.current_prompt = text
        self._sync_controls()
        self._set_status("正在准备对话…")
        if self.conversation_id:
            self._start_stream(self.conversation_id, text, int(model_id))
            return
        run_async(lambda: core.api("POST", "/api/ai/conversations", {"model_id": int(model_id)}),
                  lambda result: self._conversation_created(result, text, int(model_id)))

    def _conversation_created(self, result, text, model_id):
        if not cfg.token:
            self.busy = False
            self._set_status("请重新登录后继续使用 AI 助教")
            self._sync_controls()
            return
        status, data = result if isinstance(result, tuple) else (0, {})
        if status != 200 or not isinstance(data, dict) or not data.get("id"):
            self.busy = False
            self._set_status("无法创建对话：" + str((data or {}).get("detail", "请检查登录状态或权限")))
            self.input.setPlainText(text)
            self._sync_controls()
            return
        self.conversation_id = int(data["id"])
        self._loaded_conversation_id = None
        self._conversation_model_id = model_id
        self.current_title.setText(data.get("title") or "新对话")
        self.refresh_conversations(select_id=self.conversation_id)
        self._start_stream(self.conversation_id, text, model_id)

    def _start_stream(self, conversation_id, text, model_id):
        self.transcript += "你：" + text + "\nAI："
        self.answer = ""
        self.stream_error = False
        self._append_message("user", text)
        self.current_bubble = self._append_message("assistant", "正在思考…")
        self._set_status("AI 正在回答…")
        body = {"content": text, "model_id": model_id, "online_search": self.search_check.isChecked()}

        def work():
            return core.ai_stream(f"/api/ai/conversations/{conversation_id}/messages", body,
                                  on_event=lambda event: self.events.event.emit(event), timeout=180)
        run_async(work, self._stream_finished)

    def _on_stream_event(self, event):
        if not cfg.token or not isinstance(event, dict):
            return
        if event.get("searching"):
            self._set_status("正在联网搜索…")
        if event.get("delta"):
            self._set_status("AI 正在回答…")
            self.answer += str(event["delta"])
            if not self.flush_timer.isActive():
                self.flush_timer.start()
        if event.get("error"):
            self.stream_error = True
            self.answer += ("\n" if self.answer else "") + "⚠ " + str(event["error"])
            self._flush_answer()

    def _flush_answer(self):
        self.flush_timer.stop()
        if self.current_bubble is None:
            return
        was_bottom = self.history_scroll.verticalScrollBar().value() >= \
                     self.history_scroll.verticalScrollBar().maximum() - 28
        self.current_bubble.set_content(self.answer or "正在思考…", markdown=bool(self.answer))
        if was_bottom:
            self._scroll_to_bottom()

    def _stream_finished(self, result):
        if not cfg.token:
            self.flush_timer.stop()
            self.answer = ""
            self.current_bubble = None
            self.busy = False
            self._set_status("请重新登录后继续使用 AI 助教")
            self._sync_controls()
            return
        status, data = result if isinstance(result, tuple) else (0, {})
        if status != 200:
            detail = (data or {}).get("detail", "请求失败") if isinstance(data, dict) else "请求失败"
            self.answer += ("\n" if self.answer else "") + "⚠ " + str(detail)
            self.stream_error = True
        if not self.answer:
            self.answer = "（AI 没有返回内容，请重试。）"
        if self.current_bubble is not None:
            self.current_bubble.set_content(self.answer, markdown=True)
        self.transcript += self.answer + "\n\n"
        if self.conversation_id is not None:
            self._loaded_conversation_id = self.conversation_id
        if self.current_title.text() == "新对话" and self.current_prompt:
            self.current_title.setText(self.current_prompt[:20])
        self.answer = ""
        self.current_bubble = None
        self.busy = False
        self._set_status("回答完成" if status == 200 and not self.stream_error else "AI 请求失败")
        self._sync_controls()
        self.refresh_conversations(select_id=self.conversation_id)

# ================================================================== 实验扩展
class ExperimentResource(CardWidget):
    def __init__(self, item, parent_page):
        super().__init__(parent_page)
        self.item = item
        self.page = parent_page
        self.extension_id = item["id"]
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 15, 18, 15)
        details = QVBoxLayout()
        details.setSpacing(5)
        title = StrongBodyLabel(f"{item['subject']} · {item['name']}")
        description = BodyLabel(item["description"])
        description.setWordWrap(True)
        self.status = CaptionLabel("")
        self.status.setWordWrap(True)
        self.status.setTextColor("#666666", "#aaaaaa")
        license_label = CaptionLabel(item["license"])
        license_label.setTextColor("#666666", "#aaaaaa")
        details.addWidget(title)
        details.addWidget(description)
        details.addWidget(license_label)
        details.addWidget(self.status)
        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        details.addWidget(self.progress)
        row.addLayout(details, 1)

        actions = QVBoxLayout()
        self.action = PrimaryPushButton("下载并安装")
        self.action.clicked.connect(self.activate)
        self.remove_button = PushButton("卸载扩展")
        self.remove_button.clicked.connect(self.remove)
        actions.addWidget(self.action)
        actions.addWidget(self.remove_button)
        row.addLayout(actions)
        self._busy = False
        self._sync_state()

    def _sync_state(self):
        available = bool(self.item["available"])
        installed = experiment_extensions.is_installed(self.extension_id)
        self.remove_button.setVisible(installed and available)
        if not available:
            if self.extension_id == "avogadro" and experiment_extensions.platform_key().startswith("windows"):
                message = "Windows 上游仅提供需管理员授权的系统安装器，暂不作为 FLA 扩展安装。"
            else:
                message = f"当前系统 {experiment_extensions.platform_label()} 暂无适配安装包。"
            self.status.setText(message)
            self.action.setText("当前系统暂不可用")
            self.action.setEnabled(False)
        elif installed:
            self.status.setText(f"已安装到 FLA 扩展目录 · {self.item['version']} · 可离线使用")
            self.action.setText("打开")
            self.action.setEnabled(not self._busy)
        else:
            self.status.setText(f"未安装 · {experiment_extensions.platform_label()} · {self.item['version']}")
            self.action.setText("下载并安装")
            self.action.setEnabled(not self._busy)

    def _update_progress(self, received, total):
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(received * 100 / total)))
            self.status.setText(f"正在下载并校验… {fmt_size(received)} / {fmt_size(total)}")
        else:
            self.progress.setRange(0, 0)
            self.status.setText(f"正在下载并校验… 已收取 {fmt_size(received)}")

    def activate(self):
        if self._busy:
            return
        if experiment_extensions.is_installed(self.extension_id):
            self._open()
            return
        self._busy = True
        self.action.setEnabled(False)
        self.action.setText("正在准备扩展…")
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText("连接官方分发源…")
        signal = _ExtensionProgress()
        signal.changed.connect(self._update_progress)
        _keep.add(signal)

        def work():
            return experiment_extensions.install(
                self.extension_id,
                progress=lambda received, total: signal.changed.emit(received, total),
            )

        def done(result):
            _keep.discard(signal)
            self.progress.hide()
            self._busy = False
            if isinstance(result, tuple) and result and result[0] == 0:
                detail = result[1].get("detail", "下载或安装失败") if isinstance(result[1], dict) else str(result[1])
                self.status.setText(detail)
                self.page.win.toast("扩展安装失败：" + detail, ok=False)
                self._sync_state()
                return
            self._sync_state()
            self._open()

        run_async(work, done)

    def _open(self):
        try:
            entry = experiment_extensions.open_installed(self.extension_id)
            if self.item["kind"] == "html":
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry))):
                    raise RuntimeError("无法调用系统默认浏览器打开离线模拟")
            self.page.win.toast("扩展已在本机打开")
        except Exception as exc:
            self.page.win.toast("无法打开实验扩展：" + str(exc), ok=False)

    def remove(self):
        if MessageBox("卸载实验扩展", "将删除 FLA 扩展中心管理目录中的本地文件，不会调用系统级卸载器；若扩展仍在运行，请先关闭。继续吗？", self).exec():
            self._busy = True
            self.action.setEnabled(False)

            def done(result):
                self._busy = False
                if isinstance(result, tuple) and result and result[0] == 0:
                    detail = result[1].get("detail", "卸载失败") if isinstance(result[1], dict) else str(result[1])
                    self.page.win.toast("卸载失败：" + detail, ok=False)
                else:
                    self.page.win.toast("实验扩展已从 FLA 本地目录卸载")
                self._sync_state()
            run_async(lambda: experiment_extensions.uninstall(self.extension_id), done)


class ClassroomToolCard(CardWidget):
    """课堂工具卡片: 点一下就打开对应的工具页"""

    def __init__(self, icon, name, desc, tab, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        iw = IconWidget(icon)
        iw.setFixedSize(30, 30)
        h.addWidget(iw)
        v = QVBoxLayout()
        v.setSpacing(3)
        v.addWidget(StrongBodyLabel(name))
        d = CaptionLabel(desc)
        d.setWordWrap(True)
        d.setTextColor("#666666", "#aaaaaa")
        v.addWidget(d)
        h.addLayout(v, 1)
        b = PrimaryPushButton("打开")
        b.setFixedWidth(90)
        b.clicked.connect(lambda: open_classroom(tab))
        h.addWidget(b)


class ExperimentsPage(Page):
    """课堂工具 (抽人/计时/分组/计分/表决/函数图像) + 可离线使用的开源实验扩展"""

    def __init__(self, win):
        super().__init__("experiments", "课堂工具", "上课真正用得上的六件套, 断网也能用; 下方是可选的开源实验扩展。")
        self.win = win

        tools = CardWidget()
        tl = QVBoxLayout(tools)
        tl.setContentsMargins(18, 15, 18, 15)
        tl.setSpacing(8)
        tl.addWidget(StrongBodyLabel("课堂六件套 · 电脑/平板/手机都能开"))
        grid = QHBoxLayout()
        grid.setSpacing(10)
        left = QVBoxLayout()
        right = QVBoxLayout()
        items = [
            (FIF.PEOPLE, "随机抽人", "滚动抽取, 1/2/3/5 人, 不重复, 抽过的人不再点", "pick"),
            (FIF.SYNC, "随机分组", "按组数或每组人数一键分好, 可直接复制到群里", "group"),
            (FIF.STOP_WATCH, "计时器", "iOS 式滚轮拖时/分/秒, 支持 ± 与全屏投影", "timer"),
            (FIF.CERTIFICATE, "计分板", "分组加减分, 大屏显示, 自动排序", "score"),
            (FIF.GLOBE, "表决器", "A/B/C/D/弃权, 键盘 1-5 计票, 实时统计", "vote"),
            (FIF.EDIT, "函数图像", "输入表达式即画图, 拖动平移/滚轮缩放", "graph"),
        ]
        for n, (icon, name, desc, tab) in enumerate(items):
            (left if n % 2 == 0 else right).addWidget(ClassroomToolCard(icon, name, desc, tab, tools))
        grid.addLayout(left, 1)
        grid.addLayout(right, 1)
        tl.addLayout(grid)
        row = QHBoxLayout()
        b = PrimaryPushButton(FIF.LINK, "在新窗口打开完整课堂工具页")
        b.clicked.connect(lambda: open_classroom("pick"))
        row.addWidget(b)
        row.addStretch(1)
        tl.addLayout(row)
        self.lay.addWidget(tools)

        note = CardWidget()
        note_lay = QVBoxLayout(note)
        note_lay.setContentsMargins(18, 15, 18, 15)
        note_lay.addWidget(StrongBodyLabel("开源实验扩展 · " + experiment_extensions.platform_label()))
        note_text = BodyLabel(
            "点击“下载并安装”后，FLA 会在后台按当前系统/处理器选择适配包，校验来源并保存到当前用户的 FLA/Extensions 目录，随后直接打开；"
            "PhET 单文件模拟安装后可离线运行。不会把你带到上游下载页面，也不会静默提权；若系统要求授权，会由操作系统提示并需你确认。原项目署名与许可说明保留；只上架已核验的离线包，尚未核验的在线资源不会冒充一键安装项；卸载只移除 FLA 扩展目录中的内容。"
        )
        note_text.setWordWrap(True)
        note_lay.addWidget(note_text)
        self.lay.addWidget(note)

        for item in experiment_extensions.catalog():
            self.lay.addWidget(ExperimentResource(item, self))
        self.lay.addStretch(1)


class _SysSig(QObject):
    line = pyqtSignal(str)
    finished = pyqtSignal(bool, str)


class SystemPage(Page):
    """系统维护: 希沃全面消杀 + 重启还原 (影子系统)"""

    def __init__(self, win):
        super().__init__("system", "系统维护", "清理希沃残留; 需要时开启重启还原, 防止学生乱装乱改。")
        self.win = win
        self.sig = _SysSig()
        self.sig.line.connect(self._log)
        self.sig.finished.connect(self._done)

        # ---------- 希沃消杀 ----------
        c1 = CardWidget()
        v1 = QVBoxLayout(c1)
        v1.setContentsMargins(20, 16, 20, 16)
        v1.setSpacing(8)
        v1.addWidget(StrongBodyLabel("希沃全面消杀"))
        t = BodyLabel("清理希沃白板5 / 希沃管家 / PPT 小工具 的进程、服务、驱动、注册表、计划任务、"
                      "开机自启、安装目录、PPT 加载项与 COM 组件。删除前自动备份到隔离区, 可还原。")
        t.setWordWrap(True)
        v1.addWidget(t)
        self.purge_force = CheckBox("强制跳过希沃消杀 (仅给影子系统用)")
        self.purge_force.hide()
        self.admin_tip = CaptionLabel("")
        self.admin_tip.setWordWrap(True)
        self.admin_tip.setTextColor("#c62828", "#ef9a9a")
        v1.addWidget(self.admin_tip)
        row1 = QHBoxLayout()
        self.b_scan = PushButton(FIF.SEARCH, "体检 (只看不改)")
        self.b_scan.clicked.connect(lambda: self._run_purge(scan_only=True))
        self.b_purge = PrimaryPushButton(FIF.BROOM, "开始消杀")
        self.b_purge.clicked.connect(self._confirm_purge)
        self.b_restore = PushButton(FIF.SYNC, "还原最近一次备份")
        self.b_restore.clicked.connect(self._restore)
        self.b_admin = PushButton(FIF.FINGERPRINT, "以管理员身份重启 FLA")
        self.b_admin.clicked.connect(self._elevate)
        self.b_admin.hide()
        row1.addWidget(self.b_scan)
        row1.addWidget(self.b_purge)
        row1.addWidget(self.b_restore)
        row1.addWidget(self.b_admin)
        row1.addStretch(1)
        v1.addLayout(row1)
        self.purge_out = PlainTextEdit()
        self.purge_out.setReadOnly(True)
        self.purge_out.setMinimumHeight(170)
        self.purge_out.setPlaceholderText("点「体检」先看看这台电脑有多少希沃残留…")
        v1.addWidget(self.purge_out)
        self.lay.addWidget(c1)

        # ---------- 影子系统 ----------
        c2 = CardWidget()
        v2 = QVBoxLayout(c2)
        v2.setContentsMargins(20, 16, 20, 16)
        v2.setSpacing(8)
        v2.addWidget(StrongBodyLabel("重启还原 (影子系统)"))
        self.shadow_state = BodyLabel("检测中…")
        self.shadow_state.setWordWrap(True)
        v2.addWidget(self.shadow_state)
        self.shadow_detail = CaptionLabel("")
        self.shadow_detail.setWordWrap(True)
        self.shadow_detail.setTextColor("#666666", "#aaaaaa")
        v2.addWidget(self.shadow_detail)
        op = QHBoxLayout()
        op.addWidget(CaptionLabel("overlay 容量"))
        self.overlay = ComboBox()
        for mb in (1024, 2048, 4096, 8192, 16384):
            self.overlay.addItem(f"{mb} MB")
        self.overlay.setCurrentIndex(2)
        op.addWidget(self.overlay)
        op.addSpacing(12)
        self.clean_before = CheckBox("开启前先消杀希沃")
        self.clean_before.setChecked(True)
        op.addWidget(self.clean_before)
        self.skip_clean = CheckBox("强制跳过消杀")
        self.skip_clean.setChecked(False)
        op.addWidget(self.skip_clean)
        op.addStretch(1)
        v2.addLayout(op)
        row2 = QHBoxLayout()
        self.b_shadow_check = PushButton(FIF.SYNC, "重新检测")
        self.b_shadow_check.clicked.connect(self.refresh_shadow)
        self.b_shadow_on = PrimaryPushButton(FIF.POWER_BUTTON, "开启重启还原")
        self.b_shadow_on.clicked.connect(self._confirm_shadow)
        self.b_shadow_off = PushButton(FIF.CANCEL, "关闭重启还原")
        self.b_shadow_off.clicked.connect(self._shadow_off)
        row2.addWidget(self.b_shadow_check)
        row2.addWidget(self.b_shadow_on)
        row2.addWidget(self.b_shadow_off)
        row2.addStretch(1)
        v2.addLayout(row2)
        self.shadow_out = PlainTextEdit()
        self.shadow_out.setReadOnly(True)
        self.shadow_out.setMinimumHeight(130)
        v2.addWidget(self.shadow_out)
        self.lay.addWidget(c2)
        self.lay.addStretch(1)

        self._busy = False
        QTimer.singleShot(100, self._check_admin)
        QTimer.singleShot(200, self.refresh_shadow)

    def _check_admin(self):
        try:
            from .purge import is_admin
            ok = is_admin()
        except Exception:
            ok = False
        if ok:
            self.admin_tip.setText("")
        else:
            self.admin_tip.setText("当前不是管理员: 只能体检, 不能删除服务/驱动/Program Files, 也不能开启影子系统。"
                                   "请点右侧按钮以管理员身份重启 FLA。")
            self.b_admin.show()

    def _elevate(self):
        """以管理员身份重新拉起 FLA (UAC 提权)"""
        try:
            import ctypes
            exe = os.environ.get("FLA_EXE") or ""
            script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fla_desktop.py")
            if exe:
                params, target = "", exe
            else:
                params, target = '"%s"' % script, sys.executable
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, params, None, 1)
            if rc > 32:
                QApplication.quit()
        except Exception as e:
            InfoBar.error("提权失败", str(e), parent=self)

    # ------------------------------------------------ 公共
    def _log(self, text):
        self.purge_out.appendPlainText(text)
        self.shadow_out.appendPlainText(text)

    def _done(self, ok, msg):
        self._busy = False
        for b in (self.b_scan, self.b_purge, self.b_shadow_on, self.b_shadow_off, self.b_restore):
            b.setEnabled(True)
        if not msg:
            return
        if ok:
            InfoBar.success("完成", msg, duration=4000, parent=self, position=InfoBarPosition.TOP_RIGHT)
        else:
            InfoBar.warning("未完成", msg, duration=6000, parent=self, position=InfoBarPosition.TOP_RIGHT)

    # ------------------------------------------------ 希沃消杀
    def _confirm_purge(self):
        if self._busy:
            return
        box = MessageBox("确认消杀希沃残留?",
                         "会结束希沃相关进程、删除服务/驱动/计划任务/注册表/安装目录。\n"
                         "删除前会自动备份到隔离区, 可在本页还原。\n"
                         "建议先点「体检」看清楚要删什么。", self)
        box.yesButton.setText("开始消杀")
        box.cancelButton.setText("取消")
        if box.exec():
            self._run_purge(scan_only=False)

    def _run_purge(self, scan_only: bool):
        if self._busy:
            return
        self._busy = True
        for b in (self.b_scan, self.b_purge):
            b.setEnabled(False)
        self.purge_out.clear()
        self.purge_out.appendPlainText("正在体检…" if scan_only else "正在消杀 (先备份)…")

        def work():
            try:
                from .purge import SeewoPurge
                from .core import data_dir
                p = SeewoPurge(data_dir=data_dir(), log=lambda t: self.sig.line.emit(t))
                r = p.scan()
                if not r.steps:
                    self.sig.line.emit("没有发现希沃残留 🎉")
            except Exception as e:
                self.sig.finished.emit(False, f"体检失败: {e}")
                return
            if scan_only:
                for st in r.steps:
                    self.sig.line.emit(f"[{st.kind}] {st.label}" + (f" — {st.detail}" if st.detail else ""))
                for n in r.notes:
                    self.sig.line.emit("· " + n)
                self.sig.finished.emit(True, f"体检完成: {r.summary()}")
                return
            try:
                r = p.purge(backup=True, progress=lambda i, t, label: self.sig.line.emit(f"({i}/{t}) {label}"))
            except Exception as e:
                self.sig.finished.emit(False, f"消杀失败: {e}")
                return
            bad = [s for s in r.steps if not s.ok]
            for n in r.notes:
                self.sig.line.emit("· " + n)
            self.sig.finished.emit(not bad, f"消杀完成: 成功 {len(r.steps) - len(bad)}/{len(r.steps)} 项")

        threading.Thread(target=work, daemon=True).start()

    def _restore(self):
        from .purge import restore
        from .core import data_dir
        base = os.path.join(data_dir(), "purge")
        if not os.path.isdir(base):
            InfoBar.info("没有备份", "本机还没有消杀备份", parent=self)
            return
        dirs = sorted(d for d in os.listdir(base) if d.startswith("quarantine-"))
        if not dirs:
            InfoBar.info("没有备份", "本机还没有消杀备份", parent=self)
            return
        target = os.path.join(base, dirs[-1])

        def work():
            try:
                self.sig.line.emit("正在从备份还原: " + target)
                ok, bad = restore(target, log=lambda t: self.sig.line.emit(t))
                self.sig.finished.emit(bad == 0, f"还原完成: 成功 {ok}, 失败 {bad}")
            except Exception as e:
                self.sig.finished.emit(False, f"还原失败: {e}")

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------ 影子系统
    def refresh_shadow(self):
        from .shadow import ShadowSystem
        try:
            st = ShadowSystem().detect()
        except Exception as e:
            self.shadow_state.setText(f"检测失败: {e}")
            return
        self.shadow_state.setText(st.summary())
        detail = []
        if st.edition or st.product:
            detail.append(f"{st.product} ({st.edition}, build {st.build})")
        detail.extend(st.reasons)
        detail.extend(st.notes)
        self.shadow_detail.setText("\n".join(detail))
        on = st.method in ("uwf", "vhd")
        self.b_shadow_on.setEnabled(on and not self._busy)
        self.b_shadow_off.setEnabled(bool(st.uwf_enabled) and not self._busy)
        names = {"uwf": "UWF 统一写入筛选器", "vhd": "VHD 差分盘"}
        self.shadow_out.clear()
        self.shadow_out.appendPlainText("方案: " + names.get(st.method, "不可用"))

    def _confirm_shadow(self):
        if self._busy:
            return
        msg = ("开启后, 从下次重启开始: 学生装软件、改设置、删文件, 再重启一次全部还原。\n"
               "关闭方法: 在非还原状态下点「关闭重启还原」, 或再次运行 FLA。\n")
        if self.clean_before.isChecked() and not self.skip_clean.isChecked():
            msg += "将先自动消杀希沃残留 (推荐)。"
        box = MessageBox("开启重启还原?", msg, self)
        box.yesButton.setText("开启并重启")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self._busy = True
        self.b_shadow_on.setEnabled(False)
        self.shadow_out.appendPlainText("正在开启…")

        def work():
            try:
                from .shadow import ShadowSystem
                s = ShadowSystem(log=lambda t: self.sig.line.emit(t))
                ok, m, acts = s.enable(
                    clean_seewo=self.clean_before.isChecked(),
                    skip_clean=self.skip_clean.isChecked(),
                    overlay_mb=[1024, 2048, 4096, 8192, 16384][max(0, self.overlay.currentIndex())],
                    reboot=True)
                for a in acts:
                    self.sig.line.emit(("✓ " if a.ok else "✗ ") + a.name)
                self.sig.finished.emit(ok, m)
            except Exception as e:
                self.sig.finished.emit(False, f"开启失败: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _shadow_off(self):
        if self._busy:
            return
        box = MessageBox("关闭重启还原?", "会先把当前的改动提交进系统盘, 再关闭写保护。", self)
        box.yesButton.setText("关闭")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self._busy = True
        self.b_shadow_off.setEnabled(False)

        def work():
            try:
                from .shadow import ShadowSystem
                ok, m, acts = ShadowSystem(log=lambda t: self.sig.line.emit(t)).disable(reboot=True)
                for a in acts:
                    self.sig.line.emit(("✓ " if a.ok else "✗ ") + a.name)
                self.sig.finished.emit(ok, m)
            except Exception as e:
                self.sig.finished.emit(False, f"关闭失败: {e}")

        threading.Thread(target=work, daemon=True).start()


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

        v = card("存储位置")
        self.cache_path_edit = LineEdit()
        self.cache_path_edit.setReadOnly(True)
        self.download_path_edit = LineEdit()
        self.download_path_edit.setReadOnly(True)
        for title, kind, edit in (("缓存目录", "cache", self.cache_path_edit),
                                  ("课件下载目录", "download", self.download_path_edit)):
            r = QHBoxLayout()
            r.addWidget(BodyLabel(title), 0)
            r.addWidget(edit, 1)
            browse = PushButton("更改…")
            browse.clicked.connect(lambda _=False, k=kind: self.choose_storage(k))
            open_btn = PushButton(FIF.FOLDER, "打开")
            open_btn.clicked.connect(lambda _=False, k=kind: self.open_storage(k))
            r.addWidget(browse)
            r.addWidget(open_btn)
            v.addLayout(r)
        storage_tip = CaptionLabel("默认优先选择可写的非系统盘；没有其他磁盘时使用用户文档目录。你可以随时改到任意可写位置；更改后不会自动搬迁旧文件。")
        storage_tip.setWordWrap(True)
        storage_tip.setTextColor("#666666", "#aaaaaa")
        v.addWidget(storage_tip)

        v = card("启动")
        r = QHBoxLayout()
        r.addWidget(BodyLabel("Windows 当前用户登录后启动 FLA（不需要管理员权限）"), 1)
        self.startup_switch = SwitchButton()
        self.startup_switch.setOnText("开")
        self.startup_switch.setOffText("关")
        self.startup_switch.setChecked(cfg.startup)
        self.startup_switch.checkedChanged.connect(self.set_startup)
        r.addWidget(self.startup_switch)
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

        v = card("关于")
        v.addWidget(BodyLabel(f"{core.APP_NAME}  v{core.VERSION}"))
        r = QHBoxLayout()
        ub = PushButton(FIF.UPDATE, "检查更新")
        ub.clicked.connect(lambda: win.check_update(manual=True))
        cb = PushButton(FIF.FOLDER, "打开缓存目录")
        cb.clicked.connect(lambda: self.open_storage("cache"))
        r.addWidget(ub)
        r.addWidget(cb)
        r.addStretch(1)
        v.addLayout(r)
        self.lay.addStretch(1)
        self.sync()

    def sync(self):
        logged = bool(cfg.token)
        self.acc_state.setText(f"已登录：{cfg.user}" if logged else "未登录")
        self.cache_path_edit.setText(cfg.cache_dir or "尚未设置（请选择可写目录）")
        self.download_path_edit.setText(cfg.download_dir or "尚未设置（请选择可写目录）")
        self.startup_switch.setChecked(cfg.startup)
        for w in (self.user, self.pwd, self.login_btn):
            w.setVisible(not logged)
        self.logout_btn.setVisible(logged)

    def choose_storage(self, kind):
        old = cfg.cache_dir if kind == "cache" else cfg.download_dir
        label = "缓存目录" if kind == "cache" else "课件下载目录"
        path = QFileDialog.getExistingDirectory(self, f"选择{label}（建议非系统盘）", old or os.path.expanduser("~"))
        if not path:
            return
        try:
            core.set_storage_dir(kind, path)
            self.sync()
            self.win.toast(f"{label}已更新。旧文件仍保留在原目录。")
        except Exception as e:
            self.win.toast(str(e), ok=False)

    def open_storage(self, kind):
        try:
            path = core.storage_location(kind)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            self.win.toast(str(e), ok=False)

    def set_startup(self, enabled):
        if not core.IS_WIN:
            self.startup_switch.setChecked(False)
            self.win.toast("登录启动仅支持 Windows", ok=False)
            return
        if not core.set_autostart(enabled):
            self.startup_switch.setChecked(cfg.startup)
            self.win.toast("无法更新当前用户启动项", ok=False)

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
        self._preferred_main_size = QSize(940, 680)
        self._base_main_minimum = QSize(680, 460)
        self._main_screen_bounds = {}
        self._main_screen_key = ""
        self._watched_main_screen_keys = set()
        self._applying_main_screen_layout = False
        self._window_screen_signal_connected = False
        self._main_screen_reflow_timer = QTimer(self)
        self._main_screen_reflow_timer.setSingleShot(True)
        self._main_screen_reflow_timer.setInterval(150)
        self._main_screen_reflow_timer.timeout.connect(self._refresh_main_screen_layout)
        self.setWindowTitle(core.APP_NAME)
        self.resize(940, 680)
        self.home = HomePage(self)
        self.library = LibraryPage(self)
        self.ai = AIPage(self)
        self.experiments = ExperimentsPage(self)
        # 系统维护页最容易受环境差异影响 (管理员检测/图标/COM); 单独兜底,
        # 绝不让它把整个主窗口带崩 —— 一个页面出问题不该导致双击打不开
        self.system = None
        try:
            self.system = SystemPage(self)
        except Exception as e:                       # noqa: BLE001
            import traceback
            traceback.print_exc()
            try:
                with open(os.path.join(core.data_dir(), "error.log"), "a", encoding="utf-8") as f:
                    f.write("[SystemPage] %s\n" % e)
            except Exception:
                pass
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
        self.addSubInterface(self.ai, FIF.EDUCATION, "AI 助教")
        self.addSubInterface(self.experiments, FIF.EDUCATION, "课堂工具")
        self.addSubInterface(self.chat, FIF.MESSAGE, "聊天")
        self.addSubInterface(self.forum, FIF.CHAT, "论坛")
        self.addSubInterface(self.anns, FIF.MEGAPHONE, "公告")
        self.addSubInterface(self.admin, FIF.CERTIFICATE, "管理", NavigationItemPosition.BOTTOM)
        self.navigationInterface.widget(self.admin.objectName()).setVisible(False)
        if self.system is not None:
            self.addSubInterface(self.system, FIF.BROOM, "系统维护", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.profile, FIF.PEOPLE, "个人中心", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
        self.navigationInterface.setExpandWidth(180)
        def page_changed(_index):
            current = self.stackedWidget.currentWidget()
            if current is self.library:
                self.library.load()
            elif current is self.ai:
                self.ai.refresh()
        self.stackedWidget.currentChanged.connect(page_changed)
        self._tray()
        self._initialize_main_screen_layout()
        self.refresh_all()
        QTimer.singleShot(2500, lambda: self.check_update(manual=False))
        QTimer.singleShot(300, self.load_theme)

    @staticmethod
    def _screen_key(screen):
        if screen is None:
            return ""
        try:
            name = screen.name() or ""
            serial = screen.serialNumber() or ""
            return f"{name}:{serial}" if serial else name or f"screen-{id(screen)}"
        except Exception:
            return f"screen-{id(screen)}"

    @staticmethod
    def _rect_tuple(rect):
        return int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())

    @staticmethod
    def _screen_bounds_tuple(screen):
        area = screen.availableGeometry()
        return int(area.x()), int(area.y()), int(area.width()), int(area.height())

    def _initialize_main_screen_layout(self):
        app = QApplication.instance()
        screens = app.screens() if app else []
        self._watch_main_screen_changes()
        screen = (QApplication.primaryScreen() if app else None) or (screens[0] if screens else None)
        if screen is None:
            return
        bounds = self._screen_bounds_tuple(screen)
        self._main_screen_key = self._screen_key(screen)
        self._main_screen_bounds = {self._screen_key(item): self._screen_bounds_tuple(item) for item in screens}
        bx, by, width, height = bounds
        min_w = min(self._base_main_minimum.width(), max(1, width - 16))
        min_h = min(self._base_main_minimum.height(), max(1, height - 16))
        preferred = self._preferred_main_size
        x = bx + (width - preferred.width()) // 2
        y = by + (height - preferred.height()) // 2
        fitted = fit_window_rect((x, y, preferred.width(), preferred.height()), bounds,
                                 desired_size=(preferred.width(), preferred.height()),
                                 min_size=(min_w, min_h), margin=8)
        self._applying_main_screen_layout = True
        try:
            self.setMinimumSize(min_w, min_h)
            self.setGeometry(*fitted)
        finally:
            self._applying_main_screen_layout = False

    def _watch_main_screen_changes(self):
        app = QApplication.instance()
        if app is None:
            return
        for name in ("screenAdded", "primaryScreenChanged"):
            signal = getattr(app, name, None)
            if signal is not None:
                try:
                    signal.connect(self._schedule_main_screen_reflow)
                except (TypeError, RuntimeError):
                    pass
        removed = getattr(app, "screenRemoved", None)
        if removed is not None:
            try:
                removed.connect(self._main_screen_removed)
            except (TypeError, RuntimeError):
                pass
        for screen in app.screens():
            self._watch_main_screen(screen)

    def _main_screen_removed(self, screen):
        self._watched_main_screen_keys.discard(self._screen_key(screen))
        self._schedule_main_screen_reflow()

    def _watch_main_screen(self, screen):
        if screen is None:
            return
        key = self._screen_key(screen)
        if not key or key in self._watched_main_screen_keys:
            return
        self._watched_main_screen_keys.add(key)
        for name in ("geometryChanged", "availableGeometryChanged", "logicalDotsPerInchChanged",
                     "physicalDotsPerInchChanged"):
            signal = getattr(screen, name, None)
            if signal is not None:
                try:
                    signal.connect(self._schedule_main_screen_reflow)
                except (TypeError, RuntimeError):
                    pass

    def _connect_window_screen_signal(self):
        if self._window_screen_signal_connected:
            return
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.screenChanged.connect(self._schedule_main_screen_reflow)
                self._window_screen_signal_connected = True
            except (AttributeError, TypeError, RuntimeError):
                pass

    def _schedule_main_screen_reflow(self, *_args):
        app = QApplication.instance()
        if app is not None:
            for screen in app.screens():
                self._watch_main_screen(screen)
        self._main_screen_reflow_timer.start()

    def _refresh_main_screen_layout(self):
        app = QApplication.instance()
        screens = app.screens() if app else []
        if not screens:
            return
        current_bounds = {self._screen_key(screen): self._screen_bounds_tuple(screen) for screen in screens}
        state = self.windowState()
        was_minimized = bool(state & Qt.WindowMinimized)
        was_maximized = bool(state & Qt.WindowMaximized)
        was_fullscreen = bool(state & Qt.WindowFullScreen)
        rect = self.normalGeometry() if (was_minimized or was_maximized or was_fullscreen) else self.geometry()
        rect_tuple = self._rect_tuple(rect)
        screen_probe = (self.frameGeometry().center() if was_maximized or was_fullscreen
                        else rect.center())
        screen = QApplication.screenAt(screen_probe) or QApplication.primaryScreen() or screens[0]
        key = self._screen_key(screen)
        bounds = current_bounds.get(key, self._screen_bounds_tuple(screen))
        previous_key = self._main_screen_key
        previous_bounds = self._main_screen_bounds.get(previous_key)
        map_from = None
        if previous_bounds is not None and (
                (key == previous_key and previous_bounds != bounds) or previous_key not in current_bounds):
            map_from = previous_bounds

        bx, by, width, height = bounds
        min_w = min(self._base_main_minimum.width(), max(1, width - 16))
        min_h = min(self._base_main_minimum.height(), max(1, height - 16))
        self._applying_main_screen_layout = True
        try:
            self.setMinimumSize(min_w, min_h)
            fitted = fit_window_rect(
                rect_tuple, bounds,
                desired_size=(self._preferred_main_size.width(), self._preferred_main_size.height()),
                min_size=(min_w, min_h), margin=8, old_bounds=map_from,
            )
            if was_minimized or was_maximized or was_fullscreen:
                # A maximized window can hide an out-of-bounds restore rectangle.
                # Reflow that normal rectangle too, then restore the original state.
                self.setWindowState(Qt.WindowNoState)
                self.setGeometry(*fitted)
                self.setWindowState(state)
            else:
                self.setGeometry(*fitted)
        finally:
            self._applying_main_screen_layout = False
        self._main_screen_key = key
        self._main_screen_bounds = current_bounds

    def event(self, event):
        result = super().event(event)
        if event.type() == getattr(QEvent, "ScreenChangeInternal", -1):
            if hasattr(self, "_main_screen_reflow_timer"):
                QTimer.singleShot(0, self._schedule_main_screen_reflow)
        return result

    def nativeEvent(self, event_type, message):
        if core.IS_WIN:
            try:
                from ctypes import wintypes
                native = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if native.message in (0x007E, 0x02E0, 0x001A):  # DISPLAYCHANGE, DPI/SETTINGCHANGE
                    QTimer.singleShot(120, self._schedule_main_screen_reflow)
                    stage = getattr(self, "stage", None)
                    refresh = getattr(stage, "_schedule_screen_refresh", None)
                    if refresh is not None:
                        QTimer.singleShot(120, refresh)
            except Exception:
                pass
        return super().nativeEvent(event_type, message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (not getattr(self, "_applying_main_screen_layout", True)
                and not self.isMaximized() and not self.isFullScreen()):
            size = event.size()
            self._preferred_main_size = QSize(size.width(), size.height())

    def showEvent(self, event):
        super().showEvent(event)
        self._connect_window_screen_signal()
        self._schedule_main_screen_reflow()

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
        self.ai.refresh()
        if self.stackedWidget.currentWidget() is self.library:
            self.library.load()

    # ---- 下载并放映
    def open_file(self, job: dict):
        name = job.get("name") or "课件.pptx"
        try:
            path = core.download_path(name)
        except Exception as e:
            MessageBox("尚未设置课件保存位置", str(e), self).exec()
            self.switchTo(self.settings)
            return
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
        try:
            dest = core.update_temp_path()
        except Exception as e:
            self._updating = False
            self.toast("请先在设置中指定可写的缓存目录，再下载更新包：" + str(e), ok=False)
            self.switchTo(self.settings)
            return
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
