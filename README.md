# FLA · FYX Lesson All

面向课堂教学的课件与互动白板系统：上传课件（Office / PDF / 图片 / 音视频），在教室大屏或任意设备上打开、写画、圈选、放映，带完整用户系统、论坛、微信级聊天与管理后台。由学生为老师打造。

> **v1.34**：放映舞台左侧工具栏左右翻页键专用于**独立切换画布板书页数**（与微软课件解耦，绝不重载或重置课件进度，只能通过点击触发）；上下左右与翻页笔按键完全放行给微软原生响应，选择模式下屏幕点击直接穿透课件。完整自动同步技术方案备用于 [`docs/SLIDE_SYNC_BACKUP.md`](docs/SLIDE_SYNC_BACKUP.md)。
>
> **v1.27**：微软放映**板书画布严格随页切换**（老大难修复）· 聊天升级到**微信级**（私聊/未读角标/已读回执/正在输入/引用/回应/@/图片文件语音/群管理/搜索）· **认证证书大升级**（3D 全息证书 + 二维码核验 + 打印存 PDF + 后台实时预览）· **nginx 接管 80/443 反代 8306**（`edge.sh`）· **SSL 自动签发用文件验证**（`https.sh`，HTTP-01）· **`completely_new_install.sh` 连 Docker 一起铲掉重装** · 全站动效打磨 · 322 项冒烟测试
>
> **v1.26**：图片放映修复 · 通用视频格式（mkv/mov/wmv/avi…）自动转 MP4 · 扫码登录 · 论坛 · 聊天 · 公告（全局/专属）· 认证图标颜色自定义 · 站长认证（铂金极光）· 默认端口 8306

---

## 一、部署（解压 → 运行一个脚本）

```bash
# 上传压缩包到服务器(任意位置), 解压后:
cd FLA
sudo bash install.sh
```

脚本会自动完成：修复 CentOS 7 EOL 源 → 安装 Docker（已装自动跳过）→ 安装 docker compose → 选端口（默认 **8306**，被占用自动顺延 8307…8310）→ **装宿主机 nginx 网关（占 80 + 443，反代到 FLA 端口）** → **自动签发 SSL 证书（文件验证 HTTP-01）** → 生成配置 → 构建启动 → 健康检查。全程写入 `install.log`。

### 🌐 nginx 接管 80 / 443（`edge.sh`）

FLA 自己跑在 **8306**，但**所有指向本机的域名**都由宿主机 nginx 收下并反代过去——这样微软 Office 在线放映要求的"域名 + 80/443 公开直链"天然满足，不用把 FLA 塞进 80。

```bash
sudo bash edge.sh setup 域名A 域名B              # 为这两个域名装反代: 80/443 → 127.0.0.1:8306
sudo bash edge.sh --port 8310 setup 域名A 域名B   # FLA 不在 8306 时指定反代端口
sudo bash edge.sh status                          # 网关配置/证书/端口/监听/是否还是欢迎页
sudo bash edge.sh doctor                          # ★ 打开不是 FLA? 一次查清原因并给出命令
sudo bash edge.sh fix                             # ★ 自动修复(中和发行版默认站点 → 重载 → 复验)
sudo bash edge.sh reload                          # 重新生成配置并重载(先 nginx -t 自检)
sudo bash edge.sh remove                          # 卸掉网关并还原被停用的默认站点(FLA 仍可 8306 直连)
```

- 生成 `/etc/nginx/conf.d/fla-edge.conf`，**两层结构**：
  1. **域名专用 server**（`server_name 域名A www.域名A 域名B www.域名B;`，80 与 443 各一个）→ 按 Host 精确匹配反代到 `127.0.0.1:8306`。**即使别处存在 `default_server` 冲突，这两个域名也照样生效**
  2. **catch-all server**（`server_name _;` + `listen 80 default_server;`）→ 其余任意域名/IP 也落到 FLA，不留发行版欢迎页。**只有在确认没有别处声明 `default_server` 时才生成**，否则 `nginx -t` 会因 *duplicate default server* 失败导致整站起不来
