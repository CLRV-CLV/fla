"""客户端内置的网站功能: 聊天 / 论坛 / 个人中心 (原生界面, 不跳浏览器)"""
from __future__ import annotations

import html
import os

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl
from PyQt5.QtGui import QImage, QTextDocument
from PyQt5.QtWidgets import (QFileDialog, QHBoxLayout, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, ComboBox, InfoBar, InfoBarPosition, LineEdit,
                            ListWidget, MessageBoxBase, PasswordLineEdit, PlainTextEdit, PrimaryPushButton,
                            ProgressBar, PushButton, StrongBodyLabel, SubtitleLabel, TextBrowser, TitleLabel,
                            TransparentToolButton)

from . import core
from .core import cfg
from .window import Page, fmt_size, run_async

CSS = """
body{font-family:'Microsoft YaHei UI','Segoe UI';font-size:14px;color:#1a1a1a}
.who{color:#888;font-size:12px}
.b{background:#f2f2f2;padding:8px 12px}
.me{background:#111;color:#fff;padding:8px 12px}
.sys{color:#999;font-size:12px}
a{color:#0b57d0;text-decoration:none}
.me a{color:#9ecbff}
h2{margin:4px 0}
.post{border-bottom:1px solid #eee;padding:10px 0}
"""


def _err(data, st):
    if isinstance(data, dict) and data.get("detail"):
        d = data["detail"]
        return d if isinstance(d, str) else str(d)
    return "无法连接服务器" if not st else f"错误 {st}"


def toast(parent, ok, text, title=""):
    (InfoBar.success if ok else InfoBar.error)(title, text, parent=parent.window(),
                                              position=InfoBarPosition.TOP, duration=2500 if ok else 5000)


def _esc(t):
    return html.escape(t or "").replace("\n", "<br>")


def _short(ts):
    return (ts or "")[5:16]


