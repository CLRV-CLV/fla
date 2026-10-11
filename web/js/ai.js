/* FLA v3.6 - AI 助手: 多轮对话(流式) / 扩写缩写转写润色翻译 / AI 论坛回复 */
'use strict';

window.AI = { view, attach, draftModal, forumReply, models: loadModels };

const $a = (s, el) => (el || document).querySelector(s);
const $$a = (s, el) => Array.from((el || document).querySelectorAll(s));

let S = null;              // 当前页面状态
let modelCache = null;

async function loadModels(force) {
  if (modelCache && !force) return modelCache;
  modelCache = await API.get('/api/ai/models');
  return modelCache;
}

/* Markdown 渲染 (先整体转义再解析, 防 XSS): 标题 / 列表 / 引用 / 表格 / 代码块 / 行内代码 / 粗斜体 / 删除线 / 链接 / 分割线
   流式输出时未闭合的代码块会一直渲染为代码, 直到收到结束标记 */
function mdInline(s) {
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + t + '</a>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
       .replace(/__([^_\n]+)__/g, '<b>$1</b>')
       .replace(/~~([^~\n]+)~~/g, '<s>$1</s>')
       .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => '<code>' + codes[+i] + '</code>');
}

function mdTable(lines) {
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const head = cells(lines[0]);
  const rows = lines.slice(2).map(cells);
  return '<div class="ai-tbl-wrap"><table><thead><tr>' + head.map(c => '<th>' + mdInline(c) + '</th>').join('') +
    '</tr></thead><tbody>' + rows.map(r => '<tr>' + head.map((_, i) => '<td>' + mdInline(r[i] || '') + '</td>').join('') + '</tr>').join('') +
    '</tbody></table></div>';
}

function md(text) {
  const src = UI.esc(text || '').replace(/\r\n?/g, '\n');
  const lines = src.split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // 代码块
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { body.push(lines[i]); i++; }
      i++;
      out.push('<pre class="ai-code"><code>' + body.join('\n') + '</code></pre>');
      continue;
    }
    if (!line.trim()) { i++; continue; }
    // 标题
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) { const lv = Math.min(h[1].length + 2, 6); out.push('<h' + lv + '>' + mdInline(h[2]) + '</h' + lv + '>'); i++; continue; }
    // 分割线
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { out.push('<hr>'); i++; continue; }
    // 表格
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const tl = [line, lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { tl.push(lines[i]); i++; }
      out.push(mdTable(tl));
      continue;
    }
    // 引用
    if (/^\s*&gt;/.test(line)) {
      const q = [];
      while (i < lines.length && /^\s*&gt;/.test(lines[i])) { q.push(lines[i].replace(/^\s*&gt;\s?/, '')); i++; }
      out.push('<blockquote>' + mdInline(q.join('<br>')) + '</blockquote>');
      continue;
    }
    // 列表 (无序 / 有序)
    const ul = /^\s*[-*+]\s+/, ol = /^\s*\d+[.)]\s+/;
    if (ul.test(line) || ol.test(line)) {
      const ordered = ol.test(line);
      const re = ordered ? ol : ul;
      const items = [];
      while (i < lines.length && re.test(lines[i])) { items.push(lines[i].replace(re, '')); i++; }
      out.push((ordered ? '<ol>' : '<ul>') + items.map(x => '<li>' + mdInline(x) + '</li>').join('') + (ordered ? '</ol>' : '</ul>'));
      continue;
    }
    // 段落 (合并连续行)
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(#{1,6}\s|```|[-*+]\s|\d+[.)]\s|&gt;)/.test(lines[i]) && !(lines[i].includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1]))) {
      para.push(lines[i]); i++;
    }
    if (!para.length) { para.push(lines[i]); i++; }
    out.push('<p>' + mdInline(para.join('<br>')) + '</p>');
  }
  return out.join('');
}

/* ================================================================
 *  1. AI 对话页 (#/ai)
 * ================================================================ */
