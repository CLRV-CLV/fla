#!/usr/bin/env python3
"""FLA v1.27 冒烟测试 — 不需要 Docker / LibreOffice / 外网.

跑法:
    python3 tests/smoke.py            # 或 .venv/bin/python tests/smoke.py

它在一个临时数据目录里拉起 FastAPI(Starlette TestClient), 依次验证:
  1. 健康检查 / 管理员登录
  2. 上传一个"最小的真 pptx"(3 页, 16:9) → 微软在线视图对接信息 (/ms-view)
     - 直链是否带域名 + 80/443(微软硬性要求)  → ms_ok
     - 页数 / 真实 slide id / 宽高比是否从包里读出来
     - 深链模板 url_tpl 里的 {n} / {id} 占位符(前端翻页靠它换 iframe.src,
       这正是"板书画布随页切换"的服务端那一半)
  3. 公开直链免登录可下载(微软抓取器要能拿到文件), 且支持 HEAD/Range
  4. 批注 PUT/GET 往返(含 v1.27 新增的 ms 段: 板书区域/同步模式/附加板书页)
  5. 白板新建、社区(论坛/聊天)、公告、认证等接口不回 5xx
任何一步失败都会打印 FAIL 并以非 0 退出。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

# ---- 必须在 import server.* 之前设定环境 -------------------------------------
_TMP = tempfile.mkdtemp(prefix="fla-smoke-")
os.environ["FLA_DATA_DIR"] = _TMP
os.environ["ADMIN_PASSWORD"] = "smoke-admin-pw"
os.environ["PUBLIC_BASE_URL"] = "https://class.example.com"   # 部署脚本写进来的对外域名
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import warnings                                    # noqa: E402

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient          # noqa: E402
from server.main import app                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  → " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n== " + t + " " + "=" * max(0, 60 - len(t)))


# ---------------------------------------------------------------- 最小 pptx ---
CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>"""


