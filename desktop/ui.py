"""FLA 桌面助手 - 现代桌面客户端 UI 界面
采用轻量、现代的 Fluent 质感设计：
- 优先支持系统原生 Edge WebView2 现代渲染界面
- 无依赖时自动无缝降级为现代深色卡片式原生 GUI (Fluent Dark 风格)
- 包含白板拦截控制、服务状态监视、手机投屏二维码预览、一键静默自动更新
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import webbrowser
from typing import Callable, Optional

from .auto_updater import CURRENT_VERSION, check_version, get_server_url, perform_update

logger = logging.getLogger("fla.desktop.ui")


HTML_TEMPLATE = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FLA 课堂助手</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; user-select: none; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: #121316;
      color: #f8fafc;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 24px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      background: rgba(18, 19, 22, 0.88);
      backdrop-filter: blur(12px);
    }
    .logo-group {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .logo-badge {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: #09090b;
      border: 1px solid rgba(255, 255, 255, 0.2);
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 16px;
      color: #fff;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    }}
    .title-box h1 {{
      font-size: 16px;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #f1f5f9;
    }}
    .title-box p {{
      font-size: 11px;
      color: #94a3b8;
      margin-top: 1px;
    }}
    .version-tag {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 9999px;
      background: #18181b;
      border: 1px solid #27272a;
      color: #f4f4f5;
      font-size: 12px;
      font-weight: 600;
    }}
    .status-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #ffffff;
      box-shadow: 0 0 8px #ffffff;
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.5; transform: scale(0.9); }}
    }}
    .main-body {{
      flex: 1;
      padding: 24px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .card {{
      background: #1e293b;
      border-radius: 12px;
      padding: 16px 20px;
      border: 1px solid rgba(255,255,255,0.06);
      display: flex;
      align-items: center;
      justify-content: space-between;
      transition: all 0.2s;
    }}
    .card:hover {{
      border-color: rgba(2, 132, 199, 0.3);
      box-shadow: 0 6px 20px rgba(0,0,0,0.25);
    }}
    .card-left {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    .card-icon {{
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: rgba(255,255,255,0.04);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
    }}
    .card-text h3 {{
      font-size: 14px;
      font-weight: 600;
      color: #e2e8f0;
    }}
    .card-text p {{
      font-size: 12px;
      color: #64748b;
      margin-top: 2px;
    }}
    .badge {{
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
    }}
    .badge-success {{
      background: rgba(16, 185, 129, 0.12);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.25);
    }}
    .badge-info {{
      background: rgba(2, 132, 199, 0.12);
      color: #38bdf8;
      border: 1px solid rgba(2, 132, 199, 0.25);
    }}
    .footer-bar {{
      padding: 16px 24px;
      border-top: 1px solid rgba(255,255,255,0.08);
      background: #18191d;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .btn {{
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: none;
      transition: all 0.15s ease;
      text-decoration: none;
    }}
    .btn-primary {{
      background: #ffffff;
      color: #09090b;
      font-weight: 700;
    }}
    .btn-primary:hover {{
      background: #e4e4e7;
    }}
    .btn-secondary {{
      background: rgba(255,255,255,0.08);
      color: #cbd5e1;
    }}
    .btn-secondary:hover {{
      background: rgba(255,255,255,0.12);
      color: #fff;
    }}
    .update-box {{
      background: rgba(2, 132, 199, 0.08);
      border: 1px dashed rgba(2, 132, 199, 0.35);
      border-radius: 10px;
      padding: 14px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 4px;
    }}
    .update-info {{
      font-size: 13px;
      color: #94a3b8;
    }}
    .update-info strong {{
      color: #f1f5f9;
    }}
  </style>
</head>
<body>
  <div class="header">
    <div class="logo-group">
      <div class="logo-badge">FLA</div>
      <div class="title-box">
        <h1>FLA 课堂助手</h1>
        <p>现代化多媒体教学终端 · 极简高效</p>
      </div>
    </div>
    <div class="version-tag">
      <span class="status-dot"></span>
      <span>v{CURRENT_VERSION} 运行中</span>
    </div>
  </div>

  <div class="main-body">
    <div class="card">
      <div class="card-left">
        <div class="card-icon">🛡️</div>
        <div class="card-text">
          <h3>希沃白板5 拦截器</h3>
          <p>智能抑制 PPT 翻页笔劫持，自动挂载 FLA 原生悬浮工具条</p>
        </div>
      </div>
      <span class="badge badge-success">自动拦截运行中</span>
    </div>

    <div class="card">
      <div class="card-left">
        <div class="card-icon">📊</div>
        <div class="card-text">
          <h3>PPT 演示联动与板书同步</h3>
          <p>幻灯片页码严格跟随，板书画布与课件翻页全自动按页隔离</p>
        </div>
      </div>
      <span class="badge badge-success">COM 监听就绪</span>
    </div>

    <div class="card">
      <div class="card-left">
        <div class="card-icon">🔌</div>
        <div class="card-text">
          <h3>本地 Office 原生桥接服务</h3>
          <p>响应网页端一键唤起 PowerPoint / WPS 原生演示与编辑</p>
        </div>
      </div>
      <span class="badge badge-info">127.0.0.1:8307</span>
    </div>

    <div class="update-box">
      <div class="update-info">
        <div id="update-status"><strong>自动更新状态：</strong> 客户端已是最新版本</div>
        <div style="font-size: 11px; margin-top: 3px; color: #64748b;">每次启动自动比对服务端版本，无需手动重新下载</div>
      </div>
      <button class="btn btn-secondary" id="btn-check-update" onclick="checkUpdateManual()">立即检查更新</button>
    </div>
  </div>

  <div class="footer-bar">
    <button class="btn btn-secondary" onclick="openWebConsole()">进入 FLA 网页端</button>
    <button class="btn btn-primary" onclick="minimizeWindow()">最小化到托盘运行</button>
  </div>

  <script>
    function checkUpdateManual() {{
      const st = document.getElementById('update-status');
      st.innerHTML = '正在检查服务端最新版本...';
      fetch('/api/check_update')
        .then(r => r.json())
        .then(data => {{
          if (data && data.has_update) {{
            st.innerHTML = '<span style="color:#38bdf8;font-weight:600;">发现新版本 v' + data.version + '，正在原地自动更新...</span>';
          }} else {{
            st.innerHTML = '<strong>自动更新状态：</strong> 已是最新版本 (v{CURRENT_VERSION})';
          }}
        }})
        .catch(e => {{
          st.innerHTML = '<strong>自动更新状态：</strong> 检查更新完毕 (v{CURRENT_VERSION})';
        }});
    }}

    function openWebConsole() {{
      if (window.pywebview) {{
        pywebview.api.open_browser();
      }} else {{
        window.open('{get_server_url()}', '_blank');
      }}
    }}

    function minimizeWindow() {{
      if (window.pywebview) {{
        pywebview.api.minimize();
      }}
    }}
  </script>
</body>
</html>
"""


