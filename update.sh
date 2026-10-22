#!/bin/bash
# ================================================================
#  FLA (FYX Lesson All) 在线更新脚本
#  用法:  sudo bash update.sh [选项]
#    -b, --branch 分支名   拉取哪个分支 (默认: 当前 checkout 所在分支, 非 git 仓库时 main)
#    --no-pull             不拉代码, 只用现有代码重建容器 (手工覆盖过文件时用)
#    --no-backup           跳过数据库备份
#    --full                更新后改用 install.sh 全量重装 (会重装网关/证书, 较慢)
#    -y, --yes             不问确认, 直接执行
#    -h, --help            显示帮助
#
#  它做四件事 (全部可重复执行, 数据永不丢):
#    1. 备份课件数据库 (docker 容器内 /data/app.db → 宿主机 ./backup/)
#    2. 拉取最新代码 (git fetch + reset，或 codeload 归档包；deploy/.env 等本地配置不动)
#    3. 重建并拉起容器 (沿用 install.sh 记录的 compose 文件 + profile)
#    4. 健康检查 + 打印版本号 (失败时给出排查命令)
#
#  日常管理仍用 run.sh: sudo bash run.sh status|logs|restart|edge|ssl
#  本脚本只负责"更新"，不修改端口/域名/证书/管理员密码等配置。
# ================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

BRAND="FLA (FYX Lesson All)"
DEFAULT_REPO="CLRV-CLV/fla"
DEFAULT_BRANCH="main"
LOG="update.log"
BACKUP_DIR="$SCRIPT_DIR/backup"

TARGET_BRANCH=""
DO_PULL=1
DO_BACKUP=1
FULL_INSTALL=0
ASSUME_YES=0

log()  { echo -e "\033[36m>>\033[0m $*" | tee -a "$LOG"; }
ok()   { echo -e "\033[32m✔\033[0m $*"  | tee -a "$LOG"; }
warn() { echo -e "\033[33m⚠\033[0m $*"  | tee -a "$LOG"; }
die()  { echo -e "\033[31m✘\033[0m $*"  | tee -a "$LOG"; exit 1; }

usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    -b|--branch)   TARGET_BRANCH="${2:-}"; shift 2 ;;
    --branch=*)    TARGET_BRANCH="${1#*=}"; shift ;;
    --no-pull)     DO_PULL=0; shift ;;
    --no-backup)   DO_BACKUP=0; shift ;;
    --full)        FULL_INSTALL=1; shift ;;
    -y|--yes)      ASSUME_YES=1; shift ;;
    -h|--help)     usage ;;
    *)             die "未知参数: $1  (用 --help 查看用法)" ;;
  esac
done

echo "=================================================================="
echo "  $BRAND · 更新 $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================================="

# ---------- 0. 环境检查 ----------
if [ "$(id -u)" != "0" ]; then
  die "请用 root 运行: sudo bash update.sh"
fi
command -v docker >/dev/null 2>&1 || die "未找到 docker, 请先 sudo bash install.sh"
DC=""
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "未找到 docker compose, 请先 sudo bash install.sh"; fi

# compose 文件 / profile / 端口: 沿用 install.sh 的记录
CF=$(cat deploy/.compose_file 2>/dev/null)
if [ -z "${CF:-}" ] || [ ! -f "$CF" ]; then
  if [ -f deploy/docker-compose.direct.yml ] && grep -q '^DS_PORT=' deploy/.env 2>/dev/null; then
    CF="deploy/docker-compose.direct.yml"
  elif docker images 2>/dev/null | grep -q 'onlyoffice/documentserver'; then
    CF="deploy/docker-compose.yml"
  else
    CF="deploy/docker-compose.lite.yml"
  fi
fi
PROF=$(cat deploy/.compose_profile 2>/dev/null)
DCP=""
[ -n "${PROF:-}" ] && DCP="--profile $PROF"
PORT=$(grep -E '^PORT=' deploy/.env 2>/dev/null | head -1 | cut -d= -f2)
PORT=${PORT:-8306}

ok "compose: $CF ${PROF:+(profile: $PROF)} · 应用端口: $PORT"

# ---------- 1. 备份数据库 ----------
if [ "$DO_BACKUP" = "1" ]; then
  log "[1/4] 备份数据库 ..."
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'fla'; then
    mkdir -p "$BACKUP_DIR"
    STAMP=$(date '+%Y%m%d-%H%M%S')
    # 用 sqlite backup API 取一致性快照 (直接 cp 可能拿到写了一半的 WAL)
    if docker exec fla python3 -c "import sqlite3; s=sqlite3.connect('/data/app.db'); d=sqlite3.connect('/data/app.db.bak'); s.backup(d); d.close(); s.close()" >/dev/null 2>&1 \
       && docker cp fla:/data/app.db.bak "$BACKUP_DIR/app-$STAMP.db" >/dev/null 2>&1; then
      docker exec fla rm -f /data/app.db.bak >/dev/null 2>&1 || true
      ok "数据库已备份: backup/app-$STAMP.db ($(du -h "$BACKUP_DIR/app-$STAMP.db" | cut -f1))"
      # 只保留最近 10 份
      ls -1t "$BACKUP_DIR"/app-*.db 2>/dev/null | tail -n +11 | while read -r old; do rm -f "$old"; done
    else
      warn "数据库备份失败 (不影响更新, 数据卷 fla-data 本身不会被改动)"
    fi
  else
    warn "fla 容器未运行, 跳过数据库备份"
  fi