- 公共反代段：WebSocket 升级头、`X-Forwarded-For/Proto/Host/Port`、`/.well-known/acme-challenge/` 走本地 webroot、`client_max_body_size 2048m`、`proxy_buffering off`（音视频 Range 流畅）、`proxy_request_buffering off`（大文件不先落盘）、gzip、HSTS
- **跳 https 时豁免 ACME 路径**：server 级 `if` 在 nginx 的 SERVER_REWRITE 阶段执行（早于 location 匹配），若直接 `if ($fla_force_https) { return 301 … }`，续期请求会被 301 到 https 而拿不到验证文件。这里用 `set $fla_redir` 三段式显式排除 `/.well-known/acme-challenge/`
- **只有拿到证书才写 443**：没有证书时不会生成 `listen 443`（否则 `nginx -t` 直接失败、整站起不来）；先用自签证书兜底，正式证书签发后自动替换
- http2 写法按 nginx 版本自适应：≥1.25.1 用 `http2 on;`，更老的用 `listen 443 ssl http2;`
- **自动让开发行版默认站点**（这一步决定"打开是不是 Welcome to nginx!"）：
  - `conf.d/default.conf`、`sites-enabled/default`、`default.d/*.conf` → **移出**到 `/etc/nginx/fla-disabled/`（Debian 的 `include sites-enabled/*` 是通配符，就地改名等于没停用），并记 `manifest`，`remove` 时精确还原
  - **RHEL/CentOS/Alma/Rocky/Fedora 把欢迎页站点直接写在 `/etc/nginx/nginx.conf` 里**（`listen 80 default_server; root /usr/share/nginx/html;`）→ 用 awk 按花括号配对把整个 `server{}` 块（含嵌套 `location`）注释掉，先备份 `nginx.conf.fla-bak`，幂等可重跑，Debian 型 `nginx.conf` 不会被误伤
- **装完自动落地复验**：本机带 `Host:` 头请求 80/443，识别返回的到底是 FLA、`Welcome to nginx!`、502（容器没起）还是端口没监听，并直接给出对应补救命令
- 没有证书时先用**自签引导证书**把 443 撑起来（`/etc/fla/ssl/`），签好真证书自动替换
- 状态记录在 `/etc/fla/edge.state`；反代目标端口跟着 FLA 实际端口走
- **换端口**：`sudo bash install.sh --port 8310`，或改 `deploy/.env` 里的 `PORT` 后 `sudo bash run.sh restart && sudo bash edge.sh setup 域名`

### 🔒 SSL 证书：文件验证，全自动（`https.sh`）

```bash
sudo bash https.sh                      # 交互输入域名(可多个), 自动签发 + 自动续期
sudo bash https.sh class.fyx.best       # 直接指定域名
sudo bash https.sh --yes 域名           # 免交互(安装脚本内部就是这么调的)
sudo bash https.sh --status             # 看证书到期时间/续期任务
sudo bash https.sh --renew              # 手动续期一次(平时不用管)
```

- **只用文件验证（HTTP-01 webroot `/var/www/fla-acme`）**：不停 80 端口、不抢占 nginx，正在上课也能签
- 优先 `certbot`，没有就自动装；装不上再退到 `acme.sh`（都不行会明确告诉你为什么，例如域名没解析到本机）
- 签发成功后自动：写入 `PUBLIC_BASE_URL=https://域名`（微软放映直链、扫码登录、分享链接都用它）→ 装续期钩子 → **重建 app 容器**让配置生效
- **自动续期**：cron + systemd timer 双保险（每天 03:17 / 15:17 各试一次），续期后自动重载 nginx
- 证书放在 `/etc/letsencrypt/live/域名/`，并复制到 `/etc/fla/ssl/` 供网关使用

### 🧨 彻底重装（`completely_new_install.sh`）

**删掉所有 Docker 容器 + Docker 本身，再从零装一遍。** 用于 Docker 坏掉、iptables 混乱、内核升级后容器起不来等"救不回来"的场景。

```bash
sudo bash completely_new_install.sh                # 交互确认(要手打 YES-DELETE-ALL)
sudo bash completely_new_install.sh --keep-data    # 保留 fla-data 卷
sudo bash completely_new_install.sh --no-backup    # 连自动备份都不做(不可恢复!)
sudo bash completely_new_install.sh --keep-docker  # 只清空容器/镜像/卷, 不卸载 docker 本体
sudo bash completely_new_install.sh --skip-install # 只清理, 不重装
sudo bash completely_new_install.sh --force        # 完全无人值守(危险)
# 其余参数原样转给 install.sh:
sudo bash completely_new_install.sh --port 8306 --domain t.clrv.top --lite
```

会依次：**先把 `fla-data` 备份成 `fla-backup-*.tar.gz`**（默认行为，`--no-backup` 可关）→ 停并删**所有**容器（不止 FLA）→ 删所有镜像/卷/网络 → 卸载 docker / docker-ce / containerd 及残留配置 → 清 `/var/lib/docker`、`/etc/docker` → 重新安装 Docker + compose → 接着按你给的参数跑 `install.sh`。全过程写 `reinstall.log`。**不会动**：本目录源码与 `deploy/.env`、宿主机 nginx 网关与 Let's Encrypt 证书、已有的备份文件。

### 🖥 自动后台保活（screen）

输入管理员密码后，安装会**自动转入 screen 会话**后台执行（没有 screen 自动安装/降级 tmux/nohup）——**SSH 断开、关掉电脑都不影响安装**：

```bash
screen -r fla-install   # 实时查看安装画面 (离开按 Ctrl+A 再按 D)
tail -f install.log     # 或者看日志文件
```

### 🔄 智能重建 + 三级自愈（可放心反复运行）

重复运行 `install.sh` 会自动清理旧的 FLA 容器后重建，**数据卷固定名 `fla-data`，用户/课件/批注全部保留**。与 FLA 无关的同名容器会被**改名保留**而不是删除。镜像层有缓存，重跑很快。

