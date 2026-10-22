"""FLA - 手机投屏与远程授课遥控路由 (v1.28)
支持手机端实时遥控电脑 PPT/办公文件放映：
- 翻页 / 动画步进 / 步退
- 实时激光笔同步 (手机触控板模拟红外激光点)
- 白板画笔实时同步
- 黑屏 / 计时器 / 抽选联动
- 同时支持 WebSocket 高速实时通信 与 HTTP 轮询兜底 (适配校园复杂防火墙网络)
"""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

router = APIRouter(prefix="/api/remote", tags=["remote"])

# 内存会话管理
SESSIONS: Dict[str, dict] = {}
MAX_SESSION_AGE = 86400  # 24小时有效
ALLOWED_BASES = {"https://t.clrv.top", "https://t.fyx.best"}
MAX_FRAME = 3 * 1024 * 1024


class CreateSessionReq(BaseModel):
    title: Optional[str] = "课堂放映"
    fid: Optional[int] = None
    page: Optional[int] = 1
    total: Optional[int] = 1


class UpdateStateReq(BaseModel):
    title: Optional[str] = None
    page: Optional[int] = None
    total: Optional[int] = None
    black: Optional[bool] = None


class ActionReq(BaseModel):
    action: str  # next, prev, stepNext, stepPrev, goto, laser, ink, clear, black
    data: Optional[dict] = None


def cleanup_expired():
    now = time.time()
    expired = [k for k, v in SESSIONS.items() if now - v.get("created_at", 0) > MAX_SESSION_AGE]
    for k in expired:
        SESSIONS.pop(k, None)


# ---- iOS 整屏投屏: 广播扩展(ReplayKit)没有界面, 按设备 identifierForVendor 取主 App 登记的会话
IOS_REG: Dict[str, dict] = {}


@router.post("/ios/register")
def ios_register(body: dict):
    vid = str(body.get("vid") or "")[:64]
    sid, code = str(body.get("sid") or ""), str(body.get("code") or "")
    if len(vid) < 8 or sid not in SESSIONS or SESSIONS[sid].get("code") != code:
        raise HTTPException(400, "bad request")
    now = time.time()
    for k in [k for k, v in IOS_REG.items() if now - v["t"] > 43200]:
        IOS_REG.pop(k, None)
    IOS_REG[vid] = {"sid": sid, "code": code, "t": now}
    return {"ok": True}


@router.get("/ios/session")
def ios_session(vid: str = ""):
    r = IOS_REG.get(vid)
    if not r or r["sid"] not in SESSIONS:
        raise HTTPException(404, "no session")
    return {"sid": r["sid"], "code": r["code"]}


# ---- iPhone 整屏投屏 (App Store 推流 App → 内置 RTMP 接收端)
import re as _re


def _rtmp_host(request: Request) -> str:
    h = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


@router.get("/rtmp/status")
def rtmp_status(key: str, request: Request):
    from .. import rtmp_ingest
    if not _re.fullmatch(r"[a-z0-9]{12,40}", key or ""):
        raise HTTPException(400, "密钥格式错误")
    d = rtmp_ingest.status(key)
    d["url"] = "rtmp://%s:%d/live" % (_rtmp_host(request), rtmp_ingest.RTMP_PORT)
    d["running"] = rtmp_ingest._server is not None
    return d


@router.get("/rtmp/debug")
def rtmp_debug(request: Request):
    """排错: 仅本机访问 (curl http://127.0.0.1:8306/api/remote/rtmp/debug)"""
    from .. import rtmp_ingest
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "仅限服务器本机访问")
    try:
        import av  # noqa
        pyav = getattr(av, "__version__", "yes")
    except Exception as e:
        pyav = "未安装: %s" % e
    import shutil
    return {"running": rtmp_ingest._server is not None, "pyav": pyav, "ffmpeg": shutil.which("ffmpeg"),
            "live": rtmp_ingest.debug(), "bindings": len(rtmp_ingest.BINDINGS)}


