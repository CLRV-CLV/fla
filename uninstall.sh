#!/bin/bash
# FLA 卸载脚本: 停止并删除 FLA 容器与数据卷
# ⚠ 会删除全部用户/课件/批注/聊天数据, 不可恢复(除非你先备份)
# 用法:
#   sudo bash uninstall.sh              删容器+数据卷(镜像保留)
#   sudo bash uninstall.sh --images     连镜像一起删
#   sudo bash uninstall.sh --edge       同时移除宿主机 nginx 的 80/443 边缘网关配置
#   sudo bash uninstall.sh --keep-data  只删容器, 保留数据卷(下次装回来数据还在)
#   sudo bash uninstall.sh --backup     先把数据卷备份成 tar.gz 再删
set -u
[ "$(id -u)" = "0" ] || { echo "请用 root 运行: sudo bash uninstall.sh"; exit 1; }
cd "$(dirname "$0")"

RMIMG=0; RMEDGE=0; KEEPDATA=0; BACKUP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --images) RMIMG=1; shift ;;
    --edge) RMEDGE=1; shift ;;
    --keep-data) KEEPDATA=1; shift ;;
    --backup) BACKUP=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,2\}//'; exit 0 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

DC=""
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else echo "未找到 docker compose (可能已卸载, 继续做兜底清理)"; fi

echo "⚠ 这将删除 FLA 的容器$([ "$KEEPDATA" = "1" ] || echo '和全部数据(用户/课件/批注/聊天)'), 且不可恢复!"
read -p "输入 yes 确认删除: " A
[ "$A" = "yes" ] || { echo "已取消"; exit 0; }

# ---- 可选备份 ----
if [ "$BACKUP" = "1" ] && command -v docker >/dev/null 2>&1; then
  VOL=""; for v in fla-data deploy_fla-data; do docker volume inspect "$v" >/dev/null 2>&1 && { VOL="$v"; break; }; done
  if [ -n "$VOL" ]; then
    F="$PWD/fla-backup-$(date '+%Y%m%d-%H%M%S').tar.gz"
    docker stop fla >/dev/null 2>&1
    MP=$(docker volume inspect "$VOL" --format '{{.Mountpoint}}' 2>/dev/null)
    if [ -n "$MP" ] && tar -czf "$F" -C "$MP" . 2>/dev/null; then
      echo "✔ 数据已备份: $F ($(du -h "$F" | cut -f1))"
    else
      echo "⚠ 备份失败, 继续卸载"
    fi
  fi
fi

[ -n "$DC" ] && {
  $DC -f deploy/docker-compose.yml down -v --remove-orphans 2>/dev/null
  $DC -f deploy/docker-compose.lite.yml down -v --remove-orphans 2>/dev/null
  [ -f deploy/docker-compose.direct.yml ] && $DC --profile full -f deploy/docker-compose.direct.yml down -v --remove-orphans 2>/dev/null
}
# 兜底清理(处理改名/历史遗留)
docker rm -f fla nginx fla-onlyoffice 2>/dev/null
if [ "$KEEPDATA" = "1" ]; then
  echo "✔ 数据卷 fla-data 已保留 (下次部署数据仍在)"
else
  docker volume rm -f fla-data deploy_fla-data 2>/dev/null
fi
rm -f deploy/.compose_file deploy/.compose_profile

if [ "$RMIMG" = "1" ]; then
  echo ">> 删除 FLA 相关镜像 ..."
  docker rmi -f onlyoffice/documentserver:latest 2>/dev/null
  docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(deploy-app|deploy-nginx|fla)' | xargs -r docker rmi -f 2>/dev/null
else
  echo ">> 镜像已保留 (如需连镜像一起删: sudo bash uninstall.sh --images)"
fi

if [ "$RMEDGE" = "1" ]; then
  echo ">> 移除宿主机 nginx 边缘网关 (80/443) ..."
  bash ./edge.sh remove
  rm -f /etc/fla/edge.state
  echo "   (Let's Encrypt 证书保留在 /etc/fla/ssl 与 /etc/letsencrypt; 撤销续期: sudo bash https.sh --remove)"
else
  echo ">> 宿主机 nginx 的 80/443 边缘网关配置已保留 (移除: sudo bash uninstall.sh --edge)"
fi
echo "完成。"
