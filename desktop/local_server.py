"""FLA Desktop - 本地驻留守护进程与网页调用桥接服务 (v1.28)
监听 127.0.0.1:8307，打通网页端与桌面端：
1. 网站上一键调用：网页端发起 POST http://127.0.0.1:8307/api/open 直接调起本地 PowerPoint / WPS
2. 自定义协议关联：自动注册 Windows 注册表 fla://open 协议 (网页在未开启桌面端时可一键拉起客户端)
3. 自动下载并缓存课件文件，智能识别系统默认办公套件，并挂载希沃拦截器与 FLA 悬浮栏
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .overlay_window import overlay
from .ppt_controller import ppt_controller
from .seewo_interceptor import seewo_interceptor

logger = logging.getLogger("fla.daemon")

LOCAL_PORT = 8307
CACHE_DIR = Path.home() / ".fla" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 抑制常规日志，避免刷屏
        pass

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 允许所有本地浏览器跨域调用 (CORS)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            player = ppt_controller.detect_default_player()
            self._send_json(200, {
                "ok": True,
                "version": "1.28.0",
                "service": "fla-desktop-bridge",
                "default_player": player,
                "is_playing": ppt_controller.is_playing,
                "current_page": ppt_controller.current_page,
                "total_pages": ppt_controller.total_pages,
            })
        else:
            self._send_json(404, {"ok": False, "error": "Not Found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/open":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                req = json.loads(body.decode("utf-8")) if body else {}

                url = req.get("url")
                name = req.get("name") or "presentation.pptx"
                token = req.get("token") or ""

                if not url:
                    self._send_json(400, {"ok": False, "error": "缺少课件下载或直链 URL"})
                    return

                # 异步下载并打开课件
                threading.Thread(target=open_file_task, args=(url, name, token), daemon=True).start()
                self._send_json(200, {
                    "ok": True,
                    "msg": f"正在调起本地系统默认办公软件打开 {name}…",
                    "player": ppt_controller.detect_default_player(),
                })
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})

        elif parsed.path == "/api/action":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                req = json.loads(body.decode("utf-8")) if body else {}
                act = req.get("action")

                if act == "next":
                    ppt_controller.next_step()
                elif act == "prev":
                    ppt_controller.prev_step()
                elif act == "goto":
                    page = int(req.get("page", 1))
                    ppt_controller.goto_slide(page)
                elif act == "black":
                    ppt_controller.toggle_black_screen()
                elif act == "exit":
                    ppt_controller.exit_presentation()

                self._send_json(200, {"ok": True, "action": act})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
        else:
            self._send_json(404, {"ok": False, "error": "Not Found"})


def open_file_task(url: str, filename: str, token: str = ""):
    """下载并调起本地应用打开"""
    try:
        ext = os.path.splitext(filename)[1] or ".pptx"
        local_path = CACHE_DIR / f"presentation_{int(time.time())}{ext}"

        # 补全 headers
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("User-Agent", "FLA-Desktop/1.28")

        with urllib.request.urlopen(req, timeout=30) as resp, open(local_path, "wb") as f:
            shutil.copyfileobj(resp, f)

        logger.info(f"课件已下载缓存至: {local_path}")

        # 1. 启动希沃拦截器 (抑制希沃白板5自动生成的多余工具栏)
        seewo_interceptor.start()

        # 2. 调起系统默认播放器 (PowerPoint / WPS)
        success = ppt_controller.open_presentation(str(local_path), auto_slideshow=True)
        if success:
            # 3. 挂接 FLA 专属悬浮工具栏 (含投屏 + 水印 + 画布随PPT页码联动)
            overlay.ppt_ctrl = ppt_controller
            overlay.start_overlay()
    except Exception as e:
        logger.error(f"打开课件失败: {e}")


def register_windows_protocol():
    """在 Windows 注册表注册 fla:// 协议 (便于从网页端超链接直接拉起客户端)"""
    if platform.system().lower() != "windows":
        return
    try:
        import sys
        import winreg

        exe_path = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])
        cmd = f'"{exe_path}" "%1"'

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\fla") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:FLA Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\fla\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd)

        logger.info("已成功向系统注册 fla:// 协议关联")
    except Exception as e:
        logger.debug(f"注册协议关联跳过: {e}")


def start_server_in_background(port: int = LOCAL_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), BridgeHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"FLA 桌面桥接服务已在后台启动: http://127.0.0.1:{port}")
    register_windows_protocol()
    return server


def start_server(port: int = LOCAL_PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), BridgeHandler)
    logger.info(f"FLA 桌面桥接服务已启动: http://127.0.0.1:{port}")
    register_windows_protocol()
    server.serve_forever()
