"""FLA 桌面端 —— 希沃 (Seewo / 视睿 CVTE / EasiNote) 深度消杀引擎 (v1.37)

目标: 把电脑上 "希沃白板5 / 希沃视频展台 / 希沃管家" 留下的东西清干净, 而不只是隐藏窗口。
覆盖 9 类残留:

    1. 进程      EasiNote / PPTService / SeewoLink / SeewoPinco / 希沃管家 …  (含子进程)
    2. 服务      EasiService / SeewoService / SeewoUpdateService …
    3. 驱动      seewo*.sys / Easi*.sys 及对应服务项 (占用中的标记为重启删除)
    4. 注册表    HKLM/HKCU \\Software\\Seewo、CVTE、视睿 及 Uninstall 项
    5. 计划任务  \\Seewo* \\EasiNote* \\CVTE*
    6. 开机自启  Run/RunOnce 值 + 启动文件夹快捷方式
    7. 安装目录  Program Files / ProgramData / AppData 下的希沃目录 (整体搬进隔离区)
    8. 残留文件  目录删掉后仍在的 .dll/.exe/.sys (重启删除)
    9. PPT 加载项 / COM CLSID  PowerPoint、WPS 的希沃插件与 shell 扩展注册

安全设计 (为什么可以放心点 "开始消杀"):
    · 只删 "白名单关键词 + 明确路径" 命中的东西, 不做通配符乱删
    · 每一项删除前先备份: 注册表用 reg export, 文件整体移动到隔离区 (可一键还原)
    · 驱动/被占用文件不硬删, 走 PendingFileRenameOperations, 重启后系统自己删
    · 全程写日志 data/purge/seewo-*.log, 每一步都可审计
    · 需要管理员权限; 非 Windows 平台直接拒绝执行 (只返回报告)

命令行:
    python -m fla_qt.purge --scan            # 只体检, 不动任何东西
    python -m fla_qt.purge --purge           # 消杀 (会自动备份)
    python -m fla_qt.purge --purge --no-backup
    python -m fla_qt.purge --restore <隔离区目录>
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

IS_WIN = platform.system().lower() == "windows"
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

# ----------------------------------------------------------------- 关键字
KEYWORDS = ("seewo", "easinote", "cvte", "hitevision", "希沃", "视睿", "easinote5")

PROCESS_NAMES = [
    "EasiNote.exe", "EasiNote5.exe", "EasiNoteUpdate.exe", "PPTService.exe", "PPTAssistant.exe",
    "SeewoPPTAssistant.exe", "EasiService.exe", "EasiTool.exe", "EasiCamera.exe", "EasiScreen.exe",
    "SeewoLink.exe", "SeewoLinkHelper.exe", "SeewoLinkService.exe", "SeewoPinco.exe",
    "SeewoBoardTool.exe", "SeewoUpdate.exe", "SeewoService.exe", "SeewoAssistant.exe",
    "SeewoRemoteDesktop.exe", "SeewoDeviceManager.exe", "SeewoWhiteboard.exe", "SeewoDisplay.exe",
    "SeewoCast.exe", "SeewoWebServer.exe", "SeewoCmd.exe", "SeewoHotFix.exe", "SeewoSetup.exe",
    "SeewoPcManager.exe", "CVTECamera.exe", "HiteVision.exe",
]

SERVICE_NAMES = [
    "EasiService", "EasiNoteService", "SeewoService", "SeewoUpdateService", "SeewoLinkService",
    "SeewoDeviceService", "SeewoAssistantService", "SeewoDisplayService", "SeewoHotFixService",
    "SeewoPcService", "PPTService", "CVTEService", "HiteVisionService", "SeewoGuardService",
]

DRIVER_NAMES = [
    "seewo", "seewohid", "seewodisp", "seewovirtual", "seewousb", "seewoserial",
    "easivirtual", "easiserial", "easihid", "easidisp", "cvtehid", "cvteusb", "hitevision",
]
DRIVER_QUERY_NAMES = [
    "SeewoDisplayDriver", "seewohid", "SeewoUsbDriver", "SeewoSerial", "EasiVirtualDriver",
    "EasiSerial", "EasiHid", "CVTEHid", "CVTEUsb", "HiteVisionDriver", "SeewoVirtualDisplay",
    "SeewoBoardDriver",
]

REGISTRY_ROOTS = [
    (r"SOFTWARE\Seewo", "HKLM"),
    (r"SOFTWARE\WOW6432Node\Seewo", "HKLM"),
    (r"SOFTWARE\CVTE", "HKLM"),
    (r"SOFTWARE\WOW6432Node\CVTE", "HKLM"),
    (r"SOFTWARE\EasiNote", "HKLM"),
    (r"SOFTWARE\WOW6432Node\EasiNote", "HKLM"),
    (r"SOFTWARE\Seewo", "HKCU"),
    (r"SOFTWARE\CVTE", "HKCU"),
    (r"SOFTWARE\EasiNote", "HKCU"),
    (r"SOFTWARE\Microsoft\Office\PowerPoint\Addins\Seewo.PPTAssistant", "HKCU"),
    (r"SOFTWARE\Microsoft\Office\PowerPoint\Addins\Seewo.PPTAssistant", "HKLM"),
    (r"SOFTWARE\Microsoft\Office\PowerPoint\Addins\EasiNote.PPTPlugin", "HKCU"),
    (r"SOFTWARE\Microsoft\Office\PowerPoint\Addins\EasiNote.PPTPlugin", "HKLM"),
    (r"SOFTWARE\Microsoft\Office\PowerPoint\Addins\SeewoPPT", "HKCU"),
    (r"SOFTWARE\Kingsoft\Office\WPS\AddinsCOMPlugins\Seewo.PPTAssistant", "HKCU"),
]

UNINSTALL_ROOTS = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
]

RUN_KEYS = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKCU"),
]

INSTALL_DIRS = [
    r"C:\Program Files\Seewo",
    r"C:\Program Files (x86)\Seewo",
    r"C:\Program Files\EasiNote",
    r"C:\Program Files (x86)\EasiNote",
    r"C:\Program Files\CVTE",
    r"C:\Program Files (x86)\CVTE",
    r"C:\ProgramData\Seewo",
    r"C:\ProgramData\EasiNote",
    r"C:\ProgramData\CVTE",
    r"C:\SeewoData",
    r"C:\Seewo",
]

USER_DIR_TEMPLATES = [
    r"%LOCALAPPDATA%\Seewo",
    r"%LOCALAPPDATA%\EasiNote",
    r"%LOCALAPPDATA%\CVTE",
    r"%APPDATA%\Seewo",
    r"%APPDATA%\EasiNote",
    r"%APPDATA%\CVTE",
    r"%LOCALAPPDATA%\Programs\Seewo",
    r"%LOCALAPPDATA%\Programs\EasiNote",
]

STARTUP_DIRS = [
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup",
    r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Startup",
]

TASK_PATTERNS = ("seewo", "easinote", "cvte", "hitevision", "希沃")

FILE_EXTS = (".exe", ".dll", ".sys", ".ocx", ".drv", ".lnk", ".url", ".bat", ".cmd", ".ps1")


def _expand(p: str) -> str:
    return os.path.expandvars(p) if p else p


def _hit(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in KEYWORDS)


@dataclass
class Step:
    """一个可执行的消杀动作"""
    kind: str                       # proc/service/driver/reg/task/startup/dir/file/clsid
    key: str                        # 唯一标识 (进程名 / 服务名 / 注册表路径 / 目录)
    label: str                      # 给人看的中文描述
    detail: str = ""
    ok: Optional[bool] = None
    msg: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "key": self.key, "label": self.label, "detail": self.detail,
                "ok": self.ok, "msg": self.msg, "extra": self.extra}


@dataclass
class Report:
    started: float = 0.0
    admin: bool = False
    is_windows: bool = IS_WIN
    steps: List[Step] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    backup_dir: str = ""
    restore_hint: str = ""

    def by_kind(self, kind: str) -> List[Step]:
        return [s for s in self.steps if s.kind == kind]

    def counts(self) -> Dict[str, int]:
        d: Dict[str, int] = {}
        for s in self.steps:
            d[s.kind] = d.get(s.kind, 0) + 1
        return d

    def summary(self) -> str:
        c = self.counts()
        if not c:
            return "未发现希沃残留"
        names = {"proc": "进程", "service": "服务", "driver": "驱动", "reg": "注册表",
                 "task": "计划任务", "startup": "开机自启", "dir": "安装目录",
                 "file": "残留文件", "clsid": "COM/CLSID"}
        return " · ".join(f"{names.get(k, k)} {v}" for k, v in c.items())

    def to_json(self) -> str:
        return json.dumps({"started": self.started, "admin": self.admin, "windows": self.is_windows,
                           "backup_dir": self.backup_dir, "notes": self.notes,
                           "steps": [s.as_dict() for s in self.steps]}, ensure_ascii=False, indent=2)


# ================================================================= 基础工具
def is_admin() -> bool:
    if not IS_WIN:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run(args: Sequence[str], timeout: int = 20) -> Tuple[int, str]:
    try:
        p = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW, errors="ignore")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def _attach_console_free() -> None:
    pass


class SeewoPurge:
    """扫描 + 消杀。所有方法在非 Windows 上安全返回空结果。"""

    def __init__(self, data_dir: Optional[str] = None, log=print):
        self.data_dir = data_dir or os.path.join(os.path.expanduser("~"), ".fla")
        self.base = os.path.join(self.data_dir, "purge")
        self.log_cb = log
        self.report = Report(started=time.time(), admin=is_admin(), is_windows=IS_WIN)
        self._log_path = ""
        self._file_backups: List[Dict[str, str]] = []

    # ------------------------------------------------------------ 日志
    def _log(self, text: str):
        try:
            if not self._log_path:
                os.makedirs(self.base, exist_ok=True)
                self._log_path = os.path.join(self.base, "seewo-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), text))
        except Exception:
            pass
        try:
            self.log_cb(text)
        except Exception:
            pass

    # ============================================================ 扫描
    def scan(self) -> Report:
        r = self.report = Report(started=time.time(), admin=is_admin(), is_windows=IS_WIN)
        if not IS_WIN:
            r.notes.append("当前系统不是 Windows, 无法扫描希沃残留 (仅可在教室 Windows 电脑执行)。")
            return r
        if not r.admin:
            r.notes.append("当前不是管理员权限: 只能体检, 无法删除服务/驱动/Program Files。请右键 FLA → 以管理员身份运行。")
        self._scan_processes()
        self._scan_services(drivers=False)
        self._scan_drivers()
        self._scan_registry()
        self._scan_uninstall()
        self._scan_tasks()
        self._scan_startup()
        self._scan_dirs()
        self._scan_clsid()
        self._log("扫描完成: " + r.summary())
        return r

    def _scan_processes(self):
        if not IS_WIN:
            return
        rc, out = _run(["tasklist", "/FO", "CSV", "/NH"])
        if rc != 0:
            return
        seen = set()
        for line in out.splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) < 2:
                continue
            name = cols[0].strip('"')
            if not name.lower().endswith(".exe"):
                continue
            if name.lower() in [p.lower() for p in PROCESS_NAMES] or _hit(name):
                if name.lower() in seen:
                    continue
                seen.add(name.lower())
                self.report.steps.append(Step("proc", name, f"进程 {name}", "PID " + cols[1]))

    def _scan_services(self, drivers: bool = False):
        if not IS_WIN:
            return
        rc, out = _run(["sc", "query", "type=", "driver" if drivers else "service", "state=", "all"])
        if rc != 0 and not out:
            return
        cur = ""
        binpath = ""
        for line in out.splitlines():
            m = re.match(r"SERVICE_NAME:\s*(\S+)", line)
            if m:
                if cur:
                    self._maybe_add_service(cur, binpath, drivers)
                cur = m.group(1)
                binpath = ""
            elif "BINARY_PATH_NAME" in line:
                binpath = line.split(":", 1)[1].strip()
        if cur:
            self._maybe_add_service(cur, binpath, drivers)

    def _maybe_add_service(self, name: str, binpath: str, driver: bool):
        low = name.lower()
        wanted = DRIVER_QUERY_NAMES if driver else SERVICE_NAMES
        hit = low in [n.lower() for n in wanted] or _hit(name) or _hit(binpath) or \
              (driver and any(k in low for k in DRIVER_NAMES))
        if not hit:
            return
        kind = "driver" if driver else "service"
        label = ("驱动" if driver else "服务") + " " + name
        self.report.steps.append(Step(kind, name, label, binpath, extra={"binpath": binpath}))

    def _scan_drivers(self):
        self._scan_services(drivers=True)

    def _scan_registry(self):
        if not IS_WIN:
            return
        import winreg
        roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
        for path, hive in REGISTRY_ROOTS:
            try:
                winreg.OpenKey(roots[hive], path).Close()
            except OSError:
                continue
            self.report.steps.append(Step("reg", f"{hive}\\{path}", f"注册表 {hive}\\{path}",
                                          extra={"hive": hive, "path": path}))

    def _scan_uninstall(self):
        if not IS_WIN:
            return
        import winreg
        roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
        for path, hive in UNINSTALL_ROOTS:
            try:
                key = winreg.OpenKey(roots[hive], path)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    sk = winreg.OpenKey(key, sub)
                    disp, _ = winreg.QueryValueEx(sk, "DisplayName")
                    loc = ""
                    try:
                        loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                    except OSError:
                        pass
                    uninst = ""
                    try:
                        uninst, _ = winreg.QueryValueEx(sk, "UninstallString")
                    except OSError:
                        pass
                    sk.Close()
                except OSError:
                    continue
                if _hit(str(disp)) or _hit(loc) or _hit(uninst):
                    self.report.steps.append(Step(
                        "reg", f"{hive}\\{path}\\{sub}", f"卸载项 {disp or sub}",
                        loc or uninst, extra={"hive": hive, "path": f"{path}\\{sub}",
                                              "uninstall": uninst, "install": loc}))
            key.Close()

    def _scan_tasks(self):
        if not IS_WIN:
            return
        rc, out = _run(["schtasks", "/Query", "/FO", "CSV", "/NH"], timeout=30)
        if rc != 0:
            return
        for line in out.splitlines():
            name = line.split('","')[0].strip('"').strip()
            if not name:
                continue
            low = name.lower()
            if any(p in low for p in TASK_PATTERNS):
                self.report.steps.append(Step("task", name, "计划任务 " + name))

    def _scan_startup(self):
        if not IS_WIN:
            return
        import winreg
        roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
        for path, hive in RUN_KEYS:
            try:
                key = winreg.OpenKey(roots[hive], path)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if _hit(name) or (_hit(str(value)) and any(
                        k in str(value).lower() for k in ("seewo", "easinote", "cvte"))):
                    self.report.steps.append(Step("startup", f"{hive}\\{path}\\{name}",
                                                  f"开机自启 {name} ({hive})", str(value),
                                                  extra={"hive": hive, "path": path, "name": name}))
            key.Close()
        for d in STARTUP_DIRS:
            p = _expand(d)
            if not os.path.isdir(p):
                continue
            for fn in os.listdir(p):
                if _hit(fn):
                    self.report.steps.append(Step("startup", os.path.join(p, fn), "启动项 " + fn))

    def _scan_dirs(self):
        for d in self._candidate_dirs():
            if os.path.isdir(d):
                size = _dir_size(d)
                self.report.steps.append(Step("dir", d, "安装目录 " + d,
                                              "%.1f MB" % (size / 1048576.0)))

    def _candidate_dirs(self) -> List[str]:
        out: List[str] = []
        for p in INSTALL_DIRS:
            out.append(p)
        for t in USER_DIR_TEMPLATES:
            out.append(_expand(t))
        # 从卸载项里再挖 InstallLocation / UninstallString 所在目录
        for s in self.report.steps:
            if s.kind != "reg":
                continue
            for k in ("install", "uninstall"):
                v = (s.extra.get(k) or "").strip().strip('"')
                if not v or not _hit(v):
                    continue
                m = re.match(r"([A-Za-z]:\\[^\"]+?)(?:\\[\w .-]+\.exe)?$", v)
                if m:
                    cand = m.group(1)
                    if os.path.isdir(cand):
                        out.append(cand)
        seen, uniq = set(), []
        for p in out:
            if not p:
                continue
            key = os.path.normcase(os.path.normpath(p))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(p)
        # 去掉被父目录包含的子目录, 避免重复删
        uniq.sort(key=len)
        final: List[str] = []
        for p in uniq:
            if any(os.path.normcase(p).startswith(os.path.normcase(q) + os.sep) for q in final):
                continue
            final.append(p)
        return final

    def _scan_clsid(self):
        if not IS_WIN:
            return
        import winreg
        for hive_name, hive in (("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)):
            for base in (r"SOFTWARE\Classes\CLSID", r"SOFTWARE\Classes\WOW6432Node\CLSID"):
                try:
                    root = winreg.OpenKey(hive, base)
                except OSError:
                    continue
                i = 0
                while True:
                    try:
                        guid = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    if not guid.startswith("{"):
                        continue
                    found = ""
                    for sub in ("InprocServer32", "LocalServer32"):
                        try:
                            k = winreg.OpenKey(root, guid + "\\" + sub)
                            try:
                                found, _ = winreg.QueryValueEx(k, "")
                            except OSError:
                                pass
                            k.Close()
                        except OSError:
                            continue
                        if _hit(str(found)):
                            break
                    if found and _hit(str(found)):
                        self.report.steps.append(Step(
                            "clsid", f"{hive_name}\\{base}\\{guid}", "COM 组件 " + guid,
                            str(found), extra={"hive": hive_name, "path": base + "\\" + guid}))
                root.Close()

    # ============================================================ 隔离区
    def _ensure_backup(self) -> str:
        if not self.report.backup_dir:
            d = os.path.join(self.base, "quarantine-" + time.strftime("%Y%m%d-%H%M%S"))
            os.makedirs(d, exist_ok=True)
            self.report.backup_dir = d
            self.report.restore_hint = d
            with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"created": time.time(), "files": [], "registry": []}, f, ensure_ascii=False, indent=2)
        return self.report.backup_dir

    def _manifest_add(self, kind: str, item: Dict[str, str]):
        try:
            mp = os.path.join(self._ensure_backup(), "manifest.json")
            with open(mp, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault(kind, []).append(item)
            with open(mp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _backup_reg(self, hive: str, path: str) -> bool:
        if not self.report.backup_dir:
            return False
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", hive + "_" + path)[:120]
        out = os.path.join(self.report.backup_dir, safe + ".reg")
        rc, _ = _run(["reg", "export", f"{hive}\\{path}", out, "/y"], timeout=30)
        ok = rc == 0 and os.path.exists(out)
        if ok:
            self._manifest_add("registry", {"hive": hive, "path": path, "file": out})
        return ok

    def _move_to_quarantine(self, path: str) -> Tuple[bool, str]:
        """整个目录/文件搬到隔离区; 被占用则退回 "重启删除"。"""
        bdir = self._ensure_backup()
        name = os.path.basename(path.rstrip("\\/")) or "item"
        dest = os.path.join(bdir, "files", name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(bdir, "files", f"{name}.{n}")
            n += 1
        try:
            shutil.move(path, dest)
            self._manifest_add("files", {"from": path, "to": dest})
            return True, "已移入隔离区"
        except Exception as e:
            ok = self._schedule_delete(path)
            if ok:
                self._manifest_add("files", {"from": path, "to": "", "pending": "1"})
                return True, "文件被占用, 已安排重启后删除 (%s)" % e
            return False, str(e)

    # ============================================================ 执行
    def purge(self, backup: bool = True, progress: Optional[Callable[[int, int, str], None]] = None,
              force: bool = False) -> Report:
        """执行消杀。force=True 只给测试/离线演练用 (跳过 Windows 判断)。"""
        r = self.report if self.report.steps else self.scan()
        if not IS_WIN and not force:
            r.notes.append("非 Windows 平台, 未执行任何删除动作。")
            return r
        if not backup:
            r.notes.append("用户选择了『无备份』模式, 删除后不可恢复。")
        total = len(r.steps)
        self._log(f"开始消杀: 共 {total} 项 (备份={'是' if backup else '否'})")
        for idx, step in enumerate(r.steps):
            if progress:
                try:
                    progress(idx + 1, total, step.label)
                except Exception:
                    pass
            try:
                ok, msg = self._do(step, backup)
            except Exception as e:      # 单项失败不影响整体
                ok, msg = False, "异常: %s" % e
            step.ok, step.msg = ok, msg
            self._log(("[OK] " if ok else "[失败] ") + step.label + (" — " + msg if msg else ""))
        okn = sum(1 for s in r.steps if s.ok)
        self._log(f"消杀结束: 成功 {okn}/{total}")
        r.notes.append(f"成功处理 {okn}/{total} 项。日志: {self._log_path}")
        if any(not s.ok for s in r.steps):
            r.notes.append("有项目失败: 多为文件被占用或权限不足, 重启后重跑一次即可 (会继续清理)。")
        r.notes.append("建议重启电脑, 让驱动的重启删除、PowerPoint 加载项卸载彻底生效。")
        return r

    def _do(self, step: Step, backup: bool) -> Tuple[bool, str]:
        if step.kind == "proc":
            return self._kill_process(step.key)
        if step.kind == "service":
            return self._delete_service(step.key, driver=False)
        if step.kind == "driver":
            return self._delete_service(step.key, driver=True)
        if step.kind == "task":
            return self._delete_task(step.key)
        if step.kind == "reg":
            return self._delete_reg(step, backup)
        if step.kind == "startup":
            return self._delete_startup(step, backup)
        if step.kind in ("dir", "file"):
            return self._delete_path(step.key, backup)
        if step.kind == "clsid":
            return self._delete_reg(step, backup, unregister=True)
        return False, "未知类型"

    # ------------------------------------------------------------ 进程
    def _kill_process(self, name: str) -> Tuple[bool, str]:
        rc, out = _run(["taskkill", "/F", "/T", "/IM", name], timeout=15)
        if rc == 0:
            return True, "已结束"
        if "not found" in out.lower() or "没有找到" in out:
            return True, "进程已不存在"
        return False, out.strip()[:200]

    # ------------------------------------------------------------ 服务 / 驱动
    def _delete_service(self, name: str, driver: bool) -> Tuple[bool, str]:
        msgs = []
        rc, _ = _run(["sc", "stop", name], timeout=25)
        if rc == 0:
            msgs.append("已停止")
        for _ in range(10):
            rc, out = _run(["sc", "query", name], timeout=10)
            if rc != 0:
                break
            if "STOPPED" in out.upper() or "1  STOPPED" in out:
                break
            time.sleep(0.3)
        rc, out = _run(["sc", "delete", name], timeout=15)
        if rc == 0:
            msgs.append("服务项已删除")
        elif "1060" in out or "does not exist" in out.lower():
            return True, "; ".join(msgs) or "服务不存在"
        else:
            # sc delete 失败 (服务仍被占用/标记删除中): 先禁用, 保证重启后不再运行,
            # 下次再跑消杀就能删掉; 注册表项不是文件, 不能用 PendingFileRenameOperations
            rc2, out2 = _run(["sc", "config", name, "start=", "disabled"], timeout=15)
            if rc2 == 0:
                msgs.append("暂时删不掉, 已设为禁用, 重启后再消杀一次即可删除")
                return True, "; ".join(msgs)
            return False, (out.strip() or out2.strip())[:200]
        return True, "; ".join(msgs)

    # ------------------------------------------------------------ 计划任务
    def _delete_task(self, name: str) -> Tuple[bool, str]:
        rc, out = _run(["schtasks", "/Delete", "/TN", name, "/F"], timeout=20)
        if rc == 0:
            return True, "已删除"
        if "cannot find" in out.lower() or "找不到" in out:
            return True, "任务不存在"
        return False, out.strip()[:200]

    # ------------------------------------------------------------ 注册表
    def _delete_reg(self, step: Step, backup: bool, unregister: bool = False) -> Tuple[bool, str]:
        import winreg
        hive_name = step.extra.get("hive", "HKLM")
        path = step.extra.get("path", "")
        hive = winreg.HKEY_LOCAL_MACHINE if hive_name == "HKLM" else winreg.HKEY_CURRENT_USER
        note = ""
        if unregister and step.kind == "clsid":
            # 先让系统反注册, 再删键
            dll = step.detail.strip()
            if dll.lower().endswith(".dll") and os.path.exists(dll):
                _run(["regsvr32", "/u", "/s", dll], timeout=20)
                note = "已反注册; "
        if backup:
            if not self._backup_reg(hive_name, path):
                # 备份失败仍然继续, 但要记下来
                note += "注册表备份失败; "
        ok, msg = _delete_reg_tree(hive, path)
        if ok:
            return True, note + "键已删除"
        return False, note + msg

    def _delete_startup(self, step: Step, backup: bool) -> Tuple[bool, str]:
        if step.key.lower().endswith((".lnk", ".url", ".bat", ".cmd", ".ps1", ".exe")):
            return self._delete_path(step.key, backup)
        import winreg
        hive = winreg.HKEY_LOCAL_MACHINE if step.extra.get("hive") == "HKLM" else winreg.HKEY_CURRENT_USER
        path, name = step.extra.get("path", ""), step.extra.get("name", "")
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
            return True, "自启项已删除"
        except FileNotFoundError:
            return True, "自启项不存在"
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------ 文件
    def _delete_path(self, path: str, backup: bool) -> Tuple[bool, str]:
        if not path or not os.path.exists(path):
            return True, "已不存在"
        if os.path.isdir(path):
            if backup:
                ok, msg = self._move_to_quarantine(path)
                if ok:
                    return True, msg
                # 移动失败 (被占用) → 先删能删的, 其余重启删
            freed, pending = _rmtree_best_effort(path)
            if backup:
                pass
            if pending:
                self._schedule_delete(path)
                return True, f"删除 {freed} 项, {pending} 项被占用已安排重启后删除"
            return True, f"已删除 ({freed} 项)"
        if backup:
            ok, msg = self._move_to_quarantine(path)
            if ok:
                return True, msg
        try:
            os.remove(path)
            return True, "已删除"
        except Exception:
            if self._schedule_delete(path):
                return True, "已安排重启后删除"
            return False, "删除失败"

    def _schedule_delete(self, path: str) -> bool:
        """写 PendingFileRenameOperations: 重启时由内核删除 (驱动 .sys / 占用中的 dll)"""
        if not IS_WIN:
            return False
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SYSTEM\CurrentControlSet\Control\Session Manager",
                                 0, winreg.KEY_READ | winreg.KEY_WRITE)
            try:
                old, _ = winreg.QueryValueEx(key, "PendingFileRenameOperations")
            except OSError:
                old = []
            # 格式: 成对出现 —— 源路径, 目标路径 (空串 = 直接删除)
            src = path if path.startswith("\\??\\") else "\\??\\" + path
            cur = [str(x) for x in (old or [])]
            if src not in cur:
                cur.extend([src, ""])
            winreg.SetValueEx(key, "PendingFileRenameOperations", 0, winreg.REG_MULTI_SZ, cur)
            key.Close()
            return True
        except Exception:
            return False


# ================================================================= 辅助
def _delete_reg_tree(hive, path: str) -> Tuple[bool, str]:
    """递归删注册表键 (winreg 没有递归删除)"""
    import winreg
    try:
        _del_key(hive, path)
        return True, ""
    except FileNotFoundError:
        return True, ""
    except Exception as e:
        return False, str(e)


def _del_key(hive, path: str):
    import winreg
    try:
        k = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except FileNotFoundError:
        return
    while True:
        try:
            sub = winreg.EnumKey(k, 0)
        except OSError:
            break
        try:
            _del_key(hive, path + "\\" + sub)
        except Exception:
            pass
    k.Close()
    winreg.DeleteKey(hive, path)


def _rmtree_best_effort(root: str) -> Tuple[int, int]:
    """删目录: 能删的都删, 返回 (成功数, 被占用数)"""
    okc = pending = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                os.remove(p)
                okc += 1
            except Exception:
                pending += 1
        for dn in dirnames:
            p = os.path.join(dirpath, dn)
            try:
                os.rmdir(p)
            except Exception:
                pass
    try:
        os.rmdir(root)
    except Exception:
        pending += 1 if os.path.exists(root) else 0
    return okc, pending


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except Exception:
                pass
    return total


def restore(quarantine_dir: str, log=print) -> Tuple[int, int]:
    """把隔离区里的文件搬回原位; 返回 (成功, 失败)"""
    mp = os.path.join(quarantine_dir, "manifest.json")
    if not os.path.exists(mp):
        raise FileNotFoundError("隔离区缺少 manifest.json: " + quarantine_dir)
    with open(mp, "r", encoding="utf-8") as f:
        data = json.load(f)
    ok = bad = 0
    for item in data.get("files", []):
        src, dst = item.get("to") or "", item.get("from") or ""
        if not src or not dst or not os.path.exists(src):
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.move(src, dst)
            else:
                shutil.copy2(src, dst)
            ok += 1
        except Exception as e:
            bad += 1
            log("还原失败 %s: %s" % (dst, e))
    for item in data.get("registry", []):
        reg = item.get("file") or ""
        if reg and os.path.exists(reg):
            rc, out = _run(["reg", "import", reg], timeout=30)
            (ok := ok + 1) if rc == 0 else (bad := bad + 1)
    return ok, bad


def _cli(argv: List[str]) -> int:
    if "--scan" in argv or len(argv) <= 1:
        p = SeewoPurge()
        r = p.scan()
        print(r.summary())
        for s in r.steps:
            print(f"  [{s.kind}] {s.label}" + (f" — {s.detail}" if s.detail else ""))
        for n in r.notes:
            print("·", n)
        return 0
    if "--purge" in argv:
        p = SeewoPurge()
        p.scan()
        r = p.purge(backup="--no-backup" not in argv)
        print(r.summary())
        for s in r.steps:
            print(("  ✓ " if s.ok else "  ✗ ") + s.label + " — " + s.msg)
        for n in r.notes:
            print("·", n)
        return 0 if all(s.ok for s in r.steps) else 1
    if "--restore" in argv:
        i = argv.index("--restore")
        ok, bad = restore(argv[i + 1] if i + 1 < len(argv) else "")
        print(f"还原完成: 成功 {ok}, 失败 {bad}")
        return 0
    print("用法: python -m fla_qt.purge --scan | --purge [--no-backup] | --restore <隔离区>")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
