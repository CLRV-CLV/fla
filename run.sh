#!/bin/bash
# ================================================================
#  FLA (FYX Lesson All) 日常管理脚本 v1.27
#  用法:  sudo bash run.sh {status|start|stop|restart|logs|pull|update|screen|edge|ssl}
#    status   查看容器/服务状态 + 80/443 边缘网关 + 证书到期时间
#    start    启动(开机也会自动启动, 此为手动停止后再启动)
#    stop     停止(容器保留, 数据不丢)
#    restart  重启
#    logs     查看实时日志, 可选指定服务: run.sh logs [app|nginx|documentserver]
#    pull     同步最新代码并热重启容器: run.sh pull [分支名, 默认当前分支]
#    update   更新/重新部署版本(智能重建容器, 数据保留, 自动进 screen) [分支名]
#    screen   查看正在进行的安装会话(如果有)
#    edge     查看/重建边缘网关(nginx 占 80/443 接管所有域名 → 反代 FLA 端口)
#             run.sh edge [status|reload|setup 域名...]
#    ssl      证书相关: run.sh ssl [status|renew|签发新域名...]
#    doctor   一键诊断: 收集全部容器日志/网络/健康检查到 doctor.log
#    ds-remove 删除 OnlyOffice 容器并重启 (Office 默认走微软在线渲染)
#    reset-admin 重置管理员密码: run.sh reset-admin [新密码]
# ================================================================
set -u
cd "$(dirname "$0")"

if [ "$(id -u)" != "0" ]; then
  echo "请用 root 运行: sudo bash run.sh {status|start|stop|restart|logs|update}"
  exit 1
fi

DC=""
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else echo "✘ 未找到 docker compose"; exit 1; fi

CF=$(cat deploy/.compose_file 2>/dev/null)
if [ -z "$CF" ] || [ ! -f "$CF" ]; then
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
[ -n "$PROF" ] && DCP="--profile $PROF"
PORT=$(grep -E '^PORT=' deploy/.env 2>/dev/null | head -1 | cut -d= -f2)
PORT=${PORT:-8306}
PUBLIC_URL=$(grep -E '^PUBLIC_BASE_URL=' deploy/.env 2>/dev/null | head -1 | cut -d= -f2-)