# ============================================================== 聊天
class ChatPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("chat")
        self.win = win
        self.rid = 0
        self.rooms = []
        self.msgs = []
        self.imgs = {}          # att_id -> QImage
        self._busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        top = QHBoxLayout()
        top.addWidget(TitleLabel("聊天"))
        top.addStretch(1)
        self.tip = CaptionLabel("")
        top.addWidget(self.tip)
        rb = TransparentToolButton(FIF.SYNC)
        rb.setToolTip("刷新")
        rb.clicked.connect(self.load)
        top.addWidget(rb)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.list = ListWidget()
        self.list.setFixedWidth(240)
        self.list.currentRowChanged.connect(self.pick)
        body.addWidget(self.list)

        right = QVBoxLayout()
        self.title = SubtitleLabel("选择左侧的会话")
        right.addWidget(self.title)
        self.view = TextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self.open_link)
        self.view.document().setDefaultStyleSheet(CSS)
        right.addWidget(self.view, 1)
        inp = QHBoxLayout()
        self.att = TransparentToolButton(FIF.FOLDER)
        self.att.setToolTip("发送图片 / 文件")
        self.att.clicked.connect(self.send_file)
        inp.addWidget(self.att)
        self.edit = LineEdit()
        self.edit.setPlaceholderText("输入消息，回车发送")
        self.edit.returnPressed.connect(self.send)
        inp.addWidget(self.edit, 1)
        self.sendb = PrimaryPushButton("发送")
        self.sendb.clicked.connect(self.send)
        inp.addWidget(self.sendb)
        right.addLayout(inp)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(3000)
        self._tick = 0
        self._enable(False)

    def _enable(self, on):
        for w in (self.edit, self.sendb, self.att):
            w.setEnabled(on)

    # ---- 会话列表
    def load(self):
        if not cfg.token:
            self.list.clear()
            self.tip.setText("请先到「设置」登录账号")
            return

        def done(r):
            st, data = r
            if st != 200:
                self.tip.setText(_err(data, st))
                return
            self.tip.setText("")
            self.fill_rooms(data.get("items") or [])
        run_async(lambda: core.api("GET", "/api/chat/inbox"), done)

    def fill_rooms(self, items):
        self.rooms = items
        self.list.blockSignals(True)
        self.list.clear()
        sel = -1
        for i, r in enumerate(items):
            name = r.get("name") or "会话"
            if r.get("kind") == "dm" and r.get("peer"):
                name = r["peer"].get("nickname") or name
            last = r.get("last") or {}
            prev = ""
            if last:
                prev = ("[已撤回]" if last.get("deleted") else
                        {"image": "[图片]", "file": "[文件]", "audio": "[语音]"}.get(last.get("kind"), "")
                        + (last.get("content") or ""))
                if r.get("kind") != "dm":
                    prev = (last.get("nickname") or "") + "：" + prev
            n = r.get("unread") or 0
            badge = f"  ({n})" if n else ""
            it = QListWidgetItem(f"{'📌 ' if r.get('pinned') else ''}{name}{badge}\n{prev[:28]}")
            it.setSizeHint(QSize(220, 54))
            self.list.addItem(it)
            if r["id"] == self.rid:
                sel = i
        if sel >= 0:
            self.list.setCurrentRow(sel)
        self.list.blockSignals(False)

    def pick(self, row):
        if row < 0 or row >= len(self.rooms):
            return
        r = self.rooms[row]
        if r["id"] == self.rid:
            return
        self.rid = r["id"]
        self.msgs = []
        name = r.get("name") or "会话"
        if r.get("kind") == "dm" and r.get("peer"):
            name = r["peer"].get("nickname") or name
        self.title.setText(f"{name}" + (f"  · {r.get('members')} 人" if r.get("kind") != "dm" else ""))
        self.view.setHtml("<p class='sys'>加载中…</p>")
        self._enable(True)
        rid = self.rid

        def done(res):
            st, data = res
            if rid != self.rid:
                return
            if st != 200:
                self.view.setHtml(f"<p class='sys'>{_esc(_err(data, st))}</p>")
                return
            self.msgs = data.get("items") or []
            self.render(True)
            self.mark_read()
        run_async(lambda: core.api("GET", f"/api/chat/rooms/{rid}/messages?limit=60"), done)

    def mark_read(self):
        if self.msgs and self.rid:
            rid, last = self.rid, self.msgs[-1]["id"]
            run_async(lambda: core.api("POST", f"/api/chat/rooms/{rid}/read", {"last_id": last}),
                      lambda _r: None)
            for r in self.rooms:
                if r["id"] == rid and r.get("unread"):
                    r["unread"] = 0
                    self.fill_rooms(self.rooms)
                    break

    # ---- 消息渲染
    def render(self, to_bottom=False):
        sb = self.view.verticalScrollBar()
        at_bottom = to_bottom or sb.value() >= sb.maximum() - 30
        parts = []
        for m in self.msgs:
            a = m.get("author") or {}
            who = _esc(m.get("nickname_in_room") or a.get("nickname") or "")
            if m.get("deleted"):
                parts.append(f"<p class='sys' align='center'>{who} 撤回了一条消息</p>")
                continue
            att = m.get("att")
            if m.get("kind") == "image" and att:
                aid = att["id"]
                if aid in self.imgs:
                    c = f"<a href='att:{aid}'><img src='img:{aid}'></a>"
                else:
                    c = f"<a href='att:{aid}'>[图片] {_esc(att.get('name'))}</a>"
                    self.fetch_img(aid, att.get("url"))
            elif att:
                c = f"<a href='att:{att['id']}'>📎 {_esc(att.get('name'))} ({fmt_size(att.get('size') or 0)})</a>"
            else:
                c = _esc(m.get("content"))
                if m.get("reply"):
                    rp = m["reply"]
                    c = (f"<span class='sys'>回复 {_esc((rp.get('author') or {}).get('nickname', ''))}："
                         f"{_esc((rp.get('content') or '')[:40])}</span><br>") + c
            mine = m.get("mine")
            al = "right" if mine else "left"
            cls = "me" if mine else "b"
            head = f"{_short(m.get('created_at'))}" if mine else f"{who}  {_short(m.get('created_at'))}"
            parts.append(f"<p class='who' align='{al}'>{head}</p>"
                         f"<table align='{al}' cellpadding='0'><tr><td class='{cls}'>{c}</td></tr></table>")
        if not parts:
            parts.append("<p class='sys' align='center'>还没有消息，打个招呼吧</p>")
        self.view.setHtml("".join(parts))
        if at_bottom:
            QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def fetch_img(self, aid, url):
        if aid in self.imgs or not url:
            return
        self.imgs[aid] = None

        def done(b):
            img = QImage()
            if b and img.loadFromData(b):
                if img.width() > 260:
                    img = img.scaledToWidth(260, Qt.SmoothTransformation)
                self.imgs[aid] = img
                self.view.document().addResource(QTextDocument.ImageResource, QUrl(f"img:{aid}"), img)
                self.render()
        run_async(lambda: core.fetch_bytes(url), done)

    def open_link(self, url: QUrl):
        s = url.toString()
        if s.startswith("att:"):
            aid = int(s[4:])
            att = next((m["att"] for m in self.msgs if m.get("att") and m["att"]["id"] == aid), None)
            if not att:
                return
            try:
                dest = core.cache_path(f"chat_{aid}_{att.get('name') or 'file'}")
            except Exception as e:
                toast(self, False, "请先在设置中选择可写的缓存目录：" + str(e))
                return

            def done(ok):
                if ok and hasattr(os, "startfile"):
                    os.startfile(dest)  # noqa
                elif not ok:
                    toast(self, False, "下载失败")
            run_async(lambda: core.download(core.full_url(att["url"]), dest), done)
        elif s.startswith("http"):
            import webbrowser
            webbrowser.open(s)

    # ---- 发送
    def send(self):
        t = self.edit.text().strip()
        if not t or not self.rid or self._busy:
            return
        self._busy = True
        rid = self.rid

        def done(r):
            self._busy = False
            st, data = r
            if st != 200:
                toast(self, False, _err(data, st))
                return
            self.edit.clear()
            self.poll_msgs()
        run_async(lambda: core.api("POST", f"/api/chat/rooms/{rid}/messages", {"content": t, "kind": "text"}), done)

    def send_file(self):
        if not self.rid:
            return
        p, _ = QFileDialog.getOpenFileName(self, "发送图片 / 文件", os.path.expanduser("~"))
        if not p:
            return
        ext = os.path.splitext(p)[1].lower()
        kind = "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp") else "file"
        rid = self.rid
        self.tip.setText("正在发送 " + os.path.basename(p) + " …")

        def work():
            st, data = core.upload(p, None, url="/api/chat/attachments", fields={"kind": kind})
            if st != 200:
                return st, data
            return core.api("POST", f"/api/chat/rooms/{rid}/messages",
                            {"kind": kind, "att_id": data.get("id") or data.get("att_id") or 0})

        def done(r):
            st, data = r
            self.tip.setText("")
            if st != 200:
                toast(self, False, _err(data, st))
            else:
                self.poll_msgs()
        run_async(work, done)

    # ---- 轮询
    def poll(self):
        if not cfg.token or not self.isVisible():
            return
        self._tick += 1
        if self.rid:
            self.poll_msgs()
        if self._tick % 4 == 0:
            self.load()

    def poll_msgs(self):
        rid = self.rid
        after = self.msgs[-1]["id"] if self.msgs else 0

        def done(r):
            st, data = r
            if rid != self.rid or st != 200:
                return
            new = [m for m in (data.get("items") or []) if not self.msgs or m["id"] > self.msgs[-1]["id"]]
            if new:
                self.msgs += new
                self.render()
                self.mark_read()
        run_async(lambda: core.api("GET", f"/api/chat/rooms/{rid}/messages?after={after}"), done)

    def showEvent(self, e):
        super().showEvent(e)
        self.load()


