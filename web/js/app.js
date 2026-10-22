/* FLA - 前端主应用: 现代化桌面端工作台 / 课件库 / 桌面客户端中心 / 论坛 / 个人中心 */
'use strict';
const $ = (s, el) => (el || document).querySelector(s);
const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
const toast = (m, t) => UI.toast(m, t);
window.toast = toast;

window.App = {
  user: null,
  files: [],
  filteredFiles: [],
  selectedFids: new Set(),
  searchTerm: '',
  filterKind: 'all',
  sortBy: 'date_desc',
  viewMode: (function() {
    try { return localStorage.getItem('fla_view_mode') || 'grid'; } catch(e) { return 'grid'; }
  })(),
  desktopReady: false,
  inspectingFile: null,
  setCleanup(fn) { _cleanup = fn; }   /* v3.6 修: 原先写到 App._cleanup, 路由根本不会执行 */
};

let _cleanup = null;
let pollT = null;
let desktopProbeTimer = null;

const ACCEPT = '.ppt,.pptx,.pps,.ppsx,.pot,.potx,.doc,.docx,.dot,.dotx,.rtf,.xls,.xlsx,.csv,.txt,.odt,.ods,.odp,.wps,.et,.dps,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.svg,.mp3,.wav,.ogg,.m4a,.aac,.flac,.mp4,.webm,.mkv,.mov,.m4v';

/* ================================================================
 *  1. 客户端与本地 8307 桥接探测 (桌面端实时联通状态)
 * ================================================================ */
async function probeDesktopClient() {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 1200);
    const r = await fetch('http://127.0.0.1:8307/api/status', { signal: ctrl.signal }).catch(() => null);
    clearTimeout(tid);
    if (r && r.ok) {
      App.desktopReady = true;
      updateDesktopStatusUI();
      return true;
    }
  } catch (e) {}
  App.desktopReady = false;
  updateDesktopStatusUI();
  return false;
}

function updateDesktopStatusUI() {
  const pill = $('#top-desktop-status');
  if (pill) {
    if (App.desktopReady) {
      pill.innerHTML = '<span class="dh-dot online" style="display:inline-block;margin-right:5px;"></span>客户端已就绪';
      pill.className = 'btn sm soft';
      pill.title = 'FLA 桌面助手正在运行 (127.0.0.1:8307 桥接已连接)';
    } else {
      pill.innerHTML = UI.icon('laptop', 14) + ' <span>桌面端</span>';
      pill.className = 'btn sm ghost';
      pill.title = '未检测到本地桌面客户端 (点击进入桌面端中心)';
    }
  }
}

/* ================================================================
 *  2. 应用初始化与全局路由
 * ================================================================ */
async function initApp() {
  window.addEventListener('hashchange', route);
  // 客户端(Windows/安卓)免登录跳转: #/auth?token=...&to=library  (token 只在 hash 里, 不会发给服务器日志)
  try {
    const m = /^#\/auth\?(.*)$/.exec(location.hash || '');
    if (m) {
      const q = new URLSearchParams(m[1]);
      if (q.get('token')) API.setToken(q.get('token'));
      history.replaceState(null, '', location.pathname + '#/' + (q.get('to') || 'library').replace(/[^a-z0-9/_-]/gi, ''));
    }
  } catch (e) { }
  // 手机 App / 主屏「FLA」里: 右下角常驻「投屏」按钮, 随时回到投屏/连接电脑
  try {
    const inApp = /FLA-App/.test(navigator.userAgent) || navigator.standalone || (window.matchMedia && matchMedia('(display-mode: standalone)').matches);
    if (inApp) {
      const a = document.createElement('a');
      a.href = /FLA-App/.test(navigator.userAgent) ? 'https://app.fla/home.html' : '/cast.html';
      a.id = 'fla-cast-fab';
      a.textContent = '投屏';
      a.style.cssText = 'position:fixed;right:16px;bottom:calc(22px + env(safe-area-inset-bottom));z-index:9998;background:#000;color:#fff;border-radius:999px;padding:12px 18px;font-size:15px;font-weight:600;box-shadow:0 6px 20px rgba(0,0,0,.3);text-decoration:none';
      document.body.appendChild(a);
    }
  } catch (e) { }
  if (API.token) {
    try {
      App.user = await API.get('/api/auth/me');
    } catch (e) {
      API.setToken('');
      App.user = null;
    }
  }
  if (window.Chat && App.user) Chat.startBadge();
  
  // 顶栏滚动收紧
  let stickRaf = 0;
  const onScroll = () => {
    if (stickRaf) return;
    stickRaf = requestAnimationFrame(() => {
      stickRaf = 0;
      const t = document.querySelector('.topbar');
      if (!t) return;
      const want = (window.scrollY || document.documentElement.scrollTop || 0) > 6;
      if (t.classList.contains('stuck') !== want) t.classList.toggle('stuck', want);
    });
  };
  window.addEventListener('scroll', onScroll, { passive: true });

  // 全局快捷键与拖拽
  setupGlobalShortcuts();
  setupGlobalDragDrop();

  // 桌面端状态后台探测 (仅在已登录真实浏览器中按需探测)
  if (typeof navigator !== 'undefined' && navigator.userAgent !== 'Node' && App.user) {
    probeDesktopClient();
    if (desktopProbeTimer) clearInterval(desktopProbeTimer);
    desktopProbeTimer = setInterval(probeDesktopClient, 12000);
  }

  route();
  /* 通知 index.html 看门狗: 应用已成功启动 */
  try { document.getElementById('app').dataset.booted = '1'; } catch (e) { }
}

if (window.__flaDeferInit) {
  window.__flaInit = initApp;
} else if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

function runCleanup() {
  if (_cleanup) { try { _cleanup(); } catch (e) { } _cleanup = null; }
  if (pollT) { clearInterval(pollT); pollT = null; }
}
App.runCleanup = runCleanup;

function route() {
  runCleanup();
  const rawHash = location.hash || '';
  const path = rawHash.replace(/^#\/?/, '').split('?')[0];
  const parts = path.split('/').filter(Boolean);
  
  if (parts[0] === 'remote') {
    if (window.Remote) return window.Remote.view();
  }
  if (parts[0] === 'mobile') return viewMobileCenter();
  if (parts[0] === 'desktop' && !App.user) return viewDesktopCenter();
  if (!App.user) {
    if (parts[0] === 'login') return viewLogin();
    if (parts[0] === 'register') return viewRegister();
    if (parts[0] === 'qr-approve') {
      try { sessionStorage.setItem('fla_after_login', location.hash); } catch (e) { }
      return viewLogin();
    }
    return viewHome();
  }
  if (parts[0] === 'login' || parts[0] === 'register') { location.hash = '#/library'; return; }
  if (parts[0] === 'home') return viewHome();
  refreshAnnBadge();
  if (window.Chat) Chat.refreshBadge();

  if (!parts.length || parts[0] === 'library') return viewLibrary();
  if (parts[0] === 'desktop') return viewDesktopCenter();
  if (parts[0] === 'profile') return viewProfile();
  if (parts[0] === 'forum') return viewForum(parts[1] ? parseInt(parts[1], 10) : 0);
  if (parts[0] === 'chat') return Chat.view();
  if (parts[0] === 'ai') return window.AI ? AI.view() : toast('AI 模块加载中，请稍后重试', 'err');
  if (parts[0] === 'qr-approve') return viewQrApprove();
  if (parts[0] === 'admin') {
    if (App.user.role !== 'admin') { toast('需要管理员权限', 'err'); location.hash = '#/library'; return; }
    return Admin.view();
  }
  if (parts[0] === 'view' && parts[1] && window.Viewer) { Viewer.open(parseInt(parts[1], 10)); refreshAnnBadge(); return; }
  location.hash = '#/library';
}

/* ================================================================
 *  3. 全局快捷键与全屏拖拽上传
 * ================================================================ */
function setupGlobalShortcuts() {
  document.addEventListener('keydown', e => {
    // ⌘K 或 Ctrl+K 触发搜索或桌面命令
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      const sInp = $('#lib-search-input');
      if (sInp) { sInp.focus(); sInp.select(); }
      else { location.hash = '#/library'; setTimeout(() => { const i = $('#lib-search-input'); if (i) i.focus(); }, 150); }
      return;
    }
    // / 键直接聚焦课件搜索
    if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      const sInp = $('#lib-search-input');
      if (sInp) { e.preventDefault(); sInp.focus(); }
      return;
    }
    // Esc 键关闭抽屉或取消选择
    if (e.key === 'Escape') {
      closeFileDrawer();
      if (App.selectedFids.size > 0) {
        App.selectedFids.clear();
        updateBatchBar();
        renderFileList();
      }
    }
  });
}

function setupGlobalDragDrop() {
  let dragCounter = 0;
  window.addEventListener('dragenter', () => {
    if (['#/library', '#/home'].includes(location.hash || '#/library')) {
      dragCounter++;
      showGlobalDropzone(true);
    }
  });
  window.addEventListener('dragleave', () => {
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      showGlobalDropzone(false);
    }
  });
  window.addEventListener('dragover', e => { e.preventDefault(); });
  window.addEventListener('drop', e => {
    e.preventDefault();
    dragCounter = 0;
    showGlobalDropzone(false);
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      if (!App.user) {
        toast('请先登录后再上传课件', 'warn');
        return;
      }
      if (location.hash !== '#/library') location.hash = '#/library';
      uploadFiles(Array.from(e.dataTransfer.files));
    }
  });
}

function showGlobalDropzone(show) {
  let el = $('#global-drop-overlay');
  if (!el && show) {
    el = document.createElement('div');
    el.id = 'global-drop-overlay';
    el.className = 'global-drop-backdrop';
    el.innerHTML = '<div class="global-drop-box">' +
      UI.icon('upload', 52) +
      '<h3>释放鼠标以上传课件到 FLA 云端</h3>' +
      '<p>支持 PPT / Word / Excel / PDF / 视频 / 音频 / 图片</p>' +
      '</div>';
    document.body.appendChild(el);
  }
  if (el) el.classList.toggle('active', show);
}

/* ================================================================
 *  4. 通用辅助与认证徽章体系 (兼容测试脚本)
 * ================================================================ */
function avatarHTML(u, size) {
  size = size || 34;
  const st = 'width:' + size + 'px;height:' + size + 'px';
  if (u && u.avatar) return '<img class="avatar" style="' + st + '" src="' + UI.esc(u.avatar) + '">';
  const c = UI.esc(((u && u.nickname) || 'U').slice(0, 1).toUpperCase());
  return '<span class="avatar avatar-ph" style="' + st + ';font-size:' + Math.round(size * .45) + 'px">' + c + '</span>';
}

function certHTML(u) {
  if (!u) return '';
  let h = '';
  if (u.role === 'admin') h += '<span class="cert owner" title="站长认证">' + UI.icon('crown', 13) + '<em>站长</em></span>';
  if (u.is_teacher) {
    const icon = CERT_ICON_LIST.includes(u.cert_icon) ? u.cert_icon : 'medal';
    const color = /^#[0-9a-fA-F]{6}$/.test(u.cert_color || '') ? u.cert_color : '';
    h += '<span class="cert"' + (color ? ' style="--cc:' + color + '"' : '') + ' title="教师认证">' +
      UI.icon(icon, 13) + '<em>' + UI.esc(u.cert_title || '认证教师') + '</em></span>';
  }
  return h;
}

const CERT_ICON_LIST = ['medal', 'star', 'crown', 'award', 'shield', 'heart', 'zap', 'gem', 'trophy', 'flag'];
const CERT_COLOR_LIST = ['#f0d488', '#d8dee9', '#e8b48f', '#93c5fd', '#d8b4fe', '#fca5a5', '#86efac', '#fdba74'];

function hexA(hex, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return 'rgba(240,212,136,' + a + ')';
  const n = parseInt(m[1], 16);
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
}

function certCode(u) {
  const raw = 'FLA|' + (u.id || 0) + '|' + (u.role || '') + '|' + (u.is_teacher ? 1 : 0) + '|' +
    (u.cert_title || '') + '|' + String(u.created_at || '').slice(0, 10);
  let h = 0x811c9dc5;
  for (let i = 0; i < raw.length; i++) { h ^= raw.charCodeAt(i); h = (h * 0x01000193) >>> 0; }
  const s36 = (h.toString(36).toUpperCase() + '000000').slice(0, 6);
  return s36.slice(0, 3) + '-' + s36.slice(3);
}

