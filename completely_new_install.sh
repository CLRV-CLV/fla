#!/bin/bash
# ================================================================
#  FLA 全新重装 (completely new install)
#  ----------------------------------------------------------------
#  把服务器上的 Docker 彻底清空并卸载, 再从零重装 FLA。
#  适用场景: docker/容器/网络/镜像被玩坏了、iptables 规则错乱、
#           换版本要干净环境、排查"只有我这台机器有问题"的怪故障。
#
#  会删除:
#    · 所有容器 (不只是 FLA 的! 包括你自己跑的其他项目)
#    · 所有镜像 / 网络 / 构建缓存
#    · Docker 数据目录 /var/lib/docker 与 /var/lib/containerd
#    · Docker 软件包本身 (docker-ce / containerd / compose 插件)
#    · 数据卷 fla-data (默认先自动备份成 tar.gz, 见下)
#  不会动:
#    · 本目录下的 FLA 源码与 deploy/.env
#    · 宿主机 nginx (80/443 边缘网关) 与 Let's Encrypt 证书
#    · 数据库/课件的备份文件 (fla-backup-*.tar.gz)
#
#  用法:
#    sudo bash completely_new_install.sh                 交互(需输入确认词)
#    sudo bash completely_new_install.sh --yes           仍需确认词, 但不提问其他项
#    sudo bash completely_new_install.sh --force         完全无人值守(危险)
#    sudo bash completely_new_install.sh --keep-data     保留 fla-data 数据卷
#    sudo bash completely_new_install.sh --no-backup     不备份数据卷(不可恢复!)
#    sudo bash completely_new_install.sh --keep-docker   只清空容器/镜像, 不卸载 docker
#    sudo bash completely_new_install.sh --skip-install  只清理, 不重装
#    其余参数原样传给 install.sh, 例如:
#    sudo bash completely_new_install.sh --port 8306 --domain t.clrv.top --lite
# ================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
LOG="$SCRIPT_DIR/reinstall.log"

ASSUME_YES=0; FORCE=0; KEEP_DATA=0; DO_BACKUP=1; KEEP_DOCKER=0; SKIP_INSTALL=0
PASS_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)        ASSUME_YES=1; shift ;;
    --force)         FORCE=1; ASSUME_YES=1; shift ;;
    --keep-data)     KEEP_DATA=1; shift ;;
    --no-backup)     DO_BACKUP=0; shift ;;
    --keep-docker)   KEEP_DOCKER=1; shift ;;
    --skip-install)  SKIP_INSTALL=1; shift ;;
    -h|--help)       grep '^#' "$0" | sed 's/^# \{0,2\}//'; exit 0 ;;
    *)               PASS_ARGS+=("$1"); shift ;;
  esac
done

[ "$(id -u)" = "0" ] || { echo "✘ 请用 root 运行: sudo bash completely_new_install.sh"; exit 1; }
: >"$LOG"
log(){ local L="[$(date '+%H:%M:%S')] $*"; echo "$L"; echo "$L" >>"$LOG" 2>/dev/null; }
try(){ "$@" >>"$LOG" 2>&1; }

TS=$(date '+%Y%m%d-%H%M%S')
BACKUP_FILE="$SCRIPT_DIR/fla-backup-$TS.tar.gz"

detect_os(){
  if [ -f /etc/centos-release ] || [ -f /etc/redhat-release ]; then OS="rh"
  elif [ -f /etc/debian_version ]; then OS="deb"
  else OS="other"; fi
  PKG=""; command -v dnf >/dev/null 2>&1 && PKG="dnf"
  [ -z "$PKG" ] && command -v yum >/dev/null 2>&1 && PKG="yum"
  [ -z "$PKG" ] && command -v apt-get >/dev/null 2>&1 && PKG="apt"
}
detect_os

HAS_DOCKER=0
command -v docker >/dev/null 2>&1 && HAS_DOCKER=1

# ---------------------------------------------------------------- 清单预览
echo ""
echo "=============================================================="
echo "  FLA 全新重装 — 这台服务器上的 Docker 将被彻底清空"
echo "=============================================================="
if [ "$HAS_DOCKER" = "1" ]; then
  NC=$(docker ps -aq 2>/dev/null | wc -l)
  NI=$(docker images -q 2>/dev/null | wc -l)
  NV=$(docker volume ls -q 2>/dev/null | wc -l)
  echo "  容器: $NC 个   镜像: $NI 个   数据卷: $NV 个"
  echo ""
  echo "  即将被删除的容器:"
  docker ps -a --format '    {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null | head -30
  [ "$NC" -gt 30 ] && echo "    ... 共 $NC 个"
  echo ""
  echo "  即将被删除的数据卷:"
  docker volume ls -q 2>/dev/null | sed 's/^/    /' | head -20
