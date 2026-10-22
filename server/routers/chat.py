"""FLA v1.27 - 聊天(微信级)

v1.26 的聊天只有"群 + 文本 + 2.5 秒轮询"。这一版按微信的使用习惯补齐:

  会话      私聊(1:1, 复用同一会话) / 群聊 / 官方大厅
  未读      每个会话独立未读数(chat_members.last_read) → 导航栏红点与角标
  已读回执  群里显示"已读 n/m", 私聊显示"已读/未读"(同样由 last_read 推出, 零额外写)
  免打扰    muted=1 → 只显小红点不显数字(微信同款)
  置顶      pinned=1 → 会话置顶
  群昵称    每个群可单独设置显示名
  消息类型  文本 / 图片 / 文件 / 语音(MediaRecorder 录制) / 系统提示
  引用回复  reply_to → 消息上方显示被引用的摘要
  表情回应  ❤ 👍 😂 🎉 👀 🙏 一键回应, 可撤销
  @提醒     @某人 → 未读列表标"[有人@我]", 只提醒真正被 @ 的成员
  正在输入  "对方正在输入…"(6 秒时效, 前端 2 秒节流上报)
  撤回      2 分钟内可撤回(管理员不限时), 撤回后显示"xx 撤回了一条消息"
  编辑      自己的消息可改(标"已编辑")
  群管理    改名/公告/邀请/踢人/转让群主/退群/解散
  搜索      全文搜索我有权限的会话记录
  历史分页  before=<id> 向上翻旧消息(微信式下拉加载)

全部走 HTTP 轮询(2–3 秒), 不依赖 WebSocket: 学校内网/代理环境更稳, 老浏览器也能用。
"""
from __future__ import annotations

import re
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..deps import require_user, user_card

router = APIRouter(prefix="/api/chat")

# ------------------------------------------------------------------ 常量 ---
TYPING_TTL = 6.0          # "正在输入"时效(秒)
RECALL_WINDOW = 120       # 撤回时限(秒) — 与微信一致
MSG_MAX = 2000            # 文本上限(比 v1.26 的 500 宽松, 支持粘贴讲义)
REACTIONS = {"❤", "👍", "👎", "😂", "🎉", "👀", "🙏", "🔥"}
IMG_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
AUD_EXT = {"webm", "ogg", "oga", "mp3", "m4a", "aac", "wav", "amr", "mp4"}
FILE_EXT = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "md", "zip",
            "rar", "7z", "csv", "json", "png", "jpg", "jpeg", "gif", "webp", "mp4", "mp3"}
LIMITS = {"image": 12 << 20, "audio": 8 << 20, "file": 200 << 20}
_last_send: dict[int, float] = {}      # uid → 上次发言时间(轻量防刷)


def _clean(s, n):
    return (s or "").strip()[:n]


def _att_dir(uid) -> Path:
    d = db.DATA / "chat" / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _member(rid, uid):
    return db.q1("SELECT * FROM chat_members WHERE room_id=? AND uid=? AND left_at IS NULL", (rid, uid))


def _members(rid):
    return db.q("SELECT * FROM chat_members WHERE room_id=? AND left_at IS NULL", (rid,))