function certTier(u) {
  if (u.role === 'admin') return { k: 'platinum', zh: '铂金', en: 'PLATINUM AURORA' };
  const c = (u.cert_color || '#f0d488').toLowerCase();
  const m = /^#([0-9a-f]{6})$/.exec(c);
  if (!m) return { k: 'custom', zh: '定制', en: 'CUSTOM EDITION' };
  const n = parseInt(m[1], 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 510, sat = mx ? (mx - mn) / mx : 0;
  if (sat < .16) return l > .62 ? { k: 'silver', zh: '白银', en: 'SILVER EDITION' } : { k: 'graphite', zh: '墨金', en: 'GRAPHITE EDITION' };
  if (r > g && g > b) return l > .58 ? { k: 'gold', zh: '黄金', en: 'GOLD EDITION' } : { k: 'bronze', zh: '青铜', en: 'BRONZE EDITION' };
  if (b > r) return { k: 'sapphire', zh: '蓝宝', en: 'SAPPHIRE EDITION' };
  if (g > r) return { k: 'emerald', zh: '翡翠', en: 'EMERALD EDITION' };
  return { k: 'custom', zh: '定制', en: 'CUSTOM EDITION' };
}

function certCardHTML(u) {
  const no = 'FLA-' + ('0000' + (u.id || 0)).slice(-4);
  const code = certCode(u);
  const tier = certTier(u);
  const since = String(u.created_at || '').slice(0, 10) || '—';
  const spark = (l, t, d, sz) => '<i class="cc-spark" style="left:' + l + '%;top:' + t + '%;animation-delay:' + d + 's;font-size:' + sz + 'px">✦</i>';
  const sparks = spark(9, 18, 0, 13) + spark(88, 13, 1.1, 10) + spark(80, 62, 2.2, 14) + spark(13, 68, .6, 10);
  let h = '';

  if (u.role === 'admin') {
    h += '<div class="cert-card tier-platinum" data-cert="owner" data-no="' + no + '" data-code="' + code + '"' +
      ' data-title="站长" data-en="FLA · SITE OWNER" data-since="' + since + '" data-tier="' + tier.en + '">' +
      '<i class="cc-guilloche"></i><i class="cc-beam"></i><i class="cc-holo"></i>' + sparks +
      '<div class="cc-inner">' +
      '<div class="cc-tier">' + UI.icon('gem', 12) + '<span>' + tier.en + ' · ' + tier.zh + '级</span></div>' +
      '<div class="cc-medal">' + UI.icon('crown', 46) + '</div>' +
      '<div class="cc-title">站长</div>' +
      '<div class="cc-sub">FLA · SITE OWNER</div>' +
      '<div class="cc-rows">' +
      '<div><span>证书编号</span><b>NO. ' + no + '</b></div>' +
      '<div><span>签发日期</span><b>' + since + '</b></div>' +
      '<div><span>授权范围</span><b>站点创始人 · 最高权限</b></div>' +
      '<div><span>校验码</span><b>' + code + '</b></div>' +
      '</div>' +
      '<div class="cc-foot"><div class="cc-qr"></div>' +
      '<div class="cc-sign"><b>FLA 官方签发</b><em>扫码核验 · ' + code + '</em></div>' +
      '<div class="cc-seal"><b>FLA</b><span>站长</span></div></div>' +
      '</div>' +
      '<div class="cc-acts"><button type="button" data-ca="zoom">' + UI.icon('maximize', 13) + ' 查看大图</button>' +
      '<button type="button" data-ca="print">' + UI.icon('download', 13) + ' 打印 / 存 PDF</button></div>' +
      '</div>';
  } else if (u.is_teacher) {
    const icon = CERT_ICON_LIST.includes(u.cert_icon) ? u.cert_icon : 'medal';
    const color = /^#[0-9a-fA-F]{6}$/.test(u.cert_color || '') ? u.cert_color : '#f0d488';
    const title = u.cert_title || '认证教师';
    h += '<div class="cert-card tier-' + tier.k + '" data-cert="teacher" data-no="' + no + '" data-code="' + code + '"' +
      ' data-title="' + UI.esc(title) + '" data-en="FLA · CERTIFIED EDUCATOR" data-since="' + since + '"' +
      ' data-tier="' + tier.en + '" style="--cc:' + color + '">' +
      '<i class="cc-guilloche"></i><i class="cc-holo"></i>' + sparks +
      '<div class="cc-inner">' +
      '<div class="cc-tier">' + UI.icon('medal', 12) + '<span>' + tier.en + ' · ' + tier.zh + '级</span></div>' +
      '<div class="cc-medal">' + UI.icon(icon, 48) + '</div>' +
      '<div class="cc-title">' + UI.esc(title) + '</div>' +
      '<div class="cc-sub">FLA · CERTIFIED EDUCATOR</div>' +
      '<div class="cc-rows">' +
      '<div><span>证书编号</span><b>NO. ' + no + '</b></div>' +
      '<div><span>签发日期</span><b>' + since + '</b></div>' +
      '<div><span>持证人</span><b>' + UI.esc(u.nickname || u.username || '') + '</b></div>' +
      '<div><span>校验码</span><b>' + code + '</b></div>' +
      '</div>' +
      '<div class="cc-foot"><div class="cc-qr"></div>' +
      '<div class="cc-sign"><b>FLA 官方认证</b><em>扫码核验 · ' + code + '</em></div>' +
      '<div class="cc-seal"><b>FLA</b><span>已认证</span></div></div>' +
      '</div>' +
      '<div class="cc-acts"><button type="button" data-ca="zoom">' + UI.icon('maximize', 13) + ' 查看大图</button>' +
      '<button type="button" data-ca="print">' + UI.icon('download', 13) + ' 打印 / 存 PDF</button></div>' +
      '</div>';
  } else {
    h += '<div class="cert-card pending">' +
      '<div class="cc-medal">' + UI.icon('lock', 28) + '</div>' +
      '<div class="cc-title">教师认证</div>' +
      '<div class="cc-sub">FLA · CERTIFIED EDUCATOR</div>' +
      '<p class="cc-note">未完成教师认证（不影响课件功能，认证后展示专属证书）</p>' +
      '</div>';
  }
  return h;
}

function bindCertCards(root) {
  const cards = $$('.cert-card:not(.pending)', root || document);
  cards.forEach(card => {
    let raf = 0;
    const move = e => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / (r.width || 1), py = (e.clientY - r.top) / (r.height || 1);
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        card.style.setProperty('--rx', ((0.5 - py) * 11).toFixed(2) + 'deg');
        card.style.setProperty('--ry', ((px - 0.5) * 14).toFixed(2) + 'deg');
        card.style.setProperty('--mx', (px * 100).toFixed(1) + '%');
        card.style.setProperty('--my', (py * 100).toFixed(1) + '%');
      });
    };
    card.addEventListener('pointermove', move);
    card.addEventListener('pointerleave', () => {
      card.style.setProperty('--rx', '0deg'); card.style.setProperty('--ry', '0deg');
      card.style.setProperty('--mx', '50%'); card.style.setProperty('--my', '50%');
    });
    const qr = $('.cc-qr', card);
    if (qr && !qr.dataset.done) {
      qr.dataset.done = '1';
      const txt = 'FLA CERTIFICATE\nNO. ' + card.dataset.no + '\n' + card.dataset.title + '\n' + card.dataset.code;
      if (UI && UI.renderQR) UI.renderQR(qr, txt, 62);
    }
    $$('.cc-acts button', card).forEach(b => b.onclick = ev => {
      ev.preventDefault(); ev.stopPropagation();
      const act = b.dataset.ca;
      if (act === 'zoom') certZoom(card);
      else if (act === 'print') certPrint(card);
    });
  });
}

function certZoom(card) {
  const ov = document.createElement('div');
  ov.className = 'cc-zoom';
  const clone = card.cloneNode(true);
  clone.classList.add('cc-big');
  clone.querySelectorAll('.cc-acts').forEach(x => x.remove());
  ov.innerHTML = '<button class="cc-zoom-x" type="button">' + UI.icon('close', 20) + '</button>';
  ov.appendChild(clone);
  document.body.appendChild(ov);
  requestAnimationFrame(() => ov.classList.add('on'));
  bindCertCards(ov);
  const close = () => { ov.classList.remove('on'); setTimeout(() => ov.remove(), 280); };
  ov.addEventListener('click', e => { if (e.target === ov || e.target.closest('.cc-zoom-x')) close(); });
  const onKey = e => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
}

function certPrint(card) {
  const ov = document.createElement('div');
  ov.className = 'cc-print';
  const clone = card.cloneNode(true);
  clone.querySelectorAll('.cc-acts').forEach(x => x.remove());
  ov.appendChild(clone);
  document.body.appendChild(ov);
  bindCertCards(ov);
  const done = () => { setTimeout(() => ov.remove(), 400); };
  window.addEventListener('afterprint', done, { once: true });
  setTimeout(() => { window.print(); setTimeout(done, 1500); }, 260);
}
window.certCardHTML = certCardHTML;
window.bindCertCards = bindCertCards;

function bindLogout() {
  const b = $('#logout');
  if (b) b.onclick = () => { API.setToken(''); App.user = null; location.hash = '#/home'; };
}
window.bindLogout = bindLogout;

/* ================================================================
 *  5. App Shell: 现代化桌面级导航顶栏
 * ================================================================ */
function shell(content, active) {
  const u = App.user;
  const on = k => (active === k ? 'on' : '');
  const touch = /Android|iPhone|iPad|Harmony|FLA-App/i.test(navigator.userAgent);
  const castHref = /FLA-App/.test(navigator.userAgent) ? 'https://app.fla/home.html' : '/cast.html';
  const nav =
    '<a class="' + on('home') + '" href="#/home">' + UI.icon('board', 15) + ' 首页</a>' +
    (u ? '<a class="' + on('library') + '" href="#/library">' + UI.icon('folder', 15) + ' 课件库</a>' : '') +
    (u ? '<a class="' + on('chat') + '" href="#/chat">' + UI.icon('chat', 15) + ' 聊天<i class="nav-badge hidden" id="chat-badge"></i></a>' : '') +
    (u ? '<a class="' + on('forum') + '" href="#/forum">' + UI.icon('forum', 15) + ' 论坛</a>' : '') +
    (u ? '<a class="' + on('ai') + '" href="#/ai">' + UI.icon('sparkle', 15) + ' AI</a>' : '') +
    (touch ? '<a href="' + castHref + '">' + UI.icon('mobile', 15) + ' 投屏</a>' : '') +
    '<a class="' + on('remote') + '" href="#/remote">' + UI.icon('qr', 15) + ' 遥控</a>' +
    '<a class="' + on('desktop') + '" href="#/desktop">' + UI.icon('laptop', 15) + ' 桌面端</a>' +
    '<a class="' + on('mobile') + '" href="#/mobile">' + UI.icon('mobile', 15) + ' 手机端</a>' +
    (u ? '<a class="' + on('profile') + '" href="#/profile">' + UI.icon('users', 15) + ' 我的</a>' : '') +
    (u && u.role === 'admin' ? '<a class="' + on('admin') + '" href="#/admin">' + UI.icon('gear', 15) + ' 管理</a>' : '');
  const right = u
    ? '<button class="icon-btn" id="top-ann" title="公告"><i class="ann-dot hidden" id="ann-dot"></i>' + UI.icon('horn', 16) + '</button>' +
      '<button class="icon-btn" id="top-qr" title="扫码登录其他设备">' + UI.icon('qr', 16) + '</button>' +
      certHTML(u) +
      '<span class="uchip">' + avatarHTML(u, 26) + '<b>' + UI.esc(u.nickname) + '</b></span>' +
      '<button class="btn sm ghost" id="logout" title="退出登录">' + UI.icon('logout', 15) + '</button>'
    : '<a class="btn sm ghost auth-btn" href="#/login">登录</a><a class="btn sm primary auth-btn" href="#/register">注册</a>';
  return '<header class="topbar">' +
    '<div style="display:flex;align-items:center;gap:18px;">' +
      '<a class="brand" href="#/home">' + UI.icon('board', 24) + '<span>FLA</span></a>' +
      '<nav class="nav">' + nav + '</nav>' +
    '</div>' +
    '<div class="top-right">' + right + '</div>' +
    '</header>' +
    '<div class="page">' + content + '</div>';
}
window.shell = shell;

