"""FLA - 文件路由: 上传 / 列表 / 流式下载(支持 Range) / 批注存取 / OnlyOffice 放映"""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import jwt
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .. import converter, db, msview
from ..deps import require_user
from ..security import secret

router = APIRouter(prefix="/api/files")

# ---- OnlyOffice 配置 (docker-compose 里启用) ----
OO_URL = os.environ.get("ONLYOFFICE_URL", "").rstrip("/")          # 浏览器访问路径, 如 /ds
OO_JWT = os.environ.get("ONLYOFFICE_JWT_SECRET", "").strip()       # 与 documentserver 共享的 JWT 密钥(可为空=不签名)
APP_INTERNAL = os.environ.get("APP_INTERNAL_URL", f"http://app:{os.environ.get('PORT', '8306')}").rstrip("/")  # documentserver 回源地址
OO_ENABLED = bool(OO_URL)

OFFICE = converter.CONVERTIBLE
IMAGES = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "svg"}
AUDIO = {"mp3", "wav", "ogg", "m4a", "aac", "flac"}
VIDEO = converter.VIDEO_EXTS   # v1.26: mkv/mov/wmv/avi 等由 ffmpeg 自动转码为 mp4
ALLOWED = OFFICE | IMAGES | AUDIO | VIDEO | {"pdf"}

EXTRA_MIME = {
    "svg": "image/svg+xml",
    "mkv": "video/x-matroska",
    "m4a": "audio/mp4",
    "m4v": "video/mp4",
    "flac": "audio/flac",
    "csv": "text/csv",
    "txt": "text/plain",
    "avi": "video/x-msvideo",
    "wmv": "video/x-ms-wmv",
    "flv": "video/x-flv",
    "mov": "video/quicktime",
    "mpg": "video/mpeg",
    "mpeg": "video/mpeg",
    "rm": "application/vnd.rn-realmedia",
    "rmvb": "application/vnd.rn-realmedia-vbr",
    "3gp": "video/3gpp",
    "ogv": "video/ogg",
}


def kind_of(ext: str) -> str:
    if ext in OFFICE:
        return "office"
    if ext in IMAGES:
        return "image"
    if ext in AUDIO:
        return "audio"
    if ext in VIDEO:
        return "video"
    if ext == "pdf":
        return "pdf"
    return "other"


def mime_of(row) -> str:
    ext = row["ext"]
    if ext in EXTRA_MIME:
        return EXTRA_MIME[ext]
    m, _ = mimetypes.guess_type("x." + ext)
    return m or "application/octet-stream"


def file_meta(row) -> dict:
    mf = []
    if row["missing_fonts"]:
        try:
            mf = json.loads(row["missing_fonts"])
        except Exception:
            mf = []
    return {
        "id": row["id"],
        "name": row["orig_name"],
        "ext": row["ext"],
        "kind": row["kind"],
        "size": row["size_bytes"],
        "status": row["status"],
        "pages": row["pages"],
        "error": row["error"],
        "missing_fonts": mf,
        "created_at": row["created_at"],
        "onlyoffice_enabled": OO_ENABLED and row["kind"] == "office",
        "anim": bool(row["anim"]),
    }


