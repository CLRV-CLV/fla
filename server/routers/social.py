"""FLA v1.26 - 社区: 论坛(板块/帖子/回复)
管理员可编辑/删除任意内容, 权限(允许发帖等)由系统设置控制

v1.27: 聊天升级为微信级(私聊/未读/已读回执/免打扰/置顶/群昵称/图片文件语音/
表情回应/@提醒/正在输入), 代码迁到 routers/chat.py, 本模块只留论坛。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import db
from ..deps import require_user, user_card as _user_card

router = APIRouter(prefix="/api")


def _clean(s, n):
    s = (s or "").strip()
    return s[:n]


# ================================ 论坛 ================================

@router.get("/forum/boards")
def forum_boards(request: Request):
    require_user(request)
    rows = db.q("SELECT b.*, (SELECT COUNT(*) FROM forum_threads t WHERE t.board_id=b.id) AS threads"
                " FROM forum_boards b ORDER BY b.sort, b.id")
    return {"items": [{"id": r["id"], "name": r["name"], "descr": r["descr"],
                       "threads": r["threads"]} for r in rows]}


@router.get("/forum/threads")
def forum_threads(request: Request, board: int = 0, page: int = 1, q: str = ""):
    u = require_user(request)
    size = 15
    page = max(1, page)
    where, args = "", []
    if board:
        where = "WHERE t.board_id=?"
        args.append(board)
    kw = (q or "").strip()[:50]
    if kw:
        where = (where + " AND " if where else "WHERE ") + "(t.title LIKE ? OR t.content LIKE ?)"
        args += [f"%{kw}%", f"%{kw}%"]
    total = db.q1(f"SELECT COUNT(*) AS c FROM forum_threads t {where}", tuple(args))["c"]
    rows = db.q(
        f"SELECT t.* FROM forum_threads t {where}"
        " ORDER BY t.pinned DESC, t.updated_at DESC LIMIT ? OFFSET ?", tuple(args + [size, (page - 1) * size]))
    items = []
    for r in rows:
        cnt = db.q1("SELECT COUNT(*) AS c FROM forum_posts WHERE thread_id=?", (r["id"],))["c"]
        last = db.q1("SELECT created_at FROM forum_posts WHERE thread_id=? ORDER BY id DESC LIMIT 1", (r["id"],))
        items.append({"id": r["id"], "board_id": r["board_id"], "title": r["title"],
                      "pinned": bool(r["pinned"]), "locked": bool(r["locked"]),
                      "replies": cnt, "author": _user_card(r["user_id"]),
                      "created_at": r["created_at"], "updated_at": r["updated_at"],
                      "last_reply_at": (last["created_at"] if last else r["created_at"]),
                      "mine": r["user_id"] == u["id"]})
    return {"items": items, "total": total, "page": page, "pages": max(1, -(-total // size))}


class ThreadIn(BaseModel):
    board_id: int
    title: str
    content: str


@router.post("/forum/threads")
def create_thread(body: ThreadIn, request: Request):
    u = require_user(request)
    if db.get_setting("forum_enabled", "1") != "1" and u["role"] != "admin":
        raise HTTPException(403, "论坛已由管理员关闭")
    board = db.q1("SELECT id FROM forum_boards WHERE id=?", (body.board_id,))
    if not board:
        raise HTTPException(404, "板块不存在")
    title = _clean(body.title, 100)
    content = _clean(body.content, 8000)
    if len(title) < 2:
        raise HTTPException(400, "标题太短")
    if not content:
        raise HTTPException(400, "内容不能为空")
    now = db.now()
    cur = db.ex("INSERT INTO forum_threads(board_id,user_id,title,content,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?)", (body.board_id, u["id"], title, content, now, now))
    return {"id": cur.lastrowid}


def _get_thread(tid):
    t = db.q1("SELECT * FROM forum_threads WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404, "帖子不存在")
    return t


@router.get("/forum/threads/{tid}")
def thread_detail(tid: int, request: Request, page: int = 1):
    u = require_user(request)
    t = _get_thread(tid)
    size = 20
    page = max(1, page)
    total = db.q1("SELECT COUNT(*) AS c FROM forum_posts WHERE thread_id=?", (tid,))["c"]
    rows = db.q("SELECT * FROM forum_posts WHERE thread_id=? ORDER BY id LIMIT ? OFFSET ?",
                (tid, size, (page - 1) * size))
    posts = [{"id": r["id"], "content": r["content"], "author": _user_card(r["user_id"]),
              "edited": bool(r["edited"]), "edited_by_admin": bool(r["edited_by"]) and r["user_id"] != r["edited_by"],
              "created_at": r["created_at"], "mine": r["user_id"] == u["id"]} for r in rows]
    return {"id": t["id"], "board_id": t["board_id"], "title": t["title"], "content": t["content"],
            "pinned": bool(t["pinned"]), "locked": bool(t["locked"]),
            "author": _user_card(t["user_id"]), "created_at": t["created_at"],
            "mine": t["user_id"] == u["id"], "posts": posts,
            "total": total, "page": page, "pages": max(1, -(-total // size))}


class PostIn(BaseModel):
    content: str


@router.post("/forum/threads/{tid}/posts")
def create_post(tid: int, body: PostIn, request: Request):
    u = require_user(request)
    if db.get_setting("forum_enabled", "1") != "1" and u["role"] != "admin":
        raise HTTPException(403, "论坛已由管理员关闭")
    t = _get_thread(tid)
    if t["locked"] and u["role"] != "admin":
        raise HTTPException(403, "帖子已锁定")
    content = _clean(body.content, 8000)
    if not content:
        raise HTTPException(400, "回复内容不能为空")
    db.ex("INSERT INTO forum_posts(thread_id,user_id,content,created_at) VALUES(?,?,?,?)",
          (tid, u["id"], content, db.now()))
    db.ex("UPDATE forum_threads SET updated_at=? WHERE id=?", (db.now(), tid))
    return {"ok": True}


class ThreadPatch(BaseModel):
    title: str | None = None
    content: str | None = None
    pinned: bool | None = None
    locked: bool | None = None


@router.patch("/forum/threads/{tid}")
def patch_thread(tid: int, body: ThreadPatch, request: Request):
    u = require_user(request)
    t = _get_thread(tid)
    admin = u["role"] == "admin"
    if t["user_id"] != u["id"] and not admin:
        raise HTTPException(403, "只能编辑自己的帖子")
    if t["locked"] and not admin:
        raise HTTPException(403, "帖子已锁定")
    if body.pinned is not None and not admin:
        raise HTTPException(403, "只有管理员能置顶")
    if body.title is not None:
        title = _clean(body.title, 100)
        if len(title) < 2:
            raise HTTPException(400, "标题太短")
        db.ex("UPDATE forum_threads SET title=? WHERE id=?", (title, tid))
    if body.content is not None:
        db.ex("UPDATE forum_threads SET content=? WHERE id=?", (_clean(body.content, 8000), tid))
    if body.pinned is not None and admin:
        db.ex("UPDATE forum_threads SET pinned=? WHERE id=?", (1 if body.pinned else 0, tid))
    if body.locked is not None and admin:
        db.ex("UPDATE forum_threads SET locked=? WHERE id=?", (1 if body.locked else 0, tid))
    db.ex("UPDATE forum_threads SET updated_at=? WHERE id=?", (db.now(), tid))
    return {"ok": True}


@router.delete("/forum/threads/{tid}")
def delete_thread(tid: int, request: Request):
    u = require_user(request)
    t = _get_thread(tid)
    if t["user_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能删除自己的帖子")
    db.ex("DELETE FROM forum_posts WHERE thread_id=?", (tid,))
    db.ex("DELETE FROM forum_threads WHERE id=?", (tid,))
    return {"ok": True}


class PostPatch(BaseModel):
    content: str


@router.patch("/forum/posts/{pid}")
def patch_post(pid: int, body: PostPatch, request: Request):
    u = require_user(request)
    r = db.q1("SELECT * FROM forum_posts WHERE id=?", (pid,))
    if not r:
        raise HTTPException(404, "回复不存在")
    if r["user_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能编辑自己的回复")
    content = _clean(body.content, 8000)
    if not content:
        raise HTTPException(400, "内容不能为空")
    db.ex("UPDATE forum_posts SET content=?, edited=1, edited_by=? WHERE id=?", (content, u["id"], pid))
    return {"ok": True}


@router.delete("/forum/posts/{pid}")
def delete_post(pid: int, request: Request):
    u = require_user(request)
    r = db.q1("SELECT * FROM forum_posts WHERE id=?", (pid,))
    if not r:
        raise HTTPException(404, "回复不存在")
    if r["user_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能删除自己的回复")
    db.ex("DELETE FROM forum_posts WHERE id=?", (pid,))
    return {"ok": True}


# v1.27: 聊天(群组/私聊/未读/已读回执/附件/回应/@/正在输入)已升级为独立模块 → server/routers/chat.py
