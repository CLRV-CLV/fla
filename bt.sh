#!/usr/bin/env bash
# FLA 宝塔面板 / 非 Docker 部署脚本
#   首次安装:  bash bt.sh install          (装系统依赖 + Python 虚拟环境 + 开机自启服务)
#   更新代码:  bash bt.sh pull [分支]       (默认 arena/63f6f4b2-fla, 更新后自动重启; 也可 FLA_BRANCH=xxx bash bt.sh pull)
#   宝塔Python项目模式: bash bt.sh deps (只装系统依赖) ; bash bt.sh pull (只更新代码, 然后在宝塔点重启)
#   其它:      bash bt.sh start|stop|restart|status|logs|passwd 新密码
# 进程由 systemd 守护 (fla.service, 崩溃自动重启/开机自启); 宝塔负责 Nginx 反代、SSL、防火墙。
set -u
cd "$(dirname "$0")"
DIR=$(pwd)
# 默认拉取分支: 修复/新功能分支. 合并进 main 后可改成 main 或 BRANCH_DEFAULT="main"
# 优先级: 命令行参数 > 环境变量 FLA_BRANCH > 下面这个默认值
BRANCH_DEFAULT="${FLA_BRANCH:-arena/63f6f4b2-fla}"
ENVF="$DIR/deploy/.env"
VENV="$DIR/.venv"
SVC=fla

[ "$(id -u)" = "0" ] || { echo "请用 root 运行: bash bt.sh ..."; exit 1; }
mkdir -p "$DIR/deploy"
[ -f "$ENVF" ] || printf 'PORT=8306\nFLA_DATA_DIR=%s/data\nADMIN_PASSWORD=\nPUBLIC_BASE_URL=\nRTMP_PORT=1935\n' "$DIR" > "$ENVF"
getenv() { grep -E "^$1=" "$ENVF" 2>/dev/null | tail -1 | cut -d= -f2-; }
PORT=$(getenv PORT); PORT=${PORT:-8306}
RTMP=$(getenv RTMP_PORT); RTMP=${RTMP:-1935}

find_python() {
  # 优先 3.11/3.12/3.10, 再找宝塔「Python 版本管理」装的版本, 最后系统 python3 (>=3.9)
  for c in python3.11 python3.12 python3.10 \
           /www/server/pyporject_evn/versions/3.11*/bin/python3 /www/server/pyporject_evn/versions/3.12*/bin/python3 \
           /www/server/pyporject_evn/versions/3.10*/bin/python3 /www/server/python_manager/versions/3.1[0-2]*/bin/python3 python3; do
    p=$(command -v "$c" 2>/dev/null || { [ -x "$c" ] && echo "$c"; })
    [ -n "$p" ] || continue
    "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null && { echo "$p"; return; }
  done
}

pkg_install() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update || true
    apt-get install -y --no-install-recommends curl tar ca-certificates python3 python3-venv python3-pip \
      libreoffice-impress libreoffice-writer libreoffice-calc fontconfig tzdata ffmpeg \
      fonts-noto-cjk fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea
    apt-get install -y --no-install-recommends fonts-liberation2 fonts-lxgw-wenkai 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
    Y=$(command -v dnf || command -v yum)
    $Y install -y epel-release 2>/dev/null || true
    $Y install -y curl tar python3 python3-pip libreoffice-impress libreoffice-writer libreoffice-calc \
      fontconfig google-noto-sans-cjk-ttc-fonts liberation-fonts 2>/dev/null || true
    $Y install -y ffmpeg 2>/dev/null || echo "  ! ffmpeg 未装上 (视频转码/iPhone投屏会用到), 可在宝塔软件商店或 rpmfusion 安装"
  else
    echo "  ! 未识别的系统, 请手动安装: libreoffice ffmpeg 中文字体 python3"
  fi
  fc-cache -f >/dev/null 2>&1 || true
}

write_service() {
  cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=FLA (FYX Lesson All)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
EnvironmentFile=$ENVF
Environment=PYTHONUNBUFFERED=1 TZ=Asia/Hong_Kong MALLOC_ARENA_MAX=2
ExecStart=$VENV/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port \${PORT} --workers 1 --proxy-headers --forwarded-allow-ips *
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable $SVC >/dev/null 2>&1
}