def _get_owned(fid: int, request: Request):
    u = require_user(request)
    row = db.q1("SELECT * FROM files WHERE id=?", (fid,))
    if not row:
        raise HTTPException(404, "文件不存在")
    if row["user_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "无权访问该文件")
    return u, row


def _stream(path: Path, range_header: str | None, mime: str, name: str, download: bool = False,
          ascii_name: str | None = None):
    size = path.stat().st_size
    disp = ("attachment" if download else "inline")
    disp += '; filename="' + (ascii_name or "file") + '"'
    disp += "; filename*=UTF-8''" + quote(name)
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": disp,
               "Cache-Control": "private, max-age=3600"}

    def gen(start, end):
        with open(path, "rb") as f:
            f.seek(start)
            remain = end - start + 1
            while remain > 0:
                chunk = f.read(min(1 << 20, remain))
                if not chunk:
                    break
                remain -= len(chunk)
                yield chunk

    m = re.match(r"bytes=(\d*)-(\d*)", range_header or "", re.I)
    if m and (m.group(1) or m.group(2)):
        start = int(m.group(1) or 0)
        end = int(m.group(2) or size - 1)
        start = max(0, min(start, size - 1))
        end = max(start, min(end, size - 1))
        return StreamingResponse(gen(start, end), status_code=206, media_type=mime,
                                 headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})
    return StreamingResponse(gen(0, size - 1), media_type=mime, headers=headers)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    u = require_user(request)
    orig = (file.filename or "file").strip()[:200]
    ext = Path(orig).suffix.lower().lstrip(".")
    if ext not in ALLOWED:
        raise HTTPException(400, f"不支持的文件类型 .{ext or '(无后缀)'}")
    max_mb = int(db.get_setting("max_upload_mb", "500"))
    used = db.used_bytes(u["id"])
    limit = min(max_mb * 1024 * 1024, u["quota_bytes"] - used)
    if limit <= 0:
        raise HTTPException(413, "存储空间不足, 请删除部分文件或联系管理员扩容")
    d = db.DATA / "uploads" / str(u["id"])
    d.mkdir(parents=True, exist_ok=True)
    stored = d / f"{int(time.time())}_{secrets.token_hex(6)}.{ext}"
    size = 0
    try:
        with open(stored, "wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "文件超出大小限制或存储空间不足")
                out.write(chunk)
    except HTTPException:
        stored.unlink(missing_ok=True)
        raise
    if size == 0:
        stored.unlink(missing_ok=True)
        raise HTTPException(400, "空文件")
    kind = kind_of(ext)
    # v1.26: Office 转 PDF / 非通用视频格式转 mp4 都算"转换中"
    need_tx = kind == "video" and converter.needs_transcode(ext)
    status = "converting" if (kind == "office" or need_tx) else "ready"
    pages = 0
    if kind == "pdf":
        try:
            from pypdf import PdfReader
            pages = len(PdfReader(str(stored)).pages)
        except Exception:
            pages = 0
    cur = db.ex("INSERT INTO files(user_id,orig_name,stored_path,ext,kind,size_bytes,status,pages,share_token,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (u["id"], orig, str(stored), ext, kind, size, status, pages,
                 secrets.token_hex(16), db.now()))
    fid = cur.lastrowid
    if kind == "office" or need_tx:
        converter.enqueue(fid)
    return file_meta(db.q1("SELECT * FROM files WHERE id=?", (fid,)))


def _share_path(row):
    """该文件的公开直链路径(不含 origin).
    v1.17: FYX 开头纯 ASCII UID + 扩展名, 文件名不进 URL
    (微软 Office 在线预览抓取器对非 ASCII 路径会 404)."""
    tok = row["share_token"]
    if not tok or not tok.startswith("FYX"):
        tok = "FYX" + secrets.token_hex(8)
        db.ex("UPDATE files SET share_token=? WHERE id=?", (tok, row["id"]))
    return "/api/files/share/" + tok + "." + (row["ext"] or "bin")


@router.api_route("/share/{uid}", methods=["GET", "HEAD"])
def share_uid(uid: str, request: Request):
    """v1.17 公开直链: /api/files/share/FYXxxxxxxxxxxxxxxxx.pptx (纯 ASCII).
    同时响应 HEAD(微软 Office 抓取器先 HEAD 探测)."""
    m = re.match(r"^(FYX[0-9a-f]{16})(?:\.([A-Za-z0-9]{1,8}))?$", uid)
    if not m:
        raise HTTPException(400, "非法链接")
    row = db.q1("SELECT * FROM files WHERE share_token=?", (m.group(1),))
    if not row or not row["stored_path"]:
        raise HTTPException(404, "文件不存在或已删除")
    aname = m.group(1) + "." + (m.group(2) or row["ext"] or "bin")
    path = Path(row["stored_path"])
    if request.method == "HEAD":
        return Response(headers={
            "Content-Length": str(path.stat().st_size),
            "Content-Type": mime_of(row),
            "Content-Disposition": 'inline; filename="' + aname + '"',
            "Accept-Ranges": "bytes"})
    return _stream(path, request.headers.get("range"), mime_of(row),
                   row["orig_name"], ascii_name=aname)