/* 导航栏常驻: 除放映/观看页、遥控进行中外, 任何页面渲染后若没有顶栏就自动补上 */
function ensureNav() {
  const app = document.getElementById('app');
  if (!app || app.querySelector(':scope > .topbar')) return;
  const h = location.hash || '';
  if (/^#\/view\//.test(h) || (/^#\/remote/.test(h) && /[?&](sid|code)=/.test(h))) return;
  if (!app.firstElementChild) return;
  const parts = h.replace(/^#\/?/, '').split(/[/?]/);
  app.insertAdjacentHTML('afterbegin', shell('', parts[0] || 'home').replace('<div class="page"></div>', ''));
  bindLogout();
}
new MutationObserver(ensureNav).observe(document.getElementById('app') || document.body, { childList: true });

/* ================================================================
 *  6. 课件库工作台 (搜索/分类/排序/双视图/批量管理/侧边抽屉)
 * ================================================================ */
async function viewLibrary() {
  document.title = '我的课件 - FLA';
  $('#app').innerHTML = shell(
    '<div class="lib-hero">' +
      '<div class="lib-hero-left">' +
        '<h2>我的课件</h2>' +
      '</div>' +
      '<div class="lib-hero-actions">' +
        '<div class="storage-card" id="storage"></div>' +
        '<button class="btn" id="newboard">' + UI.icon('edit', 16) + ' 新建白板</button>' +
        '<button class="btn primary" id="upbtn">' + UI.icon('upload', 18) + ' 上传课件</button>' +
        '<input type="file" id="upinput" multiple hidden accept="' + ACCEPT + '">' +
      '</div>' +
    '</div>' +

    '<div class="lib-toolbar">' +
      '<div class="lib-search-box">' +
        '<span class="lib-search-icon">' + UI.icon('search', 16) + '</span>' +
        '<input type="search" id="lib-search-input" placeholder="搜索课件名称… (按 / 聚焦)" value="' + UI.esc(App.searchTerm) + '">' +
        (App.searchTerm ? '<button class="lib-search-clear" id="lib-search-clear">×</button>' : '') +
      '</div>' +

      '<div class="lib-filters">' +
        renderFilterPills() +
      '</div>' +

      '<div class="lib-view-ops">' +
        '<select class="lib-sort-select" id="lib-sort-select">' +
          '<option value="date_desc"' + (App.sortBy === 'date_desc' ? ' selected' : '') + '>最新上传优先</option>' +
          '<option value="date_asc"' + (App.sortBy === 'date_asc' ? ' selected' : '') + '>最早上传优先</option>' +
          '<option value="name_asc"' + (App.sortBy === 'name_asc' ? ' selected' : '') + '>文件名称 A-Z</option>' +
          '<option value="size_desc"' + (App.sortBy === 'size_desc' ? ' selected' : '') + '>文件体积最大</option>' +
        '</select>' +

        '<div class="view-toggle">' +
          '<button class="view-toggle-btn ' + (App.viewMode === 'grid' ? 'active' : '') + '" id="vt-grid" title="卡片网格视图">' + UI.icon('grid', 15) + '</button>' +
          '<button class="view-toggle-btn ' + (App.viewMode === 'table' ? 'active' : '') + '" id="vt-table" title="详细列表视图">' + UI.icon('list', 15) + '</button>' +
        '</div>' +
      '</div>' +
    '</div>' +

    '<div class="dropzone" id="dropzone">' + UI.icon('cloud', 18) + ' 或将文件拖到这里 · 支持 Office / PDF / 图片 / 音频 / 视频</div>' +

    '<div id="files-container">' +
      '<div class="files-grid" id="grid"><div class="empty">加载中…</div></div>' +
    '</div>' +

    '<div class="batch-bar" id="batch-bar">' +
      '<span class="batch-count" id="batch-count">已选 0 项</span>' +
      '<button class="btn sm ghost" id="batch-dl-btn" style="color:#fff;">' + UI.icon('download', 14) + ' 批量下载</button>' +
      '<button class="btn sm danger" id="batch-del-btn">' + UI.icon('trash', 14) + ' 批量删除</button>' +
      '<button class="btn sm ghost" id="batch-cancel-btn" style="color:#a1a1aa;">取消选择</button>' +
    '</div>' +

    '<div class="file-drawer-backdrop" id="drawer-backdrop"></div>' +
    '<div class="file-drawer" id="file-drawer"></div>' +

    '<div class="uplist" id="uplist"></div>',
    'library'
  );

  bindLogout();
  refreshMe();
  bindLibraryToolbarEvents();
  await loadLibraryFiles();

  $('#newboard').onclick = async () => {
    try {
      const f = await API.post('/api/files/board', {});
      location.hash = '#/view/' + f.id;
    } catch (e) { UI.toast(e.message, 'err'); }
  };
  $('#upbtn').onclick = () => $('#upinput').click();
  $('#upinput').onchange = e => { uploadFiles(Array.from(e.target.files)); e.target.value = ''; };

  const dz = $('#dropzone');
  if (dz) {
    dz.onclick = () => $('#upinput').click();
    dz.ondragover = e => { e.preventDefault(); dz.classList.add('over'); };
    dz.ondragleave = () => dz.classList.remove('over');
    dz.ondrop = e => { e.preventDefault(); dz.classList.remove('over'); uploadFiles(Array.from(e.dataTransfer.files)); };
  }
}

function renderFilterPills() {
  const kinds = [
    { id: 'all', label: '全部' },
    { id: 'ppt', label: '幻灯片 (PPT)' },
    { id: 'doc', label: '文档 (Word/PDF)' },
    { id: 'excel', label: '表格 (Excel)' },
    { id: 'board', label: '互动白板' },
    { id: 'media', label: '影音媒体' }
  ];
  return kinds.map(k =>
    '<button class="filter-pill ' + (App.filterKind === k.id ? 'active' : '') + '" data-k="' + k.id + '">' +
      k.label +
    '</button>'
  ).join('');
}

function bindLibraryToolbarEvents() {
  const sInp = $('#lib-search-input');
  if (sInp) {
    sInp.oninput = e => {
      App.searchTerm = e.target.value.trim().toLowerCase();
      applyFiltersAndSort();
    };
  }
  const sClr = $('#lib-search-clear');
  if (sClr) {
    sClr.onclick = () => {
      App.searchTerm = '';
      if (sInp) sInp.value = '';
      applyFiltersAndSort();
    };
  }
  $$('.filter-pill').forEach(btn => {
    btn.onclick = () => {
      App.filterKind = btn.dataset.k;
      $$('.filter-pill').forEach(b => b.classList.toggle('active', b === btn));
      applyFiltersAndSort();
    };
  });
  const sortSel = $('#lib-sort-select');
  if (sortSel) {
    sortSel.onchange = e => {
      App.sortBy = e.target.value;
      applyFiltersAndSort();
    };
  }
  const vtGrid = $('#vt-grid'), vtTable = $('#vt-table');
  if (vtGrid && vtTable) {
    vtGrid.onclick = () => setViewMode('grid');
    vtTable.onclick = () => setViewMode('table');
  }

  // 批量操作栏事件
  const bCancel = $('#batch-cancel-btn');
  if (bCancel) bCancel.onclick = () => { App.selectedFids.clear(); updateBatchBar(); renderFileList(); };
  const bDel = $('#batch-del-btn');
  if (bDel) bDel.onclick = batchDelete;
  const bDl = $('#batch-dl-btn');
  if (bDl) bDl.onclick = batchDownload;

  // 抽屉背景点击关闭
  const dbp = $('#drawer-backdrop');
  if (dbp) dbp.onclick = closeFileDrawer;
}

function setViewMode(mode) {
  App.viewMode = mode;
  try { localStorage.setItem('fla_view_mode', mode); } catch(e){}
  const vtGrid = $('#vt-grid'), vtTable = $('#vt-table');
  if (vtGrid) vtGrid.classList.toggle('active', mode === 'grid');
  if (vtTable) vtTable.classList.toggle('active', mode === 'table');
  renderFileList();
}

function applyFiltersAndSort() {
  let list = App.files.slice();

  // 1. 分类过滤
  if (App.filterKind !== 'all') {
    list = list.filter(f => {
      const ext = ((f.name || '').split('.').pop() || '').toLowerCase();
      if (App.filterKind === 'ppt') return /^(ppt|pptx|pps|ppsx|dps)$/.test(ext);
      if (App.filterKind === 'doc') return /^(doc|docx|wps|rtf|pdf|txt)$/.test(ext);
      if (App.filterKind === 'excel') return /^(xls|xlsx|et|csv)$/.test(ext);
      if (App.filterKind === 'board') return f.kind === 'board';
      if (App.filterKind === 'media') return /^(png|jpg|jpeg|webp|gif|svg|mp3|wav|mp4|webm|mkv|mov)$/.test(ext);
      return true;
    });
  }

  // 2. 搜索词
  if (App.searchTerm) {
    list = list.filter(f => (f.name || '').toLowerCase().includes(App.searchTerm));
  }

  // 3. 排序
  list.sort((a, b) => {
    if (App.sortBy === 'date_desc') return (b.created_at || '').localeCompare(a.created_at || '');
    if (App.sortBy === 'date_asc') return (a.created_at || '').localeCompare(b.created_at || '');
    if (App.sortBy === 'name_asc') return (a.name || '').localeCompare(b.name || '');
    if (App.sortBy === 'size_desc') return (b.size || 0) - (a.size || 0);
    return 0;
  });

  App.filteredFiles = list;
  renderFileList();
}

function renderFileList() {
  const container = $('#files-container');
  if (!container) return;

  const files = App.filteredFiles;
  if (!files.length) {
    container.innerHTML = '<div class="empty">' + UI.icon('file', 42) +
      '<p style="font-size:15px;margin-top:12px;">' +
      (App.searchTerm ? '未找到包含 “' + UI.esc(App.searchTerm) + '” 的课件' : '还没有课件，点击右上角「上传课件」开始') +
      '</p></div>';
    return;
  }

  if (App.viewMode === 'grid') {
    container.innerHTML = '<div class="files-grid" id="grid">' + files.map(cardHTML).join('') + '</div>';
    bindFileGridEvents();
  } else {
    container.innerHTML = renderTableViewHTML(files);
    bindFileTableEvents();
  }
}

function kindBadge(f) {
  const ext = ((f.name || '').split('.').pop() || '').toLowerCase();
  if (f.kind === 'board') return '<span class="kind-badge board">' + UI.icon('board', 12) + ' 白板</span>';
  if (/^(ppt|pptx|pps|ppsx|dps)$/.test(ext)) return '<span class="kind-badge ppt">' + UI.icon('play', 12) + ' PPT</span>';
  if (/^(doc|docx|wps|rtf)$/.test(ext)) return '<span class="kind-badge word">' + UI.icon('file', 12) + ' DOC</span>';
  if (/^(xls|xlsx|et|csv)$/.test(ext)) return '<span class="kind-badge excel">' + UI.icon('chart', 12) + ' XLS</span>';
  if (ext === 'pdf') return '<span class="kind-badge pdf">' + UI.icon('file', 12) + ' PDF</span>';
  if (/^(mp4|webm|mkv|mov|mp3|wav|png|jpg|jpeg|webp)$/.test(ext)) return '<span class="kind-badge media">' + UI.icon('image', 12) + ' 媒体</span>';
  return '<span class="kind-badge doc">' + UI.icon('file', 12) + ' 文件</span>';
}

function cardHTML(f) {
  const st = f.status === 'converting' ? '<span class="st converting">转换中…</span>'
    : f.status === 'failed'
      ? '<span class="st failed" title="' + UI.esc(f.error || '点击「详情」查看失败原因') + '">转换失败</span>'
      : '';
  const isOffice = f.kind === 'office';
  const isSel = App.selectedFids.has(f.id);

  return '<div class="file-card ' + (isSel ? 'selected' : '') + '" data-id="' + f.id + '">' +
    '<div class="fc-select-box" data-sel="' + f.id + '" title="多选">' +
      (isSel ? '✓' : '') +
    '</div>' +
    '<div class="fc-top">' + kindBadge(f) + st + '</div>' +
    '<div class="fc-name" title="' + UI.esc(f.name) + '">' + UI.esc(f.name) + '</div>' +
    '<div class="fc-meta">' +
      (f.kind === 'board' ? '无限画布' : UI.fmtSize(f.size)) + ' · ' + UI.fmtDate(f.created_at) +
      (f.kind !== 'board' && f.pages ? ' · ' + f.pages + ' 页' : '') +
    '</div>' +
    '<div class="act">' +
    (isOffice
      ? '<button data-a="open" title="在线纯净预览">' + UI.icon('eye', 14) + ' 预览</button>' +
        '<button data-a="present" class="btn-present" title="全屏教学放映 (画笔/计时/抽选)">' + UI.icon('play', 13) + ' 放映</button>' +
        '<button data-a="local" class="btn-local" title="直接调用本地 PowerPoint/WPS">' + UI.icon('external', 13) + ' 本地打开</button>'
      : '<button data-a="open" title="打开">' + UI.icon('board', 15) + ' 打开</button>' +
        (f.kind !== 'board' ? '<button data-a="present" class="btn-present" title="全屏放映">' + UI.icon('play', 13) + ' 放映</button>' : '')) +
    (f.kind === 'board' ? '' : '<button data-a="dl" title="下载">' + UI.icon('download', 15) + '</button>') +
    (f.kind === 'board' ? '' : '<button data-a="link" title="复制公开直链">' + UI.icon('link', 15) + '</button>') +
    '<button data-a="rename" title="重命名">' + UI.icon('edit', 14) + '</button>' +
    '<button data-a="inspect" title="查看课件详情">' + UI.icon('info', 14) + '</button>' +
    (f.status === 'failed' ? '<button data-a="retry" title="重试转换">' + UI.icon('refresh', 15) + '</button>' : '') +
    '<button data-a="del" title="删除" class="danger">' + UI.icon('trash', 15) + '</button>' +
    '</div></div>';
}

function bindFileGridEvents() {
  const grid = $('#grid');
  if (!grid) return;
  $$('.file-card', grid).forEach(el => {
    const fid = +el.dataset.id;
    const f = App.files.find(x => x.id === fid);
    if (!f) return;

    // 单击卡片整体打开详情抽屉
    el.onclick = e => {
      if (e.target.closest('.fc-select-box') || e.target.closest('.act button')) return;
      openFileDrawer(f);
    };

    // 多选框点击
    const sBox = el.querySelector('.fc-select-box');
    if (sBox) {
      sBox.onclick = e => {
        e.stopPropagation();
        toggleSelectFile(fid);
      };
    }

    // 动作按钮
    $$('.act button', el).forEach(b => {
      b.onclick = e => {
        e.stopPropagation();
        handleFileAction(b.dataset.a, f);
      };
    });
  });
}

function renderTableViewHTML(files) {
  const allSel = files.length > 0 && files.every(f => App.selectedFids.has(f.id));
  return '<div class="files-table-wrap">' +
    '<table class="files-table">' +
      '<thead><tr>' +
        '<th style="width:40px;"><input type="checkbox" id="tbl-sel-all" ' + (allSel ? 'checked' : '') + '></th>' +
        '<th>课件名称</th>' +
        '<th style="width:110px;">格式类型</th>' +
        '<th style="width:100px;">文件体积</th>' +
        '<th style="width:90px;">规格/页数</th>' +
        '<th style="width:140px;">上传时间</th>' +
        '<th style="width:230px;">快捷操作</th>' +
      '</tr></thead>' +
      '<tbody>' +
        files.map(f => {
          const isSel = App.selectedFids.has(f.id);
          const isOffice = f.kind === 'office';
          return '<tr data-id="' + f.id + '" class="' + (isSel ? 'selected' : '') + '">' +
            '<td><input type="checkbox" class="tbl-row-chk" data-fid="' + f.id + '" ' + (isSel ? 'checked' : '') + '></td>' +
            '<td><div class="ft-name-cell">' + kindBadge(f) + '<span>' + UI.esc(f.name) + '</span></div></td>' +
            '<td>' + (f.ext || (f.kind === 'board' ? '白板' : '—')).toUpperCase() + '</td>' +
            '<td>' + (f.kind === 'board' ? '—' : UI.fmtSize(f.size)) + '</td>' +
            '<td>' + (f.pages ? f.pages + ' 页' : (f.kind === 'board' ? '无限画布' : '—')) + '</td>' +
            '<td class="muted">' + UI.fmtDate(f.created_at) + '</td>' +
            '<td><div class="ft-acts">' +
              (isOffice ? '<button class="btn xs" data-a="open">' + UI.icon('eye', 13) + ' 预览</button>' : '') +
              '<button class="btn xs primary" data-a="present">' + UI.icon('play', 13) + ' 放映</button>' +
              (isOffice ? '<button class="btn xs soft" data-a="local" title="本地Office打开">' + UI.icon('external', 13) + ' 本地</button>' : '') +
              '<button class="btn xs ghost" data-a="inspect" title="详情">' + UI.icon('info', 13) + '</button>' +
              '<button class="btn xs danger" data-a="del" title="删除">' + UI.icon('trash', 13) + '</button>' +
            '</div></td>' +
          '</tr>';
        }).join('') +
      '</tbody>' +
    '</table>' +
  '</div>';
}

function bindFileTableEvents() {
  const tbl = $('.files-table');
  if (!tbl) return;

  const selAll = $('#tbl-sel-all');
  if (selAll) {
    selAll.onchange = e => {
      const checked = e.target.checked;
      App.filteredFiles.forEach(f => {
        if (checked) App.selectedFids.add(f.id);
        else App.selectedFids.delete(f.id);
      });
      updateBatchBar();
      renderFileList();
    };
  }

  $$('.tbl-row-chk', tbl).forEach(chk => {
    chk.onclick = e => {
      e.stopPropagation();
      toggleSelectFile(+chk.dataset.fid);
    };
  });

  $$('tbody tr', tbl).forEach(tr => {
    const fid = +tr.dataset.id;
    const f = App.files.find(x => x.id === fid);
    if (!f) return;

    tr.onclick = e => {
      if (e.target.closest('input') || e.target.closest('button')) return;
      openFileDrawer(f);
    };

    $$('.ft-acts button', tr).forEach(btn => {
      btn.onclick = e => {
        e.stopPropagation();
        handleFileAction(btn.dataset.a, f);
      };
    });
  });
}

function toggleSelectFile(fid) {
  if (App.selectedFids.has(fid)) App.selectedFids.delete(fid);
  else App.selectedFids.add(fid);
  updateBatchBar();
  renderFileList();
}

function updateBatchBar() {
  const bar = $('#batch-bar');
  const count = $('#batch-count');
  if (!bar || !count) return;
  const num = App.selectedFids.size;
  if (num > 0) {
    count.textContent = '已选 ' + num + ' 项课件';
    bar.classList.add('show');
  } else {
    bar.classList.remove('show');
  }
}

async function batchDelete() {
  const count = App.selectedFids.size;
  if (!count) return;
  const ok = await UI.confirm('确定批量删除已选择的 ' + count + ' 个课件？删除后无法恢复。');
  if (!ok) return;

  const fids = Array.from(App.selectedFids);
  let succ = 0;
  for (const fid of fids) {
    try {
      await API.del('/api/files/' + fid);
      succ++;
    } catch (e) {}
  }
  App.selectedFids.clear();
  updateBatchBar();
  toast('已成功删除 ' + succ + ' 个课件', 'ok');
  refreshList(); refreshMe();
}

function batchDownload() {
  const fids = Array.from(App.selectedFids);
  if (!fids.length) return;
  toast('正在启动 ' + fids.length + ' 个文件下载…', 'ok');
  fids.forEach((fid, idx) => {
    setTimeout(() => {
      window.open('/api/files/' + fid + '/download?token=' + encodeURIComponent(API.token), '_blank');
    }, idx * 250);
  });
}

/* ================================================================
 *  7. 课件详情侧边抽屉 (Inspector Drawer)
 * ================================================================ */
function openFileDrawer(f) {
  App.inspectingFile = f;
  const drawer = $('#file-drawer');
  const backdrop = $('#drawer-backdrop');
  if (!drawer || !backdrop) return;

  const ext = ((f.name || '').split('.').pop() || '').toLowerCase();
  const isOffice = f.kind === 'office' && /^(ppt|pptx|doc|docx|xls|xlsx)$/.test(ext);

  drawer.innerHTML =
    '<div class="fd-head">' +
      '<b style="font-size:16px;">课件详情</b>' +
      '<button class="icon-btn" id="fd-close" type="button" style="width:30px;height:30px;">×</button>' +
    '</div>' +
    '<div class="fd-body">' +
      '<div class="fd-preview-box">' +
        kindBadge(f) +
      '</div>' +
      '<div style="margin-bottom:18px;">' +
        '<div style="font-size:17px;font-weight:700;color:var(--ink);line-height:1.4;margin-bottom:6px;" id="fd-title">' + UI.esc(f.name) + '</div>' +
        '<button class="btn xs ghost" id="fd-rename-btn" style="color:var(--brand);">' + UI.icon('edit', 12) + ' 重命名</button>' +
      '</div>' +
      '<div class="fd-row"><span>文件类型</span><b>' + (ext || f.kind).toUpperCase() + '</b></div>' +
      '<div class="fd-row"><span>文件体积</span><b>' + (f.kind === 'board' ? '无限画布' : UI.fmtSize(f.size)) + '</b></div>' +
      '<div class="fd-row"><span>幻灯页数</span><b>' + (f.pages ? f.pages + ' 页' : '—') + '</b></div>' +
      '<div class="fd-row"><span>转换状态</span><b>' +
        (f.status === 'ready' ? '正常' : f.status === 'converting' ? '转换中…' : '失败') + '</b></div>' +
      '<div class="fd-row"><span>上传日期</span><b>' + UI.fmtDate(f.created_at) + '</b></div>' +
      (f.status === 'failed'
        ? '<div class="fd-error">' +
            '<div class="fd-error-t">转换失败原因</div>' +
            '<div class="fd-error-msg">' + UI.esc(f.error || '服务器没有返回原因，可能缺少 LibreOffice 或文件已损坏') + '</div>' +
            '<div class="fd-error-acts">' +
              '<button class="btn sm primary" id="fd-act-retry">' + UI.icon('refresh', 14) + ' 重试转换</button>' +
              '<button class="btn sm ghost" id="fd-act-copy-err">复制原因</button>' +
            '</div>' +
            '<div class="fd-error-tip">常见原因：服务器未装 LibreOffice；文件加密/损坏；课件过大超时。' +
              '可在服务器执行 <code>bash bt.sh doctor</code> 自检。</div>' +
          '</div>' : '') +
      (f.status === 'converting'
        ? '<div class="fd-error" style="background:#fffbeb;border-color:#fde68a;">' +
            '<div class="fd-error-msg" style="color:#92400e;">正在后台转换，通常需要几秒到几十秒；' +
            '大课件或首次转换可能更久，页面会自动刷新状态。</div></div>' : '') +
      '<div style="display:flex;flex-direction:column;gap:10px;margin-top:20px;">' +
        '<button class="btn primary" id="fd-act-present">' + UI.icon('play', 15) + ' 全屏互动放映</button>' +
        (isOffice ? '<button class="btn" id="fd-act-open">' + UI.icon('eye', 15) + ' 在线纯净预览</button>' : '') +
        (isOffice ? '<button class="btn soft" id="fd-act-local">' + UI.icon('external', 15) + ' 调起本地Office放映</button>' : '') +
        (f.kind !== 'board' ? '<button class="btn ghost" id="fd-act-link">' + UI.icon('link', 15) + ' 复制公开免登录直链</button>' : '') +
        (f.kind !== 'board' ? '<button class="btn ghost" id="fd-act-dl">' + UI.icon('download', 15) + ' 下载到电脑</button>' : '') +
        '<button class="btn danger" id="fd-act-del">' + UI.icon('trash', 15) + ' 删除课件</button>' +
      '</div>' +
      '<div class="fd-qr-box" id="fd-qr-box">' +
        '<div style="font-size:12px;color:var(--mut);margin-bottom:8px;">手机扫码直达放映</div>' +
        '<div id="fd-qr"></div>' +
      '</div>' +
    '</div>';

  $('#fd-close').onclick = closeFileDrawer;
  $('#fd-rename-btn').onclick = () => renameFile(f);
  $('#fd-act-present').onclick = () => handleFileAction('present', f);
  const retryBtn = $('#fd-act-retry');
  if (retryBtn) retryBtn.onclick = () => handleFileAction('retry', f);
  const copyErrBtn = $('#fd-act-copy-err');
  if (copyErrBtn) copyErrBtn.onclick = () => {
    copyText(f.error || '（无原因）').then(() => toast('失败原因已复制', 'ok')).catch(() => {});
  };
  if ($('#fd-act-open')) $('#fd-act-open').onclick = () => handleFileAction('open', f);
  if ($('#fd-act-local')) $('#fd-act-local').onclick = () => handleFileAction('local', f);
  if ($('#fd-act-link')) $('#fd-act-link').onclick = () => handleFileAction('link', f);
  if ($('#fd-act-dl')) $('#fd-act-dl').onclick = () => handleFileAction('dl', f);
  $('#fd-act-del').onclick = () => handleFileAction('del', f);

  // 渲染直链二维码
  const qrBox = $('#fd-qr');
  if (qrBox) {
    const directUrl = location.origin + '/present.html?fid=' + f.id + '&token=' + encodeURIComponent(API.token);
    UI.renderQR(qrBox, directUrl, 130);
  }

  drawer.classList.add('open');
  backdrop.classList.add('open');
}

function closeFileDrawer() {
  const drawer = $('#file-drawer');
  const backdrop = $('#drawer-backdrop');
  if (drawer) drawer.classList.remove('open');
  if (backdrop) backdrop.classList.remove('open');
  App.inspectingFile = null;
}

/* ================================================================
 *  8. 课件单个动作调度
 * ================================================================ */
async function handleFileAction(a, f) {
  if (a === 'inspect') {
    openFileDrawer(f);
  }
  if (a === 'open') {
    openFile(f);
  }
  if (a === 'present') {
    const ext = ((f.name || '').split('.').pop() || '').toLowerCase();
    const ms = f.kind === 'office' && /^(ppt|pptx|doc|docx|xls|xlsx)$/.test(ext);
    const trk = ms ? '&track=ms' : '';
    window.open('/present.html?fid=' + f.id + '&token=' + encodeURIComponent(API.token) + trk, '_blank');
  }
  if (a === 'local') {
    openLocalFile(f);
  }
  if (a === 'dl') {
    window.open('/api/files/' + f.id + '/download?token=' + API.token, '_blank');
  }
  if (a === 'link') {
    API.get('/api/files/' + f.id + '/share-link').then(r => {
      const direct = r.direct || (location.origin + r.path);
      copyText(direct).then(() => toast('直链已复制: ' + direct, 'ok')).catch(() => toast(direct));
    }).catch(err => toast(err.message, 'err'));
  }
  if (a === 'rename') {
    renameFile(f);
  }
  if (a === 'retry') {
    API.post('/api/files/' + f.id + '/retry')
      .then(() => { toast('已重新开始转换，稍等几秒后刷新', 'ok'); closeFileDrawer(); refreshList(); })
      .catch(err => toast(err.message, 'err'));
  }
  if (a === 'del') {
    const ok = await UI.confirm('确定删除《' + UI.esc(f.name) + '》？删除后无法恢复');
    if (!ok) return;
    API.del('/api/files/' + f.id).then(() => {
      toast('已删除', 'ok');
      closeFileDrawer();
      refreshList(); refreshMe();
    }).catch(err => toast(err.message, 'err'));
  }
}

async function renameFile(f) {
  const m = UI.modal({
    title: '重命名课件',
    body: '<label>课件名称<input id="ren-input" value="' + UI.esc(f.name) + '"></label>'
  });
  m.foot.innerHTML = '<button class="btn" id="ren-cancel">取消</button><button class="btn primary" id="ren-save">保存</button>';
  m.foot.querySelector('#ren-cancel').onclick = m.close;
  m.foot.querySelector('#ren-save').onclick = async () => {
    const newName = $('#ren-input').value.trim();
    if (!newName) { toast('名称不能为空', 'err'); return; }
    try {
      await API.patch('/api/files/' + f.id, { name: newName });
      toast('重命名成功', 'ok');
      m.close();
      f.name = newName;
      if ($('#fd-title')) $('#fd-title').textContent = newName;
      renderFileList();
    } catch (e) { toast(e.message, 'err'); }
  };
}

async function refreshMe() {
  try { App.user = await API.get('/api/auth/me'); renderStorage(); } catch (e) { }
}

function renderStorage() {
  const el = $('#storage'); if (!el) return;
  const u = App.user;
  const pct = Math.min(100, Math.round(u.used_bytes / u.quota_bytes * 100));
  el.innerHTML = '<div class="storage"><div class="sbar"><i style="width:' + pct + '%"></i></div><span>' +
    UI.fmtSize(u.used_bytes) + ' / ' + UI.fmtSize(u.quota_bytes) + '</span></div>';
}

function copyText(t) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t);
  return new Promise((res, rej) => {
    const ta = document.createElement('textarea');
    ta.value = t; ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy') ? res() : rej(new Error('copy fail')); }
    catch (e) { rej(e); } finally { ta.remove(); }
  });
}

