#!/bin/bash
# ================================================================
#  FLA 边缘网关 (edge nginx) — 占用 80/443, 接管所有域名指向
#  ----------------------------------------------------------------
#  作用:
#   1. 在宿主机安装/复用 nginx, 让它独占 80 与 443
#   2. server_name _ + default_server  →  任何解析到本机的域名/IP 都落到 FLA
#   3. 反向代理到 FLA 应用端口 (默认 8306, 读 deploy/.env 的 PORT)
#   4. 预置 /.well-known/acme-challenge/ 文件验证目录 (HTTP-01 签发证书用,
#      签发/续期期间 nginx 不需要停机)
#   5. 还没有正式证书时, 自动生成自签证书兜底 → 443 立即可用
#
#  用法:
#    sudo bash edge.sh setup   [域名...]   安装/更新边缘网关(默认动作)
#    sudo bash edge.sh status              查看 80/443 监听与反代目标
#    sudo bash edge.sh reload              校验配置并重载
#    sudo bash edge.sh remove              移除 FLA 边缘网关配置(不卸载 nginx)
#    sudo bash edge.sh doctor              诊断"打开是 Welcome to nginx!/502/证书告警"
#    sudo bash edge.sh fix                 doctor + 自动修复(中和发行版默认站点后重载)
#    sudo bash edge.sh --port 8306 setup   手动指定 FLA 端口
#
#  与其他脚本的关系:
#    install.sh  会自动调用  edge.sh setup  +  https.sh(文件验证签发证书)
#    https.sh    只负责证书, 配置由本脚本生成(证书签发后自动 reload)
# ================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
LOG="${FLA_EDGE_LOG:-$SCRIPT_DIR/edge.log}"

CONF="/etc/nginx/conf.d/fla-edge.conf"
CERT_DIR="/etc/fla/ssl"
CERT="$CERT_DIR/fullchain.pem"
KEY="$CERT_DIR/privkey.pem"
WEBROOT="/var/www/fla-acme"
STATE="/etc/fla/edge.state"          # 记录已配置域名, 供 https.sh / run.sh 复用

# ---------- 参数 ----------
ACTION="setup"; DOMS=""; PORT_OVERRIDE=""
NO_CATCHALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    setup|status|reload|remove|doctor|fix|no-catchall|-h|--help) ACTION="${1#-}"; [ "$ACTION" = "help" ] && ACTION="--help"; shift ;;
    --port) PORT_OVERRIDE="${2:-}"; shift 2 ;;
    --domain|--domains) DOMS="$DOMS ${2:-}"; shift 2 ;;
    --no-catchall|--only-domains|--strict-domains) NO_CATCHALL=1; shift ;;
    --yes|-y) shift ;;
    -*) echo "未知参数: $1"; exit 1 ;;
    *) DOMS="$DOMS $1"; shift ;;
  esac
done
[ "$ACTION" = "--help" ] && { grep '^#' "$0" | sed 's/^# \{0,2\}//'; exit 0; }

DOMS=$(echo "$DOMS" | tr ',' ' '); DOMS=$(echo $DOMS)

log(){ local L="[$(date '+%H:%M:%S')] $*"; echo "$L"; echo "$L" >>"$LOG" 2>/dev/null; }
die(){ log "✘ $*"; exit 1; }
try(){ "$@" >>"$LOG" 2>&1; }

# ---------- 读取 FLA 端口与历史域名 ----------
fla_port(){
  local P=""
  [ -n "$PORT_OVERRIDE" ] && P="$PORT_OVERRIDE"
  [ -z "$P" ] && P=$(grep -E '^PORT=' deploy/.env 2>/dev/null | head -1 | cut -d= -f2)
  [ -z "$P" ] && P=$(grep -E '^PORT=' "$STATE" 2>/dev/null | head -1 | cut -d= -f2)
  echo "${P:-8306}"
}
saved_doms(){ grep -E '^DOMAINS=' "$STATE" 2>/dev/null | head -1 | cut -d= -f2-; }
saved_no_catchall(){ grep -E '^NO_CATCHALL=' "$STATE" 2>/dev/null | head -1 | cut -d= -f2; }

PORT=$(fla_port)
[ -z "$DOMS" ] && DOMS=$(saved_doms)
[ "$NO_CATCHALL" = "0" ] && [ "$(saved_no_catchall)" = "1" ] && NO_CATCHALL=1

save_state(){
  mkdir -p "$(dirname "$STATE")" 2>/dev/null
  {
    echo "# FLA 边缘网关状态 (由 edge.sh 维护)"
    echo "PORT=$PORT"
    echo "DOMAINS=$DOMS"
    echo "NO_CATCHALL=$NO_CATCHALL"
    echo "UPDATED=$(date '+%F %T')"
  } > "$STATE" 2>/dev/null
}

detect_os(){
  if [ -f /etc/centos-release ] || [ -f /etc/redhat-release ]; then OS="rh"; PKG="yum"
  elif [ -f /etc/debian_version ]; then OS="deb"; PKG="apt"
  else OS="other"; PKG=""; fi
  command -v dnf >/dev/null 2>&1 && PKG="dnf"
}
detect_os

nginx_ver(){ nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1; }
ver_ge(){   # ver_ge A B → A >= B ?
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}

