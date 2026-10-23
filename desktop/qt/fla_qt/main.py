"""FLA 桌面端入口"""
from __future__ import annotations

import os
import sys


def _resource(name: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (getattr(sys, "_MEIPASS", ""), os.path.join(here, ".."), os.path.join(here, "..", "..", "assets")):
        p = os.path.join(base, name)
        if base and os.path.exists(p):
            return p
    return ""


def _install_crash_log():
    """pythonw 无控制台: 未捕获异常写日志并弹窗, 避免双击没反应"""
    import traceback

    def hook(t, v, tb):
        text = "".join(traceback.format_exception(t, v, tb))
        try:
            from .core import data_dir
            with open(os.path.join(data_dir(), "error.log"), "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, text[-1500:], "FLA 出错了 (已记录到 error.log)", 0x10)
            except Exception:
                pass
    sys.excepthook = hook


def main() -> int:
    _install_crash_log()
    from . import bridge, core, single

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    selftest = "--selftest" in sys.argv
    job = core.parse_protocol(args[0]) if args else None

    # 严格单实例: 命名互斥体 (双击 N 次也只留一个进程), 后来者唤醒前一个后立即退出
    si = single.SingleInstance()
    if not selftest and not si.acquire():
        if si.wake_existing(sys.argv[1:]):
            return 0
        # 兜底: 可能是没带单实例的老版本在跑, 退回到端口转发
        if bridge.forward("/api/open", job) if job else bridge.forward("/api/show"):
            return 0

    bridge_ok = selftest or bridge.start()
    if not bridge_ok:
        # 端口已被占用: 多半是已有 FLA 在运行 → 把任务转交过去后退出
        if bridge.forward("/api/open", job) if job else bridge.forward("/api/show"):
            return 0

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import QApplication
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(core.APP_NAME)
    ico = _resource("fla.ico")
    if ico:
        app.setWindowIcon(QIcon(ico))

    from qfluentwidgets import Theme, setTheme, setThemeColor
    setTheme(Theme.LIGHT)
    setThemeColor("#111111")

    from .stage import Stage
    from .window import MainWindow

    core.cleanup_old()
    core.register_protocol()
    core.SeewoSuppressor().start()

    win = None

    def show_main():
        win.bring_up()

    stage = Stage(show_main)
    win = MainWindow(stage, bridge_ok)
    bridge.bus.open_requested.connect(win.open_file)
    bridge.bus.control.connect(stage.handle)
    bridge.bus.show_main.connect(win.bring_up)
    win.show()

    def _on_wake(argv):
        """后来实例唤起时: 显示主窗 + 有课件参数就直接打开"""
        try:
            win.bring_up()
        except Exception:
            pass
        try:
            p = core.parse_protocol(argv[0]) if argv else None
            if p:
                win.open_file(p)
        except Exception:
            pass

    si.listen(_on_wake)
    if core.cfg.dock_on_start:
        stage.start()
    if job:
        win.open_file(job)

    if selftest:
        from PyQt5.QtCore import QTimer
        def probe():
            stage.start()
            for c in ("pen", "marker", "laser", "eraser", "board", "board", "timer", "palette",
                      "palette", "collapse", "dock_toggle", "mouse", "end"):
                stage.handle(c)
                app.processEvents()
            win.switchTo(win.library)
            win.switchTo(win.settings)
            app.processEvents()
            print("SELFTEST OK", core.VERSION, flush=True)
            app.exit(0)
        QTimer.singleShot(1500, probe)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