@router.get("/share/{token}/{fname}")
def share_raw(token: str, fname: str, request: Request):
    """公开直链(免登录): 持有链接即可下载原文件.
    用途: 微软 Office 在线查看器等服务需要直接抓取文件. 链接不可猜测, 可随时换库重置."""
    if not re.match(r"^[0-9a-f]{32}$", token):
        raise HTTPException(400, "非法链接")
    row = db.q1("SELECT * FROM files WHERE share_token=?", (token,))
    if not row or not row["stored_path"]:
        raise HTTPException(404, "文件不存在或已删除")
    return _stream(Path(row["stored_path"]), request.headers.get("range"),
                   mime_of(row), row["orig_name"])


def _public_base(request: Request) -> str:
    """公开访问基地址: 管理后台设置 > deploy/.env(PUBLIC_BASE_URL) > 当前请求 origin.

    v1.27: 部署脚本会把边缘网关的对外域名写进 PUBLIC_BASE_URL,
    于是微软在线放映拿到的直链天然是 https://域名 (满足微软"域名+80/443"的要求)。
    """
    return msview.public_base(request)


@router.get("/{fid}/share-link")
def share_link(fid: int, request: Request):
    """返回本文件的公开直链(需登录, 懒生成令牌).
    v1.18: direct=完整直链(公开基地址+路径); ms_ok=微软 viewer 可用性
    (微软要求: 域名访问且端口 80/443, IP 直连或 8080 等端口会被拒)."""
    _, row = _get_owned(fid, request)
    path = _share_path(row)
    direct = _public_base(request) + path
    return {"path": path, "direct": direct, "ms_ok": msview.ms_ok(direct)}


@router.get("/{fid}/ms-view")
def ms_view(fid: int, request: Request):
    """v1.27 微软在线视图对接信息: 直链 + 每页深链模板 + 幻灯宽高比 + 真实 slide id.

    前端据此实现【画布随页切换】: 翻页 = 换 iframe.src(同一 src 只改定位参数),
    我方页码即真相, 板书层严格跟着走; 微软侧转换缓存可复用, 不必重新转换。
    """
    _, row = _get_owned(fid, request)
    ext = (row["ext"] or "").lower()
    direct = _public_base(request) + _share_path(row)
    fam = msview.ms_family(ext)
    size = msview.slide_size(row["stored_path"]) if fam == "ppt" else None
    aspect = round(size[0] / size[1], 6) if size and size[1] else 0.0
    if not aspect and fam == "ppt":
        # 退一步: 用动画清单里的页面尺寸(转换时已解析过)
        try:
            f = _anim_base(row) / "anim.json"
            if f.exists():
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("slideW") and d.get("slideH"):
                    aspect = round(d["slideW"] / d["slideH"], 6)
        except Exception:
            pass
    ids = msview.slide_ids(row["stored_path"], _anim_base(row)) if fam == "ppt" else []
    pages = int(row["pages"] or 0)
    if fam == "ppt" and ids and pages != len(ids):
        pages = len(ids)                      # slide id 列表就是权威页数
    url1 = msview.embed_url(direct, ext, 1, ids[0] if ids else 0, aspect)
    if fam in ("ppt", "word"):
        # 深链模板: 前端把 {n} 换成页码(1-based)、{id} 换成该页真实 slide id
        tpl = msview.embed_url(direct, ext, 987654, 876543, aspect)
        tpl = (tpl.replace("wdStartOn=987654", "wdStartOn={n}")
                  .replace("wdSlideId=876543", "wdSlideId={id}"))
        if "{n}" not in tpl:
            tpl = url1
    else:
        tpl = url1
    return {
        "provider": "ms",
        "family": fam,
        "ext": ext,
        "direct": direct,
        "ms_ok": msview.ms_ok(direct),
        "pages": pages or 1,
        "aspect": aspect or (1.777778 if fam == "ppt" else 0.0),
        "slide_ids": ids,
        "url": url1,
        "url_tpl": tpl,
        "deep_link": bool(fam in ("ppt", "word")),
        "converted": bool(row["pdf_path"]) and row["status"] == "ready",
    }