else
  log "[1/4] 已跳过数据库备份 (--no-backup)"
fi

# ---------- 2. 拉取代码 ----------
REPO_SLUG="$DEFAULT_REPO"
IS_GIT=0
if [ -d .git ] && command -v git >/dev/null 2>&1; then
  IS_GIT=1
  REMOTE_URL=$(git remote get-url origin 2>/dev/null || true)
  if [ -n "$REMOTE_URL" ]; then
    SLUG=$(echo "$REMOTE_URL" | sed -E 's#^.*[:/]([^/:]+/[^/:]+)$#\1#; s#\.git$##')
    case "$SLUG" in */*) REPO_SLUG="$SLUG" ;; esac
  fi
fi
if [ -z "$TARGET_BRANCH" ]; then
  if [ "$IS_GIT" = "1" ]; then
    CUR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    case "$CUR" in ""|HEAD) TARGET_BRANCH="${FLA_BRANCH:-$DEFAULT_BRANCH}" ;; *) TARGET_BRANCH="$CUR" ;; esac
  else
    TARGET_BRANCH="${FLA_BRANCH:-$DEFAULT_BRANCH}"
  fi
fi
log "[2/4] 更新代码: $REPO_SLUG  分支 $TARGET_BRANCH ..."

if [ "$ASSUME_YES" != "1" ] && [ "$DO_PULL" = "1" ]; then
  echo -n "  即将执行 git reset --hard (会丢弃本机对代码文件的修改, 数据与 deploy/.env 不受影响), 继续? [Y/n] "
  read -r ans
  case "$ans" in n|N|no|NO) die "已取消" ;; esac
fi

OLD_COMMIT=""
if [ "$DO_PULL" = "1" ]; then
  if [ "$IS_GIT" = "1" ]; then
    OLD_COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
    PULLED=0
    # 用 FETCH_HEAD: 单分支克隆下 origin/<branch> 不会刷新, 会"假装更新成功"
    if git fetch --prune origin "+refs/heads/$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH" 2>>"$LOG" || \
       git fetch origin "$TARGET_BRANCH" 2>>"$LOG"; then
      if git reset --hard FETCH_HEAD 2>>"$LOG"; then PULLED=1; fi
    fi
    if [ "$PULLED" = "0" ] && [ -n "$REMOTE_URL" ]; then
      warn "从 origin 拉取失败, 尝试完整仓库地址 ..."
      if git fetch "$REMOTE_URL" "$TARGET_BRANCH" 2>>"$LOG" && git reset --hard FETCH_HEAD 2>>"$LOG"; then PULLED=1; fi
    fi
    [ "$PULLED" = "1" ] || warn "git 拉取失败 (服务器可能访问不了 github.com)"
  else
    warn "当前目录不是 git 仓库, 改用代码归档包"
    TARBALL=/tmp/fla-src-$$.tgz
    rm -f "$TARBALL"
    AUTH_HDR=()
    [ -n "${GITHUB_TOKEN:-}" ] && AUTH_HDR=(-H "Authorization: Bearer $GITHUB_TOKEN")
    for U in "https://codeload.github.com/$REPO_SLUG/tar.gz/refs/heads/$TARGET_BRANCH" \
             "https://github.com/$REPO_SLUG/archive/refs/heads/$TARGET_BRANCH.tar.gz" \
             "https://gh-proxy.com/https://github.com/$REPO_SLUG/archive/refs/heads/$TARGET_BRANCH.tar.gz"; do
      log "  下载: $U"
      if command -v curl >/dev/null 2>&1; then
        curl -fL --connect-timeout 15 --max-time 600 "${AUTH_HDR[@]}" -o "$TARBALL" "$U" \
          || { warn "    下载失败 (curl 退出码 $?)"; rm -f "$TARBALL"; continue; }
      elif command -v wget >/dev/null 2>&1; then
        wget -T 15 --header="Authorization: Bearer ${GITHUB_TOKEN:-}" -O "$TARBALL" "$U" 2>>"$LOG" \
          || { warn "    下载失败 (wget)"; rm -f "$TARBALL"; continue; }
      else die "缺少 curl/wget, 无法下载代码包"; fi
      if [ -s "$TARBALL" ] && tar -tzf "$TARBALL" >/dev/null 2>&1; then
        break
      fi
      warn "    文件无效 (可能分支名不存在或仓库是私有的)"
      rm -f "$TARBALL"
    done
    if [ -s "$TARBALL" ]; then
      # 保留本机配置/数据: deploy/.env、.compose_*、日志与备份
      tar -xzf "$TARBALL" --strip-components=1 \
        --exclude='deploy/.env' --exclude='deploy/.compose_file' \
        --exclude='deploy/.compose_profile' --exclude='backup/*' >>"$LOG" 2>&1 \
        && ok "代码包已覆盖解压"
      rm -f "$TARBALL"
    else
      warn "代码包下载失败"
    fi
  fi
else
  log "  已跳过代码拉取 (--no-pull), 使用当前目录代码"
fi

# 代码是否真的存在: 拉取失败/目录为空时不能继续假装更新成功
if [ ! -f server/main.py ] || [ ! -f web/index.html ]; then
  die "代码目录不完整 (缺 server/main.py 或 web/index.html) —— 更新已中止, 容器未改动。
  可尝试: sudo bash update.sh --branch <分支名>   或用 --no-pull 使用当前目录的代码
  私有仓库请先配置 git 凭据, 或设置 GITHUB_TOKEN 环境变量后重试"
fi

# 写入构建标记: 前端页脚 / /api/version 会显示
mkdir -p server
if [ "$IS_GIT" = "1" ]; then
  echo "  $OLD_COMMIT -> $(git log --oneline -1 2>/dev/null)"
  git log -1 --format='%h %cd' --date=format:'%Y-%m-%d %H:%M' > server/BUILD 2>/dev/null || true
else
  date '+tgz %Y-%m-%d %H:%M' > server/BUILD 2>/dev/null || true
fi
VER=$(grep -oP 'CURRENT_VERSION = "\K[^"]+' server/routers/desktop.py 2>/dev/null)
[ -n "${VER:-}" ] && ok "代码版本: v$VER"

# 静态资源的 ?v= 版本号变了才需要强刷; 这里记录一下方便排查
APP_V=$(grep -oP "js/app\.js\?v=\K[0-9]+" web/index.html 2>/dev/null | head -1)

# ---------- 3. 全量重装 或 只重建容器 ----------
if [ "$FULL_INSTALL" = "1" ]; then
  log "[3/4] 全量重装 (install.sh, 数据保留) ..."
  exec bash "$SCRIPT_DIR/install.sh"
fi

log "[3/4] 重建并启动容器 ..."
# 重启前先清掉历史遗留的中间层 nginx (老版本 install.sh 会占用 8306)
docker rm -f nginx >/dev/null 2>&1 || true
if ! $DC $DCP -f "$CF" up -d --build --remove-orphans 2>&1 | tee -a "$LOG"; then
  die "容器构建/启动失败, 详见 $LOG 与: $DC $DCP -f $CF logs --tail 80"
fi
systemctl restart fla >/dev/null 2>&1 || true   # 兼容 systemd 直跑(非容器)的部署
ok "容器已重建"

# ---------- 4. 健康检查 ----------
log "[4/4] 等待服务就绪 ..."
READY=0
for _ in $(seq 1 45); do
  if curl -sf -m 4 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then READY=1; break; fi
  if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'fla'; then
    warn "fla 容器意外退出, 最近日志:"
    docker logs --tail 40 fla 2>&1 | tee -a "$LOG"
    break
  fi
  sleep 2
done

if [ "$READY" = "1" ]; then
  ok "应用已就绪: http://127.0.0.1:$PORT/"
else
  warn "应用 90 秒内未响应本地 $PORT (可能还在启动)"
fi

# 顺带看一眼 80/443 边缘网关
if [ -f /etc/nginx/conf.d/fla-edge.conf ]; then
  PHOST=$(grep -E '^PUBLIC_BASE_URL=' deploy/.env 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's~https?://([^/:]+).*~\1~')
  if [ -n "${PHOST:-}" ] && curl -sfk -m 5 -H "Host: $PHOST" https://127.0.0.1/api/health >/dev/null 2>&1; then
    ok "边缘网关 443 正常 (https://$PHOST/)"
  fi
fi

echo ""
echo "── 容器状态 ──"
docker ps -a --filter name=fla --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true
echo ""
echo "── 完成 ──"
echo "  代码: $REPO_SLUG @ $TARGET_BRANCH ${VER:+(v$VER)}"
echo "  数据: 未改动 (docker 卷 fla-data)"
[ -n "${STAMP:-}" ] && echo "  备份: backup/app-$STAMP.db"
echo "  ⚠ 前端 JS 有更新时请让老师按一次 Ctrl+F5 强刷 (静态资源带 ?v= 版本号, 一般会自动更新)"
echo "  排查: sudo bash run.sh status   /   sudo bash run.sh logs app"
echo ""