/* 列表是否还停留在最初那个「加载中…」占位符 (说明一次都没渲染成功) */
function fileListIsPlaceholder() {
  const container = $('#files-container');
  if (!container) return false;
  const first = container.firstElementChild;
  if (!first || !first.classList.contains('empty')) return false;
  return /加载中/.test(first.textContent || '');
}

function showFileListLoadError(error) {
  const container = $('#files-container');
  if (!container) return;
  const message = error && error.message ? error.message : '请稍后重试';
  container.innerHTML = '<div class="empty file-list-error"><p>课件列表加载失败</p>' +
    '<p class="muted">' + UI.esc(message) + '</p>' +
    '<button class="btn sm primary" id="file-list-retry">重新加载</button></div>';
  const retry = $('#file-list-retry', container);
  if (retry) retry.onclick = () => {
    container.innerHTML = '<div class="files-grid" id="grid"><div class="empty">加载中…</div></div>';
    loadLibraryFiles();
  };
}

async function requestFileList() {
  // 文件数量会随长期使用增长；避免普通 API 的 8 秒默认超时让列表永远停在“加载中”。
  const files = await API.req('/api/files', { timeout: 30000 });
  if (!Array.isArray(files)) throw new Error('服务器返回的文件列表格式异常，请刷新后重试');
  return files;
}

/* 课件库入口加载: 先走常规 refreshList; 若列表仍停在占位符(接口没被触发/异常被吞),
   再直接请求一次接口, 绝不让页面无限停在「加载中…」 */
async function loadLibraryFiles() {
  try { await refreshList(); } catch (e) { /* 下面统一兜底 */ }
  if (!fileListIsPlaceholder()) return true;
  try {
    App.files = await requestFileList();
    applyFiltersAndSort();
    return true;
  } catch (e) {
    showFileListLoadError(e);
    return false;
  }
}

async function refreshList() {
  let files;
  try {
    files = await requestFileList();
  } catch (e) {
    // 首次加载失败(且页面还没渲染出任何列表)必须显示错误与重试入口；
    // 已有数据时保留列表，避免短暂断线清空页面。
    if (!App.files.length || fileListIsPlaceholder()) showFileListLoadError(e);
    else UI.toast('文件列表暂时无法更新：' + (e.message || '网络错误'), 'err');
    return false;
  }
  try {
    App.files = files;
    applyFiltersAndSort();
  } catch (e) {
    // API 返回成功也可能遇到坏数据/渲染异常；不能让初始占位符永久留在页面。
    showFileListLoadError(new Error('课件数据已收到，但页面渲染失败：' + (e.message || '未知错误')));
    return false;
  }

  if (pollT) { clearInterval(pollT); pollT = null; }
  if (files.some(f => f.status === 'converting')) {
    let pollBusy = false;
    pollT = setInterval(async () => {
      if (!$('#grid') && !$('.files-table')) { clearInterval(pollT); pollT = null; return; }
      if (pollBusy) return;
      pollBusy = true;
      try {
        App.files = await requestFileList();
        applyFiltersAndSort();
      } catch (e) {
        // 转换状态轮询可稍后重试，不要让瞬时网络波动打断正在显示的列表。
      } finally {
        pollBusy = false;
      }
    }, 2500);
    App.setCleanup(() => { if (pollT) { clearInterval(pollT); pollT = null; } });
  }
  return true;
}

async function uploadFiles(files) {
  for (const f of files) {
    if (!f.name) continue;
    const item = UI.h('<div class="upitem"><div class="row"><span class="nm">' + UI.esc(f.name) + '</span><span class="pct">0%</span></div>' +
      '<div class="pbar"><i></i></div><div class="msg"></div></div>');
    $('#uplist').appendChild(item);
    const form = new FormData();
    form.append('file', f, f.name);
    try {
      await API.upload('/api/files/upload', form, pct => {
        item.querySelector('.pct').textContent = pct + '%';
        item.querySelector('.pbar i').style.width = pct + '%';
      });
      item.querySelector('.pbar i').style.width = '100%';
      item.querySelector('.pct').textContent = '完成';
      setTimeout(() => item.remove(), 2200);
      refreshList(); refreshMe();
    } catch (err) {
      item.classList.add('err');
      item.querySelector('.pct').textContent = '失败';
      item.querySelector('.msg').textContent = err.message;
    }
  }
}

/* ================================================================
 *  9. 专属桌面端中心 (Windows 客户端一键下载与连接)
 * ================================================================ */
function mobileDownloadSection() {
  setTimeout(() => {
    fetch('/api/app/info').then(r => r.json()).then(d => {
      const a = document.getElementById('apk-dl');
      if (a && !(d.android && d.android.available)) { a.removeAttribute('href'); a.classList.remove('primary'); a.style.opacity = '.6'; a.textContent = '安卓 App 构建中，稍后开放'; }
      if (a && d.android && d.android.size) a.innerHTML += ' <small style="opacity:.75">v' + (d.android.version || '') + ' · ' + Math.max(1, Math.round(d.android.size / 1024)) + ' KB</small>';
    }).catch(() => { });
  }, 0);
  const card = (title, body, btn) =>
    '<div style="flex:1;min-width:240px;background:#fff;border:1px solid var(--line,#e5e5e5);border-radius:16px;padding:22px;display:flex;flex-direction:column;gap:10px;">' +
    '<h3 style="margin:0;font-size:18px;">' + title + '</h3><div style="font-size:13.5px;color:var(--ink2,#444);line-height:1.7;flex:1;">' + body + '</div>' + btn + '</div>';
  return '<section class="home-section" id="mobile-dl" style="max-width:1100px;margin:40px auto;padding:0 20px;">' +
    '<h2 style="text-align:center;margin-bottom:6px;">手机端</h2>' +
    '<p style="text-align:center;color:var(--mut,#777);margin-bottom:22px;">手机整屏实时投到电脑大屏 · 手机观看电脑并批注 · <a href="#/mobile" style="font-weight:600;">进入手机端中心 →</a></p>' +
    '<div style="display:flex;gap:16px;flex-wrap:wrap;">' +
    card('安卓 / 鸿蒙 4.x 及以下（Android 7+）',
      '安装「FLA 手机端」App，<b>扫电脑上的二维码</b>或输入 4 位配对码，<b>整个手机屏幕</b>实时投到电脑（PPT、相册、任何 App）。<br>同一 Wi-Fi 自动直连，延迟最低。<br><span style="color:var(--mut,#888)">安装时如提示“未知来源”，请选择“允许本次安装”。</span>',
      '<a class="btn primary" href="/api/app/android" id="apk-dl">' + UI.icon('download', 16) + ' 下载安卓 APK</a>') +
    card('iPhone / iPad',
      '点下方按钮下载<b>描述文件</b> → 打开「设置」顶部「已下载描述文件」→ 安装，主屏即出现全屏的「FLA 投屏」图标，像 App 一样用（摄像头/照片投屏、观看电脑屏幕并批注）。<br><span style="color:var(--mut,#888)">请用 Safari 打开本页再点；可随时在 设置-通用-VPN与设备管理 删除。</span>',
      '<a class="btn primary" href="/api/app/ios.mobileconfig">' + UI.icon('download', 16) + ' 安装 iOS 描述文件</a><a class="btn" href="/cast.html" style="margin-top:6px;">直接打开网页版</a>') +
    card('鸿蒙 NEXT（纯血鸿蒙 5.x）',
      '纯血鸿蒙也能装安卓 App：在华为应用市场搜索并安装<b>「卓易通」</b>（或「出境易」），打开后添加/导入上面下载的 <b>FLA-cast.apk</b> 即可运行。<br>不想装也可直接用网页版（扫码即用，功能同 iPhone）。',
      '<a class="btn primary" href="/api/app/android">' + UI.icon('download', 16) + ' 下载 APK（卓易通中安装）</a><a class="btn" href="/cast.html" style="margin-top:6px;">打开网页版</a>') +
    '</div></section>';
}

