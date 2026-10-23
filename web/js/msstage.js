/* ================================================================
 * FLA v1.27 — 微软放映舞台 (MSStage)
 * ----------------------------------------------------------------
 * 解决的老大难: 微软 Office 在线视图是【跨域 iframe】, 父页面既读不到它当前
 * 在第几页, 也没法命令它翻页 → 板书画布永远停在第一页, 与画面脱节。
 *
 * 本模块的做法(画布严格随页切换):
 *  1. 由【我方】掌握页码: 翻页 = 换 iframe.src(同一个 src, 只改 wdStartOn /
 *     wdSlideId 定位参数)。页码是我方的状态, 画布层自然严格跟着走;
 *     src 不变 → 微软侧的转换缓存可复用, 不需要重新转换。
 *  2. 双 iframe 乒乓 + 预载下一页: 前进几乎是瞬时(下一页已在后台就绪),
 *     切换用 220ms 交叉淡入, 不闪黑屏。
 *  3. 画布坐标绑定【幻灯区域】而不是整个窗口: 按文档真实宽高比做 letterbox
 *     计算, 笔迹落在幻灯片上; 还能手动微调板书区域(拖动/缩放, 存到服务器)。
 *  4. 三种同步模式, 老师可随时切换(顶栏「同步」按钮):
 *       deep   我方驱动(默认) — ‹ › / 缩略图 / 数字键 直接翻页, 画布同步
 *       follow 微软自翻       — 老师点微软画面翻页, 板书用 ‹ › 或 Ctrl+← → 对齐
 *       (还有「本地引擎」按钮 → 完全离线的高保真渲染, 见 present.js)
 *  5. 首次进入会做一次「对齐自检」: 翻到第 2 页看微软画面是否跟着跳,
 *     不跳就自动建议切到 follow 模式, 并记住老师的选择。
 *
 * 纯 ES5(兼容学校旧浏览器 Chrome 60+), 与 viewer.js / present.js 的批注数据互通:
 *   { pages:[{t,n,pid,bg}], strokes:{pid:[...]}, bb:{n}, ms:{rect,sync,extra} }
 * 入口: MSStage.mount(opts) -> Promise<stage>
 * ================================================================ */
