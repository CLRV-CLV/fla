"""主窗口 (qfluentwidgets FluentWindow)"""
from __future__ import annotations

import os
import threading
import webbrowser

from PyQt5.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QTextCursor
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
                             QPlainTextEdit, QSystemTrayIcon, QTableWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (Action, BodyLabel, ComboBox, CaptionLabel, CardWidget, FluentWindow, IconWidget, InfoBar,
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
        for ic, t, to in ((FIF.LIBRARY, "课件库", "library"), (FIF.EDUCATION, "AI 助教", "ai"),
                          (FIF.LINK, "实验工具", "experiments"), (FIF.MESSAGE, "聊天", "chat"),
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


class _AIInput(QPlainTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            event.accept()
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class AIPage(Page):
    """桌面端复用服务端 AI 模型、权限和 SSE 对话接口。"""
    def __init__(self, win):
        super().__init__("ai", "AI 助教", "使用管理员开放的模型；对话会保存到当前账号。")
        self.win = win
        self.models = []
        self.conversation_id = None
        self.transcript = ""
        self.answer = ""
        self.busy = False
        self.stream_error = False
        self.events = _AIEvents()
        self.events.event.connect(self._on_stream_event)
        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(50)
        self.flush_timer.timeout.connect(self._flush_answer)

        row = QHBoxLayout()
        self.model = ComboBox()
        self.model.setMinimumWidth(260)
        self.model.addItem("选择 AI 模型", userData=None)
        refresh_btn = PushButton(FIF.SYNC, "刷新模型")
        refresh_btn.clicked.connect(self.refresh_models)
        new_btn = PushButton(FIF.ADD, "新对话")
        new_btn.clicked.connect(self.new_conversation)
        row.addWidget(BodyLabel("模型"))
        row.addWidget(self.model)
        self.search_check = QCheckBox("联网搜索")
        self.search_check.setToolTip("本轮问题会发送给 DuckDuckGo 搜索网页；请勿搜索个人或敏感信息。")
        row.addWidget(self.search_check)
        row.addWidget(refresh_btn)
        row.addStretch(1)
        row.addWidget(new_btn)
        self.lay.addLayout(row)
        search_notice = CaptionLabel("仅勾选后才联网；开启时本轮问题会发送给 DuckDuckGo，请勿输入个人或敏感信息。")
        search_notice.setWordWrap(True)
        search_notice.setTextColor("#666666", "#aaaaaa")
        self.lay.addWidget(search_notice)

        self.status = CaptionLabel("登录后使用 AI 助教")
        self.status.setTextColor("#666666", "#aaaaaa")
        self.lay.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("AI 对话会显示在这里。")
        self.output.setMinimumHeight(300)
        self.lay.addWidget(self.output, 1)

        row = QHBoxLayout()
        self.input = _AIInput()
        self.input.setPlaceholderText("输入问题，Enter 发送，Shift+Enter 换行")
        self.input.setMaximumHeight(130)
        self.input.setMinimumHeight(82)
        self.input.send_requested.connect(self.send)
        self.send_btn = PrimaryPushButton(FIF.SEND, "发送")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        self.lay.addLayout(row)
        self.refresh_models()

    def refresh_models(self, *_args):
        if not cfg.token:
            self.status.setText("请先到「设置」登录账号")
            self.model.clear()
            self.model.addItem("登录后加载模型", userData=None)
            self.send_btn.setEnabled(False)
            return
        self.status.setText("正在获取可用模型…")
        run_async(lambda: core.api("GET", "/api/ai/models"), self._models_loaded)

    def _models_loaded(self, result):
        status, data = result if isinstance(result, tuple) else (0, {})
        if status != 200 or not isinstance(data, dict):
            self.status.setText((data or {}).get("detail", "无法加载 AI 模型，请检查网络或登录状态"))
            self.send_btn.setEnabled(False)
            return
        self.models = data.get("items") or []
        self.model.clear()
        if not data.get("enabled"):
            self.model.addItem("AI 功能已关闭", userData=None)
            self.status.setText("AI 功能已由管理员关闭")
            self.send_btn.setEnabled(False)
            return
        for item in self.models:
            label = item.get("name") or item.get("model") or "AI 模型"
            if item.get("channel"):
                label += "（" + item["channel"] + "）"
            self.model.addItem(label, userData=item.get("id"))
        if not self.models:
            self.model.addItem("暂无可用模型", userData=None)
            self.status.setText("管理员尚未为你的用户组开放 AI 模型")
            self.send_btn.setEnabled(False)
        else:
            self.status.setText("可以开始提问；支持流式输出。")
            self.send_btn.setEnabled(True)

    def new_conversation(self, *_args):
        if self.busy:
            return
        self.conversation_id = None
        self.transcript = ""
        self.answer = ""
        self.output.clear()
        self.status.setText("新对话已就绪")
        self.input.setFocus()

    def send(self, *_args):
        if self.busy:
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
        self.send_btn.setEnabled(False)
        self.status.setText("正在连接 AI…")
        if self.conversation_id:
            self._start_stream(self.conversation_id, text, int(model_id))
            return
        run_async(lambda: core.api("POST", "/api/ai/conversations", {"model_id": int(model_id)}),
                  lambda result: self._conversation_created(result, text, int(model_id)))

    def _conversation_created(self, result, text, model_id):
        status, data = result if isinstance(result, tuple) else (0, {})
        if status != 200 or not isinstance(data, dict) or not data.get("id"):
            self.busy = False
            self.send_btn.setEnabled(True)
            self.status.setText("无法创建对话：" + str((data or {}).get("detail", "请重新登录或检查权限")))
            self.input.setPlainText(text)
            return
        self.conversation_id = int(data["id"])
        self._start_stream(self.conversation_id, text, model_id)

    def _start_stream(self, conversation_id, text, model_id):
        self.transcript += "你：" + text + "\nAI："
        self.answer = ""
        self.stream_error = False
        self._flush_answer()
        self.status.setText("AI 正在回答…")
        body = {"content": text, "model_id": model_id, "online_search": self.search_check.isChecked()}
        def work():
            return core.ai_stream(f"/api/ai/conversations/{conversation_id}/messages", body,
                                  on_event=lambda event: self.events.event.emit(event), timeout=180)
        run_async(work, self._stream_finished)

    def _on_stream_event(self, event):
        if not isinstance(event, dict):
            return
        if event.get("searching"):
            self.status.setText("正在联网搜索…")
        if event.get("delta"):
            self.status.setText("AI 正在回答…")
            self.answer += str(event["delta"])
            if not self.flush_timer.isActive():
                self.flush_timer.start()
        if event.get("error"):
            self.stream_error = True
            self.answer += ("\n" if self.answer else "") + "⚠ " + str(event["error"])
            self._flush_answer()

    def _flush_answer(self):
        self.flush_timer.stop()
        self.output.setPlainText(self.transcript + self.answer)
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def _stream_finished(self, result):
        status, data = result if isinstance(result, tuple) else (0, {})
        if status != 200:
            detail = (data or {}).get("detail", "请求失败") if isinstance(data, dict) else "请求失败"
            self.answer += ("\n" if self.answer else "") + "⚠ " + str(detail)
        if self.answer:
            self.transcript += self.answer + "\n\n"
        self.answer = ""
        self._flush_answer()
        self.busy = False
        self.send_btn.setEnabled(bool(self.models) and bool(cfg.token))
        self.status.setText("回答完成" if status == 200 and not self.stream_error else "AI 请求失败")


# ================================================================== 设置
class ExperimentResource(CardWidget):
    def __init__(self, subject, name, description, license_text, url, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 15, 18, 15)
        details = QVBoxLayout()
        details.setSpacing(5)
        title = StrongBodyLabel(f"{subject} · {name}")
        description_label = BodyLabel(description)
        description_label.setWordWrap(True)
        license_label = CaptionLabel(license_text)
        license_label.setTextColor("#666666", "#aaaaaa")
        details.addWidget(title)
        details.addWidget(description_label)
        details.addWidget(license_label)
        row.addLayout(details, 1)
        button = PushButton(FIF.LINK, "官方项目页 / 获取")
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        row.addWidget(button)


class ExperimentsPage(Page):
    """由上游开源教学项目提供的可选实验工具目录，不复制或静默安装第三方程序。"""

    def __init__(self, win):
        super().__init__("experiments", "实验工具", "可选使用物理、化学与生物教学工具；优先选择开放源码项目。")
        self.win = win
        note = CardWidget()
        note_lay = QVBoxLayout(note)
        note_lay.setContentsMargins(18, 15, 18, 15)
        note_lay.addWidget(StrongBodyLabel("安全与许可"))
        note_text = BodyLabel("按钮只会打开项目官方页面，由教师自行选择在线使用或下载安装；FLA 不会在后台下载、静默安装或执行第三方程序。首次使用请先在受控设备上测试，并遵守各项目许可、署名要求与实验安全规范。")
        note_text.setWordWrap(True)
        note_lay.addWidget(note_text)
        self.lay.addWidget(note)

        resources = [
            ("物理", "Tracker 视频分析与建模", "从实验视频追踪位置、速度、加速度，并将模型与真实运动对照；提供 Windows、macOS、Linux 安装包。", "GNU GPL v3 · 开源桌面软件", "https://opensourcephysics.github.io/tracker-website/"),
            ("物理 / 化学 / 生物", "PhET 互动模拟", "科罗拉多大学的研究型互动模拟；可在线运行，部分模拟可下载离线使用。具体许可与署名要求以单个模拟页面为准。", "开放源码 · 按单个模拟项目的许可使用", "https://phet.colorado.edu/en/simulations"),
            ("化学", "Avogadro 2", "跨平台分子编辑与三维可视化，适用于课堂分子结构演示，也可处理晶体和生物分子。", "BSD 3-Clause · 开源桌面软件", "https://avogadro.cc/"),
            ("生物", "Virtual Genetics Lab", "遗传学与进化实验模拟，面向高中高年级及大学入门课程；项目提供在线体验。", "项目标示免费开源 · 具体子许可请查看上游", "https://vgl.umb.edu/"),
            ("生物", "Human BioMedia 虚拟实验", "包含血液学、血型和神经生理等互动实验练习；无需安装，适合作为课堂补充。", "开放源码教学内容 · 在线使用", "https://www.humanbiomedia.org/lab-simulations/"),
            ("生物 / 化学", "Virtual Molecular Biology Lab", "模拟凝胶电泳实验，便于课前熟悉操作流程；上游为 Java Swing 项目，属于较早期软件，需先验证当前系统兼容性。", "Academic Free License · 旧版 Java 桌面项目", "https://sourceforge.net/projects/virtbiolab/"),
        ]
        for item in resources:
            self.lay.addWidget(ExperimentResource(*item))
        self.lay.addStretch(1)


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
        self.setWindowTitle(core.APP_NAME)
        self.resize(940, 680)
        self.home = HomePage(self)
        self.library = LibraryPage(self)
        self.ai = AIPage(self)
        self.experiments = ExperimentsPage(self)
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
        self.addSubInterface(self.experiments, FIF.LINK, "实验工具")
        self.addSubInterface(self.chat, FIF.MESSAGE, "聊天")
        self.addSubInterface(self.forum, FIF.CHAT, "论坛")
        self.addSubInterface(self.anns, FIF.MEGAPHONE, "公告")
        self.addSubInterface(self.admin, FIF.CERTIFICATE, "管理", NavigationItemPosition.BOTTOM)
        self.navigationInterface.widget(self.admin.objectName()).setVisible(False)
        self.addSubInterface(self.profile, FIF.PEOPLE, "个人中心", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
        self.navigationInterface.setExpandWidth(180)
        def page_changed(_index):
            current = self.stackedWidget.currentWidget()
            if current is self.library:
                self.library.load()
            elif current is self.ai:
                self.ai.refresh_models()
        self.stackedWidget.currentChanged.connect(page_changed)
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
        self.ai.refresh_models()
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