/* ================================================================
 *  9b. 手机端中心 (公开页面, 无需登录)
 * ================================================================ */
/* iOS 安装向导: 按系统版本给出「免电脑、免证书」的可行路线 (TrollStore 永久安装, 不需要签名、不会掉签) */
function iosGuide() {
  const ua = navigator.userAgent;
  const m = /OS (\d+)_(\d+)(?:_(\d+))?/.exec(ua);
  const v = m ? [+m[1], +m[2], +(m[3] || 0)] : null;
  const ver = v ? v.join('.') : '';
  const le = (a, b) => { for (let i = 0; i < 3; i++) { if (a[i] !== b[i]) return a[i] < b[i]; } return true; };
  const ipa = location.origin + '/api/app/ios.ipa';
  const magnet = 'apple-magnifier://install?url=' + encodeURIComponent(ipa);
  let tier = 'unknown';
  if (v) {
    if (v[0] < 14) tier = 'old';
    else if (le(v, [15, 4, 99])) tier = 'ota';       // 14.0 – 15.4.x: Safari 直接装 TrollStore, 全程无电脑
    else if (v[0] === 16 && v[1] >= 7) tier = 'none';  // 16.7 正式版起已修复
    else if (le(v, [17, 0, 0])) tier = 'pc';         // 15.5 – 17.0: TrollStore 可装, 但首次需要电脑
    else tier = 'none';                              // 17.0.1+: 苹果已修复漏洞
  }
  const step = (n, t) => '<div><b>' + n + '</b>' + t + '</div>';
  const install =
    step('安装 FLA', '在 TrollStore 设置里打开「URL Scheme」后，回到本页点下方「一键安装到 TrollStore」；或点「下载 IPA」→ 分享 → 用 TrollStore 打开 → Install。永久有效，不掉签、不用证书。') ;
  const btns = '<div class="mc-btns"><a class="mc-btn w" href="' + magnet + '">一键安装到 TrollStore</a><a class="mc-btn" href="/api/app/ios.ipa">下载 IPA</a></div>';
  let body = '';
  if (tier === 'ota') {
    body = '<p class="s">你的系统 iOS ' + ver + ' 支持<b>免电脑、免证书、永久安装</b>（TrollStore）。只需做一次：</p><div class="mc-steps">' +
      step('安装 TrollStore 助手', '用 Safari 打开 <a href="https://api.jailbreaks.app/troll" style="text-decoration:underline">api.jailbreaks.app/troll</a>（iOS 14 的 A12 及更新机型用 <a href="https://api.jailbreaks.app/troll64e" style="text-decoration:underline">troll64e</a>），弹窗点「安装」，桌面出现「GTA Car Tracker」（不出现就重启手机）。') +
      step('装好 TrollStore', '打开 GTA Car Tracker → Install TrollStore，桌面出现 TrollStore。') +
      install + '</div>' + btns;
  } else if (tier === 'pc') {
    body = '<p class="s">你的系统 iOS ' + ver + ' 可以永久安装（TrollStore），但<b>第一次</b>需要信息老师用电脑帮忙装一次 TrollStore（TrollInstallerX），之后所有更新都在手机上点一下完成。</p><div class="mc-steps">' +
      step('电脑装 TrollStore（一次）', '按 B站/网上「TrollInstallerX 安装 TrollStore」教程操作（iOS 17.0 用 TrollRestore）。') +
      install + '</div>' + btns;
  } else if (tier === 'none') {
    body = '<p class="s">你的系统 iOS ' + ver + ' 已被苹果修复漏洞，<b>不存在</b>既不用电脑、又不用证书的正规安装方式。</p>' +
      '<div class="mc-grid">' +
      '<div class="mc-card"><h3>推荐：描述文件 + GRecord 整屏投屏</h3><p>① 安装描述文件，主屏出现「FLA」；② App Store 免费下载「GRecord」；③ 在 FLA 投屏里点「iPhone 整屏投屏」，复制地址粘贴到 GRecord（只需一次）。之后每节课扫码 → 开始直播，整屏画面自动上大屏。</p><div class="act"><a class="w" href="/api/app/ios.mobileconfig">安装描述文件</a></div></div>' +
      '<div class="mc-card"><h3>根本解决：TestFlight</h3><p>学校/开发者开通苹果开发者账号（688 元/年）后，老师只需从 App Store 装「TestFlight」，点邀请链接即可安装完整版，<b>不用电脑、不用证书、所有 iOS 版本都能用</b>。</p></div>' +
      '<div class="mc-card"><h3>关于 B站「共享证书」方法</h3><p>那类方法（轻松签/全能签 + 网上公开的企业证书）用的是别人泄露的证书：随时被苹果吊销导致 App 闪退，签名工具还能往 App 里注入代码，不建议用在教师手机上，所以本站不提供。</p></div>' +
      '</div>';
  } else if (tier === 'old') {
    body = '<p class="s">iOS ' + ver + ' 版本过低，请使用网页版。</p>';
  } else {
    body = '<p class="s">请用 iPhone / iPad 的 Safari 打开本页，会自动显示适合你系统版本的安装方法。</p><div class="mc-grid">' +
      '<div class="mc-card"><h3>iOS 14.0 – 15.4.1</h3><p>免电脑、免证书、永久安装：Safari 打开 api.jailbreaks.app/troll 装 TrollStore，再一键安装 FLA。</p></div>' +
      '<div class="mc-card"><h3>iOS 15.5 – 17.0</h3><p>首次需电脑装 TrollStore（TrollInstallerX），之后永久免签。</p></div>' +
      '<div class="mc-card"><h3>iOS 17.0.1 及以上</h3><p>无免证书方法：先用免安装版（描述文件）；开通开发者账号后可走 TestFlight。</p></div>' +
      '</div>';
  }
  return '<section class="mc-sec" id="mc-ios-guide"><h2>iPhone 完整版安装（整屏投屏）</h2>' + body + '</section>';
}

async function viewDesktopCenter() {
  document.title = '桌面端 - FLA';
  const ready = await probeDesktopClient();
  $('#app').innerHTML = shell(
    '<div style="max-width:560px;margin:8vh auto;text-align:center;padding:0 16px">' +
      '<h1 style="font-size:34px;margin-bottom:6px">FLA 桌面端</h1>' +
      '<p class="muted" style="letter-spacing:3px;margin-bottom:22px">轻量 · 高效 · 帮助</p>' +
      '<p style="margin-bottom:22px">' + (ready ? '<span class="dh-dot online" style="display:inline-block;margin-right:6px"></span>本机客户端已运行' : '<span class="muted">Windows 10 / 11 · 单文件免安装 <span id="dk-ver"></span></span>') + '</p>' +
      '<div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap">' +
        '<a href="/api/desktop/download" class="btn primary lg">' + UI.icon('download', 18) + ' 下载 FLA.exe</a>' +
        '<button class="btn lg" id="btn-test-desktop">' + UI.icon('refresh', 16) + ' 唤起本机客户端</button>' +
      '</div>' +
    '</div>', 'desktop');
  bindLogout();
  fetch('/api/desktop/version').then(r => r.json()).then(d => { const e = $('#dk-ver'); if (e && d.version) e.textContent = '· v' + d.version; }).catch(() => { });
  $('#btn-test-desktop').onclick = async () => {
    const ok = await probeDesktopClient();
    if (ok) toast('本机客户端正在运行 ✓', 'ok');
    else { toast('未检测到客户端，请先下载运行 FLA.exe', 'warn'); window.location.href = 'fla://open?test=1'; }
  };
}

function viewMobileCenter() {
  document.title = '手机端 - FLA';
  const wx = /[?&]wx=1/.test(location.hash) || /MicroMessenger| QQ\//i.test(navigator.userAgent);
  const ua = navigator.userAgent, isIOS = /iPhone|iPad/i.test(ua), isAnd = /Android|Harmony/i.test(ua);
  const qr = (isIOS || isAnd) ? '' : UI.qrSvgString(location.origin + '/api/app/get', 168);
  const row = (title, sub, btns) =>
    '<div class="card" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:16px 18px;margin-bottom:10px">' +
    '<div style="flex:1;min-width:180px"><b style="font-size:15.5px">' + title + '</b><div class="muted" style="font-size:12.5px;margin-top:2px">' + sub + '</div></div>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap">' + btns + '</div></div>';
  const a = (href, t, primary) => '<a class="btn sm' + (primary ? ' primary' : '') + '" href="' + href + '">' + t + '</a>';
  const ipa = location.origin + '/api/app/ios.ipa';
  const rows = {
    and: row('安卓', 'Android 7+ · 整屏投屏', a('/api/app/android', '下载 APK <small id="mc-apk"></small>', true)),
    hm: row('鸿蒙 NEXT', '应用市场装「卓易通」，导入 APK', a('/api/app/android', '下载 APK')),
    ios: row('iPhone 主屏版', '描述文件 · 免安装', a('/api/app/ios.mobileconfig', '安装描述文件', true)),
    ipa: row('iPhone 完整版', 'TrollStore 永久安装 · iOS 14 – 17.0', a('apple-magnifier://install?url=' + encodeURIComponent(ipa), '装到 TrollStore', true) + a('/api/app/ios.ipa', '下载 IPA')),
    web: row('网页版', '无需安装', a('/cast.html', '打开')),
  };
  const order = isIOS ? ['ios', 'ipa', 'web', 'and', 'hm'] : isAnd ? ['and', 'hm', 'web', 'ios', 'ipa'] : ['and', 'ios', 'ipa', 'hm', 'web'];
  $('#app').innerHTML = shell(
    (wx ? '<div onclick="this.remove()" style="position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:99;color:#fff;padding:24px;text-align:right;font-size:17px;line-height:1.7">点右上角 <b>···</b><br>选「在浏览器中打开」</div>' : '') +
    '<div style="max-width:640px;margin:5vh auto 40px;padding:0 16px">' +
      '<div style="text-align:center;margin-bottom:22px"><h1 style="font-size:32px;margin-bottom:4px">FLA 手机端</h1>' +
      '<p class="muted" style="letter-spacing:3px">轻量 · 高效 · 帮助</p>' +
      (qr ? '<div style="display:inline-block;background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px 14px 8px;margin-top:16px">' + qr + '<div class="muted" style="font-size:12px">手机扫码</div></div>' : '') +
      '</div>' + order.map(k => rows[k]).join('') +
      (isIOS ? '<details class="card" style="padding:14px 18px;margin-top:6px"><summary style="cursor:pointer;font-weight:600">iPhone 完整版怎么装？</summary>' + iosGuide() + '</details>' : '') +
    '</div>', 'mobile');
  bindLogout();
  fetch('/api/app/info').then(r => r.json()).then(d => {
    const el = document.getElementById('mc-apk');
    if (el && d.android && d.android.version) el.textContent = 'v' + d.android.version;
  }).catch(() => { });
}

/* ================================================================
 *  10. 官方门户 / 官网主页 (viewHome)
 * ================================================================ */
function viewHome() {
  document.title = 'FLA · 轻量 · 高效 · 帮助';
  const u = App.user;
  const navUserSection = u
    ? '<a href="#/library" class="btn primary sm home-nav-dl">' + UI.icon('folder', 14) + ' <span>进入控制台</span></a>'
    : '<a href="#/login" class="home-link">登录</a>' +
      '<a href="#/register" class="home-link">注册</a>' +
      '<a href="/api/desktop/download" class="btn primary sm home-nav-dl">' + UI.icon('download', 14) + ' <span>下载客户端</span></a>';

  const heroActions = u
    ? '<a href="/api/desktop/download" class="btn primary lg home-btn-dl">' +
        UI.icon('download', 18) +
        '<span><strong>下载 Windows 客户端</strong></span>' +
      '</a>' +
      '<a href="#/library" class="btn lg home-btn-portal">' + UI.icon('folder', 16) + ' 进入课件工作台</a>' +
      '<a href="#/remote" class="btn soft lg home-btn-reg">' + UI.icon('qr', 15) + ' 手机扫码遥控</a>'
    : '<a href="/api/desktop/download" class="btn primary lg home-btn-dl">' +
        UI.icon('download', 18) +
        '<span><strong>下载 Windows 客户端</strong></span>' +
      '</a>' +
      '<a href="#/login" class="btn lg home-btn-portal">' + UI.icon('external', 16) + ' 登录网页控制台</a>' +
      '<a href="#/register" class="btn soft lg home-btn-reg">注册新账号</a>' +
      '<a href="#/remote" class="btn soft lg home-btn-reg">' + UI.icon('qr', 15) + ' 手机遥控</a>';

  $('#app').innerHTML = shell(
    '<section class="home-hero" style="min-height:66vh;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center">' +
      '<h1 class="home-hero-title" style="font-size:56px;letter-spacing:2px">FLA</h1>' +
      '<p class="home-hero-desc" style="font-size:20px;letter-spacing:6px;margin:6px 0 30px">轻量 · 高效 · 帮助</p>' +
      '<div class="live-stats" id="live-stats">' +
        '<div class="ls-item"><b data-k="users">–</b><span>注册用户</span></div>' +
        '<div class="ls-item"><b data-k="online">–</b><span>当前在线</span></div>' +
        '<div class="ls-item"><b data-k="hits">–</b><span>累计访问</span></div>' +
        '<div class="ls-item"><b data-k="traffic">–</b><span>累计流量</span></div>' +
      '</div>' +
      '<div class="home-hero-actions">' + heroActions + '</div>' +
    '</section>', 'home');
  bindLogout();
  startLiveStats();
  const fLink = $('#nav-features-link');
  if (fLink) {
    fLink.onclick = e => {
      e.preventDefault();
      const sec = $('#features');
      if (sec) sec.scrollIntoView({ behavior: 'smooth' });
    };
  }
}

/* v3.6: 首页实时数据 (用户数/在线/访问/流量), 每 5 秒刷新 */
function fmtTraffic(n) {
  n = Number(n) || 0;
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
  if (n < 1024 ** 3) return (n / 1024 / 1024).toFixed(1) + ' MB';
  if (n < 1024 ** 4) return (n / 1024 ** 3).toFixed(2) + ' GB';
  return (n / 1024 ** 4).toFixed(2) + ' TB';
}
function fmtCount(n) { return (Number(n) || 0).toLocaleString('en-US'); }
function startLiveStats() {
  let stopped = false;
  const tick = async () => {
    const box = $('#live-stats');
    if (stopped || !box) return;
    try {
      const d = await API.get('/api/stats/live');
      const set = (k, v) => { const el = box.querySelector('[data-k="' + k + '"]'); if (el) el.textContent = v; };
      set('users', fmtCount(d.users));
      set('online', fmtCount(d.online));
      set('hits', fmtCount(d.hits));
      set('traffic', fmtTraffic(d.traffic_bytes));
    } catch (e) { }
  };
  tick();
  const t = setInterval(tick, 5000);
  App.setCleanup(() => { stopped = true; clearInterval(t); });
}