open_ports() {
  for P in "$PORT" "$RTMP"; do
    if command -v bt >/dev/null 2>&1 || [ -d /www/server/panel ]; then
      # 宝塔防火墙 (调用面板自带脚本; 失败不影响, 也可在 面板→安全 手动放行)
      /www/server/panel/pyenv/bin/python /www/server/panel/tools.py firewall_add "$P" >/dev/null 2>&1 || true
    fi
    if systemctl is-active firewalld >/dev/null 2>&1; then
      firewall-cmd --permanent --add-port="$P/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1
    fi
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then ufw allow "$P/tcp" >/dev/null 2>&1; fi
    if command -v iptables >/dev/null 2>&1 && ! iptables -C INPUT -p tcp --dport "$P" -j ACCEPT 2>/dev/null; then
      iptables -I INPUT -p tcp --dport "$P" -j ACCEPT 2>/dev/null
    fi
  done
  echo "  ✔ 已尝试放行 $PORT、$RTMP (宝塔: 面板→安全 里确认; 云控制台安全组需手动放行 TCP $RTMP)"
}

pip_install() {
  "$VENV/bin/python" -m pip install -q --upgrade pip 2>/dev/null || true
  "$VENV/bin/python" -m pip install -r requirements.txt \
    || "$VENV/bin/python" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
}

health() {
  for i in $(seq 1 30); do
    if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
      echo "✔ 应用已就绪: http://127.0.0.1:$PORT/   版本: $(curl -s http://127.0.0.1:$PORT/api/version)"
      return 0
    fi
    sleep 1
  done
  echo "✘ 30 秒内未就绪, 查看日志: bash bt.sh logs"; return 1
}

REPO_PATH="CLRV-CLV/fla"   # 旧名 CLRV-FYX/fla 会 301 重定向到这里, 两者都能用
# 代码源: 直连 GitHub 慢/断时自动换加速镜像 (按顺序尝试)
SOURCES="https://github.com https://gh-proxy.com/https://github.com https://ghfast.top/https://github.com https://gh.llkk.cc/https://github.com"

