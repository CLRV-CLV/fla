"""FLA - 文档转换服务: LibreOffice headless -> PDF + 字体检测/自动补齐"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path

from . import db

# 可被 LibreOffice 转换为 PDF 的类型
CONVERTIBLE = {
    "ppt", "pptx", "pps", "ppsx", "pot", "potx",
    "doc", "docx", "dot", "dotx", "rtf", "txt",
    "xls", "xlsx", "csv",
    "odt", "ods", "odp",
    "wps", "et", "dps",  # WPS 专有格式, 转换会给出友好提示
}
WPS_NATIVE = {"wps", "et", "dps"}

# v1.26: 浏览器不能普遍直读的视频格式 → ffmpeg(开源) 转码为 mp4 再播放
WEB_VIDEO = {"mp4", "webm", "m4v"}
TRANSCODABLE = {"mkv", "mov", "avi", "wmv", "flv", "mpg", "mpeg", "rm", "rmvb", "ts", "m2ts", "3gp", "ogv"}
VIDEO_EXTS = WEB_VIDEO | TRANSCODABLE

_jobs = queue.Queue()
_started = False

# LibreOffice 转换超时(秒): 大课件/图片多时 420s 容易不够, 可用 FLA_SOFFICE_TIMEOUT 调整
SOFFICE_TIMEOUT = int(os.environ.get("FLA_SOFFICE_TIMEOUT", "900"))


def lo_env():
    """LibreOffice 的进程环境.

    宝塔(Python 项目, 进程属主 www)/精简容器里 HOME 可能不存在或不可写,
    soffice 会直接失败 (Failed to create "home" / javaldx 报错)。
    这里统一给一个可写的 HOME + 环境, 让 headless 转换在任何部署方式下都能跑。
    """
    env = os.environ.copy()
    home = Path(env.get("HOME") or "/tmp")
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".fla_write_test"
        probe.write_text("1")
        probe.unlink()
    except Exception:
        home = Path("/tmp/fla-lohome")     # HOME 不可写 → 退到 /tmp
        try:
            home.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    env["HOME"] = str(home)
    env.setdefault("LANG", "C.UTF-8")
    env["SAL_USE_VCLPLUGIN"] = "svp"        # 无显示器环境必需
    env["SAL_DISABLE_OPENCL"] = "1"
    env.pop("SAL_USE_VCLPLUGIN_DEFAULT", None)
    return env


def lo_error(r, extra: str = "") -> str:
    """把 soffice 的输出翻译成能看懂、能照做的中文错误(保留原始后半段方便排查)。"""
    out = ((getattr(r, "stderr", "") or "") + "\n" + (getattr(r, "stdout", "") or "")).strip()
    low = out.lower()
    code = getattr(r, "returncode", "?")
    hint = ""
    if any(k in low for k in ("no such file", "not found", "command not found")):
        hint = "服务器缺少 LibreOffice(or 找不到 soffice 命令)"
    elif "permission denied" in low or "failed to create" in low and "home" in low:
        hint = "LibreOffice 无法写入用户配置目录(权限/家目录问题)"
    elif any(k in low for k in ("could not be loaded", "general input/output error",
                                "no export filter", "input error", "corrupt",
                                "format error", "source file could not be loaded")):
        hint = "文件已损坏或格式不被支持(可用 PowerPoint/WPS 打开后另存为 .pptx 重试)"
    elif any(k in low for k in ("password", "encrypted")):
        hint = "文档有密码保护, 请先取消密码再上传"
    elif "timed out" in low:
        hint = "转换超时(课件过大或图片过多)"
    else:
        hint = "LibreOffice 转换失败"
    tail = out[-600:].strip()
    msg = f"{hint} (退出码 {code})"
    if extra:
        msg = f"{extra}: " + msg
    if tail:
        msg += " | " + tail
    return msg[:800]


def _run_lo(args, timeout=None):
    """执行 soffice, 统一环境与超时; 超时也转成 CompletedProcess 便于统一报错。"""
    timeout = timeout or SOFFICE_TIMEOUT
    try:
        return subprocess.run([soffice_bin()] + args, capture_output=True, text=True,
                              timeout=timeout, env=lo_env())
    except subprocess.TimeoutExpired as e:
        class _R:
            returncode = "timeout"
            stdout = (e.stdout or b"").decode("utf-8", "ignore") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr or b"").decode("utf-8", "ignore") if isinstance(e.stderr, bytes) else (e.stderr or "")
        _R.stderr += f"\ntimed out after {timeout}s"
        return _R()
    except FileNotFoundError:
        class _R:
            returncode = 127
            stdout = ""
            stderr = "soffice: no such file or directory"
        return _R()


def self_check() -> dict:
    """启动自检: LibreOffice 是否可用(结果打印到日志, 也供 bt.sh doctor 参考)。"""
    b = soffice_bin()
    info = {"soffice": b or "", "timeout": SOFFICE_TIMEOUT, "ok": False, "detail": ""}
    if not b:
        info["detail"] = "未找到 soffice/libreoffice, Office 课件将无法转换"
        return info
    try:
        r = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=30, env=lo_env())
        info["version"] = (r.stdout or r.stderr or "").strip().splitlines()[0] if (r.stdout or r.stderr) else ""
        info["ok"] = r.returncode == 0
        info["detail"] = info.get("version", "")
    except Exception as e:
        info["detail"] = str(e)[:200]
    return info


def requeue_stuck():
    """启动时把上个进程遗留在「转换中」的任务重新排队.

    宝塔/Python 项目模式重启进程时, 正在转的课件会永远卡在「转换中…」,
    这里统一重新入队, 避免用户只能手动点重试。
    """
    try:
        rows = db.q("SELECT id FROM files WHERE status='converting' ORDER BY id")
    except Exception:
        return 0
    for r in rows:
        enqueue(r["id"])
    if rows:
        print(f"[converter] 重启后重新排队 {len(rows)} 个未完成的转换任务")
    return len(rows)


def start_worker():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker, daemon=True, name="converter").start()


def enqueue(file_id):
    _jobs.put(file_id)


def _worker():
    while True:
        fid = _jobs.get()
        try:
            _convert(fid)
        except Exception as e:  # noqa
            print("[converter] error:", fid, e)
        finally:
            # v1.26 内存优化: 每个任务结束即回收(转换期峰值数百MB, 不回收会常驻)
            import gc
            gc.collect()


def soffice_bin():
    return shutil.which("soffice") or shutil.which("libreoffice")


def ffmpeg_bin():
    return shutil.which("ffmpeg")


def needs_transcode(ext: str) -> bool:
    """非浏览器通用格式(mkv/mov/wmv/avi/...) → 需 ffmpeg 转码"""
    return ext in TRANSCODABLE


def _transcode_video(fid, row):
    """v1.26: ffmpeg 转码为 H.264/mp4 (faststart), 失败不阻塞(保留原文件尝试原生播放)"""
    src = Path(row["stored_path"])
    outdir = db.DATA / "converted" / str(fid)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "play.mp4"
    try:
        if not ffmpeg_bin():
            raise RuntimeError("服务器未安装 ffmpeg, 已保留原文件直接播放(部分格式可能无法播放)")
        cmd = [ffmpeg_bin(), "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if not out.exists() or out.stat().st_size < 1024:
            raise RuntimeError("视频转码失败(源格式可能损坏或不受支持), 已保留原文件")
        db.ex("UPDATE files SET status='ready', media_path=?, error=NULL WHERE id=?", (str(out), fid))
        print(f"[converter] file {fid} video -> mp4 ok ({out.stat().st_size} bytes)")
    except Exception as e:
        print("[converter] transcode fail:", fid, e)
        db.ex("UPDATE files SET status='ready', media_path=NULL, error=? WHERE id=?", (str(e)[:300], fid))


def _convert(fid):
    row = db.q1("SELECT * FROM files WHERE id=?", (fid,))
    if not row:
        return
    if row["kind"] == "video" and needs_transcode(row["ext"]):
        db.ex("UPDATE files SET status='converting', error=NULL WHERE id=?", (fid,))
        _transcode_video(fid, row)
        return
    db.ex("UPDATE files SET status='converting', error=NULL WHERE id=?", (fid,))
    src = Path(row["stored_path"])
    outdir = db.DATA / "converted" / str(fid)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.pdf"):
        old.unlink()
    try:
        if not soffice_bin():
            raise RuntimeError("服务器未安装 LibreOffice(soffice), 无法转换 Office 课件。宝塔用户请执行: bash bt.sh deps  或  apt/yum install libreoffice-impress")
        if row["ext"] in WPS_NATIVE:
            raise RuntimeError("WPS 专有格式(.wps/.et/.dps)暂无法自动转换, 请在 WPS 中另存为 docx/xlsx/pptx 后重新上传")
        missing = detect_missing_fonts(src, row["ext"])
        db.ex("UPDATE files SET missing_fonts=? WHERE id=?", (json.dumps(missing, ensure_ascii=False), fid))
        # v1.13: PPT 家族先做 OMML 公式预处理(LibreOffice 不支持 PPTX 公式, 会整块丢弃)
        work = src
        pre = None
        if row["kind"] == "office" and row["ext"] in ("pptx", "ppsx", "potx"):
            try:
                from . import omml_img
                pre = omml_img.preprocess(src, outdir)
            except Exception as e:
                print("[converter] omml preprocess fail:", fid, e)
            if pre:
                work = pre
        elif row["ext"] in ("ppt", "pps", "pot"):
            # 旧版二进制格式: 先转 pptx, 再预处理
            try:
                from . import omml_img
                tmpx = outdir / "_src_conv.pptx"
                r0 = _run_lo(["--headless", "--norestore", "--nologo", "--nolockcheck",
                              "-env:UserInstallation=file:///tmp/lo_anim",
                              "--convert-to", "pptx", "--outdir", str(outdir), str(src)], timeout=300)
                cand = outdir / (src.stem + ".pptx")
                if r0.returncode == 0 and cand.exists():
                    cand.rename(tmpx)
                    pre = omml_img.preprocess(tmpx, outdir)
                    if pre:
                        work = pre
                    else:
                        work = tmpx
                if work == src and tmpx.exists():
                    tmpx.unlink()
            except Exception as e:
                print("[converter] legacy omml preprocess fail:", fid, e)
        r = None
        for attempt in (1, 2):
            profile = f"-env:UserInstallation=file:///tmp/lo_profile_{fid}_{attempt}"
            r = _run_lo(["--headless", "--norestore", "--nologo", "--nolockcheck",
                         profile, "--convert-to", "pdf", "--outdir", str(outdir), str(work)])
            pdf = outdir / (work.stem + ".pdf")
            if not pdf.exists() and work is not src:
                pdf = outdir / (src.stem + ".pdf")  # 兜底: 原名输出
            if r.returncode == 0 and pdf.exists() and pdf.stat().st_size > 0:
                break
            # 第一次失败常见于首次初始化用户配置/残留锁, 换个干净 profile 再试一次
            print(f"[converter] file {fid} convert attempt {attempt} failed: \n{(r.stderr or r.stdout or '')[-800:]}")
            if not soffice_bin():
                break
        if r is None or r.returncode != 0 or not pdf.exists() or pdf.stat().st_size == 0:
            raise RuntimeError(lo_error(r))
        pages = _pdf_pages(pdf)
        print(f"[converter] file {fid} -> pdf ok, {pages} pages")
        # v1.12: PPT 家族提取放映动画清单(失败不影响主流程)
        # v1.13: status=ready 移到动画解析之后, 避免客户端在动画清单未落盘时读到半成品
        anim_flag = 0
        if row["kind"] == "office" and row["ext"] in ("pptx", "ppsx", "potx", "ppt", "pps", "pot"):
            try:
                from . import pptx_anim
                m = pptx_anim.build_and_store(work, outdir, ext=row["ext"], main_pages=pages)
                anim_flag = 1 if m else 0
            except Exception as e:
                print("[converter] anim parse fail:", fid, e)
        db.ex("UPDATE files SET status='ready', pdf_path=?, pages=?, anim=? WHERE id=?",
              (str(pdf), pages, anim_flag, fid))
        print(f"[converter] file {fid} ready, {pages} pages (anim={anim_flag})")
    except Exception as e:
        db.ex("UPDATE files SET status='failed', error=? WHERE id=?", (str(e)[:400], fid))


def _pdf_pages(p: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(p)).pages)
    except Exception:
        try:
            data = p.read_bytes()
            n = len(re.findall(rb"/Type\s*/Page(?!s)", data))
            return max(1, n)
        except Exception:
            return 1


# ---------------- 字体检测与自动补齐 ----------------
FONT_PATTERNS = {
    "pptx": [r'typeface="([^"]+)"'],
    "potx": [r'typeface="([^"]+)"'],
    "ppsx": [r'typeface="([^"]+)"'],
    "docx": [r'w:(?:ascii|hAnsi|eastAsia|cs)="([^"]+)"'],
    "dotx": [r'w:(?:ascii|hAnsi|eastAsia|cs)="([^"]+)"'],
    "xlsx": [r'<name val="([^"]+)"'],
}
GENERIC = {"", "+mn-lt", "+mj-lt", "+mn-ea", "+mj-ea", "+mn-cs", "+mj-cs", "minorfont", "majorfont"}


def installed_families():
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True,
                             timeout=30).stdout
        fams = set()
        for line in out.splitlines():
            for f in line.split(","):
                f = f.strip()
                if f:
                    fams.add(f.lower())
        return fams
    except Exception:
        return set()


def detect_missing_fonts(path: Path, ext: str):
    """解析文档 XML 中引用的字体名, 返回服务器未安装的字体列表"""
    pats = FONT_PATTERNS.get(ext, [])
    if not pats:
        return []
    wanted = set()
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if not n.endswith(".xml"):
                    continue
                try:
                    txt = z.read(n).decode("utf-8", "ignore")
                except Exception:
                    continue
                for pat in pats:
                    for m in re.findall(pat, txt):
                        m = m.strip()
                        if m and m.lower() not in GENERIC:
                            wanted.add(m)
    except Exception:
        return []
    have = installed_families()
    if not have:
        return []
    return [f for f in sorted(wanted) if f.lower() not in have][:40]


# 仅自动下载"开源"字体; 商业字体(微软雅黑等)不能合法分发, 由 fontconfig 替换规则兜底, 不会出现方框
AUTO_FONTS = [
    ("Noto Sans CJK SC",
     "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"),
    ("Noto Sans CJK SC",
     "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf"),
    ("Noto Serif CJK SC",
     "https://github.com/notofonts/noto-cjk/raw/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Regular.otf"),
    ("LXGW WenKai",
     "https://github.com/lxgw/LxgwWenKai/releases/latest/download/LXGWWenKai-Regular.ttf"),
    ("STIX Two Math",
     "https://github.com/google/fonts/raw/main/ofl/stixtwomath/STIXTwoMath-Regular.ttf"),
]


def ensure_fonts():
    """启动时检查关键开源字体, 缺失则自动下载(尽力而为, 不阻塞服务)"""
    have = installed_families()
    if not have:
        return  # 无 fontconfig 的环境跳过
    need = [(fam, url) for fam, url in AUTO_FONTS if fam.lower() not in have]
    if not need:
        print("[fonts] 关键字体齐全")
        return
    print("[fonts] 检测到缺少字体, 尝试自动下载:", sorted({f for f, _ in need}))
    import urllib.request
    base = None
    for d in ("/usr/share/fonts/edu-auto", str(db.DATA / "fonts")):
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            base = Path(d)
            break
        except Exception:
            continue
    if base is None:
        return
    for fam, url in need:
        try:
            dest = base / (fam.replace(" ", "") + "_" + url.rsplit("/", 1)[-1])
            if dest.exists():
                continue
            print("[fonts] 下载", fam, "...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            print("[fonts] 已安装", fam)
        except Exception as e:
            print("[fonts] 下载失败(跳过):", fam, e)
    try:
        subprocess.run(["fc-cache", "-f"], capture_output=True, timeout=120)
    except Exception:
        pass