@router.get("")
def list_files(request: Request):
    u = require_user(request)
    rows = db.q("SELECT * FROM files WHERE user_id=? ORDER BY id DESC", (u["id"],))
    return [file_meta(r) for r in rows]


@router.get("/{fid}/meta")
def meta(fid: int, request: Request):
    _, row = _get_owned(fid, request)
    return file_meta(row)


@router.get("/{fid}/raw")
def raw(fid: int, request: Request):
    _, row = _get_owned(fid, request)
    if not row["stored_path"]:
        raise HTTPException(400, "白板文件没有原始附件")
    path = Path(row["stored_path"])
    mime = mime_of(row)
    # v1.26: 视频优先播放转码后的 mp4(浏览器兼容), 下载/分享仍走原文件
    if row["kind"] == "video" and row["media_path"]:
        mp4 = Path(row["media_path"])
        if mp4.exists():
            path, mime = mp4, "video/mp4"
    return _stream(path, request.headers.get("range"), mime, row["orig_name"])


@router.get("/{fid}/download")
def download(fid: int, request: Request):
    _, row = _get_owned(fid, request)
    return _stream(Path(row["stored_path"]), request.headers.get("range"), mime_of(row), row["orig_name"], download=True)


@router.get("/{fid}/pdf")
def pdf(fid: int, request: Request):
    _, row = _get_owned(fid, request)
    if row["kind"] == "pdf":
        # 直接上传的 PDF: 原文件就是 PDF, 直接流式返回
        return _stream(Path(row["stored_path"]), request.headers.get("range"), "application/pdf",
                       Path(row["orig_name"]).stem + ".pdf")
    if row["kind"] != "office":
        raise HTTPException(400, "该文件不是 Office 文档")
    if row["status"] != "ready" or not row["pdf_path"]:
        raise HTTPException(409, "文档尚未转换完成")
    return _stream(Path(row["pdf_path"]), request.headers.get("range"), "application/pdf",
                   Path(row["orig_name"]).stem + ".pdf")


@router.get("/{fid}/anim")
def anim_manifest(fid: int, request: Request):
    """放映动画清单(v1.12). 无动画文件返回空清单而非404, 便于前端统一处理."""
    _, row = _get_owned(fid, request)
    base = _anim_base(row)
    f = base / "anim.json"
    if row["kind"] == "office" and row["anim"] and f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"v": 1, "slideW": 12192000, "slideH": 6858000, "pages": []}


@router.get("/{fid}/bgpdf")
def bg_pdf(fid: int, request: Request):
    """动画页背景 PDF(已抠掉动画元素, 元素由前端 HTML/CSS 重绘)."""
    _, row = _get_owned(fid, request)
    if row["kind"] != "office" or not row["anim"]:
        raise HTTPException(404, "无动画背景")
    f = _anim_base(row) / "bg.pdf"
    if not f.exists():
        raise HTTPException(404, "无动画背景")
    return _stream(f, request.headers.get("range"), "application/pdf",
                   Path(row["orig_name"]).stem + "-bg.pdf")


@router.get("/{fid}/anim-media/{name}")
def anim_media(fid: int, name: str, request: Request):
    """动画元素图片素材."""
    _, row = _get_owned(fid, request)
    if not re.match(r"^[A-Za-z0-9_.-]+$", name) or ".." in name:
        raise HTTPException(400, "非法路径")
    f = _anim_base(row) / "anim_media" / name
    if row["kind"] != "office" or not row["anim"] or not f.exists():
        raise HTTPException(404, "素材不存在")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}.get(
        f.suffix.lstrip(".").lower(), "application/octet-stream")
    return _stream(f, request.headers.get("range"), mime, name)