async function view() {
  document.title = 'AI 助手 - FLA';
  S = { convs: [], cur: null, msgs: [], models: [], modelId: 0, busy: false, ctrl: null, enabled: true, onlineSearch: false };
  $('#app').innerHTML = shell(
    '<div class="ai-wrap">' +
      '<aside class="ai-side">' +
        '<button class="btn primary sm ai-new" id="ai-new">' + UI.icon('plus', 14) + ' 新对话</button>' +
        '<div class="ai-list" id="ai-list"></div>' +
      '</aside>' +
      '<section class="ai-main">' +
        '<div class="ai-top"><span class="ai-title">AI 助手</span>' +
          '<label class="ai-search-toggle" title="联网搜索会把本轮问题发送给 DuckDuckGo。请勿搜索个人或敏感信息。">' +
            '<input id="ai-web-search" type="checkbox"> 联网搜索</label>' +
          '<select id="ai-model" class="inp ai-model-sel"></select></div>' +
        '<div class="ai-search-hint">仅在勾选“联网搜索”后才会联网；开启时，本轮问题会发送给 DuckDuckGo。请勿输入个人或敏感信息。</div>' +
        '<div class="ai-msgs" id="ai-msgs"></div>' +
        '<div class="ai-input">' +
          '<textarea id="ai-inp" rows="2" maxlength="8000" placeholder="输入问题或需求，Enter 发送，Shift+Enter 换行"></textarea>' +
          '<button class="btn primary" id="ai-send">' + UI.icon('send', 14) + ' 发送</button>' +
        '</div>' +
      '</section>' +
    '</div>', 'ai');
  bindLogout();
  App.setCleanup(() => { if (S && S.ctrl) S.ctrl.abort(); S = null; });

  $('#ai-new').onclick = () => newConv();
  $('#ai-send').onclick = () => send(false);
  $('#ai-web-search').onchange = e => { S.onlineSearch = !!e.target.checked; };
  $('#ai-inp').onkeydown = e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(false); }
  };

  try {
    const m = await loadModels(true);
    S.models = m.items; S.enabled = m.enabled;
  } catch (e) { toast(e.message, 'err'); }
  const sel = $a('#ai-model');
  if (!S.enabled) {
    sel.innerHTML = '<option>AI 功能已关闭</option>';
    $a('#ai-msgs').innerHTML = '<div class="empty">管理员已关闭 AI 功能</div>';
    $a('#ai-send').disabled = true; $a('#ai-inp').disabled = true;
    return;
  }
  if (!S.models.length) {
    sel.innerHTML = '<option>暂无可用模型</option>';
    $a('#ai-msgs').innerHTML = '<div class="empty">管理员尚未为你所在的用户组开放 AI 模型</div>';
    $a('#ai-send').disabled = true; $a('#ai-inp').disabled = true;
  } else {
    sel.innerHTML = S.models.map(m => '<option value="' + m.id + '" title="' + UI.esc(m.intro || '') + '">' +
      UI.esc(m.name) + '（' + UI.esc(m.channel) + '）</option>').join('');
    S.modelId = S.models[0].id;
    sel.value = S.modelId;
    const hint = S.models.find(m => m.id === S.modelId);
    sel.title = hint && hint.intro ? hint.intro : '';
    sel.onchange = e => {
      S.modelId = +e.target.value;
      sel.title = (S.models.find(m => m.id === S.modelId) || {}).intro || '';
      if (S.cur) API.patch('/api/ai/conversations/' + S.cur.id, { model_id: S.modelId }).catch(err => toast(err.message, 'err'));
    };
  }
  await refreshList();
  if (S.convs.length) openConv(S.convs[0].id); else renderMsgs();
}

async function refreshList() {
  if (!S) return;
  try { S.convs = (await API.get('/api/ai/conversations')).items; } catch (e) { toast(e.message, 'err'); }
  const box = $a('#ai-list');
  if (!box) return;
  box.innerHTML = S.convs.length ? S.convs.map(c =>
    '<div class="ai-item' + (S.cur && S.cur.id === c.id ? ' on' : '') + '" data-id="' + c.id + '">' +
      '<span>' + UI.esc(c.title) + '</span>' +
      '<button class="ai-del" data-del="' + c.id + '" title="删除">×</button></div>').join('')
    : '<div class="muted ai-empty">还没有对话</div>';
  $$a('.ai-item', box).forEach(el => el.onclick = e => {
    if (e.target.dataset.del) return;
    openConv(+el.dataset.id);
  });
  $$a('.ai-del', box).forEach(b => b.onclick = async e => {
    e.stopPropagation();
    const ok = await UI.confirm('删除该对话？聊天记录将无法恢复');
    if (!ok) return;
    try {
      await API.del('/api/ai/conversations/' + b.dataset.del);
      if (S.cur && S.cur.id === +b.dataset.del) { S.cur = null; S.msgs = []; renderMsgs(); }
      refreshList();
    } catch (err) { toast(err.message, 'err'); }
  });
}