else
  echo "  (未检测到 docker)"
fi
echo ""
echo "  FLA 数据卷 fla-data 里是【全部用户 / 课件 / 批注 / 聊天 / 论坛】"
if [ "$DO_BACKUP" = "1" ]; then
  echo "  → 会先备份到: $BACKUP_FILE"
else
  echo "  → ⚠ 你选择了 --no-backup: 数据将【不可恢复】"
fi
[ "$KEEP_DOCKER" = "1" ] && echo "  → --keep-docker: 保留 docker 本体, 只清空容器/镜像/卷"
echo "=============================================================="
echo ""

if [ "$FORCE" != "1" ]; then
  read -p "确认要继续吗? 请输入 YES-DELETE-ALL : " A
  if [ "$A" != "YES-DELETE-ALL" ]; then echo "已取消 (什么都没改)"; exit 0; fi
fi

# ---------------------------------------------------------------- 1. 备份数据卷
if [ "$HAS_DOCKER" = "1" ] && [ "$DO_BACKUP" = "1" ]; then
  log "[1/6] 备份 FLA 数据卷 ..."
  VOL=""
  for v in fla-data deploy_fla-data; do
    docker volume inspect "$v" >/dev/null 2>&1 && { VOL="$v"; break; }
  done
  if [ -n "$VOL" ]; then
    MP=$(docker volume inspect "$VOL" --format '{{.Mountpoint}}' 2>/dev/null)
    if [ -n "$MP" ] && [ -d "$MP" ]; then
      # 数据库一致性: 先停 app 容器, 让 SQLite WAL 落盘
      try docker stop fla
      if tar -czf "$BACKUP_FILE" -C "$MP" . >>"$LOG" 2>&1; then
        SZ=$(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1)
        log "  ✔ 已备份 $VOL → $BACKUP_FILE ($SZ)"
        log "    恢复方法: 重装后 docker run --rm -v fla-data:/d -v \"$SCRIPT_DIR\":/b alpine \\"
        log "              tar -xzf /b/$(basename "$BACKUP_FILE") -C /d"
      else
        log "  ⚠ 备份失败(磁盘满?) — 见 reinstall.log"
        if [ "$ASSUME_YES" != "1" ]; then
          read -p "  备份失败, 仍要继续删除? [y/N] " A2
          [ "$A2" = "y" ] || { log "已取消"; exit 1; }
        fi
      fi
    fi
  else
    log "  (没有找到 fla-data 数据卷, 跳过备份)"
  fi
else
  log "[1/6] 跳过备份"
fi

# ---------------------------------------------------------------- 2. 停止并删除所有容器
log "[2/6] 停止并删除所有容器 ..."
if [ "$HAS_DOCKER" = "1" ]; then
  # 先 compose down (清理网络/匿名卷), 再暴力兜底
  for F in deploy/docker-compose.yml deploy/docker-compose.lite.yml deploy/docker-compose.direct.yml; do
    [ -f "$F" ] || continue
    DC="docker compose"; docker compose version >/dev/null 2>&1 || DC="docker-compose"
    try $DC --profile full --profile ds -f "$F" down --remove-orphans
  done
  CID=$(docker ps -aq 2>/dev/null)
  if [ -n "$CID" ]; then
    try docker stop $CID
    docker rm -f $CID >>"$LOG" 2>&1
    log "  ✔ 已删除容器: $(echo "$CID" | wc -l) 个"
  else
    log "  (没有容器)"
  fi
fi

