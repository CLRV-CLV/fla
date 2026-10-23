"""FLA 桌面端核心: 配置 / 服务器 API / 课件下载与调起 / 放映按键 / 希沃抑制 / fla:// 协议 / 自更新"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "3.6.0"
APP_NAME = "FLA 课堂助手"
DEFAULT_SERVER = "https://t.clrv.top"
SERVERS = [("https://t.clrv.top", "t.clrv.top（主线路）"), ("https://t.fyx.best", "t.fyx.best（备用线路）")]
ALLOWED = [u for u, _ in SERVERS]
BRIDGE_PORT = 8307
IS_WIN = sys.platform == "win32"


# ------------------------------------------------------------------ 配置
def data_dir() -> str:
    """只存轻量配置与批注; 大文件缓存/课件另行保存到可配置的非系统盘目录。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "FLA")
    os.makedirs(d, exist_ok=True)
    return d


def _probe_writable(path: str) -> bool:
    """确认目录可写, 避免把默认存储选到光驱或只读分区。"""
    try:
        os.makedirs(path, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".fla-write-test-", dir=path)
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def default_storage_dirs() -> tuple[str, str]:
    """Windows 优先选择可写的非系统盘；若没有其他盘则落在用户文档目录，可随时修改。"""
    if IS_WIN:
        system_drive = os.path.splitdrive(os.environ.get("SystemDrive", "C:"))[0].upper()
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive = letter + ":"
            if drive.upper() == system_drive or not os.path.isdir(drive + "\\"):
                continue
            root = os.path.join(drive + "\\", "FLA")
            if _probe_writable(root):
                return os.path.join(root, "Cache"), os.path.join(root, "Courseware")
        root = os.path.join(os.path.expanduser("~"), "Documents", "FLA")
        return os.path.join(root, "Cache"), os.path.join(root, "Courseware")
    root = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"), "FLA")
    return os.path.join(root, "cache"), os.path.join(root, "courseware")


def _clean_storage_dir(path: str) -> str:
    path = os.path.expandvars(os.path.expanduser((path or "").strip()))
    return os.path.abspath(path) if path else ""


def storage_location(kind: str) -> str:
    if kind not in ("cache", "download"):
        raise ValueError("未知存储类型")
    value = cfg.cache_dir if kind == "cache" else cfg.download_dir
    path = _clean_storage_dir(value)
    if not path:
        raise RuntimeError("尚未设置非系统盘存储位置，请先在「设置 → 存储位置」中选择目录。")
    os.makedirs(path, exist_ok=True)
    if not _probe_writable(path):
        raise RuntimeError("存储目录不可写，请在「设置 → 存储位置」中选择可写目录。")
    return path


def set_storage_dir(kind: str, path: str) -> str:
    if kind not in ("cache", "download"):
        raise ValueError("未知存储类型")
    clean = _clean_storage_dir(path)
    if not clean:
        raise ValueError("请选择有效的存储目录。")
    if not _probe_writable(clean):
        raise ValueError("该目录不可写，请选择一个可写目录。")
    if kind == "cache":
        cfg.cache_dir = clean
    else:
        cfg.download_dir = clean
    cfg.save()
    return clean


class Config:
    def __init__(self):
        self.path = os.path.join(data_dir(), "config.json")
        self.server = DEFAULT_SERVER
        self.token = ""
        self.user = ""
        self.seewo = True
        self.dock_on_start = False
        self.cache_dir, self.download_dir = default_storage_dirs()
        self.dock_size = 40
        self.dock_hidden = []
        self.startup = False
        self.load()
        self._fix()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.server = d.get("server") or DEFAULT_SERVER
            self.token = d.get("token", "")
            self.user = d.get("user", "")
            self.seewo = bool(d.get("seewo", True))
            self.dock_on_start = bool(d.get("dock_on_start", False))
            self.cache_dir = _clean_storage_dir(d.get("cache_dir", self.cache_dir)) or self.cache_dir
            self.download_dir = _clean_storage_dir(d.get("download_dir", self.download_dir)) or self.download_dir
            try:
                self.dock_size = int(d.get("dock_size", 40))
            except (TypeError, ValueError):
                self.dock_size = 40
            hidden = d.get("dock_hidden", [])
            self.dock_hidden = [str(x) for x in hidden if isinstance(x, (str, int))] if isinstance(hidden, list) else []
            self.startup = bool(d.get("startup", False))
            return
        except Exception:
            pass
        # 迁移 v2 原生版 config.ini
        try:
            with open(os.path.join(data_dir(), "config.ini"), encoding="utf-8", errors="ignore") as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    if k == "server_url" and v:
                        self.server = v
                    elif k == "token":
                        self.token = v
                    elif k == "user":
                        self.user = v
                    elif k == "seewo":
                        self.seewo = v != "0"
        except Exception:
            pass

    def _fix(self):
        s = (self.server or "").strip().rstrip("/").lower()
        s = s.replace("http://", "https://")
        if not s.startswith("https://"):
            s = "https://" + s
        self.server = s if s in ALLOWED else DEFAULT_SERVER
        self.cache_dir = _clean_storage_dir(self.cache_dir)
        self.download_dir = _clean_storage_dir(self.download_dir)
        self.dock_size = max(32, min(48, int(self.dock_size or 40)))
        self.dock_hidden = sorted(set(self.dock_hidden or []))

    def save(self):
        self._fix()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"server": self.server, "token": self.token, "user": self.user,
                           "seewo": self.seewo, "dock_on_start": self.dock_on_start,
                           "cache_dir": self.cache_dir, "download_dir": self.download_dir,
                           "dock_size": self.dock_size, "dock_hidden": self.dock_hidden,
                           "startup": self.startup}, f, ensure_ascii=False)
        except Exception:
            pass


