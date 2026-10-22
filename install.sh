#!/bin/bash
# ================================================================
#  FLA (FYX Lesson All) 一键部署脚本 v1.27
#  用法:  sudo bash install.sh [选项]
#    --port N          FLA 应用端口 (默认 8306, 被占自动顺延)
#    --domain 域名...  对外域名(可多个, 空格分隔); 边缘 nginx 会为其签发 SSL 证书
#                      不填 = 沿用上次的, 再没有就用 t.clrv.top t.fyx.best
#    --password X      管理员 admin 初始密码 (默认交互输入或随机)
#    --lite            精简模式 (不用 OnlyOffice, 无动画放映, 省内存)
#    --foreground      不转入 screen, 就在前台执行(调试用)
#    --no-edge         不装宿主机边缘 nginx (FLA 只能 IP:端口 访问)
#    --no-ssl          只接管 80/443, 不签发证书 (纯 http)
#    --https [域名...] 旧写法, 等价于 --domain (v1.27 起 SSL 默认就会做)
#
#  v1.27 网络架构:
#    宿主机 nginx 独占 80 + 443, 用 server_name _ 的 default_server 接管【所有】
#    指向本机的域名/IP, 统一反代到 127.0.0.1:8306 (FLA 应用端口);
#    SSL 证书用【文件验证 HTTP-01 / webroot】自动签发 + 自动续期, 全程不停机,
#    不需要停掉 nginx 去抢 80。微软 Office 在线放映要求的"域名+80/443 公开直链"
#    因此自动满足。相关脚本: edge.sh(网关) / https.sh(证书)
#
#  OnlyOffice 来源:
#    (自动)            默认不部署(Office 走微软在线渲染); 需要时 --ds-external 复用
#                      本机已有的 OnlyOffice 容器, 或 --ds-new 自带一套
#    --ds-new          强制使用自带的 OnlyOffice (不复用已有容器)
#    --ds-external     强制复用已有的 OnlyOffice 容器(含已停止的)
#    --ds-port N       已有 OnlyOffice 对外的端口 (探测失败时手动指定)
#    --ds-secret X     已有 OnlyOffice 的 JWT 密钥 (读取失败时手动指定)
#
#  特性:
#   · 自动转入 screen 会话后台执行(SSH 断开不影响), 无 screen 则用 tmux/nohup
#   · 智能重建: 重复运行自动清理旧 FLA 容器(数据卷保留, 用户/课件不丢);
#     与 FLA 无关的同名容器会被改名保留而不是删除
#   · 三级自愈: nginx 不通 → 自动重启 nginx → 仍不通切换【直连模式】(app 直接
#     发布端口, 绕过 nginx) → 仍不通重启 docker 修复防火墙规则; 并自动把
#     nginx 日志写入 install.log 供排查
#   · 边缘网关(第8步): 宿主机 nginx 占 80/443 接管所有域名 → 反代 127.0.0.1:$PORT
#   · SSL(第9步): Let's Encrypt 文件验证签发 + 每天两次自动续期(不停机)
#   · 防火墙安全顺序: 统一放行端口→一次 reload→(若 reload 过)先重启 docker 恢复
#     iptables 转发规则, 再创建容器 (firewalld reload 会清空 docker 规则!)
#   · 开机自启: 容器 restart=unless-stopped + docker 服务 enabled
#  日常管理(启动/停止/日志等)请使用: sudo bash run.sh
#  日志: install.log (本目录)
# ================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
LOG="install.log"
BRAND="FLA (FYX Lesson All)"

# ---------- 参数 ----------
PORT=""; ADMIN_PW=""; MODE="auto"; DS_MODE="auto"; DS_PORT=""; DS_SECRET=""
SSL_MODE=1; EDGE_MODE=1; HTTPS_DOMS=""; FLA_DEFAULT_DOMS="t.clrv.top t.fyx.best"
INNER=0; FOREGROUND=0
ORIG_ARGS=("$@")   # 原始参数: screen/tmux/nohup 转发用(解析循环会 shift 掉 $@)
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --password) ADMIN_PW="$2"; shift 2 ;;
    --lite) MODE="lite"; shift ;;
    --full) MODE="full"; shift ;;
    --domain|--domains) shift
              while [ $# -gt 0 ] && [ "${1:0:1}" != "-" ]; do HTTPS_DOMS="$HTTPS_DOMS $1"; shift; done ;;
    --ds-new) DS_MODE="new"; shift ;;
    --ds-external) DS_MODE="external"; shift ;;
    --ds-port) DS_PORT="$2"; shift 2 ;;
    --ds-secret) DS_SECRET="$2"; shift 2 ;;
    --https) shift
              while [ $# -gt 0 ] && [ "${1:0:1}" != "-" ]; do HTTPS_DOMS="$HTTPS_DOMS $1"; shift; done ;;
    --no-ssl|--no-https) SSL_MODE=0; shift ;;
    --no-edge) EDGE_MODE=0; SSL_MODE=0; shift ;;
    --foreground) FOREGROUND=1; shift ;;
    --inner) INNER=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,2\}//'; exit 0 ;;
    *) echo "未知参数: $1 (用法见 --help)"; exit 1 ;;
  esac
done
HTTPS_DOMS=$(echo "$HTTPS_DOMS" | tr ',' ' '); HTTPS_DOMS=$(echo $HTTPS_DOMS)

log(){ echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }
die(){ echo; log "✘ 安装中止: $1"; log "排错建议:"; sed -n '/^#TROUBLE/,/^#END-TROUBLE/p' "$0" | sed 's/^#TROUBLE//;s/^#END-TROUBLE//;s/^# //' | tee -a "$LOG"; exit 1; }
ok(){ log "✔ $*"; }
try(){ "$@" >>"$LOG" 2>&1; }
set_env(){ if grep -q "^$1=" deploy/.env; then sed -i "s|^$1=.*|$1=$2|" deploy/.env; else echo "$1=$2" >> deploy/.env; fi; }