def _room(rid):
    r = db.q1("SELECT * FROM chat_rooms WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "会话不存在")
    return r


def _can_see(r, u) -> bool:
    """官方大厅人人可见; 群/私聊仅成员与管理员可见"""
    if u["role"] == "admin" or r["official"]:
        return True
    return _member(r["id"], u["id"]) is not None


def _need_access(rid, u):
    r = _room(rid)
    if not _can_see(r, u):
        raise HTTPException(403, "你不是这个会话的成员")
    return r


def _chat_enabled(u) -> bool:
    return db.get_setting("chat_enabled", "1") == "1" or u["role"] == "admin"


def _banned(u) -> bool:
    return "chat_banned" in u.keys() and bool(u["chat_banned"])


def _dm_key(a, b):
    return f"{min(a, b)}-{max(a, b)}"


def _last_msg(rid):
    return db.q1("SELECT * FROM chat_messages WHERE room_id=? ORDER BY id DESC LIMIT 1", (rid,))


def _unread(rid, uid, last_read):
    row = db.q1("SELECT COUNT(*) AS c FROM chat_messages WHERE room_id=? AND id>? AND deleted=0 AND uid<>?",
                (rid, last_read or 0, uid))
    return int(row["c"] if row else 0)


def _typing(rid, exclude_uid):
    cut = time.time() - TYPING_TTL
    rows = db.q("SELECT uid FROM chat_typing WHERE room_id=? AND at>? AND uid<>?", (rid, cut, exclude_uid))
    return [r["uid"] for r in rows]


def _sys(rid, text):
    """群内系统提示(加入/退群/改名/撤回…), 微信同款灰条"""
    db.ex("INSERT INTO chat_messages(room_id,uid,content,kind,created_at) VALUES(?,0,?,'system',?)",
          (rid, _clean(text, 200), db.now()))


def _att_out(a):
    if not a:
        return None
    return {"id": a["id"], "kind": a["kind"], "name": a["name"], "mime": a["mime"],
            "size": a["size_bytes"], "dur_ms": a["dur_ms"],
            "url": f"/api/chat/attachments/{a['id']}"}


def _room_out(r, u):
    """会话列表里的一条(群/私聊统一结构)"""
    rid = r["id"]
    me = _member(rid, u["id"])
    last_read = me["last_read"] if me else 0
    lm = _last_msg(rid)
    ms = _members(rid)
    kind = r["kind"] if "kind" in r.keys() else "group"
    out = {
        "id": rid, "kind": kind, "official": bool(r["official"]),
        "name": r["name"], "avatar": r["avatar"] if "avatar" in r.keys() else "",
        "intro": r["intro"] if "intro" in r.keys() else "",
        "owner_id": r["owner_id"],
        "members": len(ms) + (1 if r["official"] else 0),
        "joined": bool(me) or bool(r["official"]),
        "muted": bool(me["muted"]) if me else False,
        "pinned": bool(me["pinned"]) if me else False,
        "my_nickname": (me["nickname"] if me else "") or "",
        "unread": _unread(rid, u["id"], last_read) if (me or r["official"]) else 0,
        "at_me": 0,
        "typing": [],
        "last": None,
        "last_id": lm["id"] if lm else 0,
        "peer": None,
        "created_at": r["created_at"],
    }
    if lm:
        who = user_card(lm["uid"]) if lm["uid"] else None
        out["last"] = {"id": lm["id"], "kind": lm["kind"], "deleted": bool(lm["deleted"]),
                       "content": "" if lm["deleted"] else lm["content"],
                       "uid": lm["uid"], "nickname": (who or {}).get("nickname", "系统"),
                       "created_at": lm["created_at"]}
    if kind == "dm":
        # 私聊: 对方是谁
        peer = next((m for m in ms if m["uid"] != u["id"]), None)
        if peer:
            out["peer"] = user_card(peer["uid"])
            if not out["name"]:
                out["name"] = out["peer"]["nickname"]
    if me or r["official"]:
        out["typing"] = _typing(rid, u["id"])
        row = db.q1("SELECT COUNT(*) AS c FROM chat_mentions WHERE uid=? AND room_id=? AND seen=0",
                    (u["id"], rid))
        out["at_me"] = int(row["c"] if row else 0)
    return out


def _reacts_of(msg_ids):
    if not msg_ids:
        return {}
    ph = ",".join("?" * len(msg_ids))
    rows = db.q(f"SELECT * FROM chat_reactions WHERE msg_id IN ({ph}) ORDER BY rowid", tuple(msg_ids))
    out = {}
    for r in rows:
        out.setdefault(r["msg_id"], []).append({"emoji": r["emoji"], "uid": r["uid"]})
    return out


def _atts_of(ids):
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    return {r["id"]: r for r in db.q(f"SELECT * FROM chat_attachments WHERE id IN ({ph})", tuple(ids))}


def _msg_out(m, u, read_map=None, atts=None, reacts=None, replies=None, room=None):
    """单条消息的完整载荷"""
    kind = m["kind"] if "kind" in m.keys() else "text"
    mine = m["uid"] == u["id"]
    out = {
        "id": m["id"], "room_id": m["room_id"], "uid": m["uid"], "kind": kind,
        "content": "" if m["deleted"] else m["content"],
        "deleted": bool(m["deleted"]), "edited": bool(m["edited"]),
        "edited_by_admin": bool(m["edited_by"]) and m["edited_by"] != m["uid"],
        "created_at": m["created_at"], "mine": mine,
        "author": user_card(m["uid"]) if m["uid"] else {"id": 0, "nickname": "系统", "avatar": "",
                                                        "username": "", "role": "user", "is_teacher": False,
                                                        "cert_title": "", "cert_icon": "", "cert_color": ""},
        "att": None, "reply": None, "reacts": [], "mentions": [],
        "can_recall": mine and not m["deleted"] and (
            _ts_age(m["created_at"]) <= RECALL_WINDOW or u["role"] == "admin"),
    }
    if room is not None and room["kind"] == "dm":
        out["nickname_in_room"] = out["author"]["nickname"]
    if atts:
        aid = m["att_id"] if "att_id" in m.keys() else None
        out["att"] = _att_out(atts.get(aid)) if aid else None
    if reacts:
        rs = reacts.get(m["id"]) or []
        grouped = {}
        for r in rs:
            grouped.setdefault(r["emoji"], []).append(r["uid"])
        out["reacts"] = [{"emoji": e, "uids": uids, "mine": u["id"] in uids} for e, uids in grouped.items()]
    if replies:
        rid_ = m["reply_to"] if "reply_to" in m.keys() else None
        src = replies.get(rid_) if rid_ else None
        if src:
            who = user_card(src["uid"])
            out["reply"] = {"id": src["id"], "nickname": who["nickname"], "kind": src["kind"],
                            "content": ("[已撤回]" if src["deleted"] else src["content"])[:120]}
    if read_map is not None and mine and not m["deleted"]:
        # 已读回执: 群里 n/m, 私聊 已读/未读
        out["read_count"] = sum(1 for uid, lr in read_map.items() if uid != m["uid"] and lr >= m["id"])
        out["read_total"] = max(0, len(read_map) - 1)
    return out


def _ts_age(ts_str) -> float:
    """数据库时间是本地 'YYYY-MM-DD HH:MM:SS', 与 db.now() 同源, 直接相减"""
    try:
        from datetime import datetime
        t = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - t).total_seconds()
    except Exception:
        return 1e9