# ============================================================== 论坛
class NewThreadDialog(MessageBoxBase):
    def __init__(self, parent, boards):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel("发表新帖"))
        self.board = ComboBox()
        for b in boards:
            self.board.addItem(b["name"], userData=b["id"])
        self.t = LineEdit()
        self.t.setPlaceholderText("标题")
        self.c = PlainTextEdit()
        self.c.setPlaceholderText("内容")
        self.c.setMinimumHeight(200)
        for w in (self.board, self.t, self.c):
            self.viewLayout.addWidget(w)
        self.yesButton.setText("发布")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(520)


class ForumPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("forum")
        self.win = win
        self.boards = []
        self.threads = []
        self.page = 1
        self.pages = 1
        self.tid = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # 列表
        lw = QWidget()
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(TitleLabel("论坛"))
        top.addStretch(1)
        self.board = ComboBox()
        self.board.setMinimumWidth(160)
        self.board.currentIndexChanged.connect(lambda _: self.load_threads(1))
        top.addWidget(self.board)
        nb = PrimaryPushButton(FIF.EDIT, "发帖")
        nb.clicked.connect(self.new_thread)
        top.addWidget(nb)
        rb = TransparentToolButton(FIF.SYNC)
        rb.clicked.connect(self.load)
        top.addWidget(rb)
        lv.addLayout(top)
        self.tip = CaptionLabel("")
        lv.addWidget(self.tip)
        self.list = ListWidget()
        self.list.itemClicked.connect(lambda it: self.open_thread(it.data(Qt.UserRole)))
        lv.addWidget(self.list, 1)
        pg = QHBoxLayout()
        self.prev = PushButton(FIF.LEFT_ARROW, "上一页")
        self.prev.clicked.connect(lambda: self.load_threads(self.page - 1))
        self.next = PushButton(FIF.RIGHT_ARROW, "下一页")
        self.next.clicked.connect(lambda: self.load_threads(self.page + 1))
        self.pgl = CaptionLabel("")
        pg.addStretch(1)
        for w in (self.prev, self.pgl, self.next):
            pg.addWidget(w)
        pg.addStretch(1)
        lv.addLayout(pg)
        self.stack.addWidget(lw)

        # 详情
        dw = QWidget()
        dv = QVBoxLayout(dw)
        dv.setContentsMargins(0, 0, 0, 0)
        dt = QHBoxLayout()
        back = PushButton(FIF.LEFT_ARROW, "返回列表")
        back.clicked.connect(lambda: (self.stack.setCurrentIndex(0), self.load_threads(self.page)))
        dt.addWidget(back)
        dt.addStretch(1)
        self.adm = []
        for t, fn in (("置顶/取消", self.adm_pin), ("锁定/解锁", self.adm_lock), ("删除帖子", self.adm_del)):
            b = PushButton(t)
            b.clicked.connect(fn)
            b.hide()
            dt.addWidget(b)
            self.adm.append(b)
        dv.addLayout(dt)
        self.detail = TextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.detail.document().setDefaultStyleSheet(CSS)
        dv.addWidget(self.detail, 1)
        rp = QHBoxLayout()
        self.reply = PlainTextEdit()
        self.reply.setPlaceholderText("写下你的回复…")
        self.reply.setFixedHeight(80)
        rp.addWidget(self.reply, 1)
        self.rb = PrimaryPushButton("回复")
        self.rb.clicked.connect(self.send_reply)
        rp.addWidget(self.rb)
        dv.addLayout(rp)
        self.stack.addWidget(dw)

    def showEvent(self, e):
        super().showEvent(e)
        if self.stack.currentIndex() == 0:
            self.load()

    def load(self):
        if not cfg.token:
            self.tip.setText("请先到「设置」登录账号")
            self.list.clear()
            return

        def done(r):
            st, data = r
            if st != 200:
                self.tip.setText(_err(data, st))
                return
            self.boards = data.get("items") or []
            cur = self.board.currentData()
            self.board.blockSignals(True)
            self.board.clear()
            self.board.addItem("全部板块", userData=0)
            for b in self.boards:
                self.board.addItem(f"{b['name']} ({b.get('threads', 0)})", userData=b["id"])
            idx = max(0, next((i for i in range(self.board.count()) if self.board.itemData(i) == cur), 0))
            self.board.setCurrentIndex(idx)
            self.board.blockSignals(False)
            self.load_threads(self.page)
        run_async(lambda: core.api("GET", "/api/forum/boards"), done)

    def load_threads(self, page):
        page = max(1, min(page, self.pages if page > self.page else page))
        bid = self.board.currentData() or 0
        self.tip.setText("加载中…")

        def done(r):
            st, data = r
            if st != 200:
                self.tip.setText(_err(data, st))
                return
            self.page, self.pages = data.get("page", 1), data.get("pages", 1)
            self.threads = data.get("items") or []
            self.tip.setText(f"共 {data.get('total', 0)} 个帖子" if self.threads else "这里还没有帖子，点「发帖」开个头")
            self.list.clear()
            bn = {b["id"]: b["name"] for b in self.boards}
            for t in self.threads:
                flag = ("📌 " if t.get("pinned") else "") + ("🔒 " if t.get("locked") else "")
                it = QListWidgetItem(f"{flag}{t['title']}\n{(t.get('author') or {}).get('nickname', '')} · "
                                     f"{bn.get(t.get('board_id'), '')} · {t.get('replies', 0)} 回复 · "
                                     f"{_short(t.get('last_reply_at'))}")
                it.setData(Qt.UserRole, t["id"])
                it.setSizeHint(QSize(400, 56))
                self.list.addItem(it)
            self.pgl.setText(f"{self.page} / {self.pages}")
            self.prev.setEnabled(self.page > 1)
            self.next.setEnabled(self.page < self.pages)
        run_async(lambda: core.api("GET", f"/api/forum/threads?board={bid}&page={page}"), done)

    def open_thread(self, tid, page=1):
        self.tid = tid
        self.stack.setCurrentIndex(1)
        self.detail.setHtml("<p class='sys'>加载中…</p>")

        def done(r):
            st, data = r
            if st != 200:
                self.detail.setHtml(f"<p class='sys'>{_esc(_err(data, st))}</p>")
                return
            a = data.get("author") or {}
            h = [f"<h2>{_esc(data.get('title'))}</h2>",
                 f"<p class='who'>{_esc(a.get('nickname'))} · {_short(data.get('created_at'))}</p>",
                 f"<div class='post'>{_esc(data.get('content'))}</div>"]
            for i, p in enumerate(data.get("posts") or []):
                pa = p.get("author") or {}
                n = (data.get("page", 1) - 1) * 20 + i + 1
                h.append(f"<div class='post'><p class='who'>#{n} {_esc(pa.get('nickname'))} · "
                         f"{_short(p.get('created_at'))}{' · 已编辑' if p.get('edited') else ''}</p>"
                         f"{_esc(p.get('content'))}</div>")
            if data.get("pages", 1) > 1:
                h.append(f"<p class='sys'>第 {data['page']}/{data['pages']} 页（显示最新一页回复）</p>")
            self.detail.setHtml("".join(h))
            self.cur_t = data
            is_admin = (getattr(self.win, "me", None) or {}).get("role") == "admin"
            for b in self.adm:
                b.setVisible(is_admin or (b is self.adm[2] and data.get("mine")))
            locked = data.get("locked")
            self.reply.setEnabled(not locked)
            self.rb.setEnabled(not locked)
            self.reply.setPlaceholderText("帖子已锁定" if locked else "写下你的回复…")
            if page == 1 and data.get("pages", 1) > 1:
                self.open_thread(tid, data["pages"])
            else:
                sb = self.detail.verticalScrollBar()
                QTimer.singleShot(0, lambda: sb.setValue(sb.maximum() if page > 1 or data.get("posts") else 0))
        run_async(lambda: core.api("GET", f"/api/forum/threads/{tid}?page={page}"), done)

    def adm_pin(self):
        t = getattr(self, "cur_t", None)
        if t:
            run_async(lambda: core.api("PATCH", f"/api/forum/threads/{t['id']}", {"pinned": not t.get("pinned")}),
                      lambda r: (toast(self, r[0] == 200, "已更新" if r[0] == 200 else _err(r[1], r[0])),
                                 self.open_thread(t["id"])))

    def adm_lock(self):
        t = getattr(self, "cur_t", None)
        if t:
            run_async(lambda: core.api("PATCH", f"/api/forum/threads/{t['id']}", {"locked": not t.get("locked")}),
                      lambda r: (toast(self, r[0] == 200, "已更新" if r[0] == 200 else _err(r[1], r[0])),
                                 self.open_thread(t["id"])))

    def adm_del(self):
        from qfluentwidgets import MessageBox
        t = getattr(self, "cur_t", None)
        if not t:
            return
        m = MessageBox("删除帖子", f"删除「{t.get('title')}」及全部回复？", self.window())
        m.yesButton.setText("删除")
        m.cancelButton.setText("取消")
        if m.exec():
            def done(r):
                toast(self, r[0] == 200, "已删除" if r[0] == 200 else _err(r[1], r[0]))
                if r[0] == 200:
                    self.stack.setCurrentIndex(0)
                    self.load()
            run_async(lambda: core.api("DELETE", f"/api/forum/threads/{t['id']}"), done)

    def send_reply(self):
        t = self.reply.toPlainText().strip()
        if not t or not self.tid:
            return
        tid = self.tid

        def done(r):
            st, data = r
            if st != 200:
                toast(self, False, _err(data, st))
                return
            self.reply.clear()
            self.open_thread(tid)
        run_async(lambda: core.api("POST", f"/api/forum/threads/{tid}/posts", {"content": t}), done)

    def new_thread(self):
        if not cfg.token:
            toast(self, False, "请先登录")
            return
        if not self.boards:
            toast(self, False, "还没有板块")
            return
        d = NewThreadDialog(self.window(), self.boards)
        cur = self.board.currentData()
        for i in range(d.board.count()):
            if d.board.itemData(i) == cur:
                d.board.setCurrentIndex(i)
        if not d.exec():
            return
        body = {"board_id": d.board.currentData(), "title": d.t.text().strip(), "content": d.c.toPlainText().strip()}

        def done(r):
            st, data = r
            if st != 200:
                toast(self, False, _err(data, st))
                return
            toast(self, True, "已发布")
            self.open_thread(data["id"])
        run_async(lambda: core.api("POST", "/api/forum/threads", body), done)