启动后如果服务访问不通，脚本会**自动逐级降级自救**，并把诊断日志写进 `install.log`：

1. nginx 容器异常 → 自动重启一次 nginx
2. 仍不通 → 切换**直连模式**（app 容器直接发布端口，绕过 nginx；自带 OnlyOffice 时 DS 也直接发布端口）
3. 仍不通 → 重启 docker 服务修复 iptables/防火墙规则（容器会自动重启）
4. 全部失败 → 把所有容器日志打包进 install.log 后才报错

最终以哪种模式运行，`run.sh status` 一目了然。

### 🔑 已有 OnlyOffice？直接复用（你的配置优先）

检测到本机**正在运行**的 OnlyOffice 容器时自动复用（保留你的配置）：自动读取 JWT 密钥（环境变量或 local.json）与端口、健康检查、设置开机自启、写好对接配置。`--ds-new` 强制用自带的 · `--ds-external` 强制复用（含已停止的）· `--ds-port N` / `--ds-secret X` 手动指定。未开启 JWT 的 DS 也能以无签名模式对接。

### 🔄 在线更新：update.sh

只更新代码、不动配置和数据（**推荐日常用这个**）：

```bash
cd /root/FLA            # 进入部署目录 (当初 install.sh 所在目录)
sudo bash update.sh     # 备份数据库 → 拉取当前分支最新代码 → 重建容器 → 健康检查
```

常用变体：

```bash
sudo bash update.sh --branch arena/63f6f4b2-fla   # 指定要拉的分支
sudo bash update.sh --no-pull                     # 不拉代码, 只用当前目录代码重建(手工覆盖文件后)
sudo bash update.sh --no-backup                   # 跳过数据库备份
sudo bash update.sh --full                        # 改成全量重装(等价 install.sh, 会重装网关/证书, 较慢)
sudo bash update.sh -y                            # 不询问确认
```

- 备份落在 `backup/app-<时间戳>.db`（保留最近 10 份），日志写在 `update.log`
- 数据库在 Docker 卷 `fla-data` 里，更新过程**从不会重建/删除该卷**，用户与课件不丢
- `deploy/.env`（端口/域名/密钥）与 `deploy/.compose_*` 不会被覆盖
- 若服务器不是 git 仓库，会自动改用 GitHub 归档包并保留上述本地配置；私有仓库请先配好 git 凭据或设置 `GITHUB_TOKEN`
- 更新完前端一般自动生效；个别浏览器顽固缓存时按一次 **Ctrl+F5**

### 🛠 日常管理：run.sh

```bash
sudo bash run.sh status    # 查看容器与服务状态(含网关/证书)
sudo bash run.sh start     # 启动
sudo bash run.sh stop      # 停止(数据不丢)
sudo bash run.sh restart   # 重启
sudo bash run.sh logs      # 实时日志 (可指定 app|nginx|documentserver)
sudo bash run.sh update    # 旧写法: 全量重装(等价 install.sh); 日常更新请用 update.sh
sudo bash run.sh edge      # 宿主机 nginx 网关(80/443) → 转 edge.sh status
sudo bash run.sh ssl       # 证书状态/到期时间 → 转 https.sh --status
sudo bash run.sh screen    # 查看进行中的安装会话
sudo bash run.sh doctor    # 一键诊断(收集全部日志到 doctor.log, 出问题时发给开发者)
```

### 其他选项

默认端口 **8306**（被占自动顺延）· `--port 80` 若要 FLA 直接占用 80 · `--password XXX` 指定初始密码 · `--lite` 精简模式（不用 OnlyOffice，省 2G 内存）· `--foreground` 强制前台执行（调试用）· OnlyOffice 镜像拉取失败自动降级精简模式，网络恢复后重跑即升级。

**开机自启**：容器 `restart: unless-stopped` + docker 服务 enabled（复用外部 OnlyOffice 时同样自动设置），服务器重启后整套服务自动拉起。

**排错**：任何一步失败都会打印排错建议。仍解决不了就把 `install.log` 发给开发者（自愈过程已把容器诊断日志都写进去了）。

**打开是 "Welcome to nginx!"？** 说明宿主机 nginx 还在用发行版默认站点，FLA 网关没生效（通常是 `nginx.conf` 内置的 `default_server` 与我们冲突，`nginx -t` 失败）。一条命令定位并修复：

```bash
sudo bash edge.sh doctor    # 8 段诊断: nginx 本体 / nginx -t 报错原文 / 谁在监听 80,443 /
                            # 网关配置摘要 / 谁在抢 default_server / FLA 容器与端口 / 证书 / 实际访问结果
sudo bash edge.sh fix       # 自动: 停用并中和默认站点 → 重新生成配置 → 重载 → 复验
sudo bash edge.sh setup 域名A 域名B   # 或重新指定要反代的两个域名
```

