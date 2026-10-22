#!/bin/bash
# ================================================================
#  FLA HTTPS — Let's Encrypt 证书【文件验证 (HTTP-01 / webroot)】
#  ----------------------------------------------------------------
#  v1.27 重写: 不再用 --standalone 抢占 80 (那需要停 nginx, 有停机窗口),
#  改为文件验证: nginx 持续在 80 上服务 /.well-known/acme-challenge/,
#  签发与续期全程不停机、不影响正在上课的老师。
#
#  用法:
#    sudo bash https.sh                       交互输入域名(回车=沿用上次/默认)
#    sudo bash https.sh 域名1 域名2 ...       多域名一张证书(SAN)
#    sudo bash https.sh --yes 域名...         非交互(install.sh 内部调用)
#    sudo bash https.sh --check               干跑: 只做 DNS/80 连通性自检
#    sudo bash https.sh --status              查看证书与到期时间
#    sudo bash https.sh --renew               手动续期一次
#    sudo bash https.sh --force 域名...       强制重签(忽略现有证书)
#    sudo bash https.sh --remove              撤销自动续期任务(证书保留)
#
#  做的事:
#   1. 确保边缘网关就绪 (调用 edge.sh: nginx 占 80/443, catch-all 接管所有域名)
#   2. 文件验证自检: 往 /var/www/fla-acme 写测试文件, 用域名回读, 提前暴露
#      "DNS 没指向本机 / 云安全组没放行 80 / 有别的程序抢 80" 这三类问题
#   3. 签发: certbot --webroot (缺 certbot 时自动改用 acme.sh, 两者都是文件验证)
#   4. 安装到 /etc/fla/ssl/{fullchain,privkey}.pem → reload nginx (443 立即生效)
#   5. 自动续期: cron(每天两次) + deploy 钩子(续期后自动拷贝证书并重载)
#   6. 把公开访问地址写进 deploy/.env (PUBLIC_BASE_URL=https://主域名),
#      微软 Office 在线放映要求的"域名 + 80/443 直链"由此自动满足
# ================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
LOG="$SCRIPT_DIR/https.log"
ENVF="$SCRIPT_DIR/deploy/.env"
STATE="/etc/fla/edge.state"

DEFAULT_DOMS="t.clrv.top t.fyx.best"
CERT_NAME="fla"
LE_LIVE="/etc/letsencrypt/live/$CERT_NAME"
CERT_DIR="/etc/fla/ssl"
CERT="$CERT_DIR/fullchain.pem"
KEY="$CERT_DIR/privkey.pem"
WEBROOT="/var/www/fla-acme"
CHAL="$WEBROOT/.well-known/acme-challenge"
HOOK="/etc/letsencrypt/renewal-hooks/deploy/fla-install-cert.sh"
ACME_HOME="/root/.acme.sh"

MODE="run"; ASSUME_YES=0; FORCE=0; DOMS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check)   MODE="check";   shift ;;
    --status)  MODE="status";  shift ;;
    --renew)   MODE="renew";   shift ;;
    --remove)  MODE="remove";  shift ;;
    --force)   FORCE=1;        shift ;;
    --yes|-y)  ASSUME_YES=1;  shift ;;
    --port)    PORT_OVERRIDE="${2:-}"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,2\}//'; exit 0 ;;
    -*) echo "未知参数: $1 (用法见 --help)"; exit 1 ;;
    *) DOMS="$DOMS $1"; shift ;;
  esac
done
DOMS=$(echo "$DOMS" | tr ',' ' '); DOMS=$(echo $DOMS)

log(){ local L="[$(date '+%H:%M:%S')] $*"; echo "$L"; [ "$(id -u)" = "0" ] && echo "$L" >>"$LOG" 2>/dev/null; }

fla_port(){
  local P="${PORT_OVERRIDE:-}"
  [ -z "$P" ] && P=$(grep -E '^PORT=' "$ENVF" 2>/dev/null | head -1 | cut -d= -f2)
  echo "${P:-8306}"
}
FPORT=$(fla_port)

set_env(){  # set_env KEY VALUE
  [ -f "$ENVF" ] || { mkdir -p "$(dirname "$ENVF")"; : >"$ENVF"; chmod 600 "$ENVF"; }
  if grep -q "^$1=" "$ENVF"; then sed -i "s|^$1=.*|$1=$2|" "$ENVF"; else echo "$1=$2" >>"$ENVF"; fi
}