# ============================================================== 个人中心
class ProfilePage(Page):
    def __init__(self, win):
        super().__init__("profile", "个人中心")
        self.win = win
        card = CardWidget()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 16, 20, 16)
        self.name = StrongBodyLabel("未登录")
        self.meta = CaptionLabel("")
        cv.addWidget(self.name)
        cv.addWidget(self.meta)
        self.quota = ProgressBar()
        cv.addWidget(self.quota)
        self.qtext = CaptionLabel("")
        cv.addWidget(self.qtext)
        self.lay.addWidget(card)

        c2 = CardWidget()
        v2 = QVBoxLayout(c2)
        v2.setContentsMargins(20, 16, 20, 16)
        v2.addWidget(StrongBodyLabel("资料"))
        self.nick = LineEdit()
        self.nick.setPlaceholderText("昵称")
        self.sig = LineEdit()
        self.sig.setPlaceholderText("个性签名")
        v2.addWidget(self.nick)
        v2.addWidget(self.sig)
        sb = PrimaryPushButton("保存资料")
        sb.clicked.connect(self.save)
        h = QHBoxLayout()
        h.addWidget(sb)
        h.addStretch(1)
        v2.addLayout(h)
        self.lay.addWidget(c2)

        c3 = CardWidget()
        v3 = QVBoxLayout(c3)
        v3.setContentsMargins(20, 16, 20, 16)
        v3.addWidget(StrongBodyLabel("修改密码"))
        self.old = PasswordLineEdit()
        self.old.setPlaceholderText("当前密码")
        self.new = PasswordLineEdit()
        self.new.setPlaceholderText("新密码")
        v3.addWidget(self.old)
        v3.addWidget(self.new)
        pb = PushButton("修改密码")
        pb.clicked.connect(self.change_pw)
        h3 = QHBoxLayout()
        h3.addWidget(pb)
        h3.addStretch(1)
        v3.addLayout(h3)
        self.lay.addWidget(c3)

        self.lay.addStretch(1)

    def showEvent(self, e):
        super().showEvent(e)
        self.load()

    def load(self):
        if not cfg.token:
            self.name.setText("未登录 — 请到「设置」登录")
            self.meta.setText("")
            return

        def done(r):
            st, u = r
            if st != 200:
                self.name.setText(_err(u, st))
                return
            role = "管理员" if u.get("role") == "admin" else ("教师" if u.get("is_teacher") else "用户")
            self.name.setText(f"{u.get('nickname')}  （{u.get('username')} · {role}）")
            self.meta.setText(f"{u.get('signature') or '还没有签名'} · 注册于 {(u.get('created_at') or '')[:10]}")
            if not self.nick.hasFocus() or not self.nick.text():
                self.nick.setText(u.get("nickname") or "")
            if not self.sig.hasFocus() or not self.sig.text():
                self.sig.setText(u.get("signature") or "")
            q, used = u.get("quota_bytes") or 0, u.get("used_bytes") or 0
            if q:
                self.quota.setValue(min(100, int(used * 100 / q)))
                self.qtext.setText(f"已用 {fmt_size(used)} / {fmt_size(q)}")
            else:
                self.quota.setValue(0)
                self.qtext.setText(f"已用 {fmt_size(used)}（不限）")
        run_async(lambda: core.api("GET", "/api/auth/me"), done)


    def save(self):
        body = {"nickname": self.nick.text().strip(), "signature": self.sig.text()}

        def done(r):
            st, data = r
            toast(self, st == 200, "已保存" if st == 200 else _err(data, st))
            if st == 200:
                self.load()
        run_async(lambda: core.api("PUT", "/api/users/profile", body), done)

    def change_pw(self):
        body = {"old_password": self.old.text(), "new_password": self.new.text()}
        if not body["new_password"]:
            return

        def done(r):
            st, data = r
            toast(self, st == 200, "密码已修改" if st == 200 else _err(data, st))
            if st == 200:
                self.old.clear()
                self.new.clear()
        run_async(lambda: core.api("POST", "/api/auth/change_password", body), done)


