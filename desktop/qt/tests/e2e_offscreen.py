"""离线端到端自检 (Linux offscreen): 需本地 uvicorn 在 127.0.0.1:8306
用法: QT_QPA_PLATFORM=offscreen python3 desktop/qt/tests/e2e_offscreen.py [截图目录]
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LOCALAPPDATA", "/tmp/fla-e2e-local")

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QPointF  # noqa: E402
from PyQt5.QtGui import QColor, QFontDatabase, QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
for f in (os.path.expanduser("~/.fonts/NotoSansCJKsc-Regular.otf"),):
    if os.path.exists(f):
        QFontDatabase.addApplicationFont(f)
from qfluentwidgets import setThemeColor  # noqa: E402

setThemeColor("#111111")
from fla_qt import core  # noqa: E402

B = "http://127.0.0.1:8306"
core.full_url = lambda p: p if p.startswith("http") else B + p
from fla_qt.stage import Stage, load_ink, _screen_key  # noqa: E402
from fla_qt.window import MainWindow  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"
os.makedirs(out, exist_ok=True)
st = Stage(lambda: None)
w = MainWindow(st, True)
w.show()


def post(path, body=None, raw=None, ct="application/json"):
    req = urllib.request.Request(B + path, data=raw if raw is not None else json.dumps(body).encode(),
                                 headers={"Content-Type": ct}, method="POST")
    return json.loads(urllib.request.urlopen(req).read() or b"{}")


def wait(cond, t=8):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.03)
        if cond():
            return True
    return False


def snap(x, n):
    for _ in range(5):
        app.processEvents()
    x.grab().save(f"{out}/{n}.png")


def stroke(page_pts):
    return {"tool": "pen", "color": "#E53935", "w": 3, "pts": [QPointF(*p) for p in page_pts]}


ok = {}
ok["server_locked"] = core.cfg.server in core.ALLOWED

# ---------- 桌面模式
st.start()
ok["desk_dock"] = st.desk_dock.isVisible() and not st.ppt_dock.isVisible()
ok["desk_no_pagelabel"] = not st.desk_dock.page_label.isVisible()
# Simulate a display shrinking after the toolbar was positioned on the former large mode.
scr = QApplication.primaryScreen()
work = scr.availableGeometry()
key = _screen_key(scr)
original_dock_rect = st.desk_dock.frameGeometry()
st._screen_bounds[key] = type(work)(work.x(), work.y(), max(1920, work.width()), max(1080, work.height()))
st.desk_dock.setGeometry(work.x() + max(1700, work.width() - 1),
                         work.y() + max(900, work.height() - 1),
                         st.desk_dock.width(), st.desk_dock.height())
st._floating_screens[id(st.desk_dock)] = key
st._refresh_screen_layout()
app.processEvents()
reflowed = st.desk_dock.frameGeometry()
ok["screen_reflow"] = work.contains(reflowed.topLeft()) and work.contains(reflowed.bottomRight())
st.desk_dock.setGeometry(original_dock_rect)
app.processEvents()
# The app window must also stay reachable and fit its work area after a display shrinks.
original_main_rect = w.geometry()
main_key = w._screen_key(scr)
w._main_screen_key = main_key
w._main_screen_bounds[main_key] = (work.x(), work.y(), max(1920, work.width()), max(1080, work.height()))
w.setGeometry(work.x() + max(1700, work.width() - 1),
              work.y() + max(900, work.height() - 1),
              w.width(), w.height())
w._refresh_main_screen_layout()
app.processEvents()
main_rect = w.frameGeometry()
ok["main_window_reflow"] = work.contains(main_rect.topLeft()) and work.contains(main_rect.bottomRight())
# Reflow the saved restore rectangle too; maximized apps otherwise return off-screen.
w.setGeometry(work.x() + max(1700, work.width() - 1),
              work.y() + max(900, work.height() - 1),
              w.width(), w.height())
app.processEvents()
w._main_screen_bounds[main_key] = (work.x(), work.y(), max(1920, work.width()), max(1080, work.height()))
w.showMaximized()
app.processEvents()
w._refresh_main_screen_layout()
app.processEvents()
normal_rect = w.normalGeometry()
ok["main_restore_geometry_reflow"] = work.contains(normal_rect.topLeft()) and work.contains(normal_rect.bottomRight())
w.showNormal()
w.setGeometry(original_main_rect)
w._refresh_main_screen_layout()
app.processEvents()
st.ov.strokes().append(stroke([(10, 10), (50, 50)]))
st.handle("next")
ok["desk_next_keeps_canvas"] = st.ov.page == 1 and len(st.ov.strokes()) == 1

# ---------- 进入 PPT
info = {"active": True, "app": "PowerPoint", "path": r"C:\\a\\课件.pptx", "page": 2, "total": 9, "rect": None}
st._on_ppt(info)
ok["ppt_enter_swaps_dock"] = st.ppt_dock.isVisible() and not st.desk_dock.isVisible()
ok["desktop_ink_saved"] = len(load_ink("desktop").get(1, [])) == 1
ok["ppt_canvas_fresh"] = st.ov.page == 2 and not st.ov.strokes()
ok["ppt_label"] = st.ppt_dock.page_label.text() == "2/9"
st.ov.strokes().append(stroke([(5, 5), (90, 90)]))
st.handle("next")
cmd = st.watcher.q.get_nowait()
ok["ppt_next_via_com"] = cmd[0] == "next"
st._on_ppt({**info, "page": 3})            # 页码变化 (无论来自我们按钮还是点击 PPT)
ok["page3_empty"] = st.ov.page == 3 and not st.ov.strokes()
st._on_ppt({**info, "page": 2})
ok["page2_restored"] = len(st.ov.strokes()) == 1
st.handle("pen")
ok["pen_popup"] = st.pen_pop.isVisible()
st.handle("pen")
ok["pen_popup_toggle"] = not st.pen_pop.isVisible()
st.handle("eraser")
ok["eraser_popup"] = st.eraser_pop.isVisible()
snap(st.ppt_dock, "ppt_dock")
st.handle("mouse")
# ---------- 退出 PPT
st._on_ppt({"active": False})
ok["ppt_leave_restores_desk"] = st.desk_dock.isVisible() and not st.ppt_dock.isVisible()
ok["desk_ink_back"] = len(st.ov.pages.get(1, [])) == 1
ok["ppt_ink_on_disk"] = len(load_ink(st._ppt_name(info["path"])).get(2, [])) == 1
snap(st.desk_dock, "desk_dock")

# ---------- 手机投屏
st.handle("phone")
ok["session"] = wait(lambda: bool(w.cast.sid))
sid, code = w.cast.sid, w.cast.code
ok["qr"] = wait(lambda: w.cast.panel.qr.pixmap() is not None and not w.cast.panel.qr.pixmap().isNull())
wait(lambda: False, 1.0)
n0 = len(st.ov.strokes())
post(f"/api/remote/{sid}/action", {"action": "ink", "data": {"id": "a", "tool": "pen", "color": "#1E88E5", "w": 0.004,
                                                           "pts": [[0.1, 0.1], [0.5, 0.5]], "end": False}})
post(f"/api/remote/{sid}/action", {"action": "ink", "data": {"id": "a", "pts": [[0.9, 0.5]], "end": True}})
ok["phone_ink"] = wait(lambda: len(st.ov.strokes()) == n0 + 1)
post(f"/api/remote/{sid}/action", {"action": "erase", "data": {"x": 0.3, "y": 0.3, "r": 0.02, "mode": "pixel"}})
ok["pixel_erase"] = wait(lambda: len(st.ov.strokes()) == n0 + 2)
img = QImage(64, 48, QImage.Format_RGB32)
img.fill(QColor("#43A047"))
ba = QByteArray()
b = QBuffer(ba)
b.open(QIODevice.WriteOnly)
img.save(b, "JPG")
post(f"/api/remote/{sid}/action", {"action": "cast_start"})
post(f"/api/remote/{sid}/frame/phone?code={code}", raw=bytes(ba), ct="image/jpeg")
ok["phone_view"] = wait(lambda: w.cast.phone_view.isVisible() and w.cast.phone_view.pix is not None)
r = urllib.request.urlopen(f"{B}/api/remote/{sid}/frame/pc?code={code}&after=0")
ok["pc_frame"] = r.status == 200 and r.read(2) == b"\xff\xd8"
snap(w.cast.panel, "cast_panel")
w.cast.stop()
st.shutdown()
print(json.dumps(ok, ensure_ascii=False, indent=0))
print("ALL", all(ok.values()))
sys.exit(0 if all(ok.values()) else 1)