# ---------------- 域名收集 ----------------
if [ -z "$DOMS" ]; then
  if [ "$MODE" = "run" ] && [ -t 0 ] && [ "$ASSUME_YES" != "1" ]; then
    LAST=$(grep -E '^DOMAINS=' "$STATE" 2>/dev/null | head -1 | cut -d= -f2-)
    read -p "请输入域名(多个用空格分隔, 回车 = ${LAST:-$DEFAULT_DOMS}): " IN
    DOMS=${IN:-${LAST:-$DEFAULT_DOMS}}
  else
    DOMS=$(grep -E '^DOMAINS=' "$STATE" 2>/dev/null | head -1 | cut -d= -f2-)
    [ -z "$DOMS" ] && DOMS="$DEFAULT_DOMS"
  fi
fi
DOMS=$(echo "$DOMS" | tr ',' ' '); DOMS=$(echo $DOMS)
[ -n "$DOMS" ] || { echo "✘ 未提供域名"; exit 1; }
for d in $DOMS; do
  echo "$d" | grep -Eq '^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$' \
    || { echo "✘ 域名格式不对: $d"; exit 1; }
done
PRIMARY=$(echo $DOMS | awk '{print $1}')

# ---------------- 工具函数 ----------------
public_ip(){ curl -sf -m 6 https://api.ipify.org 2>/dev/null || curl -sf -m 6 https://ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}'; }

dns_check(){
  local IP DNSIP BAD=0 d
  IP=$(public_ip)
  log "本机公网 IP: ${IP:-未知}"
  for d in $DOMS; do
    DNSIP=$(getent hosts "$d" 2>/dev/null | awk '{print $1}' | head -1)
    if [ -z "$DNSIP" ]; then
      log "  ✘ $d: DNS 解析失败 — 请到域名服务商加一条 A 记录指向 ${IP:-本机公网IP}"; BAD=$((BAD+1))
    elif [ -n "$IP" ] && [ "$DNSIP" != "$IP" ]; then
      log "  ✘ $d 解析到 $DNSIP ≠ 本机 $IP — 签发必定失败, 请先改 DNS(或等生效)"; BAD=$((BAD+1))
    else
      log "  ✓ $d → $DNSIP"
    fi
  done
  return $BAD
}

# 文件验证自检: 写一个随机文件, 再用域名从公网回读一遍
file_check(){
  mkdir -p "$CHAL" || return 2
  local TOK="fla-selftest-$(date +%s)-$RANDOM" d OK=0 BAD=0
  echo "ok-$TOK" > "$CHAL/$TOK"
  chmod 644 "$CHAL/$TOK" 2>/dev/null
  for d in $DOMS; do
    local GOT
    GOT=$(curl -sf -m 12 "http://$d/.well-known/acme-challenge/$TOK" 2>/dev/null | head -1)
    if [ "$GOT" = "ok-$TOK" ]; then
      log "  ✓ 文件验证通道 OK: http://$d/.well-known/acme-challenge/"
      OK=$((OK+1))
    else
      log "  ✘ 文件验证不通: http://$d/.well-known/acme-challenge/$TOK (取回: '${GOT:-空}')"
      BAD=$((BAD+1))
    fi
  done
  rm -f "$CHAL/$TOK"
  if [ "$BAD" != "0" ]; then
    log "  排查顺序:"
    log "   1) 云服务器控制台安全组是否放行 80 (最常见)"
    log "   2) 本机防火墙: firewall-cmd --list-ports / ufw status"
    log "   3) nginx 是否在跑: systemctl status nginx ; 配置: nginx -t"
    log "   4) 是否有别的程序/容器抢了 80: sudo bash edge.sh status"
    log "   5) 域名是否开启了 CDN/代理(需回源到本机且透传 /.well-known/)"
  fi
  [ "$OK" != "0" ] && return 0
  return 1
}

cert_fresh(){   # 证书存在 + 覆盖全部域名 + 30 天内不到期 + 非自签
  [ -s "$CERT" ] || return 1
  command -v openssl >/dev/null 2>&1 || return 0
  openssl x509 -in "$CERT" -noout -issuer 2>/dev/null | grep -q 'FLA Edge' && return 1   # 自签兜底
  local TXT d
  TXT=$(openssl x509 -in "$CERT" -noout -text 2>/dev/null) || return 1
  for d in $DOMS; do echo "$TXT" | grep -q "DNS:$d[, ]\|DNS:$d$" || return 1; done
  openssl x509 -in "$CERT" -checkend $((30*24*3600)) -noout >/dev/null 2>&1 || return 1
  return 0
}

ensure_edge(){
  # 边缘网关(80/443 catch-all)必须先就绪, 否则文件验证无从谈起
  if [ ! -f /etc/nginx/conf.d/fla-edge.conf ] || ! systemctl is-active nginx >/dev/null 2>&1; then
    log ">> 先部署边缘网关 (nginx 占用 80/443 → 反代 127.0.0.1:$FPORT) ..."
    bash "$SCRIPT_DIR/edge.sh" --port "$FPORT" setup $DOMS >>"$LOG" 2>&1 || {
      log "  ⚠ edge.sh 返回非 0, 继续尝试(详见 https.log)"; }
  else
    # 已有配置: 刷新域名/端口并重载
    bash "$SCRIPT_DIR/edge.sh" --port "$FPORT" setup $DOMS >>"$LOG" 2>&1 || true
  fi
  mkdir -p "$CHAL"; chmod -R a+rX "$WEBROOT" 2>/dev/null
  systemctl is-active nginx >/dev/null 2>&1 || { log "✘ nginx 未运行, 无法做文件验证"; return 1; }
  return 0
}

install_cert_to_edge(){   # $1=fullchain $2=privkey
  mkdir -p "$CERT_DIR"
  cp -f "$1" "$CERT" && cp -f "$2" "$KEY" || { log "✘ 证书安装失败(拷贝到 $CERT_DIR)"; return 1; }
  chmod 600 "$KEY"; chmod 644 "$CERT"
  nginx -t >>"$LOG" 2>&1 || { log "✘ nginx 配置校验失败"; return 1; }
  systemctl reload nginx >>"$LOG" 2>&1 || systemctl restart nginx >>"$LOG" 2>&1
  log "  ✔ 证书已安装到 $CERT_DIR 并 reload nginx (443 生效)"
}

write_deploy_hook(){
  mkdir -p "$(dirname "$HOOK")" 2>/dev/null
  cat > "$HOOK" <<EOF
#!/bin/bash
# FLA: 证书续期后自动把新证书装到边缘网关并重载 nginx (由 https.sh 生成)
SRC="$LE_LIVE"
[ -s "\$SRC/fullchain.pem" ] || exit 0
mkdir -p "$CERT_DIR"
cp -f "\$SRC/fullchain.pem" "$CERT"
cp -f "\$SRC/privkey.pem"   "$KEY"
chmod 600 "$KEY"; chmod 644 "$CERT"
nginx -t >/dev/null 2>&1 && systemctl reload nginx
exit 0
EOF
  chmod +x "$HOOK" 2>/dev/null
}

write_cron(){
  local LINE
  if command -v certbot >/dev/null 2>&1; then
    LINE="17 3,15 * * * certbot renew --quiet --deploy-hook '$HOOK' >>/var/log/fla-certbot.log 2>&1 # fla-cert-renew"
  elif [ -x "$ACME_HOME/acme.sh" ]; then
    LINE="17 3,15 * * * $ACME_HOME/acme.sh --cron --home $ACME_HOME >>/var/log/fla-acme.log 2>&1 # fla-cert-renew"
  else
    return 1
  fi
  ( crontab -l 2>/dev/null | grep -v 'fla-cert-renew'; echo "$LINE" ) | crontab - >>"$LOG" 2>&1
  # 有 systemd 时再加一个 timer 兜底(部分机器没有 crond)
  if command -v systemctl >/dev/null 2>&1 && [ -d /etc/systemd/system ]; then
    cat > /etc/systemd/system/fla-cert-renew.service <<EOF
[Unit]
Description=FLA certificate renewal (HTTP-01 webroot)
[Service]
Type=oneshot
ExecStart=/bin/bash -lc '$(echo "$LINE" | cut -d' ' -f6- | sed "s/ # fla-cert-renew//")'
EOF
    cat > /etc/systemd/system/fla-cert-renew.timer <<EOF
[Unit]
Description=FLA certificate renewal timer
[Timer]
OnCalendar=*-*-* 03,15:17:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable --now fla-cert-renew.timer >/dev/null 2>&1
  fi
  log "  ✔ 自动续期已配置 (每天 03:17 / 15:17, 文件验证, 无需停机)"
}

# ---------------- 签发: certbot (优先) ----------------
issue_certbot(){
  if ! command -v certbot >/dev/null 2>&1; then
    log "  安装 certbot ..."
    if command -v apt-get >/dev/null 2>&1; then
      apt-get install -y -qq certbot >>"$LOG" 2>&1 || apt-get install -y -qq python3-certbot-nginx >>"$LOG" 2>&1
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y certbot python3-certbot-nginx >>"$LOG" 2>&1 || dnf install -y certbot >>"$LOG" 2>&1
    elif command -v yum >/dev/null 2>&1; then
      yum install -y epel-release >>"$LOG" 2>&1
      yum install -y certbot python3-certbot-nginx >>"$LOG" 2>&1 || yum install -y certbot >>"$LOG" 2>&1
    fi
    # 包管理器不行 → pip / snap / 官方脚本
    command -v certbot >/dev/null 2>&1 || { command -v pip3 >/dev/null 2>&1 && pip3 install --quiet certbot >>"$LOG" 2>&1; }
    command -v certbot >/dev/null 2>&1 || { command -v snap >/dev/null 2>&1 && { snap install core >/dev/null 2>&1; snap install --classic certbot >/dev/null 2>&1; ln -sf /snap/bin/certbot /usr/bin/certbot 2>/dev/null; }; }
  fi
  command -v certbot >/dev/null 2>&1 || return 2

  local DOMARGS="" d
  for d in $DOMS; do DOMARGS="$DOMARGS -d $d"; done
  log "  certbot 文件验证签发中 (webroot=$WEBROOT, 域名: $DOMS) ..."
  certbot certonly --webroot -w "$WEBROOT" --cert-name "$CERT_NAME" $DOMARGS --expand \
    --non-interactive --agree-tos --keep-until-expiring \
    -m "admin@$PRIMARY" >>"$LOG" 2>&1
  local RC=$?
  [ "$RC" != "0" ] && [ "$FORCE" = "1" ] && {
    log "  重试一次(强制重签)..."
    certbot certonly --webroot -w "$WEBROOT" --cert-name "$CERT_NAME" $DOMARGS --expand \
      --non-interactive --agree-tos --force-renewal -m "admin@$PRIMARY" >>"$LOG" 2>&1
    RC=$?
  }
  [ "$RC" = "0" ] || { log "  ✘ certbot 签发失败(原因见 https.log 尾部)"; tail -25 "$LOG" | sed 's/^/    /'; return 1; }
  [ -s "$LE_LIVE/fullchain.pem" ] || { log "  ✘ 签发成功但找不到证书文件 $LE_LIVE"; return 1; }
  install_cert_to_edge "$LE_LIVE/fullchain.pem" "$LE_LIVE/privkey.pem" || return 1
  return 0
}

# ---------------- 签发: acme.sh (certbot 装不上时的备选, 同样是文件验证) ----------------
issue_acmesh(){
  if [ ! -x "$ACME_HOME/acme.sh" ]; then
    log "  安装 acme.sh (纯 shell, 无依赖) ..."
    curl -sfL https://get.acme.sh -o /tmp/acme.sh 2>>"$LOG" || { log "  ✘ acme.sh 下载失败"; return 2; }
    sh /tmp/acme.sh --install --home "$ACME_HOME" --accountemail "admin@$PRIMARY" >>"$LOG" 2>&1 \
      || { log "  ✘ acme.sh 安装失败"; return 2; }
  fi
  [ -x "$ACME_HOME/acme.sh" ] || return 2
  local DOMARGS="" d
  for d in $DOMS; do DOMARGS="$DOMARGS -d $d"; done
  log "  acme.sh 文件验证签发中 (webroot=$WEBROOT, 域名: $DOMS) ..."
  "$ACME_HOME/acme.sh" --set-default-ca --server letsencrypt >>"$LOG" 2>&1
  "$ACME_HOME/acme.sh" --issue --home "$ACME_HOME" --webroot "$WEBROOT" $DOMARGS \
    --keylength ec-256 --force >>"$LOG" 2>&1 \
    || "$ACME_HOME/acme.sh" --issue --home "$ACME_HOME" --webroot "$WEBROOT" $DOMARGS >>"$LOG" 2>&1 \
    || { log "  ✘ acme.sh 签发失败(见 https.log)"; tail -25 "$LOG" | sed 's/^/    /'; return 1; }
  mkdir -p "$CERT_DIR"
  # ec 证书优先, 失败退回 rsa
  local D1
  D1=$(echo $DOMS | awk '{print $1}')
  if "$ACME_HOME/acme.sh" --install-cert --home "$ACME_HOME" -d "$D1" --ecc \
       --fullchain-file "$CERT" --key-file "$KEY" --reloadcmd "nginx -t && systemctl reload nginx" >>"$LOG" 2>&1 \
     || "$ACME_HOME/acme.sh" --install-cert --home "$ACME_HOME" -d "$D1" \
       --fullchain-file "$CERT" --key-file "$KEY" --reloadcmd "nginx -t && systemctl reload nginx" >>"$LOG" 2>&1; then
    chmod 600 "$KEY"; chmod 644 "$CERT"
    log "  ✔ acme.sh 证书已安装到 $CERT_DIR"
    return 0
  fi
  log "  ✘ acme.sh 证书安装失败"
  return 1
}

# ---------------- 模式分发 ----------------
case "$MODE" in
check)
  echo "===== 干跑自检 (不签发证书, 不改动系统) ====="
  echo "域名:        $DOMS"
  echo "主域名:      $PRIMARY"
  echo "FLA 端口:    $FPORT (deploy/.env → 反代目标)"
  echo "文件验证根:  $CHAL"
  if [ -f "$CERT" ] && cert_fresh; then echo "现有证书:    可复用(覆盖全部域名, 30 天内不到期)"
  elif [ -f "$CERT" ]; then echo "现有证书:    存在但不满足(域名不全/临期/自签) → 将重签"
  else echo "现有证书:    无 → 将新签发"; fi
  echo "-- DNS --"; dns_check; echo "   (异常数: $?)"
  echo "-- 80/443 --"
  command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -E ':(80|443)\s' | sed 's/^/   /' || echo "   (无 ss 命令)"
  echo "-- 文件验证通道 --"
  if [ "$(id -u)" = "0" ]; then file_check; else echo "   (需 root 才能写测试文件, 跳过)"; fi
  exit 0 ;;

