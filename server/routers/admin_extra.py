"""FLA v3.6 - 管理后台扩展: 用户组 / 批量假用户 / 首页展示数据"""
import json
import random
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import db
from ..deps import require_admin
from ..security import hash_password

router = APIRouter(prefix="/api/admin")


# ---------------- 用户组 ----------------
def _group_out(r):
    n = db.q1("SELECT COUNT(*) AS c FROM users WHERE group_id=?", (r["id"],))["c"]
    return {"id": r["id"], "name": r["name"], "descr": r["descr"],
            "is_default": bool(r["is_default"]), "users": n}


@router.get("/groups")
def list_groups(request: Request):
    require_admin(request)
    return {"items": [_group_out(r) for r in db.q("SELECT * FROM user_groups ORDER BY id")]}


class GroupIn(BaseModel):
    name: str
    descr: str = ""


@router.post("/groups")
def create_group(body: GroupIn, request: Request):
    require_admin(request)
    name = body.name.strip()
    if not (1 <= len(name) <= 30):
        raise HTTPException(400, "组名需 1-30 个字符")
    if db.q1("SELECT id FROM user_groups WHERE name=?", (name,)):
        raise HTTPException(400, "组名已存在")
    cur = db.ex("INSERT INTO user_groups(name,descr,is_default,created_at) VALUES(?,?,0,?)",
                (name, body.descr.strip()[:100], db.now()))
    return _group_out(db.q1("SELECT * FROM user_groups WHERE id=?", (cur.lastrowid,)))


@router.patch("/groups/{gid}")
def update_group(gid: int, body: GroupIn, request: Request):
    require_admin(request)
    r = db.q1("SELECT * FROM user_groups WHERE id=?", (gid,))
    if not r:
        raise HTTPException(404, "用户组不存在")
    name = body.name.strip()
    if not (1 <= len(name) <= 30):
        raise HTTPException(400, "组名需 1-30 个字符")
    db.ex("UPDATE user_groups SET name=?, descr=? WHERE id=?", (name, body.descr.strip()[:100], gid))
    return _group_out(db.q1("SELECT * FROM user_groups WHERE id=?", (gid,)))


@router.delete("/groups/{gid}")
def delete_group(gid: int, request: Request):
    require_admin(request)
    r = db.q1("SELECT * FROM user_groups WHERE id=?", (gid,))
    if not r:
        raise HTTPException(404, "用户组不存在")
    if r["is_default"]:
        raise HTTPException(400, "默认组不能删除")
    # 成员、邀请码回落到默认组; AI 模型的授权组里去掉该组
    db.ex("UPDATE users SET group_id=1 WHERE group_id=?", (gid,))
    db.ex("UPDATE invite_codes SET group_id=1 WHERE group_id=?", (gid,))
    for m in db.q("SELECT id, groups FROM ai_models"):
        try:
            gs = [g for g in json.loads(m["groups"] or "[]") if g != gid]
        except Exception:
            gs = []
        db.ex("UPDATE ai_models SET groups=? WHERE id=?", (json.dumps(gs), m["id"]))
    db.ex("DELETE FROM user_groups WHERE id=?", (gid,))
    return {"ok": True}


# ---------------- 批量假用户 ----------------
_SURNAMES = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
_GIVEN = "伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华建国文俊安宁欣怡嘉晨雨萱子涵梓轩浩然佳琪思远一鸣晓峰辉雪梅慧玲云帆博瑶悦"
_EN_NAMES = ["Alex", "Mia", "Leo", "Nora", "Ethan", "Lily", "Owen", "Zoe", "Ryan", "Ivy", "Jack", "Ella"]


def _fake_nick():
    if random.random() < 0.8:
        g = "".join(random.choice(_GIVEN) for _ in range(random.choice([1, 2])))
        return random.choice(_SURNAMES) + g
    return random.choice(_EN_NAMES) + str(random.randint(10, 99))


class FakeIn(BaseModel):
    count: int = 10
    prefix: str = "fake"
    group_id: int = 1


