"""127.0.0.1:8307 本地桥: 网页端探测 / 一键调起本地放映 / 遥控指令"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt5.QtCore import QObject, pyqtSignal

from .core import BRIDGE_PORT, VERSION


class Bus(QObject):
    open_requested = pyqtSignal(dict)   # {url, name, token, fid}
    control = pyqtSignal(str)
    show_main = pyqtSignal()


bus = Bus()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Private-Network", "true")   # Chrome 私有网络访问预检

    def _json(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/api/status", "/", "/api/health"):
            self._json(200, {"ok": True, "version": VERSION, "status": "running", "service": "fla_desktop"})
        elif path == "/api/show":
            bus.show_main.emit()
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            body = {}
        if path == "/api/open":
            url = body.get("url") or ""
            if not url:
                self._json(400, {"ok": False, "error": "缺少 url"})
                return
            # 先应答再处理 (网页端探测超时只有 1.2s)
            self._json(200, {"ok": True, "message": "已交给桌面端打开"})
            bus.open_requested.emit({"url": url, "name": body.get("name") or "", "token": body.get("token") or "",
                                     "fid": str(body.get("fid") or "")})
        elif path == "/api/control":
            cmd = body.get("command") or body.get("action") or ""
            self._json(200, {"ok": True})
            bus.control.emit(str(cmd))
        elif path == "/api/show":
            self._json(200, {"ok": True})
            bus.show_main.emit()
        else:
            self._json(404, {"ok": False})


_server = None


def start() -> bool:
    """端口被占 → False (通常说明已有实例在运行)"""
    global _server
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), Handler)
    except OSError:
        return False
    _server.daemon_threads = True
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return True


def forward(path: str, body: dict | None = None) -> bool:
    """二次启动时, 把请求转交给已在运行的实例"""
    try:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{BRIDGE_PORT}{path}", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3).read()
        return True
    except Exception:
        return False
