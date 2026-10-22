"""FLA v3.6 - 实时统计: 注册用户 / 在线人数 / 累计访问 / 累计流量

- 流量: 纯 ASGI 中间件统计每个请求实际发出的字节数 (已 gzip 压缩后的真实流量)
- 在线: 最近 5 分钟内有请求的 IP+UA 去重数 (内存级, 不落库)
- 累计访问/流量: 内存计数, 每 60 秒与退出时写入 settings, 重启不清零
- 展示值 = 真实值 + 管理员设置的偏移量 (后台可改, 用于彰显欢迎程度)
"""
import hashlib
import threading
import time

from fastapi import APIRouter

from .. import db

router = APIRouter(prefix="/api/stats")

WINDOW = 300  # 在线统计窗口(秒)
_lock = threading.Lock()
_seen: dict[str, float] = {}          # 访客键 -> 最近访问时间
_totals = {"hits": 0, "bytes": 0}
_dirty = False


def _load():
    try:
        _totals["hits"] = int(db.get_setting("traffic_hits", "0") or 0)
        _totals["bytes"] = int(db.get_setting("traffic_bytes", "0") or 0)
    except Exception:
        pass


def flush():
    global _dirty
    with _lock:
        if not _dirty:
            return
        h, b = _totals["hits"], _totals["bytes"]
        _dirty = False
    try:
        db.set_setting("traffic_hits", str(h))
        db.set_setting("traffic_bytes", str(b))
    except Exception:
        pass


def _flush_loop():
    while True:
        time.sleep(60)
        flush()


def start():
    _load()
    threading.Thread(target=_flush_loop, daemon=True).start()


def _record(key: str, nbytes: int):
    global _dirty
    now = time.time()
    with _lock:
        _seen[key] = now
        _totals["hits"] += 1
        _totals["bytes"] += nbytes
        _dirty = True
        if len(_seen) > 5000:
            for k in [k for k, t in _seen.items() if now - t > WINDOW]:
                _seen.pop(k, None)


def online_count() -> int:
    now = time.time()
    with _lock:
        for k in [k for k, t in _seen.items() if now - t > WINDOW]:
            _seen.pop(k, None)
        return len(_seen)


class TrafficMiddleware:
    """统计 HTTP 响应字节数; 统计接口自身不计入"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith("/api/stats"):
            return await self.app(scope, receive, send)
        sent = [0]

        async def _send(message):
            if message["type"] == "http.response.body":
                sent[0] += len(message.get("body", b"") or b"")
            await send(message)

        headers = dict(scope.get("headers") or [])
        fwd = (headers.get(b"x-forwarded-for") or b"").decode("latin-1").split(",")[0].strip()
        client = scope.get("client") or ("", 0)
        ip = fwd or (headers.get(b"x-real-ip") or b"").decode("latin-1").strip() or client[0]
        ua = (headers.get(b"user-agent") or b"").decode("latin-1")
        key = hashlib.md5(f"{ip}|{ua}".encode("utf-8", "ignore")).hexdigest()
        try:
            await self.app(scope, receive, _send)
        finally:
            _record(key, sent[0])


def display_values():
    """首页展示值 = 真实值 + 偏移"""
    users = db.q1("SELECT COUNT(*) AS c FROM users")["c"]
    off_u = int(db.get_setting("show_users_offset", "0") or 0)
    off_mb = int(db.get_setting("show_traffic_offset_mb", "0") or 0)
    with _lock:
        hits, nbytes = _totals["hits"], _totals["bytes"]
    return {
        "users": users + off_u,
        "online": online_count() + int(db.get_setting("show_online_offset", "0") or 0),
        "hits": hits + int(db.get_setting("show_hits_offset", "0") or 0),
        "traffic_bytes": nbytes + off_mb * 1024 * 1024,
    }


@router.get("/live")
def live():
    return display_values()