def _read_map(rid):
    return {m["uid"]: (m["last_read"] or 0) for m in _members(rid)}


def _pack(rows, u, room):
    """把一页消息补全(附件/引用/回应/已读数)"""
    rmap = _read_map(room["id"])
    atts = _atts_of([r["att_id"] for r in rows if "att_id" in r.keys() and r["att_id"]])
    reacts = _reacts_of([r["id"] for r in rows])
    rids = [r["reply_to"] for r in rows if "reply_to" in r.keys() and r["reply_to"]]
    replies = {}
    if rids:
        ph = ",".join("?" * len(rids))
        replies = {r["id"]: r for r in db.q(f"SELECT * FROM chat_messages WHERE id IN ({ph})", tuple(rids))}
    ment = {}
    ph = ",".join("?" * len(rows)) if rows else "0"
    if rows:
        for r in db.q(f"SELECT * FROM chat_mentions WHERE msg_id IN ({ph})", tuple(x["id"] for x in rows)):
            ment.setdefault(r["msg_id"], []).append(r["uid"])
    out = [_msg_out(m, u, rmap, atts, reacts, replies, room) for m in rows]
    for o in out:
        o["mentions"] = ment.get(o["id"]) or []
    return out


# ============================================================ 未读 / 会话列表 ===

def _my_rooms(u):
    """我参与的所有会话: 官方大厅 + 我未退出的群/私聊"""
    rows = db.q("""SELECT r.* FROM chat_rooms r
                   LEFT JOIN chat_members m ON m.room_id=r.id AND m.uid=? AND m.left_at IS NULL
                   WHERE r.official=1 OR m.uid IS NOT NULL""", (u["id"],))
    # 官方大厅人人有份: 补一条成员行, 未读数/已读回执才有落点(只在缺失时写一次)
    for r in rows:
        if r["official"] and not db.q1("SELECT uid FROM chat_members WHERE room_id=? AND uid=?",
                                       (r["id"], u["id"])):
            db.ex("INSERT OR IGNORE INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)",
                  (r["id"], u["id"], db.now()))
    return rows


@router.get("/unread")
def unread(request: Request):
    """导航栏红点: 总未读(免打扰的会话只算"有点"不算数字)"""
    u = require_user(request)
    total, dot = 0, 0
    rooms = []
    for r in _my_rooms(u):
        me = _member(r["id"], u["id"])
        n = _unread(r["id"], u["id"], me["last_read"] if me else 0)
        if n:
            rooms.append({"id": r["id"], "unread": n, "muted": bool(me["muted"]) if me else False})
            if me and me["muted"]:
                dot += 1
            else:
                total += n
    return {"total": total, "dot": dot, "rooms": rooms, "server_time": db.now()}


@router.get("/inbox")
def inbox(request: Request):
    """会话列表(置顶优先, 其次按最后一条消息时间倒序)"""
    u = require_user(request)
    items = [_room_out(r, u) for r in _my_rooms(u)]
    items.sort(key=lambda x: (0 if x["pinned"] else 1, -(x["last_id"] or 0)))
    return {"items": items,
            "allow_create": db.get_setting("allow_group_create", "0") == "1" or u["role"] == "admin",
            "server_time": db.now()}


@router.get("/rooms")
def rooms(request: Request):
    """v1.26 兼容入口: 群列表(不含私聊)"""
    u = require_user(request)
    rows = db.q("SELECT * FROM chat_rooms WHERE official=1 OR kind='group' ORDER BY official DESC, id")
    return {"items": [_room_out(r, u) for r in rows],
            "allow_create": db.get_setting("allow_group_create", "0") == "1" or u["role"] == "admin"}


# ================================================================ 通讯录 ===

@router.get("/contacts")
def contacts(request: Request, q: str = "", limit: int = 200):
    """可选联系人(建群/邀请/@ 用): 教师与管理员靠前, 支持昵称/用户名模糊搜索.

    只返回公开小卡字段(不含手机号等隐私), 校园内部通讯录语义。"""
    u = require_user(request)
    limit = max(1, min(limit, 300))
    kw = _clean(q, 30)
    sql = ("SELECT id,username,nickname,avatar,role,is_teacher,cert_title,cert_icon,cert_color "
           "FROM users WHERE 1=1")
    args: list = []
    if kw:
        sql += " AND (nickname LIKE ? OR username LIKE ?)"
        args += [f"%{kw}%", f"%{kw}%"]
    sql += " ORDER BY role DESC, is_teacher DESC, nickname LIMIT ?"
    args += [limit]
    rows = db.q(sql, tuple(args))
    # 最近聊过的人排最前(微信"最近联系人"手感)
    recent = {r["uid"]: i for i, r in enumerate(db.q(
        "SELECT DISTINCT m.uid FROM chat_messages m JOIN chat_members c"
        " ON c.room_id=m.room_id AND c.uid=? WHERE m.uid<>? ORDER BY m.id DESC LIMIT 30",
        (u["id"], u["id"])))}
    out = []
    for r in rows:
        card = {"id": r["id"], "username": r["username"], "nickname": r["nickname"] or r["username"],
                "avatar": r["avatar"], "role": r["role"], "is_teacher": bool(r["is_teacher"]),
                "cert_title": r["cert_title"], "cert_icon": r["cert_icon"], "cert_color": r["cert_color"],
                "self": r["id"] == u["id"]}
        out.append(card)
    out.sort(key=lambda x: (0 if x["self"] else 1, recent.get(x["id"], 99), x["id"]))
    return {"items": out}