/* ================================================================
 *  11. 登录 / 注册 / 扫码登录
 * ================================================================ */
function viewLogin() {
  document.title = '登录 - FLA';
  const regLink = '<p class="auth-foot" style="margin-top:16px;font-size:13px;color:var(--mut);">还没有账号？<a href="#/register">使用邀请码注册</a></p><p class="auth-extra-links"><a href="#/home">返回官网首页</a> · <a href="/api/desktop/download">下载 Windows 客户端</a></p>';
  $('#app').innerHTML =
    '<div class="auth-bg"><div class="auth-card modal" style="max-width:380px;margin:auto;padding:32px 28px;background:#fff;border-radius:18px;box-shadow:var(--shadow-xl);">' +
    '<div class="auth-logo" style="text-align:center;margin-bottom:12px;color:var(--brand);">' + UI.icon('board', 38) + '</div>' +
    '<h1 style="text-align:center;font-size:24px;margin-bottom:4px;">FLA</h1><p class="sub" style="text-align:center;color:var(--mut);font-size:13.5px;margin-bottom:20px;">轻量 · 高效 · 帮助</p>' +
    '<div class="auth-tabs view-toggle" style="margin-bottom:18px;"><button class="view-toggle-btn active" id="lt-pw" style="flex:1;">密码登录</button><button class="view-toggle-btn" id="lt-qr" style="flex:1;">' + UI.icon('qr', 14) + ' 扫码登录</button></div>' +
    '<form id="f">' +
    '<label>用户名<input name="username" autocomplete="username" required></label>' +
    '<label>密码<input name="password" type="password" autocomplete="current-password" required></label>' +
    '<button class="btn primary block" type="submit" style="margin-top:16px;">登 录</button></form>' +
    '<div id="qrbox" class="qr-login hidden" style="text-align:center;padding:16px 0;">' +
    '<div class="qr-holder" id="qr-holder" style="display:flex;justify-content:center;margin-bottom:12px;"><div class="qr-spin"></div></div>' +
    '<p class="qr-tip" style="font-size:13px;color:var(--ink2);">用<b>已登录 FLA 的设备</b>扫码授权登录</p>' +
    '<p class="qr-tip muted" style="font-size:12px;color:var(--mut);">登录页二维码每 2 分钟自动刷新</p>' +
    '</div>' +
    regLink +
    '</div></div>';

  $('#f').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await API.post('/api/auth/login', { username: String(fd.get('username')).trim(), password: String(fd.get('password')) });
      API.setToken(r.token); App.user = r.user;
      afterLoginGo();
    } catch (err) { toast(err.message, 'err'); }
  };

  let qrTimers = [];
  function stopQrLogin() { qrTimers.forEach(t => clearInterval(t) || clearTimeout(t)); qrTimers = []; }
  async function startQrLogin() {
    stopQrLogin();
    const holder = $('#qr-holder');
    if (!holder) return;
    holder.innerHTML = '<div class="qr-spin"></div>';

    let ticket = '';
    /* v3.1: 票据接口只取轻量 JSON(不带 SVG), 二维码在本地用当前域名生成;
       跨境慢链路: 单次 20s 超时 + 自动重试 3 次, 失败给出「重试」按钮 */
    const refresh = async (attempt) => {
      attempt = attempt || 0;
      if (attempt === 0 && !holder.querySelector('svg,canvas,img,table')) holder.innerHTML = '<div class="qr-spin"></div>';
      try {
        const r = await API.req('/api/auth/qr/ticket', { method: 'POST', timeout: 20000 });
        ticket = r.ticket;
        const url = location.origin + '/#/qr-approve?ticket=' + encodeURIComponent(ticket);
        /* 1) 浏览器离线生成 SVG  2) 服务器返回的 SVG  3) 服务器图片 */
        if (!(UI && UI.renderQR && UI.renderQR(holder, url, 190))) {
          if (r.qr_svg && r.qr_svg.trim().indexOf('<svg') === 0) {
            holder.innerHTML = r.qr_svg;
            const svg = holder.querySelector('svg');
            if (svg) { svg.setAttribute('width', '190'); svg.setAttribute('height', '190'); svg.style.width = '190px'; svg.style.height = '190px'; }
          } else {
            holder.innerHTML = '<img alt="二维码" width="190" height="190" style="width:190px;height:190px;display:block" src="/api/auth/qr/image?ticket=' +
              encodeURIComponent(ticket) + '&o=' + encodeURIComponent(location.origin) + '">';
          }
        }
      } catch (e) {
        if (attempt < 3 && $('#qr-holder') === holder) { setTimeout(() => refresh(attempt + 1), 1500); return; }
        holder.innerHTML = '<div class="qr-err" style="color:var(--danger);font-size:13px;">二维码加载失败（网络较慢）<br><button class="btn sm soft" id="qr-retry" style="margin-top:8px">重试</button></div>';
        const b = $('#qr-retry'); if (b) b.onclick = () => refresh(0);
      }
    };
    await refresh();
    qrTimers.push(setInterval(refresh, 120000));

    const poll = async () => {
      if (!ticket) return;
      try {
        const r = await API.get('/api/auth/qr/status?ticket=' + encodeURIComponent(ticket));
        if (r.status === 'ok' && r.token) {
          stopQrLogin();
          API.setToken(r.token); App.user = r.user;
          toast('扫码授权成功 ✓', 'ok');
          afterLoginGo();
        } else if (r.status === 'invalid' || r.status === 'expired') {
          ticket = '';
          refresh(); // 生成新二维码
        }
      } catch (e) { }
    };
    qrTimers.push(setInterval(poll, 1500));
    App.setCleanup(stopQrLogin);
  }

  const switchTab = qr => {
    $('#lt-pw').classList.toggle('active', !qr);
    $('#lt-qr').classList.toggle('active', qr);
    const f = $('#f'), qb = $('#qrbox');
    /* 注意: #qrbox 初始带 .hidden (display:none !important), 必须切换 class 而不是改 style */
    if (f) { f.classList.toggle('hidden', qr); f.style.display = ''; }
    if (qb) { qb.classList.toggle('hidden', !qr); qb.style.display = ''; }
    if (qr) startQrLogin(); else stopQrLogin();
  };
  if ($('#lt-pw')) $('#lt-pw').onclick = () => switchTab(false);
  if ($('#lt-qr')) $('#lt-qr').onclick = () => switchTab(true);
  switchTab(false);
}

function afterLoginGo() {
  let back = '';
  try { back = sessionStorage.getItem('fla_after_login') || ''; sessionStorage.removeItem('fla_after_login'); } catch (e) { }
  const target = back || '#/library';
  if (location.hash === target) {
    route();
  } else {
    location.hash = target;
  }
}

async function viewRegister() {
  document.title = '注册 - FLA';
  let open = true;
  try { open = (await API.get('/api/auth/config')).registration_open; } catch (e) { }
  if (!open) {
    $('#app').innerHTML = '<div class="auth-bg"><div class="auth-card modal" style="max-width:380px;margin:auto;padding:32px;background:#fff;border-radius:18px;"><h1>注册未开放</h1><p class="sub">请联系管理员获取邀请码或直接分配账号</p><p class="auth-foot"><a href="#/login">返回登录</a></p></div></div>';
    return;
  }
  $('#app').innerHTML =
    '<div class="auth-bg"><div class="auth-card modal" style="max-width:380px;margin:auto;padding:32px 28px;background:#fff;border-radius:18px;box-shadow:var(--shadow-xl);">' +
    '<div class="auth-logo" style="text-align:center;margin-bottom:12px;color:var(--brand);">' + UI.icon('board', 38) + '</div>' +
    '<h1 style="text-align:center;font-size:24px;margin-bottom:4px;">注册账号</h1><p class="sub" style="text-align:center;color:var(--mut);font-size:13.5px;margin-bottom:20px;">FLA 智慧互动教学系统</p>' +
    '<form id="f">' +
    '<label>用户名<input name="username" autocomplete="username" minlength="2" maxlength="32" required placeholder="用于登录"></label>' +
    '<label>姓名 / 昵称<input name="nickname" maxlength="32" required placeholder="课件与互动展示名称"></label>' +
    '<label>密码<input name="password" type="password" minlength="6" autocomplete="new-password" required placeholder="至少 6 位"></label>' +
    '<label>邀请码<input name="invite_code" required placeholder="向管理员获取教师邀请码"></label>' +
    '<button class="btn primary block" type="submit" style="margin-top:16px;">注 册</button></form>' +
    '<p class="auth-foot" style="margin-top:16px;font-size:13px;color:var(--mut);text-align:center;">已有账号？<a href="#/login">立即登录</a></p>' +
    '<p class="auth-extra-links"><a href="#/home">返回官网首页</a> · <a href="/api/desktop/download">下载 Windows 客户端</a></p>' +
    '</div></div>';
  $('#f').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const invite = String(fd.get('invite_code') || '').trim();
    if (!invite) { toast('请填写管理员提供的邀请码', 'err'); return; }
    try {
      const r = await API.post('/api/auth/register', {
        username: String(fd.get('username')).trim(),
        nickname: String(fd.get('nickname')).trim(),
        password: String(fd.get('password')),
        invite_code: invite,
      });
      API.setToken(r.token); App.user = r.user;
      toast('注册成功，欢迎使用 FLA', 'ok');
      location.hash = '#/library';
    } catch (err) { toast(err.message, 'err'); }
  };
}

/* ================================================================
 *  12. 个人中心与资料修改 (viewProfile)
 * ================================================================ */
async function viewProfile() {
  document.title = '个人中心 - FLA';
  try { App.user = await API.get('/api/auth/me'); } catch (e) { return; }
  const u = App.user;
  $('#app').innerHTML = shell(
    (u.must_change_password ? '<div class="warnbar" style="background:#fffbeb;border:1px solid #fef3c7;color:#b45309;padding:12px 16px;border-radius:10px;margin-bottom:16px;">⚠ 当前使用初始密码，请尽快修改</div>' : '') +
    '<div class="profile-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px;">' +
    '<div class="card p-card" style="padding:24px;border-radius:16px;background:#fff;border:1px solid var(--line);box-shadow:var(--shadow-sm);"><h3>个人资料</h3>' +
    '<div class="avatar-big" id="avwrap" style="text-align:center;margin:16px 0 10px;">' + avatarHTML(u, 84) + '</div>' +
    '<div style="text-align:center;margin-bottom:14px;"><button class="btn sm" id="avbtn">更换头像</button><input type="file" id="avin" hidden accept="image/png,image/jpeg,image/webp,image/gif"></div>' +
    '<label>昵称<input id="nk" maxlength="32" value="' + UI.esc(u.nickname) + '"></label>' +
    '<label>个性签名<textarea id="sg" maxlength="200" rows="3" placeholder="写点什么介绍自己…">' + UI.esc(u.signature) + '</textarea></label>' +
    '<div style="margin:20px 0;">' + certCardHTML(u) + '</div>' +
    '<button class="btn primary" id="savep">保存资料</button></div>' +

    '<div class="card p-card" style="padding:24px;border-radius:16px;background:#fff;border:1px solid var(--line);box-shadow:var(--shadow-sm);"><h3>账号与安全</h3>' +
    '<div class="kv" style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line);font-size:13.5px;"><span>用户名</span><b>' + UI.esc(u.username) + '</b></div>' +
    '<div class="kv" style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line);font-size:13.5px;"><span>角色</span><b>' + (u.role === 'admin' ? '管理员' : '教师用户') + '</b></div>' +
    '<div class="kv" style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line);font-size:13.5px;"><span>存储空间</span><b>' + UI.fmtSize(u.used_bytes) + ' / ' + UI.fmtSize(u.quota_bytes) + '</b></div>' +
    '<div class="kv" style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line);font-size:13.5px;"><span>注册时间</span><b>' + UI.fmtDate(u.created_at) + '</b></div>' +
    '<hr><h3>修改密码</h3>' +
    '<label>原密码<input id="pw0" type="password" autocomplete="current-password"></label>' +
    '<label>新密码<input id="pw1" type="password" minlength="6" autocomplete="new-password"></label>' +
    '<label>确认新密码<input id="pw2" type="password" minlength="6" autocomplete="new-password"></label>' +
    '<button class="btn" id="savepw" style="margin-top:12px;">修改密码</button></div>' +
    '</div>', 'profile');
  bindLogout();
  bindCertCards();

  $('#avbtn').onclick = () => $('#avin').click();
  $('#avin').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    const form = new FormData(); form.append('file', f, f.name || 'avatar.png');
    try {
      const r = await API.upload('/api/users/avatar', form);
      $('#avwrap').innerHTML = '<img class="avatar" style="width:84px;height:84px;border-radius:50%;object-fit:cover;" src="' + UI.esc(r.avatar) + '">';
      toast('头像已更新', 'ok');
    } catch (err) { toast(err.message, 'err'); }
    e.target.value = '';
  };
  $('#savep').onclick = async () => {
    try {
      App.user = await API.put('/api/users/profile', { nickname: $('#nk').value, signature: $('#sg').value });
      toast('已保存', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  };
  $('#savepw').onclick = async () => {
    const p1 = $('#pw1').value, p2 = $('#pw2').value;
    if (p1 !== p2) { toast('两次输入的新密码不一致', 'err'); return; }
    try {
      await API.post('/api/auth/change_password', { old_password: $('#pw0').value, new_password: p1 });
      toast('密码已修改', 'ok');
      $('#pw0').value = $('#pw1').value = $('#pw2').value = '';
    } catch (err) { toast(err.message, 'err'); }
  };
}

/* ================================================================
 *  13. 打开课件与调用本地放映 (openFile / openLocalFile)
 * ================================================================ */
function openFile(f) {
  if (f.kind === 'board') { location.hash = '#/view/' + f.id; return; }
  const ext = ((f.name || '').split('.').pop() || '').toLowerCase();
  const ms = f.kind === 'office' && /^(ppt|pptx|doc|docx|xls|xlsx)$/.test(ext);
  if (ms) {
    location.hash = '#/view/' + f.id;
    return;
  }
  window.open('/present.html?fid=' + f.id + '&token=' + encodeURIComponent(API.token), '_blank');
}

async function openLocalFile(f) {
  toast('正在尝试调用本地放映…');
  let directUrl = location.origin + '/api/files/' + f.id + '/download?token=' + encodeURIComponent(API.token);
  try {
    const share = await API.get('/api/files/' + f.id + '/share-link');
    if (share && share.direct) directUrl = share.direct;
  } catch (e) {}

  const ext = ((f.name || '').split('.').pop() || '').toLowerCase();
  const isPpt = /^(ppt|pptx|pps|ppsx|dps)$/.test(ext);
  const isDoc = /^(doc|docx|rtf|wps)$/.test(ext);
  const isXls = /^(xls|xlsx|csv|et)$/.test(ext);
  const officeScheme = isPpt ? 'ms-powerpoint:ofe|u|' : isDoc ? 'ms-word:ofe|u|' : isXls ? 'ms-excel:ofe|u|' : '';

  // 1. 请求本地 127.0.0.1:8307
  let bridgeSuccess = false;
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 1200);
    const res = await fetch('http://127.0.0.1:8307/api/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fid: f.id,
        name: f.name,
        url: directUrl,
        token: API.token
      }),
      signal: ctrl.signal
    });
    clearTimeout(tid);
    const d = await res.json();
    if (d && d.ok) {
      toast(d.msg || '已成功调起本地桌面客户端放映！', 'ok');
      bridgeSuccess = true;
      return;
    }
  } catch (err) {}

  // 2. 本地 8307 客户端未启动：提供选择
  if (!bridgeSuccess) {
    const dlUrl = '/api/files/' + f.id + '/download?token=' + encodeURIComponent(API.token);
    if (officeScheme && directUrl.startsWith('http')) {
      try { window.location.href = officeScheme + encodeURI(directUrl); } catch (e) {}
    }

    UI.modal({
      title: '本地放映方式选择',
      body: '<div style="line-height:1.65;font-size:14px;color:#27272a;">' +
        '<p style="margin-bottom:8px;">课件 <b>《' + UI.esc(f.name) + '》</b> 支持以下本地播放方式：</p>' +
        '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;margin:12px 0;font-size:13px;color:#475569;">' +
          '<b>💡 推荐放映途径：</b><br>' +
          '1. <b>本地引擎放映：</b>无需安装任何 Office 软件，直接在浏览器中全屏放映并保留画笔、计时、抽选功能；<br>' +
          '2. <b>FLA 原生桌面客户端：</b>专为多媒体教室研发，自带云存储与投屏功能，启动零延迟。' +
        '</div>' +
        '<div style="display:flex;gap:10px;margin-top:16px;flex-wrap:wrap;">' +
          '<button class="btn primary" id="modalLocalPresent" style="text-decoration:none;">' + UI.icon('play', 15) + ' 启动本地引擎放映</button>' +
          '<a href="' + dlUrl + '" class="btn" style="text-decoration:none;">' + UI.icon('download', 15) + ' 下载课件到本地</a>' +
          '<a href="/api/desktop/download" class="btn" style="text-decoration:none;">' + UI.icon('external', 15) + ' 下载桌面客户端</a>' +
        '</div>' +
      '</div>',
      width: '500px'
    });

    const lpBtn = document.getElementById('modalLocalPresent');
    if (lpBtn) {
      lpBtn.onclick = () => {
        window.open('/present.html?fid=' + f.id + '&token=' + encodeURIComponent(API.token) + '&track=local', '_blank');
      };
    }
  }
}