(function () {
  'use strict';

  var FONT = '-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif';
  var VW = 1280, VH = 720;                 /* 板书虚拟坐标系(与旧数据一致) */
  var PEN_COLORS = ['#ef4444', '#1f2937', '#2563eb', '#059669', '#f59e0b', '#ffffff'];
  var MARKER_COLORS = ['#fde047', '#86efac', '#93c5fd', '#f9a8d4', '#fdba74'];
  var SHAPE_COLORS = ['#ef4444', '#111827', '#2563eb', '#059669', '#9333ea', '#ffffff'];
  var TEXT_COLORS = ['#ef4444', '#1f2937', '#2563eb', '#059669', '#f59e0b', '#ffffff'];
  var SHAPE_LIST = [['line', '直线'], ['arrow', '箭头'], ['rect', '矩形'], ['ellipse', '椭圆'], ['triangle', '三角形']];
  var BOARD_BG = { w: '#ffffff', k: '#14171c', g: '#1c3b2d' };
  var TOOLS = [
    ['cursor', '正常模式 · 点击画面交给微软 (Esc)', 'cursor'],
    ['select', '选择 / 移动板书 (1)', 'select'],
    ['pen', '笔 (2)', 'pen'],
    ['marker', '荧光笔 (3)', 'marker'],
    ['shape', '几何图形 (4)', 'shapes'],
    ['text', '文本 (5)', 'text'],
    ['laser', '激光笔 (6)', 'laser'],
    ['eraser', '橡皮 (7)', 'eraser']
  ];
  var KEY_TOOL = { '1': 'select', '2': 'pen', '3': 'marker', '4': 'shape', '5': 'text', '6': 'laser', '7': 'eraser' };
  var SHAPE_MINI = {
    line: '<path d="M5 19L19 5"/>',
    arrow: '<path d="M5 19L19 5"/><path d="M13 5h6v6"/>',
    rect: '<rect x="4" y="6" width="16" height="12" rx="1"/>',
    ellipse: '<ellipse cx="12" cy="12" rx="9" ry="7"/>',
    triangle: '<path d="M12 4l9 16H3z"/>'
  };
  var ICONS = {
    cursor: '<path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/>',
    select: '<circle cx="11" cy="11" r="7.5" stroke-dasharray="3.2 3.2"/><path d="M15.8 15.8 21 21l-1.8.6.6-1.8z" fill="currentColor" stroke="none"/>',
    pen: '<path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    marker: '<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4l8 8Z"/>',
    shapes: '<rect x="3" y="3" width="8.5" height="8.5" rx="1.5"/><circle cx="16.5" cy="16.5" r="5"/>',
    text: '<path d="M5 7V5h14v2"/><path d="M12 5v14"/><path d="M9 19h6"/>',
    laser: '<circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none"/><path d="M12 3.5v2.6M12 17.9v2.6M3.5 12h2.6M17.9 12h2.6M5.9 5.9l1.9 1.9M16.2 16.2l1.9 1.9M18.1 5.9l-1.9 1.9M7.8 16.2l-1.9 1.9"/>',
    eraser: '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6M14 11v6"/>',
    full: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    board: '<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M12 17v3M8 21h8"/>',
    plusPage: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M12 11v6M9 14h6"/>',
    bolt: '<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/>',
    square: '<rect x="4.5" y="4.5" width="15" height="15" rx="2"/>',
    chevL: '<path d="m15 18-6-6 6-6"/>',
    chevR: '<path d="m9 18 6-6-6-6"/>',
    film: '<rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M7 5v14M17 5v14M2.5 12h19"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    move: '<path d="M12 2v20M2 12h20"/><path d="m12 2-3 3M12 2l3 3M12 22l-3-3M12 22l3-3M2 12l3-3M2 12l3 3M22 12l-3-3M22 12l-3 3"/>',
    sync: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    back: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
    down: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    timer: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5"/><path d="M9 2h6"/>',
    dice: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/><circle cx="15.5" cy="8.5" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="8.5" cy="15.5" r="1.5" fill="currentColor"/><circle cx="15.5" cy="15.5" r="1.5" fill="currentColor"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    stepNext: '<path d="M5 4l10 8-10 8V4z"/><path d="M19 5v14"/>',
    stepPrev: '<path d="M19 20L9 12l10-8v16z"/><path d="M5 19V5"/>',
    line: '<path d="M5 12h14"/>',
    cast: '<path d="M2 16.1A5 5 0 0 1 5.9 20M2 12.05A9 9 0 0 1 9.95 20M2 8V6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/><line x1="2" y1="20" x2="2.01" y2="20"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    play: '<path d="M7 5v14l12-7z"/>',
    pause: '<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>'
  };

  function icon(n, s) {
    return '<svg width="' + (s || 20) + '" height="' + (s || 20) + '" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (ICONS[n] || '') + '</svg>';
  }
  function el(tag, cls, css) {
    var d = document.createElement(tag);
    if (cls) d.className = cls;
    if (css) d.style.cssText = css;
    return d;
  }
  function escHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>\"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;' })[ch];
    });
  }
  function svgEl(w, h, inner, sw) {
    return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="' + (sw || 2) + '" stroke-linecap="round" stroke-linejoin="round">' + inner + '</svg>';
  }
  function st(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function stSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
  function uid() { return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function fmtT(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function restoreWidgetRect(widget, key) {
    if (!widget || !key) return;
    widget.setAttribute('data-fla-rect-key', key);
    try {
      var r = JSON.parse(st(key, 'null'));
      if (!r || !isFinite(r.x) || !isFinite(r.y)) return;
      if (isFinite(r.w) && r.w > 0) widget.style.width = clamp(r.w, 260, Math.max(300, window.innerWidth - 16)) + 'px';
      if (isFinite(r.h) && r.h > 0) widget.style.height = clamp(r.h, 180, Math.max(220, window.innerHeight - 16)) + 'px';
      var widgetW = isFinite(r.w) && r.w > 0 ? widget.style.width : (widget.offsetWidth || parseFloat(getComputedStyle(widget).width) || 320);
      var widgetH = isFinite(r.h) && r.h > 0 ? widget.style.height : (widget.offsetHeight || parseFloat(getComputedStyle(widget).height) || 240);
      widget.style.left = clamp(r.x, 8, Math.max(8, window.innerWidth - parseFloat(widgetW) - 8)) + 'px';
      widget.style.top = clamp(r.y, 8, Math.max(8, window.innerHeight - parseFloat(widgetH) - 8)) + 'px';
      widget.style.right = 'auto'; widget.style.bottom = 'auto';
    } catch (e) {}
  }
  function saveWidgetRect(widget) {
    var key = widget && widget.getAttribute('data-fla-rect-key');
    if (!key) return;
    var r = widget.getBoundingClientRect();
    stSet(key, JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height) }));
  }
  function ensureResizeGrip(widget) {
    if (widget && !widget.querySelector('.fla-resize-grip')) {
      var grip = el('div', 'fla-resize-grip');
      grip.title = '拖动调整大小';
      grip.setAttribute('aria-label', '拖动调整大小');
      widget.appendChild(grip);
    }
  }
  function makeDraggable(widget, handle, resizable) {
    if (!widget || !handle) return;
    if (resizable) ensureResizeGrip(widget);
    if (window.interact) {
      if (!handle.id) handle.id = 'fla-drag-' + uid();
      var interactable = window.interact(widget);
      if (!widget.__flaInteractBound) {
        widget.__flaInteractBound = true;
        interactable.draggable({
          allowFrom: '#' + handle.id,
          ignoreFrom: 'button, input, select, textarea, label',
          inertia: false,
          listeners: {
            move: function (e) {
              var r = widget.getBoundingClientRect();
              var x = clamp(r.left + e.dx, 8, Math.max(8, window.innerWidth - widget.offsetWidth - 8));
              var y = clamp(r.top + e.dy, 8, Math.max(8, window.innerHeight - widget.offsetHeight - 8));
              widget.style.left = x + 'px'; widget.style.top = y + 'px';
              widget.style.right = 'auto'; widget.style.bottom = 'auto';
              widget.style.transform = 'none';
            },
            end: function () { saveWidgetRect(widget); }
          }
        });
        if (resizable) {
          interactable.resizable({
            edges: { right: '.fla-resize-grip', bottom: '.fla-resize-grip' },
            inertia: false,
            modifiers: [window.interact.modifiers.restrictSize({
              min: { width: 260, height: 180 },
              max: { width: Math.max(300, window.innerWidth - 16), height: Math.max(220, window.innerHeight - 16) }
            })],
            listeners: {
              move: function (e) {
                widget.style.width = e.rect.width + 'px';
                widget.style.height = e.rect.height + 'px';
                widget.style.left = clamp(e.rect.left, 8, Math.max(8, window.innerWidth - e.rect.width - 8)) + 'px';
                widget.style.top = clamp(e.rect.top, 8, Math.max(8, window.innerHeight - e.rect.height - 8)) + 'px';
                widget.style.right = 'auto'; widget.style.bottom = 'auto'; widget.style.transform = 'none';
                widget.dispatchEvent(new Event('fla-widget-resize'));
              },
              end: function () { saveWidgetRect(widget); }
            }
          });
        }
      }
      handle.style.touchAction = 'none';
      return;
    }
    if (handle.__flaDragBound) return;
    handle.__flaDragBound = true;
    var isDragging = false, startX = 0, startY = 0, origX = 0, origY = 0;
    handle.addEventListener('pointerdown', function (e) {
      if (e.target.closest('button, input, select, textarea, label')) return;
      isDragging = true; startX = e.clientX; startY = e.clientY;
      var rect = widget.getBoundingClientRect(); origX = rect.left; origY = rect.top;
      try { handle.setPointerCapture(e.pointerId); } catch (err) {}
    });
    handle.addEventListener('pointermove', function (e) {
      if (!isDragging) return;
      var maxW = window.innerWidth - widget.offsetWidth - 10;
      var maxH = window.innerHeight - widget.offsetHeight - 10;
      widget.style.left = Math.max(10, Math.min(maxW, origX + e.clientX - startX)) + 'px';
      widget.style.top = Math.max(10, Math.min(maxH, origY + e.clientY - startY)) + 'px';
      widget.style.right = 'auto'; widget.style.bottom = 'auto';
      if (!widget.classList.contains('ms-bnb')) widget.style.transform = 'none';
    });
    function stopDrag(e) {
      if (isDragging) { isDragging = false; try { handle.releasePointerCapture(e.pointerId); } catch (err) {} saveWidgetRect(widget); }
    }
    handle.addEventListener('pointerup', stopDrag);
    handle.addEventListener('pointercancel', stopDrag);
    if (resizable) {
      var grip = widget.querySelector('.fla-resize-grip');
      if (grip && !grip.__flaResizeBound) {
        grip.__flaResizeBound = true;
        var resizing = false, rsx = 0, rsy = 0, rsw = 0, rsh = 0, rsl = 0, rst = 0;
        grip.addEventListener('pointerdown', function (e) {
          e.preventDefault(); e.stopPropagation();
          var r = widget.getBoundingClientRect();
          resizing = true; rsx = e.clientX; rsy = e.clientY; rsw = r.width; rsh = r.height; rsl = r.left; rst = r.top;
          try { grip.setPointerCapture(e.pointerId); } catch (err) {}
        });
        grip.addEventListener('pointermove', function (e) {
          if (!resizing) return;
          e.preventDefault(); e.stopPropagation();
          widget.style.width = clamp(rsw + e.clientX - rsx, 260, Math.max(300, window.innerWidth - rsl - 8)) + 'px';
          widget.style.height = clamp(rsh + e.clientY - rsy, 180, Math.max(220, window.innerHeight - rst - 8)) + 'px';
          widget.style.left = rsl + 'px'; widget.style.top = rst + 'px';
          widget.style.right = 'auto'; widget.style.bottom = 'auto'; widget.style.transform = 'none';
          widget.dispatchEvent(new Event('fla-widget-resize'));
        });
        function stopResize(e) {
          if (!resizing) return;
          resizing = false;
          try { grip.releasePointerCapture(e.pointerId); } catch (err) {}
          saveWidgetRect(widget);
        }
        grip.addEventListener('pointerup', stopResize);
        grip.addEventListener('pointercancel', stopResize);
      }
    }
  }

  /* ================================================================== */
  function mount(opts) {
    opts = opts || {};
    var S = {
      fid: opts.fid, token: opts.token || '', mode: opts.mode || 'present',
      meta: opts.meta || {}, mv: null, ann: null,
      slides: 1, extra: 0, page: 1, slidePage: 1, bbN: 1,
      strokes: {}, ops: {}, redos: {}, bbOps: {}, bbRedos: {},
      tool: 'cursor', drawing: null, selId: null, selOff: null, laserDots: [],
      dirty: false, saveT: 0, dead: false, t0: Date.now(),
      rect: null, frames: [], fcur: -1, loading: 0,
      sync: st('fla_ms_sync', 'deep'), thumbs: {}, pdf: null, pdfBusy: false,
      bbOpen: false, bbCur: 0, cfg: null, hideT: 0, align: null, dpr: 1,
      keepToolbar: st('fla_keep_toolbar', '1') !== '0',   /* 默认工具栏常驻不收起 */
      manifest: null, stepInSlide: 0
    };
    var CFG_DEFAULT = {
      pen: { color: '#ef4444', width: 4 },
      marker: { color: '#fde047', width: 18 },
      shape: { type: 'rect', color: '#ef4444', width: 3 },
      text: { color: '#ef4444', size: 30 },
      eraser: { width: 28, mode: 'object' },
      boardBg: 'w'
    };
    var savedCfg = null;
    try {
      /* localStorage 里存坏了(手改/旧版本)也不能让放映台起不来 */
      savedCfg = JSON.parse(st('fla_ms_cfg', 'null'));
    } catch (e) { try { localStorage.removeItem('fla_ms_cfg'); } catch (e2) { } }
    S.cfg = savedCfg && typeof savedCfg === 'object' && !Array.isArray(savedCfg) ? savedCfg : {};
    for (var ck in CFG_DEFAULT) {
      if (CFG_DEFAULT[ck] && typeof CFG_DEFAULT[ck] === 'object') {
        var storedSection = S.cfg[ck];
        S.cfg[ck] = Object.assign({}, CFG_DEFAULT[ck], storedSection && typeof storedSection === 'object' ? storedSection : {});
      } else if (S.cfg[ck] === undefined || S.cfg[ck] === null) S.cfg[ck] = CFG_DEFAULT[ck];
    }
    function validColor(value, fallback) { return /^#[0-9a-f]{6}$/i.test(String(value || '')) ? value : fallback; }
    function validWidth(section, key, fallback, min, max) {
      var n = Number(section[key]);
      section[key] = Math.round(clamp(isFinite(n) ? n : fallback, min, max));
    }
    validWidth(S.cfg.pen, 'width', CFG_DEFAULT.pen.width, 1, 14);
    validWidth(S.cfg.marker, 'width', CFG_DEFAULT.marker.width, 6, 40);
    validWidth(S.cfg.shape, 'width', CFG_DEFAULT.shape.width, 1, 14);
    validWidth(S.cfg.text, 'size', CFG_DEFAULT.text.size, 14, 90);
    validWidth(S.cfg.eraser, 'width', CFG_DEFAULT.eraser.width, 6, 120);
    S.cfg.pen.color = validColor(S.cfg.pen.color, CFG_DEFAULT.pen.color);
    S.cfg.marker.color = validColor(S.cfg.marker.color, CFG_DEFAULT.marker.color);
    S.cfg.shape.color = validColor(S.cfg.shape.color, CFG_DEFAULT.shape.color);
    S.cfg.text.color = validColor(S.cfg.text.color, CFG_DEFAULT.text.color);
    if (['line', 'arrow', 'rect', 'ellipse', 'triangle'].indexOf(S.cfg.shape.type) < 0) S.cfg.shape.type = CFG_DEFAULT.shape.type;
    if (['object', 'pixel'].indexOf(S.cfg.eraser.mode) < 0) S.cfg.eraser.mode = CFG_DEFAULT.eraser.mode;
    if (['w', 'k', 'g'].indexOf(S.cfg.boardBg) < 0) S.cfg.boardBg = CFG_DEFAULT.boardBg;

    var AUTH = { Authorization: 'Bearer ' + S.token };
    function jget(u) {
      return fetch(u, { headers: AUTH }).then(function (r) {
        if (r.status === 401) throw new Error('登录已过期, 请回到课件页重新进入');
        if (!r.ok) throw new Error('请求失败 (' + r.status + ')');
        return r.json();
      });
    }
    function jpost(u, data) {
      return fetch(u, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + S.token },
        body: JSON.stringify(data || {})
      }).then(function (r) {
        if (r.status === 401) throw new Error('登录已过期, 请重新登录');
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || '请求失败'); }).catch(function () { throw new Error('请求失败 (' + r.status + ')'); });
        return r.json();
      });
    }

    /* ---------------- DOM 骨架 ---------------- */
    var host = opts.mount || document.body;
    var wrap = el('div', 'ms-stage');
    var ifrBox = el('div', 'ms-ifrbox');
    var board = el('div', 'ms-board');            /* 附加板书页 / 板中板底色 */
    var ink = el('canvas', 'ms-ink');
    var laser = el('canvas', 'ms-laser');
    var rectGuide = el('div', 'ms-rect hidden');  /* 板书区域可视化 + 手动调整 */
    ifrBox.appendChild(board);
    wrap.appendChild(ifrBox);
    wrap.appendChild(ink);
    wrap.appendChild(laser);
    wrap.appendChild(rectGuide);

    var ictx = ink.getContext('2d');
    var lctx = laser.getContext('2d');

    var isPreview = S.mode === 'view' || S.mode === 'preview';
    if (isPreview) wrap.classList.add('ms-stage-preview');

    /* ---------------- 顶栏 ---------------- */
    var top = el('div', 'ms-top');
    if (isPreview) {
      top.innerHTML =
        '<button class="ms-tb" data-a="exit" title="返回课件库 (Esc)">' + icon('back', 17) + ' 返回</button>' +
        '<span class="ms-title" id="msTitle"></span>' +
        '<span class="ms-sep"></span>' +
        '<button class="ms-tb" data-a="prev" title="上一页 (← / PageUp)">' + icon('chevL', 18) + '</button>' +
        '<button class="ms-page" id="msPage" title="当前页码">1 / 1</button>' +
        '<button class="ms-tb" data-a="next" title="下一页 (→ / PageDown)">' + icon('chevR', 18) + '</button>' +
        '<span class="ms-sep"></span>' +
        '<button class="ms-tb" data-a="film" title="缩略图导航 (G)">' + icon('film', 17) + '</button>' +
        '<button class="ms-tb" data-a="full" title="全屏 (F)">' + icon('full', 17) + '</button>';
    } else {
      top.innerHTML =
        '<button class="ms-tb" data-a="exit" title="退出 (Esc)">' + icon('back', 17) + '</button>' +
        '<span class="ms-title" id="msTitle"></span>' +
        '<span class="ms-sep"></span>' +
        '<button class="ms-tb" data-a="prev" title="上一页 (← / PageUp)">' + icon('chevL', 18) + '</button>' +
        '<button class="ms-page" id="msPage" title="点击输入页码 / 打开缩略图">1 / 1</button>' +
        '<button class="ms-tb" data-a="next" title="下一页 (→ / PageDown)">' + icon('chevR', 18) + '</button>' +
        '<span class="ms-sep"></span>' +
        '<button class="ms-tb ms-tb-action" data-a="newboard" title="新建白板">' + icon('board', 16) + ' 新建白板</button>' +
        '<button class="ms-tb" data-a="cast" title="手机投屏与远程授课遥控">' + icon('cast', 16) + ' 手机遥控</button>' +
        '<button class="ms-tb" data-a="film" title="缩略图导航 (G)">' + icon('film', 17) + '</button>' +
        '<button class="ms-tb' + (S.keepToolbar ? ' on' : '') + '" data-a="pin" id="msPin" title="工具栏常驻显示 / 自动收起">' + icon('lock', 16) + '<em id="msPinTxt">' + (S.keepToolbar ? '工具栏常驻' : '自动收起') + '</em></button>' +
        '<button class="ms-tb" data-a="align" title="微调板书区域(对准幻灯片)">' + icon('move', 17) + '</button>' +
        '<span class="ms-sep"></span>' +
        '<button class="ms-tb" data-a="time" id="msTime" title="点击归零">00:00</button>' +
        '<button class="ms-tb" data-a="settings" title="工具栏个性化定制">' + icon('settings', 17) + '</button>' +
        '<button class="ms-tb" data-a="full" title="全屏 (F)">' + icon('full', 17) + '</button>';
    }
    wrap.appendChild(top);
    var sidePrev = el('button', 'ms-side-arrow ms-side-prev');
    sidePrev.type = 'button'; sidePrev.setAttribute('aria-label', '上一页'); sidePrev.title = '上一页 (← / PageUp)';
    sidePrev.innerHTML = icon('chevL', 28);
    sidePrev.onclick = function (e) { e.stopPropagation(); goPage(S.page - 1); };
    var sideNext = el('button', 'ms-side-arrow ms-side-next');
    sideNext.type = 'button'; sideNext.setAttribute('aria-label', '下一页'); sideNext.title = '下一页 (→ / PageDown)';
    sideNext.innerHTML = icon('chevR', 28);
    sideNext.onclick = function (e) { e.stopPropagation(); goPage(S.page + 1); };
    wrap.appendChild(sidePrev); wrap.appendChild(sideNext);

    /* ---------------- 左侧工具条 (预览模式不生成) ---------------- */
    var barL = null;
    if (!isPreview) {
      barL = el('nav', 'ms-pill ms-pill-l');
      TOOLS.forEach(function (t) {
        var b = el('button', 'ms-vbtn' + (t[0] === 'cursor' ? ' on' : ''));
        b.setAttribute('data-tool', t[0]); b.title = t[1]; b.innerHTML = icon(t[2], 20);
        barL.appendChild(b);
      });
      barL.appendChild(el('i', 'ms-vsep'));
      function vbtn(cls, act, title, ic) {
        var b = el('button', 'ms-vbtn ' + (cls || ''));
        b.setAttribute('data-a', act); b.title = title; b.innerHTML = ic;
        barL.appendChild(b); return b;
      }
      vbtn('', 'undo', '撤销 (Ctrl+Z)', icon('undo', 20));
      vbtn('', 'redo', '重做 (Ctrl+Y)', icon('redo', 20));
      barL.appendChild(el('i', 'ms-vsep'));
      vbtn('', 'pgprev', '画布上一页 (独立板书)', icon('chevL', 19));
      vbtn('', 'addpage', '加一页板书(在末页之后)', icon('plusPage', 19));
      vbtn('', 'pgnext', '画布下一页 (独立板书)', icon('chevR', 19));
      wrap.appendChild(barL);
    }

    /* ---------------- 右侧工具条 (预览模式不生成) ---------------- */
    var barR = null;
    if (!isPreview) {
      barR = el('nav', 'ms-pill ms-pill-r');
      function rbtn(act, title, ic) {
        var b = el('button', 'ms-vbtn');
        b.setAttribute('data-a', act); b.title = title; b.innerHTML = ic;
        barR.appendChild(b); return b;
      }
      rbtn('stepPrev', 'PPT 动画步退 / 上一步 (翻页笔穿透)', icon('stepPrev', 19));
      rbtn('stepNext', 'PPT 动画步进 / 下一步 (翻页笔穿透)', icon('stepNext', 19));
      rbtn('timer', '课堂计时器 (秒表 / 倒计时)', icon('timer', 20));
      rbtn('picker', '随机抽选 (名单抽人 / 数字摇号)', icon('dice', 20));
      rbtn('film', '缩略图导航 (G)', icon('film', 20));
      rbtn('clear', '清空本页板书', icon('trash', 20));
      rbtn('bnb', '板中板: 独立小黑板(可加页)', icon('board', 20));
      rbtn('bg', '板书页底色: 白 / 黑板 / 绿黑板', '<span class="ms-dotbg"></span>');
      rbtn('black', '黑屏 (B)', icon('square', 20));
      rbtn('settings', '工具栏功能个性化设置', icon('settings', 20));
      rbtn('local', '本地引擎(离线高保真渲染, 含动画)', icon('bolt', 20));
      if (S.mode === 'view') {
        rbtn('present', '全屏放映', icon('full', 20));
        rbtn('refresh', '重新载入微软画面(卡住/空白时用)', icon('sync', 20));
        rbtn('dl', '下载原文件', icon('down', 20));
      }
      wrap.appendChild(barR);
    }

    /* ---------------- 工具配置弹窗 ---------------- */
    var pop = el('div', 'ms-pop hidden');
    wrap.appendChild(pop);

    /* ---------------- 提示 / 加载 / 黑屏 / 板中板 ---------------- */
    var toasts = el('div', 'ms-toasts'); wrap.appendChild(toasts);
    var load = el('div', 'ms-load');
    load.innerHTML = '<div class="ms-spin"></div><p id="msLoadT">微软服务器正在抓取课件…</p>' +
      '<p class="ms-load-sub" id="msLoadS">首次约 30–60 秒; 之后翻页走缓存, 很快</p>';
    wrap.appendChild(load);
    var blk = el('div', 'ms-black hidden'); wrap.appendChild(blk);
    var film = el('div', 'ms-film hidden'); wrap.appendChild(film);

    var bnb = el('div', 'ms-bnb');
    bnb.innerHTML = '<div class="ms-bnb-bar">' +
      '<b>板中板</b><button class="ms-tb" data-b="prev">' + icon('chevL', 15) + '</button>' +
      '<span id="bbPg">1 / 1</span><button class="ms-tb" data-b="next">' + icon('chevR', 15) + '</button>' +
      '<button class="ms-tb" data-b="add" title="加一页">' + icon('plusPage', 15) + '</button>' +
      '<button class="ms-tb" data-b="clear" title="清空本页">' + icon('trash', 15) + '</button>' +
      '<button class="ms-tb" data-b="close" title="收起">✕</button></div>' +
      '<canvas id="bbCv"></canvas>';
    wrap.appendChild(bnb);
    var bbCv = bnb.querySelector('#bbCv');
    restoreWidgetRect(bnb, 'fla_ms_bnb_rect');
    makeDraggable(bnb, bnb.querySelector('.ms-bnb-bar'), true);
    bnb.addEventListener('fla-widget-resize', function () { sizeCanvas(); bbRedraw(); });

    /* ---------------- 工具栏个性化定制配置 ---------------- */
    var DEFAULT_TB_PREFS = {
      cursor: true,
      pen: true,
      marker: true,
      shape: true,
      text: true,
      laser: true,
      eraser: true,
      undo: true,
      pageNav: true,
      addpage: true,
      stepBtns: true,
      timer: true,
      picker: true,
      film: true,
      clear: true,
      bnb: true,
      bg: true,
      black: true,
      settings: true,
      local: false
    };

    function loadTbPrefs() {
      try {
        var raw = localStorage.getItem('fla_tb_prefs');
        if (!raw) return Object.assign({}, DEFAULT_TB_PREFS);
        return Object.assign({}, DEFAULT_TB_PREFS, JSON.parse(raw));
      } catch (e) {
        return Object.assign({}, DEFAULT_TB_PREFS);
      }
    }

    var tbPrefs = loadTbPrefs();

    function applyTbPrefs() {
      ['cursor', 'pen', 'marker', 'shape', 'text', 'laser', 'eraser'].forEach(function (k) {
        var elTool = barL.querySelector('[data-tool="' + k + '"]');
        if (elTool) elTool.style.display = tbPrefs[k] === false ? 'none' : '';
      });
      var uEl = barL.querySelector('[data-a="undo"]');
      var rEl = barL.querySelector('[data-a="redo"]');
      if (uEl) uEl.style.display = tbPrefs.undo === false ? 'none' : '';
      if (rEl) rEl.style.display = tbPrefs.undo === false ? 'none' : '';

      var prevEl = barL.querySelector('[data-a="pgprev"]');
      var nextEl = barL.querySelector('[data-a="pgnext"]');
      if (prevEl) prevEl.style.display = tbPrefs.pageNav === false ? 'none' : '';
      if (nextEl) nextEl.style.display = tbPrefs.pageNav === false ? 'none' : '';

      var addEl = barL.querySelector('[data-a="addpage"]');
      if (addEl) addEl.style.display = tbPrefs.addpage === false ? 'none' : '';

      var spEl = barR.querySelector('[data-a="stepPrev"]');
      var snEl = barR.querySelector('[data-a="stepNext"]');
      if (spEl) spEl.style.display = tbPrefs.stepBtns === false ? 'none' : '';
      if (snEl) snEl.style.display = tbPrefs.stepBtns === false ? 'none' : '';

      ['timer', 'picker', 'film', 'clear', 'bnb', 'bg', 'black', 'settings', 'local'].forEach(function (k) {
        var elA = barR.querySelector('[data-a="' + k + '"]');
        if (elA) elA.style.display = tbPrefs[k] === false ? 'none' : '';
      });

      try {
        localStorage.setItem('fla_tb_prefs', JSON.stringify(tbPrefs));
      } catch (e) {}
    }
    applyTbPrefs();

    /* ---------------- 设置弹窗 ---------------- */
    var settingsModal = el('div', 'fla-settings-modal hidden');
    wrap.appendChild(settingsModal);

    function renderSettingsModal() {
      settingsModal.innerHTML =
        '<div class="fla-sm-head">' +
          '<div class="fla-sm-title">⚙️ 工具栏个性化定制</div>' +
          '<button class="fla-wh-btn" data-act="close" title="关闭">✕</button>' +
        '</div>' +
        '<div class="fla-sm-body">' +
          '<div class="fla-sm-section">' +
            '<div class="fla-sm-sec-title">左侧核心工具栏</div>' +
            '<div class="fla-sm-grid">' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="cursor"' + (tbPrefs.cursor ? ' checked' : '') + '> 光标 / 选择</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="pen"' + (tbPrefs.pen ? ' checked' : '') + '> 画笔</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="marker"' + (tbPrefs.marker ? ' checked' : '') + '> 荧光笔</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="shape"' + (tbPrefs.shape ? ' checked' : '') + '> 几何图形</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="text"' + (tbPrefs.text ? ' checked' : '') + '> 文本输入</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="laser"' + (tbPrefs.laser ? ' checked' : '') + '> 激光笔</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="eraser"' + (tbPrefs.eraser ? ' checked' : '') + '> 橡皮擦</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="undo"' + (tbPrefs.undo ? ' checked' : '') + '> 撤销 / 重做</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="pageNav"' + (tbPrefs.pageNav ? ' checked' : '') + '> 画布上一页/下一页</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="addpage"' + (tbPrefs.addpage ? ' checked' : '') + '> 加一页板书</label>' +
            '</div>' +
          '</div>' +
          '<div class="fla-sm-section">' +
            '<div class="fla-sm-sec-title">右侧辅助教学工具栏</div>' +
            '<div class="fla-sm-grid">' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="stepBtns"' + (tbPrefs.stepBtns ? ' checked' : '') + '> PPT 动画步退/步进</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="timer"' + (tbPrefs.timer ? ' checked' : '') + '> 课堂计时器</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="picker"' + (tbPrefs.picker ? ' checked' : '') + '> 随机抽选 (名单/摇号)</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="film"' + (tbPrefs.film ? ' checked' : '') + '> 缩略图导航</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="clear"' + (tbPrefs.clear ? ' checked' : '') + '> 清空本页板书</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="bnb"' + (tbPrefs.bnb ? ' checked' : '') + '> 板中板 (小黑板)</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="bg"' + (tbPrefs.bg ? ' checked' : '') + '> 板书底色切换</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="black"' + (tbPrefs.black ? ' checked' : '') + '> 幕布黑屏</label>' +
              '<label class="fla-sm-item"><input type="checkbox" data-pref="local"' + (tbPrefs.local ? ' checked' : '') + '> 本地渲染引擎</label>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="fla-sm-foot">' +
          '<button class="fla-tbtn fla-tbtn-sec" id="flaTbResetBtn">恢复默认推荐</button>' +
          '<button class="fla-tbtn fla-tbtn-pri" id="flaTbDoneBtn">完成</button>' +
        '</div>';

      settingsModal.querySelectorAll('input[data-pref]').forEach(function (inp) {
        inp.addEventListener('change', function () {
          var k = inp.getAttribute('data-pref');
          tbPrefs[k] = inp.checked;
          applyTbPrefs();
        });
      });

      var closeBtn = settingsModal.querySelector('[data-act="close"]');
      if (closeBtn) closeBtn.onclick = function () { settingsModal.classList.add('hidden'); };
      var doneBtn = settingsModal.querySelector('#flaTbDoneBtn');
      if (doneBtn) doneBtn.onclick = function () { settingsModal.classList.add('hidden'); };
      var resetBtn = settingsModal.querySelector('#flaTbResetBtn');
      if (resetBtn) resetBtn.onclick = function () {
        tbPrefs = Object.assign({}, DEFAULT_TB_PREFS);
        applyTbPrefs();
        renderSettingsModal();
        toast('已恢复默认工具栏设置');
      };
    }

    function toggleSettingsModal() {
      if (settingsModal.classList.contains('hidden')) {
        renderSettingsModal();
        settingsModal.classList.remove('hidden');
      } else {
        settingsModal.classList.add('hidden');
      }
    }

    /* ---------------- 课堂计时器 (倒计时 & 秒表 - 希沃白板旗舰级 UI) ---------------- */
    var timerWidget = el('div', 'fla-widget fla-timer-widget hidden');
    wrap.appendChild(timerWidget);
    restoreWidgetRect(timerWidget, 'fla_ms_timer_rect');

    var timerMin = el('div', 'fla-timer-min hidden');
    timerMin.innerHTML = '<span class="fla-timer-min-dot"></span>' + icon('timer', 15) + '<span id="tmMinTxt">05:00</span>';
    wrap.appendChild(timerMin);
    restoreWidgetRect(timerMin, 'fla_ms_timer_min_rect');

    var TMR = {
      mode: 'countdown',
      countdownSec: 300,
      countdownRem: 300,
      countdownRun: false,
      countdownTimer: null,
      stopwatchMs: 0,
      stopwatchRun: false,
      stopwatchTimer: null,
      stopwatchLast: 0,
      stopwatchLaps: [],
      wheelOpen: false,
      wheel: null
    };

    function playTone(freq, dur, type, delay) {
      try {
        var AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        var ctx = TMR._actx || (TMR._actx = new AudioContext());
        if (ctx.state === 'suspended') ctx.resume();
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = type || 'sine';
        var t = ctx.currentTime + (delay || 0);
        osc.frequency.setValueAtTime(freq, t);
        gain.gain.setValueAtTime(0.15, t);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t);
        osc.stop(t + dur);
      } catch (e) {}
    }

    function playAlarm() {
      playTone(523.25, 0.25, 'sine', 0);
      playTone(659.25, 0.25, 'sine', 0.12);
      playTone(783.99, 0.25, 'sine', 0.24);
      playTone(1046.50, 0.55, 'sine', 0.36);
    }

    function fmtStopwatch(ms) {
      var totalSec = Math.floor(ms / 1000);
      var m = Math.floor(totalSec / 60);
      var s = totalSec % 60;
      var tenths = Math.floor((ms % 1000) / 100);
      return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s + '.' + tenths;
    }

    function updateTimerUI() {
      var cdDigits = timerWidget.querySelector('#tmCountdownDigits');
      if (cdDigits) {
        cdDigits.textContent = fmtT(TMR.countdownRem);
        if (TMR.countdownRem === 0) cdDigits.classList.add('times-up');
        else cdDigits.classList.remove('times-up');
      }
      var swDigits = timerWidget.querySelector('#tmStopwatchDigits');
      if (swDigits) {
        swDigits.textContent = fmtStopwatch(TMR.stopwatchMs);
      }
      var minTxt = timerMin.querySelector('#tmMinTxt');
      if (minTxt) {
        minTxt.textContent = TMR.mode === 'countdown' ? fmtT(TMR.countdownRem) : fmtStopwatch(TMR.stopwatchMs);
      }
      var cdStart = timerWidget.querySelector('#tmCdStartBtn');
      if (cdStart) {
        cdStart.className = 'fla-tbtn ' + (TMR.countdownRun ? 'fla-tbtn-pause' : 'fla-tbtn-start');
        cdStart.textContent = TMR.countdownRun ? '暂停' : (TMR.countdownRem < TMR.countdownSec ? '继续' : '开始');
      }
      var swStart = timerWidget.querySelector('#tmSwStartBtn');
      if (swStart) {
        swStart.className = 'fla-tbtn ' + (TMR.stopwatchRun ? 'fla-tbtn-pause' : 'fla-tbtn-start');
        swStart.textContent = TMR.stopwatchRun ? '暂停' : (TMR.stopwatchMs > 0 ? '继续' : '开始');
      }
    }

    /* iOS 式时/分/秒滚轮: 依赖 web/js/timewheel.js (未加载时自动隐藏入口) */
    function mountTimerWheel() {
      var box = timerWidget.querySelector('#tmWheelBox');
      var toggle = timerWidget.querySelector('#tmWheelToggle');
      if (!box || !toggle) return;
      if (!window.TimeWheel) { toggle.style.display = 'none'; return; }
      toggle.onclick = function () {
        TMR.wheelOpen = !TMR.wheelOpen;
        box.classList.toggle('hidden', !TMR.wheelOpen);
        toggle.textContent = TMR.wheelOpen ? '滚轮设定 ▴' : '滚轮设定 ▾';
        renderTimerWidget();
      };
      if (!TMR.wheelOpen) { TMR.wheel = null; return; }
      TMR.wheel = window.TimeWheel.mount(box, {
        value: TMR.countdownSec,
        onChange: function (v) {
          if (TMR.countdownRun) return;
          TMR.countdownSec = v || 1;
          TMR.countdownRem = v || 1;
          updateTimerUI();
        }
      });
      if (TMR.countdownRun) TMR.wheel.setDisabled(true);
    }

    function renderTimerWidget() {
      timerWidget.innerHTML =
        '<div class="fla-widget-head" id="tmHead">' +
          '<div class="fla-wh-title">' + icon('timer', 16) + ' 计时器</div>' +
          '<div class="fla-wh-actions">' +
            '<button class="fla-wh-btn" data-act="min" title="最小化">' + icon('line', 12) + '</button>' +
            '<button class="fla-wh-btn close" data-act="close" title="关闭">' + icon('close', 13) + '</button>' +
          '</div>' +
        '</div>' +
        '<div class="fla-widget-tabs">' +
          '<div class="fla-tab-capsule">' +
            '<button class="fla-wtab ' + (TMR.mode === 'countdown' ? 'on' : '') + '" data-tab="countdown">倒计时</button>' +
            '<button class="fla-wtab ' + (TMR.mode === 'stopwatch' ? 'on' : '') + '" data-tab="stopwatch">秒表</button>' +
          '</div>' +
        '</div>' +
        '<div class="fla-widget-body" id="tmCountdownBody" style="' + (TMR.mode === 'countdown' ? '' : 'display:none;') + '">' +
          '<div class="fla-timer-board">' +
            '<div class="fla-timer-digits' + (TMR.countdownRem === 0 ? ' times-up' : '') + '" id="tmCountdownDigits">' + fmtT(TMR.countdownRem) + '</div>' +
          '</div>' +
          '<div class="fla-timer-presets">' +
            '<button class="fla-tpill" data-sec="30">30秒</button>' +
            '<button class="fla-tpill" data-sec="60">1分钟</button>' +
            '<button class="fla-tpill" data-sec="120">2分钟</button>' +
            '<button class="fla-tpill" data-sec="180">3分钟</button>' +
            '<button class="fla-tpill" data-sec="300">5分钟</button>' +
            '<button class="fla-tpill" data-sec="600">10分钟</button>' +
          '</div>' +
          '<div class="fla-timer-presets" style="margin-bottom:8px;">' +
            '<button class="fla-tpill" data-adj="-60">-1分</button>' +
            '<button class="fla-tpill" data-adj="30">+30秒</button>' +
            '<button class="fla-tpill" data-adj="60">+1分</button>' +
          '</div>' +
          '<div class="fla-timer-custom fla-timer-wheelrow">' +
            '<button class="fla-tpill" id="tmWheelToggle">滚轮设定 ▾</button>' +
            '<span style="font-size:11.5px;opacity:.65;">拖动/点 ±，像 iOS 计时器</span>' +
          '</div>' +
          '<div class="fla-timer-wheelbox' + (TMR.wheelOpen ? '' : ' hidden') + '" id="tmWheelBox"></div>' +
          '<div class="fla-timer-custom">' +
            '<span>自定义</span>' +
            '<input id="tmCustomMin" type="number" min="0" max="5999" value="' + Math.floor(TMR.countdownSec / 60) + '" aria-label="分钟">' +
            '<span>分</span><input id="tmCustomSec" type="number" min="0" max="59" value="' + (TMR.countdownSec % 60) + '" aria-label="秒">' +
            '<span>秒</span><button class="fla-tpill" id="tmSetCustom">设定</button>' +
          '</div>' +
          '<div class="fla-timer-ctrls">' +
            '<button class="fla-tbtn ' + (TMR.countdownRun ? 'fla-tbtn-pause' : 'fla-tbtn-start') + '" id="tmCdStartBtn">' + (TMR.countdownRun ? '暂停' : '开始') + '</button>' +
            '<button class="fla-tbtn fla-tbtn-sec" id="tmCdResetBtn">重置</button>' +
          '</div>' +
        '</div>' +
        '<div class="fla-widget-body" id="tmStopwatchBody" style="' + (TMR.mode === 'stopwatch' ? '' : 'display:none;') + '">' +
          '<div class="fla-timer-board">' +
            '<div class="fla-timer-digits" id="tmStopwatchDigits">' + fmtStopwatch(TMR.stopwatchMs) + '</div>' +
          '</div>' +
          '<div class="fla-timer-ctrls">' +
            '<button class="fla-tbtn ' + (TMR.stopwatchRun ? 'fla-tbtn-pause' : 'fla-tbtn-start') + '" id="tmSwStartBtn">' + (TMR.stopwatchRun ? '暂停' : '开始') + '</button>' +
            '<button class="fla-tbtn fla-tbtn-sec" id="tmSwLapBtn">计次</button>' +
            '<button class="fla-tbtn fla-tbtn-sec" id="tmSwResetBtn">重置</button>' +
          '</div>' +
          '<div class="fla-lap-list" id="tmLapList" style="' + (TMR.stopwatchLaps.length ? '' : 'display:none;') + '"></div>' +
        '</div>';

      renderLaps();
      bindTimerEvents();
      mountTimerWheel();
      makeDraggable(timerWidget, timerWidget.querySelector('#tmHead'), true);
    }

    function renderLaps() {
      var lapList = timerWidget.querySelector('#tmLapList');
      if (!lapList) return;
      if (!TMR.stopwatchLaps.length) { lapList.style.display = 'none'; return; }
      lapList.style.display = 'block';
      lapList.innerHTML = TMR.stopwatchLaps.map(function (lap, idx) {
        return '<div class="fla-lap-row"><span class="fla-lap-badge">第 ' + (idx + 1) + ' 次</span><span>分段: +' + fmtStopwatch(lap.split) + '</span><span style="font-weight:700;">' + fmtStopwatch(lap.total) + '</span></div>';
      }).reverse().join('');
    }

    function bindTimerEvents() {
      timerWidget.querySelectorAll('.fla-wtab').forEach(function (tab) {
        tab.onclick = function () {
          TMR.mode = tab.getAttribute('data-tab');
          renderTimerWidget();
        };
      });

      timerWidget.querySelectorAll('[data-sec]').forEach(function (b) {
        b.onclick = function () {
          var s = parseInt(b.getAttribute('data-sec'), 10) || 60;
          TMR.countdownSec = s;
          TMR.countdownRem = s;
          stopCountdown();
          renderTimerWidget();
        };
      });

      timerWidget.querySelectorAll('[data-adj]').forEach(function (b) {
        b.onclick = function () {
          var adj = parseInt(b.getAttribute('data-adj'), 10) || 0;
          TMR.countdownRem = Math.max(5, TMR.countdownRem + adj);
          TMR.countdownSec = Math.max(TMR.countdownSec, TMR.countdownRem);
          updateTimerUI();
        };
      });
      var setCustom = timerWidget.querySelector('#tmSetCustom');
      if (setCustom) setCustom.onclick = function () {
        var mi = timerWidget.querySelector('#tmCustomMin');
        var si = timerWidget.querySelector('#tmCustomSec');
        var minutes = clamp(parseInt(mi.value, 10) || 0, 0, 5999);
        var seconds = clamp(parseInt(si.value, 10) || 0, 0, 59);
        var total = minutes * 60 + seconds;
        if (!total) { toast('自定义时长至少为 1 秒'); return; }
        stopCountdown();
        TMR.countdownSec = total;
        TMR.countdownRem = total;
        updateTimerUI();
        toast('倒计时已设为 ' + fmtT(total));
      };

      var cdStart = timerWidget.querySelector('#tmCdStartBtn');
      if (cdStart) {
        cdStart.onclick = function () {
          if (TMR.countdownRun) {
            stopCountdown();
          } else {
            startCountdown();
          }
          updateTimerUI();
        };
      }

      var cdReset = timerWidget.querySelector('#tmCdResetBtn');
      if (cdReset) {
        cdReset.onclick = function () {
          stopCountdown();
          TMR.countdownRem = TMR.countdownSec;
          renderTimerWidget();
        };
      }

      var swStart = timerWidget.querySelector('#tmSwStartBtn');
      if (swStart) {
        swStart.onclick = function () {
          if (TMR.stopwatchRun) {
            stopStopwatch();
          } else {
            startStopwatch();
          }
          updateTimerUI();
        };
      }

      var swLap = timerWidget.querySelector('#tmSwLapBtn');
      if (swLap) {
        swLap.onclick = function () {
          if (!TMR.stopwatchMs) return;
          var last = TMR.stopwatchLaps.length ? TMR.stopwatchLaps[TMR.stopwatchLaps.length - 1].total : 0;
          TMR.stopwatchLaps.push({ split: TMR.stopwatchMs - last, total: TMR.stopwatchMs });
          renderLaps();
        };
      }

      var swReset = timerWidget.querySelector('#tmSwResetBtn');
      if (swReset) {
        swReset.onclick = function () {
          stopStopwatch();
          TMR.stopwatchMs = 0;
          TMR.stopwatchLaps = [];
          renderTimerWidget();
        };
      }

      var minBtn = timerWidget.querySelector('[data-act="min"]');
      if (minBtn) minBtn.onclick = function () {
        timerWidget.classList.add('hidden');
        timerMin.classList.remove('hidden');
      };
      var closeBtn = timerWidget.querySelector('[data-act="close"]');
      if (closeBtn) closeBtn.onclick = function () {
        timerWidget.classList.add('hidden');
        timerMin.classList.add('hidden');
      };
    }

    timerMin.onclick = function () {
      timerMin.classList.add('hidden');
      timerWidget.classList.remove('hidden');
      renderTimerWidget();
    };
    makeDraggable(timerMin, timerMin);

    function startCountdown() {
      if (TMR.countdownRem <= 0) TMR.countdownRem = TMR.countdownSec;
      TMR.countdownRun = true;
      if (TMR.wheel) TMR.wheel.setDisabled(true);
      clearInterval(TMR.countdownTimer);
      var lastTick = Date.now();
      TMR.countdownTimer = setInterval(function () {
        var now = Date.now();
        var delta = Math.floor((now - lastTick) / 1000);
        if (delta >= 1) {
          TMR.countdownRem = Math.max(0, TMR.countdownRem - delta);
          lastTick = now;
          updateTimerUI();
          if (TMR.countdownRem === 0) {
            stopCountdown();
            playAlarm();
            toast('⏰ 倒计时结束！');
            renderTimerWidget();
          }
        }
      }, 250);
    }

    function stopCountdown() {
      TMR.countdownRun = false;
      clearInterval(TMR.countdownTimer);
      TMR.countdownTimer = null;
      if (TMR.wheel) TMR.wheel.setDisabled(false);
    }

    function startStopwatch() {
      TMR.stopwatchRun = true;
      clearInterval(TMR.stopwatchTimer);
      TMR.stopwatchLast = Date.now();
      TMR.stopwatchTimer = setInterval(function () {
        var now = Date.now();
        TMR.stopwatchMs += (now - TMR.stopwatchLast);
        TMR.stopwatchLast = now;
        updateTimerUI();
      }, 50);
    }

    function stopStopwatch() {
      TMR.stopwatchRun = false;
      clearInterval(TMR.stopwatchTimer);
      TMR.stopwatchTimer = null;
    }

    function toggleTimerWidget() {
      if (timerWidget.classList.contains('hidden') && timerMin.classList.contains('hidden')) {
        renderTimerWidget();
        timerWidget.style.right = '70px';
        timerWidget.style.top = '70px';
        timerWidget.classList.remove('hidden');
      } else {
        timerWidget.classList.add('hidden');
        timerMin.classList.add('hidden');
      }
    }

    /* ---------------- 手机投屏与远程授课遥控 ---------------- */
    var castModal = null;
    var remoteSession = null;
    var remoteWs = null;
    var remotePollTimer = null;

    function openCastModal() {
      if (!castModal) {
        castModal = el('div', 'fla-settings-modal hidden');
        wrap.appendChild(castModal);
      }
      castModal.innerHTML =
        '<div class="fla-sm-head">' +
          '<div class="fla-sm-title">' + icon('cast', 16) + ' 手机投屏与远程遥控</div>' +
          '<button class="fla-wh-btn close" id="flaCastCloseBtn" title="关闭">' + icon('close', 13) + '</button>' +
        '</div>' +
        '<div class="fla-sm-body" style="align-items:center; text-align:center;">' +
          '<div id="flaCastQrCode" style="width:190px; height:190px; background:#fff; padding:6px; border-radius:8px; border:1px solid #e2e8f0; margin:6px auto;"></div>' +
          '<div style="font-size:13px; color:#64748b; margin-top:6px;">微信或浏览器扫码，或在手机端输入配对码：</div>' +
          '<div id="flaCastPin" style="font-size:28px; font-weight:700; letter-spacing:4px; color:#09090b; font-family:ui-monospace, monospace; margin:4px 0;">----</div>' +
          '<div id="flaCastStatus" style="font-size:12.5px; color:#52525b; font-weight:600; display:flex; align-items:center; justify-content:center; gap:6px;">' +
            '<span class="fla-timer-min-dot"></span> 等待手机连接…' +
          '</div>' +
          '<div style="font-size:12px; color:#64748b; line-height:1.5; margin-top:8px; max-width:320px;">' +
            '手机与电脑大屏实时同步：翻页、动画步进、红外激光指示、板书随页同步、黑屏幕布。' +
          '</div>' +
        '</div>' +
        '<div class="fla-sm-foot">' +
          '<span style="font-size:12px; color:#64748b;">局域网直连 · 毫秒级响应</span>' +
          '<button class="fla-tbtn fla-tbtn-sec" id="flaCastDoneBtn">完成</button>' +
        '</div>';

      castModal.classList.remove('hidden');
      castModal.querySelector('#flaCastCloseBtn').onclick = function () { castModal.classList.add('hidden'); };
      castModal.querySelector('#flaCastDoneBtn').onclick = function () { castModal.classList.add('hidden'); };

      initRemoteSession();
    }

    function initRemoteSession() {
      if (remoteSession) {
        renderCastModalData();
        return;
      }
      jpost('/api/remote/create', {
        title: (S.meta && S.meta.name) || '课堂放映',
        fid: S.fid,
        page: S.page,
        total: total()
      }).then(function (res) {
        if (res && res.session_id) {
          remoteSession = res;
          renderCastModalData();
          connectRemoteWs();
        }
      }).catch(function (err) {
        toast('创建遥控会话失败: ' + err.message);
      });
    }

    function renderCastModalData() {
      if (!castModal || !remoteSession) return;
      var pinEl = castModal.querySelector('#flaCastPin');
      if (pinEl) pinEl.textContent = remoteSession.code || '----';

      var qrContainer = castModal.querySelector('#flaCastQrCode');
      if (qrContainer) {
        qrContainer.innerHTML = '';
        var fullUrl = location.origin + (remoteSession.remote_url || ('/#/remote?sid=' + remoteSession.session_id + '&code=' + remoteSession.code));
        if (window.UI && typeof window.UI.renderQR === 'function') {
          window.UI.renderQR(qrContainer, fullUrl, 176);
        } else if (typeof window.renderQrSvgFallback === 'function') {
          window.renderQrSvgFallback(qrContainer, fullUrl, 176);
        } else if (window.QRCode) {
          try {
            new window.QRCode(qrContainer, {
              text: fullUrl,
              width: 176,
              height: 176
            });
          } catch (e) {
            qrContainer.textContent = fullUrl;
          }
        } else {
          qrContainer.innerHTML = '<div style="padding:10px;font-size:12px;color:#09090b;word-break:break-all;">' + fullUrl + '</div>';
        }
      }
    }

    function setCastStatus(text) {
      var stEl = castModal ? castModal.querySelector('#flaCastStatus') : null;
      if (stEl) stEl.innerHTML = '<span class="fla-timer-min-dot"></span> ' + text;
    }

    function connectRemoteWs() {
      if (!remoteSession) return;
      var sid = remoteSession.session_id;
      var wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      var wsUrl = wsProto + '//' + location.host + '/api/remote/ws/' + sid + '?role=stage';

      try {
        remoteWs = new WebSocket(wsUrl);
        remoteWs.onopen = function () {
          syncRemoteState();
          setCastStatus('大屏通道已就绪，等待手机接入…');
        };
        remoteWs.onmessage = function (e) {
          try {
            var msg = JSON.parse(e.data);
            handleRemoteMessage(msg);
          } catch (err) {}
        };
        remoteWs.onclose = function () {
          remoteWs = null;
          if (!remoteSession) return;
          setCastStatus('连接中断，已切换为轮询模式');
          startRemotePolling();
        };
      } catch (err) {
        setCastStatus('连接中断，已切换为轮询模式');
        startRemotePolling();
      }
    }

    function handleRemoteMessage(msg) {
      /* 手机接入/断开 presence 事件 (WS 与 HTTP 轮询两条通道都会送达) */
      if ((msg.type === 'hello' || msg.type === 'bye') && (msg.role || msg.sender) === 'controller') {
        setCastStatus(msg.type === 'hello' ? '手机已连接 ✓ 可以开始遥控' : '手机已断开，等待重连…');
        return;
      }
      if (msg.type === 'action') {
        var act = msg.action;
        var d = msg.data || {};
        if (act === 'next') goPage(S.page + 1);
        else if (act === 'prev') goPage(S.page - 1);
        else if (act === 'stepNext') stepNext();
        else if (act === 'stepPrev') stepPrev();
        else if (act === 'goto' && d.page) goPage(d.page);
        else if (act === 'black') blk.classList.toggle('hidden');
        else if (act === 'clear') clearPage();
        else if (act === 'whiteboard') {
          jpost('/api/files/board', {}).then(function (f) {
            if (f && f.id) location.hash = '#/view/' + f.id;
          });
        }
        else if (act === 'laser' && d.x !== undefined && d.y !== undefined) {
          var v = viewport();
          showRemoteLaser(d.x * v.w, d.y * v.h);
        }
      }
    }

    function showRemoteLaser(x, y) {
      S.laserDots.push({ x: x, y: y, t: Date.now() });
      laserLoop();
    }

    function syncRemoteState() {
      if (!remoteSession) return;
      var state = {
        title: (S.meta && S.meta.name) || '课堂放映',
        page: S.page,
        total: total(),
        black: blk && !blk.classList.contains('hidden')
      };
      if (remoteWs && remoteWs.readyState === WebSocket.OPEN) {
        remoteWs.send(JSON.stringify({ type: 'state', state: state }));
      } else {
        jpost('/api/remote/' + remoteSession.session_id + '/state', state).catch(function () {});
      }
    }

    function startRemotePolling() {
      if (remotePollTimer || !remoteSession) return;
      var lastIdx = 0;
      setCastStatus('遥控通道已就绪（轮询模式），等待手机接入…');
      remotePollTimer = setInterval(function () {
        if (!remoteSession) return;
        jget('/api/remote/' + remoteSession.session_id + '/poll?after=' + lastIdx).then(function (res) {
          if (res && res.events) {
            res.events.forEach(function (evt) {
              lastIdx++;
              handleRemoteMessage(evt);
            });
          }
        }).catch(function () {});
      }, 800);
    }

    var confettiLoad = null;
    function launchConfetti() {
      function fire() {
        if (!window.confetti) return;
        try {
          var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          if (reduced) return;
          window.confetti({ particleCount: 72, spread: 62, startVelocity: 38, origin: { y: 0.64 },
            colors: ['#ef4444', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7'], disableForReducedMotion: true });
          setTimeout(function () {
            window.confetti({ particleCount: 36, spread: 92, startVelocity: 28, origin: { y: 0.7 },
              colors: ['#f97316', '#facc15', '#34d399', '#60a5fa'], disableForReducedMotion: true });
          }, 180);
        } catch (e) {}
      }
      if (window.confetti) { fire(); return; }
      if (!confettiLoad) confettiLoad = loadScript('/lib/canvas-confetti/confetti.browser.js').then(fire).catch(function () { confettiLoad = null; });
    }

    /* ---------------- 课堂抽选 (名单抽人 & 数字摇号) ---------------- */
    var pickerWidget = el('div', 'fla-widget fla-picker-widget hidden');
    wrap.appendChild(pickerWidget);
    restoreWidgetRect(pickerWidget, 'fla_ms_picker_rect');

    var DEFAULT_ROSTER = [
      '张明', '李华', '王强', '赵敏', '钱程', '孙悦', '周杰', '吴桐',
      '郑浩', '陈晨', '韩雪', '刘洋', '杨柳', '黄晓', '何润', '徐静'
    ];

    function normalizeRoster(items) {
      var out = [], seen = Object.create(null);
      if (!Array.isArray(items)) return out;
      items.slice(0, 10000).forEach(function (item) {
        if (typeof item !== 'string' && typeof item !== 'number') return;
        var name = String(item).trim();
        if (!name || name.length > 40 || seen[name] || out.length >= 5000) return;
        seen[name] = true; out.push(name);
      });
      return out;
    }
    function loadRoster() {
      try {
        var raw = localStorage.getItem('fla_picker_roster');
        if (raw) {
          var arr = normalizeRoster(JSON.parse(raw));
          if (arr.length) return arr;
        }
      } catch (e) {}
      return DEFAULT_ROSTER.slice();
    }

    var PK = {
      mode: 'roster',
      roster: loadRoster(),
      pool: [],
      pickCount: 1,
      noRepeat: true,
      numMin: 1,
      numMax: 50,
      numPool: [],
      numPickCount: 1,
      numNoRepeat: true,
      rolling: false,
      rollTimer: null,
      drawerOpen: false
    };
    PK.pool = PK.roster.slice();
    function initNumPool() {
      PK.numPool = [];
      for (var i = PK.numMin; i <= PK.numMax; i++) PK.numPool.push(i);
    }
    initNumPool();

    function renderPickerWidget() {
      pickerWidget.innerHTML =
        '<div class="fla-widget-head" id="pkHead">' +
          '<div class="fla-wh-title">' + icon('dice', 16) + ' 随机抽选</div>' +
          '<div class="fla-wh-actions">' +
            '<button class="fla-wh-btn close" data-act="close" title="关闭">' + icon('close', 13) + '</button>' +
          '</div>' +
        '</div>' +
        '<div class="fla-widget-tabs">' +
          '<div class="fla-tab-capsule">' +
            '<button class="fla-wtab ' + (PK.mode === 'roster' ? 'on' : '') + '" data-tab="roster">名单抽选</button>' +
            '<button class="fla-wtab ' + (PK.mode === 'number' ? 'on' : '') + '" data-tab="number">数字摇号</button>' +
          '</div>' +
        '</div>' +
        '<div class="fla-widget-body" id="pkRosterBody" style="' + (PK.mode === 'roster' ? '' : 'display:none;') + '">' +
          '<div class="fla-pk-subbar">' +
            '<span id="pkPoolStatus" style="font-weight:600;">候选池: ' + PK.pool.length + ' / ' + PK.roster.length + ' 人</span>' +
            '<div style="display:flex; gap:6px;">' +
              '<button class="fla-tpill" id="pkResetPoolBtn" title="恢复全部人员至候选池">重置</button>' +
              '<button class="fla-tpill" id="pkToggleDrawerBtn">' + (PK.drawerOpen ? '收起名单' : '名单管理') + '</button>' +
            '</div>' +
          '</div>' +
          '<div class="fla-pk-drawer ' + (PK.drawerOpen ? '' : 'hidden') + '" id="pkDrawer">' +
            '<div style="display:flex; justify-content:space-between; align-items:center;">' +
              '<span style="font-size:12px; font-weight:600; color:#0f172a;">学生名单 (Excel / CSV，仅在本机解析)</span>' +
              '<input type="file" id="pkFileInput" accept=".xlsx,.csv,.txt" style="display:none;">' +
              '<button class="fla-tpill" id="pkUploadBtn" style="background:#09090b; color:#fff; border-color:#09090b;">导入表格</button>' +
            '</div>' +
            '<textarea class="fla-pk-textarea" id="pkRosterText" placeholder="每行一个学生姓名，如：&#10;张明&#10;李华&#10;王强">' + escHtml(PK.roster.join('\n')) + '</textarea>' +
            '<div style="display:flex; justify-content:flex-end; gap:6px;">' +
              '<button class="fla-tpill" id="pkDemoBtn">填入示例</button>' +
              '<button class="fla-tpill" id="pkSaveBtn" style="background:#18181b; color:#fff; border-color:#18181b;">保存名单</button>' +
            '</div>' +
          '</div>' +
          '<div class="fla-pk-subbar">' +
            '<div style="display:flex; align-items:center; gap:6px;">' +
              '<span>人数:</span>' +
              '<div class="fla-chip-group" id="pkCountChips">' +
                '<button class="fla-chip ' + (PK.pickCount === 1 ? 'on' : '') + '" data-c="1">1人</button>' +
                '<button class="fla-chip ' + (PK.pickCount === 2 ? 'on' : '') + '" data-c="2">2人</button>' +
                '<button class="fla-chip ' + (PK.pickCount === 3 ? 'on' : '') + '" data-c="3">3人</button>' +
                '<button class="fla-chip ' + (PK.pickCount === 5 ? 'on' : '') + '" data-c="5">5人</button>' +
              '</div>' +
            '</div>' +
            '<label style="display:flex; align-items:center; gap:5px; cursor:pointer; font-size:12px; font-weight:500;">' +
              '<input type="checkbox" id="pkNoRepeatChk"' + (PK.noRepeat ? ' checked' : '') + ' style="accent-color:#09090b;"> 不重复' +
            '</label>' +
          '</div>' +
          '<div class="fla-pk-stage" id="pkStage">' +
            '<div class="fla-pk-card" id="pkCard">等待开始</div>' +
            '<div class="fla-pk-winners-row" id="pkWinnersRow" style="display:none; margin-top:8px;"></div>' +
          '</div>' +
          '<button class="fla-pk-draw-btn ' + (PK.rolling ? 'rolling' : '') + '" id="pkStartBtn">' + (PK.rolling ? '停止' : '开始抽选') + '</button>' +
        '</div>' +
        '<div class="fla-widget-body" id="pkNumberBody" style="' + (PK.mode === 'number' ? '' : 'display:none;') + '">' +
          '<div class="fla-pk-subbar">' +
            '<div style="display:flex; align-items:center; gap:6px;">' +
              '<span>范围:</span>' +
              '<input type="number" id="pkNumMin" value="' + PK.numMin + '" min="0" max="9999" style="width:58px; padding:3px 5px; border-radius:6px; background:#fff; border:1px solid #cbd5e1; color:#0f172a; text-align:center; font-weight:600;">' +
              '<span>~</span>' +
              '<input type="number" id="pkNumMax" value="' + PK.numMax + '" min="1" max="9999" style="width:58px; padding:3px 5px; border-radius:6px; background:#fff; border:1px solid #cbd5e1; color:#0f172a; text-align:center; font-weight:600;">' +
            '</div>' +
            '<button class="fla-tpill" id="pkNumResetPoolBtn">重置</button>' +
          '</div>' +
          '<div class="fla-pk-subbar">' +
            '<div style="display:flex; align-items:center; gap:6px;">' +
              '<span>个数:</span>' +
              '<div class="fla-chip-group" id="pkNumCountChips">' +
                '<button class="fla-chip ' + (PK.numPickCount === 1 ? 'on' : '') + '" data-nc="1">1个</button>' +
                '<button class="fla-chip ' + (PK.numPickCount === 2 ? 'on' : '') + '" data-nc="2">2个</button>' +
                '<button class="fla-chip ' + (PK.numPickCount === 3 ? 'on' : '') + '" data-nc="3">3个</button>' +
              '</div>' +
            '</div>' +
            '<label style="display:flex; align-items:center; gap:5px; cursor:pointer; font-size:12px; font-weight:500;">' +
              '<input type="checkbox" id="pkNumNoRepeatChk"' + (PK.numNoRepeat ? ' checked' : '') + ' style="accent-color:#09090b;"> 不重复' +
            '</label>' +
          '</div>' +
          '<div class="fla-pk-stage" id="pkNumStage">' +
            '<div class="fla-pk-card" id="pkNumCard">' + PK.numMin + ' ~ ' + PK.numMax + '</div>' +
            '<div class="fla-pk-winners-row" id="pkNumWinnersRow" style="display:none; margin-top:8px;"></div>' +
          '</div>' +
          '<button class="fla-pk-draw-btn ' + (PK.rolling ? 'rolling' : '') + '" id="pkNumStartBtn">' + (PK.rolling ? '停止' : '开始摇号') + '</button>' +
        '</div>';

      bindPickerEvents();
      makeDraggable(pickerWidget, pickerWidget.querySelector('#pkHead'), true);
    }

    function bindPickerEvents() {
      pickerWidget.querySelectorAll('.fla-wtab').forEach(function (tab) {
        tab.onclick = function () {
          if (PK.rolling) return;
          PK.mode = tab.getAttribute('data-tab');
          renderPickerWidget();
        };
      });

      var closeBtn = pickerWidget.querySelector('[data-act="close"]');
      if (closeBtn) closeBtn.onclick = function () {
        if (PK.rolling) stopPickerRoll();
        pickerWidget.classList.add('hidden');
      };

      var resetPoolBtn = pickerWidget.querySelector('#pkResetPoolBtn');
      if (resetPoolBtn) resetPoolBtn.onclick = function () {
        PK.pool = PK.roster.slice();
        toast('已恢复全部 ' + PK.roster.length + ' 人至候选池');
        renderPickerWidget();
      };

      var countChips = pickerWidget.querySelectorAll('#pkCountChips .fla-chip');
      countChips.forEach(function (ch) {
        ch.onclick = function () {
          PK.pickCount = parseInt(ch.getAttribute('data-c'), 10) || 1;
          countChips.forEach(function (c) { c.classList.remove('on'); });
          ch.classList.add('on');
        };
      });

      var noRepChk = pickerWidget.querySelector('#pkNoRepeatChk');
      if (noRepChk) noRepChk.onchange = function () {
        PK.noRepeat = noRepChk.checked;
      };

      var startBtn = pickerWidget.querySelector('#pkStartBtn');
      if (startBtn) startBtn.onclick = function () {
        if (PK.rolling) stopPickerRoll(false);
        else startPickerRoll(false);
      };

      var toggleDrawerBtn = pickerWidget.querySelector('#pkToggleDrawerBtn');
      if (toggleDrawerBtn) toggleDrawerBtn.onclick = function () {
        PK.drawerOpen = !PK.drawerOpen;
        var drw = pickerWidget.querySelector('#pkDrawer');
        if (drw) drw.classList.toggle('hidden', !PK.drawerOpen);
        toggleDrawerBtn.textContent = PK.drawerOpen ? '收起名单' : '📁 名单管理';
      };

      var uploadBtn = pickerWidget.querySelector('#pkUploadBtn');
      var fileInp = pickerWidget.querySelector('#pkFileInput');
      if (uploadBtn && fileInp) {
        uploadBtn.onclick = function () { fileInp.click(); };
        fileInp.onchange = function () {
          var f = fileInp.files && fileInp.files[0];
          if (!f) return;
          fileInp.value = '';
          if (f.size > 10 * 1024 * 1024) { toast('文件过大，请选择 10MB 以内的名单文件'); return; }
          toast('正在本机解析名单文件…');
          loadScript('/lib/xlsx/xlsx.full.min.js').then(function () {
            var reader = new FileReader();
            reader.onload = function (evt) {
              try {
                var raw = evt.target.result;
                var isCsv = /\.(csv|txt)$/i.test(f.name || '');
                var book;
                if (isCsv) {
                  var text;
                  try { text = new TextDecoder('utf-8', { fatal: true }).decode(raw); }
                  catch (utfErr) {
                    try { text = new TextDecoder('gb18030').decode(raw); }
                    catch (gbErr) { text = new TextDecoder().decode(raw); }
                  }
                  book = window.XLSX.read(text, { type: 'string', raw: false });
                } else {
                  book = window.XLSX.read(raw, { type: 'array', raw: false });
                }
                var sheet = book.Sheets[book.SheetNames[0]];
                var rows = sheet ? window.XLSX.utils.sheet_to_json(sheet, { header: 1, raw: false, defval: '' }) : [];
                var cleanRows = rows.slice(0, 10001).map(function (row) {
                  return row.map(function (cell) { return String(cell == null ? '' : cell).trim(); });
                }).filter(function (row) { return row.some(Boolean); });
                if (!cleanRows.length) throw new Error('表格为空');
                var header = cleanRows[0];
                var nameCol = -1;
                var nameHeaders = ['姓名', '名字', '学生', 'name'];
                for (var ci = 0; ci < header.length; ci++) {
                  var h = header[ci].toLowerCase();
                  if (nameHeaders.some(function (key) { return h.indexOf(key) >= 0; })) { nameCol = ci; break; }
                }
                var hasHeader = nameCol >= 0;
                if (!hasHeader) {
                  for (var col = 0; col < header.length; col++) {
                    var values = cleanRows.slice(1).map(function (row) { return row[col] || ''; });
                    if (values.some(function (v) { return v && !/^[-+]?\d+(\.\d+)?$/.test(v); })) { nameCol = col; break; }
                  }
                }
                if (nameCol < 0) nameCol = 0;
                if (!hasHeader && /^(学号|编号|id)$/i.test(header[nameCol] || '')) hasHeader = true;
                var names = [];
                var seen = Object.create(null);
                for (var ri = hasHeader ? 1 : 0; ri < cleanRows.length && names.length < 5000; ri++) {
                  var value = (cleanRows[ri][nameCol] || '').trim();
                  if (value && value.length <= 40 && !seen[value]) { seen[value] = true; names.push(value); }
                }
                if (!names.length) { toast('未能识别姓名列，请确认第一列或列标题包含“姓名”'); return; }
                PK.roster = normalizeRoster(names);
                PK.pool = PK.roster.slice();
                try { localStorage.setItem('fla_picker_roster', JSON.stringify(PK.roster)); } catch (storeErr) {}
                toast('本机解析并导入 ' + PK.roster.length + ' 名学生（名单未上传）');
                PK.drawerOpen = false;
                renderPickerWidget();
              } catch (parseErr) {
                toast('名单解析失败: ' + (parseErr.message || '请检查文件格式'));
              }
            };
            reader.onerror = function () { toast('无法读取此文件'); };
            reader.readAsArrayBuffer(f);
          }).catch(function () { toast('名单解析组件加载失败，请检查网络后重试'); });
        };
      }

      var saveBtn = pickerWidget.querySelector('#pkSaveBtn');
      if (saveBtn) saveBtn.onclick = function () {
        var tx = pickerWidget.querySelector('#pkRosterText').value;
        var names = normalizeRoster(tx.split(/[\r\n]+/));
        if (!names.length) { toast('名单不能为空；每个姓名最多 40 个字符，名单最多 5000 人'); return; }
        PK.roster = names;
        PK.pool = PK.roster.slice();
        try { localStorage.setItem('fla_picker_roster', JSON.stringify(PK.roster)); } catch (e) {}
        toast('已保存 ' + PK.roster.length + ' 名学生名单');
        PK.drawerOpen = false;
        renderPickerWidget();
      };

      var demoBtn = pickerWidget.querySelector('#pkDemoBtn');
      if (demoBtn) demoBtn.onclick = function () {
        var txEl = pickerWidget.querySelector('#pkRosterText');
        if (txEl) txEl.value = DEFAULT_ROSTER.join('\n');
      };

      var numMinInp = pickerWidget.querySelector('#pkNumMin');
      var numMaxInp = pickerWidget.querySelector('#pkNumMax');
      if (numMinInp && numMaxInp) {
        numMinInp.onchange = function () {
          PK.numMin = parseInt(numMinInp.value, 10) || 1;
          initNumPool();
        };
        numMaxInp.onchange = function () {
          PK.numMax = parseInt(numMaxInp.value, 10) || 50;
          initNumPool();
        };
      }
      var numResetBtn = pickerWidget.querySelector('#pkNumResetPoolBtn');
      if (numResetBtn) numResetBtn.onclick = function () {
        initNumPool();
        toast('已重置数字池为 ' + PK.numMin + ' ~ ' + PK.numMax);
      };
      var numCountChips = pickerWidget.querySelectorAll('#pkNumCountChips .fla-chip');
      numCountChips.forEach(function (ch) {
        ch.onclick = function () {
          PK.numPickCount = parseInt(ch.getAttribute('data-nc'), 10) || 1;
          numCountChips.forEach(function (c) { c.classList.remove('on'); });
          ch.classList.add('on');
        };
      });
      var numNoRepChk = pickerWidget.querySelector('#pkNumNoRepeatChk');
      if (numNoRepChk) numNoRepChk.onchange = function () {
        PK.numNoRepeat = numNoRepChk.checked;
      };
      var numStartBtn = pickerWidget.querySelector('#pkNumStartBtn');
      if (numStartBtn) numStartBtn.onclick = function () {
        if (PK.rolling) stopPickerRoll(true);
        else startPickerRoll(true);
      };
    }

    function startPickerRoll(isNumber) {
      if (isNumber) {
        if (PK.numNoRepeat && PK.numPool.length < PK.numPickCount) {
          initNumPool();
          toast('数字池已抽完，已自动重置');
        }
      } else {
        if (!PK.roster.length) { toast('请先导入或添加学生名单'); return; }
        if (PK.noRepeat && PK.pool.length < PK.pickCount) {
          PK.pool = PK.roster.slice();
          toast('候选池人数不足，已自动重新装满候选池');
        }
      }

      PK.rolling = true;
      var cardEl = isNumber ? pickerWidget.querySelector('#pkNumCard') : pickerWidget.querySelector('#pkCard');
      var winRow = isNumber ? pickerWidget.querySelector('#pkNumWinnersRow') : pickerWidget.querySelector('#pkWinnersRow');
      var startBtn = isNumber ? pickerWidget.querySelector('#pkNumStartBtn') : pickerWidget.querySelector('#pkStartBtn');
      if (startBtn) {
        startBtn.textContent = '停止';
        startBtn.classList.add('rolling');
      }
      if (winRow) { winRow.style.display = 'none'; winRow.innerHTML = ''; }
      if (cardEl) {
        cardEl.style.display = 'block';
        cardEl.classList.remove('winner');
        cardEl.classList.add('rolling');
      }

      var source = isNumber ? PK.numPool : PK.pool;
      if (!source.length) source = isNumber ? [1, 2, 3] : PK.roster;
      var rollInterval = 45;
      var startTime = Date.now();

      function tick() {
        if (!PK.rolling) return;
        var rIdx = Math.floor(Math.random() * source.length);
        if (cardEl) cardEl.textContent = source[rIdx];
        playTone(320 + Math.random() * 180, 0.04, 'triangle', 0);
        if (Date.now() - startTime > 2200) {
          stopPickerRoll(isNumber);
          return;
        }
        PK.rollTimer = setTimeout(tick, rollInterval);
      }
      tick();
    }

    function stopPickerRoll(isNumber) {
      if (!PK.rolling) return;
      clearTimeout(PK.rollTimer);
      PK.rolling = false;

      var cardEl = isNumber ? pickerWidget.querySelector('#pkNumCard') : pickerWidget.querySelector('#pkCard');
      var winRow = isNumber ? pickerWidget.querySelector('#pkNumWinnersRow') : pickerWidget.querySelector('#pkWinnersRow');
      var startBtn = isNumber ? pickerWidget.querySelector('#pkNumStartBtn') : pickerWidget.querySelector('#pkStartBtn');
      if (startBtn) {
        startBtn.textContent = isNumber ? '开始摇号' : '开始抽选';
        startBtn.classList.remove('rolling');
      }

      var delays = [70, 110, 160, 240, 360];
      var step = 0;
      var source = isNumber ? (PK.numNoRepeat ? PK.numPool : null) : (PK.noRepeat ? PK.pool : null);
      if (!source || !source.length) source = isNumber ? PK.numPool : PK.roster;
      if (!source.length) source = isNumber ? [1, 2, 3] : ['学生'];

      function decelerate() {
        if (step < delays.length) {
          var rIdx = Math.floor(Math.random() * source.length);
          if (cardEl) cardEl.textContent = source[rIdx];
          playTone(400 + step * 60, 0.06, 'sine', 0);
          setTimeout(decelerate, delays[step++]);
        } else {
          finalizePick(isNumber);
        }
      }
      decelerate();
    }

    function finalizePick(isNumber) {
      var cardEl = isNumber ? pickerWidget.querySelector('#pkNumCard') : pickerWidget.querySelector('#pkCard');
      var winRow = isNumber ? pickerWidget.querySelector('#pkNumWinnersRow') : pickerWidget.querySelector('#pkWinnersRow');
      var count = isNumber ? PK.numPickCount : PK.pickCount;
      var winners = [];

      if (isNumber) {
        var avail = PK.numNoRepeat ? PK.numPool : [];
        if (!avail.length) {
          for (var ni = PK.numMin; ni <= PK.numMax; ni++) avail.push(ni);
        }
        for (var i = 0; i < count; i++) {
          if (!avail.length) break;
          var pickIdx = Math.floor(Math.random() * avail.length);
          var val = avail.splice(pickIdx, 1)[0];
          winners.push(val);
        }
        if (PK.numNoRepeat) PK.numPool = avail;
      } else {
        var pool = PK.noRepeat ? PK.pool : PK.roster.slice();
        if (pool.length < count) pool = PK.roster.slice();
        for (var j = 0; j < count; j++) {
          if (!pool.length) break;
          var pIdx = Math.floor(Math.random() * pool.length);
          var name = pool.splice(pIdx, 1)[0];
          winners.push(name);
        }
        if (PK.noRepeat) PK.pool = pool;
      }

      if (cardEl) {
        cardEl.classList.remove('rolling');
        if (winners.length === 1) {
          cardEl.textContent = winners[0];
          cardEl.classList.add('winner');
        } else {
          cardEl.style.display = 'none';
        }
      }

      if (winRow && winners.length > 1) {
        winRow.style.display = 'flex';
        winRow.innerHTML = '';
        winners.forEach(function (w) {
          var badge = el('span', 'fla-pk-win-badge');
          badge.textContent = w;
          winRow.appendChild(badge);
        });
      }

      playAlarm();
      if (winners.length) launchConfetti();

      var poolStatus = pickerWidget.querySelector('#pkPoolStatus');
      if (poolStatus) poolStatus.textContent = '候选池: ' + PK.pool.length + ' / ' + PK.roster.length + ' 人';
    }

    function togglePickerWidget() {
      if (pickerWidget.classList.contains('hidden')) {
        renderPickerWidget();
        pickerWidget.style.right = '70px';
        pickerWidget.style.top = '120px';
        pickerWidget.classList.remove('hidden');
      } else {
        if (PK.rolling) stopPickerRoll(false);
        pickerWidget.classList.add('hidden');
      }
    }

    host.appendChild(wrap);

    /* ==================================================================
     *  几何: 幻灯区域(板书坐标系绑定它)
     * ================================================================== */
    function viewport() {
      var r = wrap.getBoundingClientRect();
      return { w: r.width || window.innerWidth, h: r.height || window.innerHeight };
    }
    /* 手动微调的板书区域(归一化 0..1), 存服务器 */
    function slideRect() {
      var v = viewport();
      if (S.rect) {
        return { x: S.rect[0] * v.w, y: S.rect[1] * v.h, w: S.rect[2] * v.w, h: S.rect[3] * v.h };
      }
      var ar = (S.mv && S.mv.aspect) || 1.777778;
      var isPpt = !S.mv || S.mv.family === 'ppt';
      /* 微软自己的控件条在底部(约 44px), 我方顶栏在上(约 46px) */
      var topI = S.mode === 'present' ? 8 : 52, botI = isPpt ? 46 : 26;
      var aw = v.w, ah = Math.max(80, v.h - topI - botI);
      var k = Math.min(aw / ar, ah) / 1;
      var w = ar * k, h = k;
      if (w > aw) { w = aw; h = aw / ar; }
      return { x: (v.w - w) / 2, y: topI + (ah - h) / 2, w: w, h: h };
    }
    function toVirt(cx, cy) {
      var r = wrap.getBoundingClientRect(), g = slideRect();
      return [(cx - r.left - g.x) / (g.w || 1) * VW, (cy - r.top - g.y) / (g.h || 1) * VH];
    }
    function sizeCanvas() {
      var v = viewport(), dpr = Math.min(window.devicePixelRatio || 1, 2);
      S.dpr = dpr;
      if (ink.width !== Math.round(v.w * dpr) || ink.height !== Math.round(v.h * dpr)) {
        ink.width = Math.round(v.w * dpr); ink.height = Math.round(v.h * dpr);
        laser.width = ink.width; laser.height = ink.height;
      }
      var isPpt = !S.mv || S.mv.family === 'ppt';
      var botI = isPpt ? 46 : 26;
      ink.style.width = v.w + 'px';
      ink.style.height = Math.max(80, v.h - botI) + 'px';
      laser.style.width = v.w + 'px';
      laser.style.height = Math.max(80, v.h - botI) + 'px';
      redraw();
      if (S.bbOpen) bbRedraw();
    }

    /* ==================================================================
     *  笔迹绘制
     * ================================================================== */
    function strokePath(c, pts) {
      c.beginPath();
      if (pts.length === 1) { c.arc(pts[0][0], pts[0][1], .6, 0, Math.PI * 2); return; }
      c.moveTo(pts[0][0], pts[0][1]);
      for (var i = 1; i < pts.length - 1; i++) {
        c.quadraticCurveTo(pts[i][0], pts[i][1], (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2);
      }
      c.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
    }
    function snap45(a, b) {
      var dx = b[0] - a[0], dy = b[1] - a[1];
      var ang = Math.atan2(dy, dx), d = Math.sqrt(dx * dx + dy * dy);
      var s = Math.round(ang / (Math.PI / 4)) * (Math.PI / 4);
      return [a[0] + Math.cos(s) * d, a[1] + Math.sin(s) * d];
    }
    function shapePath(c, s) {
      var a = s.pts[0], b = s.pts[1] || s.pts[0];
      if (s.shape === 'line' || s.shape === 'arrow') b = snap45(a, b);
      c.beginPath();
      if (s.shape === 'line' || s.shape === 'arrow') {
        c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]);
        if (s.shape === 'arrow') {
          var ang = Math.atan2(b[1] - a[1], b[0] - a[0]), hl = Math.max(10, s.width * 3.5);
          c.moveTo(b[0], b[1]); c.lineTo(b[0] - hl * Math.cos(ang - .45), b[1] - hl * Math.sin(ang - .45));
          c.moveTo(b[0], b[1]); c.lineTo(b[0] - hl * Math.cos(ang + .45), b[1] - hl * Math.sin(ang + .45));
        }
      } else if (s.shape === 'rect') {
        c.rect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
      } else if (s.shape === 'ellipse') {
        c.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2, Math.abs(b[1] - a[1]) / 2, 0, 0, Math.PI * 2);
      } else {
        c.moveTo((a[0] + b[0]) / 2, Math.min(a[1], b[1]));
        c.lineTo(Math.max(a[0], b[0]), Math.max(a[1], b[1]));
        c.lineTo(Math.min(a[0], b[0]), Math.max(a[1], b[1]));
        c.closePath();
      }
    }
    function drawStroke(c, s) {
      if (!s || !s.pts || !s.pts.length) return;
      c.save();
      c.lineCap = 'round'; c.lineJoin = 'round';
      if (s.tool === 'text') {
        c.fillStyle = s.color; c.textBaseline = 'top';
        c.font = s.width + 'px ' + FONT;
        var ls = String(s.text || '').split('\n');
        for (var i = 0; i < ls.length; i++) c.fillText(ls[i], s.pts[0][0], s.pts[0][1] + i * s.width * 1.3);
        c.restore(); return;
      }
      if (s.tool === 'shape') {
        c.strokeStyle = s.color; c.lineWidth = s.width; shapePath(c, s); c.stroke(); c.restore(); return;
      }
      if (s.tool === 'marker') c.globalAlpha = .42;
      c.strokeStyle = s.color; c.lineWidth = s.width;
      strokePath(c, s.pts); c.stroke();
      if (s.pts.length === 1) {
        c.fillStyle = s.color; c.beginPath();
        c.arc(s.pts[0][0], s.pts[0][1], (s.width || 2) / 2, 0, Math.PI * 2); c.fill();
      }
      c.restore();
    }
    function bounds(s) {
      var xs = [], ys = [];
      if (s.tool === 'text') {
        var w = String(s.text || '').length * s.width * .62, h = s.width * 1.35;
        xs = [s.pts[0][0], s.pts[0][0] + w]; ys = [s.pts[0][1], s.pts[0][1] + h];
      } else if (s.tool === 'shape') {
        var a = s.pts[0], b = s.pts[1] || s.pts[0];
        if (s.shape === 'line' || s.shape === 'arrow') b = snap45(a, b);
        xs = [a[0], b[0]]; ys = [a[1], b[1]];
      } else {
        (s.pts || []).forEach(function (p) { xs.push(p[0]); ys.push(p[1]); });
      }
      if (!xs.length) return { x0: 0, y0: 0, x1: 0, y1: 0 };
      return { x0: Math.min.apply(null, xs), y0: Math.min.apply(null, ys),
               x1: Math.max.apply(null, xs), y1: Math.max.apply(null, ys) };
    }
    function pid() {
      return S.page <= S.slides ? 'm' + (S.page - 1) : 'x' + (S.page - S.slides - 1);
    }
    function isBoardPage() { return S.page > S.slides; }
    function redraw(preview) {
      var v = viewport(), g = slideRect();
      ictx.setTransform(1, 0, 0, 1, 0, 0);
      ictx.clearRect(0, 0, ink.width, ink.height);
      ictx.save();
      ictx.scale(S.dpr, S.dpr);
      ictx.translate(g.x, g.y);
      ictx.scale(g.w / VW, g.h / VH);
      var arr = S.strokes[pid()] || [];
      for (var i = 0; i < arr.length; i++) {
        drawStroke(ictx, arr[i]);
        if (S.selId && arr[i].id === S.selId) {
          var b = bounds(arr[i]), pad = 10;
          ictx.save();
          ictx.strokeStyle = '#3b82f6'; ictx.lineWidth = 2 * VW / (g.w || VW);
          ictx.setLineDash([8, 6]);
          ictx.strokeRect(b.x0 - pad, b.y0 - pad, b.x1 - b.x0 + pad * 2, b.y1 - b.y0 + pad * 2);
          ictx.restore();
        }
      }
      if (preview) drawStroke(ictx, preview);
      ictx.restore();
      /* 板书区域参考框(仅调整模式) */
      board.style.display = isBoardPage() ? '' : 'none';
      if (isBoardPage()) {
        board.style.background = BOARD_BG[S.cfg.boardBg] || BOARD_BG.w;
        board.style.left = g.x + 'px'; board.style.top = g.y + 'px';
        board.style.width = g.w + 'px'; board.style.height = g.h + 'px';
      }
      void v;
    }

    /* ==================================================================
     *  微软 iframe: 双缓冲 + 预载下一页
     * ================================================================== */
    function getSlideStepCount(slideIndex1Based) {
      if (!S.manifest || !S.manifest.pages) return 0;
      var p = S.manifest.pages[slideIndex1Based - 1];
      if (!p || !p.elements) return 0;
      var gs = {};
      p.elements.forEach(function (e) {
        (e.steps || []).forEach(function (st) {
          if (st.g >= 0) gs[st.g] = 1;
        });
      });
      return Object.keys(gs).length;
    }
    function focusIframe() {
      var f = curFrame();
      if (f && f.contentWindow) {
        try {
          f.focus();
          f.contentWindow.focus();
        } catch (e) { }
      }
    }
    function sendMsAction(fwd) {
      var dir = fwd ? 'next' : 'previous';
      var msgs = [
        { MessageId: fwd ? 'Action_NextSlide' : 'Action_PreviousSlide', SendTime: Date.now(), Values: {} },
        { MessageId: fwd ? 'UI_Next' : 'UI_Prev', SendTime: Date.now(), Values: {} },
        { MessageId: fwd ? 'Action_StepNext' : 'Action_StepPrevious', SendTime: Date.now(), Values: {} },
        { MessageId: 'Action_NavigateTo', SendTime: Date.now(), Values: { direction: dir } },
        { MessageId: 'Send_Keyboard_Event', SendTime: Date.now(), Values: { keyCode: fwd ? 39 : 37, key: fwd ? 'ArrowRight' : 'ArrowLeft' } },
        { type: 'action', action: dir },
        { action: fwd ? 'next' : 'prev' }
      ];
      if (S.frames && S.frames.length) {
        for (var fi = 0; fi < S.frames.length; fi++) {
          var frm = S.frames[fi];
          if (frm && frm.contentWindow) {
            msgs.forEach(function (m) {
              try {
                frm.contentWindow.postMessage(JSON.stringify(m), '*');
                frm.contentWindow.postMessage(m, '*');
              } catch (err) {}
            });
          }
        }
      }
      focusIframe();
    }
    function urlFor(n) {
      if (!S.mv) return '';
      if (!S.mv.deep_link || n <= 1) return S.mv.url;
      var tpl = S.mv.url_tpl || S.mv.url;
      var id = (S.mv.slide_ids && S.mv.slide_ids[n - 1]) || 0;
      return tpl.replace('{n}', n).replace('{id}', id || '');
    }
    function mkFrame() {
      var f = el('iframe', 'ms-frame');
      f.setAttribute('allowfullscreen', 'true');
      f.setAttribute('frameborder', '0');
      f.setAttribute('scrolling', 'no');
      f.style.opacity = '0';
      f.page = 0; f.state = 'idle'; f.token = 0;
      ifrBox.appendChild(f);
      return f;
    }
    function showLoad(on, txt) {
      if (on) {
        if (txt) load.querySelector('#msLoadT').textContent = txt;
        load.classList.remove('hidden');
      } else load.classList.add('hidden');
    }
    function hideFrames() {
      for (var i = 0; i < S.frames.length; i++) S.frames[i].style.opacity = '0';
      showLoad(false);
    }
    /* 把某个 iframe 载入第 n 页; 完成后回调(交叉淡入, 不闪黑屏) */
    function loadInto(f, n, cb) {
      var tk = ++f.token;
      f.state = 'loading'; f.page = n;
      var to = setTimeout(function () {
        if (f.token !== tk) return;
        /* 微软抓取慢: 提示但不放弃, 继续等 onload */
        load.querySelector('#msLoadS').textContent = '微软首次抓取这份文档较慢(30–60 秒), 之后翻页走缓存';
      }, 20000);
      f.onload = function () {
        if (f.token !== tk) return;
        clearTimeout(to);
        f.state = 'ready';
        /* 微软 onload 后还要一会儿才把幻灯片画出来 */
        setTimeout(function () { if (f.token === tk && f.state === 'ready' && cb) cb(f); }, 420);
      };
      f.onerror = function () { if (f.token === tk) { clearTimeout(to); f.state = 'error'; } };
      f.src = urlFor(n);
    }
    function swapTo(f) {
      if (S.dead || !f) return;
      for (var i = 0; i < S.frames.length; i++) {
        var x = S.frames[i];
        if (x === f) { x.style.opacity = '1'; x.style.zIndex = '2'; }
        else { x.style.opacity = '0'; x.style.zIndex = '1'; }
      }
      S.fcur = S.frames.indexOf(f);
      showLoad(false);
      load.querySelector('#msLoadS').textContent = '首次约 30–60 秒; 之后翻页走缓存, 很快';
      preloadNext();
    }
    function frameWith(n) {
      for (var i = 0; i < S.frames.length; i++) {
        if (S.frames[i].page === n && S.frames[i].state !== 'idle') return S.frames[i];
      }
      return null;
    }
    function curFrame() { return S.frames[S.fcur] || null; }
    /* 选一个"可以牺牲"的 iframe: 不是当前显示的, 也不是已预载的下一页 */
    function spareFrame() {
      var i, f;
      for (i = 0; i < S.frames.length; i++) {
        f = S.frames[i];
        if (f !== curFrame() && f.page !== S.page + 1 && f.page !== S.page) return f;
      }
      for (i = 0; i < S.frames.length; i++) {
        f = S.frames[i];
        if (f !== curFrame()) return f;
      }
      return curFrame() || S.frames[0];
    }
    /* 预载下一页 → 前进翻页几乎瞬时(课堂最常用的方向) */
    function preloadNext() {
      if (S.sync !== 'deep' || S.frames.length < 2) return;
      var n = S.page + 1;
      if (n > S.slides || frameWith(n)) return;
      var f = spareFrame();
      if (!f || f === curFrame()) return;
      f.style.opacity = '0'; f.style.zIndex = '1';
      loadInto(f, n, null);
    }
    function showSlide(n) {
      if (n > S.slides) { hideFrames(); return; }  /* 附加板书页: 收起微软画面 */
      S.slidePage = n;
      markFilm();
      var cf = curFrame();
      if (cf && (cf.style.opacity === '0' || cf.style.visibility === 'hidden')) {
        cf.style.visibility = 'visible';
        cf.style.opacity = '1';
        cf.style.zIndex = '2';
      }
      var f = frameWith(n);
      if (f) {
        if (f.state === 'ready') {
          swapTo(f);
          focusIframe();
        } else {
          showLoad(true, '正在切到第 ' + n + ' 页…');
          var tk = f.token;
          var wait = setInterval(function () {
            if (S.dead || f.token !== tk) { clearInterval(wait); return; }
            if (f.state === 'ready') { clearInterval(wait); swapTo(f); focusIframe(); }
          }, 150);
        }
        return;
      }
      f = spareFrame();
      showLoad(true, '正在切到第 ' + n + ' 页…');
      loadInto(f, n, function (ff) {
        swapTo(ff);
        focusIframe();
      });
    }
    function isBoardPageAt(n) { return n > S.slides; }

    /* ==================================================================
     *  翻页 (画布与画面独立 / 关联)
     * ================================================================== */
    function total() { return S.slides + S.extra; }

    /* 纯画布翻页 (与微软完全独立, 绝不重载或回退微软 iframe, 只能靠点击触发) */
    function goCanvasPage(n, quiet) {
      n = clamp(n | 0, 1, total());
      if (n === S.page && !quiet) { updatePageUI(); return; }
      S.page = n;
      S.selId = null;
      redraw();
      if (n > S.slides) {
        hideFrames();
      } else {
        var cur = curFrame();
        if (cur && cur.style.visibility === 'hidden') {
          cur.style.visibility = 'visible';
          cur.style.opacity = '1';
        }
      }
      updatePageUI();
      markFilm();
      saveSoon();
      focusIframe();
    }
    function canvasNextPage() { goCanvasPage(S.page + 1); }
    function canvasPrevPage() { goCanvasPage(S.page - 1); }

    /* 课件全量跳转 (仅在初始载入、显式增加板书页或刷新时调用) */
    function goPage(n, quiet) {
      n = clamp(n | 0, 1, total());
      if (n === S.page && !quiet) { updatePageUI(); return; }
      S.page = n;
      S.selId = null;
      redraw();
      showSlide(n);
      updatePageUI();
      markFilm();
      saveSoon();
    }
    function nextPage() { canvasNextPage(); }
    function prevPage() { canvasPrevPage(); }
    function addPage() {
      S.extra++;
      goPage(total());
      toast('已加板书页: 第 ' + S.page + ' 页 (在幻灯片之后, 可写满整屏)');
    }
    function updatePageUI() {
      var p = wrap.querySelector('#msPage');
      if (p) p.textContent = S.page + ' / ' + total();
      sidePrev.disabled = S.page <= 1;
      sideNext.disabled = S.page >= total();
      var t = wrap.querySelector('#msTitle');
      if (t) t.textContent = (S.meta.name || '课件') + (isBoardPage() ? ' · 板书页' : '');
      syncRemoteState();
    }

    /* ==================================================================
     *  撤销 / 重做 / 清除 / 橡皮
     * ================================================================== */
    function opPush(p, o) {
      (S.ops[p] = S.ops[p] || []).push(o);
      if (S.ops[p].length > 300) S.ops[p].shift();
      S.redos[p] = [];
      saveSoon();
    }
    function opApply(p, o, undo) {
      var arr = S.strokes[p] = S.strokes[p] || [];
      if (o.op === 'add') {
        if (undo) { var i = arr.indexOf(o.s); if (i >= 0) arr.splice(i, 1); } else arr.push(o.s);
      } else if (o.op === 'del') {
        if (undo) arr.push(o.s);
        else { var j = arr.indexOf(o.s); if (j >= 0) arr.splice(j, 1); }
      } else if (o.op === 'move') {
        var s = o.s, A = undo ? o.after : o.before, B = undo ? o.before : o.after;
        if (s.tool === 'text') s.pts = [B.slice()];
        else s.pts = B.map(function (q) { return q.slice(); });
        void A;
      } else if (o.op === 'clear') {
        if (undo) o.list.forEach(function (x) { arr.push(x); });
        else o.list.forEach(function (x) { var k = arr.indexOf(x); if (k >= 0) arr.splice(k, 1); });
      }
    }
    function undo() {
      var p = pid(), a = S.ops[p] || [];
      if (!a.length) { toast('这一页没有可撤销的板书'); return; }
      var o = a.pop(); opApply(p, o, true);
      (S.redos[p] = S.redos[p] || []).push(o);
      S.selId = null; redraw(); saveSoon();
    }
    function redo() {
      var p = pid(), a = S.redos[p] || [];
      if (!a.length) return;
      var o = a.pop(); opApply(p, o, false);
      (S.ops[p] = S.ops[p] || []).push(o);
      redraw(); saveSoon();
    }
    function clearPage() {
      var p = pid(), arr = S.strokes[p] || [];
      if (!arr.length) { toast('本页没有板书'); return; }
      opPush(p, { op: 'clear', list: arr.slice() });
      S.strokes[p] = []; S.selId = null; redraw();
      toast('已清空第 ' + S.page + ' 页板书 (Ctrl+Z 可撤销)');
    }
    function distToSeg(p, a, b) {
      var dx = b[0] - a[0], dy = b[1] - a[1];
      var l2 = dx * dx + dy * dy;
      if (!l2) return Math.hypot(p[0] - a[0], p[1] - a[1]);
      var t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
      return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
    }
    function erasePixelSeg(a, b, r) {
      var p = pid(), arr = S.strokes[p] || [];
      var changed = false, out = [];
      for (var i = 0; i < arr.length; i++) {
        var s = arr[i];
        var half = (s.width || 3) / 2;
        if (s.tool === 'shape' || s.tool === 'text') {
          var bx = bounds(s);
          if (a[0] >= bx.x0 - r && a[0] <= bx.x1 + r && a[1] >= bx.y0 - r && a[1] <= bx.y1 + r) {
            changed = true; opPush(p, { op: 'del', s: s }); continue;
          }
          out.push(s); continue;
        }
        if (!s.pts || !s.pts.length) continue;
        var touched = false, runs = [], curRun = [];
        for (var j = 0; j < s.pts.length; j++) {
          var pt = s.pts[j];
          var d = distToSeg(pt, a, b);
          if (d <= r + half) {
            touched = true;
            if (curRun.length) { runs.push(curRun); curRun = []; }
          } else {
            curRun.push(pt);
          }
        }
        if (curRun.length) runs.push(curRun);
        if (!touched) {
          out.push(s);
        } else {
          changed = true;
          opPush(p, { op: 'del', s: s });
          for (var k = 0; k < runs.length; k++) {
            if (runs[k].length > 0) {
              var piece = { id: uid(), tool: s.tool, color: s.color, width: s.width, pts: runs[k] };
              out.push(piece);
              opPush(p, { op: 'add', s: piece });
            }
          }
        }
      }
      if (changed) { S.strokes[p] = out; redraw(); }
    }
    function eraseAt(vx, vy) {
      var p = pid(), arr = S.strokes[p] || [], w = S.cfg.eraser.width;
      var r = w / 2 + 8, pt = [vx, vy], changed = false;
      for (var i = arr.length - 1; i >= 0; i--) {
        var s = arr[i], hit = false, b = bounds(s);
        var half = (s.width || 3) / 2;
        if (s.tool === 'shape' || s.tool === 'text') {
          hit = vx >= b.x0 - r && vx <= b.x1 + r && vy >= b.y0 - r && vy <= b.y1 + r;
        } else if (s.pts && s.pts.length) {
          if (s.pts.length === 1) {
            hit = Math.hypot(vx - s.pts[0][0], vy - s.pts[0][1]) <= r + half;
          } else {
            for (var j = 0; j < s.pts.length - 1; j++) {
              if (distToSeg(pt, s.pts[j], s.pts[j + 1]) <= r + half) { hit = true; break; }
            }
          }
        }
        if (hit) {
          arr.splice(i, 1); opPush(p, { op: 'del', s: s });
          if (S.selId === s.id) S.selId = null;
          changed = true;
        }
      }
      if (changed) redraw();
    }
    function pickAt(vx, vy) {
      var arr = S.strokes[pid()] || [];
      for (var i = arr.length - 1; i >= 0; i--) {
        var s = arr[i], b = bounds(s), pad = (s.tool === 'shape' || s.tool === 'text') ? 12 : 14 + (s.width || 3) / 2;
        if (vx >= b.x0 - pad && vx <= b.x1 + pad && vy >= b.y0 - pad && vy <= b.y1 + pad) return s;
      }
      return null;
    }

    /* ==================================================================
     *  输入 (指针事件 + 触摸)
     * ================================================================== */
    var textIn = null;
    function commitText() {
      if (!textIn) return;
      var txt = textIn.ta.value.replace(/\s+$/, ''), v = textIn.v;
      textIn.ta.remove(); textIn = null;
      if (!txt) return;
      var s = { id: uid(), tool: 'text', color: S.cfg.text.color, width: S.cfg.text.size, pts: [v], text: txt };
      (S.strokes[pid()] = S.strokes[pid()] || []).push(s);
      opPush(pid(), { op: 'add', s: s });
      redraw();
    }
    var ptDownTime = 0;
    var ptDownPos = [0, 0];
    var ptMovedDist = 0;
    var ptTapAddedStroke = null;

    ink.addEventListener('pointerdown', function (e) {
      if (S.tool === 'cursor') return;
      e.preventDefault();
      try { ink.setPointerCapture(e.pointerId); } catch (err) { }
      ptDownTime = Date.now();
      ptDownPos = [e.clientX, e.clientY];
      ptMovedDist = 0;
      ptTapAddedStroke = null;

      var v = toVirt(e.clientX, e.clientY);
      wakeUI();
      if (S.tool === 'laser') { S.laserDots.push({ x: v[0], y: v[1], t: Date.now() }); laserLoop(); return; }
      if (S.tool === 'eraser') {
        S.lastEraserPt = v;
        if (S.cfg.eraser.mode === 'pixel') erasePixelSeg(v, v, (S.cfg.eraser.width || 28) / 2);
        else eraseAt(v[0], v[1]);
        return;
      }
      if (S.tool === 'text') {
        commitText();
        var g = slideRect(), sc = g.w / VW;
        var ta = el('textarea', 'ms-textin');
        ta.style.left = (g.x + v[0] * sc) + 'px';
        ta.style.top = (g.y + v[1] * sc) + 'px';
        ta.style.fontSize = (S.cfg.text.size * sc) + 'px';
        ta.style.color = S.cfg.text.color;
        ta.rows = 1;
        wrap.appendChild(ta);
        setTimeout(function () { ta.focus(); }, 20);
        textIn = { ta: ta, v: v };
        ta.addEventListener('input', function () { ta.rows = Math.max(1, ta.value.split('\n').length); });
        ta.addEventListener('keydown', function (ev) {
          ev.stopPropagation();
          if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); commitText(); }
          else if (ev.key === 'Escape') { ta.remove(); textIn = null; }
        });
        ta.addEventListener('blur', function () { setTimeout(commitText, 120); });
        return;
      }
      if (S.tool === 'select') {
        var s0 = pickAt(v[0], v[1]);
        S.selId = s0 ? s0.id : null;
        if (s0) S.selOff = { s: s0, start: v, before: s0.pts.map(function (q) { return q.slice(); }) };
        redraw(); return;
      }
      if (S.tool === 'shape') {
        S.drawing = { id: uid(), tool: 'shape', shape: S.cfg.shape.type, color: S.cfg.shape.color,
                      width: S.cfg.shape.width, pts: [v, v.slice()] };
        return;
      }
      S.drawing = { id: uid(), tool: S.tool,
        color: S.tool === 'pen' ? S.cfg.pen.color : S.cfg.marker.color,
        width: S.tool === 'pen' ? S.cfg.pen.width : S.cfg.marker.width, pts: [v] };
      ptTapAddedStroke = S.drawing;
      (S.strokes[pid()] = S.strokes[pid()] || []).push(S.drawing);
      opPush(pid(), { op: 'add', s: S.drawing });
      redraw();
    });
    ink.addEventListener('pointermove', function (e) {
      if (S.tool === 'cursor') return;
      ptMovedDist += Math.abs(e.movementX || 0) + Math.abs(e.movementY || 0);
      var v = toVirt(e.clientX, e.clientY);
      if (S.tool === 'laser') { S.laserDots.push({ x: v[0], y: v[1], t: Date.now() }); laserLoop(); return; }
      if (S.tool === 'eraser') {
        if (e.buttons || e.pointerType === 'touch') {
          var prev = S.lastEraserPt || v;
          S.lastEraserPt = v;
          if (S.cfg.eraser.mode === 'pixel') erasePixelSeg(prev, v, (S.cfg.eraser.width || 28) / 2);
          else eraseAt(v[0], v[1]);
        }
        return;
      }
      if (S.tool === 'select' && S.selOff && (e.buttons || e.pointerType === 'touch')) {
        var s = S.selOff.s, dx = v[0] - S.selOff.start[0], dy = v[1] - S.selOff.start[1];
        s.pts = S.selOff.before.map(function (q) { return [q[0] + dx, q[1] + dy]; });
        redraw(); return;
      }
      if (!S.drawing) return;
      if (S.drawing.tool === 'shape') { S.drawing.pts[1] = v; redraw(S.drawing); return; }
      var L = S.drawing.pts[S.drawing.pts.length - 1];
      if (Math.abs(v[0] - L[0]) + Math.abs(v[1] - L[1]) < 1.1) return;
      S.drawing.pts.push(v);
      redraw();
    });
    function endStroke(e) {
      S.lastEraserPt = null;
      ptTapAddedStroke = null;

      if (S.drawing) {
        if (S.drawing.tool === 'shape') {
          var d = Math.abs(S.drawing.pts[1][0] - S.drawing.pts[0][0]) + Math.abs(S.drawing.pts[1][1] - S.drawing.pts[0][1]);
          if (d > 10) {
            (S.strokes[pid()] = S.strokes[pid()] || []).push(S.drawing);
            opPush(pid(), { op: 'add', s: S.drawing });
          }
        }
        S.drawing = null; redraw(); saveSoon();
      }
      if (S.selOff) {
        var s = S.selOff.s, after = s.pts.map(function (q) { return q.slice(); });
        if (JSON.stringify(after) !== JSON.stringify(S.selOff.before)) {
          opPush(pid(), { op: 'move', s: s, before: S.selOff.before, after: after });
        }
        S.selOff = null; saveSoon();
      }
      focusIframe();
    }
    ink.addEventListener('pointerup', endStroke);
    ink.addEventListener('pointercancel', endStroke);
    ink.addEventListener('pointerleave', function () { if (S.drawing) endStroke(); });

    /* 激光笔 */
    var laserOn = false;
    function laserLoop() {
      if (laserOn) return;
      laserOn = true;
      (function loop() {
        if (S.dead) { laserOn = false; return; }
        requestAnimationFrame(loop);
        if (!S.laserDots.length && S.tool !== 'laser') {
          lctx.setTransform(1, 0, 0, 1, 0, 0); lctx.clearRect(0, 0, laser.width, laser.height);
          laserOn = false; return;
        }
        var now = Date.now(), g = slideRect();
        S.laserDots = S.laserDots.filter(function (d) { return now - d.t < 700; });
        lctx.setTransform(1, 0, 0, 1, 0, 0);
        lctx.clearRect(0, 0, laser.width, laser.height);
        lctx.save();
        lctx.scale(S.dpr, S.dpr);
        lctx.translate(g.x, g.y); lctx.scale(g.w / VW, g.h / VH);
        for (var i = 0; i < S.laserDots.length; i++) {
          var d = S.laserDots[i], age = (now - d.t) / 700;
          lctx.beginPath();
          lctx.fillStyle = 'rgba(239,68,68,' + (1 - age * .85) + ')';
          lctx.shadowColor = 'rgba(239,68,68,.9)'; lctx.shadowBlur = 18;
          lctx.arc(d.x, d.y, 8 - age * 5, 0, Math.PI * 2); lctx.fill();
        }
        lctx.restore();
      })();
    }

    /* ==================================================================
     *  工具切换 + 配置弹窗
     * ================================================================== */
    function setTool(t) {
      commitText();
      S.tool = t;
      ink.style.pointerEvents = (t === 'cursor') ? 'none' : 'auto';
      ink.style.cursor = t === 'cursor' ? 'default' : (t === 'eraser' ? 'cell' : (t === 'select' ? 'default' : 'crosshair'));
      if (t !== 'select') { S.selId = null; redraw(); }
      if (t !== 'laser') { S.laserDots = []; }
      Array.prototype.forEach.call(barL.querySelectorAll('[data-tool]'), function (b) {
        b.classList.toggle('on', b.getAttribute('data-tool') === t);
      });
      pop.classList.add('hidden');
      wakeUI();
      stSet('fla_ms_tool', t);
      focusIframe();
    }
    function swatchRow(colors, cur, onpick) {
      var row = el('div', 'ms-pop-row');
      colors.forEach(function (c) {
        var b = el('button', 'ms-sw' + (c === cur ? ' on' : ''));
        b.style.background = c;
        b.onclick = function () {
          onpick(c);
          Array.prototype.forEach.call(row.querySelectorAll('.ms-sw'), function (x) { x.classList.remove('on'); });
          b.classList.add('on'); saveCfg();
        };
        row.appendChild(b);
      });
      var lab = el('label', 'ms-sw ms-cus'); lab.title = '自定义颜色';
      var ci = el('input'); ci.type = 'color'; ci.value = cur;
      ci.oninput = function () { onpick(ci.value); saveCfg(); };
      lab.appendChild(ci); row.appendChild(lab);
      return row;
    }
    function sliderRow(name, min, max, val, on) {
      var row = el('div', 'ms-pop-row');
      var lb = el('span', 'ms-pop-lb'); lb.textContent = name;
      var rg = el('input'); rg.type = 'range'; rg.min = min; rg.max = max; rg.step = 1;
      var initial = Math.round(clamp(Number(val) || min, min, max));
      rg.value = initial; rg.className = 'ms-range';
      var vb = el('b', 'ms-pop-val'); vb.textContent = initial;
      rg.oninput = function () {
        var next = Math.round(clamp(Number(rg.value) || min, min, max));
        rg.value = next; vb.textContent = next;
        on(next); saveCfg(); redraw();
      };
      row.appendChild(lb); row.appendChild(rg); row.appendChild(vb);
      return row;
    }
    function saveCfg() { stSet('fla_ms_cfg', JSON.stringify(S.cfg)); }
    function buildPop(t) {
      pop.innerHTML = '';
      if (t === 'pen' || t === 'marker') {
        var inkCfg = S.cfg[t], isMarker = t === 'marker';
        pop.appendChild(swatchRow(isMarker ? MARKER_COLORS : PEN_COLORS, inkCfg.color,
          function (c) { inkCfg.color = c; paintInkSample(); }));
        var sample = el('div', 'ms-width-demo');
        sample.innerHTML = '<span>当前粗细</span><i></i>';
        var sampleLine = sample.querySelector('i');
        function paintInkSample() {
          sampleLine.style.height = clamp(inkCfg.width * (isMarker ? 0.62 : 1), 2, 24) + 'px';
          sampleLine.style.background = inkCfg.color;
          sampleLine.style.opacity = isMarker ? '.48' : '1';
        }
        pop.appendChild(sample);
        pop.appendChild(sliderRow('粗细', isMarker ? 6 : 1, isMarker ? 40 : 14, inkCfg.width,
          function (v) { inkCfg.width = v; paintInkSample(); }));
        paintInkSample();
      } else if (t === 'shape') {
        var r = el('div', 'ms-pop-row');
        SHAPE_LIST.forEach(function (sh) {
          var b = el('button', 'ms-shape' + (S.cfg.shape.type === sh[0] ? ' on' : ''));
          b.title = sh[1]; b.innerHTML = svgEl(22, 22, SHAPE_MINI[sh[0]]);
          b.onclick = function () {
            S.cfg.shape.type = sh[0]; saveCfg();
            Array.prototype.forEach.call(r.querySelectorAll('.ms-shape'), function (x) { x.classList.remove('on'); });
            b.classList.add('on');
          };
          r.appendChild(b);
        });
        pop.appendChild(r);
        pop.appendChild(swatchRow(SHAPE_COLORS, S.cfg.shape.color, function (c) { S.cfg.shape.color = c; }));
        pop.appendChild(sliderRow('粗细', 1, 14, S.cfg.shape.width, function (v) { S.cfg.shape.width = v; }));
      } else if (t === 'text') {
        pop.appendChild(swatchRow(TEXT_COLORS, S.cfg.text.color, function (c) { S.cfg.text.color = c; }));
        pop.appendChild(sliderRow('字号', 14, 90, S.cfg.text.size, function (v) { S.cfg.text.size = v; }));
        var h = el('p', 'ms-pop-hint'); h.textContent = '点击画面输入, Enter 确认, Shift+Enter 换行';
        pop.appendChild(h);
      } else if (t === 'eraser') {
        var modeRow = el('div', 'ms-pop-row ms-seg-row');
        modeRow.innerHTML =
          '<button class="ms-seg-b' + (S.cfg.eraser.mode !== 'pixel' ? ' on' : '') + '" data-em="object">对象橡皮</button>' +
          '<button class="ms-seg-b' + (S.cfg.eraser.mode === 'pixel' ? ' on' : '') + '" data-em="pixel">像素橡皮</button>';
        var hint = el('p', 'ms-pop-hint');
        function updateEraserHint() {
          hint.textContent = S.cfg.eraser.mode === 'pixel'
            ? '像素橡皮：精细局部擦除，划过哪截切断哪截 · 设定下方粗细'
            : '对象橡皮：碰触整条笔画删除 · 适合快速清掉整行字或图形';
        }
        updateEraserHint();
        Array.prototype.forEach.call(modeRow.querySelectorAll('button'), function (b) {
          b.onclick = function () {
            S.cfg.eraser.mode = b.getAttribute('data-em');
            saveCfg();
            Array.prototype.forEach.call(modeRow.querySelectorAll('button'), function (x) { x.classList.remove('on'); });
            b.classList.add('on');
            updateEraserHint();
          };
        });
        pop.appendChild(modeRow);
        pop.appendChild(sliderRow('粗细 / 大小', 6, 120, S.cfg.eraser.width || 28, function (v) { S.cfg.eraser.width = v; }));
        var clearBtn = el('button', 'btn danger block');
        clearBtn.textContent = '清空本页板书';
        clearBtn.style.marginTop = '10px';
        clearBtn.onclick = function () { clearPage(); pop.classList.add('hidden'); };
        pop.appendChild(clearBtn);
        pop.appendChild(hint);
      } else { pop.classList.add('hidden'); return; }
      pop.setAttribute('data-tool', t);
      pop.classList.remove('hidden');
    }

    /* ==================================================================
     *  板书区域微调 (对准微软画面里的幻灯片)
     * ================================================================== */
    function enterAlign() {
      var g = slideRect(), v = viewport();
      S.align = { x: g.x / v.w, y: g.y / v.h, w: g.w / v.w, h: g.h / v.h, drag: null };
      rectGuide.classList.remove('hidden');
      paintAlign();
      toast('拖动中间移动板书区域, 拖四角缩放; 完成后点「完成对齐」', 6000);
      var bar = el('div', 'ms-alignbar');
      bar.innerHTML = '<button data-x="ok">完成对齐</button><button data-x="auto">恢复自动</button>' +
        '<button data-x="wide">铺满全屏</button><span>板书区域将保存在这个课件上, 换设备也一致</span>';
      bar.className = 'ms-alignbar';
      wrap.appendChild(bar);
      S.alignBar = bar;
      bar.onclick = function (e) {
        var b = e.target.closest('[data-x]'); if (!b) return;
        var v2 = viewport();
        if (b.getAttribute('data-x') === 'auto') S.rect = null;
        else if (b.getAttribute('data-x') === 'wide') S.rect = [0, 0, 1, 1];
        else S.rect = [S.align.x, S.align.y, S.align.w, S.align.h];
        exitAlign(); redraw(); saveSoon();
      };
      setTool('cursor');
    }
    function paintAlign() {
      var v = viewport(), a = S.align;
      rectGuide.style.left = (a.x * v.w) + 'px';
      rectGuide.style.top = (a.y * v.h) + 'px';
      rectGuide.style.width = (a.w * v.w) + 'px';
      rectGuide.style.height = (a.h * v.h) + 'px';
    }
    function exitAlign() {
      rectGuide.classList.add('hidden');
      if (S.alignBar) { S.alignBar.remove(); S.alignBar = null; }
      S.align = null;
    }
    rectGuide.addEventListener('pointerdown', function (e) {
      if (!S.align) return;
      e.preventDefault();
      var v = viewport(), r = rectGuide.getBoundingClientRect();
      var edge = 22, x = e.clientX, y = e.clientY;
      var mode = '';
      if (Math.abs(x - r.left) < edge) mode += 'w';
      if (Math.abs(x - r.right) < edge) mode += 'e';
      if (Math.abs(y - r.top) < edge) mode += 'n';
      if (Math.abs(y - r.bottom) < edge) mode += 's';
      S.align.drag = { mode: mode || 'move', sx: x / v.w, sy: y / v.h, o: { x: S.align.x, y: S.align.y, w: S.align.w, h: S.align.h } };
      try { rectGuide.setPointerCapture(e.pointerId); } catch (err) { }
    });
    rectGuide.addEventListener('pointermove', function (e) {
      if (!S.align || !S.align.drag) return;
      var v = viewport(), d = S.align.drag, o = d.o;
      var dx = e.clientX / v.w - d.sx, dy = e.clientY / v.h - d.sy;
      if (d.mode === 'move') { S.align.x = clamp(o.x + dx, 0, 1 - o.w); S.align.y = clamp(o.y + dy, 0, 1 - o.h); }
      else {
        if (d.mode.indexOf('e') >= 0) S.align.w = clamp(o.w + dx, .1, 1 - o.x);
        if (d.mode.indexOf('s') >= 0) S.align.h = clamp(o.h + dy, .08, 1 - o.y);
        if (d.mode.indexOf('w') >= 0) { var nw = clamp(o.w - dx, .1, o.x + o.w); S.align.x = o.x + o.w - nw; S.align.w = nw; }
        if (d.mode.indexOf('n') >= 0) { var nh = clamp(o.h - dy, .08, o.y + o.h); S.align.y = o.y + o.h - nh; S.align.h = nh; }
      }
      paintAlign();
      S.rect = [S.align.x, S.align.y, S.align.w, S.align.h];
      redraw();
    });
    rectGuide.addEventListener('pointerup', function () { if (S.align) S.align.drag = null; });

    /* ==================================================================
     *  缩略图导航 (pdf.js 渲染服务端转出的 PDF; 失败则退化成数字格子)
     * ================================================================== */
    function toggleFilm(force) {
      var on = force === undefined ? film.classList.contains('hidden') : force;
      film.classList.toggle('hidden', !on);
      var topF = top.querySelector('[data-a=film]');
      if (topF) topF.classList.toggle('on', on);
      var sideF = barR.querySelector('[data-a=film]');
      if (sideF) sideF.classList.toggle('on', on);
      if (on) { buildFilm(); wakeUI(); }
    }
    function buildFilm() {
      if (film.getAttribute('data-n') === String(S.slides) && film.childNodes.length > 1) { markFilm(); return; }
      film.innerHTML = '<div class="ms-film-head"><b>课件幻灯片缩略图</b>' +
        '<span>点缩略图切换课件幻灯片 (只控制课件, 不影响画布板书)</span>' +
        '<button class="ms-tb" data-f="close">✕</button></div><div class="ms-film-list" id="msFilmList"></div>';
      film.setAttribute('data-n', String(S.slides));
      film.querySelector('[data-f=close]').onclick = function () { toggleFilm(false); };
      var list = film.querySelector('#msFilmList');
      for (var i = 1; i <= S.slides; i++) {
        (function (n) {
          var c = el('button', 'ms-thumb');
          c.setAttribute('data-p', n);
          c.innerHTML = '<span class="ms-thumb-img" id="th' + n + '">' +
            '<i class="ms-thumb-no">' + n + '</i></span>' +
            '<span class="ms-thumb-cap">第 ' + n + ' 页</span>';
          c.onclick = function () {
            showSlide(n);
            goPage(n);
            toast('已切换到第 ' + n + ' 页');
          };
          list.appendChild(c);
        })(i);
      }
      markFilm();
      renderThumbs();
    }
    function markFilm() {
      if (film.classList.contains('hidden')) return;
      var curSlide = S.slidePage || 1;
      Array.prototype.forEach.call(film.querySelectorAll('.ms-thumb'), function (c) {
        var n = +c.getAttribute('data-p');
        c.classList.toggle('on', n === curSlide);
      });
      var cur = film.querySelector('.ms-thumb.on');
      if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    function renderThumbs() {
      if (S.pdfBusy || S.pdf === false) return;
      S.pdfBusy = true;
      loadScript('/lib/pdfjs/pdf.min.js').then(function () {
        var lib = window.pdfjsLib;
        lib.GlobalWorkerOptions.workerSrc = '/lib/pdfjs/pdf.worker.min.js';
        return lib.getDocument({ url: '/api/files/' + S.fid + '/pdf', httpHeaders: AUTH }).promise;
      }).then(function (doc) {
        S.pdf = doc;
        var n = Math.min(doc.numPages, S.slides);
        var i = 1;
        (function next() {
          if (S.dead || i > n) { S.pdfBusy = false; return; }
          doc.getPage(i).then(function (pg) {
            var k = 200 / pg.getViewport({ scale: 1 }).width;
            var vp = pg.getViewport({ scale: k });
            var cv = document.createElement('canvas');
            cv.width = Math.round(vp.width); cv.height = Math.round(vp.height);
            return pg.render({ canvasContext: cv.getContext('2d'), viewport: vp }).promise.then(function () { return cv; });
          }).then(function (cv) {
            var box = film.querySelector('#th' + i);
            if (box) {
              box.innerHTML = '';
              var img = document.createElement('img');
              img.src = cv.toDataURL('image/jpeg', .72);
              box.appendChild(img);
            }
            S.thumbs[i] = 1;
          }).catch(function () { }).then(function () {
            i++; setTimeout(next, 60);
          });
        })();
      }).catch(function () { S.pdf = false; S.pdfBusy = false; });
    }
    function loadScript(src) {
      return new Promise(function (res, rej) {
        if (document.querySelector('script[data-ms="' + src + '"]')) return res();
        var s = document.createElement('script');
        s.src = src; s.setAttribute('data-ms', src);
        s.onload = function () { res(); };
        s.onerror = function () { rej(new Error('组件加载失败: ' + src)); };
        document.head.appendChild(s);
      });
    }

    /* ==================================================================
     *  板中板 (独立小黑板, 不干扰主画面)
     * ================================================================== */
    function bbPid() { return 'bb' + S.bbCur; }
    function bbGeom() {
      var r = bbCv.getBoundingClientRect();
      var k = Math.min(r.width / VW, (r.height - 4) / VH);
      return { k: k, ox: (r.width - VW * k) / 2, oy: (r.height - VH * k) / 2 };
    }
    function bbRedraw() {
      var pg = bnb.querySelector('#bbPg');
      if (pg) pg.textContent = (S.bbCur + 1) + ' / ' + S.bbN;
      var r = bbCv.getBoundingClientRect();
      if (r.width < 4) return;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var pxW = Math.round(r.width * dpr), pxH = Math.round(r.height * dpr);
      if (bbCv.width !== pxW || bbCv.height !== pxH) { bbCv.width = pxW; bbCv.height = pxH; }
      var c = bbCv.getContext('2d'), g = bbGeom();
      c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, bbCv.width, bbCv.height);
      c.save(); c.scale(dpr, dpr); c.translate(g.ox, g.oy); c.scale(g.k, g.k);
      (S.strokes[bbPid()] || []).forEach(function (s) { drawStroke(c, s); });
      c.restore();
    }
    function bbPt(e) {
      var r = bbCv.getBoundingClientRect(), g = bbGeom();
      return [(e.clientX - r.left - g.ox) / g.k, (e.clientY - r.top - g.oy) / g.k];
    }
    var bbDraw = null;
    function bbPush(op) {
      var p = bbPid();
      (S.bbOps[p] = S.bbOps[p] || []).push(op);
      if (S.bbOps[p].length > 200) S.bbOps[p].shift();
      S.bbRedos[p] = [];
      saveSoon();
    }
    bbCv.addEventListener('pointerdown', function (e) {
      e.preventDefault(); e.stopPropagation();
      try { bbCv.setPointerCapture(e.pointerId); } catch (err) { }
      var p = bbPt(e);
      if (S.tool === 'eraser') {
        var arr = S.strokes[bbPid()] || [];
        for (var i = arr.length - 1; i >= 0; i--) {
          var s = arr[i];
          if ((s.pts || []).some(function (q) { return Math.abs(q[0] - p[0]) < 16 && Math.abs(q[1] - p[1]) < 16; })) {
            arr.splice(i, 1); bbPush({ op: 'del', s: s }); bbRedraw(); return;
          }
        }
        return;
      }
      var t = (S.tool === 'marker') ? 'marker' : 'pen';
      bbDraw = { id: uid(), tool: t,
        color: t === 'marker' ? S.cfg.marker.color : S.cfg.pen.color,
        width: t === 'marker' ? S.cfg.marker.width : S.cfg.pen.width, pts: [p] };
      (S.strokes[bbPid()] = S.strokes[bbPid()] || []).push(bbDraw);
      bbPush({ op: 'add', s: bbDraw });
      bbRedraw();
    });
    bbCv.addEventListener('pointermove', function (e) {
      if (!bbDraw) return;
      var p = bbPt(e), L = bbDraw.pts[bbDraw.pts.length - 1];
      if (Math.abs(p[0] - L[0]) + Math.abs(p[1] - L[1]) < 1.4) return;
      bbDraw.pts.push(p); bbRedraw();
    });
    function bbEnd() { bbDraw = null; bbRedraw(); saveSoon(); }
    bbCv.addEventListener('pointerup', bbEnd);
    bbCv.addEventListener('pointercancel', bbEnd);
    bnb.querySelector('.ms-bnb-bar').onclick = function (e) {
      var b = e.target.closest('[data-b]'); if (!b) return;
      var a = b.getAttribute('data-b');
      if (a === 'close') toggleBnb(false);
      else if (a === 'prev') { if (S.bbCur > 0) { S.bbCur--; bbRedraw(); saveSoon(); } }
      else if (a === 'next') { if (S.bbCur < S.bbN - 1) { S.bbCur++; bbRedraw(); saveSoon(); } }
      else if (a === 'add') { S.bbN++; S.bbCur = S.bbN - 1; bbRedraw(); saveSoon(); toast('板中板已加页: ' + S.bbN + ' 页'); }
      else if (a === 'clear') {
        var p = bbPid(), arr = S.strokes[p] || [];
        if (arr.length) { bbPush({ op: 'clear', list: arr.slice() }); S.strokes[p] = []; bbRedraw(); }
      }
    };
    function toggleBnb(force) {
      S.bbOpen = force === undefined ? !S.bbOpen : force;
      bnb.classList.toggle('on', S.bbOpen);
      barR.querySelector('[data-a=bnb]').classList.toggle('on', S.bbOpen);
      if (S.bbOpen) setTimeout(bbRedraw, 260);
    }

    /* ==================================================================
     *  放映驱动模式
     * ================================================================== */
    function setSync(m, quiet) {
      S.sync = 'deep';
      updatePageUI();
    }
    function selfTest() {}

    function goLocal() {
      var u = '/present.html?fid=' + S.fid + '&token=' + encodeURIComponent(S.token);
      saveNow();
      if (S.mode === 'view') window.open(u, '_blank');
      else location.href = u;
    }

    /* 轻量弹窗(放映页是独立文档, 没有主页面的 UI.modal) */
    function dlg(o) {
      var ov = el('div', 'ms-modal-ovl');
      var box = el('div', 'ms-modal');
      box.innerHTML = '<div class="ms-modal-h"><b>' + o.title + '</b><button class="ms-tb" data-c>✕</button></div>' +
        '<div class="ms-modal-b">' + o.html + '</div>';
      ov.appendChild(box);
      wrap.appendChild(ov);
      var body = box.querySelector('.ms-modal-b');
      function close() { ov.remove(); }
      box.querySelector('[data-c]').onclick = close;
      ov.onclick = function (e) { if (e.target === ov) close(); };
      if (o.onMount) o.onMount(body, close);
      return { close: close, el: ov };
    }

    /* ==================================================================
     *  提示条
     * ================================================================== */
    function toast(msg, dur) {
      var t = el('div', 'ms-toast'); t.textContent = msg;
      toasts.appendChild(t);
      requestAnimationFrame(function () { t.classList.add('on'); });
      setTimeout(function () {
        t.classList.remove('on');
        setTimeout(function () { t.remove(); }, 360);
      }, dur || 2800);
    }

    /* ==================================================================
     *  UI 自动隐藏 / 计时 / 全屏
     * ================================================================== */
    function wakeUI() {
      wrap.classList.remove('idle');
      clearTimeout(S.hideT);
      if (S.keepToolbar) return;   /* 常驻模式: 老师上课工具栏不收起 */
      if (S.mode === 'present') {
        S.hideT = setTimeout(function () {
          if (S.tool === 'cursor' && !S.bbOpen && pop.classList.contains('hidden')) wrap.classList.add('idle');
        }, 4200);
      }
    }
    wrap.addEventListener('mousemove', wakeUI);
    wrap.addEventListener('pointerdown', wakeUI);
    function toggleFull() {
      var d = document;
      if (d.fullscreenElement || d.webkitFullscreenElement) {
        (d.exitFullscreen || d.webkitExitFullscreen || function () { }).call(d);
      } else {
        var e = wrap, fn = e.requestFullscreen || e.webkitRequestFullscreen;
        if (fn) { try { fn.call(e); } catch (err) { } }
        else {
          fn = d.documentElement.requestFullscreen || d.documentElement.webkitRequestFullscreen;
          if (fn) { try { fn.call(d.documentElement); } catch (err2) { } }
        }
      }
    }
    S._clock = setInterval(function () {
      if (S.dead) return;
      var t = wrap.querySelector('#msTime');
      if (t) t.textContent = fmtT(Math.floor((Date.now() - S.t0) / 1000));
    }, 1000);

    /* ==================================================================
     *  按钮事件 (委托)
     * ================================================================== */
    wrap.addEventListener('click', function (e) {
      var tb = e.target.closest('[data-tool]');
      if (tb && barL.contains(tb)) {
        var t = tb.getAttribute('data-tool');
        if (t === 'cursor') { setTool('cursor'); return; }
        if (S.tool === t) {
          if (pop.classList.contains('hidden') || pop.getAttribute('data-tool') !== t) buildPop(t);
          else pop.classList.add('hidden');
        } else setTool(t);
        return;
      }
      var b = e.target.closest('[data-a]');
      if (!b) return;
      var a = b.getAttribute('data-a');
      switch (a) {
        case 'exit': doExit(); break;
        case 'newboard':
          jpost('/api/files/board', {}).then(function (f) {
            if (f && f.id) location.hash = '#/view/' + f.id;
          }).catch(function (err) { toast('新建白板失败: ' + err.message); });
          break;
        case 'cast':
          openCastModal();
          break;
        case 'prev': case 'pgprev':
          canvasPrevPage();
          break;
        case 'next': case 'pgnext':
          canvasNextPage();
          break;
        case 'addpage': addPage(); break;
        case 'undo': undo(); break;
        case 'redo': redo(); break;
        case 'clear': clearPage(); break;
        case 'bnb': toggleBnb(); break;
        case 'bg':
          S.cfg.boardBg = S.cfg.boardBg === 'w' ? 'k' : (S.cfg.boardBg === 'k' ? 'g' : 'w');
          saveCfg(); redraw();
          toast('板书底色: ' + ({ w: '白板', k: '黑板', g: '绿黑板' })[S.cfg.boardBg]);
          break;
        case 'black': blk.classList.toggle('hidden'); break;
        case 'local': goLocal(); break;
        case 'present':
          window.open('/present.html?fid=' + S.fid + '&token=' + encodeURIComponent(S.token) + '&track=ms', '_blank');
          break;
        case 'refresh':
          showLoad(true, '正在重新载入微软画面…');
          for (var ri = 0; ri < S.frames.length; ri++) {
            S.frames[ri].token++; S.frames[ri].page = 0; S.frames[ri].state = 'idle';
            S.frames[ri].style.opacity = '0';
          }
          S.fcur = -1;
          goPage(S.page, true);
          toast('已重新载入; 微软侧转换缓存偶尔需要 1–2 分钟才刷新', 5000);
          break;
        case 'dl':
          window.open('/api/files/' + S.fid + '/download?token=' + encodeURIComponent(S.token), '_blank');
          break;
        case 'film': toggleFilm(); break;
        case 'pin':
          S.keepToolbar = !S.keepToolbar;
          stSet('fla_keep_toolbar', S.keepToolbar ? '1' : '0');
          var pinEl = wrap.querySelector('#msPin');
          if (pinEl) {
            pinEl.classList.toggle('on', S.keepToolbar);
            var pinT = pinEl.querySelector('#msPinTxt');
            if (pinT) pinT.textContent = S.keepToolbar ? '工具栏常驻' : '自动收起';
          }
          wakeUI();
          toast(S.keepToolbar ? '已开启【工具栏常驻】(上课不收回)' : '已恢复【自动收起】(移至屏幕边缘唤出)');
          break;
        case 'align': S.align ? exitAlign() : enterAlign(); break;
        case 'time': S.t0 = Date.now(); break;
        case 'stepPrev':
          focusIframe();
          sendMsAction(false);
          toast('课件动画步退：已聚焦课件，按翻页笔或点微软底栏 ‹ 即可步退');
          break;
        case 'stepNext':
          focusIframe();
          sendMsAction(true);
          toast('课件动画步进：已聚焦课件，按翻页笔或点微软底栏 › 即可步进');
          break;
        case 'timer':
          toggleTimerWidget();
          break;
        case 'picker':
          togglePickerWidget();
          break;
        case 'settings':
          toggleSettingsModal();
          break;
        case 'full': toggleFull(); break;
      }
    });
    wrap.querySelector('#msPage').onclick = function () {
      var m = dlg({
        title: '跳到第几页',
        html: '<div class="ms-goto"><input type="number" min="1" max="' + total() + '" value="' + S.page + '">' +
          '<span>/ ' + total() + ' 页 (幻灯片 ' + S.slides + ' 页 + 板书页 ' + S.extra + ' 页)</span></div>' +
          '<div class="ms-goto-grid" id="msGotoGrid"></div>',
        onMount: function (body, close) {
          var inp = body.querySelector('input');
          inp.focus(); inp.select();
          inp.onkeydown = function (ev) {
            if (ev.key === 'Enter') { goPage(+inp.value || 1); close(); }
          };
          var g = body.querySelector('#msGotoGrid');
          for (var i = 1; i <= total(); i++) {
            (function (n) {
              var b = el('button', 'ms-goto-n' + (n === S.page ? ' on' : ''));
              b.textContent = n;
              b.onclick = function () { goPage(n); close(); };
              g.appendChild(b);
            })(i);
          }
          var bb = el('button', 'btn primary');
          bb.textContent = '跳到第 ' + total() + ' 页并加一页板书';
          bb.style.marginTop = '12px';
          bb.onclick = function () { addPage(); close(); };
          body.appendChild(bb);
        }
      });
      return m;
    };
    blk.onclick = function () { blk.classList.add('hidden'); };

    function doExit() {
      saveNow();
      if (opts.onExit) { opts.onExit(); return; }
      try { window.close(); } catch (e) { }
      if (window.history.length > 1) window.history.back();
    }

    /* ==================================================================
     *  微软 PostMessage 通信握手
     * ================================================================== */
    function onMsMessage(e) {
      if (S.dead || !e || !e.data) return;
      var msg = e.data;
      if (typeof msg === 'string') {
        try { msg = JSON.parse(msg); } catch (err) { }
      }
      if (!msg) return;

      // 微软 Office Online 握手协议 (通过 &sftc=1 激活)
      if (msg.MessageId === 'App_IsFrameTrusted') {
        var f = curFrame();
        if (f && f.contentWindow) {
          try {
            f.contentWindow.postMessage(JSON.stringify({
              MessageId: 'Host_IsFrameTrusted',
              SendTime: Date.now(),
              Values: { isTopFrameTrusted: true }
            }), '*');
            f.contentWindow.postMessage(JSON.stringify({
              MessageId: 'Host_PostmessageReady',
              SendTime: Date.now(),
              Values: {}
            }), '*');
          } catch (err2) { }
        }
        return;
      }

      /* 注意: 课件翻页的画布自动跳转功能已移除 (纯手动稳健控制).
       * 完整实现方法与协议代码已归档至 docs/SLIDE_SYNC_BACKUP.md, 便于后续版本平滑恢复. */
    }
    window.addEventListener('message', onMsMessage, false);

    /* ==================================================================
     *  键盘
     * ================================================================== */
    function onKey(e) {
      if (S.dead) return;
      if (textIn) return;
      var tg = e.target;
      if (tg && /INPUT|TEXTAREA|SELECT/.test(tg.tagName)) return;
      var rawKey = e.key || '';
      var k = rawKey.toLowerCase();

      /* ★ 核心规范与翻页笔穿透指令: 
       * 翻页笔及键盘方向键 arrow(up|down|left|right) (PageDown, PageUp, ArrowRight, ArrowLeft, Space 等)
       * 当老师使用画笔、荧光笔或任何绘图工具时，点击翻页笔不会切换白板页码，而是直接向微软 iframe
       * 派发 postMessage 步进指令 (驱动元素级动画步进及幻灯片前进)，并交还焦点给 iframe！ */
      var fwdKey = /^(pagedown|arrowright|arrowdown|next)$/i.test(rawKey) ||
                   e.keyCode === 34 || e.keyCode === 39 || e.keyCode === 40;
      var isPrevKey = /^(pageup|arrowleft|arrowup|prior)$/i.test(rawKey) ||
                      e.keyCode === 33 || e.keyCode === 37 || e.keyCode === 38;
      var isSpaceKey = rawKey === ' ' || e.keyCode === 32;

      if (fwdKey || isSpaceKey || isPrevKey) {
        focusIframe();
        sendMsAction(fwdKey || isSpaceKey);
        return;
      }

      if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); undo(); return; }
      if ((e.ctrlKey || e.metaKey) && k === 'y') { e.preventDefault(); redo(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      if (e.key === 'Escape') {
        if (!blk.classList.contains('hidden')) blk.classList.add('hidden');
        else if (S.align) exitAlign();
        else if (!pop.classList.contains('hidden')) pop.classList.add('hidden');
        else if (S.tool !== 'cursor') setTool('cursor');
        else if (S.mode === 'view') doExit();
      } else if (KEY_TOOL[k]) setTool(S.tool === KEY_TOOL[k] ? 'cursor' : KEY_TOOL[k]);
      else if (k === 'p' || k === 'l') setTool(S.tool === 'laser' ? 'cursor' : 'laser');
      else if (k === 'e') setTool('eraser');
      else if (k === 'b') blk.classList.toggle('hidden');
      else if (k === 'f') toggleFull();
      else if (k === 'g') toggleFilm();
      else if (k === '+' || k === '=') addPage();
      else if (k === 'delete' || k === 'backspace') {
        if (S.selId) {
          var arr = S.strokes[pid()] || [];
          for (var i = 0; i < arr.length; i++) {
            if (arr[i].id === S.selId) { opPush(pid(), { op: 'del', s: arr.splice(i, 1)[0] }); S.selId = null; redraw(); break; }
          }
        }
      }
    }
    window.addEventListener('keydown', onKey, true);

    /* ==================================================================
     *  保存 / 读取
     * ================================================================== */
    function pagesOut() {
      var out = [], i;
      for (i = 0; i < S.slides; i++) out.push({ t: 'ms', n: i, pid: 'm' + i, bg: 'w' });
      for (i = 0; i < S.extra; i++) out.push({ t: 'blank', pid: 'x' + i, w: VW, h: VH, bg: S.cfg.boardBg });
      return out;
    }
    function serialize() {
      return {
        pages: pagesOut(), strokes: S.strokes, bb: { n: S.bbN },
        ms: { rect: S.rect || null, sync: S.sync, extra: S.extra, v: 2 }
      };
    }
    function saveSoon() {
      S.dirty = true;
      clearTimeout(S.saveT);
      S.saveT = setTimeout(saveNow, 800);
    }
    function saveNow() {
      if (!S.dirty) return Promise.resolve();
      S.dirty = false;
      return fetch('/api/files/' + S.fid + '/annotations', {
        method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + S.token },
        body: JSON.stringify(serialize())
      }).then(function (r) { if (!r.ok) S.dirty = true; }).catch(function () { S.dirty = true; });
    }
    function flushSave() {
      if (!S.dirty) return;
      try {
        fetch('/api/files/' + S.fid + '/annotations', {
          method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + S.token },
          body: JSON.stringify(serialize()), keepalive: true
        });
        S.dirty = false;
      } catch (e) { }
    }
    window.addEventListener('beforeunload', flushSave);
    S._autoSave = setInterval(function () { if (S.dead) return; if (S.dirty) saveNow(); }, 15000);

    /* ==================================================================
     *  启动
     * ================================================================== */
    function boot() {
      document.title = (S.meta.name || '课件') + (S.mode === 'present' ? ' - FLA 放映' : ' - FLA');
      var t = wrap.querySelector('#msTitle');
      if (t) t.textContent = S.meta.name || '课件';
      return Promise.all([
        jget('/api/files/' + S.fid + '/ms-view'),
        jget('/api/files/' + S.fid + '/annotations').catch(function () { return null; }),
        jget('/api/files/' + S.fid + '/anim').catch(function () { return null; })
      ]).then(function (rs) {
        S.mv = rs[0]; S.ann = rs[1]; S.manifest = rs[2];
        if (!S.mv || !S.mv.direct) throw new Error('无法取得微软在线视图直链');
        S.slides = Math.max(1, S.mv.pages || 1);
        S.strokes = (S.ann && S.ann.strokes) || {};
        if (S.ann && S.ann.bb && S.ann.bb.n) S.bbN = S.ann.bb.n;
        var ms = (S.ann && S.ann.ms) || null;
        if (ms) {
          if (ms.extra) S.extra = ms.extra | 0;
          if (ms.rect && ms.rect.length === 4) S.rect = ms.rect;
          if (ms.sync && ms.sync !== S.sync) { S.sync = ms.sync; }
        } else if (S.ann && S.ann.pages && S.ann.pages.length > S.slides) {
          /* 旧版数据: 多出来的页当板书页 */
          S.extra = S.ann.pages.length - S.slides;
        }
        /* 页数校正: 直接上传的 PPT 有时 pages 为 0 */
        if (S.mv.slide_ids && S.mv.slide_ids.length && S.slides !== S.mv.slide_ids.length) {
          S.slides = S.mv.slide_ids.length;
        }

        if (!S.mv.ms_ok) {
          toast('微软要求"域名 + 80/443"的公开直链, 当前是 ' + S.mv.direct +
                ' — 请在服务器执行 sudo bash https.sh 域名 (自动文件验证签发证书)', 12000);
        }

        /* iframe 数量: 放映 2 个(乒乓 + 预载), 浏览 1 个(省内存) */
        var nf = S.mode === 'present' ? 2 : 1;
        try { nf = clamp(parseInt(/[?&]frames=(\d)/.exec(location.search)[1], 10) || nf, 1, 3); } catch (e) { }
        for (var i = 0; i < nf; i++) S.frames.push(mkFrame());

        sizeCanvas();
        updatePageUI();
        setTool(st('fla_ms_tool', 'cursor'));
        setSync(S.sync, true);
        goPage(1, true);
        if (S.mode === 'present') {
          setTimeout(function () {
            toast('翻页: ← → / 缩略图(G) — 板书会跟着切页 · 拿笔: 左侧工具条或按 2', 7000);
          }, 1200);
        }
        return stage;
      });
    }

    S._onResize = function () { if (!S.dead) { sizeCanvas(); if (S.align) paintAlign(); } };
    S._onVV = function () { if (!S.dead) sizeCanvas(); };
    window.addEventListener('resize', S._onResize);
    if (window.visualViewport) window.visualViewport.addEventListener('resize', S._onVV);

    var stage = {
      S: S, el: wrap, toast: toast, goPage: goPage, setTool: setTool,
      setSync: setSync, save: saveNow, toggleFilm: toggleFilm,
      destroy: function () {
        if (S.dead) return;
        S.dead = true;
        flushSave();
        stopCountdown();
        stopStopwatch();
        if (PK.rolling) stopPickerRoll(PK.mode === 'number');
        window.removeEventListener('keydown', onKey, true);
        window.removeEventListener('message', onMsMessage, false);
        /* 摘掉全部窗口级监听与定时器: 反复进出放映不累积泄漏 */
        window.removeEventListener('beforeunload', flushSave);
        if (S._onResize) window.removeEventListener('resize', S._onResize);
        if (S._onVV && window.visualViewport) window.visualViewport.removeEventListener('resize', S._onVV);
        if (S._autoSave) { clearInterval(S._autoSave); S._autoSave = 0; }
        if (S._clock) { clearInterval(S._clock); S._clock = 0; }
        clearTimeout(S.saveT); clearTimeout(S.hideT);
        if (textIn) { try { textIn.ta.remove(); } catch (e) { } textIn = null; }
        /* 手机遥控子系统随放映销毁: 先断 WS(屏蔽 onclose 重连) 再停轮询 */
        remoteSession = null;
        if (remoteWs) {
          try { remoteWs.onclose = null; remoteWs.close(); } catch (e) { }
          remoteWs = null;
        }
        if (remotePollTimer) { clearInterval(remotePollTimer); remotePollTimer = null; }
        if (castModal) { try { castModal.remove(); } catch (e) { } castModal = null; }
        try { wrap.remove(); } catch (e) { }
      }
    };
    return boot();
  }

  window.MSStage = { mount: mount, icon: icon };
})();