# ================================================================== 私聊 ===

@router.post("/dm/{uid}")
def open_dm(uid: int, request: Request):
    """取得(或创建)与某人的私聊会话 — 微信"发消息"入口"""
    u = require_user(request)
    if not _chat_enabled(u):
        raise HTTPException(403, "聊天区已由管理员关闭")
    if uid == u["id"]:
        raise HTTPException(400, "不能和自己私聊")
    if not db.q1("SELECT id FROM users WHERE id=?", (uid,)):
        raise HTTPException(404, "用户不存在")
    key = _dm_key(u["id"], uid)
    r = db.q1("SELECT * FROM chat_rooms WHERE kind='dm' AND dm_key=?", (key,))
    if not r:
        cur = db.ex("INSERT INTO chat_rooms(name,owner_id,official,kind,dm_key,created_at)"
                    " VALUES('',?,0,'dm',?,?)", (min(u["id"], uid), key, db.now()))
        rid = cur.lastrowid
        for x in (u["id"], uid):
            db.ex("INSERT OR IGNORE INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)",
                  (rid, x, db.now()))
        r = _room(rid)
    else:
        rid = r["id"]
        # 曾经退过(理论上私聊不退) → 重新激活
        for x in (u["id"], uid):
            db.ex("INSERT INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)"
                  " ON CONFLICT(room_id,uid) DO UPDATE SET left_at=NULL", (rid, x, db.now()))
    return {"id": rid, "room": _room_out(r, u)}


# ================================================================== 群聊 ===

class RoomIn(BaseModel):
    name: str
    intro: str = ""
    members: list[int] = []


@router.post("/rooms")
def create_room(body: RoomIn, request: Request):
    u = require_user(request)
    if _banned(u):
        raise HTTPException(403, "你已被管理员禁言")
    if not _chat_enabled(u):
        raise HTTPException(403, "聊天区已由管理员关闭")
    if db.get_setting("allow_group_create", "0") != "1" and u["role"] != "admin":
        raise HTTPException(403, "管理员未开放创建群组")
    name = _clean(body.name, 30)
    if len(name) < 2:
        raise HTTPException(400, "群组名太短")
    cur = db.ex("INSERT INTO chat_rooms(name,owner_id,official,kind,intro,created_at)"
                " VALUES(?,?,0,'group',?,?)", (name, u["id"], _clean(body.intro, 200), db.now()))
    rid = cur.lastrowid
    db.ex("INSERT OR IGNORE INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)", (rid, u["id"], db.now()))
    added = _add_members(rid, [x for x in body.members if x and x != u["id"]], u)
    if added:
        _sys(rid, f"{u['nickname'] or u['username']} 邀请了 " + "、".join(added) + " 加入群聊")
    return {"id": rid}


def _add_members(rid, uids, by):
    names = []
    for x in uids[:200]:
        row = db.q1("SELECT id,nickname,username FROM users WHERE id=?", (int(x),))
        if not row:
            continue
        db.ex("INSERT INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)"
              " ON CONFLICT(room_id,uid) DO UPDATE SET left_at=NULL", (rid, row["id"], db.now()))
        names.append(row["nickname"] or row["username"])
    return names


class InviteIn(BaseModel):
    uids: list[int] = []


@router.post("/rooms/{rid}/invite")
def invite(rid: int, body: InviteIn, request: Request):
    u = require_user(request)
    r = _need_access(rid, u)
    if r["official"] and u["role"] != "admin":
        raise HTTPException(403, "官方大厅无需邀请")
    if not _member(rid, u["id"]):
        raise HTTPException(403, "你不是这个群的成员")
    added = _add_members(rid, body.uids, u)
    if added:
        _sys(rid, f"{u['nickname'] or u['username']} 邀请了 " + "、".join(added) + " 加入群聊")
    return {"ok": True, "added": added}


@router.post("/rooms/{rid}/join")
def join_room(rid: int, request: Request):
    u = require_user(request)
    r = _room(rid)
    if r["kind"] == "dm" and u["role"] != "admin":
        raise HTTPException(403, "私聊会话不能加入")
    db.ex("INSERT INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)"
          " ON CONFLICT(room_id,uid) DO UPDATE SET left_at=NULL", (rid, u["id"], db.now()))
    return {"ok": True}