# ---------------------------------------------------------------- 3. 卷/网络/镜像/缓存
log "[3/6] 清理数据卷 / 网络 / 镜像 / 构建缓存 ..."
if [ "$HAS_DOCKER" = "1" ]; then
  if [ "$KEEP_DATA" = "1" ]; then
    for v in $(docker volume ls -q 2>/dev/null | grep -v -E '^(fla-data|deploy_fla-data)$'); do
      docker volume rm -f "$v" >>"$LOG" 2>&1
    done
    log "  ✔ 已保留 fla-data (--keep-data), 其余卷已删除"
  else
    try docker volume prune -f
    for v in $(docker volume ls -q 2>/dev/null); do docker volume rm -f "$v" >>"$LOG" 2>&1; done
    log "  ✔ 数据卷已清空"
  fi
  try docker network prune -f
  for n in $(docker network ls --format '{{.Name}}' 2>/dev/null | grep -v -E '^(bridge|host|none)$'); do
    docker network rm "$n" >>"$LOG" 2>&1
  done
  try docker builder prune -af
  try docker system prune -af --volumes
  if [ "$KEEP_DATA" != "1" ]; then
    try docker rmi -f $(docker images -q 2>/dev/null)
  else
    try docker rmi -f $(docker images -q 2>/dev/null)
  fi
  log "  ✔ 镜像/网络/缓存已清空 (剩余镜像: $(docker images -q 2>/dev/null | wc -l))"
fi

# ---------------------------------------------------------------- 4. 卸载 docker 本体
if [ "$KEEP_DOCKER" = "1" ]; then
  log "[4/6] --keep-docker: 跳过卸载 docker 本体"
else
  log "[4/6] 卸载 Docker 本体 ..."
  try systemctl stop docker.socket
  try systemctl stop docker
  try systemctl stop containerd
  try systemctl disable docker
  try systemctl disable containerd
  case "$PKG" in
    dnf|yum)
      try $PKG remove -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
                         docker-compose-plugin docker-ce-rootless-extras docker-engine \
                         docker docker-client docker-common podman-docker
      try $PKG autoremove -y
      ;;
    apt)
      try apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
                           docker-compose-plugin docker-ce-rootless-extras docker.io \
                           docker-compose docker-doc docker runc
      try apt-get autoremove -y --purge
      ;;
  esac
  rm -rf /var/lib/docker /var/lib/containerd /etc/docker /var/run/docker.sock \
         /usr/local/bin/docker-compose /root/.docker 2>>"$LOG"
  rm -f /etc/yum.repos.d/docker-ce.repo /etc/apt/sources.list.d/docker.list 2>>"$LOG"
  if command -v docker >/dev/null 2>&1; then
    log "  ⚠ 仍能找到 docker 命令: $(command -v docker) — 可能是手动安装的, 请自行删除"
  else
    log "  ✔ Docker 已彻底卸载 (/var/lib/docker 已删除)"
  fi
  # iptables 里 docker 遗留的链会让新装的 docker 出问题, 清一遍
  if command -v iptables >/dev/null 2>&1; then
    for CH in DOCKER DOCKER-USER DOCKER-ISOLATION-STAGE-1 DOCKER-ISOLATION-STAGE-2; do
      iptables -t filter -F "$CH" 2>/dev/null
      iptables -t filter -X "$CH" 2>/dev/null
      iptables -t nat -F "$CH" 2>/dev/null
      iptables -t nat -X "$CH" 2>/dev/null
    done
    log "  ✔ 已清理 docker 遗留的 iptables 链"
  fi
fi

# ---------------------------------------------------------------- 5. 清理 FLA 部署残留(保留源码与 .env)
log "[5/6] 清理部署状态文件 ..."
rm -f deploy/.compose_file deploy/.compose_profile 2>>"$LOG"
if [ "$KEEP_DATA" = "1" ] && [ "$KEEP_DOCKER" = "1" ]; then
  log "  (保留数据模式: deploy/.env 未改动)"
fi

# ---------------------------------------------------------------- 6. 重装
if [ "$SKIP_INSTALL" = "1" ]; then
  log "[6/6] --skip-install: 只清理, 不重装"
else
  log "[6/6] 开始全新安装 (会自动重装 docker → 构建 → 起容器 → 边缘 nginx 80/443 → SSL 文件验证) ..."
  echo ""
  if [ "$ASSUME_YES" = "1" ]; then
    bash "$SCRIPT_DIR/install.sh" --foreground "${PASS_ARGS[@]}" 2>&1 | tee -a "$LOG"
  else
    bash "$SCRIPT_DIR/install.sh" "${PASS_ARGS[@]}" 2>&1 | tee -a "$LOG"
  fi
fi

echo ""
log "=============================================================="
log "  全新重装流程结束"
[ -f "$BACKUP_FILE" ] && log "  旧数据备份: $BACKUP_FILE"
log "  恢复旧数据(如需): 见上面的『恢复方法』"
log "  日志: $LOG"
log "=============================================================="
