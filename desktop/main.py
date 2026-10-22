"""FLA Desktop - 桌面客户端主程序 (v1.28)
学校课堂智慧助手：
- 现代轻量高效 GUI 界面 (支持 Edge WebView2 / Fluent UI，极简专业，无浮夸冗余)
- 自动拦截并隐藏希沃白板5注入的 PPT 悬浮工具栏
- 替代为 FLA 极简专业工具栏（含投屏功能 + 右下角 FLA 水印）
- 智能识别系统默认办公套件 (Microsoft Office PowerPoint 或 WPS 演示)
- 毫秒级 COM 挂接 PPT 放映页码，实现板书画布随 PPT 翻页实时移动
- 开启 127.0.0.1:8307 桥接服务，支持在 FLA 网页端一键调用本地播放
- 支持手机扫码投屏与双向实时遥控
- 内置版本自动检测与原地静默更新 (无需每次从官网重新下载)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

from .auto_updater import CURRENT_VERSION, AutoUpdaterThread, check_version
from .local_server import open_file_task, register_windows_protocol, start_server_in_background
from .overlay_window import overlay
from .ppt_controller import ppt_controller
from .seewo_interceptor import seewo_interceptor
from .ui import ModernDesktopUI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("fla.desktop")


def handle_protocol_arg(arg: str):
    """处理类似 fla://open?url=...&name=... 命令行参数"""
    try:
        parsed = urlparse(arg)
        if parsed.scheme == "fla":
            qs = parse_qs(parsed.query)
            url = qs.get("url", [""])[0]
            name = qs.get("name", ["presentation.pptx"])[0]
            token = qs.get("token", [""])[0]
            if url:
                logger.info(f"收到协议调用请求: {name}")
                threading.Thread(target=open_file_task, args=(url, name, token), daemon=True).start()
    except Exception as e:
        logger.error(f"解析协议参数异常: {e}")


def main():
    logger.info("=" * 60)
    logger.info(f"FLA Desktop 智慧课堂客户端 (v{CURRENT_VERSION})")
    logger.info("自动检测并替代希沃白板5工具栏 · 手机投屏 · 画布随PPT页码联动 · 原地自更新")
    logger.info("=" * 60)

    # 1. 注册 Windows 协议
    register_windows_protocol()

    # 2. 启动本地 127.0.0.1:8307 桥接服务
    start_server_in_background(8307)

    # 3. 启动希沃白板5智能拦截守护线程
    seewo_interceptor.start()

    # 4. 后台自动检测版本更新 (若服务端有新版本可原地静默更新)
    AutoUpdaterThread().start()

    # 5. 处理命令行启动参数 (例如双击课件或协议打开)
    headless = "--headless" in sys.argv or "--daemon" in sys.argv
    for arg in sys.argv[1:]:
        if arg.startswith("fla://"):
            handle_protocol_arg(arg)
        elif os.path.isfile(arg):
            # 直接传入了本地文件路径
            ppt_controller.open_presentation(arg, auto_slideshow=True)
            overlay.ppt_ctrl = ppt_controller
            overlay.start_overlay()

    # 6. 启动现代化桌面图形界面
    if not headless:
        try:
            ui = ModernDesktopUI()
            ui.run()
        except Exception as e:
            logger.warning(f"UI 启动异常，保持后台运行: {e}")
            while True:
                time.sleep(1)
    else:
        logger.info("FLA Desktop 运行于无头守护进程模式")
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