status)
  echo "===== FLA HTTPS 状态 ====="
  echo "域名:      $DOMS"
  echo "反代目标:  127.0.0.1:$FPORT"
  if [ -s "$CERT" ]; then
    openssl x509 -in "$CERT" -noout -subject -issuer -enddate 2>/dev/null | sed 's/^/证书:      /'
    openssl x509 -in "$CERT" -noout -text 2>/dev/null | grep -A1 'Alternative' | tail -1 | tr -s ' ' | sed 's/^/SAN:      /'
    openssl x509 -in "$CERT" -noout -issuer 2>/dev/null | grep -q 'FLA Edge' \
      && echo "⚠ 当前为自签兜底证书 → sudo bash https.sh $DOMS 签发正式证书"
    LEFT=$(( ( $(date -d "$(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)" +%s) - $(date +%s) ) / 86400 ))
    echo "剩余天数:  ${LEFT} 天"
  else
    echo "证书:      未签发"
  fi
  echo "自动续期:  $(crontab -l 2>/dev/null | grep -q fla-cert-renew && echo '已配置(cron)' || echo '未配置')"
  systemctl list-timers 2>/dev/null | grep -q fla-cert-renew && echo "           已配置(systemd timer)"
  echo "签发工具:  $(command -v certbot >/dev/null 2>&1 && echo certbot || ([ -x "$ACME_HOME/acme.sh" ] && echo acme.sh || echo '未安装'))"
  bash "$SCRIPT_DIR/edge.sh" status
  exit 0 ;;