**卸载**：`sudo bash uninstall.sh`（需输入 yes）
- 默认：删 FLA 容器 + 数据卷，**镜像保留**（防止误删你自己改过的镜像）
- `--images` 连镜像一起删 · `--edge` 连宿主机 nginx 网关与证书一起卸 · `--keep-data` 只删容器保留数据 · `--backup` 先把 `fla-data` 打包到当前目录再删

> 首次登录后请立即修改 admin 密码。备份 Docker 卷 `fla-data` 即备份全部数据。

---

## 二、功能清单

### 🎬 微软放映舞台（v1.27 · 板书画布严格随页切换）

Office 课件用**微软 Office 在线视图**打开/放映（真字体、真动画）。它有个天生的硬伤：微软画面是**跨域 iframe**，父页面既读不到它当前在第几页，也没法命令它翻页——所以老版本的板书画布永远停在第一页，翻几页后板书和画面完全脱节。

v1.27 的解法（`web/js/msstage.js` + `server/msview.py` + `GET /api/files/{id}/ms-view`）：

| 能力 | 说明 |
|---|---|
| **我方掌握页码** | 翻页 = 换 `iframe.src` 的定位参数（`wdStartOn` / `wdSlideId`，slide id 从 pptx 的 `sldIdLst` 里真读出来）。页码是我方状态，**画布层自然严格跟着走** |
| **翻页不卡** | 双 iframe 乒乓 + **后台预载下一页**，220ms 交叉淡入，不闪黑屏；`src` 不变 → 微软侧转换缓存可复用，不用重新转换 |
| **画布对准幻灯区域** | 按文档**真实宽高比** letterbox 计算板书区域，笔迹落在幻灯片上而不是黑边；还能**手动微调**（拖动/四角缩放，存到服务器，换设备一致） |
| 缩略图导航（G） | pdf.js 懒加载真实缩略图 + 有板书的页打点标记，点一下就跳页 |
| 附加板书页 | 幻灯片讲完了继续加空白板书页（白/黑板/绿黑板），与幻灯页数互不干扰 |
| 板中板 | 从顶部滑落的独立小黑板（可加页、可清空），不遮挡主画面 |
| 全套板书工具 | 选择/笔/荧光笔/几何图形（直线·箭头·矩形·椭圆·三角形，45° 吸附）/文本/激光笔/橡皮 + 撤销重做 + 选中拖动 |
| **两种同步模式** | **我方驱动 deep**（默认，‹ › / 缩略图 / 数字键直接翻页，画布同步）· **微软自翻 follow**（老师点微软画面翻页，动画最顺，板书用 ‹ › 或 Ctrl+← → 对齐） |
| 首次对齐自检 | 第一次进放映会提示"翻一页看微软画面有没有跟着跳"，没跟着跳就一键切到 follow 模式并记住选择 |
| 兜底 | 顶栏「本地引擎」随时切到完全离线的高保真渲染（LibreOffice→PDF，含自研动画放映） |
| 快捷键 | ← → 空格翻页 · 1–7 选工具 · Ctrl+Z/Y 撤销重做 · G 缩略图 · B 黑屏 · F 全屏 · Esc 逐级退出 |

> **前提**：微软抓取器要求直链是 **域名 + 80/443**。装好 `edge.sh` + `https.sh` 后自动满足（`PUBLIC_BASE_URL` 会写进配置）。用 IP 或 8306 直连时，界面会明确提示，并可一键切「本地引擎」。

### 🧰 课堂工具（v3.6 · 抽人 / 计时 / 分组 / 计分 / 表决 / 函数图像）

`web/classroom.html` 是一个**独立、自包含、断网可用**的课堂工具页，桌面端首页与导航都能一键打开：

| 工具 | 说明 |
|---|---|
| 随机抽人 | 滚动抽取，1/2/3/5 人，「不重复」模式，历史记录，空格开始/停止 |
| 随机分组 | 按组数或每组人数分配，结果可复制 |
| 计时器 | iOS 式三列滚轮（拖动惯性吸附）+ ± 时/分/秒 + 快捷预设 + 全屏投影 |
| 计分板 | 分组加减分、自动排序、大屏显示 |
| 表决器 | A/B/C/D/弃权，键盘 1–5 计票 |
| 函数图像 | 表达式即画图，拖动平移、滚轮缩放（自写解析器，不使用 `eval`） |

放映页顶栏也加了「抽人」按钮，按 `R` 键即可在放映过程中直接抽人；三处共用同一份本地名单
（`localStorage` 键 `fla_picker_roster`）。桌面端「系统维护」页另有**希沃全面消杀**与
**重启还原（影子系统）**，见 `desktop/README.md`。

### ✏️ 白板（教室模式）

