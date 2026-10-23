"""FLA 桌面端 —— 重启还原 (影子系统) 引擎 (v1.37)

要做的是 "类似冰点还原/影子系统" 的效果: 开机后对系统盘的一切改动 (装软件、改设置、学生乱点),
重启全部消失, 回到开启时的状态。这必须是 **底层、系统级** 的实现, 而不是靠监控/回滚脚本假装的:

    方案 A (首选)  Windows 统一写入筛选器 UWF (Unified Write Filter)
        · 微软自带的写保护驱动, 所有写入都落到内存/差异盘 overlay, 重启即丢弃
        · 限制: 只有 Enterprise / Education / IoT Enterprise / Server 版本带该组件, 家庭版/专业版没有

    方案 B (备选)  VHD 差分盘 + 独立 BCD 引导项 (真·冰点做法)
        · 前提: 系统本身已经跑在 VHD/VHDX 上 (或由管理员准备好父盘),
          基于父盘建子盘, 子盘的所有改动落在子文件里, 重启引导父盘即还原
        · 限制: 普通物理机 C: 无法"事后"变成差分盘; 本工具不会假装能做到

    方案 C  **明确拒绝**
        · 两个方案都不满足时, 直接告诉用户缺什么 (例如 "需要 Windows 企业版"), 不做半吊子实现

开启前默认先跑一遍希沃全面消杀 (可强制跳过)。所有命令都带超时与返回码检查, 失败即回滚已做的步骤。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .purge import SeewoPurge, Step as PurgeStep

IS_WIN = platform.system().lower() == "windows"
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

def _uwf_candidates() -> List[str]:
    """uwfmgr.exe 的位置: 32 位 Python 在 64 位系统上要经 Sysnative 才能访问真正的 System32"""
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return [os.path.join(root, "System32", "uwfmgr.exe"),
            os.path.join(root, "Sysnative", "uwfmgr.exe")]


def _uwf_exe() -> str:
    for p in _uwf_candidates():
        if os.path.exists(p):
            return p
    return _uwf_candidates()[0]


UWF_EXE = _uwf_exe()
SHADOW_DIR = r"C:\FLA\shadow"
BCD_DESC = "FLA 影子系统 (重启还原)"

SUPPORTED_EDITIONS = ("enterprise", "education", "iotenterprise", "server", "iot")
UNSUPPORTED_EDITIONS = ("professional", "core", "home", "corecountryspecific", "coresinglelanguage")


@dataclass
class Action:
    name: str
    cmd: str = ""
    ok: Optional[bool] = None
    out: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "cmd": self.cmd, "ok": self.ok, "out": self.out}


@dataclass
class Status:
    is_windows: bool = IS_WIN
    admin: bool = False
    edition: str = ""
    product: str = ""
    build: str = ""
    uwf_exe: bool = False
    uwf_available: bool = False
    uwf_enabled: bool = False
    uwf_volume: bool = False
    overlay_mb: int = 0
    vhd_boot: bool = False
    vhd_available: bool = False
    method: str = ""                 # 'uwf' / 'vhd' / '' (不可用)
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.is_windows:
            return "影子系统仅支持 Windows"
        if self.method == "uwf":
            state = "已开启" if self.uwf_enabled else "可用未开启"
            return f"UWF 写保护 · {self.edition} · {state}"
        if self.method == "vhd":
            return "VHD 差分盘 · 系统运行在虚拟硬盘上, 可用"
        return "当前系统不支持重启还原 · " + (self.reasons[0] if self.reasons else "版本/环境不满足")

    def as_dict(self) -> Dict[str, object]:
        return {k: getattr(self, k) for k in (
            "is_windows", "admin", "edition", "product", "build", "uwf_exe", "uwf_available",
            "uwf_enabled", "uwf_volume", "overlay_mb", "vhd_boot", "vhd_available", "method",
            "reasons", "notes")}


def _run(args: Sequence[str], timeout: int = 40) -> Tuple[int, str]:
    try:
        p = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW, errors="ignore")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def _is_admin() -> bool:
    if not IS_WIN:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _read_reg(path: str, name: str, hive: str = "HKLM") -> str:
    if not IS_WIN:
        return ""
    try:
        import winreg
        root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
        with winreg.OpenKey(root, path) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v)
    except Exception:
        return ""


class ShadowSystem:
    """重启还原 (影子系统)。UI 与命令行共用。"""

    def __init__(self, data_dir: Optional[str] = None, log: Callable[[str], None] = print):
        self.data_dir = data_dir or os.path.join(os.path.expanduser("~"), ".fla")
        self.log = log

    # ============================================================ 探测
    def detect(self) -> Status:
        st = Status(is_windows=IS_WIN, admin=_is_admin())
        if not IS_WIN:
            st.reasons.append("当前不是 Windows 系统")
            return st

        st.edition = _read_reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "EditionID").lower()
        st.product = _read_reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName")
        st.build = _read_reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuild")
        st.uwf_exe = any(os.path.exists(p) for p in _uwf_candidates())

        edition_ok = any(e in st.edition for e in SUPPORTED_EDITIONS) or \
                     any(k in st.product.lower() for k in ("enterprise", "education", "server", "iot"))
        st.uwf_available = bool(st.uwf_exe and edition_ok)
        if st.uwf_exe and not edition_ok:
            st.reasons.append(
                f"检测到 uwfmgr.exe 但当前版本是 {st.product or st.edition}: "
                "家庭版/专业版不带 UWF 组件 (UWF 只在 Windows 企业版 / 教育版 / IoT 企业版提供)")
        elif not st.uwf_exe:
            st.reasons.append("系统没有 uwfmgr.exe (需要 Windows 企业版 / 教育版 / IoT 企业版)")

        if st.uwf_available:
            st.uwf_enabled, st.uwf_volume, st.overlay_mb = self._uwf_state()

        # 方案 B: 系统是否本身跑在 VHD 上
        st.vhd_boot = self._boot_from_vhd()
        st.vhd_available = bool(st.vhd_boot and shutil.which("bcdedit") and shutil.which("diskpart"))
        if st.vhd_boot:
            st.notes.append("系统从 VHD/VHDX 启动, 可用差分盘方案")
        else:
            st.notes.append("系统不在虚拟硬盘上运行, 无法事后改造成差分盘 (这是物理机的物理限制)")

        if st.uwf_enabled:
            st.method = "uwf"
        elif st.uwf_available:
            st.method = "uwf"
        elif st.vhd_available:
            st.method = "vhd"
        else:
            st.method = ""
            if st.uwf_enabled:
                st.method = "uwf"
        if not st.uwf_available and not st.vhd_available:
            st.reasons.append(
                "结论: 这台电脑无法用系统底层实现重启还原。可选项: "
                "① 换成 Windows 教育版/企业版 (学校批量授权通常有); "
                "② 若已用 VHD 部署系统, 可用差分盘方案; "
                "③ 请不要用第三方'假影子'软件替代, 它挡不住驱动层写入")
        if not st.admin:
            st.reasons.append("当前不是管理员权限: 开启/关闭影子系统必须用管理员身份运行 FLA")
        return st

    def _uwf_state(self) -> Tuple[bool, bool, int]:
        enabled = protected = False
        size_mb = 0
        rc, out = _run([UWF_EXE, "get-config"], timeout=30)
        if rc != 0:
            rc, out = _run([UWF_EXE, "filter", "get-config"], timeout=30)
        low = out.lower()
        if re.search(r"filter state[^\n]*enabled", low) or "筛选器状态: 已启用" in out:
            enabled = True
        if re.search(r"volume state[^\n]*protected", low) or "受保护" in out:
            protected = True
        m = re.search(r"(?:maximum size|最大大小)[^\n]*?(\d[\d,]*)\s*mb", low)
        if m:
            size_mb = int(m.group(1).replace(",", ""))
        return enabled, protected, size_mb

    def _boot_from_vhd(self) -> bool:
        rc, out = _run(["bcdedit", "/enum", "{current}", "/v"], timeout=20)
        if rc != 0:
            return False
        low = out.lower()
        return "vhd=[" in low or ".vhdx" in low or ".vhd" in low

    # ============================================================ 开启
    def enable(self, method: str = "auto", clean_seewo: bool = True, skip_clean: bool = False,
               overlay_mb: int = 4096, volume: str = "C:", exclusions: Sequence[str] = (),
               reboot: bool = True,
               progress: Optional[Callable[[str], None]] = None) -> Tuple[bool, str, List[Action]]:
        """开启重启还原。返回 (成功, 说明, 动作列表)

        clean_seewo=True 时先做希沃全面消杀; skip_clean=True 表示用户明确强制跳过消杀。
        """
        actions: List[Action] = []

        def say(text: str):
            self.log(text)
            if progress:
                try:
                    progress(text)
                except Exception:
                    pass

        st = self.detect()
        if not st.is_windows:
            return False, "影子系统仅支持 Windows", actions
        if not st.admin:
            return False, "需要管理员权限: 请右键 FLA → 以管理员身份运行", actions

        # ---- 1. 先消杀希沃 (默认动作, 可强制跳过) ----
        if clean_seewo and not skip_clean:
            say("开启前先消杀希沃残留…")
            pg = SeewoPurge(data_dir=self.data_dir, log=lambda s: say(s))
            report = pg.scan()
            if report.steps:
                say(f"发现 {len(report.steps)} 项希沃残留 ({report.summary()}), 开始清理")
                report = pg.purge(backup=True)
                failed = [s for s in report.steps if not s.ok]
                for s in report.steps:
                    actions.append(Action("希沃消杀: " + s.label, "", s.ok, s.msg))
                if failed:
                    return False, (f"希沃消杀有 {len(failed)} 项失败 (多为文件占用)。"
                                   "可重启后再试, 或在弹窗中勾选『强制跳过消杀』继续开启影子系统。"), actions
                say("希沃消杀完成")
            else:
                say("未发现希沃残留, 直接继续")
        elif skip_clean:
            say("已按用户要求强制跳过希沃消杀 (风险: 希沃驱动会随快照一起被保留)")
            actions.append(Action("跳过希沃消杀", "", True, "用户强制跳过"))

        # ---- 2. 选方案 ----
        chosen = method
        if chosen == "auto":
            chosen = st.method
        if chosen not in ("uwf", "vhd"):
            return False, "无法开启: " + ("；".join(st.reasons) or "没有可用的底层方案"), actions
        if chosen == "uwf" and not st.uwf_available:
            return False, "无法开启 UWF: " + "；".join(st.reasons), actions
        if chosen == "vhd" and not st.vhd_available:
            return False, "无法开启差分盘: 系统未运行在 VHD 上, 不能事后转换", actions

        # ---- 3. 执行 ----
        if chosen == "uwf":
            ok, msg = self._enable_uwf(overlay_mb, volume, exclusions, actions, say)
        else:
            ok, msg = self._enable_vhd_diff(actions, say)
        if not ok:
            return False, msg, actions

        if reboot:
            say("申请重启, 让写保护/引导项生效…")
            rc, out = _run([UWF_EXE, "filter", "restart"] if chosen == "uwf" else ["shutdown", "/r", "/t", "8"], timeout=30)
            actions.append(Action("重启生效", "restart", rc == 0, out.strip()[:200]))
            if rc != 0:
                return True, "已配置完成, 但自动重启指令失败, 请手动重启。", actions
            return True, "已开启, 电脑将在几秒后重启; 重启后所有改动都会在下次重启时还原。", actions
        return True, "已配置完成 (未自动重启), 请手动重启生效。", actions

    def _enable_uwf(self, overlay_mb: int, volume: str, exclusions: Sequence[str],
                    actions: List[Action], say: Callable[[str], None]) -> Tuple[bool, str]:
        vol = volume if str(volume).endswith(":") else (str(volume).strip("\\/") + ":")
        steps: List[Tuple[str, List[str]]] = [
            ("切换 overlay 到磁盘模式 (重启后大改动也不会占内存)", [UWF_EXE, "overlay", "set-type", "disk"]),
            (f"设置 overlay 容量 {overlay_mb} MB", [UWF_EXE, "overlay", "set-size", str(int(overlay_mb))]),
            ("开启写入筛选器", [UWF_EXE, "filter", "enable"]),
            (f"保护系统盘 {vol}", [UWF_EXE, "volume", "protect", vol]),
        ]
        done: List[Tuple[str, List[str]]] = []
        for name, cmd in steps:
            say(name.rstrip(":"))
            rc, out = _run(cmd, timeout=60)
            actions.append(Action(name, " ".join(cmd), rc == 0, out.strip()[:300]))
            if rc != 0:
                # 回滚: 已经开了保护就撤掉, 免得半开状态
                self._rollback_uwf(done, actions)
                return False, f"{name} 失败: {out.strip()[:200]}"
            done.append((name, cmd))

        # 排除项: 全限定路径, 必须在保护生效前加
        for p in exclusions:
            p = os.path.expandvars(str(p))
            if not p or not re.match(r"^[A-Za-z]:\\", p):
                continue
            say(f"添加排除目录 {p}")
            rc, out = _run([UWF_EXE, "file", "add-exclusion", p], timeout=30)
            actions.append(Action("排除 " + p, "uwfmgr file add-exclusion", rc == 0, out.strip()[:200]))

        say("UWF 已配置完成 (需要重启生效)")
        return True, ""

    def _rollback_uwf(self, done: List[Tuple[str, List[str]]], actions: List[Action]):
        for name, cmd in reversed(done):
            if "volume" in cmd and "protect" in cmd:
                vol = cmd[-1]
                _run([UWF_EXE, "volume", "unprotect", vol], timeout=40)
                actions.append(Action("回滚: 取消保护 " + vol, "", True, ""))
            elif "filter" in cmd and "enable" in cmd:
                _run([UWF_EXE, "filter", "disable"], timeout=40)
                actions.append(Action("回滚: 关闭筛选器", "", True, ""))

    def _enable_vhd_diff(self, actions: List[Action], say: Callable[[str], None]) -> Tuple[bool, str]:
        """基于 {current} 引导项推断父盘, 建差分盘并加一条独立引导项"""
        say("读取当前引导项, 定位系统父盘…")
        rc, out = _run(["bcdedit", "/enum", "{current}", "/v"], timeout=20)
        if rc != 0:
            return False, "读取 BCD 失败: " + out[:200]
        parent = ""
        for line in out.splitlines():
            if "vhd=" in line.lower() or ".vhd" in line.lower():
                m = re.search(r"\[([A-Za-z]):\]\s*(.+?\.vhdx?)", line, re.I)
                if m:
                    parent = f"{m.group(1)}:\\" + m.group(2).lstrip("\\")
                    break
        if not parent or not os.path.exists(parent):
            return False, "找不到可用的父盘 VHD (BCD 里的路径无法访问), 不敢继续, 以免开不了机"

        os.makedirs(SHADOW_DIR, exist_ok=True)
        child = os.path.join(SHADOW_DIR, "child-%s.vhdx" % time.strftime("%Y%m%d-%H%M%S"))
        say("创建差分盘 " + child)
        script = (
            'create vdisk file="%s" parent="%s"\n' % (child, parent)
        )
        rc, out = self._diskpart(script)
        actions.append(Action("创建差分盘", "diskpart create vdisk parent", rc == 0, out.strip()[:300]))
        if rc != 0 or not os.path.exists(child):
            return False, "创建差分盘失败: " + out.strip()[:200]

        say("复制引导项…")
        rc, out = _run(["bcdedit", "/copy", "{current}", "/d", BCD_DESC], timeout=20)
        guid = ""
        m = re.search(r"\{[0-9a-fA-F-]{36}\}", out)
        if rc == 0 and m:
            guid = m.group(0)
        actions.append(Action("复制引导项", "bcdedit /copy", bool(guid), out.strip()[:200]))
        if not guid:
            return False, "复制引导项失败, 已生成差分盘但未改引导 (删除 %s 即可)" % child

        drive = os.path.splitdrive(child)[0]
        rel = child[len(drive):]
        for key in ("device", "osdevice"):
            rc, out = _run(["bcdedit", "/set", guid, key, "vhd=[%s]%s" % (drive, rel)], timeout=20)
            actions.append(Action(f"设置 {key} 指向差分盘", "", rc == 0, out.strip()[:200]))
            if rc != 0:
                return False, f"设置引导 {key} 失败: {out.strip()[:200]}"
        for key, val in (("description", BCD_DESC), ("locale", "zh-CN")):
            _run(["bcdedit", "/set", guid, key, val], timeout=20)
        rc, out = _run(["bcdedit", "/displayorder", guid, "/addlast"], timeout=20)
        actions.append(Action("加入启动菜单", "bcdedit /displayorder /addlast", rc == 0, out.strip()[:200]))
        if rc != 0:
            return False, "写入启动菜单失败: " + out.strip()[:200]
        say("差分盘引导项已添加: 开机菜单选择「%s」即进入还原模式" % BCD_DESC)
        return True, ""

    def _diskpart(self, script: str) -> Tuple[int, str]:
        try:
            p = subprocess.run(["diskpart"], input=script, capture_output=True, text=True,
                               timeout=180, creationflags=CREATE_NO_WINDOW, errors="ignore")
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:
            return 1, str(e)

    # ============================================================ 关闭
    def disable(self, reboot: bool = False) -> Tuple[bool, str, List[Action]]:
        actions: List[Action] = []
        st = self.detect()
        if not st.is_windows:
            return False, "仅支持 Windows", actions
        if not st.admin:
            return False, "需要管理员权限", actions
        if not st.uwf_available:
            return False, "当前系统没有 UWF 组件", actions

        # 先把待还原的改动提交进系统盘, 否则关闭后这些改动会丢
        for name, cmd in (
            ("提交现有 overlay 改动 (不提交会丢失)", [UWF_EXE, "filter", "commit"]),
            ("取消系统盘保护", [UWF_EXE, "volume", "unprotect", "C:"]),
            ("关闭写入筛选器", [UWF_EXE, "filter", "disable"]),
        ):
            rc, out = _run(cmd, timeout=120)
            actions.append(Action(name, " ".join(cmd), rc == 0, out.strip()[:300]))
        ok = all(a.ok for a in actions if a.ok is not None)
        if reboot:
            _run([UWF_EXE, "filter", "restart"], timeout=30)
            return ok, "已关闭, 即将重启生效", actions
        return ok, "已关闭 (需要重启才完全生效)", actions

    # ============================================================ 状态
    def info_text(self) -> str:
        st = self.detect()
        lines = [f"系统: {st.product or platform.platform()} ({st.edition or '?'} build {st.build or '?'})",
                 "方案: " + {"uwf": "UWF 统一写入筛选器", "vhd": "VHD 差分盘"}.get(st.method, "不可用")]
        if st.method == "uwf":
            lines.append("当前状态: " + ("已开启" if st.uwf_enabled else "未开启"))
            if st.overlay_mb:
                lines.append(f"overlay: {st.overlay_mb} MB")
        for r in st.reasons:
            lines.append("· " + r)
        for n in st.notes:
            lines.append("· " + n)
        return "\n".join(lines)


# ================================================================= 命令行
def _cli(argv: List[str]) -> int:
    s = ShadowSystem(data_dir=os.path.join(os.path.expanduser("~"), ".fla"))
    if "--status" in argv or len(argv) <= 1:
        print(s.info_text())
        return 0
    if "--enable" in argv:
        skip = "--skip-clean" in argv
        ok, msg, acts = s.enable(skip_clean=skip, reboot="--reboot" in argv,
                                 progress=lambda t: print("  …", t))
        for a in acts:
            print(("  ✓ " if a.ok else "  ✗ ") + a.name + ((" — " + a.out) if a.out and not a.ok else ""))
        print(("成功: " if ok else "失败: ") + msg)
        return 0 if ok else 1
    if "--disable" in argv:
        ok, msg, acts = s.disable(reboot="--reboot" in argv)
        print(msg)
        return 0 if ok else 1
    print("用法: python -m fla_qt.shadow --status | --enable [--skip-clean] [--reboot] | --disable [--reboot]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