# ============================================================== 公告
LV = {"info": "公告", "warn": "注意", "imp": "重要"}


class AnnDialog(MessageBoxBase):
    """公告弹窗: 重要公告 5 秒后才能关闭"""

    def __init__(self, parent, a):
        super().__init__(parent)
        imp = a.get("level") == "imp"
        tag = CaptionLabel(LV.get(a.get("level"), "公告") + ("公告" if imp else ""))
        tag.setTextColor("#c62828" if imp else "#666666", "#ef9a9a" if imp else "#aaaaaa")
        self.viewLayout.addWidget(tag)
        self.viewLayout.addWidget(SubtitleLabel(a.get("title") or ""))
        body = TextBrowser()
        body.setOpenExternalLinks(True)
        body.document().setDefaultStyleSheet(CSS)
        body.setHtml(_esc(a.get("content")) or "<span class='sys'>（无正文）</span>")
        body.setMinimumHeight(180)
        self.viewLayout.addWidget(body)
        self.viewLayout.addWidget(CaptionLabel(_short(a.get("created_at"))))
        self.cancelButton.hide()
        self.buttonLayout.insertStretch(0, 1)
        self.yesButton.setText("我知道了")
        self.widget.setMinimumWidth(520)
        self.left = 5 if imp else 0
        if self.left:
            self.yesButton.setEnabled(False)
            self.yesButton.setText(f"请阅读 {self.left} 秒")
            self.t = QTimer(self)
            self.t.timeout.connect(self._tick)
            self.t.start(1000)

    def _tick(self):
        self.left -= 1
        if self.left > 0:
            self.yesButton.setText(f"请阅读 {self.left} 秒")
        else:
            self.t.stop()
            self.yesButton.setEnabled(True)
            self.yesButton.setText("我知道了")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.left > 0:
            return
        super().keyPressEvent(e)

    def reject(self):
        if self.left > 0:
            return
        super().reject()


class AnnPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("anns")
        self.win = win
        self.items = []
        self.popped = set()
        self._popping = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        top = QHBoxLayout()
        top.addWidget(TitleLabel("公告"))
        top.addStretch(1)
        self.tip = CaptionLabel("")
        top.addWidget(self.tip)
        rb = TransparentToolButton(FIF.SYNC)
        rb.clicked.connect(self.load)
        top.addWidget(rb)
        root.addLayout(top)
        self.view = TextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.document().setDefaultStyleSheet(CSS)
        root.addWidget(self.view, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.load(popup=True))
        self.timer.start(60000)

    def showEvent(self, e):
        super().showEvent(e)
        self.load()

    def load(self, popup=True):
        if not cfg.token:
            self.view.setHtml("<p class='sys'>请先到「设置」登录账号</p>")
            self.win.set_ann_badge(0)
            return

        def done(r):
            st, data = r
            if st != 200:
                self.tip.setText(_err(data, st))
                return
            self.tip.setText("")
            self.items = data.get("items") or []
            self.render()
            self.win.set_ann_badge(sum(1 for a in self.items if not a.get("read")))
            if popup:
                self.popup()
        run_async(lambda: core.api("GET", "/api/announcements"), done)

    def render(self):
        if not self.items:
            self.view.setHtml("<p class='sys' align='center'>暂无公告</p>")
            return
        h = []
        for a in self.items:
            imp = a.get("level") == "imp"
            h.append(f"<div class='post'><p class='who'>"
                     f"<span style='color:{'#c62828' if imp else '#666'};font-weight:bold'>{LV.get(a.get('level'), '公告')}</span>"
                     f"{' · 专属' if a.get('personal') else ''} · {_short(a.get('created_at'))}"
                     f"{'' if a.get('read') else ' <span style=color:#c62828>● 未读</span>'}</p>"
                     f"<h2>{_esc(a.get('title'))}</h2>{_esc(a.get('content'))}</div>")
        self.view.setHtml("".join(h))

    def popup(self):
        if self._popping:
            return
        q = [a for a in self.items if not a.get("read") and a["id"] not in self.popped]
        q.sort(key=lambda a: a.get("level") != "imp")
        if not q:
            return
        self._popping = True
        try:
            for a in q:
                self.popped.add(a["id"])
                win = self.win
                if win.isMinimized() or not win.isVisible():
                    win.bring_up() if hasattr(win, "bring_up") else win.show()
                AnnDialog(win, a).exec()
                a["read"] = True
                aid = a["id"]
                run_async(lambda aid=aid: core.api("POST", "/api/announcements/read", {"ids": [aid]}), lambda _r: None)
        finally:
            self._popping = False
        self.render()
        self.win.set_ann_badge(sum(1 for a in self.items if not a.get("read")))