renew)
  [ "$(id -u)" = "0" ] || { echo "✘ 请用 sudo 运行"; exit 1; }
  log "===== 手动续期检查 ====="
  if command -v certbot >/dev/null 2>&1; then
    certbot renew --deploy-hook "$HOOK" >>"$LOG" 2>&1 && log "✔ certbot 续期检查完成(未到期自动跳过)" || { log "✘ 续期失败, 见 https.log"; tail -20 "$LOG"; exit 1; }
  elif [ -x "$ACME_HOME/acme.sh" ]; then
    "$ACME_HOME/acme.sh" --cron --home "$ACME_HOME" >>"$LOG" 2>&1 && log "✔ acme.sh 续期检查完成" || { log "✘ 续期失败"; exit 1; }
  else
    log "✘ 未安装 certbot / acme.sh, 先运行: sudo bash https.sh $DOMS"; exit 1
  fi
  exit 0 ;;

remove)
  [ "$(id -u)" = "0" ] || { echo "✘ 请用 sudo 运行"; exit 1; }
  ( crontab -l 2>/dev/null | grep -v 'fla-cert-renew' ) | crontab - 2>/dev/null
  systemctl disable --now fla-cert-renew.timer >/dev/null 2>&1
  rm -f /etc/systemd/system/fla-cert-renew.{service,timer} "$HOOK"
  systemctl daemon-reload >/dev/null 2>&1
  echo "✔ 已移除自动续期任务 (证书文件保留在 $CERT_DIR)"
  exit 0 ;;