@router.post("/{sid}/rtmp-bind")
def rtmp_bind(sid: str, body: dict, request: Request, code: Optional[str] = Query(None)):
    """手机扫码连上电脑后, 把老师的个人推流密钥绑定到这台电脑 (之后推流画面自动出现在这台电脑上)"""
    from .. import rtmp_ingest
    _auth(sid, code)
    key = str(body.get("key") or "")
    if not _re.fullmatch(r"[a-z0-9]{12,40}", key):
        raise HTTPException(400, "密钥格式错误")
    rtmp_ingest.bind(key, sid, request.client.host if request.client else "")
    return rtmp_status(key, request)


@router.post("/create")
def create_session(req: CreateSessionReq, request: Request):
    cleanup_expired()
    sid = secrets.token_hex(8)
    code = f"{secrets.randbelow(9000) + 1000}"  # 4位随机验证码
    session = {
        "id": sid,
        "code": code,
        "title": req.title or "课堂放映",
        "fid": req.fid,
        "page": req.page or 1,
        "total": req.total or 1,
        "black": False,
        "created_at": time.time(),
        "last_active": time.time(),
        "events": [],  # 待消费事件队列 (for HTTP poll)
        "connections": set(),  # WebSocket 活跃连接
    }
    SESSIONS[sid] = session
    return {
        "ok": True,
        "session_id": sid,
        "code": code,
        "remote_url": f"/#/remote?sid={sid}&code={code}",
        "qr_img_url": f"/api/remote/{sid}/qr",
    }


@router.get("/{sid}/qr")
@router.head("/{sid}/qr")
def get_session_qr(sid: str, request: Request):
    """返回该投屏会话的二维码直链 (PNG 图像格式，供桌面端与各类客户端原生直载渲染)."""
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "遥控会话不存在")
    import io
    try:
        import qrcode
    except ImportError:
        from ..vendor import qrcode

    base_url = str(request.base_url).rstrip("/")
    try:
        from .. import msview
        base_url = msview.public_base(request).rstrip("/") or base_url
    except Exception:
        pass
    want = (request.query_params.get("base") or "").rstrip("/")
    if want in ALLOWED_BASES:
        base_url = want
    if request.query_params.get("kind") == "cast":
        target_url = f"{base_url}/cast.html?sid={sid}&code={session['code']}"
    else:
        target_url = f"{base_url}/#/remote?sid={sid}&code={session['code']}"
    img = qrcode.make(target_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/pair/{code}")
def find_session_by_code(code: str):
    """通过 4 位配对码快速查找活跃的放映遥控会话 (供手机直接输入配对码连接)."""
    cleanup_expired()
    for sid, s in SESSIONS.items():
        if s.get("code") == code.strip():
            return {
                "ok": True,
                "session_id": sid,
                "code": s.get("code"),
                "title": s.get("title", "课堂放映"),
                "page": s.get("page", 1),
                "total": s.get("total", 1),
            }
    raise HTTPException(404, "未找到该配对码对应的放映会话，请确认大屏已开启遥控")


@router.get("/{sid}/info")
def get_session_info(sid: str, code: Optional[str] = Query(None)):
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "遥控会话不存在或已过期")
    if code and session.get("code") != code:
        raise HTTPException(403, "遥控配对验证码错误")
    session["last_active"] = time.time()
    return {
        "ok": True,
        "id": sid,
        "title": session["title"],
        "fid": session.get("fid"),
        "page": session.get("page", 1),
        "total": session.get("total", 1),
        "black": session.get("black", False),
        "lan": session.get("lan", []) if code else [],
    }


@router.post("/{sid}/state")
def update_session_state(sid: str, req: UpdateStateReq):
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "遥控会话不存在")
    if req.title is not None:
        session["title"] = req.title
    if req.page is not None:
        session["page"] = req.page
    if req.total is not None:
        session["total"] = req.total
    if req.black is not None:
        session["black"] = req.black

    # 广播状态变更通知
    evt = {"type": "state", "state": {
        "title": session["title"],
        "page": session["page"],
        "total": session["total"],
        "black": session["black"],
    }}
    _broadcast_event(session, evt)
    return {"ok": True}


@router.post("/{sid}/action")
def send_action(sid: str, req: ActionReq):
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "遥控会话不存在")
    evt = {
        "id": secrets.token_hex(4),
        "type": "action",
        "action": req.action,
        "data": req.data or {},
        "time": time.time(),
    }
    _broadcast_event(session, evt)
    return {"ok": True}