/* 站点外观: 背景 / 登录页背景 / 界面透明度 (管理员在后台或客户端设置) */
window.applyTheme = applyTheme;
function applyTheme(c) {
  if (!c) return;
  const css = v => /^#[0-9a-f]{3,8}$/i.test(v) ? v : (/^(https?:\/\/|\/)/.test(v) ? 'url("' + v.replace(/"/g, '') + '") center/cover fixed no-repeat' : '');
  const op = Math.max(30, Math.min(100, +c.ui_opacity || 100)) / 100;
  const site = css(c.site_bg || ''), login = css(c.login_bg || '') || site;
  let t = '';
  if (site) t += 'html,body{background:' + site + ' !important}.home-portal,.home-main{background:transparent !important}';
  if (login) t += '.auth-bg{background:' + login + ' !important}';
  if (op < 1 || site || login) {
    const w = 'rgba(255,255,255,' + op + ')';
    t += ':root{--paper:' + w + '}.topbar,.home-nav,.home-footer{background:rgba(255,255,255,' + Math.min(op, .92) + ') !important}' +
      '.auth-card{background:' + w + ' !important;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}' +
      '.card,.modal,.nav,.side,.sidebar{backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}';
  }
  let el = document.getElementById('fla-theme');
  if (!el) { el = document.createElement('style'); el.id = 'fla-theme'; document.head.appendChild(el); }
  el.textContent = t;
}
fetch('/api/auth/config').then(r => r.json()).then(c => { App.config = c; applyTheme(c); }).catch(() => { });

function loadScript(src) {
  return new Promise((res, rej) => {
    if (src.includes('qrcode') && window.QRCode) return res();
    if (src.includes('jsqr') && window.jsQR) return res();
    const cleanSrc = src.split('?')[0];
    const existing = document.querySelector('script[src*="' + cleanSrc + '"]') || document.querySelector('script[data-fla="' + cleanSrc + '"]');
    if (existing) {
      if (existing.getAttribute('data-loaded') === '1' || (src.includes('qrcode') && window.QRCode) || (src.includes('jsqr') && window.jsQR)) {
        return res();
      }
      existing.addEventListener('load', () => { existing.setAttribute('data-loaded', '1'); res(); });
      existing.addEventListener('error', () => rej(new Error('组件加载失败')));
      return;
    }
    const s = document.createElement('script');
    s.src = src; s.setAttribute('data-fla', cleanSrc);
    s.onload = () => { s.setAttribute('data-loaded', '1'); res(); };
    s.onerror = () => rej(new Error('组件加载失败'));
    document.head.appendChild(s);
  });
}

/* ---------- 公告 ---------- */
async function refreshAnnBadge() {
  if (!App.user) return;
  try {
    const r = await API.get('/api/announcements');
    App.ann = r;
    const dot = $('#ann-dot');
    if (dot) dot.classList.toggle('hidden', !r.unread);
    popupAnns(r.items || []);
  } catch (e) { }
}

/* 未读公告自动弹出: 重要公告需等 5 秒才能关闭, 普通公告可立即关闭 */
let annPopping = false;
function popupAnns(items) {
  if (annPopping) return;
  const seen = JSON.parse(sessionStorage.getItem('fla_ann_popped') || '[]');
  const q = items.filter(a => !a.read && seen.indexOf(a.id) < 0)
    .sort((a, b) => (b.level === 'imp') - (a.level === 'imp'));
  if (!q.length) return;
  annPopping = true;
  const next = () => {
    const a = q.shift();
    if (!a) { annPopping = false; const d = $('#ann-dot'); if (d) d.classList.add('hidden'); return; }
    seen.push(a.id); sessionStorage.setItem('fla_ann_popped', JSON.stringify(seen));
    const imp = a.level === 'imp';
    const ov = document.createElement('div');
    ov.className = 'ann-pop-ov';
    ov.style.cssText = 'position:fixed;inset:0;z-index:10050;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:20px';
    ov.innerHTML = '<div style="background:#fff;border-radius:16px;max-width:480px;width:100%;padding:24px 24px 18px;box-shadow:0 20px 60px rgba(0,0,0,.3)">' +
      '<div style="font-size:12px;font-weight:700;color:' + (imp ? '#c62828' : '#666') + ';margin-bottom:6px">' + (imp ? '重要公告' : (a.level === 'warn' ? '注意' : '公告')) + '</div>' +
      '<h3 style="font-size:19px;margin-bottom:10px">' + UI.esc(a.title) + '</h3>' +
      '<div style="font-size:14.5px;line-height:1.65;max-height:50vh;overflow:auto;color:#333">' + UI.esc(a.content || '').replace(/\n/g, '<br>') + '</div>' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:18px"><span style="font-size:12px;color:#999">' + UI.fmtDate(a.created_at) + '</span>' +
      '<button class="btn primary" id="ann-pop-ok">我知道了</button></div></div>';
    document.body.appendChild(ov);
    const btn = ov.querySelector('#ann-pop-ok');
    const close = () => {
      ov.remove();
      API.post('/api/announcements/read', { ids: [a.id] }).catch(() => { });
      next();
    };
    if (imp) {
      let n = 5; btn.disabled = true; btn.textContent = '请阅读 ' + n + ' 秒';
      const t = setInterval(() => {
        n--;
        if (n > 0) btn.textContent = '请阅读 ' + n + ' 秒';
        else { clearInterval(t); btn.disabled = false; btn.textContent = '我知道了'; btn.onclick = close; }
      }, 1000);
    } else {
      btn.onclick = close;
      ov.addEventListener('click', e => { if (e.target === ov) close(); });
    }
  };
  next();
}

async function openAnnPanel() {
  const r = await API.get('/api/announcements').catch(() => null);
  if (!r) return;
  const lv = { info: '公告', warn: '注意', imp: '重要' };
  const items = r.items.length ? r.items.map(a =>
    '<div class="ann-item ' + a.level + (a.read ? '' : ' unread') + '" style="padding:12px;margin-bottom:8px;border-radius:10px;background:var(--bg-subtle);">' +
    '<div class="ann-head" style="display:flex;justify-content:space-between;margin-bottom:6px;"><b>' + UI.esc(a.title) + '</b>' +
    (a.personal ? '<span class="ann-personal" style="color:var(--brand);font-size:11px;font-weight:700;">专属</span>' : '') +
    '<span class="ann-lv ' + a.level + '" style="font-size:11px;padding:2px 6px;border-radius:4px;background:#e2e8f0;">' + (lv[a.level] || '公告') + '</span></div>' +
    (a.content ? '<div class="ann-body" style="font-size:13.5px;color:var(--ink-secondary);line-height:1.5;">' + UI.esc(a.content).replace(/\n/g, '<br>') + '</div>' : '') +
    '<div class="ann-time" style="font-size:11.5px;color:var(--mut);margin-top:6px;">' + UI.fmtDate(a.created_at) + '</div></div>'
  ).join('') : '<div class="empty">暂无公告</div>';
  const m = UI.modal({ title: UI.icon('horn', 18) + ' 公告', titleHTML: true,
    body: '<div class="ann-list">' + items + '</div>' });
  const unread = r.items.filter(a => !a.read).map(a => a.id);
  if (unread.length) API.post('/api/announcements/read', { ids: unread }).then(refreshAnnBadge).catch(() => { });
  refreshAnnBadge();
}

/* ---------- 扫码(登录其他设备) ---------- */
async function openScanModal() {
  const m = UI.modal({ title: UI.icon('qr', 18) + ' 扫码登录其他设备', titleHTML: true,
    body: '<div class="scan-box" style="text-align:center;"><video id="scan-v" playsinline muted style="width:100%;max-width:280px;border-radius:12px;"></video>' +
    '<div class="scan-tip" id="scan-tip" style="margin-top:10px;font-size:13px;color:var(--mut);">正在启动相机…</div></div>' +
    '<div class="scan-manual" style="margin-top:14px;display:flex;gap:8px;align-items:center;"><span>手动输入票据:</span>' +
    '<input id="scan-ticket" placeholder="qr 开头的票据码">' +
    '<button class="btn sm" id="scan-go">授权</button></div>' });
  let stream = null, raf = 0, stop = false;
  const cleanup = () => { stop = true; cancelAnimationFrame(raf); if (stream) stream.getTracks().forEach(t => t.stop()); };
  m.el.addEventListener('mousedown', e => { if (e.target === m.el) cleanup(); }, { once: false });
  const tip = $('#scan-tip');
  const approve = async ticket => {
    ticket = (ticket || '').trim();
    if (!/^qr[0-9a-f]{16,}$/.test(ticket)) { toast('票据格式不对', 'err'); return false; }
    try {
      await API.post('/api/auth/qr/approve', { ticket });
      toast('已授权该设备登录 ✓', 'ok'); cleanup(); m.close();
      return true;
    } catch (e) { toast(e.message, 'err'); return false; }
  };
  $('#scan-go').onclick = () => approve($('#scan-ticket').value);
  try {
    await loadScript('/lib/jsqr/jsQR.js');
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    const v = $('#scan-v'), cv = document.createElement('canvas'), cx = cv.getContext('2d');
    v.srcObject = stream; await v.play();
    tip.textContent = '对准另一台设备上的登录二维码';
    const loop = () => {
      if (stop || !v.videoWidth) { if (!stop) raf = requestAnimationFrame(loop); return; }
      cv.width = v.videoWidth; cv.height = v.videoHeight;
      cx.drawImage(v, 0, 0);
      const img = cx.getImageData(0, 0, cv.width, cv.height);
      const code = window.jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' });
      if (code && code.data) {
        const mm = /qr-approve\?ticket=(qr[0-9a-f]{16,})/.exec(code.data);
        if (mm) { approve(mm[1]); return; }
      }
      raf = requestAnimationFrame(loop);
    };
    loop();
  } catch (e) {
    tip.textContent = '相机不可用(' + (location.protocol === 'https:' ? '无摄像头' : '需 HTTPS') + ')。用手机扫码或在下方手动输入票据。';
  }
}

function viewQrApprove() {
  document.title = '扫码授权 - FLA';
  const q = new RegExp('[?&]ticket=([^&]*)').exec(location.hash);
  const ticket = q ? decodeURIComponent(q[1]) : '';
  if (!App.user) {
    try { sessionStorage.setItem('fla_after_login', location.hash); } catch (e) { }
    toast('请先登录, 登录后自动回到本页授权');
    location.hash = '#/login';
    return;
  }
  $('#app').innerHTML =
    '<div class="auth-bg"><div class="auth-card modal" style="max-width:380px;margin:auto;padding:32px 28px;background:#fff;border-radius:18px;box-shadow:var(--shadow-xl);">' +
    '<div class="auth-logo" style="text-align:center;color:var(--brand);margin-bottom:12px;">' + UI.icon('qr', 38) + '</div>' +
    '<h1 style="font-size:24px;text-align:center;margin-bottom:4px;">扫码登录授权</h1>' +
    '<p class="sub" style="text-align:center;color:var(--mut);font-size:13.5px;margin-bottom:18px;">' + UI.esc(App.user.nickname) + ' (' + UI.esc(App.user.username) + ')</p>' +
    '<div id="qr-approve-body">' +
    '<p style="font-size:14px;line-height:1.7;color:var(--ink2);">另一台设备正在请求登录你的账号。<br>确认是你本人在操作？</p>' +
    '<button class="btn primary block" id="qr-yes" style="margin-top:16px;">确认授权登录</button>' +
    '<p class="auth-foot" style="font-size:12px;color:var(--mut);margin-top:12px;text-align:center;">不是你操作的请忽略本页(票据 2 分钟后自动失效)</p>' +
    '</div></div></div>';
  $('#qr-yes').onclick = async () => {
    try {
      await API.post('/api/auth/qr/approve', { ticket });
      $('#qr-approve-body').innerHTML = '<p style="font-size:15px;font-weight:700;color:var(--brand);text-align:center;margin:18px 0;">✓ 已授权，另一台设备即将自动登录</p><p class="auth-foot" style="text-align:center;"><a href="#/library">返回课件库</a></p>';
    } catch (e) { toast(e.message, 'err'); }
  };
}

/* ================================================================
 *  14. 论坛 (viewForum)
 * ================================================================ */
App.forum = { board: 0, thread: 0, page: 1, q: '' };

async function viewForum(openTid) {
  document.title = '论坛 - FLA';
  if (openTid) App.forum.thread = openTid;
  $('#app').innerHTML = shell('<div id="forum"></div>', 'forum');
  bindLogout();
  if (App.forum.thread) renderForumThread(App.forum.thread);
  else renderForumList();
}

