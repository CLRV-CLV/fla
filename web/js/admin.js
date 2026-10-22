/* FLA - 管理后台 */
'use strict';

/* 函数名与 ai.js/app.js 共享全局作用域, 必须唯一 (见 ai.js 顶部说明) */
window.Admin = { view: adminView };
let sec = 'overview';
let userSearchTimer = null;

function adminView() {
  document.title = '管理后台 - FLA';
  $('#app').innerHTML =
    '<div class="adm">' +
    '<aside class="adm-side">' +
    '<div class="adm-brand">' + UI.icon('board', 24) + ' FLA</div>' +
    '<nav class="adm-nav">' +
    '<button data-s="overview">' + UI.icon('chart', 18) + ' 数据概览</button>' +
    '<button data-s="users">' + UI.icon('users', 18) + ' 用户管理</button>' +
    '<button data-s="groups">' + UI.icon('users', 18) + ' 用户组</button>' +
    '<button data-s="invites">' + UI.icon('key', 18) + ' 邀请码</button>' +
    '<button data-s="fake">' + UI.icon('plus', 18) + ' 假用户/首页数据</button>' +
    '<button data-s="ai">' + UI.icon('sparkle', 18) + ' AI 渠道与模型</button>' +
    '<button data-s="anns">' + UI.icon('horn', 18) + ' 公告管理</button>' +
    '<button data-s="forum">' + UI.icon('forum', 18) + ' 论坛管理</button>' +
    '<button data-s="chat">' + UI.icon('chat', 18) + ' 聊天管理</button>' +
    '<button data-s="settings">' + UI.icon('gear', 18) + ' 系统设置</button>' +
    '</nav>' +
    '<a class="adm-back" href="#/library">' + UI.icon('back', 15) + ' 返回前台</a>' +
    '</aside><main class="adm-main" id="adm-main"><div class="empty">加载中…</div></main></div>';
  bindLogout();
  loadGroupsCache();
  $$('.adm-nav button').forEach(b => b.onclick = () => { sec = b.dataset.s; markNav(); load(); });
  markNav();
  load();
}

function markNav() {
  $$('.adm-nav button').forEach(b => b.classList.toggle('on', b.dataset.s === sec));
}

function load() {
  if (sec === 'overview') secOverview();
  if (sec === 'users') secUsers();
  if (sec === 'invites') secInvites();
  if (sec === 'groups') secGroups();
  if (sec === 'fake') secFake();
  if (sec === 'ai') secAI();
  if (sec === 'settings') secSettings();
  if (sec === 'anns') secAnns();
  if (sec === 'forum') secForum();
  if (sec === 'chat') secChat();
}