esac

# ---------------- 正式流程 ----------------
[ "$(id -u)" = "0" ] || { echo "✘ 请用 sudo 运行 (只看自检: bash https.sh --check)"; exit 1; }
: >"$LOG" 2>/dev/null
log "===== FLA HTTPS 文件验证签发 | 域名: $DOMS | 反代 → 127.0.0.1:$FPORT ====="

ensure_edge || exit 1

dns_check; NBD=$?
if [ "$NBD" -gt 0 ]; then
  if [ "$ASSUME_YES" != "1" ] && [ -t 0 ]; then
    read -p "有 $NBD 个域名解析异常, 仍然继续? [y/N] " A
    [ "$A" = "y" ] || { log "已取消 (改好 DNS 后重跑: sudo bash https.sh $DOMS)"; exit 1; }
  else
    log "  (非交互模式: 继续尝试, 解析异常的域名会签发失败)"
  fi
fi

if [ "$FORCE" != "1" ] && cert_fresh; then
  log "✔ 证书已存在、覆盖全部域名且 30 天内不到期 → 复用, 不重签"
else
  log ">> 文件验证通道自检 ..."
  if file_check; then
    :
  else
    log "  ⚠ 自检未通过 — 仍然尝试签发(有时是本机回环解析问题), 若失败请看上面的排查清单"
  fi
  issue_certbot; RC=$?
  if [ "$RC" = "2" ]; then
    log "  certbot 不可用, 改用 acme.sh (同样是文件验证) ..."
    issue_acmesh; RC=$?
  fi
  [ "$RC" = "0" ] || { log "✘ 证书签发失败 — HTTP 访问不受影响, 修好后重跑: sudo bash https.sh $DOMS"; exit 1; }
