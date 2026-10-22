/* ================================================================
 * FLA v1.27 — 聊天(微信级)
 * ----------------------------------------------------------------
 * v1.26 的聊天只有"群 + 文本 + 2.5 秒轮询"。这一版按微信的使用习惯重做:
 *
 *   会话列表   私聊 / 群聊 / 官方大厅, 头像 + 未读角标 + 免打扰小红点 + 置顶 +
 *              最后一条摘要([图片]/[语音]/[文件]) + 相对时间 + [有人@我] + 正在输入
 *   消息       气泡(我方右侧) / 日期分隔条 / 系统灰条 / 撤回提示 / 已编辑标记 /
 *              引用回复 / 表情回应 / 已读回执(群 n·m, 私聊 已读·未读) /
 *              图片(点击看大图) / 语音(自制播放条 + 时长) / 文件(大小 + 下载)
 *   输入       多行自动增高 · Enter 发送 / Shift+Enter 换行 · @ 成员自动补全 ·
 *              表情面板 · 图片/文件(点击、拖拽、粘贴三种方式) · 语音录制 ·
 *              引用条 · 上传进度 · "正在输入"上报(2 秒节流)
 *   群资料     群名/公告(群主可改) · 成员网格 · 我在本群的昵称 · 免打扰 · 置顶 ·
 *              邀请 · 移出 · 转让群主 · 退群 · 解散 · 查找聊天记录
 *   其它       全文搜索 · 向上翻页加载历史 · 桌面通知(可选) · 离开页面自动停轮询
 *
 * 全部走 HTTP 轮询(消息 2.5s / 会话 5s / 角标 20s), 页面隐藏时暂停, 回到前台立即补拉。
 * 兼容 Chrome 60+: 不使用 ?. / ?? / replaceAll 等新语法。
 * ================================================================ */