| 工具 | 说明 |
|---|---|
| 选择 (1) | 套索圈选任意元素（笔迹/图形/文本）→ 批量**复制 / 拖动 / 角点缩放 / 删除**；再点图标弹出**全选 / 删除所选**；Ctrl+A 全选、Delete 删除；此状态**双指缩放**整个画面 |
| 笔 (2) | 再次点击图标弹出**颜色 + 粗细** |
| 荧光笔 (3) | 半透明高亮，**颜色 + 粗细** |
| 几何图形 (4) | **直线 / 箭头 / 矩形 / 椭圆 / 三角形**，直线与箭头自动吸附横平竖直与 45°，颜色 + 粗细可调 |
| 文本 (5) | 点击画面输入文字，Enter 确认；**双击已有文字可再编辑**；颜色 + 字号可调 |
| 激光笔 (6) | 红色光点拖尾强调，不留下笔迹，700ms 淡出 |
| 橡皮 (7) | **对象橡皮 / 像素橡皮**双模式（像素橡皮只擦划过部分、笔迹自动切断），粗细可调 + **清空本页**（即刻清屏、可撤销） |
| 撤销 / 取消撤销 | Ctrl+Z / Ctrl+Y，全局历史（含加页） |
| 翻页 / 加页 | 按钮或 ← → / PgUp PgDn；加页在当前页后插入并立即跳转 |
| **无限画布** | 新建的白板与所有空白页均为无限画布：自由平移缩放、细点阵网格、随意书写，截图自动截取可视区域 |
| 新建白板 | 课件库一键「新建白板」直接开讲 |
| 截图 | 当前页 + 批注合成 PNG，下载或存回「我的课件」 |
| 白板底色 | 空白页一键切换 白 / 黑板 / 绿黑板 |
| 课堂倒计时 | 顶栏 ⏱，大数字显示，最后 1 分钟变红，可暂停 |
| 全屏 | 顶栏按钮，教室投影必备 |
| 音视频 | 播放控制与白板一键互切，可在视频画面上写画 |
| **动画放映** | Office 文档一键切换 OnlyOffice 放映（原样动画），批注工具叠加其上，🔒 切换放映控件 |
| 配置记忆 | 颜色/粗细/字号/图形类型自动记住，下次打开延续 |

所有批注按页自动保存到服务器，任何设备登录都能继续。

### 💬 聊天（v1.27 · 微信级）

| 分类 | 能力 |
|---|---|
| 会话 | **私聊**（1:1，重复发起复用同一会话）· **群聊**（自建/邀请/官方大厅）· 会话列表带头像、最后一条摘要（`[图片]` `[语音]` `[文件]`）、相对时间 |
| 未读 | 每会话独立未读数 → **导航栏角标**（20 秒轮询）；**免打扰**的会话只显小红点不显数字；**置顶**会话排最前 |
| 已读回执 | 群里显示「已读 n/m」，私聊显示「已读 / 未读」（由 `chat_members.last_read` 推出，零额外写入） |
| 正在输入 | 输入时上报（2 秒节流），对方会话与标题栏显示「xx 正在输入…」，6 秒时效 |
| 消息类型 | 文本（2000 字）· **图片**（点击看大图/下载）· **文件**（200MB，带大小与下载）· **语音**（MediaRecorder 录制，自制播放条 + 时长，60 秒上限）· 系统灰条（进群/退群/改名/转让） |
| 发送方式 | 按钮选择、**拖拽进窗口**、**Ctrl+V 直接粘贴截图**，带上传进度条 |
| 引用回复 | 引用条显示原文摘要，点击可**跳转定位**（不在当前页会自动向前翻并高亮闪烁） |
| 表情回应 | ❤ 👍 😂 🎉 👀 🙏 一键回应，再点取消，多人自动聚合成计数；可查看"谁回应了" |
| @提醒 | 输入 `@` 弹出**成员自动补全**（方向键/Tab 选，Esc 关），被 @ 的人会话列表显示红色「[有人@我]」 |
| 撤回/编辑 | 自己 **2 分钟内可撤回**（全员看到"撤回了一条消息"）；文字消息可编辑（标「已编辑」）；管理员可代管任意消息（标「已编辑·管理员」） |
| 群管理 | 群名/群公告（群主可改）· **我在本群的昵称** · 邀请成员 · 移出成员 · **转让群主** · 退群（群主需先转让）· 解散 |
| 查找 | 侧栏搜索框：先过滤会话，再**全文搜索聊天记录**（关键词高亮，点击定位到那条消息） |
| 历史 | 向上滚动自动加载更早消息（保持视口不动）；增量拉新 2.5 秒；**页面隐藏自动停轮询**，回到前台立即补拉 |
| 通知 | 可选**桌面通知**（需授权，只在切到别的标签页时提醒，免打扰会话不提醒） |
| 窄屏 | 单列布局：列表 ↔ 会话滑动切换，顶栏返回按钮 |
| 后台管控 | 聊天总开关 · 建群权限 · 单人禁言 · 解散任意群 |

