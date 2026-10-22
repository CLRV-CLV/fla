"""FLA - 管理后台路由: 用户/邀请码/设置/统计"""
import re
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import db
from ..deps import require_admin, user_public
from ..routers.files import file_meta

router = APIRouter(prefix="/api/admin")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_code():
    return "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(3))


def invite_public(r):
    status = "active"
    if r["used_count"] >= r["max_uses"]:
        status = "used"
    elif r["expires_at"] and r["expires_at"] < db.now():
        status = "expired"
    return {"id": r["id"], "code": r["code"], "max_uses": r["max_uses"],
            "used_count": r["used_count"], "expires_at": r["expires_at"],
            "note": r["note"], "created_at": r["created_at"], "status": status,
            "group_id": r["group_id"] if "group_id" in r.keys() else 1}


# ---------------- 统计 ----------------
@router.get("/stats")
def stats(request: Request):
    require_admin(request)
    users = db.q1("SELECT COUNT(*) AS c FROM users")["c"]
    teachers = db.q1("SELECT COUNT(*) AS c FROM users WHERE is_teacher=1")["c"]
    files = db.q1("SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes),0) AS s FROM files")
    invites = db.q1("SELECT COUNT(*) AS c, COALESCE(SUM(used_count),0) AS u FROM invite_codes")
    converting = db.q1("SELECT COUNT(*) AS c FROM files WHERE status='converting'")["c"]
    failed = db.q1("SELECT COUNT(*) AS c FROM files WHERE status='failed'")["c"]
    recent = db.q("SELECT * FROM users ORDER BY id DESC LIMIT 8")
    # v1.26: 社区统计
    threads = db.q1("SELECT COUNT(*) AS c FROM forum_threads")["c"]
    posts = db.q1("SELECT COUNT(*) AS c FROM forum_posts")["c"]
    msgs = db.q1("SELECT COUNT(*) AS c FROM chat_messages WHERE deleted=0")["c"]
    anns = db.q1("SELECT COUNT(*) AS c FROM announcements WHERE active=1")["c"]
    return {"users": users, "teachers": teachers, "files": files["c"],
            "storage": files["s"], "invites": invites["c"], "invites_used": invites["u"],
            "converting": converting, "failed": failed,
            "threads": threads, "forum_posts": posts, "chat_messages": msgs, "announcements": anns,
            "recent_users": [user_public(r) for r in recent]}


# ---------------- 用户管理 ----------------
@router.get("/users")
def list_users(request: Request, q: str = "", page: int = 1, size: int = 20):
    require_admin(request)
    size = max(1, min(size, 100))
    page = max(1, page)
    args = []
    where = ""
    if q.strip():
        where = "WHERE username LIKE ? OR nickname LIKE ?"
        kw = f"%{q.strip()}%"
        args = [kw, kw]
    total = db.q1(f"SELECT COUNT(*) AS c FROM users {where}", args)["c"]
    rows = db.q(f"SELECT * FROM users {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                args + [size, (page - 1) * size])
    return {"total": total, "items": [user_public(r) for r in rows]}


class AdminUserIn(BaseModel):
    nickname: str | None = None
    signature: str | None = None
    role: str | None = None
    is_teacher: bool | None = None
    cert_title: str | None = None
    cert_icon: str | None = None      # v1.26: 认证图标
    cert_color: str | None = None     # v1.26: 认证颜色 (#rrggbb)
    chat_banned: bool | None = None   # v1.26: 聊天禁言
    quota_mb: int | None = None
    group_id: int | None = None        # v3.6: 用户组


CERT_ICONS = {"medal", "star", "crown", "award", "shield", "heart", "zap", "gem", "trophy", "flag"}
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@router.put("/users/{uid}")
def update_user(uid: int, body: AdminUserIn, request: Request):
    me = require_admin(request)
    row = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "用户不存在")
    if body.nickname is not None:
        nk = body.nickname.strip()
        if not (1 <= len(nk) <= 32):
            raise HTTPException(400, "昵称需 1-32 个字符")
        db.ex("UPDATE users SET nickname=? WHERE id=?", (nk, uid))
    if body.signature is not None:
        if len(body.signature) > 200:
            raise HTTPException(400, "签名最多 200 字")
        db.ex("UPDATE users SET signature=? WHERE id=?", (body.signature, uid))
    if body.role in ("user", "admin"):
        if row["id"] == me["id"] and body.role != "admin":
            raise HTTPException(400, "不能取消自己的管理员权限")
        db.ex("UPDATE users SET role=? WHERE id=?", (body.role, uid))
    if body.is_teacher is not None:
        db.ex("UPDATE users SET is_teacher=? WHERE id=?", (1 if body.is_teacher else 0, uid))
    if body.cert_title is not None:
        if len(body.cert_title) > 30:
            raise HTTPException(400, "称号最多 30 字")
        db.ex("UPDATE users SET cert_title=? WHERE id=?", (body.cert_title, uid))
    if body.cert_icon is not None:
        ic = body.cert_icon.strip()
        if ic and ic not in CERT_ICONS:
            raise HTTPException(400, "不支持的认证图标")
        db.ex("UPDATE users SET cert_icon=? WHERE id=?", (ic, uid))
    if body.cert_color is not None:
        cc = body.cert_color.strip().lower()
        if cc and not _COLOR_RE.match(cc):
            raise HTTPException(400, "认证颜色需为 #rrggbb 格式")
        db.ex("UPDATE users SET cert_color=? WHERE id=?", (cc, uid))
    if body.chat_banned is not None:
        db.ex("UPDATE users SET chat_banned=? WHERE id=?", (1 if body.chat_banned else 0, uid))
    if body.group_id is not None:
        if not db.q1("SELECT id FROM user_groups WHERE id=?", (body.group_id,)):
            raise HTTPException(400, "用户组不存在")
        db.ex("UPDATE users SET group_id=? WHERE id=?", (body.group_id, uid))
    if body.quota_mb is not None:
        if not (1 <= body.quota_mb <= 1000000):
            raise HTTPException(400, "空间配额需在 1MB - 1TB 之间")
        db.ex("UPDATE users SET quota_bytes=? WHERE id=?", (body.quota_mb * 1024 * 1024, uid))
    return user_public(db.q1("SELECT * FROM users WHERE id=?", (uid,)))