listen_80_free(){
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltn 2>/dev/null | grep -Eq '(:80|:443)\s' && return 1
  return 0
}
who_holds_80(){
  command -v ss >/dev/null 2>&1 || { echo "(无 ss 命令)"; return; }
  ss -ltnp 2>/dev/null | grep -E ':80\s' | head -2
  docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E '0\.0\.0\.0:(80|443)->' | head -3
}

# ================================================================
#  1. 安装 nginx
# ================================================================
install_nginx(){
  if command -v nginx >/dev/null 2>&1; then
    log "✔ 宿主机已安装 nginx: $(nginx_ver)"
    return 0
  fi
  log "安装宿主机 nginx ..."
  case "$PKG" in
    dnf|yum)
      try $PKG install -y nginx
      if ! command -v nginx >/dev/null 2>&1; then
        # CentOS 7/8 需要 EPEL
        try $PKG install -y epel-release
        try $PKG install -y nginx
      fi
      ;;
    apt)
      try apt-get update
      try apt-get install -y --no-install-recommends nginx
      ;;
  esac
  command -v nginx >/dev/null 2>&1 || return 1
  log "✔ nginx 安装完成: $(nginx_ver)"
  return 0
}

# ================================================================
#  2. 让开 80/443: 停用发行版默认站点 + 检查冲突
#     ★ 这一步是"打开却是 Welcome to nginx!"的根因所在 ★
# ================================================================
DISABLED_DIR="/etc/nginx/fla-disabled"
MANIFEST="$DISABLED_DIR/manifest"
NGINX_MAIN="/etc/nginx/nginx.conf"
DEFAULT_OK=1                       # 1 = 可以生成 catch-all default_server

# 把发行版默认站点移出 include 范围。
# 注意: Debian 系是 include /etc/nginx/sites-enabled/*; —— 就地改名成
# default.fla-disabled 仍然会被通配符包含进去(等于没停用), 所以必须移出目录。
disable_file(){
  local f="$1" base
  [ -e "$f" ] || return 1
  mkdir -p "$DISABLED_DIR" 2>/dev/null
  base=$(basename "$f")
  if mv -f "$f" "$DISABLED_DIR/$base" 2>/dev/null; then
    printf '%s\t%s\n' "$f" "$DISABLED_DIR/$base" >> "$MANIFEST" 2>/dev/null
    log "  已停用默认站点: $f → $DISABLED_DIR/$base (remove 时自动还原)"
    return 0
  fi
  return 1
}