cfg = Config()


# ------------------------------------------------------------------ HTTP
_CTX = ssl.create_default_context()
_CTX.check_hostname = False          # 允许自签证书 (临时 HTTPS)
_CTX.verify_mode = ssl.CERT_NONE


def full_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return cfg.server.rstrip("/") + path


def api(method: str, path: str, body=None, timeout: float = 15):
    """返回 (status, data). 网络错误 status=0"""
    data = None
    headers = {"User-Agent": f"FLA-Desktop/{VERSION}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cfg.token:
        headers["Authorization"] = "Bearer " + cfg.token
    req = urllib.request.Request(full_url(path), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as e:
        return 0, {"detail": str(e)}
    try:
        return status, json.loads(raw.decode("utf-8") or "null")
    except Exception:
        return status, {"detail": raw[:200].decode("utf-8", "ignore")}


def ai_stream(path: str, body: dict, on_event=None, timeout: float = 180):
    """流式读取服务端 SSE AI 回复, 返回 (status, data); on_event 在线程中调用。"""
    headers = {"User-Agent": f"FLA-Desktop/{VERSION}", "Accept": "text/event-stream",
               "Content-Type": "application/json"}
    if cfg.token:
        headers["Authorization"] = "Bearer " + cfg.token
    req = urllib.request.Request(full_url(path), data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        last = {}
        data_lines = []
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").rstrip("\r\n")
                if not line:
                    if data_lines:
                        try:
                            event = json.loads("\n".join(data_lines))
                            last.update(event)
                            if on_event:
                                on_event(event)
                        except (TypeError, ValueError):
                            pass
                        data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if data_lines:
                try:
                    event = json.loads("\n".join(data_lines))
                    last.update(event)
                    if on_event:
                        on_event(event)
                except (TypeError, ValueError):
                    pass
        return 200, last
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return e.code, {"detail": raw[:500].decode("utf-8", "ignore")}
    except Exception as e:
        return 0, {"detail": str(e)}


def upload(path: str, progress=None, timeout: float = 600, url: str = "/api/files/upload", fields=None):
    """上传课件到云端课件库. 返回 (status, data). progress(done, total)"""
    import io
    import mimetypes
    import uuid
    name = os.path.basename(path)
    b = "----FLA" + uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    q = name.replace('"', "'").replace("\r", "").replace("\n", "")
    head = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{q}"\r\n'
            f'Content-Type: {mimetypes.guess_type(name)[0] or "application/octet-stream"}\r\n\r\n').encode("utf-8")
    extra = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
                     for k, v in (fields or {}).items())
    body = extra + head + content + f"\r\n--{b}--\r\n".encode()
    total = len(body)

    class _R(io.BytesIO):
        def read(self, n=-1):
            chunk = super().read(65536 if n is None or n < 0 else min(n, 65536))
            if progress:
                progress(self.tell(), total)
            return chunk
    headers = {"User-Agent": f"FLA-Desktop/{VERSION}", "Accept": "application/json",
               "Content-Type": "multipart/form-data; boundary=" + b, "Content-Length": str(total)}
    if cfg.token:
        headers["Authorization"] = "Bearer " + cfg.token
    req = urllib.request.Request(full_url(url), data=_R(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as e:
        return 0, {"detail": str(e)}
    try:
        return status, json.loads(raw.decode("utf-8") or "null")
    except Exception:
        return status, {"detail": raw[:200].decode("utf-8", "ignore")}


def fetch_bytes(path: str, timeout: float = 20):
    """带登录 GET 二进制 (头像/聊天图片). 失败返回 None"""
    headers = {"User-Agent": f"FLA-Desktop/{VERSION}"}
    if cfg.token:
        headers["Authorization"] = "Bearer " + cfg.token
    try:
        req = urllib.request.Request(full_url(path), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return r.read()
    except Exception:
        return None


def download(url: str, dest: str, token: str = "", progress=None, timeout: float = 30) -> bool:
    headers = {"User-Agent": f"FLA-Desktop/{VERSION}"}
    tok = token or cfg.token
    if tok and "token=" not in url:
        headers["Authorization"] = "Bearer " + tok
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(full_url(url), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
        if total and got != total:
            raise IOError("下载不完整")
        os.replace(tmp, dest)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def version_newer(remote: str, local: str = VERSION) -> bool:
    def parts(v):
        out = []
        for p in (v or "0").split(".")[:3]:
            try:
                out.append(int("".join(ch for ch in p if ch.isdigit()) or 0))
            except Exception:
                out.append(0)
        return out + [0] * (3 - len(out))
    return parts(remote) > parts(local)


# ------------------------------------------------------------------ 放映
OFFICE_CANDIDATES = [
    r"{pf}\Microsoft Office\root\Office16\POWERPNT.EXE",
    r"{pf}\Microsoft Office\Office16\POWERPNT.EXE",
    r"{pf}\Microsoft Office\root\Office15\POWERPNT.EXE",
    r"{pf}\Microsoft Office\Office15\POWERPNT.EXE",
    r"{pf}\Microsoft Office\Office14\POWERPNT.EXE",
]


def find_presenter() -> tuple[str, str]:
    """返回 (exe路径, 名称); 找不到返回 ('', '系统默认')"""
    if not IS_WIN:
        return "", "系统默认"
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for tpl in OFFICE_CANDIDATES:
        for pf in pfs:
            p = tpl.format(pf=pf)
            if os.path.isfile(p):
                return p, "PowerPoint"
    for root in [os.environ.get("LOCALAPPDATA", ""), *pfs]:
        p = os.path.join(root, "Kingsoft", "WPS Office", "ksolaunch.exe")
        if root and os.path.isfile(p):
            return p, "WPS"
    return "", "系统默认"


def safe_name(name: str) -> str:
    name = name or "presentation.pptx"
    return "".join("_" if c in '\\/:*?"<>|' else c for c in name)[:180]


def cache_path(name: str) -> str:
    """缓存目录中的单个路径 (不用于保存课件)。"""
    return os.path.join(storage_location("cache"), safe_name(name))


def download_path(name: str) -> str:
    """课件下载目录中的目标文件; Windows 不允许退回到系统盘。"""
    return os.path.join(storage_location("download"), safe_name(name))


def launch_presentation(path: str):
    exe, kind = find_presenter()
    if exe and kind == "PowerPoint" and path.lower().endswith((".ppt", ".pptx", ".pps", ".ppsx")):
        subprocess.Popen([exe, "/s", path], close_fds=True)
    elif exe and kind == "WPS":
        subprocess.Popen([exe, path], close_fds=True)
    elif IS_WIN:
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(["xdg-open", path])


# 放映控制: 向前台放映窗口发送按键 (PowerPoint / WPS 通用)
if IS_WIN:
    import ctypes
    _u32 = ctypes.windll.user32
    _k32 = ctypes.windll.kernel32

VK = {"next": 0x22, "prev": 0x21, "first": 0x24, "last": 0x23, "black": 0x42, "white": 0x57, "esc": 0x1B}


def send_key(cmd: str):
    vk = VK.get(cmd)
    if not vk or not IS_WIN:
        return
    _u32.keybd_event(vk, 0, 0, 0)
    _u32.keybd_event(vk, 0, 2, 0)


def focus_slideshow():
    """把 PowerPoint/WPS 放映窗口切到前台, 保证按键送达"""
    if not IS_WIN:
        return
    for cls in ("screenClass", "KSOSHOWFRAME", "WPSShowFrame"):
        h = _u32.FindWindowW(cls, None)
        if h:
            _u32.SetForegroundWindow(h)
            return


# ------------------------------------------------------------------ 希沃抑制
class SeewoSuppressor(threading.Thread):
    """希沃白板5 会在 PPT 放映时注入自己的悬浮工具条; 检测到 EasiNote 进程时隐藏其顶层窗口"""
    daemon = True

    def run(self):
        if not IS_WIN:
            return
        import ctypes.wintypes as wt
        EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        while True:
            time.sleep(3)
            if not cfg.seewo:
                continue
            pids = set()
            try:
                out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EasiNote.exe", "/FO", "CSV", "/NH"],
                                     capture_output=True, text=True, timeout=5,
                                     creationflags=0x08000000).stdout
                for line in out.splitlines():
                    cols = [c.strip('"') for c in line.split('","')]
                    if len(cols) > 1 and cols[0].lower().startswith("easinote"):
                        pids.add(int(cols[1]))
            except Exception:
                continue
            if not pids:
                continue

            def cb(hwnd, _):
                pid = wt.DWORD()
                _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in pids and _u32.IsWindowVisible(hwnd) and not _u32.GetWindow(hwnd, 4):
                    _u32.ShowWindow(hwnd, 0)
                return True
            _u32.EnumWindows(EnumProc(cb), 0)


# ------------------------------------------------------------------ fla:// 协议
BUNDLED = bool(os.environ.get("FLA_EXE"))   # 由 FLA.exe 启动器拉起 (正式发行形态)


def exe_path() -> str:
    if BUNDLED:
        return os.environ["FLA_EXE"]
    return sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])


def set_autostart(enabled: bool) -> bool:
    """只写当前用户 HKCU\Run, 不提权也不改系统登录/安全策略。"""
    if not IS_WIN:
        return False
    try:
        import winreg
        exe = exe_path()
        if BUNDLED or getattr(sys, "frozen", False):
            command = f'"{exe}"'
        else:
            command = f'"{sys.executable}" "{exe}"'
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            if enabled:
                winreg.SetValueEx(key, "FLA", 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, "FLA")
                except FileNotFoundError:
                    pass
        cfg.startup = bool(enabled)
        cfg.save()
        return True
    except Exception:
        return False


def register_protocol():
    if not IS_WIN:
        return
    try:
        import winreg
        exe = exe_path()
        cmd = f'"{exe}" "%1"' if (BUNDLED or getattr(sys, "frozen", False)) else f'"{sys.executable}" "{exe}" "%1"'
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\fla")
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:FLA Protocol")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        c = winreg.CreateKey(k, r"shell\open\command")
        winreg.SetValueEx(c, "", 0, winreg.REG_SZ, cmd)
    except Exception:
        pass


def parse_protocol(arg: str) -> dict | None:
    """fla://open?url=...&name=...&token=... → dict"""
    if not arg or not arg.lower().startswith("fla:"):
        return None
    q = urllib.parse.urlparse(arg).query or arg.partition("?")[2]
    p = urllib.parse.parse_qs(q)
    url = (p.get("url") or [""])[0]
    if not url:
        return None
    return {"url": url, "name": (p.get("name") or [""])[0], "token": (p.get("token") or [""])[0],
            "fid": (p.get("fid") or [""])[0]}


# ------------------------------------------------------------------ 自更新
def apply_update(new_exe: str, restart: bool = True) -> bool:
    """用下载好的新 exe 替换自身 (Windows 允许重命名运行中的 exe); restart=False 用于退出时静默安装"""
    if not (IS_WIN and (BUNDLED or getattr(sys, "frozen", False))):
        return False
    cur = exe_path()
    old = cur + ".old"
    try:
        if os.path.exists(old):
            os.remove(old)
    except Exception:
        pass
    try:
        os.replace(cur, old)
        shutil.move(new_exe, cur)
    except Exception:
        try:
            if not os.path.exists(cur) and os.path.exists(old):
                os.replace(old, cur)
        except Exception:
            pass
        return False
    if restart:
        subprocess.Popen([cur], close_fds=True)
    return True


def cleanup_old():
    try:
        os.remove(exe_path() + ".old")
    except Exception:
        pass
    # 删除旧版本解压出的运行环境
    cur = os.environ.get("FLA_RUNTIME", "")
    root = os.path.dirname(cur) if cur else ""
    if root and os.path.isdir(root):
        for d in os.listdir(root):
            p = os.path.join(root, d)
            if os.path.normcase(p) != os.path.normcase(cur):
                shutil.rmtree(p, ignore_errors=True)


def update_temp_path() -> str:
    """更新包也落在用户配置的缓存目录, 不悄悄写入 Windows 系统临时目录。"""
    return cache_path("FLA_update.exe")