### 📁 课件与文件
- 类型：`ppt/pptx/pps/ppsx/pot/potx`、`doc/docx/dot/dotx/rtf/txt`、`xls/xlsx/csv`、`odt/ods/odp`、`pdf`、`png/jpg/jpeg/webp/gif/bmp/svg`、`mp3/wav/ogg/m4a/aac/flac`、`mp4/webm/mkv/mov/m4v/avi/wmv/flv/ts/3gp`
- Office 文档服务端 LibreOffice 自动转高保真 PDF；图片原生放映（可加板书页）；**非浏览器通用格式（mkv/mov/wmv/avi/flv/ts/3gp…）服务端 ffmpeg 自动转 MP4** 后可直接播放（开源离线，不依赖外部 API）；**直接上传的 PDF 自动统计页数**（旧数据由前端打开时自动校正）
- **公开直链**：`/api/files/share/FYXxxxxxxxx.pptx`（纯 ASCII，微软抓取器不接受中文路径），免登录可取，支持 HEAD 与 Range
- **字体保障（不出方框）**：内置思源黑体/宋体、霞鹜文楷等开源字体；fontconfig 把微软雅黑/宋体/黑体/楷体/仿宋/隶书等映射为度量相近的开源字体（商业字体无法合法分发）；转出的 PDF **内嵌字体**，任何设备显示一致；启动时自动检测并联网补齐缺失的开源字体；上传时提示文档引用了哪些未安装字体

### 👥 用户系统
- 注册需**邀请码**；bcrypt 密码加密 + JWT 令牌
- 头像 / 昵称 / 个性签名设置
- 教师默认 500MB 空间（后台可改默认值与单人配额）
- **扫码登录**：登录页「密码 / 扫码」双页签；二维码本地生成（开源 qrcode.js，不连外部服务）；已登录设备顶栏扫码图标 → 相机扫码（jsQR）或手动输票据授权；票据 150 秒有效、一次作废

### 🏅 认证证书（v1.27 大升级）

| 要素 | 说明 |
|---|---|
| **等级体系** | 站长 = **铂金极光**（PLATINUM AURORA）；教师按认证颜色自动判级：**黄金 / 白银 / 青铜 / 墨金 / 蓝宝 / 翡翠 / 定制** |
| **3D 全息** | 鼠标跟随**倾斜 + 分层视差**（奖章浮得比证书高）；**全息箔层**随倾斜角度流动变色（真证书上那层彩虹膜） |
| 工艺细节 | 雕刻底纹（guilloché）· 旋转光束 · 三重光环 · 呼吸光晕 · 星芒 · 扫光 · 四角压边 · 绶带 · 骑缝章 |
| **正式要素** | 等级条 / 证书编号 `NO. FLA-0001` / 签发日期 / 授权范围（站长）或持证人（教师）/ **校验码** |
| **二维码核验** | 懒加载本地 qrcode.js，编码「编号 + 称号 + 等级 + 签发日 + 校验码 + 站点地址」，扫码即见 |
| 校验码 | `FNV-1a(id \| 角色 \| 教师标记 \| 称号 \| 签发日)`，同一账号永远一致，可对账 |
| **查看大图** | 全屏弹层放大展示（Esc / 点空白关闭） |
| **打印 / 存 PDF** | `@media print` 只印证书本体（隐藏顶栏与页面其余部分），浏览器打印对话框里可直接"另存为 PDF" |
| 徽章 | 导航栏/论坛/聊天里的行内徽章：金属渐变文字 + 流光扫过 + 悬停微放大；站长为铂金银白渐变 |
| 后台 | 编辑用户时**证书实时预览**：改称号/图标/颜色/角色/教师开关，证书当场重绘（所见即所得） |

### 🛠 管理后台（`#/admin`）
数据概览（含论坛/聊天/公告统计）/ 用户管理（搜索、编辑资料头像、改配额、改角色、**认证称号·图标·颜色 + 证书实时预览**、聊天禁言、重置密码、管理其文件、删除）/ 邀请码（单个自定义 + 批量生成 + 次数/有效期/备注 + 删除）/ **公告管理**（全局/指定用户专属，三级级别，启用停用）/ **论坛管理**（板块增删改名、置顶/锁定/删帖）/ **聊天管理**（开关、建群权限、解散群组）/ 系统设置（默认空间、单文件上限、开放注册、公开访问地址、论坛/聊天/建群开关）

### ✨ 界面与动效（v1.27）
页面与卡片**交错入场**、卡片悬停高光扫过 + 封面轻放大、按钮按压即时反馈、弹窗弹簧缓动、全站细滚动条、上传进度条条纹流动、顶栏滚动后收紧、空状态呼吸；所有动画只动 `transform / opacity`（GPU 合成，不触发重排），并**全站尊重 `prefers-reduced-motion`**（系统开了"减少动效"就全部关闭）。

---

## 三、目录结构