async function newConv() {
  if (!S || S.busy) return;
  try {
    const c = await API.post('/api/ai/conversations', { model_id: S.modelId || null });
    S.cur = c; S.msgs = [];
    await refreshList();
    renderMsgs();
    $a('#ai-inp').focus();
  } catch (e) { toast(e.message, 'err'); }
}

async function openConv(id) {
  if (!S || S.busy) return;
  try {
    const c = await API.get('/api/ai/conversations/' + id);
    S.cur = c; S.msgs = c.messages || [];
    if (c.model_id && S.models.some(m => m.id === c.model_id)) {
      S.modelId = c.model_id; $a('#ai-model').value = c.model_id;
    }
    await refreshList();
    renderMsgs();
  } catch (e) { toast(e.message, 'err'); }
}

function renderMsgs() {
  const box = $a('#ai-msgs');
  if (!box) return;
  if (!S.cur) {
    box.innerHTML = '<div class="ai-hello"><h3>你好，我是 FLA AI 助手</h3>' +
      '<p class="muted">可以帮你备课设计、写作润色、答疑解惑。点击左侧「新对话」开始。</p></div>';
    return;
  }
  if (!S.msgs.length) {
    box.innerHTML = '<div class="ai-hello"><p class="muted">开始提问吧</p></div>';
    return;
  }
  const last = S.msgs[S.msgs.length - 1];
  box.innerHTML = S.msgs.map((m, i) =>
    '<div class="ai-row ' + m.role + '">' +
      '<div class="ai-bubble" data-idx="' + i + '">' + md(m.content) + '</div>' +
      '<div class="ai-tools">' +
        '<button data-copy="' + i + '">复制</button>' +
        (m.role === 'assistant' && m === last && !S.busy ? '<button data-regen="1">重新生成</button>' : '') +
      '</div>' +
    '</div>').join('');
  box.scrollTop = box.scrollHeight;
  $$a('[data-copy]', box).forEach(b => b.onclick = () => {
    const t = S.msgs[+b.dataset.copy].content;
    if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => toast('已复制'), () => toast('复制失败'));
  });
  const rg = $a('[data-regen]', box);
  if (rg) rg.onclick = () => send(true);
}

function setBusy(b) {
  S.busy = b;
  $a('#ai-send').disabled = b;
  $a('#ai-send').innerHTML = b ? '生成中…' : UI.icon('send', 14) + ' 发送';
}

