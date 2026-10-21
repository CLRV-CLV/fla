"""FLA - 数据库层 (SQLite, WAL, 线程安全)"""
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WEB = BASE / "web"
DATA = Path(os.environ.get("FLA_DATA_DIR") or os.environ.get("EDU_DATA_DIR") or (BASE / "data"))

for _d in (DATA, DATA / "uploads", DATA / "converted", DATA / "avatars", DATA / "fonts"):
    _d.mkdir(parents=True, exist_ok=True)

_local = threading.local()


def now():
    """本地时间字符串 (部署时建议 TZ=Asia/Hong_Kong)"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(str(DATA / "app.db"), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c


def q(sql, args=()):
    return conn().execute(sql, args).fetchall()


def q1(sql, args=()):
    return conn().execute(sql, args).fetchone()


def ex(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur


def used_bytes(user_id) -> int:
    r = q1("SELECT COALESCE(SUM(size_bytes),0) AS s FROM files WHERE user_id=?", (user_id,))
    return int(r["s"]) if r else 0


DEFAULT_SETTINGS = {
    "default_quota_mb": "500",   # 教师默认空间
    "max_upload_mb": "500",      # 单文件上限
    "registration_open": "1",    # 是否开放注册(仍需邀请码)
}


def get_setting(key: str, default: str = "") -> str:
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    if r:
        return r["value"]
    return DEFAULT_SETTINGS.get(key, default)


def set_setting(key: str, value: str):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, value))


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  nickname TEXT NOT NULL DEFAULT '',
  signature TEXT NOT NULL DEFAULT '',
  avatar TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user',
  is_teacher INTEGER NOT NULL DEFAULT 0,
  cert_title TEXT NOT NULL DEFAULT '',
  quota_bytes INTEGER NOT NULL DEFAULT 524288000,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS invite_codes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  max_uses INTEGER NOT NULL DEFAULT 1,
  used_count INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  orig_name TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  ext TEXT NOT NULL,
  kind TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  pdf_path TEXT,
  pages INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  missing_fonts TEXT,
  anim INTEGER NOT NULL DEFAULT 0,
  share_token TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_files_owner_order ON files(user_id, id DESC);
CREATE TABLE IF NOT EXISTS annotations(
  file_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
/* v1.26: 公告 / 论坛 / 聊天 / 扫码登录 */
CREATE TABLE IF NOT EXISTS announcements(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL DEFAULT 'info',
  scope TEXT NOT NULL DEFAULT 'global',
  target_uid INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ann_reads(
  uid INTEGER NOT NULL,
  aid INTEGER NOT NULL,
  read_at TEXT NOT NULL,
  PRIMARY KEY(uid, aid)
);
CREATE TABLE IF NOT EXISTS forum_boards(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  descr TEXT NOT NULL DEFAULT '',
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forum_threads(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  board_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  locked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forum_posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  edited INTEGER NOT NULL DEFAULT 0,
  edited_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_rooms(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  owner_id INTEGER NOT NULL,
  official INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'group',        -- v1.27: group 群聊 | dm 私聊
  dm_key TEXT,                               -- v1.27: '小uid-大uid', 私聊复用同一会话
  avatar TEXT NOT NULL DEFAULT '',           -- v1.27: 群头像
  intro TEXT NOT NULL DEFAULT '',            -- v1.27: 群公告
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_members(
  room_id INTEGER NOT NULL,
  uid INTEGER NOT NULL,
  joined_at TEXT NOT NULL,
  last_read INTEGER NOT NULL DEFAULT 0,      -- v1.27: 已读到的消息 id (未读数 + 已读回执都靠它)
  muted INTEGER NOT NULL DEFAULT 0,          -- v1.27: 消息免打扰
  pinned INTEGER NOT NULL DEFAULT 0,         -- v1.27: 置顶会话
  nickname TEXT NOT NULL DEFAULT '',         -- v1.27: 我在本群的昵称
  left_at TEXT,                              -- v1.27: 退群时间(NULL = 在群里)
  PRIMARY KEY(room_id, uid)
);
CREATE TABLE IF NOT EXISTS chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id INTEGER NOT NULL,
  uid INTEGER NOT NULL,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',         -- v1.27: text|image|file|audio|system
  att_id INTEGER,                            -- v1.27: 附件 id
  reply_to INTEGER,                          -- v1.27: 引用的消息 id
  deleted INTEGER NOT NULL DEFAULT 0,        -- 撤回/删除
  edited INTEGER NOT NULL DEFAULT 0,
  edited_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_attachments(  -- v1.27: 图片/文件/语音
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id INTEGER NOT NULL DEFAULT 0,
  uid INTEGER NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  dur_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_reactions(    -- v1.27: 表情回应
  msg_id INTEGER NOT NULL,
  uid INTEGER NOT NULL,
  emoji TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(msg_id, uid)
);
CREATE TABLE IF NOT EXISTS chat_typing(       -- v1.27: "正在输入" (只用内存级时效, 定期清)
  room_id INTEGER NOT NULL,
  uid INTEGER NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY(room_id, uid)
);
CREATE TABLE IF NOT EXISTS chat_mentions(     -- v1.27: @我 的消息(红点提醒)
  msg_id INTEGER NOT NULL,
  uid INTEGER NOT NULL,
  room_id INTEGER NOT NULL,
  seen INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY(msg_id, uid)
);
CREATE INDEX IF NOT EXISTS ix_msg_room ON chat_messages(room_id, id);
CREATE INDEX IF NOT EXISTS ix_att_room ON chat_attachments(room_id, id);
CREATE INDEX IF NOT EXISTS ix_member_uid ON chat_members(uid, room_id);
/* v3.6: 用户组 / AI 渠道与模型 / AI 会话 */
CREATE TABLE IF NOT EXISTS user_groups(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  descr TEXT NOT NULL DEFAULT '',
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_channels(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,                     -- OpenAI 兼容地址, 例如 https://api.xxx.com/v1
  api_keys TEXT NOT NULL DEFAULT '',         -- 多个 token, 每行一个, 轮询使用
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_models(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL,
  model TEXT NOT NULL,                        -- 上游模型 id
  display_name TEXT NOT NULL DEFAULT '',
  intro TEXT NOT NULL DEFAULT '',             -- 可选介绍
  groups TEXT NOT NULL DEFAULT '[]',         -- JSON 数组: 允许使用的用户组 id (管理员始终可用)
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '新对话',
  model_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_id INTEGER NOT NULL,
  role TEXT NOT NULL,                         -- user | assistant
  content TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_conv_user ON ai_conversations(user_id, id);
CREATE INDEX IF NOT EXISTS ix_ai_msg_conv ON ai_messages(conv_id, id);
CREATE TABLE IF NOT EXISTS qr_tickets(
  ticket TEXT PRIMARY KEY,
  uid INTEGER,
  token TEXT,
  expires REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
);
"""