case "${1:-help}" in
  https)
    shift
    exec bash ./https.sh "$@"
    ;;
  status)
    echo "── FLA 容器状态 (compose: $CF ${PROF:+profile:$PROF}) ──"
    docker ps -a --filter name=fla --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    echo ""
    echo "── 健康检查 ──"
    PHOST=$(echo "${PUBLIC_URL:-}" | sed -E 's~https?://([^/:]+).*~\1~')
    CHOST="${PHOST:-run.sh.local}"
    if curl -sfL -m 4 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 || \
       curl -sfkL -m 4 -H "Host: $CHOST" "https://127.0.0.1/api/health" >/dev/null 2>&1; then
      echo "✔ 应用正常  http://127.0.0.1:$PORT/ (外网: ${PUBLIC_URL:-http://127.0.0.1:$PORT})"
    else
      echo "✘ 应用未响应 (http://127.0.0.1:$PORT/api/health)"
      echo "  排查: sudo bash run.sh logs app"
    fi
    echo ""
    echo "── 边缘网关 (80/443 接管所有域名 → 127.0.0.1:$PORT) ──"
    if [ -f /etc/nginx/conf.d/fla-edge.conf ]; then
      echo "✔ 配置存在: /etc/nginx/conf.d/fla-edge.conf"
      systemctl is-active nginx >/dev/null 2>&1 && echo "✔ 宿主机 nginx 运行中" || echo "✘ 宿主机 nginx 未运行: systemctl restart nginx"
      command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -E ':(80|443)\s' | awk '{print "  监听: "$4}'
      curl -sfkL -m 4 -H "Host: $CHOST" http://127.0.0.1/api/health >/dev/null 2>&1 \
        && echo "✔ 80  → FLA 连通" || echo "✘ 80  → FLA 不通 (sudo bash edge.sh status 排查)"
      curl -sfkL -m 4 -H "Host: $CHOST" https://127.0.0.1/api/health >/dev/null 2>&1 \
        && echo "✔ 443 → FLA 连通" || echo "⚠ 443 → FLA 不通 (证书缺失? sudo bash https.sh 域名)"
    else
      echo "⚠ 未部署边缘网关 — 只能用 http://IP:$PORT 访问"
      echo "  部署: sudo bash edge.sh --port $PORT setup [域名...]"
    fi
    echo ""
    echo "── SSL 证书 ──"
    if [ -s /etc/fla/ssl/fullchain.pem ]; then
      openssl x509 -in /etc/fla/ssl/fullchain.pem -noout -enddate 2>/dev/null | sed 's/notAfter=/到期: /'
      openssl x509 -in /etc/fla/ssl/fullchain.pem -noout -issuer 2>/dev/null | grep -q 'FLA Edge' \
        && echo "⚠ 自签兜底证书 → sudo bash https.sh 域名 签发正式证书" \
        || echo "✔ 正式证书 (Let's Encrypt, 自动续期)"
      crontab -l 2>/dev/null | grep -q fla-cert-renew && echo "✔ 自动续期已配置" || echo "⚠ 自动续期未配置: sudo bash https.sh --renew"
    else
      echo "⚠ 无证书 (443 不可用)"
    fi
    [ -n "$PUBLIC_URL" ] && echo "" && echo "── 公开访问地址 (微软放映直链用) ──" && echo "  $PUBLIC_URL"
    ;;
  edge)
    shift
    SUB="${1:-status}"
    case "$SUB" in
      reload) exec bash ./edge.sh --port "$PORT" reload ;;
      setup)  shift; exec bash ./edge.sh --port "$PORT" setup "$@" ;;
      remove) exec bash ./edge.sh remove ;;
      no-catchall) exec bash ./edge.sh --port "$PORT" no-catchall ;;
      *)      exec bash ./edge.sh status ;;
    esac
    ;;
  ssl)
    shift
    SUB="${1:-status}"
    case "$SUB" in
      status) exec bash ./https.sh --status ;;
      renew)  exec bash ./https.sh --renew ;;
      check)  exec bash ./https.sh --check ;;
      *)      exec bash ./https.sh --port "$PORT" "$@" ;;
    esac
    ;;
  start)
    echo ">> 启动 FLA ..."
    docker rm -f nginx 2>/dev/null || true
    $DC $DCP -f "$CF" up -d --remove-orphans || { echo "✘ 启动失败, 查看日志: sudo bash run.sh logs"; exit 1; }
    for i in $(seq 1 20); do
      curl -sf -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && { echo "✔ 已启动"; exit 0; }
      sleep 2
    done
    echo "⚠ 容器已启动但健康检查未通过, 请稍候或查看: sudo bash run.sh logs"
    ;;
  stop)
    echo ">> 停止 FLA (数据保留) ..."
    $DC $DCP -f "$CF" stop
    echo "✔ 已停止 (重新启动: sudo bash run.sh start)"
    ;;
  restart)
    echo ">> 重启 FLA ..."
    docker rm -f nginx 2>/dev/null || true
    $DC $DCP -f "$CF" up -d --remove-orphans
    for i in $(seq 1 20); do
      curl -sf -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && { echo "✔ 已重启"; exit 0; }
      sleep 2
    done
    echo "⚠ 重启完成但健康检查未通过, 请查看: sudo bash run.sh logs"
    ;;
  logs)
    shift
    SVC="${1:-}"
    if [ -n "$SVC" ]; then
      $DC $DCP -f "$CF" logs -f --tail=200 "$SVC"
    else
      $DC $DCP -f "$CF" logs -f --tail=200
    fi
    ;;
  pull)
    shift
    TARGET_BRANCH="${1:-${FLA_BRANCH:-arena/01a0d6ff-fla}}"
    echo ">> 同步最新代码 (分支 $TARGET_BRANCH) ..."
    UPDATED=0
    if [ -d .git ] && command -v git >/dev/null 2>&1; then
      OLD_C=$(git rev-parse --short HEAD 2>/dev/null)
      # 注意: 必须用 FETCH_HEAD; origin/<分支> 在单分支克隆里不会更新, 会"假装更新成功"
      if git fetch origin "$TARGET_BRANCH" 2>/dev/null && git reset --hard FETCH_HEAD 2>/dev/null; then
        UPDATED=1
      elif git fetch https://github.com/CLRV-FYX/fla.git "$TARGET_BRANCH" 2>/dev/null && git reset --hard FETCH_HEAD 2>/dev/null; then
        UPDATED=1
      fi
    fi
    if [ "$UPDATED" = "0" ]; then
      echo "  (无 git 仓库, 下载代码包)"
      TGZ=/tmp/fla-src.tgz; rm -f "$TGZ"
      for U in "https://codeload.github.com/CLRV-FYX/fla/tar.gz/refs/heads/${TARGET_BRANCH}" \
               "https://github.com/CLRV-FYX/fla/archive/refs/heads/${TARGET_BRANCH}.tar.gz" \
               "https://gh-proxy.com/https://github.com/CLRV-FYX/fla/archive/refs/heads/${TARGET_BRANCH}.tar.gz"; do
        echo "  下载: $U"
        if command -v curl >/dev/null 2>&1; then curl -fL --connect-timeout 15 --max-time 600 -o "$TGZ" "$U" || rm -f "$TGZ"
        elif command -v wget >/dev/null 2>&1; then wget -T 15 -O "$TGZ" "$U" || rm -f "$TGZ"
        else python3 -c "import sys,urllib.request;urllib.request.urlretrieve(sys.argv[1],sys.argv[2])" "$U" "$TGZ" 2>&1 | tail -1 || rm -f "$TGZ"; fi
        if [ -s "$TGZ" ] && tar -tzf "$TGZ" >/dev/null 2>&1; then break; fi
        echo "  ✘ 失败, 换下一个地址"; rm -f "$TGZ"
      done
      if [ -s "$TGZ" ] && tar -xzf "$TGZ" --strip-components=1; then
        UPDATED=1
        echo "  ✔ 代码已更新 ($(grep -oP 'CURRENT_VERSION = "\K[^"]+' server/routers/desktop.py 2>/dev/null))"
        date '+tgz %Y-%m-%d %H:%M' > server/BUILD
      fi
    fi
    if [ -d .git ]; then
      echo "  代码版本: ${OLD_C:-?} -> $(git log --oneline -1 2>/dev/null)"
      git log -1 --format='%h %cd' --date=format:'%Y-%m-%d %H:%M' > server/BUILD 2>/dev/null || true
    fi
    [ "$UPDATED" = "0" ] && echo "  ✘ 代码同步失败 (请检查服务器能否访问 github.com)"
    # 重新 exec 本脚本，防止 bash 内存缓存/文件偏移导致未执行新版指令
    exec bash "$0" apply-update
    ;;
  apply-update)
    echo ">> 清理旧中间层容器 (释放 8306 端口归还给 fla 容器) ..."
    docker rm -f nginx 2>/dev/null || true
    echo ">> 放行 iPhone 整屏投屏端口 ${RTMP_PORT:-1935}/tcp ..."
    RP="${RTMP_PORT:-1935}"
    if systemctl is-active firewalld >/dev/null 2>&1; then
      if ! firewall-cmd --query-port="$RP/tcp" >/dev/null 2>&1; then
        firewall-cmd --permanent --add-port="$RP/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1
        # firewalld reload 会清空 docker 的 iptables 规则 → 重启 docker 恢复 (随后容器会被重建)
        systemctl restart docker 2>/dev/null || true
        echo "  ✔ firewalld 已放行 $RP/tcp"
      else
        echo "  ✔ firewalld 已放行过 $RP/tcp"
      fi
    fi
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
      ufw allow "$RP/tcp" >/dev/null 2>&1 && echo "  ✔ ufw 已放行 $RP/tcp"
    fi
    if command -v iptables >/dev/null 2>&1 && ! iptables -C INPUT -p tcp --dport "$RP" -j ACCEPT 2>/dev/null; then
      iptables -I INPUT -p tcp --dport "$RP" -j ACCEPT 2>/dev/null && echo "  ✔ iptables 已放行 $RP/tcp"
    fi
    echo "  (云服务器还需在控制台「安全组/防火墙」放行 TCP $RP, 脚本无法代劳)"
    echo ">> 重建并拉起 fla 容器 ($PORT->$PORT) ..."
    if [ -n "$DC" ] && [ -f "$CF" ]; then
      $DC $DCP -f "$CF" up -d --remove-orphans --force-recreate
    fi
    systemctl restart fla 2>/dev/null || true
    echo ">> 等待服务健康检查 ..."
    OK=0
    for i in $(seq 1 20); do
      if curl -sf -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
        OK=1
        break
      fi
      sleep 2
    done
    if [ "$OK" = "1" ]; then
      echo "✔ 应用已就绪 (http://127.0.0.1:$PORT/)"
    else
      echo "⚠ 应用启动中，若未响应可查看日志: sudo bash run.sh logs"
    fi
    echo ""
    echo "── 容器状态 ──"
    docker ps -a --filter name=fla --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true
    echo ""
    echo "✔ 更新完成！"
    ;;
  update)
    shift
    TARGET_BRANCH="${1:-${FLA_BRANCH:-arena/01a0d6ff-fla}}"
    echo ">> 拉取最新代码 (分支 $TARGET_BRANCH) ..."
    if [ -d .git ]; then
      git fetch origin "$TARGET_BRANCH" && git reset --hard "origin/$TARGET_BRANCH"
    elif command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1; then
      echo "  (非 git 仓库，通过官方归档包更新代码)"
      curl -sL "https://github.com/CLRV-FYX/fla/archive/refs/heads/${TARGET_BRANCH}.tar.gz" | tar -xz --strip-components=1
    fi
    echo ">> 重新运行 install.sh (智能重建, 数据保留) ..."
    exec bash "$PWD/install.sh" "$@"
    ;;
  ds-remove)
    echo ">> 移除 OnlyOffice 容器 (v1.19: Office 已改用微软在线渲染) ..."
    if ! command -v docker >/dev/null 2>&1; then echo "✘ 未安装 docker"; exit 1; fi
    C=$(docker ps -a --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -iE 'onlyoffice|documentserver' | cut -f1)
    if [ -z "$C" ]; then
      echo "  (未发现 OnlyOffice 容器)"
    else
      for c in $C; do
        docker rm -f "$c" >/dev/null 2>&1 && echo "  ✔ 已删除容器: $c"
      done
    fi
    sed -i 's/^ONLYOFFICE_URL=.*/ONLYOFFICE_URL=/' deploy/.env 2>/dev/null
    echo "  ✔ 已清空 ONLYOFFICE_URL 配置"
    if [ -f "$CF" ]; then
      echo ">> 重启 FLA (应用新配置) ..."
      $DC $DCP -f "$CF" up -d --force-recreate app 2>/dev/null || $DC $DCP -f "$CF" up -d
    fi
    echo "完成: Office 预览/放映现在走微软在线渲染 (需域名+80/443)"
    ;;
  reset-admin)
    NEW_PW="${2:-admin123}"
    echo ">> 重置管理员密码为: $NEW_PW"
    TARGET_CONTAINER=$(docker ps -q --filter name=fla 2>/dev/null | head -1)
    if [ -n "$TARGET_CONTAINER" ]; then
      docker exec -i "$TARGET_CONTAINER" python3 -c "import sqlite3, bcrypt; conn=sqlite3.connect('/data/fla.db'); h=bcrypt.hashpw(b'$NEW_PW', bcrypt.gensalt()).decode(); conn.execute('UPDATE users SET password_hash=? WHERE username=\"admin\"', (h,)); conn.commit(); print('✔ 管理员(admin) 密码已重置为: $NEW_PW')"
    else
      echo "✘ fla 容器未运行，请先启动: sudo bash run.sh start"
    fi
    ;;
  doctor)
    OUT="$PWD/doctor.log"
    {
      echo "===== FLA 诊断报告 $(date '+%F %T') ====="
      echo "--- 当前 compose 配置 ---"
      echo "file: $CF  profile: ${PROF:-无}"
      echo "--- .env (敏感信息已脱敏) ---"
      sed -E 's/(PASSWORD|SECRET)=.*/\1=***(已隐藏)/' deploy/.env 2>/dev/null || echo "(无 .env)"
      echo "--- 容器状态 ---"
      docker ps -a --filter name=fla --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
      echo "--- 健康检查 (本地:$PORT) ---"
      curl -sf -m 4 "http://127.0.0.1:$PORT/api/health" && echo " <- OK" || echo "✘ 本地 $PORT 不通"
      echo "--- 边缘网关 (宿主机 nginx 80/443) ---"
      if command -v nginx >/dev/null 2>&1; then
        nginx -v 2>&1; systemctl is-active nginx 2>&1
        [ -f /etc/nginx/conf.d/fla-edge.conf ] && { echo "[fla-edge.conf]"; cat /etc/nginx/conf.d/fla-edge.conf; } || echo "(无 fla-edge.conf)"
        nginx -t 2>&1 | head -5
      else
        echo "(宿主机未安装 nginx)"
      fi
      echo "80/443 监听:"; ss -ltnp 2>/dev/null | grep -E ':(80|443)\s' | head -6
      echo "80 → FLA:"; curl -sf -m 4 -H 'Host: doctor.local' http://127.0.0.1/api/health && echo " OK" || echo " ✘ 不通"
      echo "443 → FLA:"; curl -sfk -m 4 -H 'Host: doctor.local' https://127.0.0.1/api/health && echo " OK" || echo " ✘ 不通"
      echo "--- SSL 证书 ---"
      if [ -s /etc/fla/ssl/fullchain.pem ]; then
        openssl x509 -in /etc/fla/ssl/fullchain.pem -noout -subject -issuer -enddate 2>&1
      else echo "(无证书)"; fi
      echo "--- 证书续期任务 ---"; crontab -l 2>/dev/null | grep -i 'cert\|acme' || echo "(无)"
      echo "--- 公开访问地址 ---"; grep -E '^PUBLIC_BASE_URL=' deploy/.env 2>/dev/null || echo "(未设置)"
      echo "--- 容器网络 ---"
      docker inspect fla --format 'fla 网络: {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null
      docker inspect nginx --format 'nginx 网络: {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || echo "(nginx 不存在)"
      echo "--- DNS 解析测试 (从 nginx 容器内解析 app) ---"
      docker exec nginx nslookup app 2>&1 | head -5 || true
      echo "--- OnlyOffice 密钥对比 ---"
      DS_C=$(docker ps --format '{{.Names}}' | grep -iE 'onlyoffice|documentserver' | head -1)
      if [ -n "$DS_C" ]; then
        APP_S=$(grep '^ONLYOFFICE_JWT_SECRET=' deploy/.env 2>/dev/null | cut -d= -f2)
        DS_S=$(docker exec "$DS_C" /var/www/onlyoffice/documentserver/npm/json -f /etc/onlyoffice/documentserver/local.json services.CoAuthoring.secret.session.string 2>/dev/null | tr -d '"')
        fp(){ local s=$1; if [ -n "$s" ]; then echo "${s:0:4}...${s: -4} (len=${#s})"; else echo "(空)"; fi; }
        echo "FLA .env 密钥:   $(fp "$APP_S")"
        echo "DS session 密钥: $(fp "$DS_S")"
        if [ "$APP_S" = "$DS_S" ]; then echo "→ 一致"; else echo "→ 不一致! 重跑 sudo bash install.sh 自动校准"; fi
        if docker exec "$DS_C" test -f /usr/share/fonts/truetype/fla/.fla_fonts_v1 2>/dev/null; then
          echo "--- OnlyOffice 中文字体: 已注入(v1.26) ---"
        else
          echo "--- OnlyOffice 中文字体: 未注入 (PPT 字体错位时重跑 sudo bash install.sh) ---"
        fi
      else
        echo "(未发现运行中的 OnlyOffice 容器)"
      fi
      echo "--- fla 日志 (最后60行) ---"
      docker logs --tail 60 fla 2>&1
      echo "--- nginx 日志 (最后60行) ---"
      docker logs --tail 60 nginx 2>&1 || echo "(nginx 不存在)"
      echo "--- 资源 ---"
      free -m | head -2
      df -h / | tail -1
      echo "===== 诊断结束 ====="
    } | tee "$OUT"
    echo ""
    echo "诊断完成: 已生成 $OUT — 把这个文件发给开发者即可定位问题"
    ;;
  screen)
    if command -v screen >/dev/null 2>&1 && screen -ls 2>/dev/null | grep -q fla-install; then
      exec screen -r fla-install
    else
      echo "当前没有进行中的安装会话"
      echo "历史安装日志: less $PWD/install.log"
    fi
    ;;
  *)
    grep '^#' "$0" | sed 's/^# \{0,2\}//' | head -14
    ;;
esac