```
FLA/
├── install.sh                 ← 一键部署/智能重建(含 nginx 网关 + SSL, 自动进 screen 保活)
├── edge.sh                    ← v1.27 宿主机 nginx 网关: 占 80+443 catch-all → 反代 FLA 端口
├── https.sh                   ← v1.27 SSL 证书: 文件验证(HTTP-01 webroot) + 自动续期
├── update.sh                  ← 在线更新: 备份数据库 → 拉代码 → 重建容器 → 健康检查(仅更新, 不改配置)
├── run.sh                     ← 日常管理: status/start/stop/restart/logs/update/edge/ssl/doctor
├── completely_new_install.sh  ← v1.27 删掉所有容器 + Docker 本体, 再从零重装
├── uninstall.sh               ← 卸载(--images/--edge/--keep-data/--backup)
├── server/               FastAPI 后端 (SQLite, 无外部数据库依赖)
│   ├── msview.py         v1.27 微软在线视图对接(深链/slide id/宽高比/直链可用性)
│   ├── converter.py      LibreOffice 转换队列 + 字体检测/自动下载
│   ├── pptx_anim.py      pptx 动画解析(自研放映)
│   └── routers/          auth / users / files(含 OnlyOffice + ms-view) / admin
│                         / announcements / social(论坛) / chat(v1.27 微信级聊天)
├── web/                  前端 (原生 JS, 无构建步骤; Chrome/Edge 90+、Firefox 90+、Safari 15+)
│   ├── js/viewer.js      白板引擎(核心) + 课件查看器
│   ├── js/msstage.js     v1.27 微软放映舞台(画布随页切换, 放映页与浏览页共用)
│   ├── js/chat.js        v1.27 微信级聊天
│   ├── js/present.js     放映页(自研引擎; 微软轨道委托 msstage.js)
│   └── lib/              本地 pdf.js / qrcode / jsQR / tesseract(全部离线)
├── deploy/
│   ├── Dockerfile        app 镜像 (Python + LibreOffice + 中文字体, --proxy-headers)
│   ├── docker-compose.yml        完整版(含 OnlyOffice)
│   ├── docker-compose.lite.yml   精简版
│   ├── docker-compose.direct.yml 直连备用版(自愈降级时自动启用)
│   ├── nginx-app.conf    容器内 nginx(→ app:8000, WebSocket 升级)
│   ├── nginx/            统一入口(打包进镜像, 规避 SELinux 问题)
│   └── fonts/            商业字体→开源字体替换规则
└── tests/smoke.py        322 项 API 自动化冒烟测试(不需要 Docker/LibreOffice/外网)
```