def init_db():
    c = conn()
    c.executescript(SCHEMA)
    # 轻量迁移: 老库补齐新增列
    try:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(files)").fetchall()}
        if "anim" not in cols:
            c.execute("ALTER TABLE files ADD COLUMN anim INTEGER NOT NULL DEFAULT 0")
        if "share_token" not in cols:
            c.execute("ALTER TABLE files ADD COLUMN share_token TEXT")
        if "media_path" not in cols:
            c.execute("ALTER TABLE files ADD COLUMN media_path TEXT")   # v1.26: 视频转码后的 mp4
        ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "cert_icon" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN cert_icon TEXT NOT NULL DEFAULT ''")   # v1.26: 认证图标
        if "cert_color" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN cert_color TEXT NOT NULL DEFAULT ''")  # v1.26: 认证颜色
        if "chat_banned" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN chat_banned INTEGER NOT NULL DEFAULT 0")  # v1.26: 禁言
        # v1.27: 聊天升级到微信级(私聊/未读/已读回执/免打扰/置顶/群昵称/附件/回应/@)
        rcols = {r["name"] for r in c.execute("PRAGMA table_info(chat_rooms)").fetchall()}
        for col, ddl in (("kind", "TEXT NOT NULL DEFAULT 'group'"), ("dm_key", "TEXT"),
                         ("avatar", "TEXT NOT NULL DEFAULT ''"), ("intro", "TEXT NOT NULL DEFAULT ''")):
            if col not in rcols:
                c.execute(f"ALTER TABLE chat_rooms ADD COLUMN {col} {ddl}")
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(chat_members)").fetchall()}
        for col, ddl in (("last_read", "INTEGER NOT NULL DEFAULT 0"), ("muted", "INTEGER NOT NULL DEFAULT 0"),
                         ("pinned", "INTEGER NOT NULL DEFAULT 0"), ("nickname", "TEXT NOT NULL DEFAULT ''"),
                         ("left_at", "TEXT")):
            if col not in mcols:
                c.execute(f"ALTER TABLE chat_members ADD COLUMN {col} {ddl}")
        gcols = {r["name"] for r in c.execute("PRAGMA table_info(chat_messages)").fetchall()}
        for col, ddl in (("kind", "TEXT NOT NULL DEFAULT 'text'"), ("att_id", "INTEGER"),
                         ("reply_to", "INTEGER")):
            if col not in gcols:
                c.execute(f"ALTER TABLE chat_messages ADD COLUMN {col} {ddl}")
    except Exception:
        pass
    # v3.6: 用户组 (默认组 id=1 = 邀请码用户组)、假用户标记、邀请码分组
    try:
        ucols2 = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "group_id" not in ucols2:
            c.execute("ALTER TABLE users ADD COLUMN group_id INTEGER NOT NULL DEFAULT 1")
        if "is_fake" not in ucols2:
            c.execute("ALTER TABLE users ADD COLUMN is_fake INTEGER NOT NULL DEFAULT 0")
        icols = {r["name"] for r in c.execute("PRAGMA table_info(invite_codes)").fetchall()}
        if "group_id" not in icols:
            c.execute("ALTER TABLE invite_codes ADD COLUMN group_id INTEGER NOT NULL DEFAULT 1")
        if not c.execute("SELECT id FROM user_groups WHERE id=1").fetchone():
            c.execute("INSERT INTO user_groups(id,name,descr,is_default,created_at) VALUES(1,?,?,1,?)",
                      ("邀请码用户", "通过邀请码注册的用户(默认组)", now()))
        c.commit()
    except Exception:
        pass
    # v1.26: 默认论坛板块 / 官方聊天室 / 权限开关
    try:
        if not q1("SELECT id FROM forum_boards LIMIT 1"):
            for i, (nm, ds) in enumerate([("教学交流", "教学经验、课件制作心得"),
                                          ("资源分享", "好用的工具、素材与资料"),
                                          ("站务反馈", "问题反馈与功能建议")]):
                ex("INSERT INTO forum_boards(name,descr,sort,created_at) VALUES(?,?,?,?)", (nm, ds, i, now()))
        if not q1("SELECT id FROM chat_rooms WHERE official=1 LIMIT 1"):
            ex("INSERT INTO chat_rooms(name,owner_id,official,created_at) VALUES(?,?,1,?)", ("官方大厅", 1, now()))
        if not get_setting("allow_group_create"):
            set_setting("allow_group_create", "0")
    except Exception:
        pass
    c.commit()