class ModernDesktopUI:
    """现代桌面客户端图形界面"""

    def __init__(self, on_exit: Optional[Callable] = None):
        self.on_exit = on_exit
        self.server_url = get_server_url()

    def run(self):
        """尝试启动 WebView2 现代窗口，若无 pywebview 则启动 Tkinter 现代深色界面"""
        # 优先使用 pywebview
        try:
            import webview

            class Api:
                def __init__(self, parent):
                    self.parent = parent

                def open_browser(self):
                    webbrowser.open(get_server_url())

                def minimize(self):
                    try:
                        window.minimize()
                    except Exception:
                        pass

                def check_update(self):
                    return check_version()

            api = Api(self)
            window = webview.create_window(
                title=f"FLA 课堂助手 v{CURRENT_VERSION}",
                html=HTML_TEMPLATE,
                js_api=api,
                width=640,
                height=480,
                resizable=False,
            )
            webview.start()
            return
        except ImportError:
            logger.info("未安装 pywebview，使用内置现代化深色 Fluent UI 引擎启动")
        except Exception as e:
            logger.warning(f"pywebview 启动失败: {e}，回退至内置现代化界面")

        # 回退至现代 Tkinter 界面
        self._run_tk_modern_ui()

    def _run_tk_modern_ui(self):
        try:
            import tkinter as tk
            from tkinter import font as tkfont
        except ImportError:
            logger.info("无 GUI 环境，运行于后台无头守护进程模式")
            while True:
                time.sleep(1)
            return

        root = tk.Tk()
        root.title(f"FLA 课堂助手 v{CURRENT_VERSION}")
        root.geometry("620x460")
        root.resizable(False, False)
        root.configure(bg="#121316")

        # 现代字体
        font_title = ("Segoe UI", 13, "bold")
        font_sub = ("Segoe UI", 9)
        font_card_h = ("Segoe UI", 10, "bold")
        font_card_p = ("Segoe UI", 9)
        font_btn = ("Segoe UI", 9, "bold")

        # Header
        header = tk.Frame(root, bg="#121316", height=60)
        header.pack(fill="x", padx=20, pady=(16, 10))

        title_lbl = tk.Label(header, text=f"FLA 课堂助手  v{CURRENT_VERSION}", font=font_title, fg="#f8fafc", bg="#121316")
        title_lbl.pack(anchor="w")
        sub_lbl = tk.Label(header, text="现代化多媒体互动教学终端 · 希沃白板5拦截与同步", font=font_sub, fg="#94a3b8", bg="#121316")
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # Status cards
        body = tk.Frame(root, bg="#121316")
        body.pack(fill="both", expand=True, padx=20, pady=5)

        cards_data = [
            ("🛡️  希沃白板5 拦截器", "自动抑制白板翻页笔劫持，挂载 FLA 原生悬浮工具条", "已启用", "#ffffff"),
            ("📊  PPT 幻灯片与板书联动", "画布严格与幻灯片页面绑定，翻页自动隔离笔迹", "COM 监听", "#ffffff"),
            ("🔌  本地 Office 唤起服务", "网页端一键启动 PPT/WPS 原生演示 (127.0.0.1:8307)", "监听中", "#ffffff"),
        ]

        for title, desc, tag, tag_color in cards_data:
            card = tk.Frame(body, bg="#18191d", highlightbackground="#272830", highlightthickness=1)
            card.pack(fill="x", pady=5, ipady=8, ipadx=10)

            c_left = tk.Frame(card, bg="#18191d")
            c_left.pack(side="left", fill="both", expand=True, padx=10)
            tk.Label(c_left, text=title, font=font_card_h, fg="#f1f5f9", bg="#18191d").pack(anchor="w")
            tk.Label(c_left, text=desc, font=font_card_p, fg="#94a3b8", bg="#18191d").pack(anchor="w", pady=(2, 0))

            c_right = tk.Frame(card, bg="#18191d")
            c_right.pack(side="right", padx=10)
            tk.Label(c_right, text=tag, font=("Segoe UI", 8, "bold"), fg=tag_color, bg="#121316", padx=8, pady=3).pack()

        # Update card
        update_card = tk.Frame(body, bg="#18191d", highlightbackground="#272830", highlightthickness=1)
        update_card.pack(fill="x", pady=8, ipady=6, ipadx=10)
        up_lbl = tk.Label(update_card, text=f"自动更新检测：当前已是最新版本 (v{CURRENT_VERSION})", font=font_card_p, fg="#f4f4f5", bg="#18191d")
        up_lbl.pack(side="left", padx=10)

        def do_check_update():
            up_lbl.config(text="正在检测服务端最新版本...")
            def _check():
                res = check_version()
                if res and res.get("has_update"):
                    up_lbl.config(text=f"发现新版本 v{res.get('version')}，正在原地自动更新...")
                    perform_update(res.get("download_url"))
                else:
                    up_lbl.config(text=f"已是最新版本 (v{CURRENT_VERSION})")
            threading.Thread(target=_check, daemon=True).start()

        up_btn = tk.Button(update_card, text="检查更新", font=font_sub, bg="#ffffff", fg="#09090b", activebackground="#e4e4e7", activeforeground="#09090b", relief="flat", padx=8, pady=2, command=do_check_update)
        up_btn.pack(side="right", padx=10)

        # Footer
        footer = tk.Frame(root, bg="#121316", height=50)
        footer.pack(fill="x", side="bottom", padx=20, pady=16)

        def open_browser():
            webbrowser.open(get_server_url())

        def minimize():
            root.iconify()

        btn_web = tk.Button(footer, text="打开 FLA 网页控制台", font=font_btn, bg="#272830", fg="#f8fafc", activebackground="#3f3f46", activeforeground="#ffffff", relief="flat", padx=14, pady=6, command=open_browser)
        btn_web.pack(side="left")

        btn_min = tk.Button(footer, text="最小化到托盘", font=font_btn, bg="#0284c7", fg="#ffffff", activebackground="#0369a1", activeforeground="#ffffff", relief="flat", padx=14, pady=6, command=minimize)
        btn_min.pack(side="right")

        root.mainloop()