def pres_xml(n=3, cx=12192000, cy=6858000):
    """n 页幻灯片的 presentation.xml, slide id 从 256 递增(与 PowerPoint 一致)"""
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{100 + i}"/>' for i in range(n))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<p:sldMasterIdLst/>
<p:sldIdLst>{ids}</p:sldIdLst>
<p:sldSz cx="{cx}" cy="{cy}"/>
<p:notesSz cx="{cy}" cy="{cx}"/>
</p:presentation>"""


def make_pptx(n=3) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("ppt/presentation.xml", pres_xml(n))
        for i in range(n):
            z.writestr(f"ppt/slides/slide{i + 1}.xml",
                       '<?xml version="1.0"?><p:sld xmlns:p='
                       '"http://schemas.openxmlformats.org/presentationml/2006/main"/>')
    return buf.getvalue()


def main():
    # with 上下文才会触发 FastAPI 的 startup(建表 / 初始管理员)
    with TestClient(app) as c:
        return run(c)


def run(c):

    section("1. 服务与登录")
    r = c.get("/api/health")
    check("GET /api/health", r.status_code == 200 and r.json().get("ok"), r.text[:120])

    r = c.post("/api/auth/login", json={"username": "admin", "password": "smoke-admin-pw"})
    check("管理员登录", r.status_code == 200 and r.json().get("token"), r.text[:200])
    tok = (r.json() or {}).get("token", "")
    H = {"Authorization": "Bearer " + tok}
    r = c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    check("错误密码被拒", r.status_code in (400, 401, 403), r.status_code)

    section("2. 上传 pptx + 微软在线视图对接 (/ms-view)")
    pptx = make_pptx(3)
    r = c.post("/api/files/upload", headers=H,
               files={"file": ("第三章 光合作用.pptx", pptx,
                               "application/vnd.openxmlformats-officedocument.presentationml.presentation")})
    check("上传 pptx", r.status_code == 200 and r.json().get("id"), r.text[:200])
    fid = (r.json() or {}).get("id")
    if not fid:
        return finish()

    r = c.get(f"/api/files/{fid}/meta", headers=H)
    check("GET meta", r.status_code == 200 and r.json().get("kind") == "office", r.text[:200])

    r = c.get(f"/api/files/{fid}/share-link", headers=H)
    sl = r.json() if r.status_code == 200 else {}
    check("GET share-link", bool(sl.get("direct")), r.text[:200])
    check("直链是域名 + 443 (ms_ok)", sl.get("ms_ok") is True, sl.get("direct"))
    check("直链纯 ASCII(微软抓取器不接受中文路径)",
          all(ord(ch) < 128 for ch in (sl.get("path") or "")), sl.get("path"))

    r = c.get(f"/api/files/{fid}/ms-view", headers=H)
    mv = r.json() if r.status_code == 200 else {}
    check("GET /ms-view", r.status_code == 200 and mv.get("provider") == "ms", r.text[:200])
    check("family=ppt", mv.get("family") == "ppt", mv.get("family"))
    check("页数取自 slide id 列表 (=3)", mv.get("pages") == 3, mv.get("pages"))
    check("真实 slide id [256,257,258]", mv.get("slide_ids") == [256, 257, 258], mv.get("slide_ids"))
    check("宽高比 16:9", abs((mv.get("aspect") or 0) - 16 / 9) < 1e-3, mv.get("aspect"))
    check("首页 URL 打到微软 embed.aspx", "view.officeapps.live.com/op/embed.aspx" in (mv.get("url") or ""),
          (mv.get("url") or "")[:160])
    tpl = mv.get("url_tpl") or ""
    check("url_tpl 含 {n} 占位符", "{n}" in tpl, tpl[:160])
    check("url_tpl 含 {id} 占位符", "{id}" in tpl, tpl[:160])
    check("src 只出现一次(翻页不重新转换)", tpl.count("src=") == 1, tpl.count("src="))
    u2 = tpl.replace("{n}", "2").replace("{id}", "257")
    check("第 2 页深链: wdStartOn=2", "wdStartOn=2" in u2, u2[:200])
    check("第 2 页深链: wdSlideId=257", "wdSlideId=257" in u2, u2[:200])
    check("deep_link 允许我方驱动翻页", mv.get("deep_link") is True, mv.get("deep_link"))

    section("3. 公开直链(免登录, 微软抓取器要能拿到)")
    r = c.get(sl.get("path") or "/api/files/share/none.pptx")
    check("免登录下载", r.status_code == 200 and r.content == pptx, r.status_code)
    r = c.head(sl.get("path") or "/api/files/share/none.pptx")
    check("HEAD 探测", r.status_code == 200 and r.headers.get("content-length") == str(len(pptx)), r.status_code)
    r = c.get(sl.get("path") or "/api/files/share/none.pptx", headers={"Range": "bytes=0-9"})
    check("Range 分段(206)", r.status_code in (200, 206), r.status_code)
    r = c.get(f"/api/files/{fid}/ms-view")
    check("未登录访问 ms-view 被拒", r.status_code in (401, 403), r.status_code)

    section("4. 批注往返(含 v1.27 ms 段)")
    ann = {
        "pages": [{"t": "ms", "n": i, "pid": f"m{i}", "bg": "w"} for i in range(3)]
                 + [{"t": "blank", "n": 3, "pid": "x0", "w": 1280, "h": 720}],
        "strokes": {"m1": [{"id": "s1", "tool": "pen", "color": "#ef4444", "width": 4,
                            "pts": [[10, 20], [300, 400]]}],
                    "x0": [{"id": "s2", "tool": "text", "color": "#111827", "width": 30,
                            "pts": [[80, 90]], "text": "板书页"}]},
        "bb": {"1": [{"id": "b1", "tool": "pen", "color": "#fff", "width": 3, "pts": [[1, 2], [3, 4]]}], "n": 2},
        "ms": {"rect": [0.04, 0.09, 0.92, 0.8], "sync": "deep", "extra": 1, "v": 2},
    }
    r = c.put(f"/api/files/{fid}/annotations", headers=H, json=ann)
    check("PUT annotations", r.status_code == 200, r.text[:200])
    r = c.get(f"/api/files/{fid}/annotations", headers=H)
    got = r.json() if r.status_code == 200 else {}
    check("GET annotations", r.status_code == 200, r.text[:200])
    check("笔迹按页存回", (got.get("strokes") or {}).get("m1", [{}])[0].get("tool") == "pen",
          json.dumps(got.get("strokes"), ensure_ascii=False)[:200])
    check("附加板书页笔迹存回", bool((got.get("strokes") or {}).get("x0")), got.get("strokes"))
    check("板中板笔迹存回", bool((got.get("bb") or {}).get("1")), got.get("bb"))
    check("ms 段存回(板书区域/同步模式/附加页)",
          (got.get("ms") or {}).get("sync") == "deep" and len((got.get("ms") or {}).get("rect") or []) == 4
          and (got.get("ms") or {}).get("extra") == 1, got.get("ms"))
    r = c.put(f"/api/files/{fid}/annotations", headers=H, json={"pages": [], "strokes": {"m0": []}})
    check("不带 ms 段也能存(向后兼容)", r.status_code == 200, r.text[:160])
    r = c.get(f"/api/files/{fid}/annotations", headers=H)
    check("ms 段缺省时不报错", r.status_code == 200, r.text[:160])

    section("5. 白板 / 认证 / 社区")
    r = c.post("/api/files/board", headers=H, json={"name": "冒烟白板", "w": 1280, "h": 720})
    check("新建白板", r.status_code == 200 and r.json().get("id"), r.text[:200])

    # 注册需邀请码(管理员发放) — 这也是真实部署流程
    r = c.post("/api/admin/invites", headers=H, json={"code": "SMOKE-2027", "max_uses": 5, "note": "冒烟"})
    check("管理员发放邀请码", r.status_code == 200 and r.json().get("code") == "SMOKE-2027", r.text[:200])
    r = c.post("/api/auth/register", json={"username": "smoke_t1", "password": "pw-smoke-123",
                                           "nickname": "冒烟老师", "invite_code": "SMOKE-2027"})
    check("用邀请码注册教师账号", r.status_code == 200 and r.json().get("token"), r.text[:220])
    r2 = c.post("/api/auth/register", json={"username": "smoke_t2", "password": "pw-smoke-123",
                                            "nickname": "无码", "invite_code": "WRONG-CODE"})
    check("无效邀请码被拒", r2.status_code == 400, r2.status_code)
    sj = r.json() or {}
    sh = {"Authorization": "Bearer " + sj.get("token", "")}
    uid_t = (sj.get("user") or {}).get("id") or sj.get("id")

    r = c.get("/api/files", headers=sh)
    check("学生看不到别人的课件", r.status_code == 200 and all(f["id"] != fid for f in r.json()), r.text[:200])
    r = c.get(f"/api/files/{fid}/ms-view", headers=sh)
    check("越权 ms-view 被拒", r.status_code in (401, 403, 404), r.status_code)

    # 认证(教师 / 站长): 管理员发放, 前端据此渲染徽章
    r = c.get("/api/admin/users", headers=H)
    users = r.json() if r.status_code == 200 else []
    if isinstance(users, dict):
        users = users.get("users") or users.get("items") or []
    trow = next((u for u in users if u.get("username") == "smoke_t1"), None)
    check("管理员能看到用户列表", bool(trow), r.text[:160])
    if trow:
        uid_t = trow["id"]
        r = c.put(f"/api/admin/users/{uid_t}", headers=H, json={
            "is_teacher": True, "cert_title": "特级教师", "cert_icon": "medal", "cert_color": "#f59e0b"})
        check("发放教师认证", r.status_code == 200, r.text[:200])
        r = c.put(f"/api/admin/users/{uid_t}", headers=H, json={"cert_icon": "not-an-icon"})
        check("非法认证图标被拒", r.status_code == 400, r.status_code)
        r = c.put(f"/api/admin/users/{uid_t}", headers=H, json={"cert_color": "red"})
        check("非法认证颜色被拒", r.status_code == 400, r.status_code)
        r = c.get("/api/auth/me", headers=sh)
        me = r.json() if r.status_code == 200 else {}
        u = me.get("user") or me
        check("认证信息回读(称号/图标/颜色)",
              u.get("cert_title") == "特级教师" and u.get("cert_icon") == "medal"
              and u.get("cert_color") == "#f59e0b" and u.get("is_teacher") is True,
              json.dumps(u, ensure_ascii=False)[:220])

    # 站长(管理员)认证
    r = c.get("/api/auth/me", headers=H)
    adm = (r.json() or {}).get("user") or (r.json() or {})
    if adm.get("id"):
        r = c.put(f"/api/admin/users/{adm['id']}", headers=H, json={
            "cert_title": "站长", "cert_icon": "crown", "cert_color": "#a855f7"})
        check("发放站长认证", r.status_code == 200, r.text[:200])

    section("5b. 聊天(微信级): 私聊 / 未读 / 已读回执 / 群管理")
    r = c.get("/api/chat/inbox", headers=H)
    inbox = r.json() if r.status_code == 200 else {}
    check("会话列表 /inbox", r.status_code == 200 and isinstance(inbox.get("items"), list), r.text[:200])
    official = next((x for x in inbox.get("items", []) if x.get("official")), None)
    check("官方大厅自动在列(人人有份)", bool(official) and official.get("joined"), json.dumps(official, ensure_ascii=False)[:200])
    r = c.get("/api/chat/unread", headers=H)
    check("未读汇总 /unread", r.status_code == 200 and "total" in (r.json() or {}), r.text[:160])

    # --- 通讯录(建群/邀请/@ 用) ---
    r = c.get("/api/chat/contacts", headers=H)
    ct = (r.json() or {}).get("items") or []
    check("通讯录 /contacts", r.status_code == 200 and len(ct) >= 2, r.text[:200])
    check("通讯录标出自己", any(x.get("self") for x in ct), json.dumps(ct, ensure_ascii=False)[:160])
    r = c.get("/api/chat/contacts?q=冒烟", headers=H)
    check("通讯录支持搜索", r.status_code == 200 and len((r.json() or {}).get("items") or []) >= 1, r.text[:200])
    r = c.get("/api/chat/contacts")
    check("通讯录需登录", r.status_code == 401, r.status_code)

    # --- 私聊 ---
    r = c.post(f"/api/chat/dm/{uid_t}", headers=H)
    dm = (r.json() or {}).get("id") if r.status_code == 200 else 0
    check("管理员发起私聊", r.status_code == 200 and dm, r.text[:200])
    r = c.post(f"/api/chat/dm/{uid_t}", headers=H)
    check("重复发起复用同一会话(微信行为)", (r.json() or {}).get("id") == dm, r.text[:160])
    r = c.post("/api/chat/dm/1", headers=H)
    check("不能和自己私聊", r.status_code == 400, r.status_code)

    r = c.post(f"/api/chat/rooms/{dm}/messages", headers=H, json={"content": "老师好, 课件收到了"})
    m1 = (r.json() or {}).get("id") if r.status_code == 200 else 0
    check("私聊发言", r.status_code == 200 and m1, r.text[:200])
    it = (r.json() or {}).get("item") or {}
    check("发言回执带完整消息体", it.get("id") == m1 and it.get("mine") is True, json.dumps(it, ensure_ascii=False)[:200])
    r = c.get("/api/chat/unread", headers=sh)
    tu = r.json() or {}
    check("对方未读数 +1", tu.get("total") == 1 and any(x["id"] == dm for x in tu.get("rooms", [])),
          json.dumps(tu, ensure_ascii=False)[:200])
    r = c.get("/api/chat/inbox", headers=sh)
    dmr = next((x for x in (r.json() or {}).get("items", []) if x["id"] == dm), None)
    check("私聊会话显示对方昵称 + 最后一条", bool(dmr) and dmr.get("peer", {}).get("id") == 1
          and "课件" in (dmr.get("last") or {}).get("content", ""), json.dumps(dmr, ensure_ascii=False)[:240])
    r = c.post(f"/api/chat/rooms/{dm}/read", headers=sh, json={})
    check("标记已读", r.status_code == 200, r.text[:160])
    r = c.get(f"/api/chat/rooms/{dm}/messages", headers=H)
    got = (r.json() or {}).get("items") or []
    mine = next((x for x in got if x.get("mine")), None)
    check("私聊已读回执: 1/1 已读", bool(mine) and mine.get("read_count") == 1 and mine.get("read_total") == 1,
          json.dumps(mine, ensure_ascii=False)[:240])
    r = c.get("/api/chat/unread", headers=sh)
    check("已读后未读归零", (r.json() or {}).get("total") == 0, r.text[:160])

    # --- 正在输入 ---
    r = c.post(f"/api/chat/rooms/{dm}/typing", headers=sh)
    check("上报正在输入", r.status_code == 200, r.text[:120])
    r = c.get(f"/api/chat/rooms/{dm}/messages", headers=H)
    check("对方看到「正在输入」", bool((r.json() or {}).get("typing")), json.dumps((r.json() or {}).get("typing"))[:160])

    # --- 群聊 ---
    r = c.post("/api/chat/rooms", headers=sh, json={"name": "未开放前建群"})
    check("未开放建群时普通用户被拒", r.status_code == 403, r.status_code)
    r = c.put("/api/admin/settings", headers=H, json={"allow_group_create": True})
    check("管理员开放建群", r.status_code == 200, r.text[:160])
    r = c.post("/api/chat/rooms", headers=sh, json={"name": "x"})
    check("群名太短被拒", r.status_code == 400, r.status_code)
    time.sleep(0.32)
    r = c.post("/api/chat/rooms", headers=sh, json={"name": "高三(2)班", "intro": "作业与答疑", "members": [1]})
    room = r.json() if r.status_code == 200 else {}
    rid = room.get("id")
    check("教师建群并拉人", r.status_code == 200 and rid, r.text[:220])
    if rid:
        r = c.get(f"/api/chat/rooms/{rid}", headers=sh)
        det = r.json() if r.status_code == 200 else {}
        check("群资料: 成员/群主/公告", det.get("members") == 2 and det.get("i_am_owner") is True
              and det.get("intro") == "作业与答疑", json.dumps(det, ensure_ascii=False)[:240])
        r = c.post(f"/api/chat/rooms/{rid}/messages", headers=sh,
                   json={"content": "@管理员 请查收第三页板书", "mentions": [1]})
        gm = (r.json() or {}).get("id") if r.status_code == 200 else 0
        check("群内发言 + @某人", r.status_code == 200 and gm, r.text[:220])
        r = c.get("/api/chat/inbox", headers=H)
        gr = next((x for x in (r.json() or {}).get("items", []) if x["id"] == rid), None)
        check("被 @ 的人收到 at_me 提醒", bool(gr) and gr.get("at_me") == 1 and gr.get("unread") >= 1,
              json.dumps(gr, ensure_ascii=False)[:240])
        time.sleep(0.32)
        r = c.post(f"/api/chat/rooms/{rid}/messages", headers=H,
                   json={"content": "引用一下", "reply_to": gm})
        rm = (r.json() or {}).get("item") or {}
        check("引用回复带原文摘要", (rm.get("reply") or {}).get("id") == gm
              and "第三页" in (rm.get("reply") or {}).get("content", ""), json.dumps(rm, ensure_ascii=False)[:240])
        r = c.post(f"/api/chat/messages/{gm}/react", headers=H, json={"emoji": "👍"})
        check("表情回应", r.status_code == 200 and any(x["emoji"] == "👍" for x in (r.json() or {}).get("reacts", [])),
              r.text[:200])
        r = c.post(f"/api/chat/messages/{gm}/react", headers=H, json={"emoji": "👍"})
        check("再点一次取消回应", r.status_code == 200 and not (r.json() or {}).get("reacts"), r.text[:200])
        r = c.post(f"/api/chat/messages/{gm}/react", headers=H, json={"emoji": "💩"})
        check("非白名单表情被拒", r.status_code == 400, r.status_code)
        r = c.patch(f"/api/chat/messages/{gm}", headers=sh, json={"content": "改过的内容"})
        check("编辑自己的消息", r.status_code == 200, r.text[:160])
        # 第三名普通成员(非群主非管理员): 用来验证越权被拒
        r = c.post("/api/auth/register", json={"username": "smoke_t3", "password": "pw-smoke-123",
                                               "nickname": "冒烟学生", "invite_code": "SMOKE-2027"})
        s3 = {"Authorization": "Bearer " + (r.json() or {}).get("token", "")}
        uid_3 = ((r.json() or {}).get("user") or {}).get("id")
        check("注册第三名成员", r.status_code == 200 and uid_3, r.text[:200])
        r = c.post(f"/api/chat/rooms/{rid}/invite", headers=sh, json={"uids": [uid_3]})
        check("群主邀请第三名成员", r.status_code == 200, r.text[:160])
        r = c.patch(f"/api/chat/messages/{gm}", headers=s3, json={"content": "路人想改"})
        check("非作者不能编辑别人的消息", r.status_code == 403, r.status_code)
        r = c.delete(f"/api/chat/messages/{gm}", headers=s3)
        check("非作者不能撤回别人的消息", r.status_code == 403, r.status_code)
        r = c.patch(f"/api/chat/messages/{gm}", headers=H, json={"content": "管理员代为修订"})
        check("管理员可代管任意消息", r.status_code == 200, r.text[:160])
        r = c.get(f"/api/chat/rooms/{rid}/messages", headers=s3)
        ed = next((x for x in ((r.json() or {}).get("items") or []) if x["id"] == gm), None)
        check("代管消息标出 edited_by_admin", bool(ed) and ed.get("edited_by_admin") is True,
              json.dumps(ed, ensure_ascii=False)[:200])
        r = c.delete(f"/api/chat/messages/{gm}", headers=sh)
        check("作者 2 分钟内可撤回", r.status_code == 200, r.text[:160])
        r = c.get(f"/api/chat/rooms/{rid}/messages", headers=H)
        items = (r.json() or {}).get("items") or []
        rec = next((x for x in items if x["id"] == gm), None)
        check("撤回后内容为空且标 deleted", bool(rec) and rec.get("deleted") and rec.get("content") == "",
              json.dumps(rec, ensure_ascii=False)[:200])
        # 免打扰 / 置顶 / 群昵称
        r = c.patch(f"/api/chat/rooms/{rid}/me", headers=H,
                    json={"muted": True, "pinned": True, "nickname": "教务主任"})
        mo = (r.json() or {}).get("room") or {}
        check("免打扰 + 置顶 + 群昵称", mo.get("muted") is True and mo.get("pinned") is True
              and mo.get("my_nickname") == "教务主任", json.dumps(mo, ensure_ascii=False)[:220])
        r = c.post(f"/api/chat/rooms/{rid}/messages", headers=sh, json={"content": "第二条"})
        r = c.get("/api/chat/unread", headers=H)
        uu = r.json() or {}
        check("免打扰的会话不计入数字红点(只计 dot)", uu.get("total") == 0 and uu.get("dot") >= 1,
              json.dumps(uu, ensure_ascii=False)[:200])
        r = c.get("/api/chat/inbox", headers=H)
        check("置顶会话排在最前", ((r.json() or {}).get("items") or [{}])[0].get("id") == rid,
              json.dumps((r.json() or {}).get("items") or [], ensure_ascii=False)[:200])
        # 群管理
        r = c.patch(f"/api/chat/rooms/{rid}", headers=s3, json={"name": "路人改名"})
        check("普通成员不能改群资料", r.status_code == 403, r.status_code)
        r = c.post(f"/api/chat/rooms/{rid}/kick", headers=s3, json={"uid": 1})
        check("普通成员不能踢人", r.status_code == 403, r.status_code)
        r = c.patch(f"/api/chat/rooms/{rid}", headers=sh, json={"name": "高三(2)班·物理", "intro": "新公告"})
        check("群主改群名/公告", r.status_code == 200, r.text[:200])
        r = c.post(f"/api/chat/rooms/{rid}/kick", headers=sh, json={"uid": uid_3})
        check("群主移出成员", r.status_code == 200, r.text[:200])
        r = c.get(f"/api/chat/rooms/{rid}", headers=s3)
        check("被移出后看不到群", r.status_code == 403, r.status_code)
        r = c.post(f"/api/chat/rooms/{rid}/messages", headers=s3, json={"content": "我还能说话吗"})
        check("被移出后不能发言", r.status_code == 403, r.status_code)
        r = c.post(f"/api/chat/rooms/{rid}/invite", headers=sh, json={"uids": [uid_3]})
        check("重新邀请入群", r.status_code == 200 and (r.json() or {}).get("added"), r.text[:200])
        r = c.post(f"/api/chat/rooms/{rid}/transfer", headers=s3, json={"uid": 1})
        check("非群主不能转让", r.status_code == 403, r.status_code)
        r = c.post(f"/api/chat/rooms/{rid}/transfer", headers=sh, json={"uid": 1})
        check("转让群主", r.status_code == 200, r.text[:200])
        r = c.get(f"/api/chat/rooms/{rid}", headers=H)
        check("新群主资料正确", (r.json() or {}).get("i_am_owner") is True, r.text[:200])
        r = c.post(f"/api/chat/rooms/{rid}/leave", headers=sh)
        check("原群主(已转让)可退群", r.status_code == 200, r.text[:200])
        # 历史分页 + 搜索
        for i in range(5):
            time.sleep(0.32)          # 服务端有 0.3 秒防刷节流
            c.post(f"/api/chat/rooms/{rid}/messages", headers=H, json={"content": f"历史消息 {i}"})
        r = c.get(f"/api/chat/rooms/{rid}/messages?limit=3", headers=H)
        page = (r.json() or {}).get("items") or []
        check("按 limit 取最近消息", len(page) == 3, len(page))
        r = c.get(f"/api/chat/rooms/{rid}/messages?before={page[0]['id']}&limit=3", headers=H)
        older = (r.json() or {}).get("items") or []
        check("向上翻历史(before)", bool(older) and all(x["id"] < page[0]["id"] for x in older),
              json.dumps([x["id"] for x in older])[:160])
        r = c.get(f"/api/chat/rooms/{rid}/messages?after={page[-1]['id']}", headers=H)
        check("增量拉新(after)", r.status_code == 200, r.text[:160])
        r = c.get("/api/chat/search?q=历史消息", headers=H)
        hits = (r.json() or {}).get("items") or []
        check("全文搜索聊天记录", len(hits) >= 5 and all("历史消息" in x["content"] for x in hits),
              json.dumps(hits, ensure_ascii=False)[:200])
        r = c.post(f"/api/chat/rooms/{rid}/leave", headers=s3)
        check("普通成员可退群", r.status_code == 200, r.text[:160])
        r = c.get("/api/chat/search?q=历史消息", headers=s3)
        check("退群后搜不到该群记录", r.status_code == 200 and not (r.json() or {}).get("items"),
              json.dumps((r.json() or {}).get("items"), ensure_ascii=False)[:160])

    section("5c. 聊天附件: 图片 / 语音 / 文件")
    png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    r = c.post("/api/chat/attachments", headers=H, files={"file": ("板书.png", png, "image/png")},
               data={"kind": "image"})
    aid = (r.json() or {}).get("id") if r.status_code == 200 else 0
    check("上传图片附件", r.status_code == 200 and aid, r.text[:220])
    r = c.post("/api/chat/attachments", headers=H, files={"file": ("病毒.exe", b"MZ", "application/x-msdownload")},
               data={"kind": "file"})
    check("拒绝危险扩展名", r.status_code == 400, r.status_code)
    r = c.post("/api/chat/attachments", headers=H, files={"file": ("语音.webm", b"1a45dfa3" * 8, "audio/webm")},
               data={"kind": "audio", "dur_ms": "3200"})
    vid = (r.json() or {}).get("id") if r.status_code == 200 else 0
    check("上传语音附件(带时长)", r.status_code == 200 and (r.json() or {}).get("dur_ms") == 3200, r.text[:220])
    if dm and aid:
        r = c.post(f"/api/chat/rooms/{dm}/messages", headers=H, json={"kind": "image", "att_id": aid})
        im = (r.json() or {}).get("item") or {}
        check("发送图片消息", r.status_code == 200 and (im.get("att") or {}).get("kind") == "image",
              json.dumps(im, ensure_ascii=False)[:240])
        r = c.get((im.get("att") or {}).get("url") or "/api/chat/attachments/0", headers=sh)
        check("对方可取图片(inline)", r.status_code == 200 and r.content == png, r.status_code)
        r = c.get((im.get("att") or {}).get("url") or "/api/chat/attachments/0")
        check("未登录取附件被拒", r.status_code in (401, 403), r.status_code)
    if dm and vid:
        r = c.post(f"/api/chat/rooms/{dm}/messages", headers=H, json={"kind": "audio", "att_id": vid})
        check("发送语音消息", r.status_code == 200, r.text[:200])
    r = c.post(f"/api/chat/rooms/{dm}/messages", headers=H, json={"kind": "image", "att_id": 999999})
    check("不存在的附件被拒", r.status_code == 404, r.status_code)

    section("5d. 权限与开关")
    r = c.put("/api/admin/users/" + str(uid_t), headers=H, json={"chat_banned": True})
    check("管理员禁言", r.status_code == 200, r.text[:160])
    r = c.post(f"/api/chat/rooms/{dm}/messages", headers=sh, json={"content": "我还能说话吗"})
    check("被禁言者不能发言", r.status_code == 403, r.status_code)
    r = c.put("/api/admin/users/" + str(uid_t), headers=H, json={"chat_banned": False})
    check("解除禁言", r.status_code == 200, r.text[:160])
    r = c.put("/api/admin/settings", headers=H, json={"chat_enabled": False})
    check("关闭聊天区", r.status_code == 200, r.text[:160])
    r = c.post(f"/api/chat/rooms/{dm}/messages", headers=sh, json={"content": "还在吗"})
    check("关闭后普通用户不能发言", r.status_code == 403, r.status_code)
    time.sleep(0.32)
    r = c.post(f"/api/chat/rooms/{dm}/messages", headers=H, json={"content": "管理员仍可发言"})
    check("关闭后管理员仍可发言", r.status_code == 200, r.text[:160])
    r = c.put("/api/admin/settings", headers=H, json={"chat_enabled": True})
    check("重新开启聊天区", r.status_code == 200, r.text[:160])
    r = c.get(f"/api/chat/rooms/{dm}/messages", headers=sh)
    check("成员可读消息", r.status_code == 200, r.text[:160])

    for path, label in [("/api/forum/threads", "论坛帖子列表"),
                        ("/api/forum/boards", "论坛板块"),
                        ("/api/announcements", "公告列表"),
                        ("/api/auth/config", "前端配置")]:
        rr = c.get(path, headers=H)
        check(label + " 不回 5xx", rr.status_code < 500, f"{rr.status_code} {rr.text[:120]}")

    r = c.get("/api/admin/stats", headers=H)
    check("管理后台统计", r.status_code < 500, r.status_code)
    r = c.get("/api/admin/settings", headers=H)
    check("系统设置可读", r.status_code < 500, r.status_code)

    section("5e. 其余接口补测: 资料 / 头像 / 改密 / 扫码登录 / 公告 / 论坛 / 邀请码 / 文件直读")
    PNG = b"\x89PNG\r\n\x1a\n" + b"fla-smoke-avatar-bytes"

    # --- 个人资料 ---
    r = c.put("/api/users/profile", headers=sh, json={"nickname": "冒烟老师(改)", "signature": "认真上课"})
    check("PUT /api/users/profile", r.status_code == 200 and (r.json() or {}).get("nickname") == "冒烟老师(改)",
          r.text[:200])
    r = c.put("/api/users/profile", headers=sh, json={"nickname": "  "})
    check("空昵称被拒", r.status_code == 400, r.status_code)
    r = c.put("/api/users/profile", headers=sh, json={"signature": "长" * 201})
    check("超长签名被拒", r.status_code == 400, r.status_code)

    # --- 头像: 自己传 + 管理员代传 + 静态取回 ---
    r = c.post("/api/users/avatar", headers=sh, files={"file": ("a.png", PNG, "image/png")})
    av = (r.json() or {}).get("avatar", "") if r.status_code == 200 else ""
    check("POST /api/users/avatar", r.status_code == 200 and av.startswith("/api/avatars/"), r.text[:200])
    r = c.get(av or "/api/avatars/none.png")
    check("GET /api/avatars/{name} 取回原图", r.status_code == 200 and r.content == PNG, r.status_code)
    r = c.post("/api/users/avatar", headers=sh, files={"file": ("a.exe", b"MZ\x90", "application/octet-stream")})
    check("非图片头像被拒", r.status_code == 400, r.status_code)
    r = c.post(f"/api/admin/users/{uid_t}/avatar", headers=H, files={"file": ("b.png", PNG, "image/png")})
    check("管理员代传头像", r.status_code == 200 and (r.json() or {}).get("avatar"), r.text[:200])

    # --- 改密码 ---
    r = c.post("/api/auth/change_password", headers=sh,
               json={"old_password": "pw-smoke-123", "new_password": "pw-smoke-456"})
    check("POST /api/auth/change_password", r.status_code == 200, r.text[:200])
    r = c.post("/api/auth/change_password", headers=sh,
               json={"old_password": "不对", "new_password": "pw-smoke-789"})
    check("原密码错误被拒", r.status_code == 400, r.status_code)
    r = c.post("/api/auth/change_password", headers=sh, json={"old_password": "pw-smoke-456", "new_password": "123"})
    check("新密码太短被拒", r.status_code == 400, r.status_code)
    r = c.post("/api/auth/login", json={"username": "smoke_t1", "password": "pw-smoke-456"})
    check("改密后新密码可登录", r.status_code == 200 and (r.json() or {}).get("token"), r.text[:200])

    # --- 扫码登录全链路(票据 → 已登录设备批准 → 取 token → 一次作废) ---
    r = c.post("/api/auth/qr/ticket")
    qr_data = r.json() or {}
    tk = qr_data.get("ticket", "")
    check("POST /api/auth/qr/ticket", r.status_code == 200 and tk.startswith("qr"), r.text[:200])
    check("POST /api/auth/qr/ticket 服务端原生生成矢量 SVG 二维码", "<svg" in qr_data.get("qr_svg", ""), "")
    r = c.get("/api/auth/qr/status", params={"ticket": tk})
    check("未批准时 status=pending", (r.json() or {}).get("status") == "pending", r.text[:160])
    r = c.post("/api/auth/qr/approve", headers=sh, json={"ticket": tk})
    check("POST /api/auth/qr/approve", r.status_code == 200, r.text[:200])
    r = c.get("/api/auth/qr/status", params={"ticket": tk})
    qs = r.json() if r.status_code == 200 else {}
    check("批准后拿到 token + 用户信息", qs.get("status") == "ok" and bool(qs.get("token")) and qs.get("user"),
          r.text[:200])
    if qs.get("token"):
        rr = c.get("/api/auth/me", headers={"Authorization": "Bearer " + qs["token"]})
        check("扫码换来的 token 可用", rr.status_code == 200, rr.status_code)
    r = c.get("/api/auth/qr/status", params={"ticket": tk})
    check("票据一次作废(第二次取不到 token)", (r.json() or {}).get("status") != "ok", r.text[:160])
    r = c.get("/api/auth/qr/status", params={"ticket": "qr-不存在"})
    check("无效票据 → invalid", (r.json() or {}).get("status") == "invalid", r.text[:160])
    r = c.post("/api/auth/qr/approve", headers=sh, json={"ticket": "qr-不存在"})
    check("批准不存在的票据被拒(404)", r.status_code == 404, r.status_code)

    # --- 公告: 管理端 CRUD + 用户端读取/已读 ---
    r = c.post("/api/admin/announcements", headers=H,
               json={"title": "期中考试安排", "content": "周五下午两点", "level": "imp"})
    aid_g = (r.json() or {}).get("id")
    check("POST /api/admin/announcements(全局)", r.status_code == 200 and aid_g, r.text[:200])
    r = c.post("/api/admin/announcements", headers=H,
               json={"title": "给你的专属通知", "content": "补交作业", "level": "warn",
                     "scope": "user", "target_uid": uid_t})
    aid_u = (r.json() or {}).get("id")
    check("定向公告(指定用户)", r.status_code == 200 and aid_u, r.text[:200])
    r = c.post("/api/admin/announcements", headers=H, json={"title": "短"})
    check("公告标题太短被拒", r.status_code == 400, r.status_code)
    r = c.post("/api/admin/announcements", headers=H, json={"title": "级别非法", "level": "urgent"})
    check("公告级别非法被拒", r.status_code == 400, r.status_code)
    r = c.post("/api/admin/announcements", headers=H, json={"title": "定向没选人", "scope": "user"})
    check("定向公告缺 target_uid 被拒", r.status_code == 400, r.status_code)
    r = c.post("/api/admin/announcements", headers=sh, json={"title": "学生想发公告"})
    check("非管理员发公告被拒", r.status_code in (401, 403), r.status_code)

    r = c.get("/api/admin/announcements", headers=H)
    al = r.json() if r.status_code == 200 else []
    check("GET /api/admin/announcements", r.status_code == 200 and isinstance(al, list) and len(al) >= 2,
          r.text[:160])
    r = c.get("/api/announcements", headers=sh)
    ann = r.json() if r.status_code == 200 else {}
    ids = [x.get("id") for x in ann.get("items", [])]
    check("用户端收到全局 + 专属公告", aid_g in ids and aid_u in ids, ids)
    check("专属公告带 personal 标记",
          any(x.get("id") == aid_u and x.get("personal") is True for x in ann.get("items", [])), "")
    check("未读数与 items 一致",
          ann.get("unread") == sum(1 for x in ann.get("items", []) if not x.get("read")), ann.get("unread"))
    r = c.get("/api/announcements", headers=s3)
    check("别人收不到定向公告", aid_u not in [x.get("id") for x in (r.json() or {}).get("items", [])], "")
    r = c.post("/api/announcements/read", headers=sh, json={"ids": [aid_g, aid_u]})
    check("POST /api/announcements/read", r.status_code == 200, r.text[:160])
    r = c.get("/api/announcements", headers=sh)
    check("上报已读后 unread 归零", (r.json() or {}).get("unread") == 0, (r.json() or {}).get("unread"))
    r = c.patch(f"/api/admin/announcements/{aid_g}", headers=H,
                json={"title": "期中考试安排(顺延)", "content": "下周五", "level": "warn",
                      "scope": "global", "active": False})
    check("PATCH 公告(改标题 + 停用)", r.status_code == 200, r.text[:200])
    r = c.get("/api/announcements", headers=sh)
    check("停用后用户端不再看到", aid_g not in [x.get("id") for x in (r.json() or {}).get("items", [])], "")
    r = c.delete(f"/api/admin/announcements/{aid_u}", headers=sh)
    check("非管理员删公告被拒", r.status_code in (401, 403), r.status_code)
    r = c.delete(f"/api/admin/announcements/{aid_u}", headers=H)
    check("DELETE /api/admin/announcements/{aid}", r.status_code == 200, r.text[:160])

    # --- 论坛: 板块 CRUD + 发帖/回复/编辑/锁定/删除 ---
    r = c.post("/api/admin/forum/boards", headers=H, json={"name": "冒烟板块", "descr": "临时用", "sort": 9})
    bid = (r.json() or {}).get("id")
    check("POST /api/admin/forum/boards", r.status_code == 200 and bid, r.text[:200])
    r = c.post("/api/admin/forum/boards", headers=H, json={"name": "短"})
    check("板块名太短被拒", r.status_code == 400, r.status_code)
    r = c.get("/api/forum/boards", headers=sh)
    boards = (r.json() or {}).get("items", [])
    check("GET /api/forum/boards", r.status_code == 200 and any(b.get("id") == bid for b in boards), r.text[:200])
    r = c.get("/api/forum/boards")
    check("未登录看板块被拒", r.status_code in (401, 403), r.status_code)

    r = c.post("/api/forum/threads", headers=sh, json={"board_id": bid, "title": "第一帖", "content": "正文内容"})
    tid = (r.json() or {}).get("id")
    check("POST /api/forum/threads", r.status_code == 200 and tid, r.text[:200])
    r = c.post("/api/forum/threads", headers=sh, json={"board_id": bid, "title": "", "content": "无标题"})
    check("空标题发帖被拒", r.status_code == 400, r.status_code)
    r = c.get("/api/forum/threads", headers=sh, params={"board": bid})
    check("GET /api/forum/threads?board=",
          r.status_code == 200 and any(t.get("id") == tid for t in (r.json() or {}).get("items", [])), r.text[:200])
    r = c.get(f"/api/forum/threads/{tid}", headers=sh)
    det = r.json() if r.status_code == 200 else {}
    check("GET /api/forum/threads/{tid}", r.status_code == 200 and det.get("title") == "第一帖", r.text[:200])
    r = c.post(f"/api/forum/threads/{tid}/posts", headers=s3, json={"content": "路人回复"})
    check("POST /api/forum/threads/{tid}/posts", r.status_code == 200, r.text[:200])
    r = c.post(f"/api/forum/threads/{tid}/posts", headers=s3, json={"content": "   "})
    check("空回复被拒", r.status_code == 400, r.status_code)
    r = c.get(f"/api/forum/threads/{tid}", headers=sh)
    posts = (r.json() or {}).get("posts", [])
    pid = posts[-1].get("id") if posts else None
    check("回复进了帖子(带回复数)", bool(pid) and (r.json() or {}).get("total") == 1, len(posts))
    r = c.patch(f"/api/forum/posts/{pid}", headers=s3, json={"content": "改过的回复"})
    check("PATCH /api/forum/posts/{pid}(作者可改)", r.status_code == 200, r.text[:200])
    r = c.patch(f"/api/forum/posts/{pid}", headers=sh, json={"content": "别人想改"})
    check("非作者改回复被拒", r.status_code in (401, 403), r.status_code)
    r = c.get(f"/api/forum/threads/{tid}", headers=sh)
    check("改过的回复带 edited 标记", (r.json() or {}).get("posts", [{}])[0].get("edited") is True, "")
    r = c.patch(f"/api/forum/threads/{tid}", headers=sh, json={"title": "第一帖(作者改过)"})
    check("PATCH /api/forum/threads/{tid}(作者改标题)", r.status_code == 200, r.text[:200])
    r = c.patch(f"/api/forum/threads/{tid}", headers=sh, json={"pinned": True})
    check("非管理员置顶被拒", r.status_code == 403, r.status_code)
    r = c.patch(f"/api/forum/threads/{tid}", headers=H, json={"pinned": True, "locked": True})
    check("管理员置顶 + 锁定", r.status_code == 200, r.text[:200])
    r = c.get(f"/api/forum/threads/{tid}", headers=sh)
    dd = r.json() or {}
    check("置顶/锁定已生效", dd.get("pinned") is True and dd.get("locked") is True, dd)
    r = c.post(f"/api/forum/threads/{tid}/posts", headers=s3, json={"content": "锁了还想回复"})
    check("锁定帖普通用户回复被拒", r.status_code == 403, r.status_code)
    r = c.post(f"/api/forum/threads/{tid}/posts", headers=H, json={"content": "管理员可回复锁定帖"})
    check("管理员可回复锁定帖", r.status_code == 200, r.text[:200])
    r = c.delete(f"/api/forum/posts/{pid}", headers=s3)
    check("DELETE /api/forum/posts/{pid}(作者可删)", r.status_code == 200, r.text[:200])
    r = c.delete(f"/api/forum/threads/{tid}", headers=s3)
    check("非作者删帖被拒", r.status_code == 403, r.status_code)
    r = c.delete(f"/api/forum/threads/{tid}", headers=sh)
    check("DELETE /api/forum/threads/{tid}(作者可删)", r.status_code == 200, r.text[:200])
    r = c.get(f"/api/forum/threads/{tid}", headers=sh)
    check("删掉的帖子取不到(404)", r.status_code == 404, r.status_code)
    r = c.patch(f"/api/admin/forum/boards/{bid}", headers=H, json={"name": "冒烟板块(改名)", "descr": "", "sort": 1})
    check("PATCH /api/admin/forum/boards/{bid}", r.status_code == 200, r.text[:200])
    r = c.delete(f"/api/admin/forum/boards/{bid}", headers=H)
    check("DELETE /api/admin/forum/boards/{bid}", r.status_code == 200, r.text[:200])
    r = c.delete(f"/api/admin/forum/boards/{bid}", headers=H)
    check("重复删板块 → 404", r.status_code == 404, r.status_code)

    # --- 邀请码: 批量生成 / 列表 / 删除 ---
    r = c.post("/api/admin/invites/batch", headers=H,
               json={"count": 3, "max_uses": 2, "prefix": "SMK", "note": "批量"})
    items = (r.json() or {}).get("items", [])
    check("POST /api/admin/invites/batch",
          r.status_code == 200 and len(items) == 3 and all(i.get("code", "").startswith("SMK-") for i in items),
          r.text[:200])
    r = c.post("/api/admin/invites/batch", headers=H, json={"count": 0})
    check("批量数量非法被拒", r.status_code == 400, r.status_code)
    r = c.post("/api/admin/invites/batch", headers=H, json={"count": 2, "prefix": "小写前缀"})
    check("非法前缀被拒", r.status_code == 400, r.status_code)
    r = c.get("/api/admin/invites", headers=H)
    il = r.json() if r.status_code == 200 else []
    iid = next((x.get("id") for x in il if str(x.get("code", "")).startswith("SMK-")), None)
    check("GET /api/admin/invites", r.status_code == 200 and isinstance(il, list) and bool(iid), r.text[:160])
    check("邀请码状态字段正确", any(x.get("status") == "active" for x in il), "")
    r = c.get("/api/admin/invites", headers=sh)
    check("非管理员看邀请码被拒", r.status_code in (401, 403), r.status_code)
    r = c.delete(f"/api/admin/invites/{iid}", headers=H)
    check("DELETE /api/admin/invites/{iid}", r.status_code == 200, r.text[:200])

    # --- 管理端: 看某人的文件 / 重置密码 ---
    r = c.get(f"/api/admin/users/{uid_t}/files", headers=H)
    check("GET /api/admin/users/{uid}/files", r.status_code == 200 and isinstance(r.json(), list), r.text[:200])
    r = c.get(f"/api/admin/users/{uid_t}/files", headers=sh)
    check("非管理员查他人文件被拒", r.status_code in (401, 403), r.status_code)
    r = c.post(f"/api/admin/users/{uid_t}/reset_password", headers=H)
    npw = (r.json() or {}).get("password", "")
    check("POST /api/admin/users/{uid}/reset_password", r.status_code == 200 and npw.startswith("YJT"), r.text[:200])
    r = c.post("/api/auth/login", json={"username": "smoke_t1", "password": npw})
    check("重置后的新密码可登录", r.status_code == 200 and (r.json() or {}).get("token"), r.text[:200])
    r = c.post(f"/api/admin/users/{uid_t}/reset_password", headers=sh)
    check("非管理员重置他人密码被拒", r.status_code in (401, 403), r.status_code)

    # --- 文件直读: 原文件 / 下载 / 转换产物优雅降级 ---
    r = c.get(f"/api/files/{fid}/raw", headers=H)
    check("GET /api/files/{fid}/raw 返回原文件", r.status_code == 200 and r.content == pptx, r.status_code)
    r = c.get(f"/api/files/{fid}/download", headers=H)
    check("GET /api/files/{fid}/download 带 attachment 头",
          r.status_code == 200 and "attachment" in (r.headers.get("content-disposition") or ""),
          r.headers.get("content-disposition"))
    r = c.get(f"/api/files/{fid}/raw", headers=s3)
    check("越权读原文件被拒", r.status_code in (401, 403, 404), r.status_code)
    r = c.get(f"/api/files/{fid}/pdf", headers=H)
    check("GET /pdf 未转换完成时优雅返回(409 而非 5xx)", r.status_code in (200, 409), r.status_code)
    for ep in ("anim", "bgpdf", "anim-media/none.png", "onlyoffice/verify"):
        rr = c.get(f"/api/files/{fid}/{ep}", headers=H)
        check(f"GET /{ep} 不炸 5xx", rr.status_code < 500, f"{rr.status_code} {rr.text[:100]}")
    # OnlyOffice 未部署时 config 就是应当明确报 503(前端据此提示"未启用"), 不是崩溃
    rr = c.get(f"/api/files/{fid}/onlyoffice/config", headers=H)
    check("GET /onlyoffice/config 未启用 DS 时明确 503(带中文说明)",
          rr.status_code == 503 and "OnlyOffice" in rr.text, f"{rr.status_code} {rr.text[:100]}")
    r = c.get(f"/api/files/{fid}/onlyoffice/verify", headers=H)
    check("未启用 OnlyOffice 时 verify 明确返回 ok=false",
          r.status_code == 200 and (r.json() or {}).get("ok") is False, r.text[:160])
    r = c.post(f"/api/files/{fid}/retry", headers=H)
    check("POST /api/files/{fid}/retry 重新排队",
          r.status_code == 200 and (r.json() or {}).get("status") in ("converting", "ready", "failed"), r.text[:200])
    r = c.post(f"/api/files/{fid}/retry", headers=s3)
    check("越权重试转换被拒", r.status_code in (401, 403, 404), r.status_code)

    # --- 官方大厅: 普通用户主动 join(幂等) ---
    if official and official.get("id"):
        r = c.post(f"/api/chat/rooms/{official['id']}/join", headers=s3)
        check("POST /api/chat/rooms/{rid}/join(官方大厅)", r.status_code == 200, r.text[:200])
        r = c.post(f"/api/chat/rooms/{official['id']}/leave", headers=s3)
        check("官方大厅不允许退群", r.status_code in (400, 403), r.status_code)

    # --- 课堂工具: 学生名单解析 (Excel / CSV / TXT) ---
    csv_bytes = "姓名,学号\n张三,1001\n李四,1002\n王五,1003\n".encode("utf-8")
    r = c.post("/api/tools/parse-roster", files={"file": ("roster.csv", csv_bytes, "text/csv")})
    check("POST /api/tools/parse-roster 解析 CSV 名单",
          r.status_code == 200 and r.json().get("ok") is True and r.json().get("names") == ["张三", "李四", "王五"], r.text[:100])

    import io
    import zipfile
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        z.writestr("xl/sharedStrings.xml", '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>姓名</t></si><si><t>赵六</t></si><si><t>钱七</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c></row><row r="3"><c r="A3" t="s"><v>2</v></c></row></sheetData></worksheet>')
    xlsx_bytes = bio.getvalue()
    r = c.post("/api/tools/parse-roster", files={"file": ("students.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    check("POST /api/tools/parse-roster 解析 Excel XLSX 名单",
          r.status_code == 200 and r.json().get("ok") is True and r.json().get("names") == ["赵六", "钱七"], r.text[:100])

    section("6. 前端资源")
    r = c.get("/")
    check("首页引入 msstage.js", r.status_code == 200 and "msstage.js" in r.text, r.status_code)
    r = c.get("/js/msstage.js")
    check("msstage.js 已发布", r.status_code == 200 and "MSStage" in r.text, r.status_code)
    r = c.get("/js/viewer.js")
    check("viewer.js 纯净原生微软嵌入预览 (无白框干扰)", r.status_code == 200 and ("pure-ms-view" in r.text or "MSStage" in r.text), r.status_code)
    r = c.get("/js/present.js")
    check("present.js 委托 MSStage", r.status_code == 200 and "MSStage.mount" in r.text, r.status_code)
    r = c.get("/style.css")
    check("ms-stage 样式已发布", r.status_code == 200 and ".ms-stage" in r.text and ".ms-pill" in r.text,
          r.status_code)
    r = c.get("/present.html")
    check("放映页引入 msstage.js", r.status_code == 200 and "msstage.js" in r.text, r.status_code)
    r = c.get("/js/chat.js")
    check("chat.js 已发布(微信级聊天)", r.status_code == 200 and "window.Chat" in r.text, r.status_code)
    r = c.get("/js/app.js")
    check("app.js 把 #/chat 交给 Chat.view()", r.status_code == 200 and "Chat.view()" in r.text, r.text[:120])
    check("app.js 导航栏有未读角标", "chat-badge" in r.text, "")
    r = c.get("/js/ui.js")
    check("ui.js 补齐聊天图标(mic/smile/at…)", r.status_code == 200 and "mic:" in r.text and "smile:" in r.text,
          r.status_code)
    r = c.get("/style.css")
    check("聊天样式已发布", r.status_code == 200 and ".wx-item" in r.text and ".wx-bubble" in r.text, r.status_code)

    section("6b. 认证证书(v1.27 升级)")
    r = c.get("/js/app.js")
    a = r.text if r.status_code == 200 else ""
    check("证书等级算法 certTier", "function certTier(" in a, "")
    check("证书校验码 certCode(FNV-1a)", "function certCode(" in a and "0x811c9dc5" in a, "")
    check("证书交互 bindCertCards(3D/二维码/大图/打印)", "function bindCertCards(" in a and "certZoom" in a
          and "certPrint" in a, "")
    check("证书要素(编号/签发日期/授权范围/校验码)", all(k in a for k in ("证书编号", "签发日期", "校验码")), "")
    r = c.get("/js/admin.js")
    check("后台改认证时实时预览证书", r.status_code == 200 and "euprev" in r.text and "paintPrev" in r.text,
          r.status_code)
    r = c.get("/style.css")
    css = r.text if r.status_code == 200 else ""
    check("全息箔 / 雕刻底纹 / 光束样式", all(k in css for k in (".cc-holo", ".cc-guilloche", ".cc-beam")), "")
    check("证书打印样式(@media print 只印证书)", "@media print" in css and ".cc-print" in css, "")
    check("证书大图弹层样式", ".cc-zoom" in css and ".cc-big" in css, "")
    check("尊重 prefers-reduced-motion", css.count("prefers-reduced-motion") >= 3, css.count("prefers-reduced-motion"))

    section("6c. 安全: 存储型 XSS 防护(用户可控文本进 innerHTML 前必须转义)")
    r = c.get("/js/ui.js")
    uij = r.text if r.status_code == 200 else ""
    check("弹窗标题默认转义(群名/用户名可控)",
          "opts.titleHTML ? (opts.title || '') : esc(opts.title || '')" in uij, "")
    r = c.get("/js/app.js")
    aj = r.text if r.status_code == 200 else ""
    check("需要放图标的标题显式 titleHTML", aj.count("titleHTML: true") >= 2, aj.count("titleHTML: true"))
    check("论坛板块名渲染前转义", "return b ? UI.esc(b.name) : '全站'" in aj, "")
    check("帖子/回复正文先转义再换行", aj.count("UI.esc(t.content).replace(/\\n/g, '<br>')") >= 1
          and aj.count("UI.esc(p.content).replace(/\\n/g, '<br>')") >= 1, "")
    r = c.get("/js/chat.js")
    cj = r.text if r.status_code == 200 else ""
    check("聊天正文 richText 先 esc 再识别链接/@", "var t = esc(s || '');" in cj, "")
    check("系统灰条消息转义", "esc(m.content)" in cj, "")
    check("群名/昵称/签名进卡片前转义",
          "esc(u.nickname)" in cj and "esc(u.username)" in cj and "esc(u.signature)" in cj, "")
    r = c.get("/js/admin.js")
    check("后台用户表格转义昵称", r.status_code == 200 and "UI.esc(u.nickname)" in r.text, r.status_code)

    section("6d. 后台「编辑用户」弹窗样式补齐(v1.27 之前完全没样式)")
    r = c.get("/style.css")
    css2 = r.text if r.status_code == 200 else ""
    for sel in (".edit-user", ".eu-av", ".eu-cert-custom", ".cert-icon-pick", ".cip", ".cip.on",
                ".cert-color-pick", ".ccp", ".ccp.on"):
        check(f"样式存在 {sel}", sel + " " in css2 or sel + "{" in css2 or sel + " {" in css2
              or (sel + ":") in css2 or (sel + ",") in css2, "")
    check("图标选中态看得出来(描边 + 打勾)", ".cip.on::after" in css2, "")
    check("颜色选中态看得出来(勾 + 外环)", ".ccp.on::after" in css2, "")
    check("新特性有老浏览器兜底(color-mix / aspect-ratio)",
          css2.count("color-mix") >= 2 and "height: 44px" in css2, "")
    check("聊天引用条图标与置顶/免打扰标签有样式",
          ".wx-q-i" in css2 and ".wx-tag.pin" in css2 and ".wx-tag.mute" in css2, "")

    section("7. 删除与清理")
    r = c.delete(f"/api/files/{fid}", headers=H)
    check("删除课件", r.status_code == 200, r.text[:160])
    r = c.get(f"/api/files/{fid}/ms-view", headers=H)
    check("删除后 ms-view 404", r.status_code == 404, r.status_code)

    section("8. 部署脚本静态校验(沙箱里没有 docker/nginx, 只能静态验 + 真渲染配置)")
    import subprocess
    root = Path(__file__).resolve().parent.parent

    # 8.1 所有 shell 脚本语法自检
    scripts = sorted(root.glob("*.sh")) + sorted((root / "deploy").glob("*.sh"))
    check("仓库里有部署脚本", len(scripts) >= 6, [x.name for x in scripts])
    for sh in scripts:
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        check(f"bash -n {sh.name}", r.returncode == 0, r.stderr.strip()[:160])

    # 8.2 真渲染 edge.sh 的 nginx 配置(gen_conf), 验证语法级正确性
    edge = (root / "edge.sh").read_text()
    lines = edge.split("\n")
    i0 = lines.index("gen_conf(){")
    i1 = next(i for i in range(i0, len(lines)) if lines[i].strip() == 'chmod 644 "$CONF"')
    i2 = next(i for i in range(i1, len(lines)) if lines[i] == "}")
    func = "\n".join(lines[i0:i2 + 1])

    def render(port, doms, nginx_version, cert_exists, default_ok=1):
        cert = _TMP + "/cert.pem" if cert_exists else _TMP + "/no-such-cert.pem"
        if cert_exists:
            Path(cert).write_text("stub")
        harness = f"""set -u