@router.post("/rooms/{rid}/leave")
def leave_room(rid: int, request: Request):
    u = require_user(request)
    r = _need_access(rid, u)
    if r["official"]:
        raise HTTPException(400, "官方大厅不能退出")
    if r["kind"] == "dm":
        raise HTTPException(400, "私聊会话不能退出(可删除会话记录)")
    if r["owner_id"] == u["id"] and u["role"] != "admin":
        raise HTTPException(400, "群主需先转让群主才能退群")
    db.ex("UPDATE chat_members SET left_at=? WHERE room_id=? AND uid=?", (db.now(), rid, u["id"]))
    _sys(rid, f"{u['nickname'] or u['username']} 退出了群聊")
    return {"ok": True}


class UidIn(BaseModel):
    uid: int


@router.post("/rooms/{rid}/kick")
def kick(rid: int, body: UidIn, request: Request):
    u = require_user(request)
    r = _need_access(rid, u)
    if u["role"] != "admin" and r["owner_id"] != u["id"]:
        raise HTTPException(403, "只有群主或管理员可以移出成员")
    if body.uid == r["owner_id"]:
        raise HTTPException(400, "不能移出群主")
    who = user_card(body.uid)
    db.ex("UPDATE chat_members SET left_at=? WHERE room_id=? AND uid=?", (db.now(), rid, body.uid))
    _sys(rid, f"{who['nickname']} 已被移出群聊")
    return {"ok": True}


@router.post("/rooms/{rid}/transfer")
def transfer(rid: int, body: UidIn, request: Request):
    u = require_user(request)
    r = _need_access(rid, u)
    if u["role"] != "admin" and r["owner_id"] != u["id"]:
        raise HTTPException(403, "只有群主可以转让")
    if not _member(rid, body.uid):
        raise HTTPException(400, "对方不在群里")
    db.ex("UPDATE chat_rooms SET owner_id=? WHERE id=?", (body.uid, rid))
    _sys(rid, f"{user_card(body.uid)['nickname']} 成为了新群主")
    return {"ok": True}


class RoomPatch(BaseModel):
    name: str | None = None
    intro: str | None = None
    avatar: str | None = None


@router.patch("/rooms/{rid}")
def patch_room(rid: int, body: RoomPatch, request: Request):
    u = require_user(request)
    r = _need_access(rid, u)
    if u["role"] != "admin" and r["owner_id"] != u["id"]:
        raise HTTPException(403, "只有群主或管理员可以修改群资料")
    if body.name is not None:
        nm = _clean(body.name, 30)
        if len(nm) < 2:
            raise HTTPException(400, "群组名太短")
        if nm != r["name"]:
            db.ex("UPDATE chat_rooms SET name=? WHERE id=?", (nm, rid))
            _sys(rid, f"{u['nickname'] or u['username']} 把群名改为「{nm}」")
    if body.intro is not None:
        db.ex("UPDATE chat_rooms SET intro=? WHERE id=?", (_clean(body.intro, 200), rid))
    if body.avatar is not None:
        av = _clean(body.avatar, 300)
        if av and not re.match(r"^#[0-9a-fA-F]{6}$", av) and not av.startswith("/api/avatars/"):
            raise HTTPException(400, "群头像格式不支持")
        db.ex("UPDATE chat_rooms SET avatar=? WHERE id=?", (av, rid))
    return {"ok": True, "room": _room_out(_room(rid), u)}


@router.delete("/rooms/{rid}")
def delete_room(rid: int, request: Request):
    u = require_user(request)
    r = _room(rid)
    if u["role"] != "admin" and r["owner_id"] != u["id"]:
        raise HTTPException(403, "只有管理员或群主可以解散群组")
    if r["official"] and u["role"] != "admin":
        raise HTTPException(403, "官方大厅不能解散")
    db.ex("DELETE FROM chat_messages WHERE room_id=?", (rid,))
    db.ex("DELETE FROM chat_members WHERE room_id=?", (rid,))
    db.ex("DELETE FROM chat_mentions WHERE room_id=?", (rid,))
    db.ex("DELETE FROM chat_typing WHERE room_id=?", (rid,))
    db.ex("DELETE FROM chat_rooms WHERE id=?", (rid,))
    return {"ok": True}


class MePatch(BaseModel):
    muted: bool | None = None
    pinned: bool | None = None
    nickname: str | None = None


@router.patch("/rooms/{rid}/me")
def patch_me(rid: int, body: MePatch, request: Request):
    """我在本会话的设置: 免打扰 / 置顶 / 群昵称"""
    u = require_user(request)
    _need_access(rid, u)
    db.ex("INSERT OR IGNORE INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)", (rid, u["id"], db.now()))
    if body.muted is not None:
        db.ex("UPDATE chat_members SET muted=? WHERE room_id=? AND uid=?", (1 if body.muted else 0, rid, u["id"]))
    if body.pinned is not None:
        db.ex("UPDATE chat_members SET pinned=? WHERE room_id=? AND uid=?", (1 if body.pinned else 0, rid, u["id"]))
    if body.nickname is not None:
        nk = _clean(body.nickname, 32)
        db.ex("UPDATE chat_members SET nickname=? WHERE room_id=? AND uid=?", (nk, rid, u["id"]))
    return {"ok": True, "room": _room_out(_room(rid), u)}