AVATAR_EXT = {"png", "jpg", "jpeg", "webp", "gif"}


@router.post("/users/{uid}/avatar")
async def admin_upload_avatar(uid: int, request: Request, file: UploadFile = File(...)):
    require_admin(request)
    row = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "用户不存在")
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in AVATAR_EXT:
        raise HTTPException(400, "头像仅支持 png / jpg / webp / gif")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "头像不能超过 8MB")
    name = f"u{uid}_{secrets.token_hex(6)}.{ext}"
    (db.DATA / "avatars" / name).write_bytes(data)
    if row["avatar"]:
        old = db.DATA / "avatars" / Path(row["avatar"]).name
        if re.match(r"^[A-Za-z0-9_.-]+$", old.name) and old.exists():
            old.unlink()
    url = f"/api/avatars/{name}"
    db.ex("UPDATE users SET avatar=? WHERE id=?", (url, uid))
    return {"avatar": url}


@router.post("/users/{uid}/reset_password")
def reset_password(uid: int, request: Request):
    require_admin(request)
    row = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "用户不存在")
    pw = "YJT" + secrets.token_urlsafe(6)
    from ..security import hash_password
    db.ex("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
          (hash_password(pw), uid))
    return {"password": pw}


@router.delete("/users/{uid}")
def delete_user(uid: int, request: Request):
    me = require_admin(request)
    if uid == me["id"]:
        raise HTTPException(400, "不能删除自己")
    row = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "用户不存在")
    for f in db.q("SELECT * FROM files WHERE user_id=?", (uid,)):
        Path(f["stored_path"]).unlink(missing_ok=True)
        if f["pdf_path"]:
            Path(f["pdf_path"]).unlink(missing_ok=True)
            shutil.rmtree(db.DATA / "converted" / str(f["id"]), ignore_errors=True)
    db.ex("DELETE FROM files WHERE user_id=?", (uid,))
    db.ex("DELETE FROM annotations WHERE user_id=?", (uid,))
    db.ex("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


@router.get("/users/{uid}/files")
def user_files(uid: int, request: Request):
    require_admin(request)
    rows = db.q("SELECT * FROM files WHERE user_id=? ORDER BY id DESC", (uid,))
    return [file_meta(r) for r in rows]


# ---------------- 邀请码 ----------------
@router.get("/invites")
def list_invites(request: Request):
    require_admin(request)
    rows = db.q("SELECT * FROM invite_codes ORDER BY id DESC LIMIT 500")
    return [invite_public(r) for r in rows]


class InviteIn(BaseModel):
    code: str = ""
    max_uses: int = 1
    duration_hours: float | None = None
    note: str = ""
    group_id: int = 1          # v3.6: 通过该邀请码注册的用户所属组(默认: 邀请码用户组)


@router.post("/invites")
def create_invite(body: InviteIn, request: Request):
    me = require_admin(request)
    code = body.code.strip()
    if not code:
        code = gen_code()
    if not re.match(r"^[A-Za-z0-9_-]{2,64}$", code):
        raise HTTPException(400, "邀请码仅限 2-64 位字母/数字/-_")
    if db.q1("SELECT id FROM invite_codes WHERE code=?", (code,)):
        raise HTTPException(400, "邀请码已存在")
    if not (1 <= body.max_uses <= 9999):
        raise HTTPException(400, "可用次数需在 1-9999 之间")
    expires = _expires(body.duration_hours)
    if not db.q1("SELECT id FROM user_groups WHERE id=?", (body.group_id,)):
        raise HTTPException(400, "用户组不存在")
    db.ex("INSERT INTO invite_codes(code,max_uses,expires_at,note,created_by,created_at,group_id)"
          " VALUES(?,?,?,?,?,?,?)",
          (code, body.max_uses, expires, body.note.strip()[:100], me["id"], db.now(), body.group_id))
    return invite_public(db.q1("SELECT * FROM invite_codes WHERE code=?", (code,)))


class BatchIn(BaseModel):
    count: int = 10
    max_uses: int = 1
    duration_hours: float | None = None
    note: str = ""
    prefix: str = ""
    group_id: int = 1


@router.post("/invites/batch")
def batch_invite(body: BatchIn, request: Request):
    me = require_admin(request)
    if not (1 <= body.count <= 500):
        raise HTTPException(400, "批量生成数量需在 1-500 之间")
    if not (1 <= body.max_uses <= 9999):
        raise HTTPException(400, "可用次数需在 1-9999 之间")
    prefix = body.prefix.strip().upper()
    if prefix and not re.match(r"^[A-Z0-9-]{1,12}$", prefix):
        raise HTTPException(400, "前缀仅限 1-12 位大写字母/数字/-")
    expires = _expires(body.duration_hours)
    if not db.q1("SELECT id FROM user_groups WHERE id=?", (body.group_id,)):
        raise HTTPException(400, "用户组不存在")
    items = []
    for _ in range(body.count):
        for _try in range(20):
            code = (prefix + "-" if prefix else "") + gen_code()
            if not db.q1("SELECT id FROM invite_codes WHERE code=?", (code,)):
                break
        else:
            continue
        db.ex("INSERT INTO invite_codes(code,max_uses,expires_at,note,created_by,created_at,group_id)"
              " VALUES(?,?,?,?,?,?,?)",
              (code, body.max_uses, expires, body.note.strip()[:100], me["id"], db.now(), body.group_id))
        items.append(invite_public(db.q1("SELECT * FROM invite_codes WHERE code=?", (code,))))
    return {"items": items}


@router.delete("/invites/{iid}")
def delete_invite(iid: int, request: Request):
    require_admin(request)
    db.ex("DELETE FROM invite_codes WHERE id=?", (iid,))
    return {"ok": True}


def _expires(duration_hours):
    if not duration_hours or duration_hours <= 0:
        return None
    from datetime import datetime, timedelta
    return (datetime.now() + timedelta(hours=duration_hours)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 系统设置 ----------------
@router.get("/settings")
def get_settings(request: Request):
    require_admin(request)
    return {
        "default_quota_mb": int(db.get_setting("default_quota_mb", "500")),
        "max_upload_mb": int(db.get_setting("max_upload_mb", "500")),
        "registration_open": db.get_setting("registration_open", "1") == "1",
        "public_base_url": db.get_setting("public_base_url", ""),
        "site_bg": db.get_setting("site_bg", ""),
        "login_bg": db.get_setting("login_bg", ""),
        "client_bg": db.get_setting("client_bg", ""),
        "ui_opacity": int(db.get_setting("ui_opacity", "100") or 100),
        "forum_enabled": db.get_setting("forum_enabled", "1") == "1",
        "chat_enabled": db.get_setting("chat_enabled", "1") == "1",
        "allow_group_create": db.get_setting("allow_group_create", "0") == "1",
        "toolbar_keep": db.get_setting("toolbar_keep", "1") == "1",
    }


class SettingsIn(BaseModel):
    default_quota_mb: int | None = None
    max_upload_mb: int | None = None
    registration_open: bool | None = None
    public_base_url: str | None = None
    site_bg: str | None = None
    login_bg: str | None = None
    client_bg: str | None = None
    ui_opacity: int | None = None           # 3.5: 界面面板不透明度 30-100
    forum_enabled: bool | None = None      # v1.26: 论坛开关
    chat_enabled: bool | None = None       # v1.26: 聊天开关
    allow_group_create: bool | None = None  # v1.26: 允许用户创建群组
    toolbar_keep: bool | None = None        # v1.27: 放映时工具栏常驻(不自动收回)


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request):
    require_admin(request)
    if body.toolbar_keep is not None:
        db.set_setting("toolbar_keep", "1" if body.toolbar_keep else "0")
    if body.default_quota_mb is not None:
        if not (1 <= body.default_quota_mb <= 1000000):
            raise HTTPException(400, "默认空间需在 1MB - 1TB 之间")
        db.set_setting("default_quota_mb", str(body.default_quota_mb))
    if body.max_upload_mb is not None:
        if not (1 <= body.max_upload_mb <= 1000000):
            raise HTTPException(400, "单文件上限需在 1MB - 1TB 之间")
        db.set_setting("max_upload_mb", str(body.max_upload_mb))
    if body.registration_open is not None:
        db.set_setting("registration_open", "1" if body.registration_open else "0")
    if body.public_base_url is not None:
        v = body.public_base_url.strip().rstrip("/")
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise HTTPException(400, "公开访问地址需以 http:// 或 https:// 开头")
        if len(v) > 200:
            raise HTTPException(400, "公开访问地址过长")
        db.set_setting("public_base_url", v)
    for key in ("site_bg", "login_bg", "client_bg"):
        val = getattr(body, key)
        if val is not None:
            v2 = val.strip()
            if len(v2) > 500:
                raise HTTPException(400, "背景设置过长(最多500字符)")
            db.set_setting(key, v2)
    if body.ui_opacity is not None:
        db.set_setting("ui_opacity", str(max(30, min(100, int(body.ui_opacity)))))
    if body.forum_enabled is not None:
        db.set_setting("forum_enabled", "1" if body.forum_enabled else "0")
    if body.chat_enabled is not None:
        db.set_setting("chat_enabled", "1" if body.chat_enabled else "0")
    if body.allow_group_create is not None:
        db.set_setting("allow_group_create", "1" if body.allow_group_create else "0")
    return get_settings(request)


# ================================ 3.5 外观: 背景图上传 ================================
THEME_SLOTS = ("site_bg", "login_bg", "client_bg")
THEME_EXT = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}