# ---------------- 画面通道 (v3.0 桌面端): pc = 电脑屏幕→手机观看, phone = 手机摄像头→电脑大屏
def _auth(sid: str, code: Optional[str]) -> dict:
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "会话不存在或已过期")
    if session.get("code") != (code or ""):
        raise HTTPException(403, "配对码错误")
    session["last_active"] = time.time()
    return session


@router.post("/{sid}/frame/{ch}")
async def put_frame(sid: str, ch: str, request: Request, code: Optional[str] = Query(None)):
    if ch not in ("pc", "phone"):
        raise HTTPException(400, "通道无效")
    session = _auth(sid, code)
    body = await request.body()
    if not body or len(body) > MAX_FRAME or not body.startswith(b"\xff\xd8"):
        raise HTTPException(400, "需要 JPEG 图像 (≤3MB)")
    frames = session.setdefault("frames", {})
    seq = frames.get(ch, (0, b"", 0))[0] + 1
    frames[ch] = (seq, body, time.time())
    # 桥接: 手机走 HTTP 兜底、电脑已在 hub 上时, 直接推给电脑
    if ch == "phone":
        for ws in list(HUBS.get(sid, {}).get("pc", ())):
            asyncio.create_task(_hub_safe(ws, b"C" + body, True))
    return {"ok": True, "seq": seq}


@router.get("/{sid}/frame/{ch}")
def get_frame(sid: str, ch: str, code: Optional[str] = Query(None), after: int = 0):
    session = _auth(sid, code)
    if ch == "phone":
        session["pc_poll"] = time.time()      # 电脑端在 HTTP 轮询手机画面
    f = session.get("frames", {}).get(ch)
    if not f or f[0] <= after:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return Response(content=f[1], media_type="image/jpeg",
                    headers={"X-Seq": str(f[0]), "Cache-Control": "no-store", "Access-Control-Expose-Headers": "X-Seq"})


@router.post("/{sid}/frame/{ch}/clear")
def clear_frame(sid: str, ch: str, code: Optional[str] = Query(None)):
    session = _auth(sid, code)
    session.get("frames", {}).pop(ch, None)
    return {"ok": True}


@router.get("/{sid}/poll")
def poll_events(sid: str, after: int = 0):
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "遥控会话不存在")
    evts = [e for e in session["events"] if e.get("idx", 0) > after]
    return {"ok": True, "events": evts, "latest": session.get("seq", 0)}


def _broadcast_event(session: dict, evt: dict):
    # 加入 HTTP 轮询队列
    idx = session.get("seq", 0) + 1   # 单调递增 (旧实现裁剪队列后 idx 会重复, 轮询端卡死)
    session["seq"] = idx
    evt["idx"] = idx
    session["events"].append(evt)
    if len(session["events"]) > 300:
        session["events"] = session["events"][-300:]

    # 向所有活跃 WebSocket 连接广播
    for ws in list(session.get("connections", [])):
        try:
            asyncio.create_task(ws.send_json(evt))
        except Exception:
            pass


@router.websocket("/ws/{sid}")
async def remote_websocket(websocket: WebSocket, sid: str, role: str = "controller"):
    session = SESSIONS.get(sid)
    if not session:
        await websocket.close(code=4004)
        return

    await websocket.accept()
    session["connections"].add(websocket)

    # 发送当前状态
    await websocket.send_json({
        "type": "init",
        "role": role,
        "state": {
            "title": session["title"],
            "page": session["page"],
            "total": session["total"],
            "black": session["black"],
        }
    })

    # 广播 presence 事件(其余对端可见, 也进入 HTTP 轮询队列)
    _broadcast_event(session, {"type": "hello", "role": role, "time": time.time()})

    try:
        while True:
            data = await websocket.receive_json()
            # 转发至 session 所有其他对端
            evt_type = data.get("type", "action")
            broadcast_msg = {
                "type": evt_type,
                "sender": role,
                "action": data.get("action"),
                "data": data.get("data", {}),
                "time": time.time(),
            }
            # 更新状态
            if data.get("action") == "goto" and "page" in data.get("data", {}):
                session["page"] = data["data"]["page"]
            elif data.get("action") == "black":
                session["black"] = data.get("data", {}).get("black", not session["black"])

            _broadcast_event(session, broadcast_msg)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        session["connections"].discard(websocket)
        _broadcast_event(session, {"type": "bye", "role": role, "time": time.time()})