(function () {
  'use strict';

  var REACT_SET = ['❤', '👍', '😂', '🎉', '👀', '🙏'];
  var EMOJI = ['😀', '😁', '😂', '🤣', '😊', '😍', '😘', '😜', '🤔', '😐',
    '😴', '😭', '😅', '😱', '🤯', '😡', '👍', '👎', '👌', '✌️',
    '🙏', '👏', '💪', '🤝', '❤', '💔', '🔥', '✨', '🎉', '🎂',
    '📚', '✏️', '📝', '💡', '⏰', '✅', '❌', '❓', '❗', '⭐',
    '🌞', '🌙', '☁️', '🌧️', '⛄', '🍎', '☕', '🍚', '🏀', '⚽'];
  var WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  var KIND_ICON = { image: '图片', audio: '语音', file: '文件' };
  var IMG_EXT = /\.(png|jpe?g|gif|webp|bmp)$/i;
  var AUD_EXT = /\.(webm|ogg|oga|mp3|m4a|aac|wav|amr|mp4)$/i;

  var C = null;              /* 聊天页状态 */
  var badgeT = 0;            /* 全局未读角标轮询 */
  var audio = null;          /* 共用一个 Audio 播放语音 */

  function st(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function stSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
  function esc(s) { return UI.esc(s); }
  function ic(n, s) { return UI.icon(n, s || 16); }
  function av(u, size) {
    return (window.avatarHTML ? avatarHTML(u, size) : '<span class="avatar avatar-ph"></span>');
  }
  function cert(u) { return window.certHTML ? certHTML(u) : ''; }
  function $(s, el) { return (el || document).querySelector(s); }
  function $$(s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); }
  function tok(url) { return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(API.token); }

  function parseTs(s) {
    var t = new Date(String(s || '').replace(/-/g, '/')).getTime();
    return isNaN(t) ? 0 : t;
  }
  function dayKey(s) { return String(s || '').slice(0, 10); }
  function fmtDay(s) {
    var t = parseTs(s); if (!t) return '';
    var n = new Date(), d0 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
    if (t >= d0) return '今天';
    if (t >= d0 - 86400000) return '昨天';
    if (t >= d0 - 6 * 86400000) return WEEK[new Date(t).getDay()];
    var dt = new Date(t);
    return (dt.getFullYear() === n.getFullYear() ? '' : dt.getFullYear() + '年') +
      (dt.getMonth() + 1) + '月' + dt.getDate() + '日';
  }
  function relTime(s) {
    var t = parseTs(s); if (!t) return '';
    var n = new Date(), d0 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
    if (t >= d0) return String(s).slice(11, 16);
    if (t >= d0 - 86400000) return '昨天';
    if (t >= d0 - 6 * 86400000) return WEEK[new Date(t).getDay()];
    var dt = new Date(t);
    return (dt.getFullYear() === n.getFullYear() ? '' : dt.getFullYear() + '/') +
      (dt.getMonth() + 1) + '/' + dt.getDate();
  }
  function fmtDur(ms) {
    var s = Math.max(1, Math.round((ms || 0) / 1000));
    return s + '″';
  }

  /* ==================================================================
   *  入口
   * ================================================================== */
  function view() {
    destroy();
    document.title = '聊天 - FLA';
    C = {
      rooms: [], filter: st('fla_chat_filter', 'all'), query: '', room: 0, detail: null,
      lastId: 0, firstId: 0, hasMore: false, msgs: {}, members: {}, contacts: [],
      pollT: 0, inboxT: 0, busy: false, loadingOlder: false,
      reply: null, atOpen: false, atList: [], atIdx: 0, atStart: -1,
      rec: null, recT: 0, recDur: 0, uploading: 0, typingSent: 0, typingNames: [],
      notify: st('fla_chat_notify', '') === '1', dead: false, lastRender: 0,
      pendingMentions: [], order: [], lastReadAt: 0, allowCreate: false
    };
    $('#app').innerHTML = shell(wxHTML(), 'chat');
    bindLogout();
    App.setCleanup(destroy);
    bindShell();
    loadContacts();
    loadInbox(false);
    var last = parseInt(st('fla_chat_room', '0'), 10) || 0;
    if (last) openRoom(last);
    else emptyMain('选择左边的会话开始聊天, 或点 ＋ 建群 / 私聊');
    C.inboxT = setInterval(function () { if (!C.dead) loadInbox(true); }, 5000);
    document.addEventListener('visibilitychange', onVis);
  }

  function destroy() {
    if (audio) { try { audio.pause(); } catch (e) { } audio = null; }
    document.removeEventListener('visibilitychange', onVis);
    if (!C) return;
    C.dead = true;
    clearInterval(C.pollT); clearInterval(C.inboxT); clearInterval(C.recT);
    if (C.rec) { try { C.rec.stop(); } catch (e) { } C.rec = null; }
    C = null;
  }

  function onVis() {
    if (!C) return;
    if (document.hidden) { clearInterval(C.pollT); C.pollT = 0; clearInterval(C.inboxT); C.inboxT = 0; }
    else {
      loadInbox(true);
      if (C.room) loadMsgs('new');
      if (!C.pollT) C.pollT = setInterval(tick, 2500);
      if (!C.inboxT) C.inboxT = setInterval(function () { if (!C.dead) loadInbox(true); }, 5000);
    }
  }

  function tick() {
    if (C.dead || document.hidden) return;
    loadMsgs('new');
  }

  /* ==================================================================
   *  骨架
   * ================================================================== */
  function wxHTML() {
    return '<div class="wx">' +
      '<aside class="wx-side">' +
      '<div class="wx-side-head">' +
      '<div class="wx-search"><span>' + ic('search', 15) + '</span>' +
      '<input id="wx-q" placeholder="搜索会话 / 聊天记录" maxlength="30"></div>' +
      '<button class="wx-newbtn" id="wx-new" title="发起群聊 / 私聊">' + ic('plus', 18) + '</button>' +
      '</div>' +
      '<div class="wx-filters" id="wx-filters">' +
      '<button data-f="all" class="on">全部</button><button data-f="unread">未读</button>' +
      '<button data-f="group">群聊</button><button data-f="dm">私聊</button>' +
      '</div>' +
      '<div class="wx-list" id="wx-list"></div>' +
      '<div class="wx-side-foot">' +
      '<button id="wx-notify" title="新消息桌面通知">' + ic('bellOff', 14) + '<span>桌面通知</span></button>' +
      '</div>' +
      '</aside>' +
      '<main class="wx-main" id="wx-main"></main>' +
      '<aside class="wx-profile" id="wx-profile"></aside>' +
      '</div>';
  }

  function emptyMain(txt) {
    var m = $('#wx-main');
    var w = $('.wx'); if (w) w.classList.remove('room-open');
    if (!m) return;
    m.innerHTML = '<div class="wx-empty"><div class="wx-empty-i">' + ic('chat', 42) + '</div>' +
      '<p>' + esc(txt || '') + '</p></div>';
  }

  function bindShell() {
    var q = $('#wx-q');
    var deb = 0;
    q.oninput = function () {
      C.query = q.value.trim();
      clearTimeout(deb);
      deb = setTimeout(function () { renderList(); runSearch(); }, 260);
    };
    $('#wx-filters').onclick = function (e) {
      var b = e.target.closest('[data-f]'); if (!b) return;
      C.filter = b.getAttribute('data-f'); stSet('fla_chat_filter', C.filter);
      $$('#wx-filters button').forEach(function (x) { x.classList.toggle('on', x === b); });
      renderList();
    };
    $$('#wx-filters button').forEach(function (x) {
      x.classList.toggle('on', x.getAttribute('data-f') === C.filter);
    });
    $('#wx-new').onclick = newChatMenu;
    var nb = $('#wx-notify');
    paintNotifyBtn();
    nb.onclick = toggleNotify;
  }

  function paintNotifyBtn() {
    var b = $('#wx-notify'); if (!b || !C) return;
    b.classList.toggle('on', C.notify);
    b.innerHTML = ic(C.notify ? 'horn' : 'bellOff', 14) + '<span>桌面通知' + (C.notify ? '·开' : '') + '</span>';
  }

  function toggleNotify() {
    if (!('Notification' in window)) { toast('这个浏览器不支持桌面通知', 'err'); return; }
    if (C.notify) { C.notify = false; stSet('fla_chat_notify', '0'); paintNotifyBtn(); return; }
    Notification.requestPermission().then(function (p) {
      C.notify = (p === 'granted');
      stSet('fla_chat_notify', C.notify ? '1' : '0');
      paintNotifyBtn();
      toast(C.notify ? '已开启桌面通知(仅在切到别的标签页时提醒)' : '浏览器拒绝了通知权限', C.notify ? '' : 'err');
    });
  }

  /* ==================================================================
   *  会话列表
   * ================================================================== */
  function loadContacts() {
    API.get('/api/chat/contacts').then(function (r) {
      if (!C) return;
      C.contacts = (r && r.items) || [];
    }).catch(function () { });
  }

  function loadInbox(quiet) {
    return API.get('/api/chat/inbox').then(function (r) {
      if (!C) return;
      var before = {};
      C.rooms.forEach(function (x) { before[x.id] = x; });
      C.rooms = (r && r.items) || [];
      C.allowCreate = !!(r && r.allow_create);
      renderList();
      paintBadge();
      // 新消息提示(声音/桌面通知): 只在我没在看这个会话时提醒
      C.rooms.forEach(function (x) {
        var b = before[x.id];
        if (!b || !x.last || (b.last && b.last.id === x.last.id)) return;
        if (x.last.uid === (App.user || {}).id) return;
        if (C.room === x.id && !document.hidden) return;
        if (x.muted) return;
        notifyOf(x);
      });
      if (C.room) {
        var cur = roomById(C.room);
        if (cur) {
          C.typingNames = (cur.typing || []).map(function (uid) { return nickOf(uid); });
          paintHeadTyping();
        }
      }
    }).catch(function (e) { if (!quiet) toast(e.message, 'err'); });
  }

  function notifyOf(x) {
    if (!C.notify || document.hidden === false) return;
    try {
      var n = new Notification(x.name || '新消息', {
        body: previewText(x.last), icon: (x.peer && x.peer.avatar) ? x.peer.avatar : undefined, tag: 'fla-' + x.id
      });
      n.onclick = function () { window.focus(); location.hash = '#/chat'; openRoom(x.id); n.close(); };
      setTimeout(function () { try { n.close(); } catch (e) { } }, 8000);
    } catch (e) { }
  }

  function roomById(rid) {
    for (var i = 0; i < C.rooms.length; i++) if (C.rooms[i].id === rid) return C.rooms[i];
    return null;
  }

  function nickOf(uid) {
    var d = C.detail;
    if (d && d.member_list) {
      for (var i = 0; i < d.member_list.length; i++) if (d.member_list[i].id === uid) return d.member_list[i].nickname_in_room || d.member_list[i].nickname;
    }
    var m = C.members[uid];
    return m ? (m.nickname_in_room || m.nickname) : '有人';
  }

  function previewText(last) {
    if (!last) return '暂无消息';
    if (last.deleted) return '消息已撤回';
    if (last.kind && last.kind !== 'text') return '[' + (KIND_ICON[last.kind] || last.kind) + ']';
    return String(last.content || '').slice(0, 40);
  }

  function roomAvatar(x) {
    if (x.kind === 'dm' && x.peer) return av(x.peer, 44);
    if (x.avatar && /^#[0-9a-fA-F]{6}$/.test(x.avatar)) {
      return '<span class="avatar wx-gav" style="background:' + x.avatar + '">' + esc((x.name || '群').slice(0, 1)) + '</span>';
    }
    if (x.avatar) return '<img class="avatar wx-gav" src="' + esc(tok(x.avatar)) + '">';
    return '<span class="avatar avatar-ph wx-gav">' + esc((x.name || (x.official ? '官方' : '群')).slice(0, 1)) + '</span>';
  }

  function renderList() {
    var box = $('#wx-list'); if (!box || !C) return;
    var q = C.query.toLowerCase();
    var list = C.rooms.filter(function (x) {
      if (C.filter === 'unread' && !x.unread && !x.at_me) return false;
      if (C.filter === 'group' && x.kind === 'dm') return false;
      if (C.filter === 'dm' && x.kind !== 'dm') return false;
      if (!q) return true;
      var hay = (String(x.name || '') + ' ' + ((x.peer || {}).nickname || '')).toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    box.innerHTML = list.map(function (x) {
      var badge = '';
      if (x.unread) {
        badge = x.muted ? '<i class="wx-dot"></i>'
          : '<i class="wx-badge">' + (x.unread > 99 ? '99+' : x.unread) + '</i>';
      } else if (x.muted && x.at_me) badge = '<i class="wx-dot"></i>';
      var typing = x.typing && x.typing.length;
      return '<button class="wx-item' + (C.room === x.id ? ' on' : '') + (x.pinned ? ' pinned' : '') + '" data-id="' + x.id + '">' +
        '<span class="wx-av">' + roomAvatar(x) + badge + '</span>' +
        '<span class="wx-it-body">' +
        '<span class="wx-it-top"><b>' + esc(x.name || '会话') + '</b>' +
        (x.official ? '<i class="wx-tag official">官方</i>' : '') +
        (x.pinned ? '<i class="wx-tag pin" title="已置顶">' + ic('pin', 11) + '</i>' : '') +
        (x.muted ? '<i class="wx-tag mute" title="免打扰">' + ic('bellOff', 11) + '</i>' : '') +
        '<em>' + relTime(x.last ? x.last.created_at : x.created_at) + '</em></span>' +
        '<span class="wx-it-sub">' +
        (typing ? '<i class="wx-typing">正在输入<span class="wx-dots"><i>.</i><i>.</i><i>.</i></span></i>'
          : (x.at_me ? '<i class="wx-at">[有人@我] </i>' : '') +
          '<span>' + esc(previewText(x.last)) + '</span>') +
        '</span></span></button>';
    }).join('') || '<div class="wx-list-empty">' + (C.rooms.length ? '没有匹配的会话' : '还没有会话<br>点右上角 ＋ 发起私聊或建群') + '</div>';
    $$('.wx-item', box).forEach(function (b) {
      b.onclick = function () { openRoom(parseInt(b.getAttribute('data-id'), 10)); };
      b.oncontextmenu = function (e) { e.preventDefault(); roomMenu(e, parseInt(b.getAttribute('data-id'), 10)); };
    });
  }

  /* 会话右键菜单 */
  function roomMenu(e, rid) {
    var x = roomById(rid); if (!x) return;
    var items = [
      { k: 'read', t: '标记为已读', hide: !x.unread },
      { k: 'pin', t: x.pinned ? '取消置顶' : '置顶会话' },
      { k: 'mute', t: x.muted ? '取消免打扰' : '消息免打扰' },
      { k: 'dm', t: '发起私聊', hide: x.kind === 'dm' },
      { k: 'search', t: '查找聊天记录' },
      { k: 'sep' },
      { k: 'leave', t: x.kind === 'dm' ? '删除会话记录' : '退出群聊', danger: true },
      { k: 'del', t: '解散群聊', danger: true, hide: !(x.kind === 'group' && (x.owner_id === (App.user || {}).id || (App.user || {}).role === 'admin')) }
    ];
    popMenu(e.clientX, e.clientY, items, async function (k) {
      try {
        if (k === 'read') { await API.post('/api/chat/rooms/' + rid + '/read', {}); loadInbox(true); }
        else if (k === 'pin') { await API.patch('/api/chat/rooms/' + rid + '/me', { pinned: !x.pinned }); loadInbox(true); }
        else if (k === 'mute') { await API.patch('/api/chat/rooms/' + rid + '/me', { muted: !x.muted }); loadInbox(true); }
        else if (k === 'dm') { newDM(); }
        else if (k === 'search') { openRoom(rid); setTimeout(function () { toggleProfile(true); }, 260); }
        else if (k === 'leave') {
          if (x.kind === 'dm') { toast('私聊记录保存在服务器, 可在群里长按消息撤回自己的内容'); return; }
          if (!await UI.confirm('退出「' + esc(x.name) + '」? 退出后看不到新消息')) return;
          await API.post('/api/chat/rooms/' + rid + '/leave', {});
          if (C.room === rid) { C.room = 0; emptyMain('已退出群聊'); }
          loadInbox(true);
        } else if (k === 'del') {
          if (!await UI.confirm('解散「' + esc(x.name) + '」? 所有消息将被删除, 不可恢复')) return;
          await API.del('/api/chat/rooms/' + rid);
          if (C.room === rid) { C.room = 0; emptyMain('群聊已解散'); }
          loadInbox(true);
        }
      } catch (err) { toast(err.message, 'err'); }
    });
  }

  /* 通用浮层菜单 */
  function popMenu(x, y, items, onPick) {
    closePop();
    var m = document.createElement('div');
    m.className = 'wx-popmenu';
    m.id = 'wx-popmenu';
    m.innerHTML = items.filter(function (i) { return !i.hide; }).map(function (i) {
      if (i.k === 'sep') return '<i class="wx-pm-sep"></i>';
      return '<button data-k="' + i.k + '"' + (i.danger ? ' class="danger"' : '') + '>' + esc(i.t) + '</button>';
    }).join('');
    document.body.appendChild(m);
    var r = m.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + 'px';
    m.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px';
    requestAnimationFrame(function () { m.classList.add('on'); });
    m.onclick = function (e) {
      var b = e.target.closest('[data-k]'); if (!b) return;
      closePop(); onPick(b.getAttribute('data-k'));
    };
    setTimeout(function () {
      document.addEventListener('click', closePop, { once: true });
    }, 0);
  }
  function closePop() {
    var m = $('#wx-popmenu'); if (m) m.remove();
    var r = $('.wx-reactbar'); if (r) r.remove();
  }

  /* ==================================================================
   *  发起私聊 / 建群
   * ================================================================== */
  function newChatMenu() {
    var b = $('#wx-new'), r = b.getBoundingClientRect();
    popMenu(r.left, r.bottom + 6, [
      { k: 'dm', t: '发起私聊' },
      { k: 'group', t: '创建群聊', hide: !C.allowCreate },
      { k: 'reload', t: '刷新会话列表' }
    ], function (k) {
      if (k === 'dm') newDM();
      else if (k === 'group') newGroup();
      else loadInbox(false);
    });
  }

  function contactPicker(opts) {
    opts = opts || {};
    var picked = {};
    var m = UI.modal({
      title: opts.title || '选择联系人',
      body: '<div class="wx-picker">' +
        '<div class="wx-pk-search"><input id="pk-q" placeholder="搜索昵称 / 用户名" maxlength="20"></div>' +
        '<div class="wx-pk-list" id="pk-list"></div>' +
        (opts.multi === false ? '' : '<div class="wx-pk-sel" id="pk-sel"><b>已选 <span id="pk-n">0</span> 人</b></div>') +
        '</div>',
      width: '460px'
    });
    m.foot.innerHTML = '<button class="btn" id="pk-c">取消</button>' +
      (opts.multi === false ? '' : '<button class="btn primary" id="pk-ok">' + esc(opts.okText || '确定') + '</button>');
    m.foot.querySelector('#pk-c').onclick = m.close;
    function list() {
      var q = ($('#pk-q', m.body).value || '').trim().toLowerCase();
      var items = C.contacts.filter(function (u) {
        if (u.self) return false;
        if (opts.exclude && opts.exclude.indexOf(u.id) >= 0) return false;
        if (!q) return true;
        return String(u.nickname).toLowerCase().indexOf(q) >= 0 || String(u.username).toLowerCase().indexOf(q) >= 0;
      });
      $('#pk-list', m.body).innerHTML = items.map(function (u) {
        return '<button class="wx-pk-i' + (picked[u.id] ? ' on' : '') + '" data-id="' + u.id + '">' +
          av(u, 34) + '<span class="wx-pk-n">' + esc(u.nickname) + cert(u) + '</span>' +
          (u.is_teacher ? '<i class="wx-pk-t">教师</i>' : '') +
          (opts.multi === false ? '' : '<i class="wx-pk-ck">' + ic('check', 15) + '</i>') + '</button>';
      }).join('') || '<div class="wx-pk-empty">没有匹配的人</div>';
      $$('.wx-pk-i', m.body).forEach(function (b) {
        b.onclick = function () {
          var id = parseInt(b.getAttribute('data-id'), 10);
          if (opts.multi === false) { m.close(); opts.onPick([id]); return; }
          if (picked[id]) delete picked[id]; else picked[id] = 1;
          b.classList.toggle('on', !!picked[id]);
          $('#pk-n', m.body).textContent = Object.keys(picked).length;
        };
      });
    }
    $('#pk-q', m.body).oninput = list;
    var ok = $('#pk-ok', m.foot);
    if (ok) ok.onclick = function () {
      var ids = Object.keys(picked).map(function (x) { return parseInt(x, 10); });
      if (!ids.length) { toast('至少选一个人', 'err'); return; }
      m.close(); opts.onPick(ids);
    };
    list();
    setTimeout(function () { var i = $('#pk-q', m.body); if (i) i.focus(); }, 60);
  }

  function newDM() {
    contactPicker({
      title: '发起私聊', multi: false,
      onPick: function (ids) { openDM(ids[0]); }
    });
  }

  function newGroup() {
    var m = UI.modal({
      title: '创建群聊',
      body: '<label class="wx-field">群名称<input id="ng-name" maxlength="30" placeholder="例如: 高三(2)班 物理"></label>' +
        '<label class="wx-field">群公告(可选)<textarea id="ng-intro" rows="2" maxlength="200" placeholder="例如: 作业提交与答疑"></textarea></label>' +
        '<p class="wx-hint">建群后可在右侧「群资料」里邀请成员</p>',
      width: '420px'
    });
    m.foot.innerHTML = '<button class="btn" id="ng-c">取消</button><button class="btn primary" id="ng-ok">创建</button>';
    m.foot.querySelector('#ng-c').onclick = m.close;
    m.foot.querySelector('#ng-ok').onclick = async function () {
      try {
        var r = await API.post('/api/chat/rooms', {
          name: $('#ng-name', m.body).value, intro: $('#ng-intro', m.body).value
        });
        m.close(); toast('群聊已创建'); await loadInbox(true); openRoom(r.id);
        setTimeout(function () { toggleProfile(true); inviteMembers(); }, 400);
      } catch (e) { toast(e.message, 'err'); }
    };
    setTimeout(function () { $('#ng-name', m.body).focus(); }, 60);
  }

  function inviteMembers() {
    if (!C || !C.detail) return;
    var have = (C.detail.member_list || []).map(function (u) { return u.id; });
    contactPicker({
      title: '邀请成员加入「' + C.detail.name + '」', okText: '邀请', exclude: have,
      onPick: async function (ids) {
        try {
          var r = await API.post('/api/chat/rooms/' + C.room + '/invite', { uids: ids });
          toast('已邀请 ' + (r.added || []).length + ' 人');
          loadDetail(); loadInbox(true);
        } catch (e) { toast(e.message, 'err'); }
      }
    });
  }

  async function openDM(uid) {
    try {
      var r = await API.post('/api/chat/dm/' + uid, {});
      if (location.hash.indexOf('#/chat') !== 0) { location.hash = '#/chat'; await sleep(260); }
      if (!C) return;
      await loadInbox(true);
      openRoom(r.id);
    } catch (e) { toast(e.message, 'err'); }
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /* ==================================================================
   *  会话主体
   * ================================================================== */
  async function openRoom(rid) {
    if (!C) return;
    if (C.room === rid && $('#wx-msgs')) { loadMsgs('new'); return; }
    C.room = rid; stSet('fla_chat_room', String(rid));
    var wx = $('.wx'); if (wx) wx.classList.add('room-open');   /* 窄屏: 列表滑出 */
    C.lastId = 0; C.firstId = 0; C.msgs = {}; C.reply = null; C.hasMore = false;
    clearInterval(C.pollT); C.pollT = 0;
    $$('.wx-item').forEach(function (b) { b.classList.toggle('on', parseInt(b.getAttribute('data-id'), 10) === rid); });
    var main = $('#wx-main');
    main.innerHTML = '<div class="wx-loading"><div class="spin"></div><p>正在载入会话…</p></div>';
    try {
      await Promise.all([loadDetail(), loadMsgs('full')]);
    } catch (e) {
      main.innerHTML = '<div class="wx-empty"><p>' + esc(e.message) + '</p></div>';
      return;
    }
    if (!C || C.room !== rid) return;
    bindMain();
    markRead(true);
    C.pollT = setInterval(tick, 2500);
    toggleProfile(false);
  }

  function loadDetail() {
    return API.get('/api/chat/rooms/' + C.room).then(function (d) {
      if (!C) return;
      C.detail = d;
      C.members = {};
      (d.member_list || []).forEach(function (u) { C.members[u.id] = u; });
      renderHead();
      renderProfile();
    }).catch(function (e) { toast(e.message, 'err'); });
  }

  function renderHead() {
    var d = C.detail; if (!d) return;
    var h = $('#wx-head');
    if (!h) return;
    var names = (d.typing || []).map(function (uid) { return nickOf(uid); });
    h.innerHTML =
      '<button class="wx-hb wx-back" id="wx-h-back" title="返回会话列表">' + ic('chevL', 18) + '</button>' +
      '<div class="wx-head-l">' +
      '<b>' + esc(d.name || (d.peer ? d.peer.nickname : '会话')) + '</b>' +
      (d.kind === 'dm' ? '<span class="wx-head-sub">' + cert(d.peer || {}) + '</span>'
        : '<span class="wx-head-sub">' + d.members + ' 人' + (d.intro ? ' · ' + esc(d.intro.slice(0, 26)) : '') + '</span>') +
      '</div>' +
      '<div class="wx-head-r">' +
      '<span class="wx-head-typing' + (names.length ? ' on' : '') + '" id="wx-typing">' +
      (names.length ? esc(names.slice(0, 2).join('、')) + (names.length > 2 ? ' 等' : '') + ' 正在输入<span class="wx-dots"><i>.</i><i>.</i><i>.</i></span>' : '') + '</span>' +
      '<button class="wx-hb" id="wx-h-mute" title="' + (d.muted ? '取消免打扰' : '消息免打扰') + '">' + ic(d.muted ? 'bellOff' : 'horn', 17) + '</button>' +
      '<button class="wx-hb" id="wx-h-pin" title="' + (d.pinned ? '取消置顶' : '置顶会话') + '">' + ic('pin', 17) + '</button>' +
      '<button class="wx-hb" id="wx-h-prof" title="会话详情">' + ic('users', 17) + '</button>' +
      '</div>';
    $('#wx-h-back').onclick = function () {
      var w = $('.wx'); if (w) w.classList.remove('room-open');
      toggleProfile(false);
    };
    $('#wx-h-mute').onclick = function () { setMe({ muted: !d.muted }); };
    $('#wx-h-pin').onclick = function () { setMe({ pinned: !d.pinned }); };
    $('#wx-h-prof').onclick = function () { toggleProfile(); };
    $('#wx-h-mute').classList.toggle('on', !!d.muted);
    $('#wx-h-pin').classList.toggle('on', !!d.pinned);
    $('#wx-h-prof').classList.toggle('on', $('#wx-profile').classList.contains('on'));
  }

  function paintHeadTyping() {
    var d = C.detail; if (!d) return;
    var names = C.typingNames || [];
    var el2 = $('#wx-typing'); if (!el2) return;
    el2.classList.toggle('on', !!names.length);
    el2.innerHTML = names.length ? esc(names.slice(0, 2).join('、')) + (names.length > 2 ? ' 等' : '') +
      ' 正在输入<span class="wx-dots"><i>.</i><i>.</i><i>.</i></span>' : '';
  }

  function setMe(patch) {
    return API.patch('/api/chat/rooms/' + C.room + '/me', patch).then(function (r) {
      if (r && r.room) {
        var i = C.rooms.findIndex(function (x) { return x.id === C.room; });
        if (i >= 0) { C.rooms[i] = r.room; renderList(); }
      }
      loadDetail();
    }).catch(function (e) { toast(e.message, 'err'); });
  }

  /* ---------------- 消息拉取 ---------------- */
  function loadMsgs(mode) {
    if (!C || !C.room || C.busy) return Promise.resolve();
    var url = '/api/chat/rooms/' + C.room + '/messages?limit=' + (mode === 'full' ? 60 : 40);
    if (mode === 'new' && C.lastId) url += '&after=' + C.lastId;
    if (mode === 'older' && C.firstId) url += '&before=' + C.firstId;
    if (mode === 'older') C.loadingOlder = true;
    return API.get(url).then(function (r) {
      if (!C) return;
      var items = (r && r.items) || [];
      C.hasMore = !!(r && r.has_more);
      if (r && r.typing) {
        C.typingNames = r.typing;
        paintHeadTyping();
      }
      if (mode === 'full') {
        C.msgs = {}; C.order = [];
        items.forEach(function (m) { C.msgs[m.id] = m; C.order.push(m.id); });
        C.lastId = items.length ? items[items.length - 1].id : 0;
        C.firstId = items.length ? items[0].id : 0;
        paintMsgs(items, false);
      } else if (mode === 'new') {
        if (!items.length) return;
        var box = $('#wx-msgs');
        var stick = box && (box.scrollHeight - box.scrollTop - box.clientHeight < 120);
        var fresh = items.filter(function (m) { return !C.msgs[m.id]; });
        fresh.forEach(function (m) { C.msgs[m.id] = m; C.order.push(m.id); });
        C.lastId = items[items.length - 1].id;
        if (fresh.length) {
          appendMsgs(fresh);
          if (stick || document.hidden === false && fresh.some(function (m) { return m.mine; })) scrollBottom(true);
          if (stick) markRead();
          fresh.forEach(function (m) { if (!m.mine && C.room && document.hidden === false) markRead(); });
        }
      } else if (mode === 'older') {
        C.loadingOlder = false;
        if (!items.length) { C.hasMore = false; paintMoreFlag(); return; }
        items.forEach(function (m) { C.msgs[m.id] = m; });
        C.order = items.map(function (m) { return m.id; }).concat(C.order);
        C.firstId = items[0].id;
        prependMsgs(items);
      }
    }).catch(function () { C.loadingOlder = false; });
  }

  function paintMsgs(items, animate) {
    var main = $('#wx-main'); if (!main || !C) return;
    var d = C.detail || {};
    main.innerHTML =
      '<header class="wx-head" id="wx-head"></header>' +
      '<div class="wx-msgs" id="wx-msgs">' +
      (C.hasMore ? '<div class="wx-more" id="wx-more"><button>查看更早的消息</button></div>' : '') +
      '<div id="wx-msg-list">' + items.map(function (m, i) { return msgHTML(m, items[i - 1]); }).join('') +
      (items.length ? '' : '<div class="wx-no-msg">还没有消息, 打个招呼吧 👋</div>') +
      '</div></div>' +
      '<div class="wx-quote hidden" id="wx-quote"></div>' +
      '<div class="wx-rec hidden" id="wx-rec"></div>' +
      '<div class="wx-compose" id="wx-compose">' +
      '<div class="wx-tools">' +
      '<button data-t="emoji" title="表情">' + ic('smile', 19) + '</button>' +
      '<button data-t="image" title="发送图片(也可直接粘贴/拖入)">' + ic('image', 19) + '</button>' +
      '<button data-t="file" title="发送文件">' + ic('file', 19) + '</button>' +
      '<button data-t="mic" title="语音消息">' + ic('mic', 19) + '</button>' +
      '<span class="wx-tools-sep"></span>' +
      '<button data-t="at" title="@ 群成员">' + ic('at', 19) + '</button>' +
      '<span class="wx-up hidden" id="wx-up"></span>' +
      '</div>' +
      '<div class="wx-inputrow">' +
      '<div class="wx-atpop hidden" id="wx-atpop"></div>' +
      '<div class="wx-emojipop hidden" id="wx-emojipop"></div>' +
      '<textarea id="wx-inp" rows="1" maxlength="2000" placeholder="' +
      (d.kind === 'dm' ? '发消息给 ' + esc(d.peer ? d.peer.nickname : '') : '发消息到 ' + esc(d.name || '群聊')) +
      ' · Enter 发送 / Shift+Enter 换行 / 输入 @ 提醒某人"></textarea>' +
      '<button class="wx-send" id="wx-send">' + ic('send', 17) + '<span>发送</span></button>' +
      '</div></div>' +
      '<input type="file" id="wx-file-img" accept="image/*" class="hidden" multiple>' +
      '<input type="file" id="wx-file-any" class="hidden">';
    renderHead();
    bindMsgEvents();
    scrollBottom(false);
    if (animate) $$('.wx-msg', main).forEach(function (el2, i) {
      el2.style.animationDelay = Math.min(i * 18, 260) + 'ms';
    });
    var inp = $('#wx-inp');
    if (inp && window.innerWidth > 760) setTimeout(function () { inp.focus(); }, 80);
  }

  function appendMsgs(items) {
    var list = $('#wx-msg-list'); if (!list) return;
    var empty = $('.wx-no-msg', list); if (empty) empty.remove();
    /* 日期/时间分隔条需要"上一条"作参照 */
    var prev = C.order.length > items.length ? C.msgs[C.order[C.order.length - items.length - 1]] : null;
    var html = '';
    items.forEach(function (m) { html += msgHTML(m, prev); prev = m; });
    list.insertAdjacentHTML('beforeend', html);
    bindMsgEvents();
    $$('.wx-msg', list).slice(-items.length).forEach(function (n, i) {
      n.classList.add('fresh'); n.style.animationDelay = Math.min(i * 22, 240) + 'ms';
    });
    paintMoreFlag();
  }

  function prependMsgs(items) {
    var list = $('#wx-msg-list'); if (!list) return;
    var box = $('#wx-msgs');
    var oldH = box.scrollHeight, oldT = box.scrollTop;
    var html = '', prev = null;
    items.forEach(function (m) { html += msgHTML(m, prev); prev = m; });
    list.insertAdjacentHTML('afterbegin', html);
    paintMoreFlag();
    bindMsgEvents();
    box.scrollTop = oldT + (box.scrollHeight - oldH);   /* 保持视口不动 */
  }

  function paintMoreFlag() {
    var box = $('#wx-msgs'); if (!box || !C) return;
    var m = $('#wx-more', box);
    if (C.hasMore && !m) {
      box.insertAdjacentHTML('afterbegin', '<div class="wx-more" id="wx-more"><button>查看更早的消息</button></div>');
      m = $('#wx-more', box);
    }
    if (!C.hasMore && m) m.remove();
    if (m) m.querySelector('button').onclick = function () { loadMsgs('older'); };
  }

  function scrollBottom(smooth) {
    var box = $('#wx-msgs'); if (!box) return;
    if (smooth && box.scrollTo) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
    else box.scrollTop = box.scrollHeight;
  }

  /* ---------------- 单条消息 HTML ---------------- */
  function msgHTML(m, prev) {
    if (m.kind === 'system') {
      return '<div class="wx-sys" data-id="' + m.id + '"><span>' + esc(m.content) + '</span></div>';
    }
    var sep = '';
    if (!prev || dayKey(prev.created_at) !== dayKey(m.created_at) ||
        parseTs(m.created_at) - parseTs(prev.created_at) > 300000) {
      sep = '<div class="wx-day"><span>' + fmtDay(m.created_at) + ' ' + String(m.created_at).slice(11, 16) + '</span></div>';
    }
    if (m.deleted) {
      return sep + '<div class="wx-sys" data-id="' + m.id + '"><span>' +
        esc((m.author ? m.author.nickname : '有人') + ' 撤回了一条消息') + '</span></div>';
    }
    var d = C.detail || {};
    var mine = !!m.mine;
    var nm = (C.members[m.uid] && (C.members[m.uid].nickname_in_room || C.members[m.uid].nickname)) ||
      (m.author ? m.author.nickname : '');
    var body = bubbleInner(m);
    var reacts = (m.reacts || []).map(function (r) {
      return '<button class="wx-react' + (r.mine ? ' mine' : '') + '" data-e="' + esc(r.emoji) + '">' +
        r.emoji + (r.uids.length > 1 ? '<i>' + r.uids.length + '</i>' : '') + '</button>';
    }).join('');
    var receipt = '';
    if (mine) {
      if (d.kind === 'dm') receipt = '<i class="wx-read' + (m.read_count ? ' done' : '') + '">' + (m.read_count ? '已读' : '未读') + '</i>';
      else receipt = '<i class="wx-read' + (m.read_count ? ' done' : '') + '">已读 ' + (m.read_count || 0) + '/' + (m.read_total || 0) + '</i>';
    }
    return sep +
      '<div class="wx-msg' + (mine ? ' mine' : '') + '" data-id="' + m.id + '">' +
      '<span class="wx-m-av">' + av(m.author, 36) + '</span>' +
      '<div class="wx-m-main">' +
      '<div class="wx-m-name">' + esc(nm) + cert(m.author) +
      '<em>' + String(m.created_at).slice(11, 16) + '</em>' +
      (m.edited ? '<em class="wx-edited">已编辑' + (m.edited_by_admin ? '·管理员' : '') + '</em>' : '') + '</div>' +
      (m.reply ? '<button class="wx-reply-src" data-rid="' + m.reply.id + '">' +
        '<b>' + esc(m.reply.nickname) + ':</b> ' +
        esc(m.reply.kind !== 'text' ? '[' + (KIND_ICON[m.reply.kind] || '') + ']' : m.reply.content) + '</button>' : '') +
      '<div class="wx-bubble" data-id="' + m.id + '">' + body + '</div>' +
      (reacts ? '<div class="wx-reacts">' + reacts + '</div>' : '') +
      '<div class="wx-m-foot">' + receipt + '</div>' +
      '<div class="wx-m-acts">' +
      '<button data-a="react" title="表情回应">' + ic('smile', 15) + '</button>' +
      '<button data-a="reply" title="引用回复">' + ic('reply', 15) + '</button>' +
      '<button data-a="more" title="更多">' + ic('more', 15) + '</button>' +
      '</div>' +
      '</div></div>';
  }

  function bubbleInner(m) {
    if (m.kind === 'image' && m.att) {
      return '<img class="wx-img" loading="lazy" src="' + esc(tok(m.att.url)) + '" alt="' + esc(m.att.name) + '">';
    }
    if (m.kind === 'audio' && m.att) {
      var w = Math.min(180, 46 + Math.round((m.att.dur_ms || 1000) / 1000) * 3);
      return '<button class="wx-audio" data-url="' + esc(tok(m.att.url)) + '" style="width:' + w + 'px">' +
        '<span class="wx-audio-i">' + ic('play', 15) + '</span>' +
        '<span class="wx-audio-bar"><i></i></span>' +
        '<span class="wx-audio-t">' + fmtDur(m.att.dur_ms) + '</span></button>';
    }
    if (m.kind === 'file' && m.att) {
      return '<a class="wx-file" href="' + esc(tok(m.att.url)) + '" target="_blank" rel="noopener" download>' +
        '<span class="wx-file-i">' + ic('file', 22) + '</span>' +
        '<span class="wx-file-b"><b>' + esc(m.att.name) + '</b><em>' + UI.fmtSize(m.att.size) + '</em></span>' +
        '<span class="wx-file-d">' + ic('download', 16) + '</span></a>';
    }
    return '<div class="wx-text">' + richText(m.content) + '</div>';
  }

  /* 文本 → 安全 HTML: 转义后加 @高亮 / 链接 / 换行 */
  function richText(s) {
    var t = esc(s || '');
    t = t.replace(/(https?:\/\/[^\s<&]+[^\s<&.,;:!?)\]])/g, function (u) {
      return '<a href="' + u + '" target="_blank" rel="noopener nofollow">' + u + '</a>';
    });
    t = t.replace(/@([^\s@<]{1,32})/g, function (all, name) {
      var known = false;
      Object.keys(C ? C.members : {}).forEach(function (k) {
        var mm = C.members[k];
        if (mm && (mm.nickname === name || mm.nickname_in_room === name || mm.username === name)) known = true;
      });
      return known ? '<b class="wx-at-hit">' + all + '</b>' : all;
    });
    return t.replace(/\n/g, '<br>');
  }

  /* ---------------- 消息交互 ---------------- */
  function bindMsgEvents() {
    var box = $('#wx-msgs'); if (!box || box.dataset.bound) return;
    box.dataset.bound = '1';

    box.addEventListener('scroll', function () {
      if (!C) return;
      if (box.scrollTop < 60 && C.hasMore && !C.loadingOlder) loadMsgs('older');
      var stick = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
      if (stick) markRead();
    });

    box.addEventListener('click', function (e) {
      var img = e.target.closest('.wx-img');
      if (img) { lightbox(img.src, img.alt); return; }
      var au = e.target.closest('.wx-audio');
      if (au) { playAudio(au); return; }
      var rc = e.target.closest('.wx-react');
      if (rc) {
        var node = rc.closest('.wx-msg');
        reactTo(parseInt(node.getAttribute('data-id'), 10), rc.getAttribute('data-e'));
        return;
      }
      var act = e.target.closest('[data-a]');
      if (act) {
        var msg = act.closest('.wx-msg');
        var id = parseInt(msg.getAttribute('data-id'), 10);
        var a = act.getAttribute('data-a');
        if (a === 'react') reactBar(act, id);
        else if (a === 'reply') setReply(C.msgs[id]);
        else moreMenu(act, id);
        return;
      }
      var rs = e.target.closest('.wx-reply-src');
      if (rs) { jumpTo(parseInt(rs.getAttribute('data-rid'), 10)); return; }
      var avb = e.target.closest('.wx-m-av');
      if (avb) {
        var mm2 = avb.closest('.wx-msg');
        var who = C.msgs[parseInt(mm2.getAttribute('data-id'), 10)];
        if (who && who.author && who.author.id !== (App.user || {}).id) userCard(who.author);
        return;
      }
    });

    box.addEventListener('contextmenu', function (e) {
      var msg = e.target.closest('.wx-msg');
      if (!msg) return;
      e.preventDefault();
      moreMenu(msg.querySelector('[data-a=more]') || msg, parseInt(msg.getAttribute('data-id'), 10), e);
    });

    /* 拖拽上传 */
    var main = $('#wx-main');
    ['dragenter', 'dragover'].forEach(function (ev) {
      main.addEventListener(ev, function (e) { e.preventDefault(); main.classList.add('drop'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      main.addEventListener(ev, function (e) {
        e.preventDefault();
        if (ev === 'dragleave' && e.relatedTarget && main.contains(e.relatedTarget)) return;
        main.classList.remove('drop');
        if (ev === 'drop' && e.dataTransfer && e.dataTransfer.files) sendFiles(e.dataTransfer.files);
      });
    });
  }

  function reactBar(btn, mid) {
    closePop();
    var b = document.createElement('div');
    b.className = 'wx-reactbar';
    b.innerHTML = REACT_SET.map(function (e) { return '<button data-e="' + e + '">' + e + '</button>'; }).join('');
    document.body.appendChild(b);
    var r = btn.getBoundingClientRect();
    b.style.left = Math.max(8, Math.min(r.left - 40, window.innerWidth - b.offsetWidth - 8)) + 'px';
    b.style.top = Math.max(8, r.top - b.offsetHeight - 8) + 'px';
    requestAnimationFrame(function () { b.classList.add('on'); });
    b.onclick = function (e) {
      var t = e.target.closest('[data-e]'); if (!t) return;
      closePop(); reactTo(mid, t.getAttribute('data-e'));
    };
    setTimeout(function () { document.addEventListener('click', closePop, { once: true }); }, 0);
  }

  function reactTo(mid, emoji) {
    API.post('/api/chat/messages/' + mid + '/react', { emoji: emoji }).then(function () {
      loadMsgs('new');
      refreshOne(mid);
    }).catch(function (e) { toast(e.message, 'err'); });
  }

  function refreshOne(mid) {
    /* 回应/编辑/撤回后, 只需重取这一条所在的最近一页 */
    if (!C) return;
    API.get('/api/chat/rooms/' + C.room + '/messages?after=' + (mid - 1) + '&limit=1').then(function (r) {
      var it = ((r && r.items) || [])[0];
      if (!it) return;
      C.msgs[it.id] = it;
      var node = $('.wx-msg[data-id="' + it.id + '"]');
      if (node) {
        var tmp = document.createElement('div');
        tmp.innerHTML = msgHTML(it, it);
        var fresh = tmp.querySelector('.wx-msg, .wx-sys');
        if (fresh) { fresh.classList.add('swap'); node.parentNode.replaceChild(fresh, node); }
      }
    }).catch(function () { });
  }

  function moreMenu(btn, mid, ev) {
    var m = C.msgs[mid] || {};
    var admin = (App.user || {}).role === 'admin';
    var items = [
      { k: 'copy', t: '复制文字', hide: m.kind !== 'text' || m.deleted },
      { k: 'reply', t: '引用回复', hide: m.deleted },
      { k: 'react', t: '表情回应', hide: m.deleted },
      { k: 'save', t: '保存图片', hide: m.kind !== 'image' },
      { k: 'dl', t: '下载文件', hide: m.kind !== 'file' && m.kind !== 'audio' },
      { k: 'dm', t: '私聊 TA', hide: m.uid === (App.user || {}).id || (C.detail || {}).kind === 'dm' },
      { k: 'sep' },
      { k: 'edit', t: '编辑', hide: m.deleted || !((m.mine && m.kind === 'text') || admin) },
      { k: 'recall', t: m.mine ? '撤回' : '删除(管理员)', hide: m.deleted || !(m.can_recall || admin) },
      { k: 'who', t: '查看谁回应了', hide: !(m.reacts || []).length }
    ];
    var x = ev ? ev.clientX : btn.getBoundingClientRect().left;
    var y = ev ? ev.clientY : btn.getBoundingClientRect().bottom;
    popMenu(x, y, items, async function (k) {
      try {
        if (k === 'copy') {
          await copyText(m.content || '');
          toast('已复制到剪贴板');
        } else if (k === 'reply') setReply(m);
        else if (k === 'react') reactBar(btn, mid);
        else if (k === 'save') lightbox(tok((m.att || {}).url || ''), m.att ? m.att.name : '');
        else if (k === 'dl') window.open(tok((m.att || {}).url || ''), '_blank');
        else if (k === 'dm') openDM(m.uid);
        else if (k === 'edit') editMsg(m);
        else if (k === 'recall') {
          var tip = m.mine ? '撤回这条消息? 所有人都会看到"撤回了一条消息"' : '管理员删除这条消息?';
          if (!await UI.confirm(tip)) return;
          await API.del('/api/chat/messages/' + mid);
          toast('已处理'); loadMsgs('new'); refreshOne(mid); loadInbox(true);
        } else if (k === 'who') {
          var lines = (m.reacts || []).map(function (r) {
            return r.emoji + ' ' + r.uids.map(function (u) { return esc(nickOf(u)); }).join('、');
          }).join('<br>');
          UI.modal({ title: '回应详情', body: '<div class="wx-who">' + lines + '</div>', width: '360px' });
        }
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  function copyText(t) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(t);
    var ta = document.createElement('textarea');
    ta.value = t; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) { }
    ta.remove();
    return Promise.resolve();
  }

  function editMsg(m) {
    var mo = UI.modal({
      title: '编辑消息',
      body: '<textarea id="ed-t" rows="4" maxlength="2000" class="wx-ta">' + esc(m.content) + '</textarea>',
      width: '440px'
    });
    mo.foot.innerHTML = '<button class="btn" id="ed-c">取消</button><button class="btn primary" id="ed-ok">保存</button>';
    mo.foot.querySelector('#ed-c').onclick = mo.close;
    mo.foot.querySelector('#ed-ok').onclick = async function () {
      try {
        await API.patch('/api/chat/messages/' + m.id, { content: $('#ed-t', mo.body).value });
        mo.close(); toast('已更新'); refreshOne(m.id); loadInbox(true);
      } catch (e) { toast(e.message, 'err'); }
    };
    setTimeout(function () { var t = $('#ed-t', mo.body); t.focus(); t.setSelectionRange(t.value.length, t.value.length); }, 60);
  }

  function setReply(m) {
    if (!m || !C) return;
    C.reply = m;
    var q = $('#wx-quote'); if (!q) return;
    q.classList.remove('hidden');
    q.innerHTML = '<span class="wx-q-i">' + ic('reply', 14) + '</span>' +
      '<span class="wx-q-t"><b>' + esc(nickOf(m.uid)) + '</b>: ' +
      esc(m.kind !== 'text' ? '[' + (KIND_ICON[m.kind] || '') + ']' : String(m.content).slice(0, 60)) + '</span>' +
      '<button id="wx-q-x">' + ic('close', 14) + '</button>';
    $('#wx-q-x', q).onclick = function () { C.reply = null; q.classList.add('hidden'); };
    var inp = $('#wx-inp'); if (inp) inp.focus();
  }

  function jumpTo(mid) {
    var n = $('.wx-msg[data-id="' + mid + '"]');
    if (n) {
      n.scrollIntoView({ block: 'center', behavior: 'smooth' });
      n.classList.add('flash');
      setTimeout(function () { n.classList.remove('flash'); }, 1600);
      return;
    }
    /* 不在当前页 → 从这条往前翻, 翻到就高亮 */
    (async function () {
      for (var i = 0; i < 8; i++) {
        await loadMsgs('older');
        n = $('.wx-msg[data-id="' + mid + '"]');
        if (n) { jumpTo(mid); return; }
        if (!C.hasMore) break;
      }
      toast('这条消息太久了, 没能定位到', 'err');
    })();
  }

  function lightbox(src, name) {
    if (!src) return;
    var ov = document.createElement('div');
    ov.className = 'wx-lightbox';
    ov.innerHTML = '<img src="' + esc(src) + '" alt="' + esc(name || '') + '">' +
      '<button class="wx-lb-x">' + ic('close', 20) + '</button>' +
      (name ? '<a class="wx-lb-d" href="' + esc(src) + '" download>下载原图</a>' : '');
    document.body.appendChild(ov);
    requestAnimationFrame(function () { ov.classList.add('on'); });
    var close = function () { ov.classList.remove('on'); setTimeout(function () { ov.remove(); }, 260); };
    ov.onclick = function (e) { if (e.target === ov || e.target.closest('.wx-lb-x')) close(); };
    document.addEventListener('keydown', function onk(e) {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onk); }
    });
  }

  function playAudio(btn) {
    var url = btn.getAttribute('data-url');
    if (audio && audio.dataset.url === url && !audio.paused) {
      audio.pause();
      btn.classList.remove('playing');
      return;
    }
    $$('.wx-audio.playing').forEach(function (b) { b.classList.remove('playing'); });
    if (!audio) audio = new Audio();
    audio.dataset.url = url;
    audio.src = url;
    audio.onplay = function () { btn.classList.add('playing'); };
    audio.onended = function () { btn.classList.remove('playing'); $('.wx-audio-bar i', btn).style.width = '0%'; };
    audio.ontimeupdate = function () {
      var bar = $('.wx-audio-bar i', btn);
      if (bar && audio.duration) bar.style.width = Math.round(audio.currentTime / audio.duration * 100) + '%';
    };
    audio.onerror = function () { toast('语音播放失败', 'err'); btn.classList.remove('playing'); };
    audio.play().catch(function () { toast('浏览器阻止了自动播放, 请再点一次', 'err'); });
  }

  function userCard(u) {
    var m = UI.modal({
      title: '',
      body: '<div class="wx-ucard">' + av(u, 60) +
        '<div class="wx-uc-b"><b>' + esc(u.nickname) + '</b>' + cert(u) +
        '<p>@' + esc(u.username) + '</p>' +
        (u.signature ? '<p class="wx-uc-sig">' + esc(u.signature) + '</p>' : '') + '</div></div>',
      width: '380px', footer: false
    });
    var foot = document.createElement('div');
    foot.className = 'wx-uc-acts';
    foot.innerHTML = '<button class="btn primary" id="uc-dm">' + ic('chat', 15) + ' 发消息</button>';
    m.body.appendChild(foot);
    $('#uc-dm', foot).onclick = function () { m.close(); openDM(u.id); };
  }

  /* ==================================================================
   *  输入区: 文本 / 表情 / @ / 附件 / 语音
   * ================================================================== */
  function bindMain() {
    var inp = $('#wx-inp'); if (!inp) return;
    inp.addEventListener('input', function () {
      autoGrow(inp);
      sendTyping();
      checkAt(inp);
    });
    inp.addEventListener('keydown', function (e) {
      if (C.atOpen && !$('#wx-atpop').classList.contains('hidden')) {
        if (e.key === 'ArrowDown') { e.preventDefault(); moveAt(1); return; }
        if (e.key === 'ArrowUp') { e.preventDefault(); moveAt(-1); return; }
        if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickAt(C.atIdx); return; }
        if (e.key === 'Escape') { hideAt(); return; }
      }
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendText(); }
      if (e.key === 'Escape' && C.reply) { C.reply = null; $('#wx-quote').classList.add('hidden'); }
    });
    inp.addEventListener('paste', function (e) {
      var items = (e.clipboardData || {}).items || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].type && items[i].type.indexOf('image') === 0) {
          var f = items[i].getAsFile();
          if (f) { e.preventDefault(); sendFiles([f]); return; }
        }
      }
    });
    $('#wx-send').onclick = sendText;
    $$('.wx-tools [data-t]').forEach(function (b) {
      b.onclick = function () {
        var t = b.getAttribute('data-t');
        if (t === 'emoji') emojiPanel(b);
        else if (t === 'image') $('#wx-file-img').click();
        else if (t === 'file') $('#wx-file-any').click();
        else if (t === 'mic') toggleRec();
        else if (t === 'at') insertAt();
      };
    });
    $('#wx-file-img').onchange = function (e) { sendFiles(e.target.files); e.target.value = ''; };
    $('#wx-file-any').onchange = function (e) { sendFiles(e.target.files); e.target.value = ''; };
    autoGrow(inp);
  }

  function autoGrow(t) {
    t.style.height = 'auto';
    t.style.height = Math.min(160, Math.max(40, t.scrollHeight)) + 'px';
  }

  function sendTyping() {
    if (!C || !C.room) return;
    var now = Date.now();
    if (now - C.typingSent < 2000) return;
    C.typingSent = now;
    API.post('/api/chat/rooms/' + C.room + '/typing', {}).catch(function () { });
  }

  function markRead(force) {
    if (!C || !C.room || document.hidden) return;
    var now = Date.now();
    if (!force && now - C.lastReadAt < 3000) return;
    C.lastReadAt = now;
    API.post('/api/chat/rooms/' + C.room + '/read', { last_id: C.lastId || 0 }).then(function () {
      var x = roomById(C.room);
      if (x && x.unread) { x.unread = 0; x.at_me = 0; renderList(); }
      paintBadge();
    }).catch(function () { });
  }

  /* ---------------- @ 自动补全 ---------------- */
  function checkAt(inp) {
    var v = inp.value, p = inp.selectionStart || v.length;
    var i = v.lastIndexOf('@', p - 1);
    if (i < 0 || (i > 0 && !/\s/.test(v[i - 1]))) { hideAt(); return; }
    var kw = v.slice(i + 1, p);
    if (/[\s@]/.test(kw) || kw.length > 20) { hideAt(); return; }
    var list = memberList().filter(function (u) {
      return !kw || String(u.nickname).toLowerCase().indexOf(kw.toLowerCase()) >= 0 ||
        String(u.username || '').toLowerCase().indexOf(kw.toLowerCase()) >= 0;
    }).slice(0, 8);
    if (!list.length) { hideAt(); return; }
    C.atOpen = true; C.atList = list; C.atStart = i; C.atIdx = 0;
    var pop = $('#wx-atpop');
    pop.classList.remove('hidden');
    pop.innerHTML = list.map(function (u, k) {
      return '<button data-k="' + k + '"' + (k === 0 ? ' class="on"' : '') + '>' + av(u, 26) +
        '<span>' + esc(u.nickname_in_room || u.nickname) + '</span>' +
        (u.is_owner ? '<i class="wx-tag official">群主</i>' : '') + '</button>';
    }).join('');
    $$('button', pop).forEach(function (b) {
      b.onmouseenter = function () { C.atIdx = parseInt(b.getAttribute('data-k'), 10); paintAt(); };
      b.onclick = function () { pickAt(parseInt(b.getAttribute('data-k'), 10)); };
    });
  }
  function paintAt() {
    $$('#wx-atpop button').forEach(function (b, k) { b.classList.toggle('on', k === C.atIdx); });
  }
  function moveAt(d) {
    C.atIdx = (C.atIdx + d + C.atList.length) % C.atList.length;
    paintAt();
  }
  function hideAt() { if (C) C.atOpen = false; var p = $('#wx-atpop'); if (p) p.classList.add('hidden'); }
  function pickAt(k) {
    var u = C.atList[k]; if (!u) return;
    var inp = $('#wx-inp'), v = inp.value, p = inp.selectionStart || v.length;
    var nm = u.nickname_in_room || u.nickname;
    inp.value = v.slice(0, C.atStart) + '@' + nm + ' ' + v.slice(p);
    var np = C.atStart + nm.length + 2;
    inp.setSelectionRange(np, np);
    hideAt(); autoGrow(inp); inp.focus();
    if (C.pendingMentions.indexOf(u.id) < 0) C.pendingMentions.push(u.id);
  }
  function insertAt() {
    var inp = $('#wx-inp');
    var p = inp.selectionStart || inp.value.length;
    inp.value = inp.value.slice(0, p) + '@' + inp.value.slice(p);
    inp.setSelectionRange(p + 1, p + 1);
    inp.focus(); checkAt(inp);
  }
  function memberList() {
    return (C.detail && C.detail.member_list) ? C.detail.member_list.slice() : [];
  }

  /* ---------------- 表情面板 ---------------- */
  function emojiPanel(btn) {
    var pop = $('#wx-emojipop');
    if (!pop.classList.contains('hidden')) { pop.classList.add('hidden'); return; }
    pop.innerHTML = EMOJI.map(function (e) { return '<button data-e="' + e + '">' + e + '</button>'; }).join('');
    pop.classList.remove('hidden');
    $$('button', pop).forEach(function (b) {
      b.onclick = function () {
        var inp = $('#wx-inp'), p = inp.selectionStart || inp.value.length;
        inp.value = inp.value.slice(0, p) + b.getAttribute('data-e') + inp.value.slice(p);
        var np = p + b.getAttribute('data-e').length;
        inp.setSelectionRange(np, np); inp.focus(); autoGrow(inp);
      };
    });
    var r = btn.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(r.left - 8, window.innerWidth - pop.offsetWidth - 8)) + 'px';
  }

  /* ---------------- 文本发送 ---------------- */
  async function sendText() {
    var inp = $('#wx-inp');
    var v = (inp.value || '').trim();
    if (!v || !C || !C.room) return;
    var mentions = collectMentions(v);
    inp.value = ''; autoGrow(inp);
    var body = { content: v, kind: 'text', mentions: mentions };
    if (C.reply) { body.reply_to = C.reply.id; C.reply = null; $('#wx-quote').classList.add('hidden'); }
    hideAt();
    try {
      var r = await API.post('/api/chat/rooms/' + C.room + '/messages', body);
      C.pendingMentions = [];
      if (r && r.item) {
        C.msgs[r.item.id] = r.item;
        appendMsgs([r.item]);
        C.lastId = Math.max(C.lastId, r.item.id);
        scrollBottom(true);
        markRead();
      }
      loadInbox(true);
    } catch (e) {
      inp.value = v; autoGrow(inp);
      toast(e.message, 'err');
    }
  }

  function collectMentions(v) {
    var out = [];
    memberList().forEach(function (u) {
      var nm = u.nickname_in_room || u.nickname;
      if (nm && v.indexOf('@' + nm) >= 0 && u.id !== (App.user || {}).id) out.push(u.id);
    });
    (C.pendingMentions || []).forEach(function (id) { if (out.indexOf(id) < 0) out.push(id); });
    return out;
  }

  /* ---------------- 附件 ---------------- */
  function sendFiles(files) {
    Array.prototype.slice.call(files || []).forEach(function (f) {
      var kind = IMG_EXT.test(f.name) || (f.type || '').indexOf('image/') === 0 ? 'image'
        : AUD_EXT.test(f.name) || (f.type || '').indexOf('audio/') === 0 ? 'audio' : 'file';
      uploadOne(f, kind, 0);
    });
  }

  function paintUp(n) {
    var el2 = $('#wx-up'); if (!el2) return;
    C.uploading = n;
    if (!n) { el2.classList.add('hidden'); el2.innerHTML = ''; return; }
    el2.classList.remove('hidden');
    el2.innerHTML = '<i class="wx-up-bar"><b style="width:' + n + '%"></b></i><span>' + n + '%</span>';
  }

  async function uploadOne(f, kind, dur) {
    if (!C || !C.room) return;
    var fd = new FormData();
    fd.append('file', f, f.name || 'file');
    fd.append('kind', kind);
    if (dur) fd.append('dur_ms', String(dur));
    paintUp(2);
    try {
      var a = await API.upload('/api/chat/attachments', fd, function (p) { paintUp(Math.max(2, p)); });
      paintUp(98);
      var r = await API.post('/api/chat/rooms/' + C.room + '/messages',
        { kind: kind, att_id: a.id, content: '', reply_to: C.reply ? C.reply.id : 0 });
      if (C.reply) { C.reply = null; $('#wx-quote').classList.add('hidden'); }
      if (r && r.item) { C.msgs[r.item.id] = r.item; appendMsgs([r.item]); C.lastId = r.item.id; scrollBottom(true); }
      paintUp(0);
      loadInbox(true);
      markRead();
    } catch (e) {
      paintUp(0);
      toast(e.message, 'err');
    }
  }

  /* ---------------- 语音 ---------------- */
  function toggleRec() {
    if (C.rec) { stopRec(true); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      toast('这个浏览器不支持录音(需 Chrome/Edge + HTTPS)', 'err'); return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', '']
        .filter(function (x) { return !x || MediaRecorder.isTypeSupported(x); })[0] || '';
      var rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      var chunks = [];
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        var box = $('#wx-rec'); if (box) box.classList.add('hidden');
        clearInterval(C.recT);
        if (!C.recSend) { C.rec = null; return; }
        var ext = (mime.indexOf('mp4') >= 0 ? 'm4a' : mime.indexOf('ogg') >= 0 ? 'ogg' : 'webm');
        var blob = new Blob(chunks, { type: mime || 'audio/webm' });
        var f = new File([blob], '语音消息-' + Date.now() + '.' + ext, { type: blob.type });
        var dur = C.recDur;
        C.rec = null;
        if (dur < 800) { toast('说话时间太短'); return; }
        uploadOne(f, 'audio', dur);
      };
      C.rec = rec; C.recDur = 0; C.recSend = false;
      rec.start();
      var box = $('#wx-rec');
      box.classList.remove('hidden');
      box.innerHTML = '<span class="wx-rec-i"><i></i><i></i><i></i><i></i><i></i></span>' +
        '<b id="wx-rec-t">0:00</b><span class="wx-rec-tip">正在录音(最长 60 秒)</span>' +
        '<button class="btn sm" id="wx-rec-c">取消</button>' +
        '<button class="btn sm primary" id="wx-rec-s">停止并发送</button>';
      $('#wx-rec-c', box).onclick = function () { C.recSend = false; stopRec(false); };
      $('#wx-rec-s', box).onclick = function () { C.recSend = true; stopRec(true); };
      C.recT = setInterval(function () {
        C.recDur += 200;
        var t = $('#wx-rec-t');
        if (t) t.textContent = Math.floor(C.recDur / 60000) + ':' +
          String(Math.floor(C.recDur / 1000) % 60).padStart(2, '0');
        if (C.recDur >= 60000) { C.recSend = true; stopRec(true); }
      }, 200);
    }).catch(function () { toast('拿不到麦克风权限, 请在地址栏允许录音', 'err'); });
  }

  function stopRec() {
    if (C && C.rec) { try { C.rec.stop(); } catch (e) { } }
  }

  /* ==================================================================
   *  会话详情(右侧抽屉)
   * ================================================================== */
  function toggleProfile(force) {
    var p = $('#wx-profile'); if (!p) return;
    var on = force === undefined ? !p.classList.contains('on') : !!force;
    p.classList.toggle('on', on);
    $('.wx').classList.toggle('prof', on);
    var hb = $('#wx-h-prof'); if (hb) hb.classList.toggle('on', on);
    if (on) renderProfile();
  }

  function renderProfile() {
    var p = $('#wx-profile'), d = C.detail;
    if (!p || !d) return;
    if (!p.classList.contains('on')) { p.innerHTML = ''; return; }
    var me = App.user || {};
    var owner = d.i_am_owner;
    var members = (d.member_list || []).map(function (u) {
      return '<button class="wx-mm" data-id="' + u.id + '" title="' + esc(u.nickname) + '">' +
        av(u, 40) + (u.is_owner ? '<i class="wx-mm-crown">' + ic('crown', 12) + '</i>' : '') +
        '<em>' + esc(u.nickname_in_room || u.nickname) + '</em></button>';
    }).join('');
    p.innerHTML =
      '<div class="wx-pf-head"><b>' + (d.kind === 'dm' ? '私聊' : '群资料') + '</b>' +
      '<button id="wx-pf-x">' + ic('close', 17) + '</button></div>' +
      '<div class="wx-pf-body">' +
      '<div class="wx-pf-hero">' + roomAvatar(d.kind === 'dm' ? { kind: 'dm', peer: d.peer } : d) +
      '<div><b>' + esc(d.name || (d.peer ? d.peer.nickname : '')) + '</b>' +
      '<span>' + (d.kind === 'dm' ? cert(d.peer || {}) : d.members + ' 人 · 群主 ' + esc(nickOf(d.owner_id))) + '</span></div></div>' +

      (d.kind === 'group' ? '<div class="wx-pf-row"><label>群名称</label>' +
        '<div class="wx-pf-inline"><input id="pf-name" maxlength="30" value="' + esc(d.name) + '"' + (owner ? '' : ' disabled') + '>' +
        (owner ? '<button class="btn xs" id="pf-name-ok">保存</button>' : '') + '</div></div>' +
        '<div class="wx-pf-row"><label>群公告</label>' +
        '<textarea id="pf-intro" rows="2" maxlength="200" placeholder="写点群规或通知"' + (owner ? '' : ' disabled') + '>' + esc(d.intro || '') + '</textarea>' +
        (owner ? '<button class="btn xs" id="pf-intro-ok">保存公告</button>' : '') + '</div>' : '') +

      '<div class="wx-pf-row"><label>我在本群的昵称</label>' +
      '<div class="wx-pf-inline"><input id="pf-nick" maxlength="32" value="' + esc(d.my_nickname || '') + '" placeholder="' + esc(me.nickname || '') + '">' +
      '<button class="btn xs" id="pf-nick-ok">保存</button></div></div>' +

      '<div class="wx-pf-switch"><span>消息免打扰</span><i class="wx-sw' + (d.muted ? ' on' : '') + '" id="pf-mute"></i></div>' +
      '<div class="wx-pf-switch"><span>置顶会话</span><i class="wx-sw' + (d.pinned ? ' on' : '') + '" id="pf-pin"></i></div>' +

      '<div class="wx-pf-row"><label>查找聊天记录</label>' +
      '<div class="wx-pf-inline"><input id="pf-q" placeholder="关键词"><button class="btn xs" id="pf-q-ok">' + ic('search', 13) + '</button></div>' +
      '<div id="pf-hits"></div></div>' +

      (d.kind === 'group' ? '<div class="wx-pf-row"><label>群成员 (' + d.members + ')</label>' +
        '<div class="wx-pf-grid">' + members +
        (owner || me.role === 'admin' ? '<button class="wx-mm add" id="pf-invite">' + ic('plus', 18) + '<em>邀请</em></button>' : '') +
        '</div></div>' : '') +

      '<div class="wx-pf-acts">' +
      (d.kind === 'group' ? (owner ? '<button class="btn xs danger" id="pf-leave">解散群聊</button>'
        : '<button class="btn xs danger" id="pf-leave">退出群聊</button>') : '') +
      '</div>' +
      '</div>';

    $('#wx-pf-x').onclick = function () { toggleProfile(false); };
    var nm = $('#pf-name'), ni = $('#pf-intro'), nk = $('#pf-nick');
    if (nm) $('#pf-name-ok').onclick = async function () {
      try { await API.patch('/api/chat/rooms/' + C.room, { name: nm.value }); toast('已更新'); loadDetail(); loadInbox(true); }
      catch (e) { toast(e.message, 'err'); }
    };
    if (ni) $('#pf-intro-ok').onclick = async function () {
      try { await API.patch('/api/chat/rooms/' + C.room, { intro: ni.value }); toast('群公告已更新'); loadDetail(); loadInbox(true); }
      catch (e) { toast(e.message, 'err'); }
    };
    $('#pf-nick-ok').onclick = async function () {
      try { await setMe({ nickname: nk.value }); toast('群昵称已更新'); } catch (e) { toast(e.message, 'err'); }
    };
    $('#pf-mute').onclick = function () { setMe({ muted: !d.muted }); };
    $('#pf-pin').onclick = function () { setMe({ pinned: !d.pinned }); };
    $('#pf-q-ok').onclick = function () { searchInRoom($('#pf-q').value); };
    $('#pf-q').onkeydown = function (e) { if (e.key === 'Enter') searchInRoom(e.target.value); };
    var inv = $('#pf-invite'); if (inv) inv.onclick = inviteMembers;
    $$('.wx-mm[data-id]', p).forEach(function (b) {
      b.onclick = function () {
        var id = parseInt(b.getAttribute('data-id'), 10);
        var u = C.members[id];
        if (!u) return;
        memberMenu(b, u);
      };
    });
    var lv = $('#pf-leave');
    if (lv) lv.onclick = async function () {
      try {
        if (d.i_am_owner && me.role !== 'admin') {
          if (!await UI.confirm('解散「' + esc(d.name) + '」? 所有消息将被删除')) return;
          await API.del('/api/chat/rooms/' + C.room);
          toast('群聊已解散');
        } else {
          if (!await UI.confirm('退出「' + esc(d.name) + '」?')) return;
          await API.post('/api/chat/rooms/' + C.room + '/leave', {});
          toast('已退出群聊');
        }
        C.room = 0; stSet('fla_chat_room', '0');
        toggleProfile(false); emptyMain('已离开会话'); loadInbox(true);
      } catch (e) { toast(e.message, 'err'); }
    };
  }

  function memberMenu(btn, u) {
    var d = C.detail, me = App.user || {};
    var owner = d.i_am_owner || me.role === 'admin';
    var r = btn.getBoundingClientRect();
    popMenu(r.left, r.bottom + 4, [
      { k: 'card', t: '查看资料' },
      { k: 'dm', t: '私聊 TA', hide: u.id === me.id },
      { k: 'at', t: '在群里 @ TA', hide: u.id === me.id || d.kind !== 'group' },
      { k: 'nick', t: '看 TA 的群昵称', hide: !(u.nickname_in_room && u.nickname_in_room !== u.nickname) },
      { k: 'sep', hide: !owner || u.id === me.id },
      { k: 'kick', t: '移出群聊', danger: true, hide: !owner || u.id === me.id || u.is_owner },
      { k: 'transfer', t: '转让群主', danger: true, hide: !(d.i_am_owner && !u.is_owner) }
    ], async function (k) {
      try {
        if (k === 'card') userCard(u);
        else if (k === 'dm') { toggleProfile(false); openDM(u.id); }
        else if (k === 'at') {
          toggleProfile(false);
          var inp = $('#wx-inp');
          inp.value += (inp.value && !/\s$/.test(inp.value) ? ' ' : '') + '@' + (u.nickname_in_room || u.nickname) + ' ';
          inp.focus(); autoGrow(inp);
        } else if (k === 'nick') toast('群昵称: ' + u.nickname_in_room);
        else if (k === 'kick') {
          if (!await UI.confirm('把 ' + esc(u.nickname) + ' 移出群聊?')) return;
          await API.post('/api/chat/rooms/' + C.room + '/kick', { uid: u.id });
          toast('已移出'); loadDetail(); loadInbox(true);
        } else if (k === 'transfer') {
          if (!await UI.confirm('把群主转让给 ' + esc(u.nickname) + '?')) return;
          await API.post('/api/chat/rooms/' + C.room + '/transfer', { uid: u.id });
          toast('已转让'); loadDetail(); loadInbox(true);
        }
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  function searchInRoom(q) {
    q = (q || '').trim();
    var box = $('#pf-hits');
    if (!q) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="wx-pk-empty">搜索中…</div>';
    API.get('/api/chat/search?q=' + encodeURIComponent(q) + '&rid=' + C.room + '&limit=30').then(function (r) {
      var items = (r && r.items) || [];
      box.innerHTML = items.map(function (m) {
        return '<button class="wx-hit" data-id="' + m.id + '"><b>' + esc((m.author || {}).nickname) + '</b>' +
          '<em>' + esc(String(m.created_at).slice(0, 16)) + '</em>' +
          '<span>' + hlHit(m.content, q) + '</span></button>';
      }).join('') || '<div class="wx-pk-empty">没有找到相关消息</div>';
      $$('.wx-hit', box).forEach(function (b) {
        b.onclick = function () { jumpTo(parseInt(b.getAttribute('data-id'), 10)); };
      });
    }).catch(function (e) { box.innerHTML = '<div class="wx-pk-empty">' + esc(e.message) + '</div>'; });
  }

  function hlHit(text, q) {
    var t = esc(String(text || '').slice(0, 80));
    if (!q) return t;
    var i = t.toLowerCase().indexOf(esc(q).toLowerCase());
    if (i < 0) return t;
    return t.slice(0, i) + '<mark>' + t.slice(i, i + esc(q).length) + '</mark>' + t.slice(i + esc(q).length);
  }

  /* 全局搜索(侧栏输入框) */
  function runSearch() {
    var q = C.query.trim();
    var old = $('#wx-search-hits');
    if (old) old.remove();
    if (q.length < 1) return;
    API.get('/api/chat/search?q=' + encodeURIComponent(q) + '&limit=20').then(function (r) {
      if (!C || C.query.trim() !== q) return;
      var items = (r && r.items) || [];
      if (!items.length) return;
      var d = document.createElement('div');
      d.className = 'wx-search-hits';
      d.id = 'wx-search-hits';
      d.innerHTML = '<b>聊天记录 · ' + items.length + ' 条</b>' + items.map(function (m) {
        return '<button class="wx-hit" data-id="' + m.id + '" data-room="' + m.room_id + '">' +
          '<b>' + esc(m.room_name || '') + '</b><em>' + esc(String(m.created_at).slice(0, 16)) + '</em>' +
          '<span>' + hlHit(m.content, q) + '</span></button>';
      }).join('');
      $('#wx-list').insertAdjacentElement('afterend', d);
      $$('.wx-hit', d).forEach(function (b) {
        b.onclick = async function () {
          var rid = parseInt(b.getAttribute('data-room'), 10);
          var mid = parseInt(b.getAttribute('data-id'), 10);
          if (rid !== C.room) { await openRoom(rid); await sleep(200); }
          jumpTo(mid);
        };
      });
    }).catch(function () { });
  }

  /* ==================================================================
   *  全局未读角标(导航栏)
   * ================================================================== */
  function refreshBadge() {
    if (!App.user) return Promise.resolve();
    return API.get('/api/chat/unread').then(function (r) {
      App.chatUnread = (r && r.total) || 0;
      App.chatDot = (r && r.dot) || 0;
      paintBadge();
    }).catch(function () { });
  }

  function paintBadge() {
    var total = 0, dot = 0;
    if (C && C.rooms.length) {
      C.rooms.forEach(function (x) {
        if (!x.unread) return;
        if (x.muted) dot++; else total += x.unread;
      });
    } else {
      total = App.chatUnread || 0; dot = App.chatDot || 0;
    }
    var b = $('#chat-badge');
    if (!b) return;
    b.classList.toggle('hidden', !total && !dot);
    b.classList.toggle('dot', !total && !!dot);
    b.textContent = total ? (total > 99 ? '99+' : total) : '';
  }

  function startBadge() {
    if (badgeT) return;
    refreshBadge();
    badgeT = setInterval(function () {
      if (App.user && !C) refreshBadge();
    }, 20000);
  }

  /* ================================================================== */
  window.Chat = {
    view: view, destroy: destroy, refreshBadge: refreshBadge,
    openDM: openDM, startBadge: startBadge, paintBadge: paintBadge
  };
})();