async function send(regen) {
  if (!S || S.busy) return;
  const inp = $a('#ai-inp');
  const text = regen ? '' : inp.value.trim();
  if (!regen && !text) return;
  if (!S.cur) {
    try { S.cur = await API.post('/api/ai/conversations', { model_id: S.modelId || null }); }
    catch (e) { toast(e.message, 'err'); return; }
  }
  const cid = S.cur.id;
  if (regen) {
    const li = S.msgs.length - 1;
    if (li >= 0 && S.msgs[li].role === 'assistant') S.msgs.pop();
  } else {
    S.msgs.push({ id: 0, role: 'user', content: text });
    inp.value = '';
  }
  const pending = { id: 0, role: 'assistant', content: '' };
  S.msgs.push(pending);
  renderMsgs();
  setBusy(true);
  S.ctrl = new AbortController();
  let errored = false;
  try {
    const r = await fetch('/api/ai/conversations/' + cid + '/messages', {
      method: 'POST', signal: S.ctrl.signal,
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + API.token },
      body: JSON.stringify({ content: text, model_id: S.modelId || null, regenerate: !!regen, online_search: !!S.onlineSearch }),
    });
    if (!r.ok) {
      let msg = '请求失败 (' + r.status + ')';
      try { const d = await r.json(); if (d.detail) msg = d.detail; } catch (e) { }
      throw new Error(msg);
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let searching = false;
    const box = $a('#ai-msgs');
    const bubble = () => { const rows = box ? box.querySelectorAll('.ai-row.assistant .ai-bubble') : []; return rows[rows.length - 1]; };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let ev; try { ev = JSON.parse(line.slice(5)); } catch (e) { continue; }
        if (ev.searching) searching = true;
        if (ev.error) { searching = false; errored = true; pending.content += (pending.content ? '\n\n' : '') + '⚠ ' + ev.error; }
        if (ev.delta) { searching = false; pending.content += ev.delta; }
        if (ev.done && ev.message_id) pending.id = ev.message_id;
        const b = bubble();
        if (b) {
          b.innerHTML = searching && !pending.content ? '<span class="ai-search-progress">正在联网搜索…</span>' : md(pending.content);
          if (ev.error) b.classList.add('err');
          box.scrollTop = box.scrollHeight;
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') { errored = true; pending.content += (pending.content ? '\n\n' : '') + '⚠ ' + e.message; }
  } finally {
    S && (S.ctrl = null);
    if (!S) return;
    setBusy(false);
    if (!pending.content) S.msgs = S.msgs.filter(m => m !== pending);
    renderMsgs();
    refreshList();
  }
}

/* ================================================================
 *  2. 写作辅助: 挂在任意 textarea 上 (论坛发帖/回复 等)
 * ================================================================ */
const ASSIST_ITEMS = [
  ['expand', '扩写'], ['shorten', '缩写'], ['rewrite', '改写'],
  ['polish', '润色'], ['translate', '中英互译'], ['custom', '自定义指令…'],
];

function modelSelectHTML() {
  const ms = (modelCache && modelCache.items) || [];
  if (!ms.length) return '<span class="muted">暂无可用模型</span>';
  return '<select id="ai-pick-model" class="inp" style="width:auto;margin:0">' +
    ms.map(m => '<option value="' + m.id + '">' + UI.esc(m.name) + '</option>').join('') + '</select>';
}

async function runAssist(mode, text, instruction) {
  const mid = (document.getElementById('ai-pick-model') || {}).value;
  return API.post('/api/ai/assist', { mode, text, instruction: instruction || '', model_id: mid ? +mid : null }, { timeout: 150000 });
}

/* 在 textarea 旁边加一个 ✦AI 按钮: 结果预览后选择替换 / 追加 */
function attach(ta, opts) {
  if (!ta || ta.dataset.aiAttached) return;
  ta.dataset.aiAttached = '1';
  opts = opts || {};
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn xs ai-attach-btn';
  btn.innerHTML = UI.icon('sparkle', 13) + ' AI';
  btn.title = '扩写 / 缩写 / 改写 / 润色 / 翻译';
  ta.insertAdjacentElement('afterend', btn);
  btn.onclick = async () => {
    await loadModels().catch(() => ({ items: [] }));
    const m = UI.modal({
      title: 'AI 写作辅助',
      body: '<div class="ai-pop"><div class="ai-pop-row">' + modelSelectHTML() + '</div>' +
        '<div class="ai-pop-row ai-pop-modes">' + ASSIST_ITEMS.map(([k, l]) =>
          '<button type="button" class="btn sm" data-mode="' + k + '">' + l + '</button>').join('') + '</div>' +
        '<input id="ai-pop-instr" class="inp" placeholder="自定义指令，如：改成更口语化的语气（选“自定义指令”时使用）">' +
        '<div id="ai-pop-out" class="ai-pop-out muted">选择一种处理方式，处理的是选中的文字；未选中则处理全部内容。</div></div>',
      width: '560px',
    });
    m.body.querySelectorAll('[data-mode]').forEach(b => b.onclick = async () => {
      const sel = ta.selectionStart !== ta.selectionEnd ? ta.value.slice(ta.selectionStart, ta.selectionEnd) : ta.value;
      const mode = b.dataset.mode;
      const instr = m.body.querySelector('#ai-pop-instr').value.trim();
      if (!sel.trim()) { toast('请先输入内容', 'err'); return; }
      if (mode === 'custom' && !instr) { toast('请输入指令', 'err'); return; }
      const out = m.body.querySelector('#ai-pop-out');
      out.className = 'ai-pop-out'; out.innerHTML = '<span class="muted">AI 处理中…</span>';
      try {
        const r = await runAssist(mode, sel, mode === 'custom' ? instr : instr);
        out.innerHTML = '<div class="ai-pop-result">' + md(r.text) + '</div>' +
          '<div class="ai-pop-acts"><button type="button" class="btn sm" id="ai-pop-app">替换选中/全文</button>' +
          '<button type="button" class="btn sm" id="ai-pop-add">追加到末尾</button></div>';
        out.querySelector('#ai-pop-app').onclick = () => {
          const full = ta.value, s0 = ta.selectionStart, s1 = ta.selectionEnd;
          if (s0 !== s1) ta.value = full.slice(0, s0) + r.text + full.slice(s1);
          else ta.value = r.text;
          ta.dispatchEvent(new Event('input'));
          m.close(); toast('已替换');
        };
        out.querySelector('#ai-pop-add').onclick = () => {
          ta.value = (ta.value ? ta.value + '\n' : '') + r.text;
          ta.dispatchEvent(new Event('input'));
          m.close(); toast('已追加');
        };
      } catch (e) {
        out.className = 'ai-pop-out err'; out.textContent = e.message;
      }
    });
    if (opts.onOpen) opts.onOpen(m);
  };
}

/* ================================================================
 *  3. AI 起草帖子: 输入主题 → 生成 标题 + 正文 → 回填到发帖框
 * ================================================================ */
function draftModal(onFill) {
  loadModels().catch(() => { }).then(() => {
    const m = UI.modal({
      title: 'AI 起草帖子',
      body: '<div class="ai-pop"><div class="ai-pop-row">' + modelSelectHTML() + '</div>' +
        '<label>主题与要求<textarea id="ai-draft-in" rows="4" maxlength="1000" placeholder="例如：分享一个让初三复习课更高效的小技巧，口语化，300 字左右"></textarea></label></div>',
      width: '560px',
    });
    m.foot.innerHTML = '<button class="btn" id="ai-draft-c">取消</button><button class="btn primary" id="ai-draft-ok">生成</button>';
    m.foot.querySelector('#ai-draft-c').onclick = m.close;
    m.foot.querySelector('#ai-draft-ok').onclick = async () => {
      const t = m.body.querySelector('#ai-draft-in').value.trim();
      if (!t) { toast('请输入主题', 'err'); return; }
      const ok = m.foot.querySelector('#ai-draft-ok');
      ok.disabled = true; ok.textContent = '生成中…';
      try {
        const r = await runAssist('draft', t, '');
        const text = r.text || '';
        const mt = text.match(/^\s*标题[:：]\s*(.+)/);
        const title = mt ? mt[1].trim().slice(0, 100) : t.slice(0, 40);
        const body = mt ? text.replace(/^\s*标题[:：].*\n?/, '').trim() : text.trim();
        m.close();
        onFill && onFill(title, body);
        toast('已生成草稿，可编辑后发布');
      } catch (e) {
        ok.disabled = false; ok.textContent = '生成';
        toast(e.message, 'err');
      }
    };
  });
}

/* ================================================================
 *  4. AI 论坛: 让 AI 助手在帖子下回复
 * ================================================================ */
async function forumReply(tid, onDone) {
  await loadModels().catch(() => { });
  const ms = (modelCache && modelCache.items) || [];
  if (!ms.length) { toast('管理员尚未开放 AI 模型', 'err'); return; }
  const m = UI.modal({
    title: '让 AI 助手回复',
    body: '<div class="ai-pop"><div class="ai-pop-row">' + modelSelectHTML() + '</div>' +
      '<p class="muted" style="margin-top:10px">AI 会阅读本帖和最近回复，以「AI 助手」身份发表一条回复。</p></div>',
    width: '460px',
  });
  m.foot.innerHTML = '<button class="btn" id="fr-c">取消</button><button class="btn primary" id="fr-ok">让 AI 回复</button>';
  m.foot.querySelector('#fr-c').onclick = m.close;
  m.foot.querySelector('#fr-ok').onclick = async () => {
    const mid = m.body.querySelector('#ai-pick-model').value;
    const ok = m.foot.querySelector('#fr-ok');
    ok.disabled = true; ok.textContent = 'AI 思考中…';
    try {
      await API.post('/api/ai/forum/reply', { thread_id: tid, model_id: +mid }, { timeout: 150000 });
      m.close(); toast('AI 已回复');
      onDone && onDone();
    } catch (e) {
      ok.disabled = false; ok.textContent = '让 AI 回复';
      toast(e.message, 'err');
    }
  };
}