@router.get("/rooms/{rid}")
def room_detail(rid: int, request: Request):
    u = require_user(request)
    r = _need_access(rid, u)
    out = _room_out(r, u)
    ms = _members(rid)
    out["member_list"] = []
    for m in ms:
        c = user_card(m["uid"])
        c["nickname_in_room"] = m["nickname"] or c["nickname"]
        c["is_owner"] = m["uid"] == r["owner_id"]
        c["joined_at"] = m["joined_at"]
        out["member_list"].append(c)
    out["is_owner"] = r["owner_id"] == u["id"] or u["role"] == "admin"
    out["i_am_owner"] = r["owner_id"] == u["id"]
    return out


# ================================================================== 消息 ===

@router.get("/rooms/{rid}/messages")
def room_messages(rid: int, request: Request, after: int = 0, before: int = 0, limit: int = 60):
    """after=增量拉新; before=向上翻历史(微信式下拉加载)"""
    u = require_user(request)
    r = _need_access(rid, u)
    limit = max(1, min(limit, 100))
    if after:
        rows = db.q("SELECT * FROM chat_messages WHERE room_id=? AND id>? ORDER BY id LIMIT ?",
                    (rid, after, limit))
    elif before:
        rows = db.q("SELECT * FROM (SELECT * FROM chat_messages WHERE room_id=? AND id<?"
                    " ORDER BY id DESC LIMIT ?) ORDER BY id", (rid, before, limit))
    else:
        rows = db.q("SELECT * FROM (SELECT * FROM chat_messages WHERE room_id=? ORDER BY id DESC LIMIT ?)"
                    " ORDER BY id", (rid, limit))
    items = _pack(rows, u, r)
    me = _member(rid, u["id"])
    return {"items": items, "has_more": bool(before) and len(rows) == limit,
            "typing": [_nick_in_room(rid, x) for x in _typing(rid, u["id"])],
            "last_read": me["last_read"] if me else 0,
            "server_time": db.now()}


def _nick_in_room(rid, uid):
    m = db.q1("SELECT nickname FROM chat_members WHERE room_id=? AND uid=?", (rid, uid))
    if m and m["nickname"]:
        return m["nickname"]
    return user_card(uid)["nickname"]


class MsgIn(BaseModel):
    content: str = ""
    kind: str = "text"
    att_id: int = 0
    reply_to: int = 0
    mentions: list[int] = []