@router.post("/theme/{slot}")
async def upload_theme(slot: str, request: Request, file: UploadFile = File(...)):
    """上传背景图 → 保存到 data/theme, 并直接设为该位置的背景"""
    require_admin(request)
    if slot not in THEME_SLOTS:
        raise HTTPException(400, "未知位置")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in THEME_EXT:
        raise HTTPException(400, "只支持 png / jpg / webp / gif / bmp 图片")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "图片最大 15MB")
    d = db.DATA / "theme"
    d.mkdir(parents=True, exist_ok=True)
    old = db.get_setting(slot, "")
    name = f"{slot}_{secrets.token_hex(6)}.{ext}"
    (d / name).write_bytes(data)
    if old.startswith("/api/auth/theme/"):
        try:
            (d / Path(old).name).unlink()
        except Exception:
            pass
    url = "/api/auth/theme/" + name
    db.set_setting(slot, url)
    return {"ok": True, "url": url}


# ================================ v1.26 公告管理 ================================
class AnnIn(BaseModel):
    title: str
    content: str = ""
    level: str = "info"        # info | warn | imp
    scope: str = "global"      # global | user
    target_uid: int | None = None
    active: bool = True


def _ann_out(r):
    reads = db.q1("SELECT COUNT(*) AS c FROM ann_reads WHERE aid=?", (r["id"],))["c"]
    target = None
    if r["scope"] == "user" and r["target_uid"]:
        tu = db.q1("SELECT username, nickname FROM users WHERE id=?", (r["target_uid"],))
        target = (tu["nickname"] or tu["username"]) if tu else "已注销"
    return {"id": r["id"], "title": r["title"], "content": r["content"], "level": r["level"],
            "scope": r["scope"], "target": target, "active": bool(r["active"]),
            "reads": reads, "created_at": r["created_at"], "updated_at": r["updated_at"]}