/* ---------- 概览 ---------- */
async function secOverview() {
  const main = $('#adm-main');
  let s;
  try { s = await API.get('/api/admin/stats'); } catch (e) { main.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  const card = (t, v, sub) => '<div class="stat-card"><span>' + t + '</span><b>' + v + '</b><i>' + (sub || '') + '</i></div>';
  main.innerHTML =
    '<h3>数据概览</h3>' +
    '<div class="stat-grid">' +
    card('用户总数', s.users, '认证教师 ' + s.teachers) +
    card('课件总数', s.files, '转换中 ' + s.converting + ' · 失败 ' + s.failed) +
    card('总存储用量', UI.fmtSize(s.storage), '') +
    card('邀请码', s.invites, '已使用 ' + s.invites_used + ' 次') +
    card('论坛', s.threads, '回复 ' + s.forum_posts) +
    card('聊天消息', s.chat_messages, '公告 ' + s.announcements) +
    '</div>' +
    '<div class="card" style="margin-top:18px"><h4>最近注册用户</h4>' +
    '<table class="tbl"><thead><tr><th>ID</th><th>用户</th><th>昵称</th><th>认证</th><th>注册时间</th></tr></thead><tbody>' +
    (s.recent_users.map(u => '<tr><td>' + u.id + '</td><td>' + UI.esc(u.username) + '</td><td>' + UI.esc(u.nickname) + ' ' + certHTML(u) + '</td><td>' + (u.is_teacher ? '✔' : '—') + '</td><td>' + UI.fmtDate(u.created_at) + '</td></tr>').join('') || '<tr><td colspan="5" class="muted">暂无</td></tr>') +
    '</tbody></table></div>';
}

/* ---------- 用户管理 ---------- */
async function secUsers() {
  const main = $('#adm-main');
  main.innerHTML = '<div class="sec-head"><h3>用户管理</h3>' +
    '<input id="uq" class="inp" placeholder="搜索用户名 / 昵称…"></div><div id="ulist"></div>';
  $('#uq').oninput = () => {
    clearTimeout(userSearchTimer);
    userSearchTimer = setTimeout(loadUsers, 300);
  };
  await loadUsers();
}

async function loadUsers() {
  const box = $('#ulist'); if (!box) return;
  const kw = ($('#uq') || {}).value || '';
  let r;
  try { r = await API.get('/api/admin/users?q=' + encodeURIComponent(kw.trim())); } catch (e) { box.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  if (!r.items.length) { box.innerHTML = '<div class="empty">没有找到用户</div>'; return; }
  box.innerHTML = '<table class="tbl"><thead><tr><th></th><th>用户名</th><th>昵称</th><th>角色</th><th>空间</th><th>注册时间</th><th style="width:210px">操作</th></tr></thead><tbody>' +
    r.items.map(u => {
      const pct = Math.min(100, Math.round(u.used_bytes / u.quota_bytes * 100));
      return '<tr>' +
        '<td>' + avatarHTML(u, 30) + '</td>' +
        '<td><b>' + UI.esc(u.username) + '</b></td>' +
        '<td>' + UI.esc(u.nickname) + ' ' + certHTML(u) + '</td>' +
        '<td>' + (u.role === 'admin' ? '<span class="chip admin">管理员</span>' : '<span class="chip">用户</span>') + '</td>' +
        '<td style="min-width:130px"><div class="sbar sm"><i style="width:' + pct + '%"></i></div><span class="muted">' + UI.fmtSize(u.used_bytes) + ' / ' + UI.fmtSize(u.quota_bytes) + '</span></td>' +
        '<td>' + UI.fmtDate(u.created_at) + '</td>' +
        '<td><div class="row-acts">' +
        '<button class="btn xs" data-a="edit" data-id="' + u.id + '">' + UI.icon('edit', 14) + ' 编辑</button>' +
        '<button class="btn xs" data-a="files" data-id="' + u.id + '">' + UI.icon('folder', 14) + ' 文件</button>' +
        '<button class="btn xs" data-a="pw" data-id="' + u.id + '">' + UI.icon('key', 14) + ' 重置密码</button>' +
        '<button class="btn xs danger" data-a="del" data-id="' + u.id + '">' + UI.icon('trash', 14) + '</button>' +
        '</div></td></tr>';
    }).join('') + '</tbody></table>' +
    '<p class="muted" style="margin-top:8px">共 ' + r.total + ' 个用户</p>';
  $$('#ulist .row-acts button').forEach(b => {
    b.onclick = () => {
      const u = r.items.find(x => x.id === +b.dataset.id);
      const a = b.dataset.a;
      if (a === 'edit') editUserModal(u);
      if (a === 'files') filesModal(u);
      if (a === 'pw') resetPw(u);
      if (a === 'del') UI.confirm('确定删除用户 <b>' + UI.esc(u.username) + '</b> 及其全部文件？').then(ok => {
        if (!ok) return;
        API.del('/api/admin/users/' + u.id).then(() => { toast('已删除'); loadUsers(); }).catch(e2 => toast(e2.message, 'err'));
      });
    };
  });
}

function editUserModal(u) {
  const m = UI.modal({ title: '编辑用户 - ' + u.username, width: '480px' });
  m.body.innerHTML =
    '<div class="edit-user">' +
    '<div class="eu-av"><span id="euav">' + avatarHTML(u, 64) + '</span>' +
    '<button class="btn xs" id="euavbtn">更换头像</button><input type="file" id="euavin" hidden accept="image/png,image/jpeg,image/webp,image/gif"></div>' +
    '<label>昵称<input id="eunk" maxlength="32" value="' + UI.esc(u.nickname) + '"></label>' +
    '<label>个性签名<textarea id="eusg" maxlength="200" rows="2">' + UI.esc(u.signature) + '</textarea></label>' +
    '<label>角色<select id="eurole"><option value="user"' + (u.role !== 'admin' ? ' selected' : '') + '>普通用户</option><option value="admin"' + (u.role === 'admin' ? ' selected' : '') + '>管理员</option></select></label>' +
    '<label class="switch-row"><span>教师认证（仅增加称号，不影响功能）</span><input type="checkbox" id="eucert"' + (u.is_teacher ? ' checked' : '') + '></label>' +
    '<label>认证称号<input id="eutitle" maxlength="30" value="' + UI.esc(u.cert_title) + '" placeholder="例如：高级认证教师"></label>' +
    '<div class="eu-cert-custom" id="eucustom">' +
    '<label>认证图标</label><div class="cert-icon-pick" id="euicons">' +
    CERT_ICON_LIST.map(ic => '<button type="button" class="cip' + ((u.cert_icon || 'medal') === ic ? ' on' : '') + '" data-ic="' + ic + '" style="--cc:' + (u.cert_color || '#f0d488') + '">' + UI.icon(ic, 20) + '</button>').join('') +
    '</div>' +
    '<label>认证颜色</label><div class="cert-color-pick" id="eucolors">' +
    CERT_COLOR_LIST.map(c => '<button type="button" class="ccp' + ((u.cert_color || '#f0d488') === c ? ' on' : '') + '" data-c="' + c + '" style="background:' + c + '"></button>').join('') +
    '<input type="color" id="eucolor-custom" value="' + (u.cert_color || '#f0d488') + '" title="自定义颜色">' +
    '</div>' +
    /* v1.27: 改称号/图标/颜色时, 证书实时预览(所见即所得) */
    '<label>证书预览</label><div class="eu-cert-preview" id="euprev"></div>' +
    '</div>' +
    '<label class="switch-row"><span>聊天禁言（不能在聊天区发言）</span><input type="checkbox" id="euban"' + (u.chat_banned ? ' checked' : '') + '></label>' +
    '<label>用户组<select id="eugrp">' + groupsCache.map(g => '<option value="' + g.id + '"' + (g.id === u.group_id ? ' selected' : '') + '>' + UI.esc(g.name) + '</option>').join('') + '</select></label>' +
    '<label>空间配额 (MB)<input id="euquota" type="number" min="1" max="1000000" value="' + Math.round(u.quota_bytes / 1048576) + '">' +
    '<span class="muted">当前已用 ' + UI.fmtSize(u.used_bytes) + '</span></label></div>';
  m.foot.innerHTML = '<button class="btn" id="eucancel">取消</button> <button class="btn primary" id="eusave">保存</button>';
  m.body.querySelector('#euavbtn').onclick = () => m.body.querySelector('#euavin').click();
  m.body.querySelector('#euavin').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    const form = new FormData(); form.append('file', f, f.name || 'a.png');
    try {
      const r = await API.upload('/api/admin/users/' + u.id + '/avatar', form);
      m.body.querySelector('#euav').innerHTML = '<img class="avatar" style="width:64px;height:64px" src="' + UI.esc(r.avatar) + '">';
      toast('头像已更新');
    } catch (err) { toast(err.message, 'err'); }
    e.target.value = '';
  };
  m.foot.querySelector('#eucancel').onclick = m.close;
  /* v1.26: 认证图标/颜色 选择器 */
  let certIcon = u.cert_icon || 'medal', certColor = u.cert_color || '#f0d488';
  /* v1.27: 证书实时预览 — 用当前编辑中的值渲染真实证书卡 */
  const paintPrev = () => {
    const box = m.body.querySelector('#euprev');
    if (!box || !window.certCardHTML) return;
    box.innerHTML = certCardHTML({
      id: u.id, username: u.username,
      nickname: m.body.querySelector('#eunk').value || u.nickname,
      role: m.body.querySelector('#eurole').value,
      is_teacher: m.body.querySelector('#eucert').checked,
      cert_title: m.body.querySelector('#eutitle').value,
      cert_icon: certIcon, cert_color: certColor,
      created_at: u.created_at,
    });
    if (window.bindCertCards) bindCertCards(box);
  };
  $$('.cip', m.body).forEach(b => b.onclick = () => {
    certIcon = b.dataset.ic;
    $$('.cip', m.body).forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    paintPrev();
  });
  $$('.ccp', m.body).forEach(b => b.onclick = () => {
    certColor = b.dataset.c;
    $$('.ccp', m.body).forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    $$('.cip', m.body).forEach(x => x.style.setProperty('--cc', certColor));
    m.body.querySelector('#eucolor-custom').value = certColor;
    paintPrev();
  });
  m.body.querySelector('#eucolor-custom').oninput = e => {
    certColor = e.target.value;
    $$('.ccp', m.body).forEach(x => x.classList.remove('on'));
    $$('.cip', m.body).forEach(x => x.style.setProperty('--cc', certColor));
    paintPrev();
  };
  ['#eunk', '#eutitle'].forEach(sel => m.body.querySelector(sel).oninput = paintPrev);
  m.body.querySelector('#eurole').onchange = paintPrev;
  m.body.querySelector('#eucert').onchange = paintPrev;
  paintPrev();
  m.foot.querySelector('#eusave').onclick = async () => {
    try {
      await API.put('/api/admin/users/' + u.id, {
        nickname: m.body.querySelector('#eunk').value,
        signature: m.body.querySelector('#eusg').value,
        role: m.body.querySelector('#eurole').value,
        is_teacher: m.body.querySelector('#eucert').checked,
        cert_title: m.body.querySelector('#eutitle').value,
        cert_icon: certIcon,
        cert_color: certColor,
        chat_banned: m.body.querySelector('#euban').checked,
        quota_mb: parseInt(m.body.querySelector('#euquota').value, 10),
        group_id: parseInt(m.body.querySelector('#eugrp').value, 10),
      });
      toast('已保存');
      m.close(); loadUsers();
    } catch (err) { toast(err.message, 'err'); }
  };
}

function filesModal(u) {
  const m = UI.modal({ title: '用户文件 - ' + u.username, width: '640px' });
  m.body.innerHTML = '<div class="muted">加载中…</div>';
  m.foot.innerHTML = '<button class="btn" id="fclose">关闭</button>';
  m.foot.querySelector('#fclose').onclick = m.close;
  API.get('/api/admin/users/' + u.id + '/files').then(files => {
    m.body.innerHTML = files.length
      ? '<table class="tbl"><thead><tr><th>文件</th><th>大小</th><th>状态</th><th></th></tr></thead><tbody>' +
      files.map(f => '<tr><td>' + kindBadge(f) + ' ' + UI.esc(f.name) + '</td><td>' + UI.fmtSize(f.size) + '</td>' +
        '<td>' + (f.status === 'ready' ? '<span class="chip ok">正常</span>' : f.status === 'converting' ? '<span class="st converting">转换中</span>' : '<span class="st failed">失败</span>') + '</td>' +
        '<td><button class="btn xs danger" data-id="' + f.id + '">' + UI.icon('trash', 14) + '</button></td></tr>').join('') +
      '</tbody></table>'
      : '<div class="empty">该用户暂无文件</div>';
    $$('.tbl button[data-id]', m.body).forEach(b => b.onclick = () => {
      API.del('/api/files/' + b.dataset.id).then(() => { toast('已删除'); m.close(); }).catch(e => toast(e.message, 'err'));
    });
  }).catch(e => { m.body.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; });
}

async function resetPw(u) {
  const ok = await UI.confirm('为用户 <b>' + UI.esc(u.username) + '</b> 生成新的随机密码？');
  if (!ok) return;
  try {
    const r = await API.post('/api/admin/users/' + u.id + '/reset_password');
    const m = UI.modal({ title: '重置成功', width: '420px' });
    m.body.innerHTML = '<p>新密码（仅显示一次，请立即告知用户）：</p><div class="pw-show">' + UI.esc(r.password) + '</div>';
    m.foot.innerHTML = '<button class="btn primary" id="pwc">我已复制</button>';
    m.foot.querySelector('#pwc').onclick = m.close;
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- 邀请码 ---------- */
async function secInvites() {
  const main = $('#adm-main');
  const durOpts = '<option value="0">永久有效</option><option value="1">1 小时</option><option value="24">24 小时</option><option value="168">7 天</option><option value="720">30 天</option><option value="custom">自定义(小时)…</option>';
  main.innerHTML =
    '<div class="sec-head"><h3>邀请码管理</h3></div>' +
    '<div class="inv-cards">' +
    '<div class="card inv-card"><h4>单个创建（可自定义码）</h4>' +
    '<label>邀请码（留空自动生成）<input id="i1code" placeholder="例如 2026-NEW-TEACHER"></label>' +
    '<label>可用次数<input id="i1uses" type="number" min="1" max="9999" value="1"></label>' +
    '<label>有效期<select id="i1dur">' + durOpts + '</select></label>' +
    '<label id="i1chwrap" style="display:none">自定义时长(小时)<input id="i1ch" type="number" min="0.1" step="0.1" value="1"></label>' +
    '<label>用户组（通过此码注册的用户所属组）<select id="i1grp">' + groupOpts() + '</select></label>' +
    '<label>备注<input id="i1note" maxlength="100" placeholder="例如：数学教研组"></label>' +
    '<button class="btn primary" id="i1go">创建</button></div>' +
    '<div class="card inv-card"><h4>批量生成</h4>' +
    '<label>生成数量<input id="i2count" type="number" min="1" max="500" value="10"></label>' +
    '<label>前缀（可选）<input id="i2prefix" placeholder="例如 MATH"></label>' +
    '<label>每张可用次数<input id="i2uses" type="number" min="1" max="9999" value="1"></label>' +
    '<label>有效期<select id="i2dur">' + durOpts + '</select></label>' +
    '<label id="i2chwrap" style="display:none">自定义时长(小时)<input id="i2ch" type="number" min="0.1" step="0.1" value="1"></label>' +
    '<label>用户组<select id="i2grp">' + groupOpts() + '</select></label>' +
    '<label>备注<input id="i2note" maxlength="100"></label>' +
    '<button class="btn primary" id="i2go">批量生成</button></div>' +
    '</div><div id="ilist" style="margin-top:18px"></div>';

  const bindDur = (sel, wrap) => {
    $(sel).onchange = () => { $(wrap).style.display = $(sel).value === 'custom' ? '' : 'none'; };
  };
  bindDur('#i1dur', '#i1chwrap'); bindDur('#i2dur', '#i2chwrap');

  const durVal = (sel, ch) => $(sel).value === 'custom' ? (parseFloat($(ch).value) || 0) : parseFloat($(sel).value) || 0;

  $('#i1go').onclick = async () => {
    try {
      await API.post('/api/admin/invites', {
        code: $('#i1code').value.trim(), max_uses: +$('#i1uses').value || 1,
        duration_hours: durVal('#i1dur', '#i1ch'), note: $('#i1note').value, group_id: +$('#i1grp').value || 1,
      });
      toast('邀请码已创建'); loadInvites();
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#i2go').onclick = async () => {
    try {
      const r = await API.post('/api/admin/invites/batch', {
        count: +$('#i2count').value || 1, prefix: $('#i2prefix').value.trim(),
        max_uses: +$('#i2uses').value || 1, duration_hours: durVal('#i2dur', '#i2ch'), note: $('#i2note').value, group_id: +$('#i2grp').value || 1,
      });
      toast('已生成 ' + r.items.length + ' 个邀请码');
      loadInvites();
    } catch (e) { toast(e.message, 'err'); }
  };
  await loadInvites();
}

async function loadInvites() {
  const box = $('#ilist'); if (!box) return;
  await loadGroupsCache();
  let list;
  try { list = await API.get('/api/admin/invites'); } catch (e) { box.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  if (!list.length) { box.innerHTML = '<div class="empty">还没有邀请码</div>'; return; }
  const stMap = { active: '<span class="chip ok">有效</span>', used: '<span class="chip">已用完</span>', expired: '<span class="chip warn">已过期</span>' };
  box.innerHTML = '<table class="tbl"><thead><tr><th>邀请码</th><th>使用</th><th>状态</th><th>有效期至</th><th>备注</th><th>创建时间</th><th></th></tr></thead><tbody>' +
    list.map(i => '<tr><td><code class="icode">' + UI.esc(i.code) + '</code> <button class="btn xs" data-copy="' + UI.esc(i.code) + '">复制</button></td>' +
      '<td>' + i.used_count + ' / ' + i.max_uses + '</td>' +
      '<td>' + stMap[i.status] + '</td>' +
      '<td>' + (i.expires_at ? UI.fmtDate(i.expires_at) : '永久') + '</td>' +
      '<td>' + UI.esc(i.note || '—') + ' <span class="chip">' + UI.esc(groupName(i.group_id)) + '</span></td>' +
      '<td>' + UI.fmtDate(i.created_at) + '</td>' +
      '<td><button class="btn xs danger" data-del="' + i.id + '">' + UI.icon('trash', 14) + '</button></td></tr>').join('') +
    '</tbody></table>';
  $$('#ilist [data-copy]').forEach(b => b.onclick = () => {
    const code = b.dataset.copy;
    if (navigator.clipboard) navigator.clipboard.writeText(code).then(() => toast('已复制'), () => toast(code));
    else toast('邀请码：' + code);
  });
  $$('#ilist [data-del]').forEach(b => b.onclick = () => {
    UI.confirm('删除该邀请码？未使用完的次数将失效').then(ok => {
      if (!ok) return;
      API.del('/api/admin/invites/' + b.dataset.del).then(() => { toast('已删除'); loadInvites(); }).catch(e => toast(e.message, 'err'));
    });
  });
}

/* ---------- 设置 ---------- */
function bgRow(id, label, v) {
  return '<label>' + label + '<div style="display:flex;gap:8px"><input id="' + id + '" type="text" style="flex:1" placeholder="留空=默认; #颜色; 或图片链接" value="' + UI.esc(v || '') + '">' +
    '<button type="button" class="btn sm" data-bgup="' + id + '">上传图片</button><button type="button" class="btn sm ghost" data-bgclr="' + id + '">清除</button></div></label>';
}
async function secSettings() {
  const main = $('#adm-main');
  let s;
  try { s = await API.get('/api/admin/settings'); } catch (e) { main.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  main.innerHTML = '<h3>系统设置</h3><div class="card" style="max-width:520px">' +
    '<label>新用户默认空间 (MB)<input id="sq" type="number" min="1" max="1000000" value="' + s.default_quota_mb + '"><p class="muted">默认 500MB，仅影响之后注册的用户</p></label>' +
    '<label>单文件上传上限 (MB)<input id="sm" type="number" min="1" max="1000000" value="' + s.max_upload_mb + '"></label>' +
    '<label class="switch-row"><span>开放注册（注册始终需要邀请码）</span><input type="checkbox" id="sr"' + (s.registration_open ? ' checked' : '') + '></label>' +
    '<label>公开访问地址（域名，供微软在线放映抓取）<input id="spb" type="text" placeholder="例: http://t.fyx.best" value="' + UI.esc(s.public_base_url || '') + '"><p class="muted">微软 Office 放映要求域名+80/443 端口。填了它，即使用 IP 打开 FLA，直链也走此域名；留空则按当前浏览器地址生成</p></label>' +
    '<hr><h4>外观</h4>' +
    bgRow('sbg', '站点背景', s.site_bg) + bgRow('slbg', '登录页背景（留空=同站点背景）', s.login_bg) + bgRow('scbg', 'Windows 客户端背景', s.client_bg) +
    '<label>界面不透明度 <b id="sopv">' + (s.ui_opacity || 100) + '%</b><input id="sop" type="range" min="30" max="100" value="' + (s.ui_opacity || 100) + '"><p class="muted">有背景图时调低可透出背景（网站与客户端通用）</p></label>' +
    '<hr><h4>课件放映与白板</h4>' +
    '<label class="switch-row"><span>放映时工具栏常驻显示（不自动收回，便于翻页与板书）</span><input type="checkbox" id="stb"' + (s.toolbar_keep ? ' checked' : '') + '><p class="muted">开启后全屏放映时底栏与顶栏始终显示，避免上课时找不到画笔或翻页键</p></label>' +
    '<hr><h4>社区权限</h4>' +
    '<label class="switch-row"><span>开启论坛</span><input type="checkbox" id="sforum"' + (s.forum_enabled ? ' checked' : '') + '></label>' +
    '<label class="switch-row"><span>开启聊天区</span><input type="checkbox" id="schat"' + (s.chat_enabled ? ' checked' : '') + '></label>' +
    '<label class="switch-row"><span>允许用户创建群组（官方大厅不受影响）</span><input type="checkbox" id="sgrp"' + (s.allow_group_create ? ' checked' : '') + '></label>' +
    '<button class="btn primary" id="ssave">保存设置</button></div>';
  $('#sop').oninput = () => { $('#sopv').textContent = $('#sop').value + '%'; };
  $$('[data-bgup]').forEach(b => b.onclick = () => {
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*';
    inp.onchange = async () => {
      if (!inp.files[0]) return;
      const fd = new FormData(); fd.append('file', inp.files[0]);
      const slot = { sbg: 'site_bg', slbg: 'login_bg', scbg: 'client_bg' }[b.dataset.bgup];
      try {
        const r = await fetch('/api/admin/theme/' + slot, { method: 'POST', body: fd, headers: { Authorization: 'Bearer ' + API.token } });
        const j = await r.json(); if (!r.ok) throw new Error(j.detail || '上传失败');
        $('#' + b.dataset.bgup).value = j.url; toast('已上传并生效');
        if (window.applyTheme) fetch('/api/auth/config').then(r => r.json()).then(applyTheme);
      } catch (e) { toast(e.message, 'err'); }
    };
    inp.click();
  });
  $$('[data-bgclr]').forEach(b => b.onclick = () => { $('#' + b.dataset.bgclr).value = ''; });
  $('#ssave').onclick = async () => {
    try {
      await API.put('/api/admin/settings', {
        default_quota_mb: +$('#sq').value, max_upload_mb: +$('#sm').value,
        registration_open: $('#sr').checked,
        public_base_url: $('#spb').value.trim(),
        site_bg: $('#sbg').value.trim(), login_bg: $('#slbg').value.trim(), client_bg: $('#scbg').value.trim(),
        ui_opacity: +$('#sop').value,
        toolbar_keep: $('#stb').checked,
        forum_enabled: $('#sforum').checked,
        chat_enabled: $('#schat').checked,
        allow_group_create: $('#sgrp').checked,
      });
      toast('设置已保存');
      if (window.applyTheme) fetch('/api/auth/config').then(r => r.json()).then(applyTheme);
    } catch (e) { toast(e.message, 'err'); }
  };
}


/* ============================== v1.26 公告 / 论坛 / 聊天 管理 ============================== */

async function secAnns() {
  const main = $('#adm-main');
  let list;
  try { list = await API.get('/api/admin/announcements'); } catch (e) { main.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  const lvTxt = { info: '公告', warn: '注意', imp: '重要' };
  main.innerHTML = '<h3>公告管理</h3>' +
    '<div class="lib-actions" style="margin-bottom:14px"><button class="btn primary" id="an-new">' + UI.icon('plus', 15) + ' 新建公告</button></div>' +
    '<div class="card"><table class="tbl"><thead><tr><th>标题</th><th style="width:110px">范围</th><th style="width:70px">级别</th><th style="width:70px">已读</th><th style="width:88px">状态</th><th style="width:150px"></th></tr></thead><tbody>' +
    (list.map(a => '<tr data-id="' + a.id + '">' +
      '<td><b>' + UI.esc(a.title) + '</b><br><span class="muted">' + UI.fmtDate(a.created_at) + '</span></td>' +
      '<td>' + (a.scope === 'user' ? '专属 ' + UI.esc(a.target || '') : '全站') + '</td>' +
      '<td><span class="ann-lv ' + a.level + '">' + (lvTxt[a.level] || a.level) + '</span></td>' +
      '<td>' + a.reads + '</td>' +
      '<td><span class="chip ' + (a.active ? 'ok' : '') + '">' + (a.active ? '启用' : '停用') + '</span></td>' +
      '<td><button class="btn xs" data-op="edit">编辑</button> <button class="btn xs" data-op="toggle">' + (a.active ? '停用' : '启用') + '</button> <button class="btn xs danger" data-op="del">删除</button></td>' +
      '</tr>').join('') || '<tr><td colspan="6" class="muted" style="text-align:center;padding:30px">暂无公告</td></tr>') +
    '</tbody></table></div>';
  $('#an-new').onclick = () => annEditor(null);
  $$('#adm-main [data-op]').forEach(b => b.onclick = async () => {
    const aid = +b.closest('tr').dataset.id;
    const a = list.find(x => x.id === aid);
    if (b.dataset.op === 'edit') return annEditor(a);
    if (b.dataset.op === 'del') {
      const ok = await UI.confirm('确定删除该公告？');
      if (!ok) return;
      try { await API.del('/api/admin/announcements/' + aid); secAnns(); } catch (e) { toast(e.message, 'err'); }
    } else {
      try { await API.patch('/api/admin/announcements/' + aid, { active: !a.active }); secAnns(); } catch (e) { toast(e.message, 'err'); }
    }
  });
}

function annEditor(a) {
  const isEdit = !!a;
  const m = UI.modal({ title: isEdit ? '编辑公告' : '新建公告', width: '500px' });
  m.body.innerHTML =
    '<label>标题<input id="an-title" maxlength="100" value="' + (isEdit ? UI.esc(a.title) : '') + '"></label>' +
    '<label>内容<textarea id="an-content" rows="5">' + (isEdit ? UI.esc(a.content) : '') + '</textarea></label>' +
    '<label>级别<select id="an-level">' +
    '<option value="info"' + (isEdit && a.level === 'info' ? ' selected' : '') + '>公告(普通)</option>' +
    '<option value="warn"' + (isEdit && a.level === 'warn' ? ' selected' : '') + '>注意(橙色)</option>' +
    '<option value="imp"' + (isEdit && a.level === 'imp' ? ' selected' : '') + '>重要(红色, 优先展示)</option></select></label>' +
    '<label>范围<select id="an-scope">' +
    '<option value="global"' + (isEdit && a.scope === 'global' ? ' selected' : '') + '>全站公告</option>' +
    '<option value="user"' + (isEdit && a.scope === 'user' ? ' selected' : '') + '>指定用户(专属)</option></select></label>' +
    '<label id="an-user-row" class="hidden">目标用户<input id="an-uid" type="number" placeholder="用户 ID" value="' + (isEdit && a.target_uid ? a.target_uid : '') + '"></label>' +
    '<label class="switch-row"><span>立即启用</span><input type="checkbox" id="an-active"' + (!isEdit || a.active ? ' checked' : '') + '></label>';
  m.foot.innerHTML = '<button class="btn" id="an-cancel">取消</button> <button class="btn primary" id="an-ok">保存</button>';
  const scopeSel = m.body.querySelector('#an-scope');
  const syncRow = () => m.body.querySelector('#an-user-row').classList.toggle('hidden', scopeSel.value !== 'user');
  scopeSel.onchange = syncRow; syncRow();
  m.foot.querySelector('#an-cancel').onclick = m.close;
  m.foot.querySelector('#an-ok').onclick = async () => {
    const body = {
      title: m.body.querySelector('#an-title').value,
      content: m.body.querySelector('#an-content').value,
      level: m.body.querySelector('#an-level').value,
      scope: scopeSel.value,
      target_uid: scopeSel.value === 'user' ? +m.body.querySelector('#an-uid').value : null,
      active: m.body.querySelector('#an-active').checked,
    };
    try {
      if (isEdit) await API.patch('/api/admin/announcements/' + a.id, body);
      else await API.post('/api/admin/announcements', body);
      toast('已保存'); m.close(); secAnns();
    } catch (e) { toast(e.message, 'err'); }
  };
}

async function secForum() {
  const main = $('#adm-main');
  let boards, threads;
  try {
    boards = (await API.get('/api/forum/boards')).items;
    threads = (await API.get('/api/forum/threads')).items;
  } catch (e) { main.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  const boardName = id => { const b = boards.find(x => x.id === id); return b ? b.name : '#' + id; };
  main.innerHTML = '<h3>论坛管理</h3>' +
    '<div class="card" style="margin-bottom:16px"><h4>板块</h4>' +
    '<table class="tbl"><tbody>' + boards.map(b =>
      '<tr data-id="' + b.id + '"><td><b>' + UI.esc(b.name) + '</b><br><span class="muted">' + UI.esc(b.descr) + '</span></td>' +
      '<td style="width:80px">' + b.threads + ' 帖</td>' +
      '<td style="width:160px"><button class="btn xs" data-op="rename">重命名</button> <button class="btn xs danger" data-op="bdel">删除</button></td></tr>').join('') +
    '</tbody></table>' +
    '<div style="display:flex;gap:8px;margin-top:12px"><input id="nb-name" class="inp" placeholder="新板块名称" style="flex:1">' +
    '<input id="nb-descr" class="inp" placeholder="描述(可选)" style="flex:1">' +
    '<button class="btn" id="nb-add">添加板块</button></div></div>' +
    '<div class="card"><h4>最新帖子</h4>' +
    '<table class="tbl"><thead><tr><th>标题</th><th style="width:110px">作者</th><th style="width:90px">板块</th><th style="width:120px"></th></tr></thead><tbody>' +
    (threads.map(t => '<tr data-id="' + t.id + '">' +
      '<td>' + (t.pinned ? '📌 ' : '') + UI.esc(t.title) + (t.locked ? ' 🔒' : '') + '</td>' +
      '<td>' + UI.esc(t.author.nickname) + '</td><td>' + UI.esc(boardName(t.board_id)) + '</td>' +
      '<td><button class="btn xs" data-op="tp">' + (t.pinned ? '取消置顶' : '置顶') + '</button> <button class="btn xs" data-op="tl">' + (t.locked ? '解锁' : '锁定') + '</button> <button class="btn xs danger" data-op="tdel">删除</button></td>' +
      '</tr>').join('') || '<tr><td colspan="4" class="muted" style="text-align:center;padding:26px">暂无帖子</td></tr>') +
    '</tbody></table></div>';
  $('#nb-add').onclick = async () => {
    try { await API.post('/api/admin/forum/boards', { name: $('#nb-name').value, descr: $('#nb-descr').value }); toast('已添加'); secForum(); }
    catch (e) { toast(e.message, 'err'); }
  };
  $$('#adm-main [data-op]').forEach(b => b.onclick = async () => {
    const tr = b.closest('tr');
    const op = b.dataset.op;
    try {
      if (op === 'bdel') {
        const ok = await UI.confirm('确定删除板块及其中所有帖子？删除后不可恢复');
        if (!ok) return;
        await API.del('/api/admin/forum/boards/' + tr.dataset.id);
      } else if (op === 'rename') {
        const nm = prompt('新板块名:', '');
        if (!nm) return;
        await API.patch('/api/admin/forum/boards/' + tr.dataset.id, { name: nm });
      } else if (op === 'tp') {
        const th = threads.find(x => x.id === +tr.dataset.id);
        await API.patch('/api/forum/threads/' + tr.dataset.id, { pinned: !(th && th.pinned) });
      } else if (op === 'tl') {
        const th2 = threads.find(x => x.id === +tr.dataset.id);
        await API.patch('/api/forum/threads/' + tr.dataset.id, { locked: !(th2 && th2.locked) });
      } else if (op === 'tdel') {
        const ok2 = await UI.confirm('确定删除该帖子？');
        if (!ok2) return;
        await API.del('/api/forum/threads/' + tr.dataset.id);
      }
      secForum();
    } catch (e) { toast(e.message, 'err'); }
  });
}

async function secChat() {
  const main = $('#adm-main');
  let s, rooms;
  try {
    s = await API.get('/api/admin/settings');
    rooms = (await API.get('/api/chat/rooms')).items;
  } catch (e) { main.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  main.innerHTML = '<h3>聊天管理</h3>' +
    '<div class="card" style="max-width:520px;margin-bottom:16px"><h4>权限</h4>' +
    '<label class="switch-row"><span>开启聊天区</span><input type="checkbox" id="cs-chat"' + (s.chat_enabled ? ' checked' : '') + '></label>' +
    '<label class="switch-row"><span>允许用户创建群组</span><input type="checkbox" id="cs-grp"' + (s.allow_group_create ? ' checked' : '') + '></label>' +
    '<p class="muted">用户个人的禁言在「用户管理 → 编辑用户」里设置</p>' +
    '<button class="btn primary" id="cs-save">保存</button></div>' +
    '<div class="card"><h4>群组 (' + rooms.length + ')</h4>' +
    '<table class="tbl"><thead><tr><th>名称</th><th style="width:90px">类型</th><th style="width:90px">成员</th><th style="width:80px">消息</th><th style="width:80px"></th></tr></thead><tbody>' +
    rooms.map(r => '<tr data-id="' + r.id + '"><td><b>' + UI.esc(r.name) + '</b></td>' +
      '<td>' + (r.official ? '<span class="chip ok">官方</span>' : '用户群') + '</td>' +
      '<td>' + r.members + '</td><td>' + r.messages + '</td>' +
      '<td>' + (r.official ? '' : '<button class="btn xs danger" data-op="del">解散</button>') + '</td></tr>').join('') +
    '</tbody></table></div>';
  $('#cs-save').onclick = async () => {
    try {
      await API.put('/api/admin/settings', { chat_enabled: $('#cs-chat').checked, allow_group_create: $('#cs-grp').checked });
      toast('已保存');
    } catch (e) { toast(e.message, 'err'); }
  };
  $$('#adm-main [data-op="del"]').forEach(b => b.onclick = async () => {
    const ok = await UI.confirm('确定解散该群组(群内历史消息一并删除)？');
    if (!ok) return;
    try { await API.del('/api/chat/rooms/' + b.closest('tr').dataset.id); secChat(); } catch (e) { toast(e.message, 'err'); }
  });
}

/* ================================================================
 *  v3.6: 用户组 / 假用户与首页数据 / AI 渠道与模型
 * ================================================================ */
let groupsCache = [];
async function loadGroupsCache() {
  try { groupsCache = (await API.get('/api/admin/groups')).items; } catch (e) { }
  return groupsCache;
}
function groupName(id) { const g = groupsCache.find(x => x.id === id); return g ? g.name : ('组#' + id); }
function groupOpts(sel) {
  return groupsCache.map(g => '<option value="' + g.id + '"' + ((sel || 1) === g.id ? ' selected' : '') + '>' + UI.esc(g.name) + '</option>').join('');
}
function groupChecks(selected) {
  return '<div class="ai-grp-checks">' + groupsCache.map(g =>
    '<label><input type="checkbox" data-grp="' + g.id + '"' + ((selected || []).includes(g.id) ? ' checked' : '') + '> ' + UI.esc(g.name) + '</label>').join('') + '</div>';
}
function readGroups(root) { return $$('[data-grp]:checked', root).map(x => +x.dataset.grp); }

/* ---------- 用户组 ---------- */
async function secGroups() {
  const main = $('#adm-main');
  await loadGroupsCache();
  main.innerHTML = '<div class="sec-head"><h3>用户组</h3></div>' +
    '<p class="muted">用户组决定可以使用哪些 AI 模型。「邀请码用户」是默认组：通过邀请码注册的用户默认属于它，不能删除。' +
    '每个邀请码可以指定注册用户所属的组。</p>' +
    '<div class="card"><table class="tbl"><thead><tr><th>组名</th><th>说明</th><th style="width:90px">成员</th><th style="width:170px"></th></tr></thead><tbody>' +
    groupsCache.map(g => '<tr data-id="' + g.id + '"><td><b>' + UI.esc(g.name) + '</b>' + (g.is_default ? ' <span class="chip ok">默认</span>' : '') + '</td>' +
      '<td>' + UI.esc(g.descr || '—') + '</td><td>' + g.users + '</td>' +
      '<td><button class="btn xs" data-op="edit">编辑</button> ' + (g.is_default ? '' : '<button class="btn xs danger" data-op="del">删除</button>') + '</td></tr>').join('') +
    '</tbody></table></div>' +
    '<div class="card" style="margin-top:16px;max-width:520px"><h4>新建用户组</h4>' +
    '<label>组名<input id="g-name" maxlength="30"></label><label>说明（可选）<input id="g-descr" maxlength="100"></label>' +
    '<button class="btn primary" id="g-add">创建</button></div>';
  $('#g-add').onclick = async () => {
    try { await API.post('/api/admin/groups', { name: $('#g-name').value, descr: $('#g-descr').value }); toast('已创建'); secGroups(); }
    catch (e) { toast(e.message, 'err'); }
  };
  $$('#adm-main [data-op]').forEach(b => b.onclick = async () => {
    const id = +b.closest('tr').dataset.id;
    const g = groupsCache.find(x => x.id === id);
    try {
      if (b.dataset.op === 'del') {
        const ok = await UI.confirm('删除用户组「' + g.name + '」？组内成员将回落到默认组，AI 模型授权中的该组会被移除');
        if (!ok) return;
        await API.del('/api/admin/groups/' + id); toast('已删除'); secGroups();
      } else {
        const nm = prompt('组名', g.name); if (nm === null) return;
        const ds = prompt('说明', g.descr || ''); if (ds === null) return;
        await API.patch('/api/admin/groups/' + id, { name: nm, descr: ds }); toast('已保存'); secGroups();
      }
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ---------- 假用户 & 首页展示数据 ---------- */
async function secFake() {
  const main = $('#adm-main');
  await loadGroupsCache();
  let st, disp;
  try {
    st = await API.get('/api/admin/fake-users/stats');
    disp = await API.get('/api/admin/display');
  } catch (e) { main.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  const o = disp.offsets, sh = disp.shown;
  main.innerHTML = '<div class="sec-head"><h3>假用户 & 首页数据</h3></div>' +
    '<div class="inv-cards">' +
    '<div class="card inv-card"><h4>批量创建假用户</h4>' +
    '<p class="muted">假用户使用随机昵称，密码随机且不对外，无法登录，会加入「官方大厅」。当前假用户：<b>' + st.count + '</b> 个</p>' +
    '<label>数量（1-500）<input id="f-count" type="number" min="1" max="500" value="20"></label>' +
    '<label>用户名前缀<input id="f-prefix" maxlength="12" value="fake"></label>' +
    '<label>所属用户组<select id="f-grp">' + groupOpts() + '</select></label>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="f-go">批量创建</button>' +
    '<button class="btn danger" id="f-del">删除全部假用户</button></div></div>' +
    '<div class="card inv-card"><h4>首页展示数据</h4>' +
    '<p class="muted">首页显示 = 真实数值 + 偏移量（偏移可为负数）。用于彰显网站的欢迎程度，可随时修改。</p>' +
    '<label>注册用户 偏移（真实 ' + (sh.users - o.show_users_offset) + '）<input id="d-u" type="number" value="' + o.show_users_offset + '"></label>' +
    '<label>在线人数 偏移（真实 ' + (sh.online - o.show_online_offset) + '）<input id="d-o" type="number" value="' + o.show_online_offset + '"></label>' +
    '<label>累计访问 偏移<input id="d-h" type="number" value="' + o.show_hits_offset + '"></label>' +
    '<label>累计流量 偏移（MB）<input id="d-t" type="number" value="' + o.show_traffic_offset_mb + '"></label>' +
    '<p class="muted">当前首页显示：用户 ' + sh.users + ' · 在线 ' + sh.online + ' · 访问 ' + sh.hits + ' · 流量 ' + UI.fmtSize(sh.traffic_bytes) + '</p>' +
    '<button class="btn primary" id="d-save">保存展示数据</button></div></div>';

  $('#f-go').onclick = async () => {
    try {
      const r = await API.post('/api/admin/fake-users', {
        count: +$('#f-count').value || 1, prefix: $('#f-prefix').value.trim() || 'fake', group_id: +$('#f-grp').value || 1,
      });
      toast('已创建 ' + r.created + ' 个假用户'); secFake();
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#f-del').onclick = async () => {
    const ok = await UI.confirm('删除全部假用户？他们发布的帖子和回复也会一并删除（不可恢复）');
    if (!ok) return;
    try { const r = await API.del('/api/admin/fake-users'); toast('已删除 ' + r.deleted + ' 个'); secFake(); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#d-save').onclick = async () => {
    try {
      await API.put('/api/admin/display', {
        show_users_offset: parseInt($('#d-u').value, 10) || 0,
        show_online_offset: parseInt($('#d-o').value, 10) || 0,
        show_hits_offset: parseInt($('#d-h').value, 10) || 0,
        show_traffic_offset_mb: parseInt($('#d-t').value, 10) || 0,
      });
      toast('已保存，首页约 5 秒内刷新'); secFake();
    } catch (e) { toast(e.message, 'err'); }
  };
}

/* ---------- AI 渠道与模型 ---------- */
function chanCardHTML(c) {
  return '<div class="ai-chan" data-id="' + c.id + '">' +
    '<div class="ai-chan-row">' +
      '<label>渠道名称<input data-f="name" maxlength="40" value="' + UI.esc(c.name) + '"></label>' +
      '<label>接口地址（到 /v1 为止）<input data-f="base_url" value="' + UI.esc(c.base_url) + '"></label>' +
      '<label class="switch-row"><span>启用</span><input type="checkbox" data-f="enabled"' + (c.enabled ? ' checked' : '') + '></label>' +
    '</div>' +
    '<label>Token（每行一个，轮换使用，当前 ' + c.keys_count + ' 个）<textarea data-f="api_keys" rows="2">' + UI.esc(c.api_keys) + '</textarea></label>' +
    '<div class="ai-chan-acts"><button class="btn xs primary" data-op="csave">保存</button> ' +
      '<button class="btn xs" data-op="cfetch">获取模型列表</button> ' +
      '<button class="btn xs danger" data-op="cdel">删除渠道</button> ' +
      '<span class="muted">已导入 ' + c.models + ' 个模型</span></div></div>';
}

async function secAI() {
  const main = $('#adm-main');
  await loadGroupsCache();
  let cfg, chans, models;
  try {
    cfg = await API.get('/api/admin/ai/config');
    chans = (await API.get('/api/admin/ai/channels')).items;
    models = (await API.get('/api/admin/ai/models')).items;
  } catch (e) { main.innerHTML = '<div class="empty">' + UI.esc(e.message) + '</div>'; return; }
  const chanName = id => { const c = chans.find(x => x.id === id); return c ? c.name : ''; };
  main.innerHTML = '<div class="sec-head"><h3>AI 渠道与模型</h3></div>' +
    '<div class="card" style="max-width:760px;margin-bottom:16px"><h4>总开关与系统提示词</h4>' +
    '<label class="switch-row"><span>开启 AI 功能（AI 助手 / 写作辅助 / AI 论坛）</span><input type="checkbox" id="ai-on"' + (cfg.enabled ? ' checked' : '') + '></label>' +
    '<label>系统提示词（可选，留空使用默认）<textarea id="ai-sys" rows="3" maxlength="2000">' + UI.esc(cfg.system_prompt) + '</textarea></label>' +
    '<button class="btn primary" id="ai-cfg-save">保存</button></div>' +

    '<div class="card" style="margin-bottom:16px"><h4>渠道（OpenAI 兼容接口）</h4>' +
    '<p class="muted">一个渠道可配置多个 Token（每行一个），自动轮换；某个 Token 报错会切换到下一个。' +
    '保存后点「获取模型列表」即可直接从上游拉取可用模型，勾选导入并设置可用用户组。</p>' +
    (chans.map(chanCardHTML).join('') || '<div class="empty">还没有渠道</div>') +
    '<div class="ai-chan"><h4>新增渠道</h4><div class="ai-chan-row">' +
      '<label>渠道名称<input id="nc-name" maxlength="40" placeholder="例如：DeepSeek"></label>' +
      '<label>接口地址<input id="nc-url" placeholder="https://api.deepseek.com/v1"></label></div>' +
    '<label>Token（每行一个）<textarea id="nc-keys" rows="2" placeholder="sk-xxxx"></textarea></label>' +
    '<button class="btn primary" id="nc-add">添加渠道</button></div></div>' +

    '<div class="card"><h4>已导入模型（' + models.length + '）</h4>' +
    (models.length ? '<div class="ai-mtable-wrap"><table class="tbl ai-mtable"><thead><tr><th>模型</th><th>显示名称</th><th>介绍（可选）</th><th>可用用户组</th><th style="width:60px">启用</th><th style="width:130px"></th></tr></thead><tbody>' +
      models.map(m => '<tr class="ai-mrow" data-id="' + m.id + '">' +
        '<td><b>' + UI.esc(m.model) + '</b><br><span class="muted">' + UI.esc(m.channel) + '</span></td>' +
        '<td><input data-f="display_name" maxlength="40" value="' + UI.esc(m.display_name) + '" placeholder="同模型名"></td>' +
        '<td><input data-f="intro" maxlength="200" value="' + UI.esc(m.intro) + '" placeholder="例如：速度快，适合日常问答"></td>' +
        '<td>' + groupChecks(m.groups) + '</td>' +
        '<td><input type="checkbox" data-f="enabled"' + (m.enabled ? ' checked' : '') + '></td>' +
        '<td><button class="btn xs primary" data-op="msave">保存</button> <button class="btn xs danger" data-op="mdel">删除</button></td>' +
        '</tr>').join('') + '</tbody></table></div>'
      : '<div class="empty">还没有导入模型，请先在上方渠道里点「获取模型列表」</div>') + '</div>';

  $('#ai-cfg-save').onclick = async () => {
    try { await API.put('/api/admin/ai/config', { enabled: $('#ai-on').checked, system_prompt: $('#ai-sys').value }); toast('已保存'); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#nc-add').onclick = async () => {
    try {
      await API.post('/api/admin/ai/channels', { name: $('#nc-name').value, base_url: $('#nc-url').value, api_keys: $('#nc-keys').value, enabled: true });
      toast('渠道已添加'); secAI();
    } catch (e) { toast(e.message, 'err'); }
  };
  $$('#adm-main .ai-chan[data-id]').forEach(card => {
    const id = +card.dataset.id;
    card.querySelector('[data-op=csave]').onclick = async () => {
      try {
        await API.patch('/api/admin/ai/channels/' + id, {
          name: card.querySelector('[data-f=name]').value,
          base_url: card.querySelector('[data-f=base_url]').value,
          api_keys: card.querySelector('[data-f=api_keys]').value,
          enabled: card.querySelector('[data-f=enabled]').checked,
        });
        toast('已保存'); secAI();
      } catch (e) { toast(e.message, 'err'); }
    };
    card.querySelector('[data-op=cfetch]').onclick = () => openFetchModal(id, chanName(id));
    card.querySelector('[data-op=cdel]').onclick = async () => {
      const ok = await UI.confirm('删除渠道「' + chanName(id) + '」及其下所有模型？');
      if (!ok) return;
      try { await API.del('/api/admin/ai/channels/' + id); toast('已删除'); secAI(); }
      catch (e) { toast(e.message, 'err'); }
    };
  });
  $$('#adm-main .ai-mrow').forEach(row => {
    const id = +row.dataset.id;
    row.querySelector('[data-op=msave]').onclick = async () => {
      try {
        await API.patch('/api/admin/ai/models/' + id, {
          display_name: row.querySelector('[data-f=display_name]').value,
          intro: row.querySelector('[data-f=intro]').value,
          groups: readGroups(row),
          enabled: row.querySelector('[data-f=enabled]').checked,
        });
        toast('已保存');
      } catch (e) { toast(e.message, 'err'); }
    };
    row.querySelector('[data-op=mdel]').onclick = async () => {
      const ok = await UI.confirm('从列表移除该模型？（不影响上游）');
      if (!ok) return;
      try { await API.del('/api/admin/ai/models/' + id); secAI(); } catch (e) { toast(e.message, 'err'); }
    };
  });
}

async function openFetchModal(chid, chName) {
  let r;
  try { r = await API.get('/api/admin/ai/channels/' + chid + '/upstream'); }
  catch (e) { toast(e.message, 'err'); return; }
  if (!r.items.length) { toast('上游没有返回模型', 'err'); return; }
  const m = UI.modal({
    title: '导入模型 - ' + chName, width: '600px',
    body: '<p class="muted">勾选要导入的模型，并设置可以使用它们的用户组（不勾选任何组 = 仅管理员可用）。</p>' +
      '<div class="ai-up-list">' + r.items.map(i =>
        '<label class="ai-up-item"><input type="checkbox" value="' + UI.esc(i.id) + '"' + (i.added ? ' disabled' : '') + '> ' +
        UI.esc(i.id) + (i.added ? ' <span class="muted">（已导入）</span>' : '') + '</label>').join('') + '</div>' +
      '<div class="ai-up-acts"><button class="btn xs" id="up-all" type="button">全选未导入</button></div>' +
      '<h4 style="margin-top:12px">可用用户组</h4>' + groupChecks([1]),
  });
  m.body.querySelector('#up-all').onclick = () => m.body.querySelectorAll('.ai-up-item input:not(:disabled)').forEach(x => x.checked = true);
  m.foot.innerHTML = '<button class="btn" id="up-c">取消</button><button class="btn primary" id="up-ok">导入选中</button>';
  m.foot.querySelector('#up-c').onclick = m.close;
  m.foot.querySelector('#up-ok').onclick = async () => {
    const models = Array.from(m.body.querySelectorAll('.ai-up-item input:checked')).map(x => x.value);
    if (!models.length) { toast('请先勾选模型', 'err'); return; }
    try {
      const res = await API.post('/api/admin/ai/channels/' + chid + '/import', { models, groups: readGroups(m.body) });
      toast('已导入 ' + res.added + ' 个模型'); m.close(); secAI();
    } catch (e) { toast(e.message, 'err'); }
  };
}