async function renderForumList() {
  const box = $('#forum');
  if (!box) return;
  App.forum.thread = 0;
  const admin = App.user.role === 'admin';
  let boards = [];
  try { boards = (await API.get('/api/forum/boards')).items; } catch (e) { toast(e.message, 'err'); return; }
  const qs = [];
  if (App.forum.board) qs.push('board=' + App.forum.board);
  if (App.forum.q) qs.push('q=' + encodeURIComponent(App.forum.q));
  const thUrl = '/api/forum/threads' + (qs.length ? '?' + qs.join('&') : '');
  const th = await API.get(thUrl).catch(() => null);
  const threads = th ? th.items : [];
  const boardName = id => { const b = boards.find(x => x.id === id); return b ? UI.esc(b.name) : '全站'; };

  box.innerHTML =
    '<div class="lib-head" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">' +
      '<h2>教学论坛与交流</h2>' +
      '<div class="lib-actions" style="display:flex;gap:10px;flex-wrap:wrap;">' +
        '<input id="fb-q" class="inp" placeholder="搜索标题或内容" value="' + UI.esc(App.forum.q || '') + '" style="width:200px;margin:0;">' +
        '<select id="fb-sel" class="inp" style="width:auto;margin:0;">' +
          '<option value="0">全部板块</option>' + boards.map(b => '<option value="' + b.id + '"' + (App.forum.board === b.id ? ' selected' : '') + '>' + UI.esc(b.name) + '</option>').join('') +
        '</select>' +
        '<button class="btn primary" id="fb-new">' + UI.icon('plus', 15) + ' 发布新帖</button>' +
        (admin ? '<button class="btn ghost" id="fb-boards">板块管理</button>' : '') +
      '</div>' +
    '</div>' +
    '<div class="forum-boards" style="display:flex;gap:10px;margin-bottom:20px;overflow-x:auto;">' + boards.map(b =>
      '<button class="filter-pill' + (App.forum.board === b.id ? ' active' : '') + '" data-b="' + b.id + '"><b>' + UI.esc(b.name) + '</b> <span style="opacity:0.75;font-size:12px;">(' + b.threads + ')</span></button>').join('') +
    '</div>' +
    (threads.length ? '<div class="files-table-wrap"><table class="files-table"><thead><tr><th>主题</th><th style="width:120px">作者</th><th style="width:70px">回复</th><th style="width:140px">最后活动</th></tr></thead><tbody>' +
      threads.map(t =>
        '<tr><td><a class="ft-title" href="#/forum/' + t.id + '" style="font-weight:600;font-size:14.5px;">' + (t.pinned ? '<span style="color:var(--brand);margin-right:4px;">' + UI.icon('pin', 12) + '</span>' : '') + UI.esc(t.title) + (t.locked ? ' <span style="color:var(--mut);font-size:11.5px;">' + UI.icon('lock', 11) + '已锁</span>' : '') + '</a><br><span class="muted" style="font-size:12px;">' + boardName(t.board_id) + '</span></td>' +
        '<td><span style="display:inline-flex;align-items:center;gap:6px;">' + avatarHTML(t.author, 24) + ' ' + UI.esc(t.author.nickname) + certHTML(t.author) + '</span></td>' +
        '<td>' + t.replies + '</td><td class="muted">' + UI.fmtDate(t.last_reply_at) + '</td></tr>').join('') +
      '</tbody></table></div>' : '<div class="empty">' + UI.icon('forum', 38) + '<p style="margin-top:12px;">还没有帖子，点击右上角「发布新帖」开始交流</p></div>');

  $('#fb-sel').onchange = e => { App.forum.board = parseInt(e.target.value, 10); renderForumList(); };
  $('#fb-q').onkeydown = e => { if (e.key === 'Enter') { App.forum.q = e.target.value.trim(); renderForumList(); } };
  $$('.filter-pill[data-b]', box).forEach(b => b.onclick = () => {
    App.forum.board = App.forum.board === +b.dataset.b ? 0 : +b.dataset.b;
    renderForumList();
  });
  $('#fb-new').onclick = () => forumComposer(boards);
  if (window.AI) AI.models().catch(() => { });
  const bm = $('#fb-boards');
  if (bm) bm.onclick = () => forumBoardsAdmin(boards);
}

function forumComposer(boards) {
  const m = UI.modal({ title: '发布新帖',
    body: '<label>板块<select id="fc-board">' + boards.map(b => '<option value="' + b.id + '">' + UI.esc(b.name) + '</option>').join('') + '</select></label>' +
    '<label>标题<input id="fc-title" maxlength="100" placeholder="简短清晰的标题"></label>' +
    '<label>内容<textarea id="fc-content" rows="6" placeholder="支持写下您的教学心得、课件建议或提问…"></textarea></label>' });
  m.foot.innerHTML = '<button class="btn" id="fc-ai">' + UI.icon('sparkle', 14) + ' AI 起草</button><span style="flex:1"></span><button class="btn" id="fc-cancel">取消</button><button class="btn primary" id="fc-ok">发布</button>';
  if (window.AI) {
    AI.attach(m.body.querySelector('#fc-content'));
    m.foot.querySelector('#fc-ai').onclick = () => AI.draftModal((title, body) => {
      m.body.querySelector('#fc-title').value = title;
      m.body.querySelector('#fc-content').value = body;
    });
  }
  m.foot.querySelector('#fc-cancel').onclick = m.close;
  m.foot.querySelector('#fc-ok').onclick = async () => {
    try {
      const r = await API.post('/api/forum/threads', {
        board_id: +$('#fc-board').value, title: $('#fc-title').value, content: $('#fc-content').value
      });
      m.close();
      App.forum.thread = r.id;
      renderForumThread(r.id);
    } catch (e) { toast(e.message, 'err'); }
  };
}

async function renderForumThread(tid) {
  const box = $('#forum');
  if (!box) return;
  let t;
  try { t = await API.get('/api/forum/threads/' + tid); } catch (e) { toast(e.message, 'err'); App.forum.thread = 0; renderForumList(); return; }
  const admin = App.user.role === 'admin';
  const mine = t.mine || admin;
  box.innerHTML =
    '<div class="lib-head" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">' +
      '<h2>论坛帖子</h2>' +
      '<button class="btn ghost" id="ft-back">' + UI.icon('back', 14) + ' 返回帖子列表</button>' +
    '</div>' +
    '<div class="card ft-main" style="padding:24px;border-radius:16px;background:#fff;border:1px solid var(--line);margin-bottom:20px;box-shadow:var(--shadow-sm);">' +
      '<div class="ft-head" style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;">' +
        '<div>' +
          '<h3 style="font-size:20px;margin-bottom:6px;">' + (t.pinned ? '<span style="color:var(--brand);margin-right:6px;">' + UI.icon('pin', 16) + '</span>' : '') + UI.esc(t.title) + '</h3>' +
          '<div class="muted ft-meta" style="display:flex;align-items:center;gap:8px;font-size:13px;">' +
            avatarHTML(t.author, 22) + ' <b>' + UI.esc(t.author.nickname) + '</b>' + certHTML(t.author) + ' · ' + UI.fmtDate(t.created_at) +
            (t.locked ? ' · <b style="color:var(--danger)">已锁定</b>' : '') +
          '</div>' +
        '</div>' +
        (mine ? '<div class="ft-ops" style="display:flex;gap:6px;">' +
          (admin ? '<button class="btn xs ghost" id="ft-pin">' + (t.pinned ? '取消置顶' : '置顶') + '</button><button class="btn xs ghost" id="ft-lock">' + (t.locked ? '解锁' : '锁定') + '</button>' : '') +
          '<button class="btn xs ghost" id="ft-edit">编辑</button>' +
          '<button class="btn xs danger" id="ft-del">删除</button></div>' : '') +
      '</div>' +
      '<div class="ft-content" style="font-size:14.5px;line-height:1.7;color:var(--ink2);">' + UI.esc(t.content).replace(/\n/g, '<br>') + '</div>' +
    '</div>' +

    '<h4 style="font-size:16px;margin-bottom:14px;">回复讨论 (' + t.total + ')</h4>' +
    (t.posts.map(p =>
      '<div class="card ft-post" data-id="' + p.id + '" style="padding:16px 20px;border-radius:12px;background:#fff;border:1px solid var(--line);margin-bottom:12px;">' +
      '<div class="ft-meta" style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px;">' +
        '<div style="display:flex;align-items:center;gap:6px;">' + avatarHTML(p.author, 24) + '<b>' + UI.esc(p.author.nickname) + '</b>' + certHTML(p.author) +
        '<span class="muted"> · ' + UI.fmtDate(p.created_at) + (p.edited ? ' · 已编辑' + (p.edited_by_admin ? '(管理员)' : '') : '') + '</span></div>' +
        ((p.mine || admin) ? '<span class="ft-post-ops" style="display:flex;gap:6px;"><button class="btn xs ghost" data-op="edit">编辑</button><button class="btn xs danger" data-op="del">删除</button></span>' : '') +
      '</div>' +
      '<div class="ft-content" style="font-size:14px;line-height:1.6;color:var(--ink2);">' + UI.esc(p.content).replace(/\n/g, '<br>') + '</div></div>').join('') || '<div class="empty">还没有人回复，来抢个沙发吧</div>') +
    (t.locked && !admin ? '<div class="card" style="text-align:center;padding:18px;color:var(--mut);">该主题已锁定，暂不支持回复</div>' :
      '<div class="card ft-reply" style="padding:20px;border-radius:14px;background:#fff;border:1px solid var(--line);margin-top:20px;">' +
      '<textarea id="ft-reply-txt" rows="3" placeholder="' + (t.locked ? '主题已锁定(管理员仍可回复)' : '写下你的回复与思考…') + '"></textarea>' +
      '<div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap"><button class="btn primary" id="ft-reply-ok">' + UI.icon('send', 14) + ' 提交回复</button>' +
      '<button class="btn" id="ft-ai">' + UI.icon('sparkle', 14) + ' 让 AI 助手回复</button></div></div>');
  if (window.AI && $('#ft-reply-txt')) AI.attach($('#ft-reply-txt'));
  if ($('#ft-ai')) $('#ft-ai').onclick = () => AI.forumReply(tid, () => renderForumThread(tid));

  $('#ft-back').onclick = () => { App.forum.thread = 0; renderForumList(); };
  const reply = async () => {
    const c = $('#ft-reply-txt').value.trim();
    if (!c) return;
    try { await API.post('/api/forum/threads/' + tid + '/posts', { content: c }); renderForumThread(tid); }
    catch (e) { toast(e.message, 'err'); }
  };
  if ($('#ft-reply-ok')) $('#ft-reply-ok').onclick = reply;
  $$('.ft-post .ft-post-ops button', box).forEach(b => b.onclick = async () => {
    const pid = +b.closest('.ft-post').dataset.id;
    if (b.dataset.op === 'del') {
      const ok = await UI.confirm('确定删除这条回复？');
      if (!ok) return;
      try { await API.del('/api/forum/posts/' + pid); renderForumThread(tid); } catch (e) { toast(e.message, 'err'); }
    } else {
      const p = t.posts.find(x => x.id === pid);
      const m = UI.modal({ title: '编辑回复', body: '<textarea id="ep-txt" rows="5">' + UI.esc(p.content) + '</textarea>' });
      m.foot.innerHTML = '<button class="btn" id="ep-c">取消</button><button class="btn primary" id="ep-ok">保存</button>';
      m.foot.querySelector('#ep-c').onclick = m.close;
      m.foot.querySelector('#ep-ok').onclick = async () => {
        try { await API.patch('/api/forum/posts/' + pid, { content: $('#ep-txt').value }); m.close(); renderForumThread(tid); }
        catch (e) { toast(e.message, 'err'); }
      };
    }
  });
  const pinB = $('#ft-pin');
  if (pinB) pinB.onclick = async () => { try { await API.patch('/api/forum/threads/' + tid, { pinned: !t.pinned }); renderForumThread(tid); } catch (e) { toast(e.message, 'err'); } };
  const lockB = $('#ft-lock');
  if (lockB) lockB.onclick = async () => { try { await API.patch('/api/forum/threads/' + tid, { locked: !t.locked }); renderForumThread(tid); } catch (e) { toast(e.message, 'err'); } };
  const delB = $('#ft-del');
  if (delB) delB.onclick = async () => {
    const ok = await UI.confirm('确定删除整个帖子(含所有回复)？删除后无法恢复');
    if (!ok) return;
    try { await API.del('/api/forum/threads/' + tid); App.forum.thread = 0; renderForumList(); } catch (e) { toast(e.message, 'err'); }
  };
  const editB = $('#ft-edit');
  if (editB) editB.onclick = () => {
    const m = UI.modal({ title: '编辑帖子',
      body: '<label>标题<input id="et-title" value="' + UI.esc(t.title) + '"></label>' +
      '<label>内容<textarea id="et-content" rows="7">' + UI.esc(t.content) + '</textarea></label>' });
    m.foot.innerHTML = '<button class="btn" id="et-c">取消</button><button class="btn primary" id="et-ok">保存</button>';
    m.foot.querySelector('#et-c').onclick = m.close;
    m.foot.querySelector('#et-ok').onclick = async () => {
      try {
        await API.patch('/api/forum/threads/' + tid, { title: $('#et-title').value, content: $('#et-content').value });
        m.close(); renderForumThread(tid);
      } catch (e) { toast(e.message, 'err'); }
    };
  };
}

function forumBoardsAdmin(boards) {
  const m = UI.modal({ title: '板块管理',
    body: '<div class="boards-admin">' + boards.map(b =>
      '<div class="board-row" data-id="' + b.id + '" style="display:flex;gap:8px;margin-bottom:8px;"><input value="' + UI.esc(b.name) + '" data-f="name" maxlength="30">' +
      '<input value="' + UI.esc(b.descr) + '" data-f="descr" maxlength="100">' +
      '<button class="btn xs" data-op="save">保存</button><button class="btn xs danger" data-op="del">删除</button></div>').join('') +
    '</div>' +
    '<div class="board-row" style="display:flex;gap:8px;margin-top:14px;"><input id="nb-name" placeholder="新板块名" maxlength="30">' +
    '<input id="nb-descr" placeholder="描述(可选)" maxlength="100"><button class="btn xs primary" id="nb-add">添加板块</button></div>' });
  $$('.board-row [data-op]', m.body).forEach(b => b.onclick = async () => {
    const row = b.closest('.board-row'), id = +row.dataset.id;
    if (b.dataset.op === 'del') {
      const ok = await UI.confirm('确定删除板块(含其中所有帖子)？');
      if (!ok) return;
      try { await API.del('/api/admin/forum/boards/' + id); m.close(); renderForumList(); } catch (e) { toast(e.message, 'err'); }
    } else {
      try {
        await API.patch('/api/admin/forum/boards/' + id, {
          name: row.querySelector('[data-f=name]').value, descr: row.querySelector('[data-f=descr]').value
        });
        toast('已保存', 'ok'); m.close(); renderForumList();
      } catch (e) { toast(e.message, 'err'); }
    }
  });
  m.body.querySelector('#nb-add').onclick = async () => {
    try { await API.post('/api/admin/forum/boards', { name: $('#nb-name').value, descr: $('#nb-descr').value }); m.close(); renderForumList(); }
    catch (e) { toast(e.message, 'err'); }
  };
}

window.openChatDM = function (uid) { if (window.Chat) Chat.openDM(uid); };

document.addEventListener('click', e => {
  if (e.target.closest('#top-ann')) { openAnnPanel(); return; }
  if (e.target.closest('#top-qr')) { openScanModal(); return; }
});
