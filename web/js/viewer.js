/* FLA (FYX Lesson All) - 课件查看器 + 互动白板引擎
   功能: PDF/图片/音视频渲染、笔/荧光笔/橡皮(含清屏)、几何图形(直线/箭头/矩形/椭圆/三角形+角度吸附)、
        文本框、激光笔、圈选(复制/编辑/删除/全选)、双指缩放、撤销/重做、翻页/加页、截图、
        对象橡皮/像素橡皮、无限画布白板、PDF页数自动校正、新建白板、
        OnlyOffice 动画放映、课堂倒计时、全屏、白板底色、自动保存批注、配置记忆 */
'use strict';

(function () {

  const PEN_COLORS = ['#1f2937', '#ef4444', '#2563eb', '#059669', '#f59e0b', '#ffffff'];
  const MARKER_COLORS = ['#fde047', '#86efac', '#93c5fd', '#f9a8d4', '#fdba74'];
  const SHAPE_COLORS = ['#111827', '#ef4444', '#2563eb', '#059669', '#9333ea', '#ffffff'];
  const TEXT_COLORS = ['#1f2937', '#ef4444', '#2563eb', '#059669', '#f59e0b', '#ffffff'];
  const FONT_STACK = '-apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';
  const TOOLS = ['select', 'pen', 'marker', 'shape', 'text', 'laser', 'eraser'];
  const KEY_TOOL = { '1': 'select', '2': 'pen', '3': 'marker', '4': 'shape', '5': 'text', '6': 'laser', '7': 'eraser' };
  const SHAPE_LIST = [['line', '直线'], ['arrow', '箭头'], ['rect', '矩形'], ['ellipse', '椭圆'], ['triangle', '三角形']];
  const SHAPE_MINI = {
    line: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 19L19 5"/></svg>',
    arrow: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 19L19 5"/><path d="M13 5h6v6"/></svg>',
    rect: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="6" width="16" height="12" rx="1"/></svg>',
    ellipse: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="12" rx="9" ry="7"/></svg>',
    triangle: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 4l9 16H3z"/></svg>',
  };
  const DEFAULT_CFG = {
    pen: { color: '#1f2937', width: 3 },
    marker: { color: '#fde047', width: 16 },
    eraser: { width: 28, mode: 'object' },  // object=对象橡皮 pixel=像素橡皮
    shape: { type: 'line', color: '#111827', width: 3 },
    text: { color: '#1f2937', size: 28 },
  };

  let V = null;
  let raf = 0;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const deep = o => JSON.parse(JSON.stringify(o));

  function storeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function storeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }

  window.Viewer = { open, destroy };

  function destroy() {
    if (!V) return;
    V.destroyed = true;
    try { if (V.dirty) flushSave(); } catch (e) { }
    stopTimer();
    if (V.pollT) clearInterval(V.pollT);
    if (V.saveT) clearTimeout(V.saveT);
    if (V.laserT) cancelAnimationFrame(V.laserT);
    if (V.textEdit) { try { V.textEdit.el.remove(); } catch (e) { } V.textEdit = null; }
    if (V._keyHandler) window.removeEventListener('keydown', V._keyHandler);
    if (V._gestureStop) document.removeEventListener('gesturestart', V._gestureStop);
    if (V._resizeHandler) window.removeEventListener('resize', V._resizeHandler);
    if (V.msStage) { try { V.msStage.destroy(); } catch (e) { } V.msStage = null; }
    V = null;
  }

  async function open(fileId) {
    destroy();
    V = {
      id: fileId, meta: null, doc: null, pdfDoc: null, pdfjs: null,
      view: { scale: 1, tx: 0, ty: 0 }, tool: 'pen',
      cfg: deep(DEFAULT_CFG),
      undoStack: [], redoStack: [],
      sel: null, lasso: null, live: null, erased: null, lastPointer: null,
      pinch: null, moveGesture: null, scaleGesture: null,
      pointers: new Map(), blockGestures: false,
      dirty: false, saveT: null, pollT: null, bgGen: 0,
      renderTask: null, destroyed: false, forceBlank: false,
      mediaLock: false, mediaEl: null, pidSeq: 0, cw: 0, ch: 0, dpr: 1, rect: null,
      officeMode: false, oo: null, ooHolder: null, ooPage: null, _loadedScripts: {},
      laserPts: [], laserActive: false, laserT: 0,
      textEdit: null, timerInt: null, timerEl: null, timerRun: false, timerEnd: 0, timerLeft: 0,
      msStage: null,
    };
    loadCfg();
    screenMsg('<div class="spin"></div><p>正在加载课件…</p>');
    try {
      V.meta = await API.get('/api/files/' + fileId + '/meta');
    } catch (e) {
      screenMsg('<p class="err-t">' + UI.esc(e.message) + '</p><button class="btn" onclick="location.hash=\'#/library\'">返回</button>');
      return;
    }

    document.title = (V.meta.name || '课件') + ' - FLA';

    // v1.19: Office 文档直接嵌入微软在线视图(真实 Office 字体), 不再等待转换/渲染静态 PDF
    if (V.meta.kind === 'office' && !V.forceBlank &&
        /^(ppt|pptx|doc|docx|xls|xlsx)$/.test(V.meta.ext || '')) {
      return msViewer();
    }

    if (V.meta.kind === 'office' && !V.forceBlank) {
      while (V.meta.status === 'converting') {
        screenMsg('<div class="spin"></div><p>服务器正在转换文档（含字体适配），请稍候…</p>');
        await sleep(2000);
        if (V.destroyed || !V) return;
        try { V.meta = await API.get('/api/files/' + fileId + '/meta'); } catch (e) { return; }
      }
      if (V.meta.status !== 'ready') { failedScreen(); return; }
    }

    await initDoc();
    if (V.destroyed || !V) return;
    buildUI();
    await showPage(0);
    if (V.destroyed || !V) return;
    if (V.meta.missing_fonts && V.meta.missing_fonts.length) {
      const fs = V.meta.missing_fonts.slice(0, 3).join('、');
      toast('文档使用了 ' + V.meta.missing_fonts.length + ' 种服务器未安装的字体（' + fs + '…），已自动用开源字体替代渲染', 'warn');
    }
  }

  function screenMsg(html) {
    const app = document.getElementById('app');
    if (app) app.innerHTML = '<div class="v-msgscreen"><div class="v-msgbox">' + html + '</div></div>';
  }

  /* ---------- 微软嵌入预览模式: 委托 MSStage，只删工具栏，保留顶栏返回/翻页/全屏等核心操作 ---------- */
  async function msViewer() {
    const app = document.getElementById('app');
    if (!window.MSStage) {
      await fallbackLocalViewer();
      return;
    }
    app.innerHTML = '';
    try {
      V.msStage = await window.MSStage.mount({
        fid: V.id, token: API.token, meta: V.meta, mode: 'view', mount: app,
        onExit: () => { location.hash = '#/library'; },
      });
      if (window.App && App.setCleanup) App.setCleanup(() => destroy());
    } catch (e) {
      console.warn('微软在线预览挂载失败，自动降级至本地原生引擎:', e);
      await fallbackLocalViewer();
    }
  }

  async function fallbackLocalViewer() {
    try {
      await initDoc();
      if (V.destroyed || !V) return;
      buildUI();
      await showPage(0);
    } catch (err) {
      screenMsg('<p class="err-t">' + UI.esc(err.message || '课件预览加载失败') + '</p>' +
        '<button class="btn" onclick="location.hash=\'#/library\'">返回课件库</button>');
    }
  }


  function failedScreen() {
    const err = V.meta.error || '转换失败';
    screenMsg('<p class="err-t">文档转换失败</p><p class="muted">' + UI.esc(err) + '</p>' +
      '<div class="btn-row">' +
      '<button class="btn primary" id="frt">重试转换</button>' +
      '<button class="btn" id="fblank">直接使用空白白板</button>' +
      '<button class="btn ghost" onclick="location.hash=\'#/library\'">返回</button></div>');
    const rt = document.getElementById('frt'), bl = document.getElementById('fblank');
    if (rt) rt.onclick = async () => {
      try { await API.post('/api/files/' + V.id + '/retry'); } catch (e) { toast(e.message, 'err'); return; }
      open(V.id);
    };
    if (bl) bl.onclick = () => { V.forceBlank = true; open(V.id); };
  }

  async function initDoc() {
    const m = V.meta;
    let pages = null, strokes = {};
    try {
      const ann = await API.get('/api/files/' + V.id + '/annotations');
      if (ann && ann.pages && ann.pages.length) {
        let stale = false;
        if ((m.kind === 'office' || m.kind === 'pdf') && !V.forceBlank) {
          // 自愈: 旧版bug可能存下"单页空白"结构(无任何笔迹) —— 丢弃并按真实页数重建
          const allBlank = ann.pages.every(q => q.t === 'blank');
          const hasInk = Object.keys(ann.strokes || {}).some(k => (ann.strokes[k] || []).length);
          if (allBlank && !hasInk && (m.pages || 1) > ann.pages.length) stale = true;
        }
        if (!stale) { pages = ann.pages; strokes = ann.strokes || {}; }
      }
    } catch (e) { }
    if (!pages) {
      pages = [];
      if (!V.forceBlank && (m.kind === 'pdf' || m.kind === 'office')) {
        for (let i = 0; i < (m.pages || 1); i++) pages.push({ t: 'pdf', n: i, pid: 'p' + i });
      } else if (!V.forceBlank && m.kind === 'image') {
        pages = [{ t: 'image', pid: 'img0' }];
      } else if (!V.forceBlank && (m.kind === 'audio' || m.kind === 'video')) {
        pages = [{ t: 'media', pid: 'media0' }];
      } else if (m.kind === 'board') {
        pages = [{ t: 'blank', pid: 'b0', w: 1280, h: 720, bg: 'w' }];
      } else {
        pages = [{ t: 'blank', pid: 'b0', w: 1280, h: 720, bg: 'w' }];
      }
    }
    V.pidSeq = pages.reduce((a, p) => {
      const mm = /^b(\d+)$/.exec(p.pid || '');
      return mm ? Math.max(a, +mm[1]) : a;
    }, 0);
    V.doc = { pages, strokes, cur: 0 };
  }

  /* ============================== UI 构建 ============================== */

  function buildUI() {
    const isMedia = V.meta.kind === 'audio' || V.meta.kind === 'video';
    const ooOK = !!V.meta.onlyoffice_enabled;
    document.getElementById('app').innerHTML =
      '<div id="viewer">' +
      '<header class="v-top">' +
      '<button class="vbtn ghost" id="vexit" title="返回课件库">' + UI.icon('back', 20) + '</button>' +
      '<div class="v-title" id="vtitle">' + UI.esc(V.meta.name || '课件') + '</div>' +
      '<span class="v-save" id="vsave">已保存</span>' +
      '<button class="vbtn ghost sm" id="vtimer" title="课堂倒计时">' + UI.icon('timer', 18) + '</button>' +
      '<button class="vbtn ghost sm" id="vfull" title="全屏">' + UI.icon('maximize', 18) + '</button>' +
      '<div class="v-zoom"><span id="vzoom">100%</span><button class="vbtn sm" id="vfit" title="适应画面">' + UI.icon('zoomReset', 16) + '</button></div>' +
      '</header>' +
      '<div id="vstage"><div id="bgWrap"></div><canvas id="ink"></canvas>' +
      '<div id="selbar" class="hidden">' +
      '<button id="sbdup">' + UI.icon('copy', 15) + ' 复制</button>' +
      '<button id="sbdel" class="danger">' + UI.icon('trash', 15) + ' 删除</button>' +
      '<span class="hint">拖动移动 · 角点缩放</span></div>' +
      '</div>' +
      '<div id="vpop" class="hidden"></div>' +
      '<nav class="v-toolbar" id="vbar">' +
      toolBtn('select', '选择 / 圈选 (1)') + toolBtn('pen', '笔 (2)') + toolBtn('marker', '荧光笔 (3)') +
      toolBtn('shape', '几何图形 (4)') + toolBtn('text', '文本 (5)') + toolBtn('laser', '激光笔 (6)') +
      toolBtn('eraser', '橡皮 (7)') +
      '<i class="v-sep"></i>' +
      '<button class="vbtn" id="tb-undo" title="撤销 (Ctrl+Z)">' + UI.icon('undo', 20) + '</button>' +
      '<button class="vbtn" id="tb-redo" title="取消撤销 (Ctrl+Y)">' + UI.icon('redo', 20) + '</button>' +
      '<i class="v-sep"></i>' +
      '<button class="vbtn" id="tb-prev" title="上一页">' + UI.icon('chevL', 20) + '</button>' +
      '<span class="v-page" id="pgind">1 / 1</span>' +
      '<button class="vbtn" id="tb-next" title="下一页">' + UI.icon('chevR', 20) + '</button>' +
      '<button class="vbtn" id="tb-addpage" title="在当前页后新增空白页">' + UI.icon('plusPage', 20) + '</button>' +
      '<i class="v-sep"></i>' +
      '<button class="vbtn" id="tb-shot" title="截图">' + UI.icon('camera', 20) + '</button>' +
      '<button class="vbtn" id="tb-bg" title="白板底色（白/黑/绿）">' + UI.icon('palette', 20) + '</button>' +
      '<i class="v-sep"></i><button class="vbtn" id="tb-present" title="全屏放映 (F9)">' + UI.icon('play', 20) + '</button>' +
      (ooOK ? '<i class="v-sep"></i><button class="vbtn" id="tb-oo" title="动画放映 (OnlyOffice)">' + UI.icon('play', 20) + '</button>' : '') +
      ((isMedia || ooOK) ? '<i class="v-sep"></i><button class="vbtn" id="tb-lock" title="切换 白板/内容交互">' + UI.icon('unlock', 20) + '</button>' : '') +
      '</nav></div>';

    V.stage = document.getElementById('vstage');
    V.bgWrap = document.getElementById('bgWrap');
    V.ink = document.getElementById('ink');
    V.ictx = V.ink.getContext('2d');
    V.selbar = document.getElementById('selbar');
    V.pop = document.getElementById('vpop');

    document.getElementById('vexit').onclick = () => { location.hash = '#/library'; };
    document.getElementById('vfit').onclick = resetView;
    document.getElementById('vtimer').onclick = toggleTimer;
    document.getElementById('vfull').onclick = toggleFullscreen;
    document.getElementById('tb-undo').onclick = doUndo;
    document.getElementById('tb-redo').onclick = doRedo;
    document.getElementById('tb-prev').onclick = () => gotoPage(V.doc.cur - 1);
    document.getElementById('tb-next').onclick = () => gotoPage(V.doc.cur + 1);
    document.getElementById('tb-addpage').onclick = addPage;
    document.getElementById('tb-shot').onclick = e => togglePopover('shot', e.currentTarget);
    document.getElementById('tb-bg').onclick = cycleBg;

    if (isMedia || ooOK) {
      V.lockBtn = document.getElementById('tb-lock');
      V.lockBtn.onclick = () => {
        V.mediaLock = !V.mediaLock;
        V.stage.style.pointerEvents = V.mediaLock ? 'none' : 'auto';
        if (V.ooHolder) V.ooHolder.style.pointerEvents = V.mediaLock ? 'auto' : 'none';
        if (V.mediaEl) V.mediaEl.style.pointerEvents = V.mediaLock ? 'auto' : 'none';
        V.lockBtn.innerHTML = UI.icon(V.mediaLock ? 'lock' : 'unlock', 20);
        V.lockBtn.classList.toggle('on', V.mediaLock);
        toast(V.mediaLock ? '已切换为内容交互模式（可点击播放/放映）' : '已切换为白板模式');
      };
    }
    document.getElementById('tb-present').onclick = openPresent;
    if (ooOK) document.getElementById('tb-oo').onclick = toggleOffice;

    document.getElementById('sbdup').onclick = duplicateSel;
    document.getElementById('sbdel').onclick = deleteSel;

    TOOLS.forEach(t => {
      document.getElementById('tb-' + t).onclick = e => onToolBtn(t, e.currentTarget);
    });

    bindStage();
    V._keyHandler = e => {
      if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); doRedo(); }
      else if (e.key === 'ArrowLeft' || e.key === 'PageUp') gotoPage(V.doc.cur - 1);
      else if (e.key === 'ArrowRight' || e.key === 'PageDown') gotoPage(V.doc.cur + 1);
      else if (e.key === 'Escape') { closePopover(); clearSelection(); }
      else if ((e.key === 'Delete' || e.key === 'Backspace') && V.sel && V.sel.ids.size) { e.preventDefault(); deleteSel(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); selectAll(); }
      else if (e.key === 'F9') { e.preventDefault(); openPresent(); }
      else if (KEY_TOOL[e.key] && !e.ctrlKey && !e.metaKey && !e.altKey) setTool(KEY_TOOL[e.key]);
    };
    window.addEventListener('keydown', V._keyHandler);
    V._gestureStop = e => e.preventDefault();
    document.addEventListener('gesturestart', V._gestureStop);
    V._resizeHandler = () => layout();
    window.addEventListener('resize', V._resizeHandler);

    updateToolUI();
    updateHistoryUI();
    layout();
    if (window.App && App.setCleanup) App.setCleanup(() => destroy());
  }

  function openPresent() {
    var m = V.meta || {};
    if (m.kind === 'office' && /^(ppt|pptx|doc|docx|xls|xlsx)$/.test(m.ext || '')) {
      // v1.18: PPT 默认微软放映轨道(真实 PowerPoint 引擎); MS 页内可切自研引擎
      window.open('/present.html?fid=' + V.id + '&token=' + encodeURIComponent(API.token) + '&track=ms', '_blank');
      return;
    }
    // v1.12 自研放映: 全屏翻页 + 元素入场动画 + 放映中板书(兼容旧浏览器)
    window.open('/present.html?fid=' + V.id + '&token=' + encodeURIComponent(API.token), '_blank');
  }

  function toolBtn(t, title) {
    const icon = t === 'shape' ? 'shapes' : t === 'text' ? 'text' : t;
    return '<button class="vbtn" id="tb-' + t + '" title="' + title + '">' + UI.icon(icon, 20) + '</button>';
  }

  function cur() {
    if (V.officeMode) return V.ooPage;
    return V.doc.pages[V.doc.cur];
  }
  function strokesOf(pid) { return V.doc.strokes[pid] || (V.doc.strokes[pid] = []); }
  function k() { const p = cur(); return (p && p.w ? p.w : 1280) / 960; }

  /* ============================== 页面与背景 ============================== */

  async function ensurePdf() {
    if (V.pdfDoc) return;
    // 使用 ES5 legacy 构建的经典脚本, 兼容学校旧电脑浏览器(Chrome 60+)
    await loadScript('/lib/pdfjs/pdf.min.js');
    const lib = window.pdfjsLib;
    if (!lib) throw new Error('PDF 组件加载失败: 请强制刷新页面(Ctrl+F5); 若反复出现, 请把浏览器升级到较新版本');
    lib.GlobalWorkerOptions.workerSrc = '/lib/pdfjs/pdf.worker.min.js';
    V.pdfjs = lib;
    V.pdfDoc = await lib.getDocument({
      url: '/api/files/' + V.id + '/pdf',
      httpHeaders: { Authorization: 'Bearer ' + API.token },
    }).promise;
  }

  function loadImage(src) {
    return new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => res(img);
      img.onerror = () => rej(new Error('图片加载失败'));
      img.src = src;
    });
  }

  async function gotoPage(i) {
    if (!V || !V.doc || V.officeMode) return;
    i = clamp(i, 0, V.doc.pages.length - 1);
    if (i === V.doc.cur && V.bgBuilt) { resetView(); return; }
    V.doc.cur = i;
    await showPage(i);
  }

  function reconcilePdfPages(p) {
    if (!V.pdfDoc) return p;
    const np = V.pdfDoc.numPages;
    if (V.doc.pages.length !== np && V.doc.pages.every(q => q.t === 'pdf')) {
      V.doc.pages = Array.from({ length: np }, (_, n) => ({ t: 'pdf', n, pid: 'p' + n }));
      V.doc.cur = clamp(V.doc.cur, 0, np - 1);
      saveSoon();
      return cur();
    }
    return p;
  }

  async function showPage(i) {
    let p = cur();
    clearSelection(); V.live = null; V.lasso = null; V.erased = null;
    commitTextEdit(true);
    updatePageUI();
    try {
      if (p.t === 'pdf') {
        await ensurePdf();
        if (V.destroyed) return;
        p = reconcilePdfPages(p);
        if (!p.pdfPage) {
          p.pdfPage = await V.pdfDoc.getPage(p.n + 1);
          const vp = p.pdfPage.getViewport({ scale: 1 });
          p.w = vp.width; p.h = vp.height;
        }
      } else if (p.t === 'image') {
        if (!p.img) {
          p.img = await loadImage('/api/files/' + V.id + '/raw?token=' + API.token);
          p.w = p.img.naturalWidth || 1280; p.h = p.img.naturalHeight || 720;
        }
      } else if (p.t === 'media') {
        p.w = 1280; p.h = 720;
      } else {
        p.w = p.w || 1280; p.h = p.h || 720;
      }
    } catch (e) {
      toast(e.message || '页面加载失败', 'err');
      p.t = 'blank'; p.w = p.w || 1280; p.h = p.h || 720;
    }
    if (V.destroyed) return;
    resetView();
    buildBg();
    layout();
    paintBg();
  }

  function buildBg() {
    const p = cur();
    V.bgGen++;
    V.bgWrap.innerHTML = '';
    V.mediaEl = null;
    V.bgWrap.classList.toggle('blank-page', p.t === 'blank');
    V.bgWrap.classList.toggle('inf', p.t === 'blank');
    V.bgWrap.classList.toggle('media-page', p.t === 'media');
    V.bgWrap.classList.toggle('oo-page', p.t === 'oo');
    V.bgWrap.classList.remove('bg-k', 'bg-g');
    if (p.t === 'blank' && p.bg === 'k') V.bgWrap.classList.add('bg-k');
    if (p.t === 'blank' && p.bg === 'g') V.bgWrap.classList.add('bg-g');
    V.bgBuilt = true;
    if (p.t === 'pdf' || p.t === 'image') {
      const c = document.createElement('canvas');
      c.className = 'bgc';
      V.bgCanvas = c;
      V.bgWrap.appendChild(c);
    } else if (p.t === 'media') {
      if (V.meta.kind === 'video') {
        const v = document.createElement('video');
        v.controls = true; v.playsInline = true; v.preload = 'metadata';
        v.src = '/api/files/' + V.id + '/raw?token=' + API.token;
        v.style.pointerEvents = 'auto';
        V.bgWrap.appendChild(v);
        V.mediaEl = v;
      } else {
        const wrap = document.createElement('div');
        wrap.className = 'audio-wrap';
        wrap.innerHTML = '<div class="audio-ico">♪</div>';
        const a = document.createElement('audio');
        a.controls = true; a.preload = 'metadata';
        a.src = '/api/files/' + V.id + '/raw?token=' + API.token;
        a.style.pointerEvents = 'auto';
        wrap.appendChild(a);
        V.bgWrap.appendChild(wrap);
        V.mediaEl = a;
      }
    } else {
      V.bgCanvas = null;
    }
  }

  function fit() {
    const p = cur();
    if (!p || !p.w) return 1;
    return Math.min((V.cw - 20) / p.w, (V.ch - 20) / p.h);
  }

  function layout() {
    if (!V.stage) return;
    V.dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    V.rect = V.stage.getBoundingClientRect();
    V.cw = V.rect.width; V.ch = V.rect.height;
    V.ink.width = Math.round(V.cw * V.dpr);
    V.ink.height = Math.round(V.ch * V.dpr);
    V.ink.style.width = V.cw + 'px';
    V.ink.style.height = V.ch + 'px';
    applyView();
    paintBg();
  }

  function applyView() {
    const p = cur();
    if (!p || !p.w) return;
    if (p.t === 'blank') {            // 无限画布: 背景铺满整个舞台, 自由平移缩放
      const z = document.getElementById('vzoom');
      if (z) z.textContent = Math.round(V.view.scale * 100) + '%';
      scheduleRedraw();
      updateSelBar();
      return;
    }
    const base = fit() * V.view.scale;
    const w = p.w * base, h = p.h * base;
    V.bgWrap.style.width = w + 'px';
    V.bgWrap.style.height = h + 'px';
    V.bgWrap.style.left = (V.cw / 2 + V.view.tx - w / 2) + 'px';
    V.bgWrap.style.top = (V.ch / 2 + V.view.ty - h / 2) + 'px';
    const z = document.getElementById('vzoom');
    if (z) z.textContent = Math.round(V.view.scale * 100) + '%';
    scheduleRedraw();
    schedulePaint();
    updateSelBar();
  }

  function resetView() {
    V.view = { scale: 1, tx: 0, ty: 0 };
    applyView();
  }

  let paintT = null;
  function schedulePaint() {
    if (paintT) clearTimeout(paintT);
    paintT = setTimeout(() => { paintT = null; paintBg(); }, 280);
  }

  function paintBg() {
    const p = cur();
    if (!p || !V.bgCanvas) return;
    const base = fit() * V.view.scale;
    if (p.t === 'image' && p.img) {
      const c = V.bgCanvas;
      c.width = Math.max(1, Math.round(p.w * base * V.dpr));
      c.height = Math.max(1, Math.round(p.h * base * V.dpr));
      const x = c.getContext('2d');
      x.fillStyle = '#fff'; x.fillRect(0, 0, c.width, c.height);
      x.drawImage(p.img, 0, 0, c.width, c.height);
    } else if (p.t === 'pdf' && p.pdfPage) {
      drawPdf(p);
    }
  }

  function drawPdf(p) {
    const base = fit() * V.view.scale;
    const c = V.bgCanvas;
    const pxW = Math.min(Math.max(1, Math.ceil(p.w * base * V.dpr)), 5200);
    c.width = pxW;
    c.height = Math.max(1, Math.round(pxW * p.h / p.w));
    try { if (V.renderTask) V.renderTask.cancel(); } catch (e) { }
    const task = p.pdfPage.render({
      canvasContext: c.getContext('2d'),
      viewport: p.pdfPage.getViewport({ scale: c.width / p.w }),
    });
    V.renderTask = task;
    task.promise.catch(() => { });
  }

  /* ============================== 坐标换算 ============================== */

  function cssToPage(X, Y) {
    const p = cur(), base = fit() * V.view.scale;
    return { x: (X - V.cw / 2 - V.view.tx) / base + p.w / 2, y: (Y - V.ch / 2 - V.view.ty) / base + p.h / 2 };
  }

  function pageToCss(x, y) {
    const p = cur(), base = fit() * V.view.scale;
    return { x: V.cw / 2 + V.view.tx + (x - p.w / 2) * base, y: V.ch / 2 + V.view.ty + (y - p.h / 2) * base };
  }

  function cssPt(e) {
    return { x: e.clientX - V.rect.left, y: e.clientY - V.rect.top };
  }

  /* ============================== 绘制渲染 ============================== */

  function scheduleRedraw() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; redraw(); });
  }

  function redraw() {
    if (!V || !V.ictx) return;
    const ctx = V.ictx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, V.ink.width, V.ink.height);
    const p = cur();
    if (!p || !p.w) return;
    const base = fit() * V.view.scale;
    const ox = V.cw / 2 + V.view.tx - p.w * base / 2;
    const oy = V.ch / 2 + V.view.ty - p.h * base / 2;
    ctx.setTransform(V.dpr * base, 0, 0, V.dpr * base, V.dpr * ox, V.dpr * oy);

    const items = V.doc.strokes[p.pid] || [];
    for (const s of items) drawStroke(ctx, s, !!(V.sel && V.sel.ids.has(s.id)));
    if (V.live) drawStroke(ctx, V.live, false);

    // ---- 以下按 CSS 坐标绘制(选区/套索/橡皮光标/激光笔) ----
    ctx.setTransform(V.dpr, 0, 0, V.dpr, 0, 0);
    if (V.sel && V.sel.ids.size) drawSelBox(ctx);
    if (V.lasso && V.lasso.length > 1) {
      ctx.save();
      ctx.strokeStyle = '#111'; ctx.lineWidth = 1.6; ctx.setLineDash([7, 5]);
      ctx.beginPath();
      ctx.moveTo(V.lasso[0].x, V.lasso[0].y);
      for (let i = 1; i < V.lasso.length; i++) ctx.lineTo(V.lasso[i].x, V.lasso[i].y);
      ctx.closePath(); ctx.stroke();
      ctx.fillStyle = 'rgba(17,17,17,.09)'; ctx.fill();
      ctx.restore();
    }
    if (V.tool === 'eraser' && V.lastPointer && !V.mediaLock && !V.officeMode) {
      ctx.save();
      ctx.strokeStyle = '#111'; ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(V.lastPointer.x, V.lastPointer.y, V.cfg.eraser.width * k() * fit() * V.view.scale / 2, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
    if (V.laserPts && V.laserPts.length) {
      const now = performance.now();
      ctx.save();
      for (const q of V.laserPts) {
        const al = Math.max(0, 1 - (now - q.t) / 700);
        if (al <= 0) continue;
        ctx.globalAlpha = al;
        ctx.fillStyle = '#ef4444';
        ctx.shadowColor = 'rgba(239,68,68,.95)';
        ctx.shadowBlur = 14;
        ctx.beginPath();
        ctx.arc(q.x, q.y, 4.5 + 3.5 * al, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }

  function strokePath(ctx, pts) {
    ctx.beginPath();
    if (pts.length === 1) {
      ctx.arc(pts[0][0], pts[0][1], .6, 0, Math.PI * 2);
      return;
    }
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length - 1; i++) {
      ctx.quadraticCurveTo(pts[i][0], pts[i][1], (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2);
    }
    const L = pts[pts.length - 1];
    ctx.lineTo(L[0], L[1]);
  }

  function shapePath(ctx, s) {
    const a = s.pts[0], b = s.pts[1] || s.pts[0];
    ctx.beginPath();
    if (s.shape === 'line' || s.shape === 'arrow') {
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
      if (s.shape === 'arrow') {
        const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
        const h = Math.max(10, (s.width || 3) * 3.2);
        ctx.moveTo(b[0], b[1]);
        ctx.lineTo(b[0] - h * Math.cos(ang - 0.42), b[1] - h * Math.sin(ang - 0.42));
        ctx.moveTo(b[0], b[1]);
        ctx.lineTo(b[0] - h * Math.cos(ang + 0.42), b[1] - h * Math.sin(ang + 0.42));
      }
    } else if (s.shape === 'rect') {
      ctx.rect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
    } else if (s.shape === 'ellipse') {
      const cx = (a[0] + b[0]) / 2, cy = (a[1] + b[1]) / 2;
      ctx.ellipse(cx, cy, Math.abs(b[0] - a[0]) / 2, Math.abs(b[1] - a[1]) / 2, 0, 0, Math.PI * 2);
    } else if (s.shape === 'triangle') {
      const x1 = Math.min(a[0], b[0]), x2 = Math.max(a[0], b[0]);
      const y1 = Math.min(a[1], b[1]), y2 = Math.max(a[1], b[1]);
      ctx.moveTo((x1 + x2) / 2, y1); ctx.lineTo(x2, y2); ctx.lineTo(x1, y2); ctx.closePath();
    } else {
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
    }
  }

  function drawStroke(ctx, s, selected) {
    if (!s.pts || !s.pts.length) return;
    if (s.tool === 'text') {
      ctx.save();
      ctx.fillStyle = s.color;
      ctx.textBaseline = 'top';
      ctx.font = s.width + 'px ' + FONT_STACK;
      const lines = String(s.text || '').split('\n');
      for (let i = 0; i < lines.length; i++) {
        ctx.fillText(lines[i], s.pts[0][0], s.pts[0][1] + i * s.width * 1.3);
      }
      if (selected) {
        const bb = strokeBounds(s);
        ctx.strokeStyle = 'rgba(17,17,17,.9)';
        ctx.lineWidth = Math.max(1.5, 2 / (fit() * V.view.scale));
        ctx.setLineDash([5, 4]);
        ctx.strokeRect(bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]);
        ctx.setLineDash([]);
      }
      ctx.restore();
      return;
    }
    if (s.tool === 'shape') {
      ctx.save();
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      if (selected) {
        ctx.strokeStyle = 'rgba(17,17,17,.9)';
        ctx.lineWidth = (s.width || 4) + 6 / (fit() * V.view.scale);
        shapePath(ctx, s);
        ctx.stroke();
      }
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width;
      shapePath(ctx, s);
      ctx.stroke();
      ctx.restore();
      return;
    }
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (selected) {
      ctx.strokeStyle = 'rgba(17,17,17,.9)';
      ctx.lineWidth = (s.width || 4) + 6 / (fit() * V.view.scale);
      strokePath(ctx, s.pts);
      ctx.stroke();
    }
    if (s.tool === 'marker') ctx.globalAlpha = .42;
    ctx.strokeStyle = s.color;
    ctx.lineWidth = s.width;
    strokePath(ctx, s.pts);
    ctx.stroke();
    if (s.pts.length === 1) {
      ctx.fillStyle = s.color;
      ctx.beginPath();
      ctx.arc(s.pts[0][0], s.pts[0][1], (s.width || 2) / 2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  /* ---------- 元素包围盒 / 命中检测 ---------- */

  function strokeBounds(s) {
    if (s.tool === 'text') {
      const lines = String(s.text || '').split('\n');
      let w = 0;
      for (const L of lines) {
        let lw = 0;
        for (const ch of L) lw += ch.charCodeAt(0) > 0x2e7f ? s.width : s.width * .56;
        w = Math.max(w, lw);
      }
      const h = s.width * 1.3 * lines.length;
      return [s.pts[0][0], s.pts[0][1], s.pts[0][0] + w, s.pts[0][1] + h];
    }
    let x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
    const pad = (s.width || 4) / 2 + 2;
    for (const p of s.pts) {
      x1 = Math.min(x1, p[0] - pad); y1 = Math.min(y1, p[1] - pad);
      x2 = Math.max(x2, p[0] + pad); y2 = Math.max(y2, p[1] + pad);
    }
    return [x1, y1, x2, y2];
  }

  function shapeSegs(s) {
    const a = s.pts[0], b = s.pts[1] || s.pts[0], out = [];
    if (s.shape === 'ellipse') {
      const cx = (a[0] + b[0]) / 2, cy = (a[1] + b[1]) / 2;
      const rx = Math.abs(b[0] - a[0]) / 2 || .1, ry = Math.abs(b[1] - a[1]) / 2 || .1;
      let prev = null;
      for (let i = 0; i <= 24; i++) {
        const t = i / 24 * Math.PI * 2;
        const p = [cx + rx * Math.cos(t), cy + ry * Math.sin(t)];
        if (prev) out.push([prev, p]);
        prev = p;
      }
      return out;
    }
    if (s.shape === 'rect') {
      const x1 = Math.min(a[0], b[0]), x2 = Math.max(a[0], b[0]);
      const y1 = Math.min(a[1], b[1]), y2 = Math.max(a[1], b[1]);
      return [[[x1, y1], [x2, y1]], [[x2, y1], [x2, y2]], [[x2, y2], [x1, y2]], [[x1, y2], [x1, y1]]];
    }
    if (s.shape === 'triangle') {
      const x1 = Math.min(a[0], b[0]), x2 = Math.max(a[0], b[0]);
      const y1 = Math.min(a[1], b[1]), y2 = Math.max(a[1], b[1]);
      const tp = [(x1 + x2) / 2, y1], br = [x2, y2], bl = [x1, y2];
      return [[tp, br], [br, bl], [bl, tp]];
    }
    return [[a, b]];
  }

  function segDist(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const l2 = dx * dx + dy * dy;
    if (!l2) return Math.hypot(p[0] - a[0], p[1] - a[1]);
    let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2;
    t = clamp(t, 0, 1);
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
  }

  /* ============================== 指针事件 ============================== */

  function bindStage() {
    const st = V.stage;
    st.style.touchAction = 'none';
    st.addEventListener('pointerdown', onDown);
    st.addEventListener('pointermove', onMove);
    st.addEventListener('pointerup', onUp);
    st.addEventListener('pointercancel', onUp);
    st.addEventListener('pointerleave', () => { if (V.tool === 'eraser') { V.lastPointer = null; scheduleRedraw(); } });
    st.addEventListener('dblclick', e => {
      if (V.tool === 'select' && !V.officeMode) {
        const pt = cssPt(e);
        const p = cur();
        for (const s of (V.doc.strokes[p.pid] || [])) {
          if (s.tool !== 'text') continue;
          const bb = strokeBounds(s);
          const a = pageToCss(bb[0], bb[1]), b = pageToCss(bb[2], bb[3]);
          if (pt.x >= a.x - 6 && pt.x <= b.x + 6 && pt.y >= a.y - 6 && pt.y <= b.y + 6) {
            const txt = s.text;
            const arr = V.doc.strokes[p.pid];
            arr.splice(arr.indexOf(s), 1);
            act({ t: 'del', pid: p.pid, items: [s] });
            openTextEdit(pageToCss(s.pts[0][0], s.pts[0][1]), txt, s);
            return;
          }
        }
      }
    });
    st.addEventListener('wheel', e => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        const pt = { x: e.clientX - V.rect.left, y: e.clientY - V.rect.top };
        zoomAt(pt, e.deltaY < 0 ? 1.12 : 1 / 1.12);
      } else {
        V.view.tx -= e.deltaX; V.view.ty -= e.deltaY;
        applyView();
      }
    }, { passive: false });
  }

  function zoomAt(pt, factor) {
    const ns = clamp(V.view.scale * factor, .25, 8);
    const kk = ns / V.view.scale;
    V.view.tx = pt.x - V.cw / 2 - (pt.x - V.cw / 2 - V.view.tx) * kk;
    V.view.ty = pt.y - V.ch / 2 - (pt.y - V.ch / 2 - V.view.ty) * kk;
    V.view.scale = ns;
    applyView();
  }

  function onDown(e) {
    if (!V || V.destroyed || V.mediaLock) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    closePopover();
    V.stage.setPointerCapture(e.pointerId);
    V.pointers.set(e.pointerId, cssPt(e));
    if (V.blockGestures) return;
    if (V.pointers.size === 2) { startPinch(); return; }
    if (V.pointers.size > 2) return;
    const pt = cssPt(e);
    if (V.tool === 'pen' || V.tool === 'marker') {
      const pp = cssToPage(pt.x, pt.y);
      V.live = {
        id: uid(), tool: V.tool, color: V.cfg[V.tool].color,
        width: V.cfg[V.tool].width * k(), pts: [[pp.x, pp.y]],
      };
    } else if (V.tool === 'shape') {
      const pp = cssToPage(pt.x, pt.y);
      V.live = {
        id: uid(), tool: 'shape', shape: V.cfg.shape.type,
        color: V.cfg.shape.color, width: V.cfg.shape.width * k(),
        pts: [[pp.x, pp.y], [pp.x, pp.y]],
      };
    } else if (V.tool === 'text') {
      commitTextEdit();
      openTextEdit(pt);
    } else if (V.tool === 'laser') {
      V.laserActive = true;
      V.laserPts.push({ x: pt.x, y: pt.y, t: performance.now() });
      if (!V.laserT) V.laserT = requestAnimationFrame(laserTick);
    } else if (V.tool === 'eraser') {
      V.erased = [];
      V.lastPointer = pt;
      eraseAt(pt, pt);
    } else if (V.tool === 'select') {
      startSelect(pt);
    }
  }

  function snappedShapePoint(p0, p1) {
    const dx = p1.x - p0.x, dy = p1.y - p0.y;
    const len = Math.hypot(dx, dy);
    if (len < 4) return [p1.x, p1.y];
    const ang = Math.atan2(dy, dx);
    const step = Math.PI / 4;
    const target = Math.round(ang / step) * step;
    if (Math.abs(ang - target) < 0.10) {
      return [p0.x + Math.cos(target) * len, p0.y + Math.sin(target) * len];
    }
    return [p1.x, p1.y];
  }

  function onMove(e) {
    if (!V || V.destroyed || !V.pointers.has(e.pointerId) || V.mediaLock) return;
    const prev = V.pointers.get(e.pointerId);
    const pt = cssPt(e);
    V.pointers.set(e.pointerId, pt);
    if (V.pinch && V.pointers.size >= 2) { onPinchMove(); return; }
    if (V.blockGestures) return;
    if (V.tool === 'eraser') V.lastPointer = pt;
    if (V.tool === 'laser') {
      V.laserPts.push({ x: pt.x, y: pt.y, t: performance.now() });
      if (!V.laserT) V.laserT = requestAnimationFrame(laserTick);
      return;
    }
    if (V.live) {
      const pp = cssToPage(pt.x, pt.y);
      if (V.live.tool === 'shape') {
        const st = V.live.pts[0];
        V.live.pts[1] = (V.live.shape === 'line' || V.live.shape === 'arrow')
          ? snappedShapePoint({ x: st[0], y: st[1] }, pp) : [pp.x, pp.y];
      } else {
        const lastPt = V.live.pts[V.live.pts.length - 1];
        const lc = pageToCss(lastPt[0], lastPt[1]);
        if (dist(lc, pt) > 1.5) V.live.pts.push([pp.x, pp.y]);
      }
      scheduleRedraw();
    } else if (V.erased) {
      eraseAt(prev, pt);
    } else if (V.lasso) {
      V.lasso.push(pt);
      scheduleRedraw();
    } else if (V.moveGesture) {
      moveSel(pt);
    } else if (V.scaleGesture) {
      scaleSel(pt);
    } else if (V.tool === 'eraser') {
      scheduleRedraw();
    }
  }

  function onUp(e) {
    if (!V || V.destroyed || V.mediaLock) return;
    const wasPinch = !!V.pinch;
    V.pointers.delete(e.pointerId);
    if (V.pinch && V.pointers.size < 2) { V.pinch = null; V.blockGestures = true; }
    if (V.pointers.size === 0) V.blockGestures = false;
    if (wasPinch || V.blockGestures) return;
    const pt = cssPt(e);
    if (V.live) {
      if (V.live.tool === 'shape') {
        const a = pageToCss(V.live.pts[0][0], V.live.pts[0][1]);
        const b = pageToCss(V.live.pts[1][0], V.live.pts[1][1]);
        if (dist(a, b) < 5) { V.live = null; scheduleRedraw(); return; }
        strokesOf(cur().pid).push(V.live);
        act({ t: 'add', pid: cur().pid, items: [V.live] });
        V.live = null;
      } else {
        if (V.live.pts.length === 1) V.live.pts.push([V.live.pts[0][0] + .01, V.live.pts[0][1] + .01]);
        strokesOf(cur().pid).push(V.live);
        act({ t: 'add', pid: cur().pid, items: [V.live] });
        V.live = null;
      }
    } else if (V.erased) {
      if (V.cfg.eraser.mode === 'pixel' && V.px) {
        if (V.px.origs.length) {
          if (V.px.pieces.length) act({ t: 'split', pid: cur().pid, items: V.px.origs, pieces: V.px.pieces });
          else act({ t: 'del', pid: cur().pid, items: V.px.origs });
        }
        V.px = null;
      } else if (V.erased.length) {
        act({ t: 'del', pid: cur().pid, items: V.erased });
      }
      V.erased = null;
    } else if (V.lasso || V.moveGesture || V.scaleGesture) {
      endSelect(pt);
    } else if (V.tool === 'laser') {
      V.laserActive = false;
    }
  }

  /* ---------- 激光笔动画 ---------- */
  function laserTick() {
    if (!V || V.destroyed) { if (V) V.laserT = 0; return; }
    const now = performance.now();
    V.laserPts = (V.laserPts || []).filter(q => now - q.t < 750);
    if (V.laserPts.length || V.laserActive) {
      scheduleRedraw();
      V.laserT = requestAnimationFrame(laserTick);
    } else {
      V.laserT = 0;
      scheduleRedraw();
    }
  }

  /* ---------- 双指缩放 ---------- */
  function startPinch() {
    cancelGesture();
    const pts = Array.from(V.pointers.values());
    V.pinch = {
      d0: dist(pts[0], pts[1]) || 1, s0: V.view.scale,
      m0: { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 },
      t0: { x: V.view.tx, y: V.view.ty },
    };
  }

  function onPinchMove() {
    const pts = Array.from(V.pointers.values());
    if (pts.length < 2 || !V.pinch) return;
    const d = dist(pts[0], pts[1]) || 1;
    const ns = clamp(V.pinch.s0 * d / V.pinch.d0, .25, 8);
    const m = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
    const kk = ns / V.pinch.s0;
    V.view.tx = m.x - V.cw / 2 - (V.pinch.m0.x - V.cw / 2 - V.pinch.t0.x) * kk;
    V.view.ty = m.y - V.ch / 2 - (V.pinch.m0.y - V.ch / 2 - V.pinch.t0.y) * kk;
    V.view.scale = ns;
    applyView();
  }

  function cancelGesture() {
    if (V.live) V.live = null;
    if (V.lasso) V.lasso = null;
    if (V.moveGesture) { restoreSnap(V.moveGesture.before); V.moveGesture = null; }
    if (V.scaleGesture) { restoreSnap(V.scaleGesture.before); V.scaleGesture = null; }
    if (V.erased && V.erased.length) {
      const arr = strokesOf(cur().pid);
      V.erased.forEach(s => arr.push(s));
      V.erased = [];
    }
    if (V.px && V.px.origs.length) {
      const pidset = new Set((V.px.pieces || []).map(s => s.id));
      const arr = strokesOf(cur().pid).filter(s => !pidset.has(s.id));
      V.px.origs.forEach(s => arr.push(s));
      V.doc.strokes[cur().pid] = arr;
      V.px = null;
    }
    scheduleRedraw();
  }

  /* ---------- 橡皮 ---------- */
  /* ---------- 橡皮 (v1.24 重写: 命中半径 = 光圈半径, 所见即所得) ---------- */
  function eraserR() { return V.cfg.eraser.width * k() / 2; }

  function ptRectDist(px, py, bb) {
    const dx = Math.max(bb[0] - px, 0, px - bb[2]);
    const dy = Math.max(bb[1] - py, 0, py - bb[3]);
    return Math.hypot(dx, dy);
  }
  function segRectDist(a, b, bb) {   // 线段[a,b]到矩形的近似距离(两端+中点采样)
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
    return Math.min(ptRectDist(a[0], a[1], bb), ptRectDist(b[0], b[1], bb), ptRectDist(mx, my, bb));
  }
  function segsegDist(p1, p2, p3, p4) {   // 两线段距离(近似: 交叉采样)
    return Math.min(
      segDist(p1, p3, p4), segDist(p2, p3, p4),
      segDist(p3, p1, p2), segDist(p4, p1, p2));
  }

  function strokeHitEraser(s, a, b, r) {   // a,b: 擦除路径段两端(页面坐标)
    const half = (s.width || 4) / 2;
    if (s.tool === 'text') {
      return segRectDist([a.x, a.y], [b.x, b.y], strokeBounds(s)) <= r;
    }
    if (s.tool === 'shape') {
      for (const seg of shapeSegs(s)) {
        if (segsegDist([a.x, a.y], [b.x, b.y], seg[0], seg[1]) <= r + half) return true;
      }
      return false;
    }
    if (s.pts && s.pts.length) {
      if (s.pts.length === 1) return segDist(s.pts[0], [a.x, a.y], [b.x, b.y]) <= r + half;
      for (let j = 0; j < s.pts.length - 1; j++) {
        if (segsegDist([a.x, a.y], [b.x, b.y], s.pts[j], s.pts[j + 1]) <= r + half) return true;
      }
    }
    return false;
  }

  function eraseAt(prevCss, curCss) {
    const a = cssToPage(prevCss.x, prevCss.y), b = cssToPage(curCss.x, curCss.y);
    const r = eraserR();
    if (V.cfg.eraser.mode === 'pixel') { erasePixelSeg(a, b, r); return; }
    const p = cur();
    const arr = V.doc.strokes[p.pid] || [];
    const keep = [], hitList = [];
    for (const s of arr) (strokeHitEraser(s, a, b, r) ? hitList : keep).push(s);
    if (hitList.length) {
      for (const s of hitList) V.erased.push(s);
      V.doc.strokes[p.pid] = keep;
      cleanupSel();
      scheduleRedraw();
    }
  }

  /* ---------- 像素橡皮: 沿擦过路径切断笔迹, 半径与光圈一致 ---------- */
  function erasePixelSeg(a, b, r) {
    window.__flaDbg = window.__flaDbg || [];
    const pid = cur().pid;
    if (!V.px) V.px = { base: new Map(), origs: [], pieces: [], segs: [] };
    const px = V.px;
    px.segs.push([a.x, a.y, b.x, b.y]);
    const arr = V.doc.strokes[pid] || [];
    const pieceIds = new Set(px.pieces.map(s => s.id));
    const out = [], touched = [];
    for (const s of arr) {
      if (pieceIds.has(s.id)) continue;          // 本次扫描产生的碎片, 稍后由基线统一重算
      (strokeHitEraser(s, a, b, r) ? touched : out).push(s);
    }
    for (const s of touched) if (!px.base.has(s.id)) { px.base.set(s.id, 1); px.origs.push(deep(s)); }
    if (!touched.length && !px.origs.length) return;
    const pieces = [];
    for (const baseS of px.origs) {
      if (baseS.tool === 'text' || baseS.tool === 'shape') continue;   // 文本/图形: 整体删除
      const half = (baseS.width || 4) / 2;
      const runs = []; let run = [];
      for (const pt of baseS.pts) {
        let cut = false;
        for (const g of px.segs) {
          if (segDist(pt, [g[0], g[1]], [g[2], g[3]]) <= r + half) { cut = true; break; }
        }
        if (cut) { if (run.length > 1) runs.push(run); run = []; } else run.push(pt);
      }
      if (run.length > 1) runs.push(run);
      for (const rpts of runs) {
        pieces.push({ id: uid(), tool: baseS.tool, color: baseS.color, width: baseS.width,
          pts: rpts.map(q => [q[0], q[1]]) });
      }
    }
    px.pieces = pieces;
    V.doc.strokes[pid] = out.concat(pieces);
    cleanupSel();
    scheduleRedraw();
  }
  function cleanupSel() {
    if (!V.sel) return;
    const live = new Set((V.doc.strokes[cur().pid] || []).map(s => s.id));
    for (const id of Array.from(V.sel.ids)) if (!live.has(id)) V.sel.ids.delete(id);
    if (!V.sel.ids.size) clearSelection(); else recomputeSelBox();
  }

  /* ---------- 文本框 ---------- */
  function openTextEdit(ptCss, initialText, oldStroke) {
    commitTextEdit(true);
    const base = fit() * V.view.scale;
    const fontSize = (oldStroke ? oldStroke.width : V.cfg.text.size * k()) * base;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'tedit';
    input.value = initialText || '';
    input.style.left = ptCss.x + 'px';
    input.style.top = (ptCss.y - 4) + 'px';
    input.style.fontSize = Math.max(12, fontSize) + 'px';
    input.style.color = oldStroke ? oldStroke.color : V.cfg.text.color;
    V.stage.appendChild(input);
    V.textEdit = { el: input, cssPt: ptCss, oldStroke: oldStroke || null };
    setTimeout(() => { input.focus(); input.select(); }, 30);
    input.onkeydown = ev => {
      ev.stopPropagation();
      if (ev.key === 'Enter') { ev.preventDefault(); commitTextEdit(); }
      else if (ev.key === 'Escape') { ev.preventDefault(); cancelTextEdit(); }
    };
    input.onblur = () => commitTextEdit();
  }

  function cancelTextEdit() {
    if (!V.textEdit) return;
    const el = V.textEdit.el;
    V.textEdit = null;
    el.remove();
  }

  function commitTextEdit(silent) {
    if (!V.textEdit) return;
    const te = V.textEdit;
    V.textEdit = null;
    const val = te.el.value;
    te.el.remove();
    const txt = val.replace(/\s+$/, '');
    if (!txt) return;
    const pp = cssToPage(te.cssPt.x + 4, te.cssPt.y + 2);
    const stroke = {
      id: te.oldStroke ? te.oldStroke.id : uid(),
      tool: 'text', color: te.oldStroke ? te.oldStroke.color : V.cfg.text.color,
      width: te.oldStroke ? te.oldStroke.width : V.cfg.text.size * k(),
      pts: [[pp.x, pp.y]], text: txt,
    };
    strokesOf(cur().pid).push(stroke);
    act({ t: 'add', pid: cur().pid, items: [stroke] });
    if (!silent) scheduleRedraw();
  }

  /* ---------- 选择 / 圈选 ---------- */
  function startSelect(pt) {
    if (V.sel && V.sel.ids.size) {
      const h = handleHit(pt);
      if (h) {
        const box = selBoxCss();
        const anchorP = h === 'nw' ? [box.x + box.w, box.y + box.h]
          : h === 'ne' ? [box.x, box.y + box.h]
            : h === 'se' ? [box.x, box.y] : [box.x + box.w, box.y];
        V.scaleGesture = { corner: h, start: pt, anchor: anchorP, before: snapSel() };
        return;
      }
      if (inBox(pt, selBoxCss())) {
        V.moveGesture = { start: pt, before: snapSel(), moved: false };
        return;
      }
      clearSelection();
    }
    V.lasso = [pt];
  }

  function endSelect(pt) {
    if (V.moveGesture) {
      if (V.moveGesture.moved) act(xformAction(V.moveGesture.before));
      V.moveGesture = null;
      return;
    }
    if (V.scaleGesture) {
      act(xformAction(V.scaleGesture.before));
      V.scaleGesture = null;
      return;
    }
    if (V.lasso) {
      const poly = V.lasso;
      V.lasso = null;
      if (poly.length > 2) selectByPoly(poly); else clearSelection();
      scheduleRedraw();
    }
  }

  function selectByPoly(polyCss) {
    const p = cur();
    const poly = polyCss.map(q => { const r = cssToPage(q.x, q.y); return [r.x, r.y]; });
    const ids = new Set();
    for (const s of (V.doc.strokes[p.pid] || [])) {
      const bb = strokeBounds(s);
      const corners = [[bb[0], bb[1]], [bb[2], bb[1]], [bb[0], bb[3]], [bb[2], bb[3]]];
      let inside = false;
      for (const c of corners) { if (inPoly(c, poly)) { inside = true; break; } }
      if (!inside) {
        for (const pt of s.pts) { if (inPoly(pt, poly)) { inside = true; break; } }
      }
      if (inside) ids.add(s.id);
    }
    if (!ids.size) { clearSelection(); return; }
    V.sel = { ids };
    recomputeSelBox();
    updateSelBar();
    scheduleRedraw();
  }

  function inPoly(pt, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
      if (((yi > pt[1]) !== (yj > pt[1])) &&
        (pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
  }

  function selStrokes() {
    const p = cur();
    return (V.doc.strokes[p.pid] || []).filter(s => V.sel && V.sel.ids.has(s.id));
  }

  function recomputeSelBox() {
    if (!V.sel || !V.sel.ids.size) { V.sel = null; return; }
    let x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
    for (const s of selStrokes()) {
      const bb = strokeBounds(s);
      x1 = Math.min(x1, bb[0]); y1 = Math.min(y1, bb[1]);
      x2 = Math.max(x2, bb[2]); y2 = Math.max(y2, bb[3]);
    }
    if (x1 > x2) { V.sel = null; return; }
    V.sel.box = { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
  }

  function selBoxCss() {
    const b = V.sel.box;
    const a = pageToCss(b.x, b.y), c = pageToCss(b.x + b.w, b.y + b.h);
    return { x: Math.min(a.x, c.x), y: Math.min(a.y, c.y), w: Math.abs(c.x - a.x), h: Math.abs(c.y - a.y) };
  }

  function snapSel() {
    const m = {};
    for (const s of selStrokes()) m[s.id] = { pts: deep(s.pts), w: s.width, text: s.text };
    return m;
  }

  function restoreSnap(snap) {
    const p = cur();
    for (const s of (V.doc.strokes[p.pid] || [])) {
      if (snap[s.id]) { s.pts = deep(snap[s.id].pts); s.width = snap[s.id].w; }
    }
    recomputeSelBox();
    updateSelBar();
    scheduleRedraw();
  }

  function moveSel(pt) {
    const g = V.moveGesture;
    const d1 = cssToPage(g.start.x, g.start.y), d2 = cssToPage(pt.x, pt.y);
    const dx = d2.x - d1.x, dy = d2.y - d1.y;
    if (dist(g.start, pt) > 3) g.moved = true;
    const p = cur();
    for (const s of (V.doc.strokes[p.pid] || [])) {
      const b = g.before[s.id];
      if (b) s.pts = b.pts.map(q => [q[0] + dx, q[1] + dy]);
    }
    recomputeSelBox();
    scheduleRedraw();
    updateSelBar();
  }

  function scaleSel(pt) {
    const g = V.scaleGesture;
    const box = selBoxCss();
    const anchorCss = g.corner === 'nw' ? { x: box.x + box.w, y: box.y + box.h }
      : g.corner === 'ne' ? { x: box.x, y: box.y + box.h }
        : g.corner === 'se' ? { x: box.x, y: box.y }
          : { x: box.x + box.w, y: box.y };
    const f = clamp(dist(pt, anchorCss) / (dist(g.start, anchorCss) || 1), .2, 6);
    const p = cur();
    for (const s of (V.doc.strokes[p.pid] || [])) {
      const b = g.before[s.id];
      if (b) {
        s.pts = b.pts.map(q => [g.anchor[0] + (q[0] - g.anchor[0]) * f, g.anchor[1] + (q[1] - g.anchor[1]) * f]);
        s.width = b.w * f;
      }
    }
    recomputeSelBox();
    scheduleRedraw();
    updateSelBar();
  }

  function xformAction(before) {
    const p = cur();
    const items = [];
    for (const s of (V.doc.strokes[p.pid] || [])) {
      if (before[s.id]) items.push({ id: s.id, before: before[s.id], after: { pts: deep(s.pts), w: s.width } });
    }
    return { t: 'xform', pid: p.pid, items };
  }

  function duplicateSel() {
    if (!V.sel) return;
    const pid = cur().pid;
    const off = 28 * k();
    const clones = selStrokes().map(s => ({
      id: uid(), tool: s.tool, shape: s.shape, text: s.text,
      color: s.color, width: s.width,
      pts: s.pts.map(q => [q[0] + off, q[1] + off]),
    }));
    strokesOf(pid).push(...clones);
    act({ t: 'add', pid, items: clones });
    V.sel = { ids: new Set(clones.map(c => c.id)) };
    recomputeSelBox();
    updateSelBar();
    scheduleRedraw();
  }

  function deleteSel() {
    if (!V.sel) return;
    const pid = cur().pid;
    const items = selStrokes();
    V.doc.strokes[pid] = (V.doc.strokes[pid] || []).filter(s => !V.sel.ids.has(s.id));
    act({ t: 'del', pid, items });
    clearSelection();
  }

  function clearSelection() {
    V.sel = null;
    if (V.selbar) V.selbar.classList.add('hidden');
    scheduleRedraw();
  }

  function inBox(pt, b) {
    return pt.x >= b.x - 4 && pt.x <= b.x + b.w + 4 && pt.y >= b.y - 4 && pt.y <= b.y + b.h + 4;
  }

  function handleHit(pt) {
    if (!V.sel || !V.sel.box) return null;
    const b = selBoxCss();
    const cs = [
      ['nw', b.x, b.y], ['ne', b.x + b.w, b.y],
      ['sw', b.x, b.y + b.h], ['se', b.x + b.w, b.y + b.h],
    ];
    for (const c of cs) if (dist(pt, { x: c[1], y: c[2] }) < 18) return c[0];
    return null;
  }

  function drawSelBox(ctx) {
    if (!V.sel || !V.sel.box) return;
    const b = selBoxCss();
    ctx.save();
    ctx.strokeStyle = '#111'; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    ctx.setLineDash([]);
    ctx.fillStyle = '#fff';
    for (const c of [[b.x, b.y], [b.x + b.w, b.y], [b.x, b.y + b.h], [b.x + b.w, b.y + b.h]]) {
      ctx.beginPath(); ctx.arc(c[0], c[1], 6, 0, Math.PI * 2);
      ctx.fill(); ctx.stroke();
    }
    ctx.restore();
  }

  function updateSelBar() {
    if (!V.selbar) return;
    if (!V.sel || !V.sel.ids.size || !V.sel.box) { V.selbar.classList.add('hidden'); return; }
    const b = selBoxCss();
    V.selbar.classList.remove('hidden');
    const top = b.y > 64 ? b.y - 52 : b.y + b.h + 10;
    V.selbar.style.left = clamp(b.x, 8, Math.max(8, V.cw - 260)) + 'px';
    V.selbar.style.top = clamp(top, 8, Math.max(8, V.ch - 60)) + 'px';
  }

  /* ============================== 撤销 / 重做 ============================== */

  function act(a) {
    V.undoStack.push(a);
    if (V.undoStack.length > 300) V.undoStack.shift();
    V.redoStack.length = 0;
    updateHistoryUI();
    saveSoon();
  }

  function removeIds(pid, ids) {
    const set = new Set(ids);
    V.doc.strokes[pid] = (V.doc.strokes[pid] || []).filter(s => !set.has(s.id));
  }

  function addItems(pid, items) {
    const arr = V.doc.strokes[pid] || (V.doc.strokes[pid] = []);
    for (const it of items) if (!arr.some(s => s.id === it.id)) arr.push(it);
  }

  function invertAction(a) {
    if (a.t === 'add') removeIds(a.pid, a.items.map(i => i.id));
    else if (a.t === 'del' || a.t === 'clear') addItems(a.pid, a.items);
    else if (a.t === 'split') { removeIds(a.pid, (a.pieces || []).map(i => i.id)); addItems(a.pid, a.items); }
    else if (a.t === 'xform') {
      const arr = V.doc.strokes[a.pid] || [];
      for (const it of a.items) { const s = arr.find(x => x.id === it.id); if (s) { s.pts = deep(it.before.pts); s.width = it.before.w; } }
    } else if (a.t === 'addPage') {
      V.doc.pages.splice(a.index, 1);
      a.stash = V.doc.strokes[a.pid] || [];
      delete V.doc.strokes[a.pid];
      if (V.doc.cur >= V.doc.pages.length) V.doc.cur = V.doc.pages.length - 1;
    }
  }

  function applyAction(a) {
    if (a.t === 'add') addItems(a.pid, a.items);
    else if (a.t === 'del' || a.t === 'clear') removeIds(a.pid, a.items.map(i => i.id));
    else if (a.t === 'split') { removeIds(a.pid, a.items.map(i => i.id)); addItems(a.pid, a.pieces || []); }
    else if (a.t === 'xform') {
      const arr = V.doc.strokes[a.pid] || [];
      for (const it of a.items) { const s = arr.find(x => x.id === it.id); if (s) { s.pts = deep(it.after.pts); s.width = it.after.w; } }
    } else if (a.t === 'addPage') {
      V.doc.pages.splice(a.index, 0, { t: 'blank', pid: a.pid, w: 1280, h: 720, bg: 'w' });
      V.doc.strokes[a.pid] = a.stash || [];
    }
  }

  function doUndo() {
    const a = V.undoStack.pop();
    if (!a) return;
    invertAction(a);
    V.redoStack.push(a);
    afterHistory();
  }

  function doRedo() {
    const a = V.redoStack.pop();
    if (!a) return;
    applyAction(a);
    V.undoStack.push(a);
    afterHistory();
  }

  function afterHistory() {
    clearSelection();
    updateHistoryUI();
    updatePageUI();
    scheduleRedraw();
    saveSoon();
    if (!V.officeMode && V.doc.pages[V.doc.cur] && V.doc.pages[V.doc.cur].t === 'blank') { buildBg(); layout(); }
  }

  function updateHistoryUI() {
    const u = document.getElementById('tb-undo'), r = document.getElementById('tb-redo');
    if (u) u.disabled = !V.undoStack.length;
    if (r) r.disabled = !V.redoStack.length;
  }

  /* ============================== 翻页 / 加页 / 底色 ============================== */

  function updatePageUI() {
    const el = document.getElementById('pgind');
    if (el && V.doc) el.textContent = V.officeMode ? '放映' : (V.doc.cur + 1) + ' / ' + V.doc.pages.length;
    ['tb-prev', 'tb-next', 'tb-addpage', 'tb-shot', 'tb-bg'].forEach(id => {
      const b = document.getElementById(id);
      if (b) b.disabled = !!V.officeMode;
    });
  }

  function addPage() {
    if (V.officeMode) { toast('放映模式下不可加页，请切回静态模式'); return; }
    V.pidSeq++;
    const pid = 'b' + V.pidSeq;
    const curBg = (cur() && cur().bg) || 'w';
    V.doc.pages.splice(V.doc.cur + 1, 0, { t: 'blank', pid, w: 1280, h: 720, bg: curBg });
    V.doc.strokes[pid] = [];
    act({ t: 'addPage', index: V.doc.cur + 1, pid, stash: [] });
    gotoPage(V.doc.cur + 1);
  }

  function cycleBg() {
    if (V.officeMode) return;
    const p = cur();
    if (!p || p.t !== 'blank') { toast('仅空白白板页可切换底色'); return; }
    p.bg = p.bg === 'w' ? 'k' : p.bg === 'k' ? 'g' : 'w';
    buildBg();
    saveSoon();
    toast(p.bg === 'w' ? '白底' : p.bg === 'k' ? '黑底（黑板）' : '绿底（黑板）');
  }

  /* ============================== OnlyOffice 动画放映 ============================== */

  function loadScript(src) {
    return new Promise((res, rej) => {
      if (V._loadedScripts[src]) return res();
      const s = document.createElement('script');
      s.src = src;
      s.onload = () => { V._loadedScripts[src] = 1; res(); };
      s.onerror = () => rej(new Error('无法加载放映组件'));
      document.head.appendChild(s);
    });
  }

  async function toggleOffice() {
    if (V.officeMode) { exitOffice(); return; }
    V.officeMode = true;
    V.ooPage = { t: 'oo', pid: 'oo', w: 1280, h: 720 };
    V.mediaLock = false;
    clearSelection(); V.live = null; V.lasso = null;
    commitTextEdit(true);
    V.bgGen++;
    V.bgWrap.innerHTML = '';
    V.bgWrap.classList.remove('blank-page', 'inf', 'media-page', 'bg-k', 'bg-g');
    V.bgWrap.classList.add('oo-page');
    const holder = document.createElement('div');
    holder.id = 'oo-holder';
    holder.style.cssText = 'width:100%;height:100%;pointer-events:none;overflow:hidden;background:#fff;';
    V.bgWrap.appendChild(holder);
    V.ooHolder = holder;
    V.mediaEl = null;
    V.bgCanvas = null;
    updatePageUI();
    layout();
    const btn = document.getElementById('tb-oo');
    if (btn) btn.classList.add('on');
    try {
      const chk = await API.get('/api/files/' + V.id + '/onlyoffice/verify');
      if (chk && chk.ok === false) {
        toast('放映暂不可用：' + chk.detail, 'err');
        exitOffice();
        return;
      }
      const r = await API.get('/api/files/' + V.id + '/onlyoffice/config');
      await loadScript(r.api_url);
      if (!V || !V.officeMode || V.destroyed) return;
      V.oo = new window.DocsAPI.DocEditor('oo-holder', r.config);
      toast('已进入动画放映模式，批注工具仍可用；点 🔒 可操作放映控件');
    } catch (e) {
      toast('放映加载失败：' + e.message, 'err');
      exitOffice();
    }
  }

  function exitOffice() {
    V.officeMode = false;
    try { if (V.oo) V.oo.destroyEditor(); } catch (e) { }
    V.oo = null; V.ooHolder = null;
    V.bgWrap.classList.remove('oo-page');
    const btn = document.getElementById('tb-oo');
    if (btn) btn.classList.remove('on');
    if (V.stage) V.stage.style.pointerEvents = 'auto';
    if (V.lockBtn) { V.mediaLock = false; V.lockBtn.innerHTML = UI.icon('unlock', 20); V.lockBtn.classList.remove('on'); }
    updatePageUI();
    if (V.doc) { buildBg(); layout(); }
  }

  /* ============================== 截图 ============================== */

  async function screenshot(mode) {
    if (V.officeMode) { toast('放映模式下暂不支持截图，请切回静态模式'); return; }
    const p = cur();
    let SRC = null;
    if (p.t === 'blank') {                    // 无限画布: 截取当前可视区域
      const a = cssToPage(0, 0), b = cssToPage(V.cw, V.ch);
      SRC = { x1: Math.min(a.x, b.x), y1: Math.min(a.y, b.y),
              w: Math.abs(b.x - a.x) || 1280, h: Math.abs(b.y - a.y) || 720 };
    }
    const kx = 1600 / (SRC ? SRC.w : p.w);
    const c = document.createElement('canvas');
    c.width = Math.round((SRC ? SRC.w : p.w) * kx);
    c.height = Math.round((SRC ? SRC.h : p.h) * kx);
    const x = c.getContext('2d');
    const bgColor = p.bg === 'k' ? '#15181d' : p.bg === 'g' ? '#1c3b2d' : '#ffffff';
    x.fillStyle = bgColor;
    x.fillRect(0, 0, c.width, c.height);
    try {
      if (p.t === 'pdf' || p.t === 'image') {
        if (V.bgCanvas) x.drawImage(V.bgCanvas, 0, 0, c.width, c.height);
      } else if (p.t === 'media') {
        if (V.meta.kind === 'video' && V.mediaEl && V.mediaEl.videoWidth) {
          const vw = V.mediaEl.videoWidth, vh = V.mediaEl.videoHeight;
          const sc = Math.min(c.width / vw, c.height / vh);
          const dw = vw * sc, dh = vh * sc;
          x.drawImage(V.mediaEl, (c.width - dw) / 2, (c.height - dh) / 2, dw, dh);
        } else {
          x.fillStyle = '#f1f5f9'; x.fillRect(0, 0, c.width, c.height);
          x.fillStyle = '#94a3b8'; x.font = Math.round(c.height / 6) + 'px sans-serif';
          x.textAlign = 'center'; x.textBaseline = 'middle';
          x.fillText('♪', c.width / 2, c.height / 2);
        }
      }
    } catch (e) { }
    x.setTransform(kx, 0, 0, kx, (SRC ? -SRC.x1 : 0) * kx, (SRC ? -SRC.y1 : 0) * kx);
    for (const s of (V.doc.strokes[p.pid] || [])) drawStroke(x, s, false);
    const base = (V.meta.name || 'screenshot').replace(/\.[^.]+$/, '').slice(0, 24) || 'screenshot';
    const fname = base + '_第' + (V.doc.cur + 1) + '页.png';
    c.toBlob(blob => {
      if (!blob) { toast('截图生成失败', 'err'); return; }
      if (mode === 'download') {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        toast('截图已下载');
      } else {
        const form = new FormData();
        form.append('file', blob, fname);
        API.upload('/api/files/upload', form).then(() => toast('截图已保存到「我的课件」')).catch(e => toast(e.message, 'err'));
      }
    }, 'image/png');
  }

  /* ============================== 倒计时 / 全屏 ============================== */

  function toggleTimer() {
    if (V.timerEl) { stopTimer(); return; }
    const m = UI.modal({ title: '课堂倒计时', width: '360px' });
    m.body.innerHTML =
      '<div class="pop-row" style="justify-content:center">' +
      '<span class="pop-lb">分钟</span><input id="tmin" type="number" min="0" max="180" value="5" style="width:86px">' +
      '<span class="pop-lb" style="margin-left:14px">秒</span><input id="tsec" type="number" min="0" max="59" value="0" style="width:86px"></div>';
    m.foot.innerHTML = '<button class="btn" id="tc">取消</button><button class="btn primary" id="tg">开始</button>';
    m.foot.querySelector('#tc').onclick = m.close;
    m.foot.querySelector('#tg').onclick = () => {
      const total = (parseInt(m.body.querySelector('#tmin').value, 10) || 0) * 60 +
        (parseInt(m.body.querySelector('#tsec').value, 10) || 0);
      m.close();
      if (total > 0) startTimer(total);
      else toast('请输入时间');
    };
  }

  function startTimer(total) {
    stopTimer();
    const el = document.createElement('div');
    el.id = 'fla-timer';
    el.innerHTML = '<span class="t">00:00</span><button class="tt" title="暂停/继续">⏸</button><button class="tt" title="关闭">✕</button>';
    document.getElementById('viewer').appendChild(el);
    V.timerEl = el;
    V.timerLeft = total;
    V.timerRun = true;
    V.timerEnd = Date.now() + total * 1000;
    const t = el.querySelector('.t');
    const upd = () => {
      const s = Math.max(0, Math.round((V.timerEnd - Date.now()) / 1000));
      t.textContent = String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
      el.classList.toggle('warn', s <= 60);
    };
    upd();
    V.timerInt = setInterval(() => {
      if (!V || !V.timerRun) return;
      upd();
      if (Date.now() >= V.timerEnd) { V.timerRun = false; upd(); }
    }, 250);
    const btns = el.querySelectorAll('button');
    btns[0].onclick = () => {
      if (!V) return;
      if (V.timerRun) { V.timerLeft = Math.max(0, V.timerEnd - Date.now()); V.timerRun = false; btns[0].textContent = '▶'; }
      else { V.timerEnd = Date.now() + V.timerLeft; V.timerRun = true; btns[0].textContent = '⏸'; }
    };
    btns[1].onclick = stopTimer;
  }

  function stopTimer() {
    if (!V) return;
    if (V.timerInt) { clearInterval(V.timerInt); V.timerInt = null; }
    if (V.timerEl) { V.timerEl.remove(); V.timerEl = null; }
    V.timerRun = false;
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen && document.exitFullscreen();
    } else {
      const el = document.documentElement;
      if (el.requestFullscreen) el.requestFullscreen();
      else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
    }
  }

  /* ============================== 工具栏 / 弹出面板 ============================== */

  function onToolBtn(t, btn) {
    if (V.tool === t) { togglePopover(t, btn); return; }
    setTool(t);
    closePopover();
  }

  function setTool(t) {
    if (t === 'text') commitTextEdit();
    V.tool = t;
    clearSelection();
    updateToolUI();
  }

  function updateToolUI() {
    TOOLS.forEach(t => {
      const b = document.getElementById('tb-' + t);
      if (b) b.classList.toggle('on', V.tool === t);
    });
    V.stage.style.cursor = V.tool === 'select' ? 'default'
      : V.tool === 'eraser' || V.tool === 'laser' ? 'none' : 'crosshair';
    if (V.tool !== 'eraser') { V.lastPointer = null; scheduleRedraw(); }
  }

  function togglePopover(t, btn) {
    const pop = V.pop;
    if (!pop.classList.contains('hidden') && pop.dataset.tool === t) { closePopover(); return; }
    pop.dataset.tool = t;
    pop.innerHTML = '';
    pop.classList.remove('hidden');
    if (t === 'pen' || t === 'marker') buildInkPop(t);
    else if (t === 'shape') buildShapePop();
    else if (t === 'text') buildTextPop();
    else if (t === 'eraser') buildEraserPop();
    else if (t === 'shot') buildShotPop();
    else if (t === 'select') buildSelectPop();
    else { closePopover(); return; }
    const r = btn.getBoundingClientRect();
    pop.style.left = clamp(r.left + r.width / 2 - pop.offsetWidth / 2, 8, window.innerWidth - pop.offsetWidth - 8) + 'px';
    pop.style.top = Math.max(8, r.top - pop.offsetHeight - 12) + 'px';
  }

  function closePopover() {
    if (V && V.pop) { V.pop.classList.add('hidden'); V.pop.dataset.tool = ''; }
  }

  function buildInkPop(t) {
    const colors = t === 'pen' ? PEN_COLORS : MARKER_COLORS;
    const cfg = V.cfg[t];
    const pop = V.pop;
    pop.innerHTML =
      '<div class="pop-row swatches">' + colors.map(c =>
        '<button class="swatch' + (c === cfg.color ? ' on' : '') + '" data-c="' + c + '" style="background:' + c + '"></button>').join('') +
      '<label class="swatch custom" title="自定义颜色"><input type="color" value="' + cfg.color + '"></label></div>' +
      '<div class="pop-row"><span class="pop-lb">粗细</span>' +
      '<input type="range" min="' + (t === 'pen' ? 1 : 6) + '" max="' + (t === 'pen' ? 14 : 40) + '" step="1" value="' + cfg.width + '" id="popw">' +
      '<b class="pop-val" id="popwv">' + cfg.width + '</b></div>' +
      '<div class="pop-preview"><svg height="48" width="220"><line id="prevline" x1="10" y1="24" x2="210" y2="24" stroke="' + cfg.color + '" stroke-width="' + cfg.width + '" stroke-linecap="round"' + (t === 'marker' ? ' opacity=".42"' : '') + '/></svg></div>';
    pop.querySelectorAll('.swatch[data-c]').forEach(b => b.onclick = () => {
      cfg.color = b.dataset.c;
      pop.querySelectorAll('.swatch').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      updPrev(); saveCfg();
    });
    const ci = pop.querySelector('.swatch.custom input');
    ci.oninput = () => {
      cfg.color = ci.value;
      pop.querySelectorAll('.swatch').forEach(x => x.classList.remove('on'));
      updPrev(); saveCfg();
    };
    const rng = pop.querySelector('#popw');
    rng.oninput = () => { cfg.width = +rng.value; pop.querySelector('#popwv').textContent = rng.value; updPrev(); saveCfg(); };
    function updPrev() {
      const l = pop.querySelector('#prevline');
      l.setAttribute('stroke', cfg.color);
      l.setAttribute('stroke-width', cfg.width);
    }
  }

  function buildShapePop() {
    const cfg = V.cfg.shape;
    const pop = V.pop;
    pop.innerHTML =
      '<div class="pop-row shapes-row">' + SHAPE_LIST.map(([id, name]) =>
        '<button class="shape-b' + (cfg.type === id ? ' on' : '') + '" data-s="' + id + '" title="' + name + '">' + SHAPE_MINI[id] + '</button>').join('') + '</div>' +
      '<div class="pop-row swatches">' + SHAPE_COLORS.map(c =>
        '<button class="swatch' + (c === cfg.color ? ' on' : '') + '" data-c="' + c + '" style="background:' + c + '"></button>').join('') +
      '<label class="swatch custom" title="自定义颜色"><input type="color" value="' + cfg.color + '"></label></div>' +
      '<div class="pop-row"><span class="pop-lb">粗细</span>' +
      '<input type="range" min="1" max="12" step="1" value="' + cfg.width + '" id="popw">' +
      '<b class="pop-val" id="popwv">' + cfg.width + '</b></div>' +
      '<p class="pop-hint">直线/箭头自动吸附 横平竖直与 45°</p>';
    pop.querySelectorAll('.shape-b').forEach(b => b.onclick = () => {
      cfg.type = b.dataset.s;
      pop.querySelectorAll('.shape-b').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      saveCfg();
    });
    pop.querySelectorAll('.swatch[data-c]').forEach(b => b.onclick = () => {
      cfg.color = b.dataset.c;
      pop.querySelectorAll('.swatch').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      saveCfg();
    });
    const ci = pop.querySelector('.swatch.custom input');
    ci.oninput = () => { cfg.color = ci.value; saveCfg(); };
    const rng = pop.querySelector('#popw');
    rng.oninput = () => { cfg.width = +rng.value; pop.querySelector('#popwv').textContent = rng.value; saveCfg(); };
  }

  function buildTextPop() {
    const cfg = V.cfg.text;
    const pop = V.pop;
    pop.innerHTML =
      '<div class="pop-row swatches">' + TEXT_COLORS.map(c =>
        '<button class="swatch' + (c === cfg.color ? ' on' : '') + '" data-c="' + c + '" style="background:' + c + '"></button>').join('') +
      '<label class="swatch custom" title="自定义颜色"><input type="color" value="' + cfg.color + '"></label></div>' +
      '<div class="pop-row"><span class="pop-lb">字号</span>' +
      '<input type="range" min="12" max="72" step="2" value="' + cfg.size + '" id="pops">' +
      '<b class="pop-val" id="popsv">' + cfg.size + '</b></div>' +
      '<div class="pop-preview"><span id="txtprev" style="font-size:' + Math.min(30, cfg.size) + 'px">FLA Aa</span></div>' +
      '<p class="pop-hint">点击画面输入文字，Enter 确认；双击已有文字可编辑</p>';
    pop.querySelectorAll('.swatch[data-c]').forEach(b => b.onclick = () => {
      cfg.color = b.dataset.c;
      pop.querySelectorAll('.swatch').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      saveCfg();
    });
    const ci = pop.querySelector('.swatch.custom input');
    ci.oninput = () => { cfg.color = ci.value; saveCfg(); };
    const rng = pop.querySelector('#pops');
    rng.oninput = () => {
      cfg.size = +rng.value;
      pop.querySelector('#popsv').textContent = rng.value;
      pop.querySelector('#txtprev').style.fontSize = Math.min(30, cfg.size) + 'px';
      saveCfg();
    };
  }

  function buildEraserPop() {
    const pop = V.pop;
    const mode = V.cfg.eraser.mode === 'pixel' ? 'pixel' : 'object';
    pop.innerHTML =
      '<div class="pop-row seg-row">' +
      '<button class="seg-b' + (mode === 'object' ? ' on' : '') + '" data-m="object">对象橡皮</button>' +
      '<button class="seg-b' + (mode === 'pixel' ? ' on' : '') + '" data-m="pixel">像素橡皮</button></div>' +
      '<div class="pop-row"><span class="pop-lb">粗细</span>' +
      '<input type="range" min="6" max="120" step="2" value="' + V.cfg.eraser.width + '" id="popw">' +
      '<b class="pop-val" id="popwv">' + V.cfg.eraser.width + '</b></div>' +
      '<div class="pop-preview"><div class="eraser-demo" id="edemo"></div></div>' +
      '<button class="btn danger block" id="clearpage">清 空 本 页</button>' +
      '<p class="pop-hint" id="ehint">' + (mode === 'pixel'
        ? '像素橡皮：只擦掉划过的部分，笔迹会被切断'
        : '对象橡皮：碰到哪条笔迹就整条删除') + '</p>';
    pop.querySelectorAll('.seg-b').forEach(b => b.onclick = () => {
      V.cfg.eraser.mode = b.dataset.m;
      pop.querySelectorAll('.seg-b').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      pop.querySelector('#ehint').textContent = b.dataset.m === 'pixel'
        ? '像素橡皮：只擦掉划过的部分，笔迹会被切断'
        : '对象橡皮：碰到哪条笔迹就整条删除';
      saveCfg();
    });
    const rng = pop.querySelector('#popw');
    const demo = pop.querySelector('#edemo');
    const updDemo = () => { const s = Math.min(56, 10 + +rng.value / 2); demo.style.width = demo.style.height = s + 'px'; };
    rng.oninput = () => {
      V.cfg.eraser.width = +rng.value;
      pop.querySelector('#popwv').textContent = rng.value;
      updDemo(); saveCfg();
    };
    updDemo();
    pop.querySelector('#clearpage').onclick = () => {
      const pid = cur().pid;
      const items = (V.doc.strokes[pid] || []).slice();
      if (!items.length) { closePopover(); return; }
      V.doc.strokes[pid] = [];
      clearSelection();
      act({ t: 'clear', pid, items });
      closePopover();
    };
  }

  function buildSelectPop() {
    const pop = V.pop;
    const n = V.sel ? V.sel.ids.size : 0;
    pop.innerHTML =
      '<div class="pop-row"><button class="btn block" id="selall">' + UI.icon('check', 15) + ' 全选本页 (Ctrl+A)</button></div>' +
      '<div class="pop-row"><button class="btn block danger" id="seldel"' + (n ? '' : ' disabled') + '>删除所选' + (n ? ' (' + n + ' 项)' : '') + '</button></div>' +
      '<div class="pop-row"><button class="btn block ghost" id="selclr"' + (n ? '' : ' disabled') + '>取消选择 (Esc)</button></div>' +
      '<p class="pop-hint">空白处拖拽 = 套索圈选 · 框内拖动 = 移动 · 角点 = 缩放</p>';
    pop.querySelector('#selall').onclick = () => { selectAll(); closePopover(); };
    pop.querySelector('#seldel').onclick = () => { deleteSel(); closePopover(); };
    pop.querySelector('#selclr').onclick = () => { clearSelection(); closePopover(); };
  }

  function selectAll() {
    const p = cur();
    const ids = new Set((V.doc.strokes[p.pid] || []).map(s => s.id));
    if (!ids.size) { clearSelection(); return; }
    V.sel = { ids };
    recomputeSelBox(); updateSelBar(); scheduleRedraw();
  }

  function buildShotPop() {
    const pop = V.pop;
    pop.innerHTML =
      '<button class="btn block" id="shot-dl">' + UI.icon('download', 16) + ' 下载 PNG 图片</button>' +
      '<button class="btn block" id="shot-sv">' + UI.icon('upload', 16) + ' 保存到我的课件</button>';
    pop.querySelector('#shot-dl').onclick = () => { closePopover(); screenshot('download'); };
    pop.querySelector('#shot-sv').onclick = () => { closePopover(); screenshot('save'); };
  }

  /* ============================== 配置记忆 / 自动保存 ============================== */

  function loadCfg() {
    try {
      const s = storeGet('fla_cfg');
      if (s) {
        const c = JSON.parse(s);
        ['pen', 'marker', 'eraser', 'shape', 'text'].forEach(k => {
          if (c && c[k] && typeof c[k] === 'object') Object.assign(V.cfg[k], c[k]);
        });
      }
    } catch (e) { }
    const width = (value, fallback, min, max) => {
      const n = Number(value);
      return Math.round(clamp(Number.isFinite(n) ? n : fallback, min, max));
    };
    V.cfg.pen.width = width(V.cfg.pen.width, DEFAULT_CFG.pen.width, 1, 14);
    V.cfg.marker.width = width(V.cfg.marker.width, DEFAULT_CFG.marker.width, 6, 40);
    V.cfg.shape.width = width(V.cfg.shape.width, DEFAULT_CFG.shape.width, 1, 12);
    V.cfg.text.size = width(V.cfg.text.size, DEFAULT_CFG.text.size, 12, 72);
    V.cfg.eraser.width = width(V.cfg.eraser.width, DEFAULT_CFG.eraser.width, 6, 120);
    ['pen', 'marker', 'shape', 'text'].forEach(k => {
      if (!/^#[0-9a-f]{6}$/i.test(String(V.cfg[k].color || ''))) V.cfg[k].color = DEFAULT_CFG[k].color;
    });
    if (!['object', 'pixel'].includes(V.cfg.eraser.mode)) V.cfg.eraser.mode = DEFAULT_CFG.eraser.mode;
  }

  function saveCfg() {
    storeSet('fla_cfg', JSON.stringify(V.cfg));
  }

  function serialize() {
    return {
      pages: V.doc.pages.map(p => ({ t: p.t, n: p.n, pid: p.pid, bg: p.bg || 'w' })),
      strokes: V.doc.strokes,
    };
  }

  function setSaveState(s) {
    const el = document.getElementById('vsave');
    if (!el) return;
    el.textContent = s === 'saving' ? '保存中…' : s === 'error' ? '保存失败' : '已保存';
    el.className = 'v-save ' + (s === 'error' ? 'err' : s === 'saving' ? 'saving' : '');
  }

  function saveSoon() {
    if (!V || V.destroyed) return;
    V.dirty = true;
    setSaveState('saving');
    if (V.saveT) clearTimeout(V.saveT);
    V.saveT = setTimeout(saveNow, 900);
  }

  async function saveNow() {
    if (!V || V.destroyed || !V.doc) return;
    try {
      await API.put('/api/files/' + V.id + '/annotations', serialize());
      V.dirty = false;
      setSaveState('saved');
    } catch (e) {
      setSaveState('error');
    }
  }

  function flushSave() {
    if (!V || !V.doc) return;
    const body = JSON.stringify(serialize());
    try {
      fetch('/api/files/' + V.id + '/annotations', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + API.token },
        body, keepalive: true,
      });
      V.dirty = false;
    } catch (e) { }
  }

  window.addEventListener('beforeunload', () => { if (V && V.dirty) flushSave(); });
  window.addEventListener('hashchange', () => { if (V && V.dirty) { flushSave(); } });

})();