download_code() {
  B="${1:-$BRANCH_DEFAULT}"
  echo ">> 同步代码 (分支 $B)"
  if command -v git >/dev/null 2>&1; then
    # 增量更新: 首次把目录变成 git 仓库, 之后每次只下载变化的部分 (几十 KB), 不再整包 33MB
    # 宝塔目录属主是 www, root 操作会被 git 拒绝 (dubious ownership) → 加入信任
    git config --global --get-all safe.directory 2>/dev/null | grep -qx "$DIR" || git config --global --add safe.directory "$DIR"
    [ -d .git ] || { git init -q . && echo "  (首次: 初始化增量更新)"; }
    git config http.lowSpeedLimit 20000; git config http.lowSpeedTime 20   # 低于 20KB/s 持续 20 秒 → 换下一个源
    OLD=$(git rev-parse --short HEAD 2>/dev/null)
    for S in $SOURCES; do
      echo "  git: $S/$REPO_PATH"
      rm -f .git/FETCH_HEAD
      if timeout 900 git fetch -q --depth 1 "$S/$REPO_PATH.git" "$B" && git rev-parse -q --verify FETCH_HEAD >/dev/null \
         && [ "$(git log -1 --format=%ct FETCH_HEAD)" -ge "$(git log -1 --format=%ct HEAD 2>/dev/null || echo 0)" ] \
         && git reset -q --hard FETCH_HEAD; then
        echo "  ✔ 代码版本: ${OLD:-首次} -> $(git log --oneline -1)"
        git log -1 --format='%h %cd' --date=format:'%Y-%m-%d %H:%M' > server/BUILD
        return 0
      fi
      echo "  ✘ 失败或太慢, 换下一个源"
    done
  fi
  T=/tmp/fla-src.tgz
  for S in $SOURCES; do
    U="$S/$REPO_PATH/archive/refs/heads/$B.tar.gz"
    [ "$S" = "https://github.com" ] && U="https://codeload.github.com/$REPO_PATH/tar.gz/refs/heads/$B"
    echo "  下载: $U"; rm -f "$T"
    if command -v curl >/dev/null 2>&1; then
      curl -fL --connect-timeout 15 --speed-limit 20000 --speed-time 20 -o "$T" "$U" || rm -f "$T"
    else
      timeout 900 python3 -c "import sys,urllib.request;urllib.request.urlretrieve(sys.argv[1],sys.argv[2])" "$U" "$T" || rm -f "$T"
    fi
    [ -s "$T" ] && tar -tzf "$T" >/dev/null 2>&1 && break
    rm -f "$T"; echo "  ✘ 失败或太慢, 换下一个地址"
  done
  [ -s "$T" ] || { echo "✘ 代码下载失败"; return 1; }
  tar -xzf "$T" --strip-components=1 || return 1
  date '+tgz %Y-%m-%d %H:%M' > server/BUILD
  echo "  ✔ 代码已更新 ($(grep -oP 'CURRENT_VERSION = "\K[^"]+' server/routers/desktop.py))"
}

after_pull() {
  if [ -f /etc/systemd/system/$SVC.service ]; then
    [ -x "$VENV/bin/python" ] && pip_install >/dev/null 2>&1
    write_service; systemctl restart $SVC; health
  else
    echo "✔ 代码已更新。请到 宝塔 → 网站 → Python项目 → FLA 点「重启」(依赖有变化时先点「模块」安装 requirements.txt)"
  fi
  echo "  提示: 前端有新版本时, 浏览器按一次 Ctrl+F5 可立刻去掉旧缓存"
}

case "${1:-status}" in
  install)
    echo ">> 1/4 安装系统依赖 (LibreOffice / ffmpeg / 中文字体)"
    pkg_install
    echo ">> 2/4 Python 虚拟环境"
    PY=$(find_python)
    [ -n "$PY" ] || { echo "✘ 没有找到 Python 3.9+; 请在 宝塔→网站→Python项目→Python版本管理 安装 3.11 后重跑"; exit 1; }
    echo "  使用 $PY ($($PY -V 2>&1))"
    [ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV" || { echo "✘ 创建虚拟环境失败 (Debian/Ubuntu: apt install python3-venv)"; exit 1; }
    pip_install || { echo "✘ Python 依赖安装失败"; exit 1; }
    echo ">> 3/4 开机自启服务 + 防火墙"
    mkdir -p "$(getenv FLA_DATA_DIR || echo "$DIR/data")"
    write_service
    open_ports
    echo ">> 4/4 启动"
    systemctl restart $SVC
    health
    echo
    echo "初始管理员密码: $(cat "$(getenv FLA_DATA_DIR)/initial_admin_password.txt" 2>/dev/null || echo '见 bash bt.sh logs')"
    echo "下一步: 宝塔 → 网站 → 添加站点 → 反向代理到 http://127.0.0.1:$PORT (见 docs/宝塔部署.md)"
    ;;
  deps)
    # 宝塔 Python 项目模式: 只装系统依赖 (Python 依赖由宝塔按 requirements.txt 安装)
    pkg_install
    open_ports
    echo "✔ 系统依赖已安装。接下来在 宝塔 → 网站 → Python项目 添加项目 (见 docs/宝塔部署.md)"
    ;;
  pull|update)
    download_code "${2:-}" || exit 1
    # 更新后 bt.sh 自身也可能被换掉: 若新文件支持 _after_pull, 就交给新文件收尾
    # (bash 会按旧文件偏移继续读脚本, 文件被替换后容易执行到错乱的指令)。
    # 老版本 bt.sh 没有 _after_pull → 自动走下面原地收尾, 保证首次升级也安全。
    if [ "${FLA_REEXEC:-0}" != "1" ] && grep -q '^  _after_pull)' "$0" 2>/dev/null; then
      FLA_REEXEC=1 exec bash "$0" _after_pull
    fi
    after_pull
    ;;
  _after_pull)
    after_pull
    ;;
  start|stop|restart) systemctl "$1" $SVC; [ "$1" = stop ] || health ;;
  status) systemctl status $SVC --no-pager -l | head -15; curl -s "http://127.0.0.1:$PORT/api/version"; echo ;;
  logs) journalctl -u $SVC -n 200 --no-pager ;;
  passwd)
    [ -n "${2:-}" ] || { echo "用法: bash bt.sh passwd 新密码"; exit 1; }
    D=$(getenv FLA_DATA_DIR); D=${D:-$DIR/data}
    "$VENV/bin/python" -c "import sqlite3,bcrypt,sys;c=sqlite3.connect(sys.argv[1]+'/fla.db');c.execute('UPDATE users SET password_hash=? WHERE username=\"admin\"',(bcrypt.hashpw(sys.argv[2].encode(),bcrypt.gensalt()).decode(),));c.commit();print('✔ admin 密码已重置')" "$D" "$2"
    ;;
  *) echo "用法: bash bt.sh {install|pull|start|stop|restart|status|logs|passwd 新密码}" ;;
esac