# ================================================================ v3.3 低延迟中继 (hub)
# 一条 WebSocket 同时承载: 文本 = JSON 指令/批注/ack, 二进制 = 画面帧 (首字节 b"P" 电脑画面 / b"C" 手机画面 + JPEG)
# 角色: pc (电脑桌面端) / phone (手机网页或 App)。只转发给对端角色; 对端正在发送时丢帧, 永远只发最新帧。
# 端到端流控: 接收方显示完一帧回 {"type":"ack","ch":"P"}, 发送方收到 ack 才发下一帧 → 不堆积, 延迟最低。
HUBS: Dict[str, Dict[str, set]] = {}


async def _hub_send(ws: WebSocket, payload, binary: bool):
    st = ws.scope.setdefault("fla_state", {"busy": False})
    if binary:
        if st["busy"]:
            return False          # 对端还在收上一帧 → 丢掉旧帧
        st["busy"] = True
        try:
            await ws.send_bytes(payload)
        finally:
            st["busy"] = False
        return True
    await ws.send_text(payload)
    return True


def _hub_peers(sid: str, role: str) -> list:
    other = "phone" if role == "pc" else "pc"
    return list(HUBS.get(sid, {}).get(other, ()))


async def _hub_presence(sid: str):
    h = HUBS.get(sid, {})
    import json as _json
    msg = _json.dumps({"type": "peers", "pc": len(h.get("pc", ())), "phone": len(h.get("phone", ()))})
    for ws in [w for s in h.values() for w in s]:
        try:
            await ws.send_text(msg)
        except Exception:
            pass


@router.websocket("/hub/{sid}")
async def remote_hub(websocket: WebSocket, sid: str, code: str = "", role: str = "phone"):
    session = SESSIONS.get(sid)
    if not session or session.get("code") != code or role not in ("pc", "phone"):
        await websocket.close(code=4004)
        return
    await websocket.accept()
    hub = HUBS.setdefault(sid, {"pc": set(), "phone": set()})
    hub[role].add(websocket)
    await _hub_presence(sid)
    try:
        while True:
            m = await websocket.receive()
            if m.get("type") == "websocket.disconnect":
                break
            session["last_active"] = time.time()
            if m.get("bytes") is not None:
                data = m["bytes"]
                if len(data) > MAX_FRAME + 1:
                    continue
                for peer in _hub_peers(sid, role):
                    asyncio.create_task(_hub_safe(peer, data, True))
            elif m.get("text") is not None:
                txt = m["text"]
                if txt == "ping":
                    await websocket.send_text("pong")
                    continue
                for peer in _hub_peers(sid, role):
                    asyncio.create_task(_hub_safe(peer, txt, False))
                # 兼容旧客户端: 手机指令同时进入 HTTP 轮询队列
                if role == "phone" and '"ack"' not in txt[:40]:
                    try:
                        import json as _json
                        d = _json.loads(txt)
                        if d.get("action"):
                            _broadcast_event(session, {"id": secrets.token_hex(4), "type": "action", "action": d["action"],
                                                       "data": d.get("data") or {}, "time": time.time(), "via": "hub"})
                    except Exception:
                        pass
    except Exception:
        pass
    finally:
        hub[role].discard(websocket)
        if not hub["pc"] and not hub["phone"]:
            HUBS.pop(sid, None)
        await _hub_presence(sid)


async def _hub_safe(ws, payload, binary):
    try:
        await _hub_send(ws, payload, binary)
    except Exception:
        pass


@router.post("/{sid}/lan")
def set_lan(sid: str, body: dict, code: Optional[str] = Query(None)):
    """桌面端上报局域网直连地址 (手机 App / 网页优先尝试直连, 不绕服务器)"""
    session = _auth(sid, code)
    session["lan"] = [str(u)[:120] for u in (body.get("urls") or [])][:4]
    return {"ok": True}