def _anim_base(row):
    """动画产物目录 = 转换 PDF 所在目录."""
    if row["pdf_path"]:
        return Path(row["pdf_path"]).parent
    return db.DATA / "converted" / str(row["id"])


@router.patch("/{fid}")
def rename_file(fid: int, request: Request, body: dict):
    """重命名课件/白板"""
    _, row = _get_owned(fid, request)
    new_name = str(body.get("name", "")).strip()
    if not new_name:
        raise HTTPException(400, "课件名称不能为空")
    db.ex("UPDATE files SET orig_name=? WHERE id=?", (new_name, fid))
    return {"ok": True, "name": new_name}


@router.delete("/{fid}")
def delete_file(fid: int, request: Request):
    _, row = _get_owned(fid, request)
    if row["stored_path"]:
        Path(row["stored_path"]).unlink(missing_ok=True)
    if row["pdf_path"]:
        Path(row["pdf_path"]).unlink(missing_ok=True)
        shutil.rmtree(db.DATA / "converted" / str(fid), ignore_errors=True)
    db.ex("DELETE FROM files WHERE id=?", (fid,))
    db.ex("DELETE FROM annotations WHERE file_id=?", (fid,))
    return {"ok": True}


@router.post("/{fid}/retry")
def retry(fid: int, request: Request):
    _, row = _get_owned(fid, request)
    if row["kind"] != "office":
        raise HTTPException(400, "该文件无需转换")
    db.ex("UPDATE files SET status='converting', error=NULL WHERE id=?", (fid,))
    converter.enqueue(fid)
    return file_meta(db.q1("SELECT * FROM files WHERE id=?", (fid,)))


