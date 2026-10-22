"""低延迟链路自检: 服务器中继 + 局域网直连 (需本地 uvicorn :8306)"""
import json
import os
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LOCALAPPDATA", "/tmp/fla-e2e-local")
os.environ.setdefault("FLA_TEST_LAN_IP", "127.0.0.1")

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PyQt5.QtGui import QColor, QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

import traceback  # noqa: E402
sys.excepthook = lambda *a: traceback.print_exception(*a)
app = QApplication(sys.argv)
from fla_qt import core  # noqa: E402

B = "http://127.0.0.1:8306"
core.full_url = lambda p: p if p.startswith("http") else B + p
from fla_qt.stage import Stage  # noqa: E402
from fla_qt.window import MainWindow  # noqa: E402

st = Stage(lambda: None)
w = MainWindow(st, True)


def wait(cond, t=8):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
        if cond():
            return True
    return False


def jpeg():
    img = QImage(64, 48, QImage.Format_RGB32)
    img.fill(QColor("#43A047"))
    ba = QByteArray()
    b = QBuffer(ba)
    b.open(QIODevice.WriteOnly)
    img.save(b, "JPG")
    return bytes(ba)


from PyQt5.QtGui import QPixmap  # noqa: E402
_n = [0]


def fake_screen():          # offscreen 平台截不到屏: 用变化的纯色图代替
    _n[0] += 1
    pm = QPixmap(1920, 1080)
    pm.fill(QColor.fromHsv(_n[0] * 7 % 360, 200, 200))
    return pm


w.cast._screen_pixmap = fake_screen
ok = {}
st.handle("phone")
ok["session"] = wait(lambda: bool(w.cast.sid))
sid, code = w.cast.sid, w.cast.code
ok["server_hub"] = wait(lambda: w.cast.link.server_ok)
ok["lan_listen"] = bool(w.cast.link.lan_urls)
lan = w.cast.link.lan_urls[0] if w.cast.link.lan_urls else {}
ok["lan_qr_mode"] = w.cast.qr_mode == "lan" and w.cast.panel.qr.pixmap() is not None


def phone(url, res, tag, seconds=2.0):
    try:
        with connect(url, open_timeout=5, max_size=8 * 1024 * 1024) as c:
            frames, first, t0 = 0, None, time.time()
            c.send(json.dumps({"action": "ink", "data": {"id": tag, "tool": "pen", "color": "#E53935", "w": 0.004,
                                                       "pts": [[0.1, 0.1], [0.4, 0.6]], "end": True}}))
            c.send(b"C" + jpeg())
            acked = False
            while time.time() - t0 < seconds:
                try:
                    m = c.recv(timeout=0.5)
                except TimeoutError:
                    continue
                if isinstance(m, bytes) and m[:1] == b"P":
                    frames += 1
                    first = first or time.time() - t0
                    c.send(json.dumps({"type": "ack", "ch": "P"}))
                elif isinstance(m, str) and '"ack"' in m and '"C"' in m:
                    acked = True
            res[tag] = {"frames": frames, "first": first, "ack_C": acked}
    except Exception as e:
        res[tag] = {"err": repr(e)}


res = {}
n0 = len(st.ov.strokes())
th = threading.Thread(target=phone, args=(f"ws://127.0.0.1:8306/api/remote/hub/{sid}?code={code}&role=phone", res, "srv"))
th.start()
wait(lambda: not th.is_alive(), 6)
ok["srv_frames"] = res.get("srv", {}).get("frames", 0) >= 3
ok["srv_ink"] = len(st.ov.strokes()) >= n0 + 1
ok["srv_phone_frame_ack"] = res.get("srv", {}).get("ack_C") is True
ok["phone_view_shown"] = w.cast.phone_view.isVisible()
ok["bad_code_rejected"] = False
try:
    with connect(f"ws://127.0.0.1:8306/api/remote/hub/{sid}?code=0000&role=phone", open_timeout=3) as c:
        c.recv(timeout=2)
except Exception:
    ok["bad_code_rejected"] = True

if lan:
    html = urllib.request.urlopen(lan["page"].split("&srv")[0], timeout=3).read().decode()
    ok["lan_page"] = "FLA_LAN_WS_PORT=" in html
    n1 = len(st.ov.strokes())
    th = threading.Thread(target=phone, args=(lan["ws"], res, "lan"))
    th.start()
    wait(lambda: not th.is_alive(), 6)
    ok["lan_frames"] = res.get("lan", {}).get("frames", 0) >= 3
    ok["lan_ink"] = len(st.ov.strokes()) >= n1 + 1
    ok["lan_phone_frame_ack"] = res.get("lan", {}).get("ack_C") is True
info = json.loads(urllib.request.urlopen(f"{B}/api/remote/{sid}/info?code={code}").read())
ok["lan_reported"] = bool(info.get("lan"))
w.cast.stop()
print(json.dumps(res))
print(json.dumps(ok, indent=0))
print("ALL", all(ok.values()))
sys.exit(0 if all(ok.values()) else 1)
