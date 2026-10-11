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

VERSION = "3.5.0"
APP_NAME = "FLA 课堂助手"
DEFAULT_SERVER = "https://t.clrv.top"
SERVERS = [("https://t.clrv.top", "t.clrv.top（主线路）"), ("https://t.fyx.best", "t.fyx.best（备用线路）")]
ALLOWED = [u for u, _ in SERVERS]
BRIDGE_PORT = 8307
IS_WIN = sys.platform == "win32"


# ------------------------------------------------------------------ 配置
def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "FLA")
    os.makedirs(d, exist_ok=True)
    return d


class Config:
    def __init__(self):
        self.path = os.path.join(data_dir(), "config.json")
        self.server = DEFAULT_SERVER
        self.token = ""
        self.user = ""
        self.seewo = True
        self.dock_on_start = False
        self.dock_hidden = []     # 工具盒中隐藏的工具 (key 列表)
        self.dock_scale = 1.0     # 工具盒按钮缩放 (0.8 ~ 1.5)
        self.cache_dir = ""       # 课件缓存目录 (空 = 默认非 C 盘位置)
        self.download_dir = ""    # 下载保存目录 (空 = 默认非 C 盘位置)
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
            self.cache_dir = d.get("cache_dir", "") or ""
            self.dock_hidden = [str(x) for x in (d.get("dock_hidden") or [])]
            try:
                self.dock_scale = min(1.5, max(0.8, float(d.get("dock_scale", 1.0))))
            except Exception:
                self.dock_scale = 1.0
            self.download_dir = d.get("download_dir", "") or ""
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

    def save(self):
        self._fix()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"server": self.server, "token": self.token, "user": self.user,
                           "seewo": self.seewo, "dock_on_start": self.dock_on_start,
                           "cache_dir": self.cache_dir, "download_dir": self.download_dir,
                           "dock_hidden": self.dock_hidden, "dock_scale": self.dock_scale}, f, ensure_ascii=False)
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


def _is_c_drive(path: str) -> bool:
    if not IS_WIN:
        return False
    drive = os.path.splitdrive(os.path.abspath(path))[0].upper()
    return drive in ("", "C:")


def default_storage_root() -> str:
    """默认存储根目录: 优先选择非系统盘 (D/E/F...), 避免学校电脑的冰点还原把缓存/课件清空。
    没有其它盘时才退回用户数据目录 (此时界面会提示老师手动改到其它盘)。"""
    if IS_WIN:
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if os.path.isdir(root) and os.access(root, os.W_OK):
                return os.path.join(root, "FLA")
    return data_dir()


def storage_warning() -> str:
    """若当前缓存/下载目录仍在 C 盘, 返回提示文字 (供设置页显示)"""
    for p in (cache_dir(), download_dir()):
        if _is_c_drive(p):
            return "当前存储位置在 C 盘：学校电脑开启冰点还原后课件会被清空，建议改到 D 盘等其它分区。"
    return ""


def _storage_dir(kind: str, custom: str) -> str:
    d = custom or os.path.join(default_storage_root(), kind)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.join(data_dir(), kind)
        os.makedirs(d, exist_ok=True)
    return d


def cache_dir() -> str:
    return _storage_dir("cache", cfg.cache_dir)


def download_dir() -> str:
    return _storage_dir("downloads", cfg.download_dir)


def cache_path(name: str) -> str:
    return os.path.join(cache_dir(), safe_name(name))


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
    return os.path.join(tempfile.gettempdir(), "FLA_update.exe")