# RHEL/CentOS/Alma/Rocky/Fedora 把默认站点【直接写在 nginx.conf 里】:
#     server { listen 80 default_server; server_name _; root /usr/share/nginx/html; ... }
# 它会造成两件事: ① 和我们的 default_server 冲突 → nginx -t 报
# "duplicate default server" → 配置根本没生效; ② 即便不冲突也用欢迎页接管所有域名。
# 这里把那个 server 块整段注释掉(先备份, 幂等可重跑)。
neutralize_stock_default(){
  [ -f "$NGINX_MAIN" ] || return 0
  grep -Eq 'listen[^;]*default_server|/usr/share/nginx/html' "$NGINX_MAIN" || return 0
  if grep -q '^# \[fla-disabled\]' "$NGINX_MAIN" 2>/dev/null; then
    log "  nginx.conf 里的默认站点已中和过(跳过)"
    return 0
  fi
  [ -f "$NGINX_MAIN.fla-bak" ] || cp -a "$NGINX_MAIN" "$NGINX_MAIN.fla-bak" 2>/dev/null
  awk '
    BEGIN{ inblk=0; depth=0; n=0 }
    {
      line=$0
      if (!inblk && line ~ /^[[:space:]]*server[[:space:]]*\{/) { inblk=1; depth=0; n=0 }
      if (inblk) {
        buf[++n]=line
        t=line; sub(/#.*/,"",t); o=gsub(/\{/,"",t)
        t=line; sub(/#.*/,"",t); c=gsub(/\}/,"",t)
        depth += o - c
        if (depth <= 0) {
          hit=0
          for (i=1;i<=n;i++)
            if (buf[i] ~ /listen[^;]*default_server/ || buf[i] ~ /\/usr\/share\/nginx\/html/) hit=1
          for (i=1;i<=n;i++) { if (hit) print "# [fla-disabled] " buf[i]; else print buf[i] }
          if (hit) flagged=1
          inblk=0; n=0; delete buf
          next
        }
        next
      }
      print line
    }
    END{ exit (flagged ? 0 : 3) }
  ' "$NGINX_MAIN" > "$NGINX_MAIN.fla-new"
  local rc=$?
  if [ "$rc" = "0" ] && [ -s "$NGINX_MAIN.fla-new" ]; then
    mv -f "$NGINX_MAIN.fla-new" "$NGINX_MAIN"
    log "  ✔ 已注释掉 nginx.conf 内置的默认站点(欢迎页), 备份: $NGINX_MAIN.fla-bak"
  else
    rm -f "$NGINX_MAIN.fla-new"
    [ "$rc" = "3" ] && log "  (nginx.conf 里没有内置默认站点)" \
                    || log "  ⚠ 中和 nginx.conf 默认站点失败, 稍后 doctor 会给出手工命令"
  fi
}

# 还有谁在声明 default_server? 有就不生成我们的 catch-all, 保证 nginx -t 一定通过
detect_default_conflicts(){
  DEFAULT_OK=1
  local f
  for f in "$NGINX_MAIN" /etc/nginx/conf.d/*.conf /etc/nginx/sites-enabled/*; do
    [ -f "$f" ] || continue
    [ "$f" = "$CONF" ] && continue
    if grep -Ev '^[[:space:]]*#' "$f" 2>/dev/null | grep -Eq 'listen[^;]*default_server'; then
      log "  ⚠ $f 里已有 default_server 声明"
      log "    → 本次【不生成】catch-all default_server(否则 nginx -t 会因 duplicate default server 失败)"
      log "    → 你的域名仍然正常反代(按 Host 精确匹配); 想接管其余域名请处理该文件后重跑"
      DEFAULT_OK=0
    fi
  done
}

free_ports(){
  local f moved=0
  # 清理历史遗留的就地改名残留: Debian include /etc/nginx/sites-enabled/*; 仍会加载它们!
  for f in /etc/nginx/conf.d/*.fla-disabled /etc/nginx/sites-enabled/*.fla-disabled; do
    [ -e "$f" ] || [ -L "$f" ] || continue
    disable_file "$f" && moved=$((moved+1))
  done
  # 发行版默认站点(移出 include 目录, 不删)
  for f in /etc/nginx/conf.d/default.conf /etc/nginx/sites-enabled/default \
           /etc/nginx/sites-enabled/000-default.conf /etc/nginx/sites-enabled/*default* \
           /etc/nginx/default.d/*.conf; do
    [ -e "$f" ] || continue
    if grep -Eq 'listen[^;]*(default_server|80|443)|/usr/share/nginx/html' "$f" 2>/dev/null; then
      disable_file "$f" && moved=$((moved+1))
    fi
  done
  neutralize_stock_default
  detect_default_conflicts
  [ "$moved" = "0" ] && log "  (conf.d/sites-enabled 里没有需要停用的默认站点)"

  # 其他进程占用 80/443 (apache / 别的 docker 容器)
  local holders
  holders=$(who_holds_80)
  if systemctl is-active nginx >/dev/null 2>&1; then
    : # 我们自己的 nginx, 正常
  elif [ -n "$holders" ]; then
    log "  ⚠ 80/443 目前被其他程序占用:"
    echo "$holders" | sed 's/^/      /' | tee -a "$LOG"
    if systemctl is-active httpd >/dev/null 2>&1 || systemctl is-active apache2 >/dev/null 2>&1; then
      log "  检测到 Apache(httpd/apache2) 正在占用 80/443 → 停用并禁止自启(可用 systemctl start httpd 恢复)"
      try systemctl stop httpd; try systemctl disable httpd
      try systemctl stop apache2; try systemctl disable apache2
    fi
    # docker 容器占用: 只提示, 不擅自删除用户的容器
    docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E '0\.0\.0\.0:(80|443)->' | while read -r line; do
      log "  ⚠ 容器占用 80/443: $line"
      log "    处理办法(任选其一): docker stop ${line%% *} / 改它的端口映射 / 让 FLA 换端口 --port 8307"
    done
  fi
}

# ================================================================
#  3. 自签证书兜底 (没有正式证书时让 443 立即可用)
# ================================================================
bootstrap_cert(){
  mkdir -p "$CERT_DIR" "$WEBROOT/.well-known/acme-challenge"
  chmod -R a+rX "$WEBROOT" 2>/dev/null
  if [ -s "$CERT" ] && [ -s "$KEY" ]; then
    log "  证书已存在: $CERT (正式证书由 https.sh 用文件验证签发后覆盖)"
    return 0
  fi
  command -v openssl >/dev/null 2>&1 || { log "  ⚠ 无 openssl, 跳过自签证书(443 暂不可用)"; return 1; }
  log "  生成自签证书兜底 (浏览器会提示不受信任, 正式证书签发后自动替换) ..."
  local SAN="DNS:localhost,IP:127.0.0.1" d ip
  for d in $DOMS; do SAN="$SAN,DNS:$d,DNS:www.$d"; done
  ip=$(curl -sf -m 6 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
  [ -n "$ip" ] && SAN="$SAN,IP:$ip"
  if openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout "$KEY.fla-selfsigned" -out "$CERT.fla-selfsigned" \
      -subj "/CN=FLA Edge/O=FLA/C=HK" -addext "subjectAltName=$SAN" >>"$LOG" 2>&1 \
     || openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout "$KEY.fla-selfsigned" -out "$CERT.fla-selfsigned" \
      -subj "/CN=FLA Edge/O=FLA/C=HK" >>"$LOG" 2>&1; then
    # 只有当前证书缺失或是自签时才覆盖(别把 Let's Encrypt 证书盖掉)
    if [ ! -s "$CERT" ] || grep -q 'FLA Edge' <(openssl x509 -in "$CERT" -noout -subject 2>/dev/null); then
      cp -f "$CERT.fla-selfsigned" "$CERT"; cp -f "$KEY.fla-selfsigned" "$KEY"
      chmod 600 "$KEY"; chmod 644 "$CERT"
      log "  ✔ 自签证书就绪: $CERT"
    fi
  else
    log "  ⚠ 自签证书生成失败(看 edge.log), 443 暂不可用; 80 不受影响"
    return 1
  fi
  return 0
}

# ================================================================
#  4. 生成 nginx 配置 (80/443 catch-all → 127.0.0.1:$PORT)
# ================================================================
gen_conf(){
  local d
  # ---- 域名列表(含 www 前缀) ----
  local NAMES=""
  for d in $DOMS; do NAMES="$NAMES $d www.$d"; done
  NAMES=$(echo $NAMES)

  # ---- 已签发证书的域名 → http 自动跳 https ----
  local MAPBODY=""
  for d in $DOMS; do
    MAPBODY="$MAPBODY
    $d            1;
    www.$d        1;"
  done

  local HAVECERT=0
  [ -s "$CERT" ] && [ -s "$KEY" ] && HAVECERT=1

  # ---- http2 写法按 nginx 版本适配 (1.25.1+ 用 http2 on;) ----
  # FLA_NO_HTTP2=1 可强制 HTTP/1.1: 跨境线路(如中国大陆)对 h2 多路复用的
  # 干扰/重置明显多于 h1.1, 浏览器整页打不开而 curl(h1.1) 正常时优先试这个
  local L443="    listen 443 ssl;" L443v6="    listen [::]:443 ssl;" HTTP2="    http2 on;"
  local V; V=$(nginx_ver)
  if [ -n "$V" ] && ! ver_ge "$V" "1.25.1"; then
    L443="    listen 443 ssl http2;"; L443v6="    listen [::]:443 ssl http2;"; HTTP2=""
  fi
  if [ "${FLA_NO_HTTP2:-0}" = "1" ]; then
    L443="    listen 443 ssl;"; L443v6="    listen [::]:443 ssl;"; HTTP2=""
    echo "  (FLA_NO_HTTP2=1: 已强制 HTTP/1.1)"
  fi

  # ---- 公共片段 ----
  # Let's Encrypt 文件验证 (HTTP-01): 必须永远能用 http 取到
  local ACME="    location ^~ /.well-known/acme-challenge/ {
        root $WEBROOT;
        default_type \"text/plain\";
        charset utf-8;
        try_files \$uri =404;
    }"
  # server 级 if 在 SERVER_REWRITE 阶段执行(早于 location 匹配),
  # 所以必须显式排除 ACME 路径, 否则续期请求会被 301 到 https 而验证失败
  local REDIR="    set \$fla_redir 0;
    if (\$fla_force_https) { set \$fla_redir 1; }
    if (\$uri ~ \"^/.well-known/acme-challenge/\") { set \$fla_redir 0; }
    if (\$http_x_forwarded_for) { set \$fla_redir 0; }
    if (\$fla_redir) { return 301 https://\$host\$request_uri; }"
  local TLSCFG="    ssl_certificate     $CERT;
    ssl_certificate_key $KEY;
    ssl_session_cache   shared:FLA_SSL:12m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    add_header Strict-Transport-Security \"max-age=31536000\" always;"

  # ---- ① 域名专用 server (按 Host 精确匹配, 不受任何 default_server 冲突影响) ----
  local DOM80="" DOM443=""
  if [ -n "$NAMES" ]; then
    DOM80="
# ---------- 域名专用: ${NAMES} → 127.0.0.1:$PORT ----------
server {
    listen 80;
    listen [::]:80;
    server_name $NAMES ~^.+\$;

$ACME

$REDIR

__PROXY__
}"
    if [ "$HAVECERT" = "1" ]; then
      DOM443="
server {
$L443
$L443v6
    server_name $NAMES ~^.+\$;
$HTTP2

$TLSCFG
    error_page 497 = @fla_plain;

$ACME

__PROXY__
}"
    fi
  fi

  # ---- ② catch-all default_server (只有确认没有别处占用 default_server 才生成,
  #        并且用户未要求仅限域名模式) ----
  local CAT80="" CAT443=""
  if [ "${DEFAULT_OK:-1}" = "1" ] && [ "${NO_CATCHALL:-0}" != "1" ]; then
    CAT80="
# ---------- 兜底: 其余任意域名/IP 也落到 FLA (不留发行版欢迎页) ----------
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

$ACME

$REDIR

__PROXY__
}"
    if [ "$HAVECERT" = "1" ]; then
      CAT443="
server {
$L443
$L443v6
    server_name _;
$HTTP2

$TLSCFG
    error_page 497 = @fla_plain;

$ACME

__PROXY__
}"
    fi
  fi

  cat > "$CONF.tmp" <<EOF
# ================================================================
#  FLA 边缘网关 — 由 edge.sh 自动生成, 请勿手工编辑
#  · 宿主机 nginx 独占 80 / 443, 反向代理到 FLA 容器
#  · 反代目标: http://127.0.0.1:$PORT   (FLA 容器对外端口)
#  · 域名: ${NAMES:-（未指定, 仅 catch-all）}
#  · 证书: $CERT
#    自签=临时兜底; 正式证书由 https.sh 以【文件验证 HTTP-01】签发后自动替换
#  · 重新生成: sudo bash edge.sh setup   诊断: sudo bash edge.sh doctor
# ================================================================

map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

# 已签发证书的域名 → http 自动跳 https (其余域名/IP 保持 http 直连, 避免证书告警)
map \$host \$fla_force_https {
    default       0;$MAPBODY
}

upstream fla_backend {
    server 127.0.0.1:$PORT max_fails=3 fail_timeout=10s;
    keepalive 32;
}
$DOM80
$DOM443
$CAT80
$CAT443
EOF

  # ---- 公共反代段 (80/443 共用) ----
  local PROXY
  PROXY=$(cat <<EOF
    client_max_body_size 2048m;                    # 大课件上传
    proxy_read_timeout   600s;
    proxy_send_timeout   600s;
    proxy_connect_timeout 15s;

    gzip on;
    gzip_min_length 1024;
    gzip_comp_level 5;
    gzip_vary on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml font/woff2;

    location / {
        proxy_pass http://fla_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection        \$connection_upgrade;
        proxy_set_header Upgrade           \$http_upgrade;      # WebSocket
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host  \$host;
        proxy_set_header X-Forwarded-Port  \$server_port;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;                                       # 音视频 Range 流式
        proxy_request_buffering off;                               # 大文件上传不先落盘
    }
    location @fla_plain {
        proxy_pass http://fla_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection        \$connection_upgrade;
        proxy_set_header Upgrade           \$http_upgrade;      # WebSocket
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host  \$host;
        proxy_set_header X-Forwarded-Port  \$server_port;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;                                       # 音视频 Range 流式
        proxy_request_buffering off;                               # 大文件上传不先落盘
    }
EOF
)
  # 用 awk 替换占位符(避免 sed 对多行/特殊字符的转义地狱)
  awk -v proxy="$PROXY" '{ if ($0 == "__PROXY__") print proxy; else print }' "$CONF.tmp" > "$CONF"
  rm -f "$CONF.tmp"
  chmod 644 "$CONF"
}

# ================================================================
#  5. 防火墙 / SELinux
# ================================================================
open_firewall(){
  if systemctl is-active firewalld >/dev/null 2>&1; then
    local need=0 p
    for p in 80 443; do
      firewall-cmd --query-port="$p/tcp" >/dev/null 2>&1 || { try firewall-cmd --permanent --add-port="$p/tcp"; need=1; }
      firewall-cmd --query-service=https >/dev/null 2>&1 || { try firewall-cmd --permanent --add-service=http; try firewall-cmd --permanent --add-service=https; need=1; }
    done
    [ "$need" = "1" ] && { try firewall-cmd --reload; log "  ✔ 防火墙已放行 80/443 (云服务器还需在控制台安全组放行)"; }
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
    try ufw allow 80/tcp; try ufw allow 443/tcp
    log "  ✔ ufw 已放行 80/443"
  fi
  # SELinux: 允许 nginx 发起对外/本机端口的反代连接, 否则 502
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null)" = "Enforcing" ]; then
    if command -v setsebool >/dev/null 2>&1; then
      try setsebool -P httpd_can_network_connect 1
      log "  ✔ SELinux: 已允许 nginx 反代 (httpd_can_network_connect=1)"
    fi
    command -v semanage >/dev/null 2>&1 && try semanage port -a -t http_port_t -p tcp "$PORT" 2>/dev/null
  fi
}

start_nginx(){
  try systemctl enable nginx
  nginx -t >>"$LOG" 2>&1 || { log "✘ nginx 配置校验失败:"; tail -20 "$LOG"; return 1; }
  if systemctl is-active nginx >/dev/null 2>&1; then
    try systemctl reload nginx || try systemctl restart nginx
  else
    try systemctl restart nginx || try systemctl start nginx
  fi
  sleep 1
  systemctl is-active nginx >/dev/null 2>&1 || { log "✘ nginx 启动失败:"; try journalctl -u nginx --no-pager -n 20; tail -20 "$LOG"; return 1; }
  return 0
}

# ================================================================
#  6. 落地验证: 真的打开是 FLA 吗? (而不是 "Welcome to nginx!")
# ================================================================
# probe <http|https> <Host 头> → 设置 PROBE_CODE / PROBE_KIND
#   PROBE_KIND: fla | welcome | badgw | dead | other
probe(){
  local sch="$1" hh="$2" k="" raw
  [ "$sch" = "https" ] && k="-k"
  raw=$(curl -s $k -m 6 -H "Host: $hh" -w '\n__C__%{http_code}' "$sch://127.0.0.1/" 2>/dev/null)
  PROBE_CODE="${raw##*__C__}"
  local body="${raw%%$'\n'__C__*}"
  PROBE_KIND=other
  case "$PROBE_CODE" in
    000|"")            PROBE_KIND=dead ;;
    502|503|504)       PROBE_KIND=badgw ;;
    200|301|302|304)
      if printf '%s' "$body" | grep -qi 'Welcome to nginx'; then PROBE_KIND=welcome
      elif printf '%s' "$body" | grep -q 'FLA\|/js/app\.js\|api/health'; then PROBE_KIND=fla
      elif [ "$PROBE_CODE" = "301" ] || [ "$PROBE_CODE" = "302" ]; then PROBE_KIND=fla   # 跳 https, 正常
      fi ;;
  esac
}

probe_report(){
  local sch="$1" hh="$2" label="$3" bad=0
  probe "$sch" "$hh"
  case "$PROBE_KIND" in
    fla)     log "  ✔ $label → FLA (HTTP $PROBE_CODE)" ;;
    welcome) log "  ✘ $label → 仍是发行版欢迎页 \"Welcome to nginx!\" (HTTP $PROBE_CODE)"; bad=1 ;;
    badgw)   log "  ✘ $label → HTTP $PROBE_CODE (nginx 通了, 但后端 FLA 没响应)"; bad=2 ;;
    dead)    log "  ✘ $label → 连不上(nginx 没在监听 $sch 的端口?)"; bad=3 ;;
    *)       log "  ? $label → HTTP $PROBE_CODE (无法识别的响应, 见下)"; bad=4 ;;
  esac
  return $bad
}

verify_edge(){
  local d bad=0
  log "-- 落地验证(本机自测, 不影响外网) --"
  if curl -sf -m 5 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    log "  ✔ FLA 容器后端 127.0.0.1:$PORT/api/health 正常"
  else
    log "  ✘ FLA 容器后端 127.0.0.1:$PORT 无响应 → 反代会是 502"
    log "    查: docker ps --filter name=fla · 起: sudo bash run.sh start · 装: sudo bash install.sh"
    bad=2
  fi
  if [ -n "$DOMS" ]; then
    for d in $DOMS; do
      probe_report http  "$d" "http://$d/"  || { [ $? -gt $bad ] && bad=$?; }
      [ -s "$CERT" ] && { probe_report https "$d" "https://$d/" || { [ $? -gt $bad ] && bad=$?; }; }
    done
  fi
  probe_report http "any.example.com" "http://<任意其它域名>/" || { [ $? -gt $bad ] && bad=$?; }
  case "$bad" in
    1) log "  → 补救: sudo bash edge.sh fix   (自动中和发行版默认站点并重载)" ;;
    2) log "  → 补救: sudo bash run.sh status 看容器; 端口不符就 sudo bash edge.sh --port <FLA端口> setup" ;;
    3) log "  → 补救: systemctl status nginx; 看日志 tail -50 $LOG" ;;
  esac
  return $bad
}

# ================================================================
#  7. doctor: 把"为什么打开不是 FLA"一次查清
# ================================================================
do_doctor(){
  local f d
  echo "================ FLA 边缘网关诊断 ================"
  echo "[1] nginx 本体"
  if command -v nginx >/dev/null 2>&1; then
    echo "    ✔ 已安装: $(nginx -v 2>&1)"
    systemctl is-active nginx >/dev/null 2>&1 && echo "    ✔ 服务运行中" || echo "    ✘ 服务未运行 → systemctl start nginx"
    systemctl is-enabled nginx >/dev/null 2>&1 && echo "    ✔ 开机自启" || echo "    ⚠ 未设开机自启 → systemctl enable nginx"
  else
    echo "    ✘ 宿主机没有 nginx → sudo bash edge.sh setup $DOMS"
  fi

  echo "[2] 配置语法 nginx -t"
  if command -v nginx >/dev/null 2>&1; then
    if nginx -t 2>/tmp/fla-nginx-t.$$; then
      echo "    ✔ 通过"; sed 's/^/      /' /tmp/fla-nginx-t.$$
    else
      echo "    ✘ 失败(这就是网关没生效的原因):"; sed 's/^/      /' /tmp/fla-nginx-t.$$
      nginx -t 2>&1 | grep -i 'duplicate default server' >/dev/null && \
        echo "      → 典型的 default_server 冲突: sudo bash edge.sh fix 可自动中和发行版默认站点"
    fi
    rm -f /tmp/fla-nginx-t.$$
  fi

  echo "[3] 谁在监听 80/443"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | grep -E ':(80|443)\s' | sed 's/^/    /' || echo "    ✘ 没人监听 80/443"
  else
    echo "    (无 ss 命令) netstat -ltnp | grep -E ':80|:443'"
  fi
  docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E '0\.0\.0\.0:(80|443)->' | sed 's/^/    ⚠ 容器占用: /'

  echo "[4] FLA 网关配置 $CONF"
  if [ -f "$CONF" ]; then
    echo "    ✔ 存在"
    grep -E '^\s*(listen|server_name|proxy_pass|upstream|server 127)' "$CONF" | sed 's/^/      /' | head -14
  else
    echo "    ✘ 不存在 → sudo bash edge.sh setup ${DOMS:-你的域名}"
  fi

  echo "[5] 还有谁在抢 default_server / 提供欢迎页"
  local found=0
  for f in "$NGINX_MAIN" /etc/nginx/conf.d/*.conf /etc/nginx/sites-enabled/*; do
    [ -f "$f" ] || continue
    [ "$f" = "$CONF" ] && continue
    if grep -Ev '^[[:space:]]*#' "$f" 2>/dev/null | grep -nE 'listen[^;]*default_server|/usr/share/nginx/html' >/tmp/fla-ds.$$; then
      found=1
      echo "    ⚠ $f:"; sed 's/^/        行/' /tmp/fla-ds.$$
    fi
  done
  rm -f /tmp/fla-ds.$$
  [ "$found" = "0" ] && echo "    ✔ 没有冲突(发行版默认站点已停用/中和)"
  if [ -f "$NGINX_MAIN" ] && grep -q '^# \[fla-disabled\]' "$NGINX_MAIN" 2>/dev/null; then
    echo "    ✔ nginx.conf 内置欢迎页站点已注释 (备份: $NGINX_MAIN.fla-bak)"
  fi
  ls -1 "$DISABLED_DIR" 2>/dev/null | grep -v '^manifest$' | sed 's/^/    已停用: /'

  echo "[6] FLA 容器与后端"
  docker ps --filter name=fla --format '    {{.Names}}  {{.Status}}  {{.Ports}}' 2>/dev/null
  echo "    deploy/.env 里的 PORT: $(grep -E '^PORT=' deploy/.env 2>/dev/null | cut -d= -f2 || echo '(未设置, 默认 8306)')"
  echo "    本脚本反代目标端口:    $PORT"
  curl -sf -m 4 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 \
    && echo "    ✔ 127.0.0.1:$PORT/api/health 正常" \
    || echo "    ✘ 127.0.0.1:$PORT/api/health 无响应 → sudo bash run.sh status"

  echo "[7] 证书"
  if [ -s "$CERT" ] && command -v openssl >/dev/null 2>&1; then
    openssl x509 -in "$CERT" -noout -subject -enddate 2>/dev/null | sed 's/^/    /'
    openssl x509 -in "$CERT" -noout -issuer 2>/dev/null | grep -q 'FLA Edge' \
      && echo "    ⚠ 自签兜底证书(浏览器会告警) → sudo bash https.sh ${DOMS:-域名}" \
      || echo "    ✔ 正式证书(Let's Encrypt)"
  else
    echo "    (无证书 → 443 不可用) sudo bash https.sh ${DOMS:-域名}"
  fi

  echo "[8] 实际访问结果"
  verify_edge
  echo "=================================================="
}

# fix = 中和发行版默认站点 + 重新生成配置 + 重载 + 验证
do_fix(){
  [ "$(id -u)" = "0" ] || die "请用 root 运行: sudo bash edge.sh fix"
  : >>"$LOG" 2>/dev/null
  log "===== FLA 边缘网关: 自动修复 ====="
  install_nginx || die "nginx 安装失败"
  free_ports
  bootstrap_cert
  gen_conf
  start_nginx || { log "✘ nginx 仍未通过校验, 下面是 nginx -t 的输出:"; nginx -t 2>&1 | sed 's/^/    /' | tee -a "$LOG"; exit 1; }
  save_state
  verify_edge
  log "修复流程结束(如仍是欢迎页, 看上面 [5] 列出的文件手工处理)"
}

# ================================================================
#  动作
# ================================================================
do_setup(){
  [ "$(id -u)" = "0" ] || die "请用 root 运行: sudo bash edge.sh setup"
  : >"$LOG" 2>/dev/null
  log "===== FLA 边缘网关 (80/443 → 127.0.0.1:$PORT) ====="
  log "接管域名: ${DOMS:-（未指定: 所有域名/IP 均走 catch-all）}"

  install_nginx || die "nginx 安装失败(网络问题?) — 看 $LOG; 或手动安装后重跑: sudo bash edge.sh setup"
  free_ports
  bootstrap_cert
  gen_conf
  open_firewall
  start_nginx || die "nginx 启动失败 — 看 $LOG 与 journalctl -u nginx"
  save_state

  local L80=0 L443=0
  command -v ss >/dev/null 2>&1 && { ss -ltn 2>/dev/null | grep -q ':80 ' && L80=1; ss -ltn 2>/dev/null | grep -q ':443 ' && L443=1; }
  echo ""
  log "================================================"
  [ "$L80" = "1" ]  && log "  ✔ 80  已监听" || log "  ✘ 80  未监听!"
  [ "$L443" = "1" ] && log "  ✔ 443 已监听" || log "  ⚠ 443 未监听 (证书缺失?)"
  log "  → 反向代理目标: http://127.0.0.1:$PORT (FLA 容器对外端口)"
  for d in $DOMS; do log "    http://$d  /  https://$d"; done
  verify_edge
  log "  正式证书(文件验证): sudo bash https.sh ${DOMS}"
  log "  一键诊断: sudo bash edge.sh doctor"
  log "================================================"
}

do_status(){
  echo "===== FLA 边缘网关状态 ====="
  echo "配置文件:   $CONF $([ -f "$CONF" ] && echo '(存在)' || echo '(未生成)')"
  echo "反代目标:   127.0.0.1:$PORT  (FLA 应用端口)"
  echo "接管域名:   ${DOMS:-（无固定域名, 全部 catch-all）}"
  echo "证书:       $CERT"
  if [ -s "$CERT" ] && command -v openssl >/dev/null 2>&1; then
    openssl x509 -in "$CERT" -noout -subject -enddate 2>/dev/null | sed 's/^/            /'
    if openssl x509 -in "$CERT" -noout -issuer 2>/dev/null | grep -q 'FLA Edge'; then
      echo "            ⚠ 当前是自签兜底证书 → sudo bash https.sh 域名 签发正式证书"
    else
      echo "            ✔ 正式证书 (Let's Encrypt)"
    fi
  else
    echo "            (无)"
  fi
  echo "文件验证根: $WEBROOT/.well-known/acme-challenge/"
  echo "-- 80/443 监听 --"
  command -v ss >/dev/null 2>&1 && ss -ltnp 2>/dev/null | grep -E ':(80|443)\s' | sed 's/^/  /' || echo "  (无 ss)"
  echo "-- 连通性 --"
  curl -sf -m 4 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && echo "  ✔ FLA 应用 127.0.0.1:$PORT" || echo "  ✘ FLA 应用 127.0.0.1:$PORT 无响应"
  curl -sf -m 4 -H 'Host: test.local' http://127.0.0.1/api/health >/dev/null 2>&1 && echo "  ✔ nginx 80 → FLA" || echo "  ✘ nginx 80 → FLA 不通"
  curl -sfk -m 4 -H 'Host: test.local' https://127.0.0.1/api/health >/dev/null 2>&1 && echo "  ✔ nginx 443 → FLA" || echo "  ✘ nginx 443 → FLA 不通"
  echo "-- 是否还是发行版欢迎页 --"
  probe http "${DOMS%% *}" 2>/dev/null || probe http "test.local"
  case "$PROBE_KIND" in
    fla)     echo "  ✔ 打开是 FLA, 不是 Welcome to nginx" ;;
    welcome) echo "  ✘ 仍是 Welcome to nginx → sudo bash edge.sh fix" ;;
    badgw)   echo "  ✘ 502/504: nginx 通了但 FLA 容器没响应 → sudo bash run.sh status" ;;
    dead)    echo "  ✘ 80 端口连不上 → systemctl status nginx" ;;
    *)       echo "  ? HTTP $PROBE_CODE → sudo bash edge.sh doctor 详查" ;;
  esac
  command -v nginx >/dev/null 2>&1 && { echo "-- nginx 版本 --"; nginx -v 2>&1 | sed 's/^/  /'; }
  echo "-- 冲突检查 --"
  local f
  for f in "$NGINX_MAIN" /etc/nginx/conf.d/*.conf /etc/nginx/sites-enabled/*; do
    [ -f "$f" ] || continue; [ "$f" = "$CONF" ] && continue
    grep -Ev '^[[:space:]]*#' "$f" 2>/dev/null | grep -qE 'listen[^;]*default_server|/usr/share/nginx/html'       && echo "  ⚠ $f 仍有 default_server / 欢迎页配置 → sudo bash edge.sh fix"
  done
}

do_remove(){
  [ "$(id -u)" = "0" ] || die "请用 root 运行"
  [ -f "$CONF" ] && { mv -f "$CONF" "$CONF.removed.$(date +%s)"; log "已移除 $CONF (备份保留)"; }
  local f orig cur
  # 还原被停用的发行版默认站点(按 manifest 精确归位)
  if [ -f "$MANIFEST" ]; then
    while IFS="$(printf '	')" read -r orig cur; do
      [ -n "$orig" ] && [ -f "$cur" ] && mv -f "$cur" "$orig" && log "已恢复默认站点: $orig"
    done < "$MANIFEST"
    rm -f "$MANIFEST"
  fi
  # 兼容老版本就地改名的做法
  for f in /etc/nginx/conf.d/*.fla-disabled /etc/nginx/sites-enabled/*.fla-disabled; do
    [ -f "$f" ] && mv -f "$f" "${f%.fla-disabled}" && log "已恢复默认站点: ${f%.fla-disabled}"
  done
  # 还原 nginx.conf(内置欢迎页站点)
  if [ -f "$NGINX_MAIN.fla-bak" ]; then
    cp -f "$NGINX_MAIN.fla-bak" "$NGINX_MAIN" && log "已还原 $NGINX_MAIN (内置默认站点恢复)"
  fi
  command -v nginx >/dev/null 2>&1 && nginx -t >>"$LOG" 2>&1 && { try systemctl reload nginx; log "nginx 已重载"; }
}

do_no_catchall(){
  [ "$(id -u)" = "0" ] || die "请用 root 运行: sudo bash edge.sh no-catchall"
  : >>"$LOG" 2>/dev/null
  log "===== FLA 边缘网关: 移除 80/443 catch-all 兜底 ====="
  log "保留反代域名: ${DOMS:-（未指定域名! 请先执行 sudo bash edge.sh setup 域名）}"
  [ -z "$DOMS" ] && die "当前未配置任何域名，不能移除默认站点。请先指定域名: sudo bash edge.sh setup 域名1 域名2"
  NO_CATCHALL=1
  DEFAULT_OK=0
  gen_conf
  save_state
  start_nginx || systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null
  log "✔ 已更新 Nginx 配置 ($CONF): 彻底移除了 default_server 与 catch-all 监听"
  log "✔ FLA 现仅在反代域名 (${DOMS}) 及直接访问 8306 端口时可用"
  log "✔ 服务器 80/443 默认访问及其他域名已完全让出，供您的其他项目自由使用！"
}

case "$ACTION" in
  setup)  do_setup ;;
  status) do_status ;;
  reload) [ "$(id -u)" = "0" ] || die "请用 root 运行"
          PORT=$(fla_port); gen_conf; start_nginx && log "✔ 配置已重新生成并重载 (→127.0.0.1:$PORT)" ;;
  remove) do_remove ;;
  doctor) do_doctor ;;
  fix)    do_fix ;;
  no-catchall) do_no_catchall ;;
  *)      grep '^#' "$0" | sed 's/^# \{0,2\}//' ;;
esac