@router.post("/rooms/{rid}/messages")
def send_message(rid: int, body: MsgIn, request: Request):
    u = require_user(request)
    if _banned(u):
        raise HTTPException(403, "你已被管理员禁言")
    if not _chat_enabled(u):
        raise HTTPException(403, "聊天区已由管理员关闭")
    r = _need_access(rid, u)
    kind = body.kind if body.kind in ("text", "image", "file", "audio") else "text"
    content = _clean(body.content, MSG_MAX)
    att_id = 0
    if kind != "text":
        a = db.q1("SELECT * FROM chat_attachments WHERE id=?", (body.att_id,))
        if not a:
            raise HTTPException(404, "附件不存在或已过期, 请重新发送")
        if a["uid"] != u["id"] and u["role"] != "admin":
            raise HTTPException(403, "只能发送自己上传的附件")
        if a["kind"] != kind:
            raise HTTPException(400, "附件类型与消息类型不一致")
        att_id = a["id"]
        db.ex("UPDATE chat_attachments SET room_id=? WHERE id=?", (rid, att_id))
        if not content:
            content = a["name"]
    elif not content:
        raise HTTPException(400, "消息不能为空")

    # 轻量防刷: 文字消息 0.3 秒一条(图片/语音/文件已有上传耗时, 不再限)
    now = time.time()
    if kind == "text":
        if now - _last_send.get(u["id"], 0) < 0.3:
            raise HTTPException(429, "发得太快了, 歇半秒")
        _last_send[u["id"]] = now

    reply_to = 0
    if body.reply_to:
        src = db.q1("SELECT * FROM chat_messages WHERE id=? AND room_id=?", (body.reply_to, rid))
        if src:
            reply_to = src["id"]

    db.ex("INSERT INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)"
          " ON CONFLICT(room_id,uid) DO UPDATE SET left_at=NULL", (rid, u["id"], db.now()))
    cur = db.ex("INSERT INTO chat_messages(room_id,uid,content,kind,att_id,reply_to,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (rid, u["id"], content, kind, att_id or None, reply_to or None, db.now()))
    mid = cur.lastrowid
    # 发言者自己视为已读
    db.ex("UPDATE chat_members SET last_read=? WHERE room_id=? AND uid=? AND last_read<?",
          (mid, rid, u["id"], mid))
    db.ex("DELETE FROM chat_typing WHERE room_id=? AND uid=?", (rid, u["id"]))
    # @提醒: 只记录真正在群里的人
    mem = {m["uid"] for m in _members(rid)}
    for x in dict.fromkeys(body.mentions or []):
        if int(x) in mem and int(x) != u["id"]:
            db.ex("INSERT OR IGNORE INTO chat_mentions(msg_id,uid,room_id,seen,created_at)"
                  " VALUES(?,?,?,0,?)", (mid, int(x), rid, db.now()))
    row = db.q1("SELECT * FROM chat_messages WHERE id=?", (mid,))
    return {"id": mid, "item": _pack([row], u, r)[0]}


class ReadIn(BaseModel):
    last_id: int = 0


@router.post("/rooms/{rid}/read")
def mark_read(rid: int, body: ReadIn, request: Request):
    """把会话标为已读(同时驱动对方的"已读"回执)"""
    u = require_user(request)
    _need_access(rid, u)
    lm = _last_msg(rid)
    target = body.last_id or (lm["id"] if lm else 0)
    db.ex("INSERT INTO chat_members(room_id,uid,joined_at,last_read) VALUES(?,?,?,?)"
          " ON CONFLICT(room_id,uid) DO UPDATE SET last_read=MAX(last_read, excluded.last_read)",
          (rid, u["id"], db.now(), target))
    db.ex("UPDATE chat_mentions SET seen=1 WHERE uid=? AND room_id=?", (u["id"], rid))
    db.ex("DELETE FROM chat_typing WHERE room_id=? AND uid=?", (rid, u["id"]))
    return {"ok": True, "last_read": target}


@router.post("/rooms/{rid}/typing")
def typing(rid: int, request: Request):
    """我正在输入(前端 2 秒节流上报, 6 秒内有效)"""
    u = require_user(request)
    _need_access(rid, u)
    db.ex("INSERT INTO chat_typing(room_id,uid,at) VALUES(?,?,?)"
          " ON CONFLICT(room_id,uid) DO UPDATE SET at=excluded.at", (rid, u["id"], time.time()))
    # 顺手清理过期记录(每次调用开销极小)
    db.ex("DELETE FROM chat_typing WHERE at < ?", (time.time() - TYPING_TTL * 10,))
    return {"ok": True}


class MsgPatch(BaseModel):
    content: str


@router.patch("/messages/{mid}")
def edit_message(mid: int, body: MsgPatch, request: Request):
    u = require_user(request)
    m = db.q1("SELECT * FROM chat_messages WHERE id=?", (mid,))
    if not m or m["deleted"]:
        raise HTTPException(404, "消息不存在")
    if m["uid"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能编辑自己的消息")
    if m["kind"] != "text":
        raise HTTPException(400, "只能编辑文字消息")
    if m["uid"] == u["id"] and u["role"] != "admin" and _ts_age(m["created_at"]) > RECALL_WINDOW * 10:
        raise HTTPException(403, "消息发送太久, 不能编辑了")
    content = _clean(body.content, MSG_MAX)
    if not content:
        raise HTTPException(400, "消息不能为空")
    db.ex("UPDATE chat_messages SET content=?, edited=1, edited_by=? WHERE id=?", (content, u["id"], mid))
    return {"ok": True}


@router.delete("/messages/{mid}")
def delete_message(mid: int, request: Request):
    """撤回(2 分钟内, 全员可见"撤回了一条消息"); 管理员不限时"""
    u = require_user(request)
    m = db.q1("SELECT * FROM chat_messages WHERE id=?", (mid,))
    if not m:
        raise HTTPException(404, "消息不存在")
    if m["deleted"]:
        return {"ok": True}
    admin = u["role"] == "admin"
    if m["kind"] == "system" and not admin:
        raise HTTPException(403, "系统提示不能撤回")
    if m["uid"] != u["id"] and not admin:
        raise HTTPException(403, "只能撤回自己的消息")
    if m["uid"] == u["id"] and not admin and _ts_age(m["created_at"]) > RECALL_WINDOW:
        raise HTTPException(403, f"超过 {RECALL_WINDOW // 60} 分钟的消息不能撤回")
    db.ex("UPDATE chat_messages SET deleted=1 WHERE id=?", (mid,))
    db.ex("DELETE FROM chat_reactions WHERE msg_id=?", (mid,))
    db.ex("DELETE FROM chat_mentions WHERE msg_id=?", (mid,))
    return {"ok": True, "by_admin": admin and m["uid"] != u["id"]}


class ReactIn(BaseModel):
    emoji: str


@router.post("/messages/{mid}/react")
def react(mid: int, body: ReactIn, request: Request):
    """表情回应: 同一个表情再点一次即取消"""
    u = require_user(request)
    m = db.q1("SELECT * FROM chat_messages WHERE id=?", (mid,))
    if not m or m["deleted"]:
        raise HTTPException(404, "消息不存在")
    _need_access(m["room_id"], u)
    e = _clean(body.emoji, 8)
    if e not in REACTIONS:
        raise HTTPException(400, "不支持的表情")
    old = db.q1("SELECT * FROM chat_reactions WHERE msg_id=? AND uid=?", (mid, u["id"]))
    if old and old["emoji"] == e:
        db.ex("DELETE FROM chat_reactions WHERE msg_id=? AND uid=?", (mid, u["id"]))
    else:
        db.ex("INSERT INTO chat_reactions(msg_id,uid,emoji,created_at) VALUES(?,?,?,?)"
              " ON CONFLICT(msg_id,uid) DO UPDATE SET emoji=excluded.emoji", (mid, u["id"], e, db.now()))
    return {"ok": True, "reacts": _pack([db.q1("SELECT * FROM chat_messages WHERE id=?", (mid,))],
                                        u, _room(m["room_id"]))[0]["reacts"]}


@router.get("/search")
def search(request: Request, q: str = "", rid: int = 0, limit: int = 50):
    """搜索聊天记录(只搜我有权看的会话)"""
    u = require_user(request)
    q = _clean(q, 60)
    if len(q) < 1:
        return {"items": []}
    limit = max(1, min(limit, 100))
    mine = {r["id"]: r for r in _my_rooms(u)}
    if rid:
        if rid not in mine:
            raise HTTPException(403, "你不是这个会话的成员")
        ids = [rid]
    else:
        ids = list(mine.keys())
    if not ids:
        return {"items": []}
    ph = ",".join("?" * len(ids))
    like = f"%{q}%"
    rows = db.q(f"SELECT * FROM chat_messages WHERE room_id IN ({ph}) AND deleted=0"
                f" AND kind='text' AND content LIKE ? ORDER BY id DESC LIMIT ?",
                tuple(ids) + (like, limit))
    out = []
    for m in rows:
        o = _msg_out(m, u, room=mine[m["room_id"]])
        o["room_name"] = _room_label(mine[m["room_id"]], u)
        out.append(o)
    return {"items": out}


def _room_label(r, u):
    if r["kind"] == "dm":
        peer = next((m for m in _members(r["id"]) if m["uid"] != u["id"]), None)
        return user_card(peer["uid"])["nickname"] if peer else (r["name"] or "私聊")
    return r["name"]


# ================================================================== 附件 ===

@router.post("/attachments")
async def upload_attachment(request: Request, file: UploadFile = File(...),
                            kind: str = Form("image"), dur_ms: int = Form(0)):
    """上传图片 / 文件 / 语音, 返回 att_id(随后由 send_message 挂到消息上)"""
    u = require_user(request)
    if _banned(u):
        raise HTTPException(403, "你已被管理员禁言")
    if not _chat_enabled(u):
        raise HTTPException(403, "聊天区已由管理员关闭")
    if kind not in ("image", "file", "audio"):
        raise HTTPException(400, "附件类型不支持")
    orig = _clean(file.filename or "", 160) or "attachment"
    ext = (Path(orig).suffix.lower().lstrip(".") or "bin")[:8]
    ok = {"image": IMG_EXT, "audio": AUD_EXT, "file": FILE_EXT}[kind]
    if ext not in ok:
        raise HTTPException(400, f"不支持的 .{ext} 文件(允许: {', '.join(sorted(ok)[:8])}…)")
    cap = LIMITS[kind]
    dest = _att_dir(u["id"]) / f"{int(time.time())}_{secrets.token_hex(5)}.{ext}"
    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    raise HTTPException(413, f"文件超过 {cap >> 20}MB 上限")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, "保存失败")
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "空文件")
    mime = (file.content_type or "")[:80]
    cur = db.ex("INSERT INTO chat_attachments(room_id,uid,kind,path,name,mime,size_bytes,dur_ms,created_at)"
                " VALUES(0,?,?,?,?,?,?,?,?)",
                (u["id"], kind, str(dest), orig, mime, size, max(0, min(int(dur_ms or 0), 600000)), db.now()))
    aid = cur.lastrowid
    # 顺手清理: 上传了却一直没发出去的孤儿附件(超过 1 天)
    _gc_orphans(u["id"])
    return {"id": aid, "kind": kind, "name": orig, "size": size, "mime": mime,
            "dur_ms": max(0, min(int(dur_ms or 0), 600000)),
            "url": f"/api/chat/attachments/{aid}"}


