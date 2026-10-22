"""v3.3 低延迟投屏链路
- 服务器中继: QWebSocket → wss://<线路>/api/remote/hub/<sid>?role=pc  (任何网络都能用)
- 局域网直连: 本机 QWebSocketServer :8308 + 迷你 HTTP :8309 提供手机页面 (同一 Wi-Fi, 不绕香港服务器)
协议: 文本 = JSON (指令/批注/ack/peers), 二进制 = 首字节 b"P"(电脑画面)/b"C"(手机画面) + JPEG
流控: 每个目标同时只允许 1 帧在途, 收到对端 ack 才发下一帧 (1.5s 超时兜底) → 不堆积、永远是最新画面
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtNetwork import QAbstractSocket, QHostAddress, QNetworkInterface
from PyQt5.QtWebSockets import QWebSocket, QWebSocketProtocol, QWebSocketServer

from . import core

WS_PORT, HTTP_PORT = 8308, 8309
ACK_TIMEOUT = 1.5


def lan_ips() -> list:
    if os.environ.get("FLA_TEST_LAN_IP"):          # 自检用
        return [os.environ["FLA_TEST_LAN_IP"]]
    ips = []
    for a in QNetworkInterface.allAddresses():
        if a.protocol() != QAbstractSocket.IPv4Protocol or a.isLoopback():
            continue
        s = a.toString()
        if s.startswith("169.254."):
            continue
        p = s.split(".")
        private = s.startswith("192.168.") or s.startswith("10.") or (p[0] == "172" and 16 <= int(p[1]) <= 31)
        if private:
            ips.append(s)
    ips.sort(key=lambda x: (not x.startswith("192.168."), x))
    return ips


def cast_html_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "cast.html"),
              os.path.join(here, "..", "..", "..", "web", "cast.html")):
        if os.path.exists(p):
            return os.path.normpath(p)
    return ""


class _Target:
    """一个画面接收方 (服务器中继 或 某个局域网手机)"""

    def __init__(self, ws, name):
        self.ws = ws
        self.name = name
        self.pending = 0.0

    def ready(self):
        return not self.pending or time.time() - self.pending > ACK_TIMEOUT


class Link(QObject):
    message = pyqtSignal(dict)        # 手机发来的指令/批注 (勿命名 event: 会覆盖 QObject.event)
    phone_frame = pyqtSignal(bytes)   # 手机画面 JPEG
    status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.sid = self.code = ""
        self.server_ws = None
        self.server_ok = False
        self.server_phones = 0
        self.server_target = None
        self.lan_server = None
        self.lan_targets = {}         # QWebSocket -> _Target
        self.http = None
        self.lan_urls = []
        self.reconnect = QTimer(self, singleShot=True, timeout=self._connect_server)
        self.ping = QTimer(self, interval=15000, timeout=self._ping)

    # ------------------------------------------------------------ 生命周期
    def start(self, sid, code):
        self.stop()
        self.sid, self.code = sid, code
        self._connect_server()
        self._start_lan()
        self.ping.start()

    def stop(self):
        self.ping.stop()
        self.reconnect.stop()
        self.sid = ""
        if self.server_ws:
            try:
                self.server_ws.textMessageReceived.disconnect()
                self.server_ws.binaryMessageReceived.disconnect()
                self.server_ws.disconnected.disconnect()
            except Exception:
                pass
            self.server_ws.close()
            self.server_ws.deleteLater()
        self.server_ws = None
        self.server_ok = False
        self.server_phones = 0
        self.server_target = None
        for ws in list(self.lan_targets):
            ws.close()
        self.lan_targets.clear()
        if self.lan_server:
            self.lan_server.close()
            self.lan_server.deleteLater()
            self.lan_server = None
        if self.http:
            threading.Thread(target=self.http.shutdown, daemon=True).start()
            self.http = None

    # ------------------------------------------------------------ 服务器中继
    def _connect_server(self):
        if not self.sid:
            return
        base = core.full_url("/").rstrip("/")
        wsurl = base.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{wsurl}/api/remote/hub/{self.sid}?code={self.code}&role=pc"
        ws = QWebSocket("", QWebSocketProtocol.VersionLatest, self)
        ws.connected.connect(self._srv_connected)
        ws.disconnected.connect(self._srv_disconnected)
        ws.textMessageReceived.connect(lambda t: self._on_text(t, "server"))
        ws.binaryMessageReceived.connect(lambda b: self._on_binary(bytes(b), "server"))
        try:
            ws.sslErrors.connect(lambda errs: ws.ignoreSslErrors())   # 与 core._CTX 一致: 证书异常也能连
        except Exception:
            pass
        self.server_ws = ws
        ws.open(QUrl(url))

    def _srv_connected(self):
        self.server_ok = True
        self.server_target = _Target(self.server_ws, "server")
        self.status.emit("已连接服务器中继")

    def _srv_disconnected(self):
        self.server_ok = False
        self.server_target = None
        self.server_phones = 0
        if self.sid:
            self.reconnect.start(2000)

    def _ping(self):
        if self.server_ok and self.server_ws:
            self.server_ws.sendTextMessage("ping")

    # ------------------------------------------------------------ 局域网直连
    def _start_lan(self):
        self.lan_urls = []
        ips = lan_ips()
        if not ips:
            return
        srv = QWebSocketServer("FLA", QWebSocketServer.NonSecureMode, self)
        port = 0
        for p in (WS_PORT, WS_PORT + 10, WS_PORT + 20):
            if srv.listen(QHostAddress.AnyIPv4, p):
                port = p
                break
        if not port:
            srv.deleteLater()
            return
        srv.newConnection.connect(self._lan_new)
        self.lan_server = srv
        html = cast_html_path()
        hport = 0
        if html:
            for p in (HTTP_PORT, HTTP_PORT + 10, HTTP_PORT + 20):
                try:
                    self.http = ThreadingHTTPServer(("0.0.0.0", p), _make_handler(html, port))
                    hport = p
                    break
                except OSError:
                    continue
        if self.http:
            threading.Thread(target=self.http.serve_forever, daemon=True).start()
        import urllib.parse
        srvq = urllib.parse.quote(core.full_url("/").rstrip("/"), safe="")
        for ip in ips[:3]:
            self.lan_urls.append({"ip": ip, "ws": f"ws://{ip}:{port}/hub/{self.sid}?code={self.code}",
                                  "page": f"http://{ip}:{hport}/cast.html?sid={self.sid}&code={self.code}&lan=1&srv={srvq}" if hport else ""})

    def _lan_new(self):
        while self.lan_server and self.lan_server.hasPendingConnections():
            ws = self.lan_server.nextPendingConnection()
            u = ws.requestUrl()
            q = dict(x.split("=", 1) for x in (u.query() or "").split("&") if "=" in x)
            if not u.path().endswith("/hub/" + self.sid) or q.get("code") != self.code:
                ws.close(QWebSocketProtocol.CloseCodePolicyViolated, "bad code")
                ws.deleteLater()
                continue
            t = _Target(ws, "lan")
            self.lan_targets[ws] = t
            ws.textMessageReceived.connect(lambda txt, w=ws: self._on_text(txt, w))
            ws.binaryMessageReceived.connect(lambda b, w=ws: self._on_binary(bytes(b), w))
            ws.disconnected.connect(lambda w=ws: self._lan_gone(w))
            ws.sendTextMessage(json.dumps({"type": "peers", "pc": 1, "phone": len(self.lan_targets)}))
            self.status.emit("手机已通过局域网直连 ✓（极速）")
            self.message.emit({"type": "hello", "role": "phone"})

    def _lan_gone(self, ws):
        self.lan_targets.pop(ws, None)
        ws.deleteLater()

    # ------------------------------------------------------------ 收
    def _on_text(self, txt, src):
        if txt == "pong":
            return
        try:
            d = json.loads(txt)
        except Exception:
            return
        t = d.get("type")
        if t == "ack":
            tgt = self.server_target if src == "server" else self.lan_targets.get(src)
            if tgt:
                tgt.pending = 0.0
            return
        if t == "peers":
            if src == "server":
                was = self.server_phones
                self.server_phones = int(d.get("phone") or 0)
                if self.server_phones and not was:
                    self.status.emit("手机已连接 ✓")
                    self.message.emit({"type": "hello", "role": "phone"})
            return
        if d.get("action"):
            self.message.emit({"type": "action", "action": d["action"], "data": d.get("data") or {}, "sender": "phone"})

    def _on_binary(self, b, src):
        if not b:
            return
        if b[:1] == b"C":
            self.phone_frame.emit(b[1:])
            ack = json.dumps({"type": "ack", "ch": "C"})
            ws = self.server_ws if src == "server" else src
            try:
                ws.sendTextMessage(ack)
            except Exception:
                pass

    # ------------------------------------------------------------ 发
    def targets(self):
        out = list(self.lan_targets.values())
        if self.server_ok and self.server_phones and self.server_target:
            out.append(self.server_target)
        return out

    def has_lan_viewer(self):
        return bool(self.lan_targets)

    def any_ready(self):
        return any(t.ready() for t in self.targets())

    def send_frame(self, jpeg: bytes):
        data = b"P" + jpeg
        n = 0
        for t in self.targets():
            if t.ready():
                t.pending = time.time()
                t.ws.sendBinaryMessage(data)
                n += 1
        return n

    def send_json(self, obj: dict):
        txt = json.dumps(obj, ensure_ascii=False)
        for t in self.targets():
            t.ws.sendTextMessage(txt)


def _make_handler(html_path, ws_port):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/cast.html"):
                try:
                    with open(html_path, "rb") as f:
                        body = f.read()
                except OSError:
                    self.send_error(404)
                    return
                body = body.replace(b"/*__LAN_WS_PORT__*/", f"window.FLA_LAN_WS_PORT={ws_port};".encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/ping":
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            else:
                self.send_error(404)
    return H
