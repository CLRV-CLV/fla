"""FLA - FastAPI 主入口"""
import os
import secrets
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import converter, db, security
from .deps import user_public
from .routers import (admin, admin_extra, ai, announcements, auth, chat, desktop, files, mobile, remote, social,
                      stats, tools, users)

app = FastAPI(title="FLA", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["Content-Disposition", "Content-Range"])


@app.get("/api/version")
def version():
    from .routers.desktop import CURRENT_VERSION
    try:
        build = open(os.path.join(os.path.dirname(__file__), "BUILD"), encoding="utf-8").read().strip()
    except Exception:
        build = ""
    return {"version": CURRENT_VERSION, "build": build}


@app.get("/api/health")
def health():
    return {"ok": True, "service": "fla"}


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(users.avatars)
app.include_router(files.router)
app.include_router(admin.router)
app.include_router(announcements.router)   # v1.26: 公告
app.include_router(social.router)          # v1.26: 论坛
app.include_router(chat.router)            # v1.27: 聊天(微信级)
app.include_router(tools.router)           # v1.27: 课堂工具(名单解析/计时)
app.include_router(remote.router)          # v1.28: 手机投屏与远程授课遥控
app.include_router(desktop.router)         # v1.28: 桌面客户端免登录下载与自动更新
app.include_router(mobile.router)          # v3.3: 安卓 APK 下载
app.include_router(admin_extra.router)     # v3.6: 用户组 / 批量假用户 / 首页展示数据
app.include_router(ai.router)              # v3.6: AI 对话 / 写作辅助 / AI 论坛
app.include_router(ai.admin_router)        # v3.6: AI 渠道与模型管理
app.include_router(stats.router)           # v3.6: 首页实时数据


def _ensure_admin():
    if db.q1("SELECT id FROM users WHERE role='admin'"):
        return
    pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(10)
    db.ex("INSERT INTO users(username,password_hash,nickname,role,created_at) VALUES(?,?,?,?,?)",
          ("admin", security.hash_password(pw), "管理员", "admin", db.now()))
    info = db.DATA / "initial_admin_password.txt"
    try:
        info.write_text(f"FLA 初始管理员账号\n用户名: admin\n密码: {pw}\n请登录后立即修改密码!\n",
                        encoding="utf-8")
    except Exception:
        pass
    print("=" * 56)
    print("!! 已创建初始管理员  用户名: admin  密码:", pw)
    print("!! 密码也已写入 data/initial_admin_password.txt, 请尽快修改")
    print("=" * 56)


@app.on_event("startup")
async def _start_rtmp():
    from . import rtmp_ingest
    await rtmp_ingest.start()


@app.on_event("startup")
def startup():
    db.init_db()
    _ensure_admin()
    stats.start()
    converter.start_worker()
    threading.Thread(target=converter.ensure_fonts, daemon=True).start()


# 前端静态资源 (SPA, hash 路由)
class NoCacheStatic(StaticFiles):
    """html 必须 revalidate(304), 避免发版后浏览器用旧缓存;
    带 ?v= 版本号的 js/css 内容不变 → 长缓存 immutable (弱网/跨国线路复访近乎秒开)"""
    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        try:
            versioned = b"v=" in (scope.get("query_string") or b"")
        except Exception:
            versioned = False
        r.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
        if path.endswith("cast.html"):
            r.headers["Cache-Control"] = "no-store"   # 主屏「FLA 投屏」必须每次取最新
        return r


# v1.37: 全局 gzip — 直连模式(无 nginx)与 lite 部署也能压缩; ~600KB 前端资源 ≈ 140KB,
# 对高丢包高延迟线路(如中美)首屏体感差距巨大
try:
    from starlette.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
except Exception as _e:
    print("[startup] GZipMiddleware unavailable:", _e)

# v3.6: 流量统计中间件 (最外层, 统计真实发出的字节数)
from .routers.stats import TrafficMiddleware  # noqa: E402
app.add_middleware(TrafficMiddleware)

app.mount("/", NoCacheStatic(directory=str(db.WEB), html=True), name="web")