fi

write_deploy_hook
write_cron

# 公开访问地址: 微软 Office 在线放映要求 "域名 + 80/443" 的直链
if cert_fresh; then
  set_env PUBLIC_BASE_URL "https://$PRIMARY"
  log "✔ 已写入 deploy/.env: PUBLIC_BASE_URL=https://$PRIMARY"
else
  set_env PUBLIC_BASE_URL "http://$PRIMARY"
  log "⚠ 证书未就绪, 先按 http://$PRIMARY 配置公开访问地址"
fi
# 让容器读到新的 PUBLIC_BASE_URL
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx fla; then
  CF=$(cat deploy/.compose_file 2>/dev/null); PROF=$(cat deploy/.compose_profile 2>/dev/null)
  DC="docker compose"; docker compose version >/dev/null 2>&1 || DC="docker-compose"
  if [ -n "$CF" ] && [ -f "$CF" ]; then
    log ">> 重建 app 容器以应用公开访问地址 ..."
    $DC ${PROF:+--profile $PROF} -f "$CF" up -d --no-deps --force-recreate app >>"$LOG" 2>&1 \
      || docker restart fla >>"$LOG" 2>&1
  fi
fi

echo ""
log "================================================"
log "  ✔ HTTPS 就绪 (文件验证 HTTP-01, 签发全程未停机)"
for d in $DOMS; do log "     https://$d   →   127.0.0.1:$FPORT"; done
log "  证书:      $CERT"
log "  自动续期:  每天 03:17 / 15:17 (手动: sudo bash https.sh --renew)"
log "  状态查看:  sudo bash https.sh --status"
log "  微软放映:  需域名+443, 现已满足 → 课件库「复制公开直链」即为 https 直链"
log "================================================"
exit 0