@router.post("/fake-users")
def create_fake_users(body: FakeIn, request: Request):
    require_admin(request)
    if not (1 <= body.count <= 500):
        raise HTTPException(400, "单次生成需在 1-500 个之间")
    prefix = (body.prefix or "fake").strip()
    if not re.match(r"^[A-Za-z0-9_]{1,12}$", prefix):
        raise HTTPException(400, "用户名前缀仅限 1-12 位字母/数字/下划线")
    if not db.q1("SELECT id FROM user_groups WHERE id=?", (body.group_id,)):
        raise HTTPException(400, "用户组不存在")
    quota = int(db.get_setting("default_quota_mb", "500")) * 1024 * 1024
    official = db.q1("SELECT id FROM chat_rooms WHERE official=1 ORDER BY id LIMIT 1")
    made = 0
    for _ in range(body.count):
        for _try in range(10):
            uname = f"{prefix}_{secrets.token_hex(3)}"
            if not db.q1("SELECT id FROM users WHERE username=?", (uname,)):
                break
        else:
            continue
        # 假用户密码随机且不告知, 无法登录, 仅用于展示
        cur = db.ex("INSERT INTO users(username,password_hash,nickname,quota_bytes,created_at,group_id,is_fake)"
                    " VALUES(?,?,?,?,?,?,1)",
                    (uname, hash_password(secrets.token_urlsafe(12)), _fake_nick(), quota, db.now(), body.group_id))
        if official:
            try:
                db.ex("INSERT OR IGNORE INTO chat_members(room_id,uid,joined_at) VALUES(?,?,?)",
                      (official["id"], cur.lastrowid, db.now()))
            except Exception:
                pass
        made += 1
    return {"ok": True, "created": made}


@router.get("/fake-users/stats")
def fake_stats(request: Request):
    require_admin(request)
    return {"count": db.q1("SELECT COUNT(*) AS c FROM users WHERE is_fake=1")["c"]}


@router.delete("/fake-users")
def delete_fake_users(request: Request):
    require_admin(request)
    ids = [r["id"] for r in db.q("SELECT id FROM users WHERE is_fake=1")]
    if not ids:
        return {"ok": True, "deleted": 0}
    ph = ",".join("?" * len(ids))
    tids = [r["id"] for r in db.q(f"SELECT id FROM forum_threads WHERE user_id IN ({ph})", tuple(ids))]
    if tids:
        tph = ",".join("?" * len(tids))
        db.ex(f"DELETE FROM forum_posts WHERE thread_id IN ({tph})", tuple(tids))
        db.ex(f"DELETE FROM forum_threads WHERE id IN ({tph})", tuple(tids))
    db.ex(f"DELETE FROM forum_posts WHERE user_id IN ({ph})", tuple(ids))
    db.ex(f"DELETE FROM chat_members WHERE uid IN ({ph})", tuple(ids))
    db.ex(f"DELETE FROM users WHERE id IN ({ph})", tuple(ids))
    return {"ok": True, "deleted": len(ids)}


# ---------------- 首页展示数据 ----------------
class DisplayIn(BaseModel):
    show_users_offset: int | None = None
    show_online_offset: int | None = None
    show_hits_offset: int | None = None
    show_traffic_offset_mb: int | None = None


def _display_settings():
    g = db.get_setting
    return {
        "show_users_offset": int(g("show_users_offset", "0") or 0),
        "show_online_offset": int(g("show_online_offset", "0") or 0),
        "show_hits_offset": int(g("show_hits_offset", "0") or 0),
        "show_traffic_offset_mb": int(g("show_traffic_offset_mb", "0") or 0),
    }


@router.get("/display")
def get_display(request: Request):
    require_admin(request)
    from .stats import display_values
    return {"offsets": _display_settings(), "shown": display_values()}


@router.put("/display")
def put_display(body: DisplayIn, request: Request):
    require_admin(request)
    for k, v in body.model_dump().items():
        if v is None:
            continue
        if not (-10**9 <= v <= 10**9):
            raise HTTPException(400, "数值超出范围")
        db.set_setting(k, str(int(v)))
    return get_display(request)
