"""离屏 UI 冒烟: 真的 new 出 MainWindow / 系统维护页 / 计时器滚轮

动机: 3.6.0 事故是 `FIF.SHIELD` 这种"只会在运行时炸"的问题 (qfluentwidgets 1.11.3 没有该图标),
静态检查看不出来, 用户看到的现象是双击图标毫无反应。这里用 offscreen 平台真正构造界面,
让这类错误在 CI/本地测试里就暴露。

需要 PyQt5 + PyQt-Fluent-Widgets (与 desktop/qt/build_bundle.sh 钉的版本一致);
没装就 skip, 不会拖累纯逻辑测试。

    QT_QPA_PLATFORM=offscreen python -m pytest desktop/qt/tests/test_ui_offscreen.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
QT_DIR = os.path.dirname(HERE)
sys.path.insert(0, QT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LOCALAPPDATA", "/tmp/fla-ui-test-local")

pytest.importorskip("PyQt5.QtWidgets", reason="需要 PyQt5")
pytest.importorskip("qfluentwidgets", reason="需要 PyQt-Fluent-Widgets")

from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setQuitOnLastWindowClosed(False)
    yield a


@pytest.fixture(scope="module")
def main_window(app):
    from fla_qt.stage import Stage
    from fla_qt.window import MainWindow
    st = Stage(lambda: None)
    win = MainWindow(st, True)
    yield win
    try:
        win.hide()
        st.timer.hide()
    except Exception:
        pass


def test_main_window_builds(main_window):
    """构造过程不能抛异常, 且系统维护页必须真的建出来 (不能悄悄降级成 None)"""
    win = main_window
    assert win.system is not None, "SystemPage 构造失败, 已在 MainWindow 里被兜底吞掉了"
    for page in (win.home, win.experiments, win.settings, win.system):
        win.switchTo(page)


def test_system_page_refresh_is_safe(main_window):
    page = main_window.system
    page._check_admin()
    page.refresh_shadow()
    page._log("测试")
    page._done(True, "ok")
    page._done(False, "fail")


def test_timer_wheel_roundtrip(main_window):
    tm = main_window.stage.timer
    tm.popup()
    tm._toggle_wheel()
    assert tm.wheelBox.isVisible()
    w = tm.wheel
    w.setValue(90)
    assert w.value() == 90
    w.step("m", 1)
    assert w.value() == 150
    w.step("h", 1)
    assert w.value() == 3750
    w.step("s", -1)
    assert w.value() == 3740
    # 上下限
    w.setValue(0)
    w.step("s", -1)
    assert w.value() == 0
    w.setValue(86399)
    w.step("h", 1)
    assert w.value() == 86399

    tm._preset(600)
    tm._show()
    assert tm.label.text() == "10:00"
    tm.toggle()
    assert tm.running and w._lock, "倒计时运行时必须锁住滚轮"
    tm.toggle()
    assert not w._lock
    tm.reset()


def test_timer_wheel_paints(main_window):
    tm = main_window.stage.timer
    tm.wheel.resize(300, 210)
    tm.wheel.repaint()
    tm.repaint()


def test_native_classroom_and_graph(app):
    """课堂工具必须由 Qt 本地窗口承载；函数图画布可绘制并计算根/交点。"""
    from PyQt5.QtGui import QImage
    from fla_qt.classroom import GraphCanvas, parse_plot
    from fla_qt.window import open_classroom

    window = open_classroom("graph")
    app.processEvents()
    assert window.tabs.count() == 6
    assert window.isVisible()
    assert window.tabs.tabText(5) == "函数图像"
    graph = window.graph
    graph.rows[0][1].setText("y=a*sin(x)")
    app.processEvents()
    assert graph.sliders.count() == 1, "表达式参数应自动生成滑块"
    for text, kind in (
        ("y=sin(x)", "explicit"),
        ("x^2+y^2=25", "implicit"),
        ("y<=x^2", "explicit"),
        ("r=1+cos(theta)", "polar"),
        ("(cos(t),sin(2t))", "parametric"),
        ("(2,3)", "point"),
    ):
        assert parse_plot(text).kind == kind
    with pytest.raises((ValueError, SyntaxError)):
        parse_plot("__import__('os').system('whoami')")

    canvas = GraphCanvas()
    canvas.resize(640, 480)
    canvas.setExpressions([
        ("y=x^2-4", True), ("y=2x", True), ("x^2+y^2=25", True),
        ("y<=x^2", True), ("r=1+cos(theta)", True),
        ("(cos(t),sin(2t))", True), ("(2,3)", True),
    ])
    image = QImage(canvas.size(), QImage.Format_ARGB32)
    canvas.render(image)
    assert not image.isNull()
    assert any(label == "根" for _, _, label, _ in canvas._analysis)
    assert any(label == "交点" for _, _, label, _ in canvas._analysis)
    window.close()