@router.get("/announcements")
def list_announcements(request: Request):
    require_admin(request)
    rows = db.q("SELECT * FROM announcements ORDER BY id DESC LIMIT 300")
    return [_ann_out(r) for r in rows]


@router.post("/announcements")
def create_announcement(body: AnnIn, request: Request):
    me = require_admin(request)
    title = body.title.strip()[:100]
    if len(title) < 2:
        raise HTTPException(400, "标题太短")
    if body.level not in ("info", "warn", "imp"):
        raise HTTPException(400, "公告级别无效")
    if body.scope not in ("global", "user"):
        raise HTTPException(400, "公告范围无效")
    if body.scope == "user":
        if not body.target_uid or not db.q1("SELECT id FROM users WHERE id=?", (body.target_uid,)):
            raise HTTPException(400, "定向公告需要选择有效用户")
    now = db.now()
    cur = db.ex("INSERT INTO announcements(title,content,level,scope,target_uid,active,created_by,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (title, body.content.strip()[:4000], body.level, body.scope,
                 body.target_uid if body.scope == "user" else None,
                 1 if body.active else 0, me["id"], now))
    return {"id": cur.lastrowid}


@router.patch("/announcements/{aid}")
def update_announcement(aid: int, body: AnnIn, request: Request):
    require_admin(request)
    row = db.q1("SELECT * FROM announcements WHERE id=?", (aid,))
    if not row:
        raise HTTPException(404, "公告不存在")
    if body.title is not None:
        title = body.title.strip()[:100]
        if len(title) < 2:
            raise HTTPException(400, "标题太短")
        db.ex("UPDATE announcements SET title=? WHERE id=?", (title, aid))
    if body.content is not None:
        db.ex("UPDATE announcements SET content=? WHERE id=?", (body.content.strip()[:4000], aid))
    if body.level in ("info", "warn", "imp"):
        db.ex("UPDATE announcements SET level=? WHERE id=?", (body.level, aid))
    if body.scope in ("global", "user"):
        if body.scope == "user":
            if not body.target_uid or not db.q1("SELECT id FROM users WHERE id=?", (body.target_uid,)):
                raise HTTPException(400, "定向公告需要选择有效用户")
            db.ex("UPDATE announcements SET scope='user', target_uid=? WHERE id=?", (body.target_uid, aid))
        else:
            db.ex("UPDATE announcements SET scope='global', target_uid=NULL WHERE id=?", (aid,))
    if body.active is not None:
        db.ex("UPDATE announcements SET active=? WHERE id=?", (1 if body.active else 0, aid))
    db.ex("UPDATE announcements SET updated_at=? WHERE id=?", (db.now(), aid))
    return _ann_out(db.q1("SELECT * FROM announcements WHERE id=?", (aid,)))