@router.post("/board")
def create_board(request: Request, body: dict | None = None):
    """新建空白白板(无限画布), 创建后直接进入授课"""
    u = require_user(request)
    name = ""
    if isinstance(body, dict):
        name = str(body.get("name", "")).strip()
    if not name:
        name = "白板 " + time.strftime("%m-%d %H:%M")
    cur = db.ex("INSERT INTO files(user_id,orig_name,stored_path,ext,kind,size_bytes,status,pages,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (u["id"], name[:200], "", "board", "board", 0, "ready", 1, db.now()))
    fid = cur.lastrowid
    return file_meta(db.q1("SELECT * FROM files WHERE id=?", (fid,)))


# ---------------- OnlyOffice 动画放映 ----------------
OO_DOC_TYPE = {}
for _e in ("ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp", "dps"):
    OO_DOC_TYPE[_e] = "slide"
for _e in ("doc", "docx", "dot", "dotx", "rtf", "txt", "odt", "wps"):
    OO_DOC_TYPE[_e] = "word"
for _e in ("xls", "xlsx", "csv", "ods", "et"):
    OO_DOC_TYPE[_e] = "cell"


def _oo_sig(fid: int, exp: int) -> str:
    return hmac.new(secret().encode(), f"{fid}:{exp}".encode(), hashlib.sha256).hexdigest()[:40]


@router.get("/{fid}/oo-download")
def oo_download(fid: int, exp: int, sig: str):
    """供 OnlyOffice 文档服务器回源拉取文件(带签名免登录, 限时)"""
    if exp < int(time.time()):
        raise HTTPException(410, "链接已过期，请重新打开放映")
    if not hmac.compare_digest(_oo_sig(fid, exp), sig or ""):
        raise HTTPException(403, "签名无效")
    row = db.q1("SELECT * FROM files WHERE id=?", (fid,))
    if not row:
        raise HTTPException(404, "文件不存在")
    if not row["stored_path"]:
        raise HTTPException(404, "该文件没有原始附件")
    return _stream(Path(row["stored_path"]), None, mime_of(row), row["orig_name"])


@router.get("/{fid}/onlyoffice/verify")
def oo_verify(fid: int, request: Request):
    """放映前自检: 用当前密钥向 documentserver 发一次真实签名请求, 判断密钥是否一致"""
    _u, row = _get_owned(fid, request)
    if not OO_ENABLED:
        return {"ok": False, "detail": "OnlyOffice 未启用"}
    if row["kind"] != "office":
        return {"ok": False, "detail": "仅 Office 文档支持放映"}
    import urllib.error
    import urllib.request
    base = OO_URL if OO_URL.startswith("http") else "http://documentserver"
    body = {"async": False, "key": "flaverify", "filetype": "txt",
            "url": "http://127.0.0.1:9/none.txt", "outputtype": "pdf"}
    if OO_JWT:
        body["token"] = jwt.encode(body, OO_JWT, algorithm="HS256")
    try:
        req = urllib.request.Request(base.rstrip("/") + "/converter",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=10)
            resp = r.read(400).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            resp = e.read(400).decode("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "detail": "无法连接 documentserver: " + str(e)[:120]}
    if "token" in resp.lower():
        return {"ok": False, "detail": "JWT 密钥不一致（DS 报 Unsupported token）。在服务器上重跑 sudo bash install.sh 会自动校准密钥"}
    return {"ok": True, "detail": resp[:160]}


@router.get("/{fid}/onlyoffice/config")
def oo_config(fid: int, request: Request):
    """生成 OnlyOffice 查看器(含动画放映)的签名配置"""
    u, row = _get_owned(fid, request)
    if not OO_ENABLED:
        raise HTTPException(503, "OnlyOffice 未启用（需要在服务器 docker-compose 中开启）")
    if row["kind"] != "office":
        raise HTTPException(400, "仅 Office 文档支持放映")
    exp = int(time.time()) + 6 * 3600
    sig = _oo_sig(fid, exp)
    cfg = {
        "documentType": OO_DOC_TYPE.get(row["ext"], "word"),
        "document": {
            "fileType": row["ext"],
            "key": f"edu{fid}_{exp // 3600}",
            "title": row["orig_name"],
            "url": f"{APP_INTERNAL}/api/files/{fid}/oo-download?exp={exp}&sig={sig}",
            "permissions": {"edit": False, "download": False, "print": False},
        },
        "editorConfig": {
            "mode": "view",
            "lang": "zh",
            "user": {"id": str(u["id"]), "name": u["nickname"]},
            "customization": {
                "uiTheme": "theme-classic-light",
                "compactHeader": True,
                "hideRightMenu": True,
            },
        },
    }
    if OO_JWT:
        cfg["token"] = jwt.encode(cfg, OO_JWT, algorithm="HS256")
    return {"api_url": OO_URL + "/web-apps/apps/api/documents/api.js", "config": cfg}


# ---------------- 批注 ----------------
class AnnIn(BaseModel):
    pages: list = []
    strokes: dict = {}
    bb: dict | None = None
    ms: dict | None = None      # v1.27: 微软放映舞台状态(板书区域/同步模式/附加板书页数)


@router.get("/{fid}/annotations")
def get_ann(fid: int, request: Request):
    _, row = _get_owned(fid, request)
    r = db.q1("SELECT data FROM annotations WHERE file_id=?", (fid,))
    if not r:
        return {"pages": None, "strokes": {}}
    try:
        return json.loads(r["data"])
    except Exception:
        return {"pages": None, "strokes": {}}


@router.put("/{fid}/annotations")
def put_ann(fid: int, body: AnnIn, request: Request):
    _, row = _get_owned(fid, request)
    payload = json.dumps({"pages": body.pages, "strokes": body.strokes,
                          **({"bb": body.bb} if body.bb else {}),
                          **({"ms": body.ms} if body.ms else {})}, ensure_ascii=False)
    if len(payload) > 30 * 1024 * 1024:
        raise HTTPException(413, "批注数据过大")
    db.ex("INSERT INTO annotations(file_id,user_id,data,updated_at) VALUES(?,?,?,?) "
          "ON CONFLICT(file_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
          (fid, row["user_id"], payload, db.now()))
    return {"ok": True}