PORT={port}; WEBROOT=/var/www/fla-acme; DOMS="{doms}"; CONF={_TMP}/edge.conf; LOG={_TMP}/e.log
log(){{ :; }}; try(){{ "$@" 2>/dev/null || true; }}
nginx_ver(){{ echo "{nginx_version}"; }}
ver_ge(){{ [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }}
CERT={cert}; KEY={cert}; DEFAULT_OK={default_ok}
{func}
gen_conf
"""
        hs = Path(_TMP) / "h.sh"
        hs.write_text(harness)
        rr = subprocess.run(["bash", str(hs)], capture_output=True, text=True)
        assert rr.returncode == 0, rr.stderr[:300]
        return Path(_TMP, "edge.conf").read_text()

    conf = render(8306, "class.fyx.best t.clrv.top", "1.20.1", True)
    check("渲染出的 nginx 配置没有残留占位符", "__PROXY__" not in conf, conf.count("__PROXY__"))
    check("大括号平衡", conf.count("{") == conf.count("}"), f'{conf.count("{")}/{conf.count("}")}')
    check("生成 4 个 server 块(两域名 80/443 + catch-all 80/443)",
          len(re.findall(r"^server \{", conf, re.M)) == 4, len(re.findall(r"^server \{", conf, re.M)))
    check("★ 域名专用块: server_name 精确列出两个域名(含 www)",
          conf.count("server_name class.fyx.best www.class.fyx.best t.clrv.top www.t.clrv.top;") == 2,
          conf.count("server_name class.fyx.best"))
    check("★ 同一端口只声明一个 default_server(否则 nginx -t 失败 → 打开是欢迎页)",
          conf.count("listen 80 default_server;") == 1, conf.count("listen 80 default_server;"))
    check("catch-all 块用 server_name _", conf.count("server_name _;") == 2, conf.count("server_name _;"))
    check("443 有 ssl 监听(写法随 nginx 版本)",
          "listen 443 ssl http2;" in conf or "listen 443 ssl;" in conf, "")
    check("反代到 127.0.0.1:8306", "server 127.0.0.1:8306" in conf and "proxy_pass http://fla_backend;" in conf, "")
    check("WebSocket 升级头", "$connection_upgrade" in conf and "$http_upgrade" in conf, "")
    check("X-Forwarded-Proto 传给后端", "X-Forwarded-Proto" in conf, "")
    check("大文件上传不拦", "client_max_body_size" in conf, "")
    check("ACME 文件验证目录挂在 80 上",
          "location ^~ /.well-known/acme-challenge/" in conf and "root /var/www/fla-acme;" in conf, "")
    check("跳 https 时豁免 ACME 路径(否则续期会被 301 打断)",
          "set $fla_redir 0;" in conf and 'if ($uri ~ "^/.well-known/acme-challenge/")' in conf
          and "if ($fla_redir)" in conf, "")
    check("已签证书的域名进 force-https map",
          "class.fyx.best" in conf and "www.class.fyx.best" in conf, "")
    check("老 nginx 用 listen 443 ssl http2 写法", "http2 on;" not in conf, "")

    conf2 = render(8306, "a.com", "1.27.1", True)
    check("新 nginx(1.25.1+) 改用 http2 on;", "http2 on;" in conf2 and "listen 443 ssl;" in conf2, "")
    conf3 = render(8310, "b.com", "1.20.1", False)
    # 没有证书时绝不能写出 listen 443(否则 nginx -t 直接失败, 整站起不来)
    check("没有证书时只生成 80 的 server(不写 listen 443)",
          len(re.findall(r"^server \{", conf3, re.M)) == 2
          and not re.search(r"^\s*listen[^;]*443", conf3, re.M), "")
    check("换端口后反代目标跟着变", "server 127.0.0.1:8310" in conf3, "")
    conf4 = render(8306, "", "1.20.1", True)
    check("纯 IP(无域名)也能生成且大括号平衡", conf4.count("{") == conf4.count("}"), "")

    # ★ 关键回归: 别处已有 default_server 时, 绝不再声明一个(否则 duplicate default server)
    conf5 = render(8306, "a.com b.com", "1.20.1", True, default_ok=0)
    check("检测到 default_server 冲突时不生成 catch-all",
          "default_server" not in conf5
          and conf5.count("server_name a.com www.a.com b.com www.b.com;") == 2,
          conf5.count("default_server"))
    check("冲突时域名反代依然可用(按 Host 精确匹配)",
          "proxy_pass http://fla_backend;" in conf5 and conf5.count("{") == conf5.count("}"), "")

    # 8.2b 中和 nginx.conf 内置欢迎页站点(RHEL 系把默认站点直接写在 nginx.conf 里,
    #      这才是"打开是 Welcome to nginx!"的真正原因)
    mawk = re.search(r"  awk '\n(.*?)\n  ' \"\$NGINX_MAIN\"", edge, re.S)
    check("edge.sh 里有中和 nginx.conf 内置默认站点的 awk", bool(mawk), "")
    check("会先备份 nginx.conf", 'cp -a "$NGINX_MAIN" "$NGINX_MAIN.fla-bak"' in edge, "")
    check("中和是幂等的(已处理过就跳过)", "grep -q '^# \\[fla-disabled\\]'" in edge, "")
    if mawk:
        RHEL = (
            "user nginx;\n"
            "events { worker_connections 1024; }\n"
            "http {\n"
            "    include /etc/nginx/conf.d/*.conf;\n"
            "\n"
            "    server {\n"
            "        listen       80 default_server;\n"
            "        listen       [::]:80 default_server;\n"
            "        server_name  _;\n"
            "        root         /usr/share/nginx/html;\n"
            "\n"
            "        include /etc/nginx/default.d/*.conf;\n"
            "\n"
            "        location / {\n"
            "        }\n"
            "\n"
            "        error_page 404 /404.html;\n"
            "            location = /40x.html {\n"
            "        }\n"
            "    }\n"
            "\n"
            "}\n"
        )
        Path(_TMP, "rhel-nginx.conf").write_text(RHEL)
        Path(_TMP, "neu.awk").write_text(mawk.group(1))
        rr = subprocess.run(["awk", "-f", str(Path(_TMP, "neu.awk")), str(Path(_TMP, "rhel-nginx.conf"))],
                            capture_output=True, text=True)
        out = rr.stdout
        live = [ln for ln in out.split("\n") if not ln.strip().startswith("#")]
        check("RHEL 内置欢迎页站点被整段注释", rr.returncode == 0 and "# [fla-disabled]" in out,
              f"rc={rr.returncode}")
        check("注释后活动配置里再无 default_server", not any("default_server" in ln for ln in live), "")
        check("注释后不再指向 /usr/share/nginx/html", not any("/usr/share/nginx/html" in ln for ln in live), "")
        check("嵌套 location 也被正确算进块里(大括号平衡)", out.count("{") == out.count("}"),
              f'{out.count("{")}/{out.count("}")}')
        check("include conf.d 与 http/events 块保持完好",
              any("include /etc/nginx/conf.d/*.conf;" in ln for ln in live)
              and any("http {" in ln for ln in live) and any("events {" in ln for ln in live), "")
        DEB = "http {\n\tinclude /etc/nginx/conf.d/*.conf;\n\tinclude /etc/nginx/sites-enabled/*;\n}\n"
        Path(_TMP, "deb-nginx.conf").write_text(DEB)
        rr2 = subprocess.run(["awk", "-f", str(Path(_TMP, "neu.awk")), str(Path(_TMP, "deb-nginx.conf"))],
                             capture_output=True, text=True)
        check("Debian 型 nginx.conf 不误伤(退出码 3 且内容不变)",
              rr2.returncode == 3 and rr2.stdout == DEB, f"rc={rr2.returncode}")

    # 8.2c 停用默认站点必须"移出 include 范围"(Debian 的 include sites-enabled/* 会把
    #      就地改名成 default.fla-disabled 的文件照样包含进去, 等于没停用)
    check("停用默认站点是移出目录而不是就地改名",
          'DISABLED_DIR="/etc/nginx/fla-disabled"' in edge and 'mv -f "$f" "$DISABLED_DIR/$base"' in edge, "")
    check("remove 时按 manifest 精确还原", "MANIFEST" in edge and "已恢复默认站点" in edge, "")
    check("remove 时还原 nginx.conf 备份", 'cp -f "$NGINX_MAIN.fla-bak" "$NGINX_MAIN"' in edge, "")
    check("提供 doctor / fix 两个动作",
          "do_doctor(){" in edge and "do_fix(){" in edge
          and "doctor) do_doctor ;;" in edge and "fix)    do_fix ;;" in edge, "")
    check("落地验证会识别 Welcome to nginx 并给补救命令",
          "Welcome to nginx" in edge and "PROBE_KIND=welcome" in edge and "edge.sh fix" in edge, "")

    check("edge.sh 支持 no-catchall 移除默认监听，仅保留指定域名与 8306",
          "no-catchall" in edge and "NO_CATCHALL" in edge, "")

    # 8.3 SSL: 必须是文件验证(HTTP-01), 不能抢占 80
    hs = (root / "https.sh").read_text()
    check("https.sh 用 --webroot 文件验证", "--webroot" in hs and '-w "$WEBROOT"' in hs, "")
    # --standalone 只允许出现在注释里(说明为什么不用), 绝不能出现在真实命令中
    standalone_cmds = [l for l in hs.split("\n")
                       if "--standalone" in l and not l.strip().startswith("#")]
    check("https.sh 不用 --standalone 抢占 80(无需停 nginx, 无停机窗口)", not standalone_cmds,
          standalone_cmds[:2])
    check("webroot 与 edge.sh 一致(/var/www/fla-acme)",
          'WEBROOT="/var/www/fla-acme"' in hs and 'WEBROOT="/var/www/fla-acme"' in edge, "")
    check("certbot 缺失时降级 acme.sh(同样是文件验证)", "acme.sh" in hs and "--webroot" in hs, "")
    check("签发前有连通性预检", "/.well-known/acme-challenge/" in hs, "")
    check("自动续期(cron + systemd timer)", "cron" in hs.lower() and "OnCalendar" in hs, "")
    check("续期后重载 nginx", "reload" in hs, "")
    check("写入 PUBLIC_BASE_URL(微软放映直链要用 https 域名)", "PUBLIC_BASE_URL" in hs, "")

    # 8.4 install.sh 必须自动串起网关 + 证书
    ins = (root / "install.sh").read_text()
    check("install.sh 自动调用 edge.sh(装 80/443 网关)", "edge.sh" in ins, "")
    check("install.sh 自动调用 https.sh(签证书)", "https.sh" in ins, "")
    check("默认端口 8306 且被占自动顺延", "8306" in ins and "8307" in ins and "8310" in ins, "")
    check("80/443 留给边缘网关(不再由 FLA 直接占)", "--port" in ins, "")

    # 8.5 彻底重装: 删所有容器 + docker 本体, 再重装
    cn = (root / "completely_new_install.sh").read_text()
    check("删掉所有容器(不止 FLA)", "docker ps -aq" in cn or "docker rm -f" in cn, "")
    check("删镜像/卷/网络", "docker system prune" in cn or ("docker volume" in cn and "docker network" in cn), "")
    check("卸载 docker 本体", re.search(r"(yum|dnf|apt-get)[^\n]*(remove|erase|purge)[^\n]*docker", cn) is not None, "")
    check("清 /var/lib/docker 与 /etc/docker", "/var/lib/docker" in cn and "/etc/docker" in cn, "")
    check("默认先备份 fla-data 卷", "fla-backup" in cn or "BACKUP_FILE" in cn, "")
    check("有 --keep-data / --no-backup / --force 开关",
          "--keep-data" in cn and "--no-backup" in cn and "--force" in cn, "")
    check("需要确认词 YES-DELETE-ALL", "YES-DELETE-ALL" in cn, "")
    check("清完会重新装 docker 并转交 install.sh", "install.sh" in cn, "")

    # 8.6 容器内 nginx(打包进镜像的那份)也要有 WS 升级
    napp = (root / "deploy" / "nginx-app.conf").read_text()
    check("容器内 nginx 有 WebSocket 升级头",
          "$connection_upgrade" in napp and "Upgrade" in napp, "")
    check("容器内 nginx 反代到 app:8000", "app_upstream" in napp or "app:8000" in napp, "")

    # 8.7 关键修复与增强检验 (扫码二维码/安装脚本自愈/PPT分步动画/双模橡皮)
    qr_js = (root / "web" / "lib" / "qrcode" / "qrcode.min.js").read_text()
    check("全新工业级二维码生成库与适配器", "QRCode" in qr_js and ("createSvgTag" in qr_js or "createTableTag" in qr_js), "")
    idx_html = (root / "web" / "index.html").read_text()
    check("index.html 显式预载 qrcode 库", "lib/qrcode/qrcode.min.js" in idx_html, "")

    check("install.sh 保护 nginx 日志打印(仅当容器存在时输出)",
          "grep -qx 'nginx'" in ins and "docker logs --tail 40 nginx" in ins, "")
    check("install.sh 具备容器内部健康探针(防止回环 NAT 误杀)",
          "check_fla_internal" in ins, "")

    pptx_anim_py = (root / "server" / "pptx_anim.py").read_text()
    check("pptx_anim 独立区分 clickEffect 步进动画",
          "click_nodes" in pptx_anim_py and "clickEffect" in pptx_anim_py, "")

    ms_js = (root / "web" / "js" / "msstage.js").read_text()
    check("放映舞台支持对象橡皮与像素橡皮双模式",
          "data-em=\"object\"" in ms_js and "data-em=\"pixel\"" in ms_js, "")
    check("放映舞台支持橡皮粗细无级滑动设定",
          "6, 120" in ms_js and "erasePixelSeg" in ms_js, "")

    pres_js = (root / "web" / "js" / "present.js").read_text()
    check("内部放映页支持激光笔多键位步进动画",
          "isNext" in pres_js and "advance()" in pres_js, "")

    # 8.8 简化发版: 彻底剥离 AI 识屏与复杂同步，回归教师纯净手动对齐与翻页
    check("放映舞台彻底移除复杂同步与AI识屏(纯净手动翻页)",
          "FLA_OCR" not in ms_js and "ms-sync-pill" not in ms_js and "toggleOcrSync" not in ms_js, "")
    check("index.html 与 present.html 彻底移除 ocr.js 依赖",
          "ocr.js" not in idx_html and "ocr.js" not in (root / "web" / "present.html").read_text(), "")
    check("放映舞台右侧栏增加缩略图按钮并保留顶栏原按钮",
          "rbtn('film'" in ms_js and 'data-a="film"' in ms_js, "")

    # 8.9 屏幕点击翻页 / 二维码100%渲染保障 / 扫码授权跳回
    dc_main = (root / "deploy" / "docker-compose.yml").read_text()
    dc_lite = (root / "deploy" / "docker-compose.lite.yml").read_text()
    check("docker-compose 具备 web/ 和 server/ 实时文件映射挂载",
          "../web:/app/web" in dc_main and "../server:/app/server" in dc_main and
          "../web:/app/web" in dc_lite and "../server:/app/server" in dc_lite, "")

    check("docker-compose 中 fla 容器直接映射对外发布 8306 端口且剔除占位 nginx 容器",
          "${PORT:-8306}:${PORT:-8306}" in dc_main and "${PORT:-8306}:${PORT:-8306}" in dc_lite and
          "container_name: nginx" not in dc_main and "container_name: nginx" not in dc_lite, "")

    r_sh = (root / "run.sh").read_text()
    check("run.sh pull 支持一键自动清理旧占位 nginx 容器并以 8306->8306 拉起 fla",
          "docker rm -f nginx" in r_sh and "up -d --remove-orphans" in r_sh, "")

    check("放映舞台彻底移除画布翻页自动跳转与触屏误触(回归纯手动翻页)",
          "wasTap" not in ms_js and "goPage(p)" not in ms_js and "prevPage()" in ms_js and "nextPage()" in ms_js, "")

    check("放映舞台左侧工具栏保留左右翻页按键用于独立切换画布页数(与微软独立)",
          "pgprev" in ms_js and "pgnext" in ms_js and "goCanvasPage" in ms_js, "")

    check("放映舞台画布切换与微软课件独立(只能靠点击触发，不重载微软iframe)",
          "canvasNextPage" in ms_js and "canvasPrevPage" in ms_js and "showSlide" not in ms_js.split("function goCanvasPage")[1].split("function canvasNextPage")[0], "")

    check("放映舞台缩略图专用于控制课件PPT页数且不影响画布",
          "showSlide(n)" in ms_js.split("function buildFilm")[1].split("function markFilm")[0] and "goCanvasPage" not in ms_js.split("function buildFilm")[1].split("function markFilm")[0], "")

    check("放映舞台对上下左右及翻页笔按键完全放行并聚焦iframe原生响应",
          "focusIframe()" in ms_js and "arrow(up|down|left|right)" in ms_js and "isNext" not in ms_js, "")

    sync_doc = (root / "docs" / "SLIDE_SYNC_BACKUP.md").read_text()
    check("备用技术文档详细记录课件与画布自动同步实现方案",
          (root / "docs" / "SLIDE_SYNC_BACKUP.md").exists() and "sftc=1" in sync_doc and "App_IsFrameTrusted" in sync_doc, "")

    check("放映舞台内置课堂计时器(倒计时+秒表+音效提醒+最小化)",
          "fla-timer-widget" in ms_js and "countdown" in ms_js and "stopwatch" in ms_js and "playAlarm" in ms_js and "fla-timer-min" in ms_js, "")

    check("放映舞台内置随机抽选(名单管理+本机Excel/CSV解析+数字摇号+彩带动效)",
          "fla-picker-widget" in ms_js and "xlsx.full.min.js" in ms_js and "reader.readAsArrayBuffer(f)" in ms_js and
          "startPickerRoll" in ms_js and "finalizePick" in ms_js and "launchConfetti" in ms_js, "")
    check("投屏舞台浮窗复用 Interact.js 实现拖动缩放，PPT 两侧提供大翻页箭头",
          "interact.min.js" in (root / "web" / "index.html").read_text() and "makeDraggable(bnb" in ms_js and
          "edges: { right: '.fla-resize-grip', bottom: '.fla-resize-grip' }" in ms_js and
          "ms-side-arrow" in (root / "web" / "style.css").read_text(), "")
    check("课堂计时器支持自定义分秒及本机记忆的浮窗尺寸",
          "tmCustomMin" in ms_js and "fla_ms_timer_rect" in ms_js and "fla-widget-resize" in ms_js, "")

    check("放映舞台向微软iframe发送postMessage穿透步进指令(元素级动画步进)",
          "sendMsAction" in ms_js and "Action_StepNext" in ms_js and "Action_NextSlide" in ms_js and "postMessage" in ms_js, "")

    check("放映舞台笔迹画布释放微软底栏控制区且翻页笔持续获得焦点",
          "botI" in ms_js and "v.h - botI" in ms_js and "focusIframe" in ms_js, "")

    check("放映舞台支持工具栏个性化定制与持久化",
          "fla-settings-modal" in ms_js and "fla_tb_prefs" in ms_js and "applyTbPrefs" in ms_js, "")

    app_js = (root / "web" / "js" / "app.js").read_text()
    ui_js = (root / "web" / "js" / "ui.js").read_text()
    check("UI 模块内置零依赖 SVG 二维码生成引擎与全局适配",
          "renderQR" in ui_js and "renderQrSvgFallback" in ui_js and "QRCode" in ui_js, "")

    check("app.js 扫码登录零异步脚本阻塞，保障秒级渲染",
          "loadScript" not in app_js.split("startQrLogin")[1].split("refresh")[0], "")

    check("扫码授权页面未登录时自动暂存目标并在登录后返回授权",
          "parts[0] === 'qr-approve'" in app_js and "fla_after_login" in app_js, "")

    check("登录后路由保障已在目标页时强制刷新路由(解决 t.clrv.top 登录卡滞)",
          "location.hash === target" in app_js and "route()" in app_js, "")

    check("present.html 正确引用相对路径 msstage.js",
          'src="js/msstage.js' in (root / "web" / "present.html").read_text(), "")

    ai_js = (root / "web" / "js" / "ai.js").read_text()
    ai_py = (root / "server" / "routers" / "ai.py").read_text()
    desktop_window = (root / "desktop" / "qt" / "fla_qt" / "window.py").read_text()
    check("Web 与桌面 AI 皆支持用户主动开启联网搜索并提示第三方隐私边界",
          "online_search" in ai_js and "_web_search(search_query)" in ai_py and
          "online_search" in desktop_window and "DuckDuckGo" in desktop_window, "")
    mobile_main = (root / "mobile" / "android" / "app" / "src" / "main" / "java" / "top" / "clrv" / "fla" / "MainActivity.java").read_text()
    mobile_home = (root / "mobile" / "android" / "app" / "src" / "main" / "assets" / "home.html").read_text()
    mobile_build = (root / "mobile" / "android" / "build_apk.sh").read_text()
    check("安卓更新比较读取 Gradle BuildConfig；直连 APK 构建也生成同版本 BuildConfig",
          "BuildConfig.VERSION_CODE" in mobile_main and "BuildConfig.java" in mobile_build and
          "home-grid" in mobile_home and "castEntry" in mobile_home, "")
    cast_py = (root / "desktop" / "qt" / "fla_qt" / "cast.py").read_text()
    check("桌面手机画面浮窗支持置顶、拖动与缩放，控制条可结束投屏",
          "WindowStaysOnTopHint" in cast_py and "QSizeGrip(self)" in cast_py and
          "stage.add_floating(self.phone_view)" in cast_py and "ctl.stop" in cast_py, "")
    core_py = (root / "desktop" / "qt" / "fla_qt" / "core.py").read_text()
    check("缓存、更新包与课件下载使用用户可配置路径",
          "def set_storage_dir" in core_py and "def download_path" in core_py and
          'return cache_path("FLA_update.exe")' in core_py, "")
    check("桌面提供可选的开源实验工具官方项目目录",
          "class ExperimentsPage(Page):" in desktop_window and "Tracker" in desktop_window and
          "Avogadro" in desktop_window and 'self.experiments, FIF.LINK, "实验工具"' in desktop_window, "")
    lib_readme = (root / "web" / "lib" / "README.md").read_text()
    check("开源拖动缩放、离线名单解析与抽选动效库附许可证/来源说明",
          "interact.js 1.10.28" in lib_readme and "SheetJS Community Edition 0.20.3" in lib_readme and
          "canvas-confetti 1.9.3" in lib_readme, "")

    # 8.10 前端脚本零语法错误与初始化执行验证 (杜绝 "加载中…" 卡死)
    node_files = ["web/js/api.js", "web/js/ui.js", "web/js/app.js", "web/js/admin.js", "web/js/chat.js",
                  "web/js/ai.js", "web/js/msstage.js", "web/js/viewer.js", "web/lib/interactjs/interact.min.js",
                  "web/lib/xlsx/xlsx.full.min.js", "web/lib/canvas-confetti/confetti.browser.js"]
    node_errors = []
    for node_file in node_files:
        node_chk = subprocess.run(["node", "--check", node_file], capture_output=True, text=True, cwd=str(root))
        if node_chk.returncode:
            node_errors.append(node_file + ": " + node_chk.stderr.strip())
    check("前端 JavaScript 与本地开源库逐个编译零语法错误", not node_errors, "\n".join(node_errors))

    # === 5f. 手机投屏遥控与桌面助手打包 (v1.28) ============================
    print("\n== 5f. 手机投屏遥控与桌面助手打包 (v1.28) =========================")
    rm_cr = c.post("/api/remote/create", json={"title": "语文课件", "fid": 1, "page": 1, "total": 12})
    check("创建手机投屏遥控会话", rm_cr.status_code == 200 and "session_id" in rm_cr.json(), "")
    sid = rm_cr.json().get("session_id")
    code = rm_cr.json().get("code")

    rm_info = c.get(f"/api/remote/{sid}/info?code={code}")
    check("查询手机遥控会话信息", rm_info.status_code == 200 and rm_info.json().get("title") == "语文课件", "")

    rm_st = c.post(f"/api/remote/{sid}/state", json={"page": 3, "total": 12, "black": False})
    check("同步遥控放映状态", rm_st.status_code == 200 and rm_st.json().get("ok") is True, "")

    rm_act = c.post(f"/api/remote/{sid}/action", json={"action": "stepNext", "data": {"step": 1}})
    check("手机发送步进/翻页指令", rm_act.status_code == 200 and rm_act.json().get("ok") is True, "")

    rm_poll = c.get(f"/api/remote/{sid}/poll?after=0")
    check("轮询遥控指令队列", rm_poll.status_code == 200 and len(rm_poll.json().get("events", [])) >= 1, "")

    dl_desk = c.get("/api/tools/download-desktop")
    check("GET /api/tools/download-desktop 下载桌面客户端 (EXE/ZIP)",
          dl_desk.status_code == 200 and len(dl_desk.content) > 1000, "")

    # v1.28 免登录桌面端分发与自动更新接口
    ver_resp = c.get("/api/desktop/version")
    check("GET /api/desktop/version 免登录版本检测接口",
          ver_resp.status_code == 200 and ver_resp.json().get("version") in ["1.28.0", "1.35.0", "1.36.0"] and "/api/desktop/download" in ver_resp.json().get("download_url", "") and len(ver_resp.json().get("changelog", [])) >= 3, ver_resp.text)

    dl_exe = c.get("/api/desktop/download")
    check("GET /api/desktop/download 免登录直连下载 Windows 单文件 EXE",
          dl_exe.status_code == 200 and "FLA.exe" in dl_exe.headers.get("content-disposition", "") and len(dl_exe.content) > 1000, "")

    desk_dir = root / "desktop"
    check("桌面端模块完整性 (main/seewo/com/overlay/server/build/ui/updater/csharp)",
          (desk_dir / "main.py").exists() and
          (desk_dir / "seewo_interceptor.py").exists() and
          (desk_dir / "ppt_controller.py").exists() and
          (desk_dir / "overlay_window.py").exists() and
          (desk_dir / "local_server.py").exists() and
          (desk_dir / "auto_updater.py").exists() and
          (desk_dir / "ui.py").exists() and
          (desk_dir / "FLA_Client.cs").exists() and
          (desk_dir / "package_exe.py").exists() and
          (desk_dir / "bin" / "FLA.exe").exists() and
          (root / "web" / "downloads" / "FLA.exe").exists() and
          (desk_dir / "build_exe.py").exists() and
          (desk_dir / "run.bat").exists() and
          (desk_dir / "build.bat").exists() and
          (desk_dir / "setup_protocol.bat").exists(), "")

    # 客户端版本比较算法与自更新逻辑
    from desktop.auto_updater import is_newer_version
    check("版本比对算法 (1.29.0 > 1.28.0)", is_newer_version("1.29.0", "1.28.0") is True, "")
    check("版本比对算法 (1.28.0 不大于 1.28.0)", is_newer_version("1.28.0", "1.28.0") is False, "")

    sim_node = subprocess.run([
        "node", "-e",
        'const fs = require("fs"); const vm = require("vm");\n'
        'const els = {}; function el(id) { return els[id] || (els[id] = { id, innerHTML: "", style: {}, classList: { add(){}, remove(){}, contains(){return false}, toggle(){} }, appendChild(){}, querySelector(s){ return el(s.replace(/^#/,"")); }, querySelectorAll(){ return []; } }); }\n'
        'const ctx = { window: {}, document: { title: "", readyState: "complete", documentElement: { scrollTop: 0, tagName: "html", style: {} }, head: { appendChild(){} }, body: { style: {}, appendChild(){} }, createElement(t){ return el("e_"+Math.random()); }, getElementById: el, querySelector(s){ return el(s.replace(/^#/,"")); }, querySelectorAll(){ return []; }, addEventListener(){} }, location: { hash: "", origin: "https://t.clrv.top" }, localStorage: { getItem(){return null}, setItem(){}, removeItem(){} }, sessionStorage: { getItem(){return null}, setItem(){}, removeItem(){} }, navigator: { userAgent: "Node" }, fetch(){ return Promise.resolve({ ok: true, json(){ return Promise.resolve({}); } }); }, console, setTimeout, clearTimeout, setInterval, clearInterval };\n'
        'ctx.window = ctx; ctx.globalThis = ctx; ctx.window.addEventListener = ()=>{};\n'
        '["lib/qrcode/qrcode.min.js", "js/api.js", "js/ui.js", "js/app.js", "js/admin.js", "js/chat.js", "js/remote.js", "js/msstage.js", "js/viewer.js"].forEach(p => vm.runInNewContext(fs.readFileSync("web/" + p, "utf8"), ctx));\n'
        'if (!els["app"] || !els["app"].innerHTML || els["app"].innerHTML.includes("加载中…")) process.exit(1);\n'
        'if (!els["app"].innerHTML.includes("home-portal") || !els["app"].innerHTML.includes("/api/desktop/download")) process.exit(2);\n'
    ], capture_output=True, text=True, cwd=str(root))
    check("未登录访问默认展示官网门户而非直接跳登录", sim_node.returncode == 0, sim_node.stderr)

    return finish()


def finish():
    print("\n" + "=" * 66)
    print(f"通过 {len(PASS)} 项, 失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
    print("=" * 66)
    shutil.rmtree(_TMP, ignore_errors=True)
    code = 1 if FAIL else 0
    sys.stdout.flush()      # os._exit 不会冲刷缓冲, 必须先 flush
    os._exit(code)          # 转换线程是常驻线程, 直接退出进程


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:                                        # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("\n冒烟测试异常中断:", e)
        shutil.rmtree(_TMP, ignore_errors=True)
        sys.stdout.flush()
        os._exit(2)