@router.delete("/announcements/{aid}")
def delete_announcement(aid: int, request: Request):
    require_admin(request)
    if not db.q1("SELECT id FROM announcements WHERE id=?", (aid,)):
        raise HTTPException(404, "公告不存在")
    db.ex("DELETE FROM announcements WHERE id=?", (aid,))
    db.ex("DELETE FROM ann_reads WHERE aid=?", (aid,))
    return {"ok": True}


# ================================ v1.26 论坛板块管理 ================================
class BoardIn(BaseModel):
    name: str
    descr: str = ""
    sort: int = 0


@router.post("/forum/boards")
def create_board(body: BoardIn, request: Request):
    require_admin(request)
    name = body.name.strip()[:30]
    if len(name) < 2:
        raise HTTPException(400, "板块名太短")
    cur = db.ex("INSERT INTO forum_boards(name,descr,sort,created_at) VALUES(?,?,?,?)",
                (name, body.descr.strip()[:100], body.sort, db.now()))
    return {"id": cur.lastrowid}


@router.patch("/forum/boards/{bid}")
def update_board(bid: int, body: BoardIn, request: Request):
    require_admin(request)
    if not db.q1("SELECT id FROM forum_boards WHERE id=?", (bid,)):
        raise HTTPException(404, "板块不存在")
    if body.name is not None:
        name = body.name.strip()[:30]
        if len(name) < 2:
            raise HTTPException(400, "板块名太短")
        db.ex("UPDATE forum_boards SET name=? WHERE id=?", (name, bid))
    if body.descr is not None:
        db.ex("UPDATE forum_boards SET descr=? WHERE id=?", (body.descr.strip()[:100], bid))
    if body.sort is not None:
        db.ex("UPDATE forum_boards SET sort=? WHERE id=?", (body.sort, bid))
    return {"ok": True}


@router.delete("/forum/boards/{bid}")
def delete_board(bid: int, request: Request):
    require_admin(request)
    if not db.q1("SELECT id FROM forum_boards WHERE id=?", (bid,)):
        raise HTTPException(404, "板块不存在")
    for t in db.q("SELECT id FROM forum_threads WHERE board_id=?", (bid,)):
        db.ex("DELETE FROM forum_posts WHERE thread_id=?", (t["id"],))
    db.ex("DELETE FROM forum_threads WHERE board_id=?", (bid,))
    db.ex("DELETE FROM forum_boards WHERE id=?", (bid,))
    return {"ok": True}
