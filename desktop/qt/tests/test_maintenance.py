"""希沃消杀 / 影子系统 / 单实例 的可重复自检 (不需要 Windows, 不需要 PyQt)

    python3 -m pytest desktop/qt/tests/test_maintenance.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fla_qt import purge, shadow, single  # noqa: E402


# ------------------------------------------------------------------ 消杀
def test_report_summary_and_json():
    r = purge.Report()
    assert r.summary() == "未发现希沃残留"
    r.steps.append(purge.Step("proc", "EasiNote.exe", "进程 EasiNote.exe"))
    r.steps.append(purge.Step("reg", r"HKLM\SOFTWARE\Seewo", "注册表"))
    assert "进程 1" in r.summary() and "注册表 1" in r.summary()
    data = r.to_json()
    assert "EasiNote.exe" in data and "Seewo" in data


def test_purge_backup_and_restore_roundtrip(tmp_path):
    src = tmp_path / "Seewo"
    (src / "sub").mkdir(parents=True)
    (src / "a.dll").write_text("x" * 50)
    (src / "sub" / "b.sys").write_text("y" * 50)

    p = purge.SeewoPurge(data_dir=str(tmp_path / "data"))
    p.report.steps = [purge.Step("dir", str(src), "安装目录 Seewo")]
    r = p.purge(backup=True, force=True)

    assert r.steps[0].ok is True
    assert not src.exists()
    assert os.path.isdir(os.path.join(r.backup_dir, "files", "Seewo"))

    ok, bad = purge.restore(r.backup_dir)
    assert (ok, bad) == (1, 0)
    assert (src / "sub" / "b.sys").exists()


def test_purge_without_backup_deletes(tmp_path):
    f = tmp_path / "leftover.exe"
    f.write_text("x")
    p = purge.SeewoPurge(data_dir=str(tmp_path / "data"))
    p.report.steps = [purge.Step("file", str(f), "残留文件")]
    r = p.purge(backup=False, force=True)
    assert r.steps[0].ok is True and not f.exists()


def test_purge_missing_path_is_ok(tmp_path):
    p = purge.SeewoPurge(data_dir=str(tmp_path / "data"))
    p.report.steps = [purge.Step("file", str(tmp_path / "nope.dll"), "已不存在")]
    r = p.purge(backup=True, force=True)
    assert r.steps[0].ok is True and "不存在" in r.steps[0].msg


def test_non_windows_scan_refuses_without_touching_anything():
    p = purge.SeewoPurge(data_dir="/tmp/fla-purge-test")
    r = p.scan()
    if sys.platform.startswith("win"):
        pytest.skip("在 Windows 上会真的扫描本机")
    assert r.steps == []
    assert any("不是 Windows" in n for n in r.notes)
    r2 = p.purge(backup=True)
    assert r2.steps == []


# ------------------------------------------------------------------ 影子系统
def test_shadow_refuses_on_non_windows():
    if sys.platform.startswith("win"):
        pytest.skip("在 Windows 上会真的探测 UWF")
    s = shadow.ShadowSystem(data_dir="/tmp/fla-shadow-test")
    st = s.detect()
    assert st.is_windows is False
    assert st.method == ""
    assert st.reasons
    ok, msg, acts = s.enable(skip_clean=True, reboot=False)
    assert ok is False and "Windows" in msg and acts == []


def test_shadow_uwf_flow_and_rollback(monkeypatch):
    calls = []

    def fake_run(args, timeout=40):
        calls.append(list(args))
        if "get-config" in args:
            return 0, "Filter state: Disabled"
        if "--fail-volume" in os.environ.get("FLA_TEST_FLAGS", ""):
            if "protect" in args:
                return 1, "volume in use"
        return 0, "ok"

    monkeypatch.setattr(shadow, "_run", fake_run)
    monkeypatch.setattr(shadow, "_is_admin", lambda: True)
    monkeypatch.setattr(shadow, "IS_WIN", True)
    monkeypatch.setattr(shadow, "_read_reg", lambda *a, **k: {
        "EditionID": "Enterprise", "ProductName": "Windows 10 Enterprise", "CurrentBuild": "19045"}[a[1]])
    real_exists = os.path.exists
    monkeypatch.setattr(shadow.os.path, "exists",
                        lambda p: True if "uwfmgr" in str(p).lower() else real_exists(p))

    s = shadow.ShadowSystem(data_dir="/tmp/fla-shadow-test", log=lambda _t: None)
    ok, msg, acts = s.enable(skip_clean=True, reboot=False, overlay_mb=2048)
    assert ok is True, msg
    flat = [" ".join(c) for c in calls]
    assert any("overlay set-size 2048" in c for c in flat)
    assert any("volume protect C:" in c and "C::" not in c for c in flat)

    # 保护系统盘失败时必须回滚已开启的筛选器
    monkeypatch.setenv("FLA_TEST_FLAGS", "--fail-volume")
    calls.clear()
    ok, msg, acts = s.enable(skip_clean=True, reboot=False)
    assert ok is False
    assert any("回滚" in a.name for a in acts)
    assert any("filter disable" in " ".join(c) for c in calls)


# ------------------------------------------------------------------ 单实例
def test_single_instance_on_posix_passes_through():
    if sys.platform.startswith("win"):
        pytest.skip("Windows 上会创建真实互斥体")
    s = single.SingleInstance()
    assert s.acquire() is True
    assert s.is_primary is True
    assert s.wake_existing(["fla://file/1"]) is False     # 非 Windows 不投递
    s.release()


def test_launcher_and_single_names_match():
    """C 启动器与 Python 侧必须用同一个互斥体/事件名, 否则连点仍会开多个"""
    c_path = os.path.join(os.path.dirname(HERE), "launcher.c")
    with open(c_path, encoding="utf-8") as f:
        c = f.read()
    assert "FLA.SingleInstance.v1" in c and "FLA.WakeEvent.v1" in c
    assert single.MUTEX_NAME.endswith("FLA.SingleInstance.v1")
    assert single.EVENT_NAME.endswith("FLA.WakeEvent.v1")