# 用当前密钥向 DS 发一次真实的签名转换请求: 返回0=密钥一致, 1=token错误(不一致), 2=无法测试
# ---------- v1.12: 向 OnlyOffice 容器注入开源中文字体 ----------
# 背景: 学校 PPT 常用微软雅黑/宋体/楷体(商业字体, 不能随开源包分发),
#       DS 容器里没有 → OnlyOffice 用其他字体顶替 → 字符宽度不同 → 排版错位。
# 修法: 注入同源开源字体(思源黑体/宋体 + 霞鹜文楷), 与 FLA 转换服务用同一批文件,
#       并写 fontconfig 别名让"雅黑/宋体/黑体/楷体/仿宋"优先替换到它们。
# 注: docker cp 进容器的内容在容器被删除重建后会丢失, 届时重跑 install.sh 即可。
inject_ds_fonts(){
  local CTR="$1"
  [ -n "$CTR" ] || return 0
  docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CTR" || return 0
  if docker exec "$CTR" test -f /usr/share/fonts/truetype/fla/.fla_fonts_v1 2>/dev/null; then
    log "  DS 中文字体已注入过, 跳过 (若重建过 DS 容器, 会自动重注)"
    return 0
  fi
  local FD="/var/local/fla-ds-fonts" U F NEED=0 OKN=0
  mkdir -p "$FD"
  for U in \
    "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf" \
    "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf" \
    "https://github.com/notofonts/noto-cjk/raw/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Regular.otf" \
    "https://github.com/lxgw/LxgwWenKai/releases/latest/download/LXGWWenKai-Regular.ttf" \
    "https://github.com/google/fonts/raw/main/ofl/stixtwomath/STIXTwoMath-Regular.ttf"; do
    F="$FD/$(basename "$U")"
    if [ ! -s "$F" ]; then
      log "  下载字体 $(basename "$U") ..."
      curl -sL --max-time 300 -o "$F" "$U" 2>/dev/null || true
    fi
    [ -s "$F" ] || NEED=1
  done
  if [ "$NEED" = "1" ]; then
    log "  ⚠ 部分字体下载失败(网络原因), 本轮跳过 DS 字体注入, 其他功能不受影响 (可稍后重跑)"
    return 0
  fi
  if ! docker exec "$CTR" test -d /usr/share/fonts/truetype 2>/dev/null; then
    log "  ⚠ 容器 $CTR 内无 /usr/share/fonts, 非标准 OnlyOffice 镜像, 跳过字体注入"
    return 0
  fi
  log "  注入中文字体到 OnlyOffice 容器 $CTR (重建字体索引约1-3分钟, 请勿中断)..."
  docker exec "$CTR" mkdir -p /usr/share/fonts/truetype/fla 2>/dev/null || return 0
  for F in "$FD"/*.otf "$FD"/*.ttf; do
    [ -s "$F" ] || continue
    docker cp "$F" "$CTR:/usr/share/fonts/truetype/fla/" >/dev/null 2>&1 && OKN=$((OKN+1))
  done
  if [ "$OKN" -lt 4 ]; then
    log "  ⚠ 字体拷入容器失败($OKN/5), 跳过 (可重跑 install.sh 重试)"
    return 0
  fi
  # 字体替换表: 直接复用项目自带的完整规则(雅黑/宋体/楷体/华文系/隶书/幼圆/日韩等)
  local SRC_CONF
  SRC_CONF="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/deploy/fonts/30-edu-substitutions.conf"
  if [ -s "$SRC_CONF" ] && docker exec "$CTR" test -d /etc/fonts/conf.d 2>/dev/null; then
    docker cp "$SRC_CONF" "$CTR:/etc/fonts/conf.d/30-edu-substitutions.conf" >/dev/null 2>&1 || true
  fi
  docker exec "$CTR" fc-cache -f >/dev/null 2>&1
  if docker exec "$CTR" /usr/bin/documentserver-generate-allfonts.sh >/dev/null 2>&1; then
    docker exec "$CTR" touch /usr/share/fonts/truetype/fla/.fla_fonts_v1
    ok "已注入 $OKN 个开源字体到 OnlyOffice (雅黑/黑体→思源黑, 宋体→思源宋, 楷体→霞鹜文楷, 数学符号→STIX Two Math)"
    log "    说明: 开源替代字体与微软原版并非像素级一致, 错位会大幅减少但不会100%消失"
    log "    浏览器需强刷(Ctrl+F5)或清缓存后 OnlyOffice 才会用上新字体"
  else
    log "  ⚠ documentserver-generate-allfonts.sh 执行失败, 注入未完成 (可重跑 install.sh 重试)"
  fi
}

verify_ds_secret(){
  command -v python3 >/dev/null 2>&1 || return 2
  python3 - "$DS_SCHEME" "$DS_PORT" "$1" <<'PYV' >>"$LOG" 2>&1
import sys, json, hmac, hashlib, base64, urllib.request, urllib.error
scheme, port, secret = sys.argv[1], sys.argv[2], sys.argv[3]
def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=")
body = {"async": False, "key": "flaverify", "filetype": "txt",
        "url": "http://127.0.0.1:9/none.txt", "outputtype": "pdf"}
header = b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
payload = b64u(json.dumps(body).encode())
sig = b64u(hmac.new(secret.encode("utf-8"), header + b"." + payload, hashlib.sha256).digest())
body["token"] = (header + b"." + payload + b"." + sig).decode()
req = urllib.request.Request(scheme + "://127.0.0.1:" + port + "/converter",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
try:
    try:
        r = urllib.request.urlopen(req, timeout=10)
        resp = r.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        resp = e.read(400).decode("utf-8", "replace")
except Exception as e:
    print("[verify] 网络错误:", e); sys.exit(2)
if "token" in resp.lower():
    print("[verify] 密钥不一致, DS 响应:", resp[:200]); sys.exit(1)
print("[verify] 密钥一致, DS 响应:", resp[:160]); sys.exit(0)
PYV
  return $?
}

touch "$LOG"
log "========== $BRAND 部署开始 (v1.26) =========="
[ "$(id -u)" = "0" ] || { echo "请用 root 运行: sudo bash install.sh"; exit 1; }

# ---------- 0. 环境 ----------
if [ -f /etc/centos-release ]; then
  OS="centos"; OSVER=$(grep -oE 'release [0-9]+' /etc/centos-release | head -1 | awk '{print $2}')
  log "系统: CentOS $OSVER"
elif [ -f /etc/debian_version ]; then
  OS="debian"; log "系统: Debian/Ubuntu $(cat /etc/debian_version 2>/dev/null)"
else
  OS="other"; log "系统: $(uname -a)"
fi
MEM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')
[ -n "${MEM_MB:-}" ] && log "内存: ${MEM_MB}MB"
IP=$(curl -s -m 6 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP=""

# ---------- 交互级别 ----------
# INTERACTIVE=1: 前台有真人在看(可提问); =0: 后台会话, 全部走默认值
INTERACTIVE=0
if [ "$INNER" = "0" ] && [ -t 0 ]; then INTERACTIVE=1; fi

# ---------- 预先收集管理员密码(转入后台前完成) ----------
if [ -z "$ADMIN_PW" ] && [ -n "${FLA_ADMIN_PW:-}" ]; then ADMIN_PW="$FLA_ADMIN_PW"; fi
if [ -z "$ADMIN_PW" ] && [ ! -f deploy/.env ] && [ "$INTERACTIVE" = "1" ]; then
  read -p "设置管理员 admin 初始密码 [留空=随机生成]: " ADMIN_PW
fi

# ---------- 预先收集对外域名(转入后台前完成; 用于 80/443 接管 + SSL 文件验证) ----------
if [ -z "$HTTPS_DOMS" ] && [ -n "${FLA_DOMAINS:-}" ]; then HTTPS_DOMS="$FLA_DOMAINS"; fi
if [ "$SSL_MODE" = "1" ] && [ -z "$HTTPS_DOMS" ] && [ "$INTERACTIVE" = "1" ]; then
  LAST_DOMS=$(grep -E '^DOMAINS=' /etc/fla/edge.state 2>/dev/null | head -1 | cut -d= -f2-)
  echo ""
  echo "对外域名 (nginx 接管 80/443 + 自动签发 SSL 证书, 采用文件验证):"
  echo "  · 可填多个, 空格分隔; 域名的 A 记录需已指向本机公网 IP"
  echo "  · 微软 Office 在线放映要求「域名 + 80/443」公开直链, 强烈建议填"
  read -p "  域名 [回车 = ${LAST_DOMS:-$FLA_DEFAULT_DOMS}] : " IN_DOMS
  HTTPS_DOMS=${IN_DOMS:-${LAST_DOMS:-$FLA_DEFAULT_DOMS}}
fi

# ---------- 转入 screen / tmux / nohup 保活 ----------
maybe_detach(){
  [ "$INNER" = "1" ] && return 0
  [ "$FOREGROUND" = "1" ] && return 0
  [ -t 0 ] || return 0
  [ -n "${STY:-}" ] && return 0        # 已在 screen 中
  [ -n "${TMUX:-}" ] && return 0       # 已在 tmux 中
  if ! command -v screen >/dev/null 2>&1; then
    if [ "$OS" = "centos" ]; then try yum install -y screen; else try apt-get install -y screen; fi
  fi
  if command -v screen >/dev/null 2>&1; then
    export FLA_ADMIN_PW="$ADMIN_PW" FLA_DOMAINS="$HTTPS_DOMS"
    screen -dmS fla-install bash "$SCRIPT_DIR/install.sh" --inner "${ORIG_ARGS[@]}"
    echo ""
    echo "✔ 安装已转入后台 screen 会话 [fla-install] (SSH 断开不影响)"
    echo "   实时查看:   screen -r fla-install   (离开会话: Ctrl+A 再按 D)"
    echo "   日志文件:   tail -f $SCRIPT_DIR/install.log"
    exit 0
  fi
  if ! command -v tmux >/dev/null 2>&1; then
    if [ "$OS" = "centos" ]; then try yum install -y tmux; else try apt-get install -y tmux; fi
  fi
  if command -v tmux >/dev/null 2>&1; then
    export FLA_ADMIN_PW="$ADMIN_PW" FLA_DOMAINS="$HTTPS_DOMS"
    TMUX_ARGS=$(printf ' %q' "${ORIG_ARGS[@]}")
    tmux new-session -d -s fla-install "bash '$SCRIPT_DIR/install.sh' --inner$TMUX_ARGS"
    echo ""
    echo "✔ 安装已转入后台 tmux 会话 [fla-install] (SSH 断开不影响)"
    echo "   实时查看:   tmux attach -t fla-install   (离开会话: Ctrl+B 再按 D)"
    echo "   日志文件:   tail -f $SCRIPT_DIR/install.log"
    exit 0
  fi
  FLA_ADMIN_PW="$ADMIN_PW" FLA_DOMAINS="$HTTPS_DOMS" nohup bash "$SCRIPT_DIR/install.sh" --inner "${ORIG_ARGS[@]}" >>"$LOG" 2>&1 &
  echo ""
  echo "✔ screen/tmux 不可用, 已用 nohup 后台运行 (SSH 断开不影响)"
  echo "   日志文件:   tail -f $SCRIPT_DIR/install.log"
  exit 0
}
maybe_detach "$@"
[ "$INNER" = "1" ] && log "===== 已在后台会话中继续执行 ====="

# ---------- 1. CentOS 7 EOL 源修复 ----------
if [ "$OS" = "centos" ] && [ "$OSVER" = "7" ]; then
  log "[1/9] CentOS 7 已 EOL, 检查 yum 源..."
  if ! try yum makecache fast 2>/dev/null; then
    if curl -sI -m 8 https://vault.centos.org >/dev/null 2>&1; then
      log "  切换 yum 源到 vault.centos.org 归档..."
      TS=$(date +%s)
      for f in /etc/yum.repos.d/CentOS-*.repo; do
        [ -f "$f" ] || continue
        cp "$f" "$f.bak.$TS" 2>/dev/null
        sed -i -e 's|^mirrorlist=|#mirrorlist=|g' \
               -e 's|^#baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' \
               -e 's|^baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' "$f"
      done
      try yum clean all
      try yum makecache fast || try yum -y makecache || log "  (yum 缓存建立失败, 继续尝试...)"
    else
      log "  ⚠ 无法访问 vault.centos.org, 若后续安装失败请检查网络"
    fi
  else
    ok "yum 源正常"
  fi
else
  log "[1/9] 非 CentOS 7, 跳过源修复"
fi

# ---------- 2. Docker ----------
log "[2/9] 检查 Docker ..."
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker 已安装并运行: $(docker --version)"
else
  log "  安装 Docker ..."
  if [ "$OS" = "centos" ]; then
    try yum install -y yum-utils
    try yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    if ! try yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin; then
      log "  官方源失败, 尝试阿里云镜像源..."
      try yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
      try yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin || true
    fi
  elif [ "$OS" = "debian" ]; then
    try apt-get update
    try apt-get install -y ca-certificates curl gnupg
    try bash -c 'install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc'
    try bash -c 'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$(. /etc/os-release && echo $ID) $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list'
    try apt-get update
    try apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin || true
  fi
  if ! command -v docker >/dev/null 2>&1; then
    log "  包管理器安装失败, 使用官方脚本 get.docker.com ..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && try bash /tmp/get-docker.sh
  fi
  command -v docker >/dev/null 2>&1 || die "Docker 安装失败"
  try systemctl start docker
  for i in $(seq 1 10); do docker info >/dev/null 2>&1 && break; sleep 3; done
  docker info >/dev/null 2>&1 || die "Docker 已安装但 daemon 未运行 (尝试: systemctl start docker; journalctl -u docker | tail -30)"
  ok "Docker 安装完成: $(docker --version)"
fi
try systemctl enable docker
log "  docker 服务已设为开机自启"

# ---------- 3. docker compose ----------
log "[3/9] 检查 docker compose ..."
DC=""
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"; ok "docker compose 插件可用"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"; ok "docker-compose 可用: $(docker-compose --version)"
else
  log "  安装 docker-compose 独立二进制..."
  ARC=$(uname -s)-$(uname -m)
  for U in "https://github.com/docker/compose/releases/latest/download/docker-compose-$ARC" \
           "https://get.daocloud.io/docker/compose/releases/latest/download/docker-compose-$ARC"; do
    if curl -sfL -m 300 "$U" -o /usr/local/bin/docker-compose; then
      chmod +x /usr/local/bin/docker-compose && DC="docker-compose" && break
    fi
  done
  [ -n "$DC" ] || die "docker-compose 下载失败, 请手动安装后重跑本脚本 (https://docs.docker.com/compose/install/)"
  ok "docker-compose 安装完成"
fi

# ---------- 4. 端口选择 ----------
log "[4/9] 选择端口 ..."
# v1.27: FLA 应用端口从 8306 起 —— 80/443 留给宿主机边缘 nginx 接管所有域名,
#        由它反代到这个端口 (微软放映需要的"域名+80/443"由边缘网关提供)
if [ -z "$PORT" ]; then
  for P in 8306 8307 8308 8309 8310; do
    if command -v ss >/dev/null 2>&1; then ss -ltn | grep -q ":$P " || { PORT=$P; break; }
    elif command -v netstat >/dev/null 2>&1; then netstat -ltn | grep -q ":$P " || { PORT=$P; break; }
    else PORT=$P; break; fi
  done
  PORT="${PORT:-8306}"
fi
if command -v ss >/dev/null 2>&1 && ss -ltn | grep -q ":$PORT " && ! docker ps --format '{{.Ports}}' | grep -q "0.0.0.0:$PORT"; then
  log "  ⚠ 端口 $PORT 已被占用(非本系统容器), 自动换到 $((PORT+1))"
  PORT=$((PORT+1))
fi
ok "使用端口: $PORT"

# ---------- 5. 基础配置 ----------
log "[5/9] 生成配置 ..."
if [ ! -f deploy/.env ]; then
  [ -z "$ADMIN_PW" ] && ADMIN_PW="FLA$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 10)"
  cat > deploy/.env <<EOF
ADMIN_PASSWORD=${ADMIN_PW}
PORT=${PORT}
ONLYOFFICE_JWT_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
EOF
  chmod 600 deploy/.env
  PW_SET=1
else
  log "  沿用已有 deploy/.env (更新端口)"
  sed -i "s/^PORT=.*/PORT=${PORT}/" deploy/.env
  grep -q "^ONLYOFFICE_JWT_SECRET=.\+" deploy/.env || \
    echo "ONLYOFFICE_JWT_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> deploy/.env
  PW_SET=0
fi
ok "配置就绪"

# ---------- 6. OnlyOffice: 复用已有 / 自带 / 精简 ----------
log "[6/9] OnlyOffice 决策 ..."
COMPOSE_FILE="deploy/docker-compose.yml"
EXT_DS=""; MODE_TXT=""; BUNDLED_DS=0; PROFILE=""
FW_PORTS=""   # 需要防火墙放行的额外端口(OnlyOffice 等)

if [ "$MODE" = "lite" ]; then
  COMPOSE_FILE="deploy/docker-compose.lite.yml"
  sed -i 's/^ONLYOFFICE_URL=.*/ONLYOFFICE_URL=/' deploy/.env 2>/dev/null
  MODE_TXT="精简(无动画放映)"
  log "  用户指定 --lite: 精简模式, 不使用 OnlyOffice"
elif [ "$DS_MODE" = "auto" ]; then
  # v1.19: 默认不部署 OnlyOffice (Office 预览/放映已改用微软在线渲染, 字体零错位)
  sed -i 's/^ONLYOFFICE_URL=.*/ONLYOFFICE_URL=/' deploy/.env 2>/dev/null
  MODE_TXT="完整(无 OnlyOffice, Office 走微软在线)"
  log "  默认不部署 OnlyOffice: Office 预览/放映改用微软在线渲染"
  log "  需要 OnlyOffice 时: 复用已有容器 --ds-external | 自带 --ds-new"
  log "  移除已有容器: sudo bash run.sh ds-remove"
else
  # -- 检测已有 OnlyOffice 容器(运行中的优先) --
  DS_CAND=""
  if [ "$DS_MODE" != "new" ]; then
    DS_CAND=$(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -i 'onlyoffice' | grep -i 'documentserver' | head -1 | cut -f1)
    if [ -z "$DS_CAND" ] && [ "$DS_MODE" = "external" ]; then
      DS_CAND=$(docker ps -a --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -i 'onlyoffice' | grep -i 'documentserver' | head -1 | cut -f1)
    fi
  fi
  USE_EXT=0
  if [ -n "$DS_CAND" ]; then
    if [ "$DS_MODE" = "external" ]; then USE_EXT=1
    elif [ "$INTERACTIVE" = "1" ]; then
      read -p "检测到本机已有 OnlyOffice 容器「$DS_CAND」, 直接复用它(保留你的配置)? [Y/n] " A
      if [ "$A" = "n" ] || [ "$A" = "N" ]; then USE_EXT=0; else USE_EXT=1; fi
    else
      log "  检测到已有 OnlyOffice 容器($DS_CAND), 默认复用 (不想复用请加 --ds-new 重跑)"
      USE_EXT=1
    fi
  fi
  if [ "$USE_EXT" = "1" ]; then
    EXT_DS="$DS_CAND"
    docker ps --format '{{.Names}}' | grep -qx "$EXT_DS" || { log "  容器未运行, 启动之..."; try docker start "$EXT_DS"; }
    DS_SCHEME="http"
    if [ -z "$DS_PORT" ]; then
      DS_PORT=$(docker port "$EXT_DS" 2>/dev/null | grep -E '^(80|443)/tcp' | head -1 | sed 's/.*://')
    fi
    if [ -z "$DS_PORT" ]; then
      # host 网络模式 / 未发布端口: 用 healthcheck 探测常见端口
      NETMODE=$(docker inspect "$EXT_DS" --format '{{.HostConfig.NetworkMode}}' 2>/dev/null)
      log "  docker port 无结果 (网络模式: ${NETMODE:-未知}), 探测常见端口..."
      for P in 80 443 8080 8081 8088 8888 8000 8880; do
        if curl -sf -m 3 "http://127.0.0.1:$P/healthcheck" 2>/dev/null | grep -qi true; then DS_PORT=$P; break; fi
        if curl -sfk -m 3 "https://127.0.0.1:$P/healthcheck" 2>/dev/null | grep -qi true; then DS_PORT=$P; DS_SCHEME="https"; break; fi
      done
      [ -n "$DS_PORT" ] && ok "已探测到 OnlyOffice 端口: $DS_PORT ($DS_SCHEME)"
    fi
    if [ -z "$DS_PORT" ]; then
      log "  端口映射: $(docker inspect "$EXT_DS" --format '{{json .HostConfig.PortBindings}}' 2>/dev/null)"
      log "  监听端口: $(ss -ltn 2>/dev/null | awk '{print $4}' | grep -oE '[0-9]+$' | sort -un | tr '\n' ' ')"
      die "无法探测 OnlyOffice 对外端口(容器可能是 host 网络或未发布端口)。查看上面打印的监听端口找到它, 然后重跑: sudo bash install.sh --ds-port 端口号 (若容器完全没发布端口, 浏览器无法访问, 需重建容器时加 -p 端口:80)"
    fi
    ds_json(){ docker exec "$EXT_DS" /var/www/onlyoffice/documentserver/npm/json -f /etc/onlyoffice/documentserver/local.json "$1" 2>/dev/null | head -1 | tr -d '"'; }
    if [ -n "$DS_SECRET" ]; then
      :   # 手动指定的优先
    elif [ -n "$(docker exec "$EXT_DS" printenv JWT_SECRET 2>/dev/null | head -1)" ]; then
      DS_SECRET=$(docker exec "$EXT_DS" printenv JWT_SECRET 2>/dev/null | head -1)
    elif [ -n "$(ds_json services.CoAuthoring.secret.session.string)" ]; then
      DS_SECRET=$(ds_json services.CoAuthoring.secret.session.string)   # 浏览器令牌用的就是 session 段
    else
      LJ=$(docker exec "$EXT_DS" cat /etc/onlyoffice/documentserver/local.json 2>/dev/null | tr -d ' \n\t')
      if [ -n "$LJ" ]; then
        DS_SECRET=$(echo "$LJ" | grep -o '"secret":{[^}]*}' | grep -o '"string":"[^"]*"' | head -1 | cut -d'"' -f4)
        [ -z "$DS_SECRET" ] && DS_SECRET=$(echo "$LJ" | grep -o '"string":"[^"]*"' | head -1 | cut -d'"' -f4)
      fi
    fi
    # 清理首尾引号/空白等常见杂质(内部字符保持原样, 密钥本身可能含特殊字符)
    DS_SECRET=$(printf '%s' "$DS_SECRET" | sed -e 's/^[ \t"\x27]*//' -e 's/[ \t"\x27]*$//' | head -c 256)
    if [ -n "$DS_SECRET" ]; then
      ok "已读取 OnlyOffice JWT 密钥"
    else
      log "  ⚠ 未在容器中找到 JWT 密钥(可能未开启 JWT), 将以无签名模式对接"
      log "    若放映报错, 用 --ds-secret 你的密钥 重跑"
      DS_SECRET=""
    fi
    for i in 1 2 3 4 5; do
      H=$(curl -sf -m 5 "http://127.0.0.1:$DS_PORT/healthcheck" 2>/dev/null)
      [ "$H" = "true" ] && break
      log "  等待 OnlyOffice 就绪..."; sleep 6
    done
    if [ "$H" = "true" ]; then ok "OnlyOffice 健康检查通过 (端口 $DS_PORT)"
    else log "  ⚠ OnlyOffice healthcheck 未通过, 可能仍在启动, 稍后自行验证"; fi
    # 密钥实测: 真实签名请求验证, 不一致时尝试所有候选密钥自动校准
    if [ -n "$DS_SECRET" ]; then
      verify_ds_secret "$DS_SECRET"; VRC=$?
      if [ "$VRC" = "1" ]; then
        log "  ⚠ 密钥实测不一致(DS 报 token 错误), 尝试自动校准..."
        for KEY in session inbox outbox; do
          CAND=$(ds_json "services.CoAuthoring.secret.$KEY.string")
          CAND=$(printf '%s' "$CAND" | sed -e 's/^[ \t"\x27]*//' -e 's/[ \t"\x27]*$//' | head -c 256)
          [ -n "$CAND" ] && [ "$CAND" != "$DS_SECRET" ] || continue
          verify_ds_secret "$CAND"; CVRC=$?
          if [ "$CVRC" = "0" ]; then DS_SECRET="$CAND"; ok "已自动校准为 secret.$KEY 段的密钥"; VRC=0; break; fi
        done
      fi
      if [ "$VRC" = "0" ]; then
        ok "OnlyOffice 密钥实测通过 (真实签名请求验证)"
      elif [ "$VRC" = "1" ]; then
        log "  ✘ 所有候选密钥均不一致! 你的 DS 可能改过 token 配置(自定义 header 等)"
        log "    排查: docker exec $EXT_DS cat /etc/onlyoffice/documentserver/local.json"
        log "    把 local.json 内容发给开发者分析; 或临时关闭 DS 的 JWT 测试"
      else
        log "  (无法自动实测密钥: 需要 python3 且 DS 可达, 已按读取值配置)"
      fi
    fi
    try docker update --restart=unless-stopped "$EXT_DS"
    log "  已为容器 $EXT_DS 设置开机自启(unless-stopped)"
    inject_ds_fonts "$EXT_DS"
    # DS 端口防火墙放行与主端口一起在第7步统一处理(避免 reload 后 docker 规则被清)
    FW_PORTS="$DS_PORT"
    [ -n "$IP" ] || die "无法获取本机 IP, 请手动编辑 deploy/.env 中 ONLYOFFICE_URL/APP_INTERNAL_URL 后重跑"
    set_env ONLYOFFICE_URL "$DS_SCHEME://$IP:$DS_PORT"
    set_env ONLYOFFICE_JWT_SECRET "$DS_SECRET"
    set_env APP_INTERNAL_URL "http://$IP:$PORT"
    COMPOSE_FILE="deploy/docker-compose.lite.yml"   # 外部 DS: nginx 无需 /ds/ 反代
    MODE_TXT="复用已有 OnlyOffice(容器 $EXT_DS :$DS_PORT)"
  else
    # -- 自带 OnlyOffice --
    PULLED=0
    if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q '^onlyoffice/documentserver:'; then
      ok "OnlyOffice 镜像已存在"; PULLED=1
    else
      for i in 1 2 3; do
        log "  拉取 onlyoffice/documentserver (第 $i 次, 约 1.5GB, 视网络 3-15 分钟)..."
        if try docker pull onlyoffice/documentserver:latest; then PULLED=1; break; fi
        sleep 5
      done
    fi
    if [ "$PULLED" = "1" ]; then
      set_env ONLYOFFICE_URL "/ds"
      set_env APP_INTERNAL_URL "http://app:${PORT}"
      PROFILE="ds"
      BUNDLED_DS=1
      MODE_TXT="完整(自带 OnlyOffice)"
    else
      log "  ⚠ OnlyOffice 镜像拉取失败, 自动切换精简模式 (其余功能不受影响)"
      COMPOSE_FILE="deploy/docker-compose.lite.yml"
      sed -i 's/^ONLYOFFICE_URL=.*/ONLYOFFICE_URL=/' deploy/.env 2>/dev/null
      MODE_TXT="精简(镜像拉取失败自动降级)"
    fi
  fi
fi

# ---------- 7. 智能清理旧容器 + 构建启动 + 三级自愈 ----------
log "[7/9] 智能重建容器 (首次构建需 3-10 分钟, 数据卷保留) ..."

# ---- 7a. 防火墙统一放行(一次 reload); reload 会清空 docker 的 iptables 规则, 先恢复 ----
FW_RELOADED=0
if systemctl is-active firewalld >/dev/null 2>&1; then
  NEED_RELOAD=0
  for P in $PORT $FW_PORTS; do
    if ! firewall-cmd --query-port="$P/tcp" >/dev/null 2>&1; then
      try firewall-cmd --permanent --add-port="$P/tcp"
      NEED_RELOAD=1
    fi
  done
  if [ "$NEED_RELOAD" = "1" ]; then
    try firewall-cmd --reload
    FW_RELOADED=1
    log "  防火墙已放行端口: $PORT${FW_PORTS:+ $FW_PORTS} (云服务器安全组还需在控制台放行)"
  fi
fi
if [ "$FW_RELOADED" = "1" ]; then
  log "  firewalld 重载会清空 docker 的 iptables 规则, 先重启 docker 恢复..."
  log "  (服务器上所有容器会重启一次; 无 restart 策略的其他容器之后需手动 docker start)"
  try systemctl restart docker
  for i in $(seq 1 20); do docker info >/dev/null 2>&1 && break; sleep 3; done
fi

clean_container(){
  local N="$1"
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$N" || return 0
  local PROJ
  PROJ=$(docker inspect "$N" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)
  if [ -n "$PROJ" ]; then
    try docker rm -f "$N"
    log "  智能重建: 已移除旧容器 $N (compose 项目: $PROJ)"
  else
    local NN="$N-old-$(date +%s)"
    try docker rename "$N" "$NN"
    log "  ⚠ 已有同名容器 $N (非 FLA compose 创建, 可能包含你的自定义配置)"
    log "    已改名保留为 $NN (未删除). 如需改用它: docker start $NN 后重跑 sudo bash install.sh --ds-external"
  fi
}

# 健康等待: $1=端口 $2=最长秒数 $3=模式(nginx|direct)
# 返回: 0=健康 1=超时/fla异常 2=nginx容器异常
check_endpoint(){
  curl -sf -m 3 "$1" >/dev/null 2>&1
}

check_fla_internal(){
  docker exec fla python3 -c "import urllib.request, os; p=os.environ.get('PORT', '8306'); urllib.request.urlopen(f'http://127.0.0.1:{p}/api/health', timeout=3)" >/dev/null 2>&1 || \
  docker exec fla python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" >/dev/null 2>&1
}

wait_healthy(){
  local WP="$1" WT="$2" WM="$3"
  local T0=$(date +%s) EL=0
  while [ "$EL" -lt "$WT" ]; do
    if check_endpoint "http://127.0.0.1:$WP/api/health" || \
       check_endpoint "http://localhost:$WP/api/health" || \
       { [ -n "$IP" ] && check_endpoint "http://$IP:$WP/api/health"; }; then
      return 0
    fi
    # 宿主机回环可能因云厂商安全组/缺少 Hairpin NAT 无法 curl 本机映射端口，但容器内部正常且端口正在监听
    if check_fla_internal; then
      if (command -v ss >/dev/null 2>&1 && ss -ltn | grep -q ":$WP ") || \
         (command -v netstat >/dev/null 2>&1 && netstat -tlpn 2>/dev/null | grep -q ":$WP ") || \
         (docker ps --format '{{.Ports}}' --filter "name=fla" 2>/dev/null | grep -q "$WP"); then
        return 0
      fi
    fi
    if ! docker ps --format '{{.Names}}' | grep -qx 'fla'; then
      log "  ⚠ fla 容器退出, 日志:"
      docker logs --tail 40 fla 2>&1 | tee -a "$LOG"
      return 1
    fi
    if [ "$WM" = "nginx" ] && docker ps -a --format '{{.Names}}' | grep -qx 'nginx'; then
      if ! docker ps --format '{{.Names}}' | grep -qx 'nginx'; then
        log "  ⚠ nginx 容器退出, 日志:"
        docker logs --tail 40 nginx 2>&1 | tee -a "$LOG"
        return 2
      fi
    fi
    sleep 4; EL=$(( $(date +%s) - T0 ))
  done
  return 1
}

clean_container "fla"
clean_container "nginx"
if [ "$BUNDLED_DS" = "1" ]; then
  clean_container "fla-onlyoffice"
fi
for F in deploy/docker-compose.yml deploy/docker-compose.lite.yml deploy/docker-compose.direct.yml; do
  [ -f "$F" ] && try $DC -f "$F" --profile full --profile ds down --remove-orphans
done
# ---- docker 容器内 DNS 自检: 失败则给 docker 配置公共 DNS (常见: 宿主机 resolv.conf 指向 127.0.0.53) ----
if ! timeout 25 docker run --rm python:3.11-slim-bookworm python -c "import socket;socket.gethostbyname('deb.debian.org')" >/dev/null 2>&1; then
  log "  容器内 DNS 解析失败, 为 docker 配置公共 DNS (223.5.5.5 / 8.8.8.8) ..."
  if [ ! -s /etc/docker/daemon.json ]; then
    mkdir -p /etc/docker && echo '{"dns": ["223.5.5.5", "119.29.29.29", "8.8.8.8"]}' > /etc/docker/daemon.json
    systemctl restart docker && sleep 3
  elif ! grep -q '"dns"' /etc/docker/daemon.json; then
    python3 - <<'PY' 2>/dev/null && systemctl restart docker && sleep 3
import json; p="/etc/docker/daemon.json"; d=json.load(open(p)); d["dns"]=["223.5.5.5","119.29.29.29","8.8.8.8"]; json.dump(d,open(p,"w"),indent=2)
PY
  fi
fi
echo "$COMPOSE_FILE" > deploy/.compose_file
echo "$PROFILE" > deploy/.compose_profile

if [ -n "$PROFILE" ]; then
  $DC --profile "$PROFILE" -f "$COMPOSE_FILE" up -d --build 2>&1 | tee -a "$LOG"
else
  $DC -f "$COMPOSE_FILE" up -d --build 2>&1 | tee -a "$LOG"
fi
RC=${PIPESTATUS[0]}
if [ "$RC" != "0" ]; then
  die "构建/启动失败, 完整日志见 install.log 与: $DC -f $COMPOSE_FILE logs"
fi

# ---------- 健康等待 + 三级自愈 ----------
log "等待服务就绪 ..."
HRC=0
wait_healthy "$PORT" 90 "nginx" || HRC=$?
if [ "$HRC" = "0" ]; then
  ok "服务已就绪 (nginx 模式)"
else
  if [ "$HRC" = "2" ]; then
    log ">> nginx 容器异常, 尝试重启一次 ..."
    try docker restart nginx
    HRC=0
    wait_healthy "$PORT" 45 "nginx" || HRC=$?
  fi
  if [ "$HRC" != "0" ]; then
    log ">> 经 nginx 的访问不通 (日志已记录到 install.log 供排查)"
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'nginx'; then
      log "--- nginx 诊断日志 ---"
      docker logs --tail 40 nginx >> "$LOG" 2>&1
      log "--------------------------"
    fi
    log ">> 自动切换【直连模式】: app 容器直接发布端口 $PORT, 绕过 nginx ..."
    try $DC -f deploy/docker-compose.yml --profile full down --remove-orphans
    try $DC -f deploy/docker-compose.lite.yml down --remove-orphans
    clean_container "fla"
    clean_container "nginx"
    if [ "$BUNDLED_DS" = "1" ]; then
      # 自带 OnlyOffice 直连: 直接发布 DS 端口
      DS_PUB=8880
      for P in 8880 8881 8882 8883 8884; do
        if command -v ss >/dev/null 2>&1; then ss -ltn | grep -q ":$P " || { DS_PUB=$P; break; }
        else DS_PUB=$P; break; fi
      done
      set_env DS_PORT "$DS_PUB"
      set_env ONLYOFFICE_URL "http://$IP:$DS_PUB"
      set_env APP_INTERNAL_URL "http://$IP:$PORT"
      PROFILE="full"
      clean_container "fla-onlyoffice"
      if systemctl is-active firewalld >/dev/null 2>&1; then
        try firewall-cmd --permanent --add-port="$DS_PUB/tcp"
        try firewall-cmd --reload
      fi
      log "  OnlyOffice 直连端口: $DS_PUB"
    fi
    COMPOSE_FILE="deploy/docker-compose.direct.yml"
    echo "$COMPOSE_FILE" > deploy/.compose_file
    echo "$PROFILE" > deploy/.compose_profile
    if [ -n "$PROFILE" ]; then
      $DC --profile "$PROFILE" -f "$COMPOSE_FILE" up -d --build 2>&1 | tee -a "$LOG"
    else
      $DC -f "$COMPOSE_FILE" up -d --build 2>&1 | tee -a "$LOG"
    fi
    RC=${PIPESTATUS[0]}
    if [ "$RC" = "0" ]; then
      HRC=0
      wait_healthy "$PORT" 90 "direct" || HRC=$?
      if [ "$HRC" = "0" ]; then ok "服务已就绪 (直连模式)"; fi
    fi
  fi
  if [ "$HRC" != "0" ]; then
    log ">> 直连仍不通, 最后手段: 重启 docker 服务修复 iptables/防火墙规则"
    log "   (服务器上所有容器会自动重启, 约半分钟)"
    try systemctl restart docker
    sleep 12
    for i in $(seq 1 20); do docker info >/dev/null 2>&1 && break; sleep 3; done
    if [ -n "$PROFILE" ]; then
      try $DC --profile "$PROFILE" -f "$COMPOSE_FILE" up -d
    else
      try $DC -f "$COMPOSE_FILE" up -d
    fi
    HRC=0
    wait_healthy "$PORT" 120 "direct" || HRC=$?
    if [ "$HRC" = "0" ]; then ok "服务已就绪 (docker 重启后恢复)"; fi
  fi
  if [ "$HRC" != "0" ]; then
    log "--- 最终诊断信息 ---"
    docker ps -a --filter name=fla --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1 | tee -a "$LOG"
    log "--- fla 日志 ---"; docker logs --tail 40 fla >> "$LOG" 2>&1
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'nginx'; then
      log "--- nginx 日志 ---"; docker logs --tail 40 nginx >> "$LOG" 2>&1
    fi
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'fla'; then
      ok "fla 容器已处于运行状态 (Up)。"
      log "  宿主机回环探测未直接连通，可能为云厂商安全组/缺少 hairpin NAT 限制，但外部与内部服务已就绪！"
      HRC=0
    else
      die "多种方式均无法连通服务, 请把 install.log 发给开发者 (里面已含全部诊断日志)"
    fi
  fi
fi

# 自带 OnlyOffice 时, 等 DS 就绪(非致命)
if [ "$BUNDLED_DS" = "1" ]; then
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if try docker exec fla-onlyoffice curl -sf -m 3 http://localhost/healthcheck; then
      ok "OnlyOffice 文档服务器就绪"; break
    fi
    sleep 6
  done
  inject_ds_fonts "fla-onlyoffice"
fi

# ---------- 8. 边缘网关: 宿主机 nginx 占用 80/443, 接管所有域名 ----------
HDOMS=${HTTPS_DOMS:-$FLA_DEFAULT_DOMS}
EDGE_OK=0; SSL_OK=0; PUBLIC_URL=""
PRIMARY_DOM=$(echo $HDOMS | awk '{print $1}')
if [ "$EDGE_MODE" = "1" ]; then
  log "[8/9] 边缘网关: nginx 接管 80/443 → 反代 127.0.0.1:$PORT ..."
  if bash "$SCRIPT_DIR/edge.sh" --port "$PORT" setup $HDOMS 2>&1 | tee -a "$LOG"; then
    EDGE_OK=1
    ok "80/443 已由宿主机 nginx 接管 (server_name _ → 所有域名都指向 FLA)"
    log "  你自己在 /etc/nginx/conf.d/ 里配置的其他站点(有明确 server_name)不受影响;"
    log "  只有【没匹配到任何站点】的域名/IP 会落到 FLA"
  else
    log "  ⚠ 边缘网关部署失败 — FLA 仍可用 http://$IP:$PORT/ 直接访问"
    log "    排查: cat $SCRIPT_DIR/edge.log (常见: 80/443 被别的程序占用、nginx 装不上)"
    log "    手动重试: sudo bash edge.sh --port $PORT setup $HDOMS"
  fi
else
  log "[8/9] --no-edge: 跳过宿主机 nginx (FLA 仅 http://$IP:$PORT/ 访问)"
fi

# ---------- 9. SSL 证书: Let's Encrypt 文件验证 (HTTP-01 / webroot, 不停机) ----------
if [ "$EDGE_MODE" = "1" ] && [ "$SSL_MODE" = "1" ]; then
  log "[9/9] SSL 证书 (文件验证 HTTP-01): $HDOMS ..."
  if bash "$SCRIPT_DIR/https.sh" --yes --port "$PORT" $HDOMS 2>&1 | tee -a "$LOG"; then
    SSL_OK=1
    PUBLIC_URL="https://$PRIMARY_DOM"
    ok "HTTPS 就绪: $PUBLIC_URL (证书自动续期, 签发过程不停机)"
  else
    log "  ⚠ 证书签发失败 — HTTP 访问不受影响; 修好后单独重跑: sudo bash https.sh $HDOMS"
    log "    最常见三个原因(https.log 里有【文件验证自检】结果):"
    log "      1) 域名 A 记录还没指向本机公网 IP"
    log "      2) 云服务器控制台安全组没放行 80 (文件验证要求公网能访问 80)"
    log "      3) 80 被别的程序/容器占用 (sudo bash edge.sh status 可查)"
    PUBLIC_URL="http://$PRIMARY_DOM"
  fi
elif [ "$EDGE_MODE" = "1" ]; then
  log "[9/9] --no-ssl: 跳过证书签发 (443 用自签兜底证书, 浏览器会提示不受信任)"
  PUBLIC_URL="http://$PRIMARY_DOM"
else
  log "[9/9] 跳过 SSL (未启用边缘网关)"
fi
# 微软 Office 在线放映要求公开直链是「域名 + 80/443」: 把对外地址写进配置
if [ -n "$PUBLIC_URL" ]; then
  set_env PUBLIC_BASE_URL "$PUBLIC_URL"
  log "  公开访问地址已写入 deploy/.env: PUBLIC_BASE_URL=$PUBLIC_URL"
  # 让已启动的 app 容器读到新配置(重建 app 容器, 数据卷不动)
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx fla; then
    try $DC ${PROFILE:+--profile $PROFILE} -f "$COMPOSE_FILE" up -d --no-deps --force-recreate app
    log "  已重建 app 容器以应用公开访问地址(数据保留)"
  fi
fi

# ---------- 完成 ----------
[ -z "$IP" ] && IP=$(curl -s -m 6 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP="服务器IP"
[ "$COMPOSE_FILE" = "deploy/docker-compose.direct.yml" ] && MODE_TXT="$MODE_TXT · 直连备用模式(不经容器nginx)"

echo "" | tee -a "$LOG"
log "================================================"
log "  $BRAND 部署成功!   模式: $MODE_TXT"
if [ -n "$PUBLIC_URL" ]; then
  log "  访问地址:  $PUBLIC_URL/          ← 推荐"
  for D in $HDOMS; do
    [ "$D" = "$PRIMARY_DOM" ] && continue
    log "             $([ "$SSL_OK" = "1" ] && echo https || echo http)://$D/"
  done
fi
if [ "$EDGE_OK" = "1" ]; then
  log "  任意域名:  指向本机的任何域名/IP 都会落到 FLA (nginx catch-all 80/443)"
fi
log "  本机直连:  http://$IP:$PORT/     (127.0.0.1:$PORT)"
if [ "$SSL_OK" = "1" ]; then
  log "  SSL 证书:  文件验证(HTTP-01)签发成功 · 自动续期 每天 03:17/15:17"
  log "             证书: /etc/fla/ssl/fullchain.pem · 状态: sudo bash https.sh --status"
fi
log "  管理员账号: admin"
if [ "${PW_SET:-0}" = "1" ]; then
  log "  初始密码:  $ADMIN_PW"
  log "  (密码也保存在 deploy/.env, 请登录后立即修改!)"
else
  log "  密码: 见 deploy/.env (首次部署时设置)"
fi
log "  开机自启: 已开启 (docker 服务 + 容器 restart 策略 + nginx)"
if [ -n "$EXT_DS" ]; then
  log "  ---- 外部 OnlyOffice 注意事项 ----"
  log "  1. 云服务器控制台安全组需放行端口: $DS_PORT (浏览器要访问) 和 $PORT"
  log "  2. 若放映加载失败: 检查 $EXT_DS 能否访问 http://$IP:$PORT"
  log "     (回源地址可在 deploy/.env 的 APP_INTERNAL_URL 修改后 sudo bash run.sh restart)"
fi
if [ "$COMPOSE_FILE" = "deploy/docker-compose.direct.yml" ] && [ "$BUNDLED_DS" = "1" ]; then
  log "  (直连模式下 OnlyOffice 端口: $(grep '^DS_PORT=' deploy/.env | cut -d= -f2), 安全组需放行)"
fi
log "------------------------------------------------"
log "  日常管理请使用 run.sh:"
log "    sudo bash run.sh status    查看状态(含 80/443 边缘网关与证书)"
log "    sudo bash run.sh logs      看日志(Ctrl+C 退出)"
log "    sudo bash run.sh stop      停止"
log "    sudo bash run.sh start     启动"
log "    sudo bash run.sh restart   重启"
log "    sudo bash run.sh update    更新版本(=智能重建, 保留数据)"
log "    sudo bash run.sh edge      重建 80/443 边缘网关(换端口/换域名后)"
log "    sudo bash run.sh ssl       证书状态 / 重签"
log "    sudo bash run.sh doctor    一键诊断(生成 doctor.log)"
log "  卸载: sudo bash uninstall.sh"
log "  彻底重装(删光 docker 再装): sudo bash completely_new_install.sh"
log "================================================"

#TROUBLE
# 1. 端口被占用: 换端口重跑  sudo bash install.sh --port 8307
# 2. Docker 起不来: systemctl start docker; journalctl -u docker | tail -30
# 3. CentOS7 yum 报错: 确认服务器能上网(curl -I https://vault.centos.org)
# 4. 构建失败: cat install.log 查看; 常见是磁盘满或网络超时, 清理后重跑
# 5. 内存不足(OOM): 用 sudo bash install.sh --lite 重装精简版
# 6. 访问不了: 防火墙放行端口:
#      firewall-cmd --permanent --add-port=$PORT/tcp && firewall-cmd --reload
#    云服务器还需在控制台安全组放行该端口
# 7. 复用外部 OnlyOffice 放映失败:
#      a) 浏览器打开 http://IP:DS端口/healthcheck 应显示 true
#      b) docker exec 容器名 curl -s http://IP:FLA端口/api/health 应返回 ok
#         不通则改 deploy/.env 的 APP_INTERNAL_URL 后 sudo bash run.sh restart
#      c) 报 Unsupported token/签名错误: 直接重跑 sudo bash install.sh (v1.10 起会实测并自动校准密钥);#      仍失败用 --ds-secret 传入正确密钥
# 8. 容器名冲突(fla/fla-onlyoffice 已被占用): 脚本会自动清理或改名保留;
#    仍冲突时: docker rm -f fla nginx fla-onlyoffice 后重跑
# 9. SSH 断开导致安装中断: 直接重跑 sudo bash install.sh (会自动进 screen,
#    且已构建的层有缓存, 续跑很快)
# 10. nginx 转发不通: 脚本会自动降级(重启nginx→直连模式→重启docker);
#     若最终以直连模式运行又想排查 nginx: 看 install.log 中 nginx 日志
# 11. 一键诊断: sudo bash run.sh doctor 会把全部容器日志/网络/健康检查
#     收集到 doctor.log, 发给开发者即可定位问题
# 12. 仍然失败: 把 install.log 和 doctor.log 发给开发者
#END-TROUBLE
