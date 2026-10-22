"""管理员后台 (客户端内): 概览 / 用户 / 邀请码 / 公告 / 论坛板块 / 设置与外观"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QStackedWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox, LineEdit, MessageBox,
                            MessageBoxBase, Pivot, PlainTextEdit, PrimaryPushButton, PushButton, SearchLineEdit,
                            Slider, SpinBox, StrongBodyLabel, SubtitleLabel, SwitchButton, TableWidget, TitleLabel)

from . import core
from .social import _err, toast
from .window import fmt_size, run_async


def _table(headers):
    t = TableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _fill(t, rows):
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            it = QTableWidgetItem("" if v is None else str(v))
            t.setItem(i, j, it)


def _call(widget, method, path, body=None, ok_text="", then=None):
    def done(r):
        st, data = r
        if st != 200:
            toast(widget, False, _err(data, st))
            return
        if ok_text:
            toast(widget, True, ok_text)
        if then:
            then(data)
    run_async(lambda: core.api(method, path, body), done)


def _confirm(widget, title, text):
    m = MessageBox(title, text, widget.window())
    m.yesButton.setText("确定")
    m.cancelButton.setText("取消")
    return m.exec()


class _Form(MessageBoxBase):
    """简单表单对话框: fields = [(key, label, kind, default, extra)]"""

    def __init__(self, parent, title, fields):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title))
        self.w = {}
        for key, label, kind, default, extra in fields:
            self.viewLayout.addWidget(CaptionLabel(label))
            if kind == "text":
                e = LineEdit()
                e.setText(str(default or ""))
            elif kind == "long":
                e = PlainTextEdit()
                e.setPlainText(str(default or ""))
                e.setMinimumHeight(140)
            elif kind == "int":
                e = SpinBox()
                e.setRange(*(extra or (0, 1000000)))
                e.setValue(int(default or 0))
            elif kind == "combo":
                e = ComboBox()
                for v, t in extra:
                    e.addItem(t, userData=v)
                for i in range(e.count()):
                    if e.itemData(i) == default:
                        e.setCurrentIndex(i)
            else:  # bool
                e = CheckBox(extra or "")
                e.setChecked(bool(default))
            self.w[key] = (kind, e)
            self.viewLayout.addWidget(e)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(460)

    def values(self):
        out = {}
        for k, (kind, e) in self.w.items():
            out[k] = (e.text().strip() if kind == "text" else e.toPlainText().strip() if kind == "long" else
                      e.value() if kind == "int" else e.currentData() if kind == "combo" else e.isChecked())
        return out


# ------------------------------------------------------------------ 各分区
class OverviewTab(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        self.lbl = BodyLabel("")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        v.addStretch(1)

    def load(self):
        def done(r):
            st, d = r
            if st != 200:
                self.lbl.setText(_err(d, st))
                return
            self.lbl.setText(
                f"用户 {d.get('users')}（教师 {d.get('teachers')}）　课件 {d.get('files')}（{fmt_size(d.get('storage') or 0)}）\n"
                f"邀请码 {d.get('invites')}（已用 {d.get('invites_used')}）　转换中 {d.get('converting')}　转换失败 {d.get('failed')}\n"
                f"帖子 {d.get('threads')}　回帖 {d.get('forum_posts')}　聊天消息 {d.get('chat_messages')}　公告 {d.get('announcements')}")
        run_async(lambda: core.api("GET", "/api/admin/stats"), done)


class UsersTab(QWidget):
    def __init__(self):
        super().__init__()
        self.items = []
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.q = SearchLineEdit()
        self.q.setPlaceholderText("搜索用户名 / 昵称")
        self.q.searchSignal.connect(lambda _: self.load())
        self.q.returnPressed.connect(self.load)
        bar.addWidget(self.q, 1)
        for t, fn in (("编辑", self.edit), ("重置密码", self.reset), ("删除", self.delete)):
            b = PushButton(t)
            b.clicked.connect(fn)
            bar.addWidget(b)
        v.addLayout(bar)
        self.t = _table(["ID", "用户名", "昵称", "身份", "空间", "禁言", "最近登录"])
        self.t.cellDoubleClicked.connect(lambda *_: self.edit())
        v.addWidget(self.t, 1)

    def load(self):
        q = self.q.text().strip()

        def done(r):
            st, d = r
            if st != 200:
                return toast(self, False, _err(d, st))
            self.items = d.get("items") or []
            _fill(self.t, [(u["id"], u["username"], u["nickname"],
                            "管理员" if u["role"] == "admin" else ("教师" if u.get("is_teacher") else "用户"),
                            f"{fmt_size(u.get('used_bytes') or 0)} / {fmt_size(u.get('quota_bytes') or 0)}",
                            "是" if u.get("chat_banned") else "", (u.get("last_login_at") or "")[:16])
                           for u in self.items])
        import urllib.parse
        run_async(lambda: core.api("GET", f"/api/admin/users?size=200&q={urllib.parse.quote(q)}"), done)

    def cur(self):
        r = self.t.currentRow()
        if 0 <= r < len(self.items):
            return self.items[r]
        toast(self, False, "先选中一个用户")

    def edit(self):
        u = self.cur()
        if not u:
            return
        f = _Form(self.window(), f"编辑用户 {u['username']}", [
            ("nickname", "昵称", "text", u["nickname"], None),
            ("role", "角色", "combo", u["role"], [("user", "普通用户"), ("admin", "管理员")]),
            ("is_teacher", "", "bool", u.get("is_teacher"), "教师认证"),
            ("cert_title", "认证头衔", "text", u.get("cert_title"), None),
            ("quota_mb", "空间 (MB)", "int", (u.get("quota_bytes") or 0) // 1048576, (1, 1000000)),
            ("chat_banned", "", "bool", u.get("chat_banned"), "聊天禁言"),
        ])
        if f.exec():
            _call(self, "PUT", f"/api/admin/users/{u['id']}", f.values(), "已保存", lambda _d: self.load())

    def reset(self):
        u = self.cur()
        if u and _confirm(self, "重置密码", f"确定重置 {u['username']} 的密码？"):
            def show(d):
                m = MessageBox("新密码", f"{u['username']} 的新密码：{d.get('password')}\n（请告知本人，登录后修改）",
                               self.window())
                m.cancelButton.hide()
                m.exec()
            _call(self, "POST", f"/api/admin/users/{u['id']}/reset_password", None, "", show)

    def delete(self):
        u = self.cur()
        if u and _confirm(self, "删除用户", f"确定删除 {u['username']}？其课件也会删除，无法恢复。"):
            _call(self, "DELETE", f"/api/admin/users/{u['id']}", None, "已删除", lambda _d: self.load())


class InvitesTab(QWidget):
    def __init__(self):
        super().__init__()
        self.items = []
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        for t, fn in (("新建邀请码", self.new), ("批量生成", self.batch), ("删除", self.delete)):
            b = PushButton(t)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        self.t = _table(["邀请码", "已用/次数", "状态", "过期", "备注"])
        v.addWidget(self.t, 1)

    def load(self):
        def done(r):
            st, d = r
            if st != 200:
                return toast(self, False, _err(d, st))
            self.items = d if isinstance(d, list) else []
            stt = {"active": "可用", "used": "已用完", "expired": "已过期"}
            _fill(self.t, [(i["code"], f"{i['used_count']}/{i['max_uses']}", stt.get(i["status"], i["status"]),
                            (i.get("expires_at") or "永久")[:16], i.get("note")) for i in self.items])
        run_async(lambda: core.api("GET", "/api/admin/invites"), done)

    def new(self):
        f = _Form(self.window(), "新建邀请码", [
            ("code", "邀请码（留空自动生成）", "text", "", None),
            ("max_uses", "可用次数", "int", 1, (1, 100000)),
            ("duration_hours", "有效期（小时，0=永久）", "int", 0, (0, 100000)),
            ("note", "备注", "text", "", None)])
        if f.exec():
            b = f.values()
            b["duration_hours"] = b["duration_hours"] or None
            _call(self, "POST", "/api/admin/invites", b, "已创建", lambda _d: self.load())

    def batch(self):
        f = _Form(self.window(), "批量生成邀请码", [
            ("count", "数量", "int", 10, (1, 500)),
            ("max_uses", "每个可用次数", "int", 1, (1, 100000)),
            ("duration_hours", "有效期（小时，0=永久）", "int", 0, (0, 100000)),
            ("prefix", "前缀", "text", "", None), ("note", "备注", "text", "", None)])
        if f.exec():
            b = f.values()
            b["duration_hours"] = b["duration_hours"] or None

            def show(d):
                codes = "\n".join(i.get("code", "") for i in d.get("items") or [])
                from PyQt5.QtWidgets import QApplication
                QApplication.clipboard().setText(codes)
                toast(self, True, f"已生成 {len(d.get('items') or [])} 个，已复制到剪贴板")
                self.load()
            _call(self, "POST", "/api/admin/invites/batch", b, "", show)

    def delete(self):
        r = self.t.currentRow()
        if 0 <= r < len(self.items) and _confirm(self, "删除邀请码", f"删除 {self.items[r]['code']}？"):
            _call(self, "DELETE", f"/api/admin/invites/{self.items[r]['id']}", None, "已删除", lambda _d: self.load())


LEVELS = [("info", "普通"), ("warn", "注意"), ("imp", "重要（强制弹出 5 秒）")]


class AnnTab(QWidget):
    def __init__(self):
        super().__init__()
        self.items = []
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        for t, fn in (("发布公告", self.new), ("编辑", self.edit), ("上线/下线", self.toggle), ("删除", self.delete)):
            b = PushButton(t)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        self.t = _table(["标题", "级别", "范围", "状态", "已读", "时间"])
        self.t.cellDoubleClicked.connect(lambda *_: self.edit())
        v.addWidget(self.t, 1)

    def load(self):
        def done(r):
            st, d = r
            if st != 200:
                return toast(self, False, _err(d, st))
            self.items = d if isinstance(d, list) else (d.get("items") or [])
            lv = dict(LEVELS)
            _fill(self.t, [(a["title"], lv.get(a["level"], a["level"]).split("（")[0],
                            "全员" if a["scope"] == "global" else f"指定：{a.get('target')}",
                            "上线" if a["active"] else "下线", a.get("reads"), (a.get("created_at") or "")[:16])
                           for a in self.items])
        run_async(lambda: core.api("GET", "/api/admin/announcements"), done)

    def _form(self, a=None):
        a = a or {}
        return _Form(self.window(), "编辑公告" if a else "发布公告", [
            ("title", "标题", "text", a.get("title"), None),
            ("content", "内容", "long", a.get("content"), None),
            ("level", "级别", "combo", a.get("level", "info"), LEVELS),
            ("active", "", "bool", a.get("active", True), "立即上线")])

    def new(self):
        f = self._form()
        if f.exec():
            b = f.values()
            b["scope"] = "global"
            _call(self, "POST", "/api/admin/announcements", b, "已发布", lambda _d: self.load())

    def cur(self):
        r = self.t.currentRow()
        if 0 <= r < len(self.items):
            return self.items[r]
        toast(self, False, "先选中一条公告")

    def edit(self):
        a = self.cur()
        if not a:
            return
        f = self._form(a)
        if f.exec():
            b = f.values()
            b["scope"] = a["scope"]
            _call(self, "PATCH", f"/api/admin/announcements/{a['id']}", b, "已保存", lambda _d: self.load())

    def toggle(self):
        a = self.cur()
        if a:
            b = {"title": a["title"], "content": a.get("content") or "", "level": a["level"],
                 "scope": a["scope"], "active": not a["active"]}
            _call(self, "PATCH", f"/api/admin/announcements/{a['id']}", b, "已更新", lambda _d: self.load())

    def delete(self):
        a = self.cur()
        if a and _confirm(self, "删除公告", f"删除「{a['title']}」？"):
            _call(self, "DELETE", f"/api/admin/announcements/{a['id']}", None, "已删除", lambda _d: self.load())


class BoardsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.items = []
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        for t, fn in (("新建板块", self.new), ("编辑", self.edit), ("删除", self.delete)):
            b = PushButton(t)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        self.t = _table(["板块", "说明", "帖子数"])
        v.addWidget(self.t, 1)

    def load(self):
        def done(r):
            st, d = r
            if st == 200:
                self.items = d.get("items") or []
                _fill(self.t, [(b["name"], b.get("descr"), b.get("threads")) for b in self.items])
        run_async(lambda: core.api("GET", "/api/forum/boards"), done)

    def _form(self, b=None):
        b = b or {}
        return _Form(self.window(), "板块", [("name", "名称", "text", b.get("name"), None),
                                           ("descr", "说明", "text", b.get("descr"), None),
                                           ("sort", "排序（小的在前）", "int", b.get("sort", 0), (0, 9999))])

    def new(self):
        f = self._form()
        if f.exec():
            _call(self, "POST", "/api/admin/forum/boards", f.values(), "已创建", lambda _d: self.load())

    def edit(self):
        r = self.t.currentRow()
        if 0 <= r < len(self.items):
            f = self._form(self.items[r])
            if f.exec():
                _call(self, "PATCH", f"/api/admin/forum/boards/{self.items[r]['id']}", f.values(), "已保存",
                      lambda _d: self.load())

    def delete(self):
        r = self.t.currentRow()
        if 0 <= r < len(self.items) and _confirm(self, "删除板块", f"删除「{self.items[r]['name']}」及其全部帖子？"):
            _call(self, "DELETE", f"/api/admin/forum/boards/{self.items[r]['id']}", None, "已删除",
                  lambda _d: self.load())


class SettingsTab(QWidget):
    BOOLS = [("registration_open", "开放注册（注册始终需要邀请码）"), ("forum_enabled", "开启论坛"),
             ("chat_enabled", "开启聊天"), ("allow_group_create", "允许用户建群"),
             ("toolbar_keep", "放映时工具栏常驻")]
    BGS = [("site_bg", "网站背景"), ("login_bg", "登录页背景"), ("client_bg", "客户端背景")]

    def __init__(self, win):
        super().__init__()
        self.win = win
        v = QVBoxLayout(self)
        c1 = CardWidget()
        g = QVBoxLayout(c1)
        g.setContentsMargins(18, 14, 18, 14)
        g.addWidget(StrongBodyLabel("外观"))
        self.bg = {}
        for key, label in self.BGS:
            h = QHBoxLayout()
            lb = BodyLabel(label)
            lb.setFixedWidth(90)
            h.addWidget(lb)
            e = LineEdit()
            e.setPlaceholderText("留空=默认；#颜色；或图片链接")
            h.addWidget(e, 1)
            up = PushButton(FIF.PHOTO if hasattr(FIF, "PHOTO") else FIF.FOLDER, "上传图片")
            up.clicked.connect(lambda _=False, k=key: self.upload(k))
            h.addWidget(up)
            cl = PushButton("清除")
            cl.clicked.connect(lambda _=False, ed=e: ed.clear())
            h.addWidget(cl)
            self.bg[key] = e
            g.addLayout(h)
        h = QHBoxLayout()
        lb = BodyLabel("界面不透明度")
        lb.setFixedWidth(90)
        h.addWidget(lb)
        self.op = Slider(Qt.Horizontal)
        self.op.setRange(30, 100)
        self.opl = BodyLabel("100%")
        self.op.valueChanged.connect(lambda x: (self.opl.setText(f"{x}%"), self.win.apply_theme_preview(x)))
        h.addWidget(self.op, 1)
        h.addWidget(self.opl)
        g.addLayout(h)
        v.addWidget(c1)

        c2 = CardWidget()
        g2 = QVBoxLayout(c2)
        g2.setContentsMargins(18, 14, 18, 14)
        g2.addWidget(StrongBodyLabel("系统"))
        self.sw = {}
        for key, label in self.BOOLS:
            h = QHBoxLayout()
            h.addWidget(BodyLabel(label), 1)
            s = SwitchButton()
            s.setOnText("开")
            s.setOffText("关")
            self.sw[key] = s
            h.addWidget(s)
            g2.addLayout(h)
        self.num = {}
        for key, label in (("default_quota_mb", "新用户默认空间 (MB)"), ("max_upload_mb", "单文件上传上限 (MB)")):
            h = QHBoxLayout()
            h.addWidget(BodyLabel(label), 1)
            sp = SpinBox()
            sp.setRange(1, 1000000)
            self.num[key] = sp
            h.addWidget(sp)
            g2.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(BodyLabel("公开访问地址"), 1)
        self.pub = LineEdit()
        self.pub.setPlaceholderText("https://t.clrv.top")
        self.pub.setMinimumWidth(260)
        h.addWidget(self.pub)
        g2.addLayout(h)
        v.addWidget(c2)
        sb = PrimaryPushButton("保存设置")
        sb.clicked.connect(self.save)
        h = QHBoxLayout()
        h.addWidget(sb)
        h.addStretch(1)
        v.addLayout(h)
        v.addStretch(1)

    def load(self):
        def done(r):
            st, d = r
            if st != 200:
                return toast(self, False, _err(d, st))
            for k, e in self.bg.items():
                e.setText(d.get(k) or "")
            self.op.blockSignals(True)
            self.op.setValue(int(d.get("ui_opacity") or 100))
            self.opl.setText(f"{self.op.value()}%")
            self.op.blockSignals(False)
            for k, s in self.sw.items():
                s.setChecked(bool(d.get(k)))
            for k, s in self.num.items():
                s.setValue(int(d.get(k) or 1))
            self.pub.setText(d.get("public_base_url") or "")
        run_async(lambda: core.api("GET", "/api/admin/settings"), done)

    def upload(self, key):
        p, _ = QFileDialog.getOpenFileName(self, "选择背景图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if not p:
            return

        def done(r):
            st, d = r
            if st != 200:
                return toast(self, False, _err(d, st))
            self.bg[key].setText(d.get("url") or "")
            toast(self, True, "已上传并生效")
            self.win.load_theme()
        run_async(lambda: core.upload(p, None, url=f"/api/admin/theme/{key}"), done)

    def save(self):
        b = {k: e.text().strip() for k, e in self.bg.items()}
        b["ui_opacity"] = self.op.value()
        b.update({k: s.isChecked() for k, s in self.sw.items()})
        b.update({k: s.value() for k, s in self.num.items()})
        b["public_base_url"] = self.pub.text().strip()
        _call(self, "PUT", "/api/admin/settings", b, "设置已保存", lambda _d: self.win.load_theme())


class AdminPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("admin")
        self.win = win
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        top = QHBoxLayout()
        top.addWidget(TitleLabel("管理"))
        top.addStretch(1)
        v.addLayout(top)
        self.pivot = Pivot()
        self.stack = QStackedWidget()
        self.tabs = [("overview", "概览", OverviewTab()), ("users", "用户", UsersTab()),
                     ("invites", "邀请码", InvitesTab()), ("anns", "公告", AnnTab()),
                     ("boards", "论坛板块", BoardsTab()), ("settings", "设置与外观", SettingsTab(win))]
        for key, text, w in self.tabs:
            self.stack.addWidget(w)
            self.pivot.addItem(routeKey=key, text=text,
                               onClick=lambda _=False, w=w: (self.stack.setCurrentWidget(w), w.load()))
        self.pivot.setCurrentItem("overview")
        v.addWidget(self.pivot)
        v.addWidget(self.stack, 1)

    def showEvent(self, e):
        super().showEvent(e)
        self.stack.currentWidget().load()