### 跑测试

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt httpx
./.venv/bin/python tests/smoke.py
```

在临时数据目录里拉起 FastAPI（Starlette TestClient），覆盖：

| 段 | 内容 |
|---|---|
| 1 | 健康检查、管理员登录、错误密码被拒 |
| 2 | 上传真 pptx → **微软视图对接**：直链域名/443 判定（`ms_ok`）、页数取自 `sldIdLst`、真实 slide id、16:9 宽高比、深链模板 `{n}`/`{id}`、`wdStartOn`/`wdSlideId` 落位、`src=` 只出现一次（翻页不重新转换） |
| 3 | 公开直链：免登录下载、HEAD 探测、Range 分段、未登录访问 `ms-view` 被拒 |
| 4 | 批注往返（含 v1.27 `ms` 段：板书区域 / 同步模式 / 附加板书页），以及不带 `ms` 段的向后兼容 |
| 5 | 白板新建、邀请码注册、教师/站长认证发放与回读、非法图标颜色被拒 |
| 5b | **聊天全链路**：通讯录、私聊复用会话、未读汇总、已读回执、正在输入、建群、@提醒、引用、回应、编辑、撤回窗口、免打扰、置顶、邀请/移出/转让/退群、历史分页、搜索 |
| 5c | 附件：图片 / 语音 / 文件、危险扩展名拦截、越权取附件拦截 |
| 5d | 权限与开关：禁言、聊天总开关、各模块不回 5xx |
| **5e** | **其余接口补测**：资料改昵称签名、头像上传与静态取回、改密码、**扫码登录全链路**（票据→批准→取 token→一次作废）、公告管理端 CRUD + 用户端已读、论坛板块/发帖/回复/编辑/置顶锁定/删除的权限矩阵、邀请码批量生成与删除、管理端查他人文件与重置密码、文件 `raw`/`download`/`pdf`/`anim`/OnlyOffice 优雅降级、官方大厅 join/退群限制 |
| 6 / 6b | 前端资源发布检查（msstage/chat/证书样式与脚本）+ **认证证书**（等级算法、FNV-1a 校验码、3D/二维码/大图/打印、后台实时预览） |
| 6c | **安全**：存储型 XSS 防护（弹窗标题默认转义、板块名/帖子正文/聊天正文/系统灰条/群名昵称签名均先转义再渲染） |
| 6d | 样式完整性：后台「编辑用户」弹窗（认证图标/颜色选择器的**选中态**此前完全无样式）、聊天引用条图标与置顶/免打扰标签；并检查新 CSS 特性有老浏览器兜底 |
| 7 | 删除课件与级联清理 |
| **8** | **部署脚本静态校验**：所有 `.sh` 过 `bash -n`；**真渲染** `edge.sh` 生成的 nginx 配置并逐条断言 5 种组合（占位符无残留、大括号平衡、**域名专用块精确列出两个域名+www**、**同一端口只有一个 `default_server`**、catch-all `server_name _`、反代 `127.0.0.1:PORT`、WebSocket 升级头、`X-Forwarded-Proto`、上传体积、ACME 目录、**跳 https 时豁免 ACME 路径**、force-https map、新/老 nginx 的 http2 写法、**无证书时绝不写 `listen 443`**、**检测到冲突时不生成 catch-all**、换端口跟随、纯 IP 可用）；**把中和 `nginx.conf` 内置欢迎页站点的 awk 抽出来跑真 RHEL 配置样本**（整段注释、嵌套 location 计入、活动配置再无 `default_server`、`include conf.d` 与 `http/events` 完好、Debian 型样本不误伤）；停用默认站点必须移出 include 范围 + `remove` 按 manifest 还原 + 还原 `nginx.conf` 备份 + 有 `doctor`/`fix` + 复验能识别 `Welcome to nginx`；`https.sh` 必须用 `--webroot` 文件验证且**真实命令里不出现 `--standalone`**、webroot 与 edge.sh 一致、certbot→acme.sh 降级、预检、cron+timer 续期、写 `PUBLIC_BASE_URL`；`install.sh` 自动串起 edge/https、默认 8306 顺延；`completely_new_install.sh` 删所有容器+镜像+卷、卸 docker 本体、清 `/var/lib/docker`、默认先备份、确认词、转交 install.sh；容器内 nginx 的 WS 升级与 `app:8000` |

**89 个后端路由全部被覆盖**，任一步失败即非 0 退出。

---

## 四、已知边界（诚实说明）

1. **微软在线放映的定位参数**：`wdStartOn` / `wdSlideId` 是微软自己的嵌入参数，官方无文档承诺；不同时期/不同文档类型行为可能不同。因此做了三重保障：① 深链失败也能正常放映首页；② **首次对齐自检**引导老师确认；③ 随时可切 **follow 模式**（微软自翻 + 板书手动对齐）或**本地引擎**（完全离线）。
2. **微软要求公开直链**：必须是域名 + 80/443（`edge.sh` + `https.sh` 装好即满足）。IP 直连或非标端口时，界面会明确提示并给出命令。
3. **动画**：静态渲染（LibreOffice→PDF）不含动画；OnlyOffice 放映与微软在线放映支持动画，但对个别复杂动画/SmartArt 的还原度略低于桌面 Office（各自引擎的能力边界）。
4. **WPS 专有格式** `.wps/.et/.dps`：LibreOffice 无滤镜，静态转换会提示"请在 WPS 中另存为 docx/xlsx/pptx"（微软在线视图仍可打开）。
5. **商业字体**（微软雅黑、方正、汉仪等）版权原因不能自动下载，系统用度量相近的开源字体替代并内嵌，视觉接近但不完全相同；走微软在线放映时用的是微软自己的字体，效果最接近桌面 Office。
6. **聊天为轮询实现**（消息 2.5s / 会话 5s / 角标 20s，页面隐藏自动暂停）：不依赖 WebSocket，学校内网与各种代理环境下更稳，代价是消息有 1–3 秒延迟。
7. **语音消息**依赖浏览器 MediaRecorder（Chrome/Edge + HTTPS 可用），Safari 录出的容器格式不同，已按 `audio/mp4` 兼容处理；不支持的浏览器会明确提示。
8. **内存**：完整版约需 3.2G+；4G 服务器建议观察，吃紧就 `--lite` 重装。

## 五、运维备忘

- 日志：`docker logs -f fla`；部署日志：`install.log`；网关日志：`/var/log/nginx/`；诊断包：`run.sh doctor`
- 更新版本：`sudo bash update.sh`（只更代码，配置/数据不动）；需要连网关证书一起重装用 `--full` 或 `sudo bash install.sh`
- PPT 显示「转换失败」：`bash bt.sh doctor`（宝塔）/ `curl localhost:8306/api/health/convert` 自查；课件详情里能看到真实原因
- 备份：备份 Docker 卷 `fla-data`（`uninstall.sh --backup` 可自动打包）
- 端口：默认 **8306**，`--port N` 修改；改了记得 `sudo bash edge.sh setup 域名` 让网关跟上
- 域名/证书：`sudo bash https.sh 域名`（文件验证，不停服）· `sudo bash https.sh --status` 看到期时间 · 续期全自动（03:17 / 15:17）
- 网关：`sudo bash edge.sh status` / `reload` / `remove`
- 微软放映直链不对：后台「系统设置 → 公开访问地址」或 `deploy/.env` 的 `PUBLIC_BASE_URL` 填 `https://你的域名`，然后 `sudo bash run.sh restart`
- 彻底救不回来：`sudo bash completely_new_install.sh`（**删掉所有容器和 Docker 本身**；会先自动备份 `fla-data`）