def _gc_orphans(uid):
    try:
        rows = db.q("SELECT * FROM chat_attachments WHERE uid=? AND room_id=0", (uid,))
        for r in rows:
            if _ts_age(r["created_at"]) > 86400:
                Path(r["path"]).unlink(missing_ok=True)
                db.ex("DELETE FROM chat_attachments WHERE id=?", (r["id"],))
    except Exception:
        pass


@router.get("/attachments/{aid}")
def get_attachment(aid: int, request: Request):
    a = db.q1("SELECT * FROM chat_attachments WHERE id=?", (aid,))
    if not a:
        raise HTTPException(404, "附件不存在")
    u = require_user(request)
    if a["uid"] != u["id"] and u["role"] != "admin":
        if not a["room_id"] or not _can_see(_room(a["room_id"]), u):
            raise HTTPException(403, "无权查看这个附件")
    p = Path(a["path"])
    if not p.exists():
        raise HTTPException(404, "附件文件已丢失")
    inline = a["kind"] in ("image", "audio")
    disp = ("inline" if inline else "attachment") + f'; filename="{secrets.token_hex(4)}.' + \
           (Path(a["name"]).suffix.lstrip(".") or "bin") + '"'
    return FileResponse(str(p), media_type=a["mime"] or None, headers={
        "Content-Disposition": disp,
        "Cache-Control": "private, max-age=86400",
        "Accept-Ranges": "bytes",
    })
