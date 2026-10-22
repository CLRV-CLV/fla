/* FLA v1.26 - 放映引擎 (Tier 2): 全屏翻页 + 元素级动画 + 放映中板书
 * v1.26: 修图片/音视频放映(此前一律走 pdf.js → 请求 /pdf 400 → 整页报错崩死);
 *        图片=原生渲染, 音视频=<video>/<audio>+Range流式, 非通用格式(mkv/wmv/avi..)服务端 ffmpeg 转码 mp4;
 *        PDF 加载失败提示友好化(区分 400/409/403)
 * v1.25: 工具栏顶部新增"光标/正常模式"按钮(点击画面即翻 PPT, 笔迹层完全穿透);
 *        修 msTrack 未 return → 原生 onKey 泄漏进微软模式, 按 Esc 直接关掉整个放映页(v1.21 起存在);
 *        补 toastMs 定义(修 v1.23 起 4 处 ReferenceError: 板书加页/开场引导等提示从未显示);
 *        教师认证徽章/证书卡 绚丽重绘(见 app.js + style.css)
 * v1.24: 删除视觉页码同步; 工具栏移到左右两侧竖排(底部全让给微软); 板书页 ‹＋›(独立于PPT);
 *        工具弹窗改为"再点一次工具"打开(修弹窗挡画布吞笔迹); 全套新图标+动画
 * v1.23: 工具栏=白板同款悬浮胶囊(左右两段, 中央完全让给微软翻页控件, 任意分辨率不遮挡);
 *        板中板(独立黑板+独立加页, 滑落/收起); msTrack 载入历史批注(修重开覆盖丢失); 站点背景
 * v1.22: 微软放映底栏开"页码窗"(真页码可见+可OCR) / 视觉同步修复(裁剪对准页码窗+二值化+psm7,
 *        识别结果实时显示✓N/T, 板书自动跟随) / ‹ › 贴纸保持点击穿透
 * 纯 ES5(兼容学校旧浏览器 Chrome 60+), 依赖 pdf.js 2.16 legacy 经典脚本.
 * v1.14: 表格 HTML 渲染 / 不支持内容主 PDF 裁剪元素 / 退出+强调动画 / 禁止整页静态回退
 * 入口: /present.html?fid=ID&token=TOKEN
 * 与查看器(viewer.js)批注数据完全互通: {pages:[{t,n,pid,bg}], strokes:{pid:[...]}}
 */
(function () {
  'use strict';

  var FONT_STACK = '"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Noto Sans SC","Cambria Math","STIX Two Math",sans-serif';
  var BLANK_BG = { w: '#ffffff', k: '#15181d', g: '#1c3b2d' };

  /* ---------- 参数与工具 ---------- */
  function qp(k) {
    var m = new RegExp('[?&]' + k + '=([^&]*)').exec(location.search);
    return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : '';
  }
  function storeToken() {
    try { return localStorage.getItem('token') || ''; } catch (e) { return ''; }
  }
  var FID = parseInt(qp('fid'), 10) || 0;
  var TRACK = qp('track') || '';
  var TOKEN = qp('token') || storeToken();
  var AUTH = { Authorization: 'Bearer ' + TOKEN };

  function jget(url) {
    return fetch(url, { headers: AUTH }).then(function (r) {
      if (r.status === 401) throw new Error('登录已过期, 请回到课件页重新进入放映');
      if (!r.ok) throw new Error('请求失败(' + r.status + ')');
      return r.json();
    });
  }
  function bget(url) {
    return fetch(url, { headers: AUTH }).then(function (r) {
      if (!r.ok) throw new Error('素材加载失败');
      return r.blob();
    });
  }
  function loadScript(src) {
    return new Promise(function (res, rej) {
      if (document.querySelector('script[data-fla="' + src + '"]')) return res();
      var s = document.createElement('script');
      s.src = src; s.setAttribute('data-fla', src);
      s.onload = function () { res(); };
      s.onerror = function () { rej(new Error('组件加载失败: ' + src)); };
      document.head.appendChild(s);
    });
  }
  function el(tag, cls, css) {
    var d = document.createElement(tag);
    if (cls) d.className = cls;
    if (css) d.style.cssText = css;
    return d;
  }
  function fmtT(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }

  /* ---------- 状态 ---------- */
  var S = {
    meta: null, manifest: null, pages: [], strokes: {},
    cur: -1, doc: null, bgDoc: null, bgFailed: false, pdfjs: null,
    imgMap: {}, boxEl: null, box: { w: 960, h: 540 }, dpr: 1,
    pageEls: [], playQueue: [], pi: 0, autoTimers: [],
    tool: 'cursor', pen: { color: '#111111', width: 3 },
    marker: { color: '#fde047', width: 16 }, eraser: { width: 28 },
    drawing: null, compare: false, dirty: false, saveT: 0,
    t0: Date.now(), timerOn: true, hideT: 0, lid: 0,
    seq: 0, lastW: 0, lastH: 0
  };

  function curPage() { return S.pages[S.cur] || { t: 'blank', pid: '_x', w: 1280, h: 720 }; }
  function mPage(n) {
    if (!S.manifest || !S.manifest.pages) return null;
    return S.manifest.pages[n] || null;
  }

  /* ---------- 启动 ---------- */
  function fail(msg) {
    var b = document.getElementById('pboot');
    if (b) { b.textContent = msg; b.className = 'perr'; }
    throw new Error(msg);
  }

  function boot() {
    if (!FID) return fail('参数缺失: 请从课件页点击「放映」进入');
    if (!TOKEN) return fail('请先登录 FLA, 再从课件页点击「放映」');
    Promise.all([
      jget('/api/files/' + FID + '/meta'),
      jget('/api/files/' + FID + '/annotations').catch(function () { return null; }),
      jget('/api/files/' + FID + '/anim').catch(function () { return null; })
    ]).then(function (rs) {
      S.meta = rs[0];
      if (S.meta.status && S.meta.status !== 'ready') return fail('文档尚未转换完成, 请稍后再试');
      if (TRACK !== 'local' && S.meta.kind === 'office' &&
          /^(ppt|pptx|doc|docx|xls|xlsx)$/.test(S.meta.ext || '')) {
        return msTrack();  // 默认使用微软官方高保真在线放映引擎(带板书画笔与互动工具)
      }
      S.manifest = rs[2] && rs[2].pages && rs[2].v >= 2 && rs[2].pages.length ? rs[2] : null;
      var ann = rs[1];
      S.strokes = (ann && ann.strokes) || {};
      if (ann && ann.bb && ann.bb.n) S.bbN = ann.bb.n;
      var pages = (ann && ann.pages && ann.pages.length) ? ann.pages.slice() : null;
      if (pages) {
        var allBlank = pages.every(function (q) { return q.t === 'blank'; });
        var hasInk = false;
        for (var k in S.strokes) if ((S.strokes[k] || []).length) { hasInk = true; break; }
        if (allBlank && !hasInk && (S.meta.pages || 1) > pages.length) pages = null;
      }
      S.pages = pages || [];
      /* v1.26: 图片/音视频不走 pdf.js — 单页原生渲染
       * (修: 此前一律 openPdf → /api/files/{id}/pdf 对图片/媒体返回 400 → 整页报错) */
      if (S.meta.kind === 'image' || S.meta.kind === 'video' || S.meta.kind === 'audio') {
        var mt = S.meta.kind === 'image' ? 'image' : 'media';
        if (!S.pages.length || !S.pages[0] || S.pages[0].t !== mt) S.pages = [{ t: mt, n: 0, pid: 'p0' }];
        return Promise.resolve();
      }
      return openPdf().then(function (numPages) {
        if (!S.pages.length || (S.pages.every(function (q) { return q.t === 'pdf'; }) && S.pages.length !== numPages)) {
          S.pages = [];
          for (var i = 0; i < numPages; i++) S.pages.push({ t: 'pdf', n: i, pid: 'p' + i });
        }
        return prefetchImgs();
      });
    }).then(function () {
      if (S.msMode) return;   /* v1.25: 微软轨道自建 UI, 原生 UI/键盘不再叠加(修 Esc 关标签页) */
      buildUI();
      showPage(0);
    }).catch(function (e) { fail(e.message || '载入失败'); });
  }

  /* ---------- v1.21 微软放映轨道「伴飞层 v3」----------
   * 底部不透明工具栏(白板同款)整条遮住微软控件; 仅 ◀/▶ 两窗透明可穿透;
   * 工具: 选择/笔/荧光笔/几何图形/文本/激光笔/橡皮 + 撤销/重做/清空;
   * 页码: 视觉同步(需 HTTPS) 或 手动; 触摸: touch-action + 指针捕获 */
  /* ---------- v1.27 微软放映轨道 → 委托给 MSStage (web/js/msstage.js) ----------
   * 老大难修复: 微软 Office 在线视图是【跨域 iframe】, 父页面既读不到它当前在
   * 第几页, 也没法命令它翻页 → 以前板书画布永远停在第一页, 与画面脱节。
   * MSStage 改由【我方】掌握页码: 翻页 = 换 iframe.src 的定位参数
   * (wdStartOn / wdSlideId), 双 iframe 乒乓 + 预载下一页, 交叉淡入不闪黑屏;
   * 画布坐标绑定幻灯区域(按真实宽高比 letterbox, 可手动微调并保存到服务器)。
   * 同步模式: deep 我方驱动 / follow 微软自翻(板书用 ‹ › 对齐), 顶栏可切。 */
  function fallbackLocalPresent() {
    S.msMode = false;
    document.body.innerHTML = '<div id="pboot">正在加载本地高保真放映引擎…</div>';
    document.body.style.cssText = 'margin:0;background:#15181d;color:#fff;overflow:hidden;';
    return openPdf().then(function (numPages) {
      if (!S.pages.length || (S.pages.every(function (q) { return q.t === 'pdf'; }) && S.pages.length !== numPages)) {
        S.pages = [];
        for (var i = 0; i < numPages; i++) S.pages.push({ t: 'pdf', n: i, pid: 'p' + i });
      }
      return prefetchImgs();
    }).then(function () {
      buildUI();
      showPage(0);
    }).catch(function (e) {
      msFail(e.message || '本地放映引擎启动失败');
    });
  }

  function msTrack() {
    S.msMode = true;   /* 原生 UI/键盘不再叠加(修 Esc 关标签页) */
    if (!window.MSStage) {
      fallbackLocalPresent();
      return;
    }
    document.body.innerHTML = '';
    document.body.style.cssText = 'margin:0;overflow:hidden;background:#000';
    window.MSStage.mount({
      fid: FID, token: TOKEN, meta: S.meta, mode: 'present',
      mount: document.body, onExit: exitPresent
    }).catch(function (e) {
      console.warn('MSStage mount failed, falling back to local presentation:', e);
      fallbackLocalPresent();
    });
  }
  function msFail(msg) {
    document.body.innerHTML = '';
    document.body.style.cssText = 'margin:0;background:#121316;color:#e8eaef;' +
      'font:500 15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;' +
      'display:grid;place-items:center;padding:28px;text-align:center';
    var d = el('div', '', 'max-width:560px');
    d.innerHTML = '<div style="font-size:40px;line-height:1;margin-bottom:14px">💡</div>' +
      '<b style="font-size:18px">放映引擎提示</b>' +
      '<p style="margin:10px 0 18px;color:rgba(255,255,255,.65)">' +
      String(msg).replace(/[<>&]/g, '') + '</p>' +
      '<button id="msLocalBtn" style="padding:10px 22px;border-radius:10px;border:none;' +
      'background:#09090b;color:#fff;font:600 14px/1 inherit;cursor:pointer;margin-right:8px;border-radius:8px;border:none;">启动本地原生放映</button>' +
      '<button id="msRetry" style="padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.24);' +
      'background:rgba(255,255,255,.1);color:#fff;font:600 13.5px/1 inherit;cursor:pointer;margin-right:8px;">重试在线放映</button>' +
      '<button id="msBack" style="padding:10px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.18);' +
      'background:transparent;color:rgba(255,255,255,.8);font:600 13.5px/1 inherit;cursor:pointer">返回课件库</button>';
    document.body.appendChild(d);
    d.querySelector('#msLocalBtn').onclick = function () { fallbackLocalPresent(); };
    d.querySelector('#msRetry').onclick = function () { location.reload(); };
    d.querySelector('#msBack').onclick = function () {
      if (window.history.length > 1) window.history.back(); else window.close();
    };
  }


  function openPdf() {
    return loadScript('/lib/pdfjs/pdf.min.js').then(function () {
      var lib = window.pdfjsLib;
      if (!lib) throw new Error('PDF 组件加载失败, 请强制刷新(Ctrl+F5)');
      lib.GlobalWorkerOptions.workerSrc = '/lib/pdfjs/pdf.worker.min.js';
      S.pdfjs = lib;
      return lib.getDocument({
        url: '/api/files/' + FID + '/pdf',
        httpHeaders: AUTH
      }).promise.catch(function (e) {   /* v1.26: 友好化 pdf.js 的原始报错 */
        var m = String((e && e.message) || e || '');
        if (m.indexOf('400') >= 0) throw new Error('该文件没有可放映的 PDF (图片/音视频请直接打开, Office 需转换完成)');
        if (m.indexOf('409') >= 0) throw new Error('文档仍在转换中, 请稍后再试');
        if (m.indexOf('403') >= 0) throw new Error('无权访问该文件');
        if (m.indexOf('404') >= 0) throw new Error('文件不存在或已删除');
        throw new Error('PDF 加载失败: ' + m.slice(0, 120));
      });
    }).then(function (doc) { S.doc = doc; return doc.numPages; });
  }

  function ensureBg() {
    if (S.bgDoc) return Promise.resolve(S.bgDoc);
    if (S.bgFailed) return Promise.resolve(null);
    return S.pdfjs.getDocument({
      url: '/api/files/' + FID + '/bgpdf',
      httpHeaders: AUTH
    }).promise.then(function (d) {
      if (d.numPages !== S.doc.numPages) throw new Error('bg pages mismatch');
      S.bgDoc = d; return d;
    }).catch(function () { S.bgFailed = true; return null; });
  }

  function prefetchImgs() {
    if (!S.manifest) return Promise.resolve();
    var jobs = [];
    S.manifest.pages.forEach(function (p) {
      (p.elements || []).forEach(function (e) {
        if (e.kind === 'pic' && e.img && !S.imgMap[e.img]) {
          jobs.push(bget('/api/files/' + FID + '/anim-media/' + e.img).then(function (b) {
            S.imgMap[e.img] = URL.createObjectURL(b);
          }).catch(function () { }));
        }
        if (e.kind === 'table') {
          (e.rows || []).forEach(function (r) {
            (r.cells || []).forEach(function (c) {
              if (c.img && !S.imgMap[c.img]) {
                jobs.push(bget('/api/files/' + FID + '/anim-media/' + c.img).then(function (b) {
                  S.imgMap[c.img] = URL.createObjectURL(b);
                }).catch(function () { }));
              }
            });
          });
        }
      });
    });
    return Promise.all(jobs);
  }

  /* ---------- UI ---------- */
  function buildUI() {
    document.body.innerHTML =
      '<div id="pwrap">' +
      '<div id="pstage"></div>' +
      '<div id="pbar"><i id="pbarin"></i></div>' +
      '<div id="ptop">' +
      '<span id="pexit" title="退出 (Esc)">✕</span>' +
      '<span id="ppg">1 / 1</span>' +
      '<span id="pmode" title="\u6e32\u67d3\u6a21\u5f0f">\u25cf</span>' +
      '<span class="pt-sep"></span>' +
      '<span id="ptime" title="点击重置计时">00:00</span>' +
      '<span class="pt-sep"></span>' +
      '<span id="pbtn-prev" title="上一页 (←)">‹</span>' +
      '<span id="pbtn-next" title="下一步/下一页 (空格)">›</span>' +
      '<span class="pt-sep"></span>' +
      '<span id="pbtn-pen" title="板书 (P)">笔</span>' +
      '<span id="pbtn-laser" title="激光笔 (L)">⦿</span>' +
      '<span id="pbtn-cmp" title="对照原版静态页 (S)">对照</span>' +
      '<span id="pbtn-full" title="全屏 (F)">⛶</span>' +
      '</div>' +
      '<div id="ptools" class="hidden">' +
      '<span class="ptl" data-c="#111111" data-w="3" title="细·黑"></span>' +
      '<span class="ptl" data-c="#111111" data-w="6" title="粗·黑"></span>' +
      '<span class="ptl" data-c="#ef4444" data-w="4" title="红"></span>' +
      '<span class="ptl" data-c="#facc15" data-w="4" title="黄"></span>' +
      '<span class="ptl" data-c="#ffffff" data-w="4" title="白"></span>' +
      '<span class="pt-sep"></span>' +
      '<span id="ptl-marker" title="荧光笔">荧光</span>' +
      '<span id="ptl-eraser" title="橡皮(对象)">擦</span>' +
      '<span id="ptl-clear" title="清空本页板书">清空</span>' +
      '<span id="ptl-off" title="收起 (P)">✓</span>' +
      '</div>' +
      '<div id="phint">点击 / 空格：下一步 · P：板书 · S：对照原版 · B：黑屏</div>' +
      '<div id="pblack"></div>' +
      '<div id="plaser"></div>' +
      '</div>';
    document.getElementById('pexit').onclick = exitPresent;
    document.getElementById('pbtn-prev').onclick = function (e) { e.stopPropagation(); prevPage(); };
    document.getElementById('pbtn-next').onclick = function (e) { e.stopPropagation(); advance(); };
    document.getElementById('pbtn-pen').onclick = function (e) { e.stopPropagation(); toggleTools(); };
    document.getElementById('pbtn-laser').onclick = function (e) { e.stopPropagation(); setTool(S.tool === 'laser' ? 'cursor' : 'laser'); };
    document.getElementById('pbtn-cmp').onclick = function (e) { e.stopPropagation(); toggleCompare(); };
    document.getElementById('pbtn-full').onclick = function (e) { e.stopPropagation(); toggleFull(); };
    document.getElementById('ptime').onclick = function (e) { e.stopPropagation(); S.t0 = Date.now(); };
    var ts = document.getElementById('ptools');
    Array.prototype.forEach.call(ts.querySelectorAll('.ptl'), function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        S.pen.color = b.getAttribute('data-c'); S.pen.width = parseInt(b.getAttribute('data-w'), 10);
        setTool('pen');
        Array.prototype.forEach.call(ts.querySelectorAll('.ptl'), function (x) { x.className = 'ptl'; });
        b.className = 'ptl on';
      };
    });
    document.getElementById('ptl-marker').onclick = function (e) { e.stopPropagation(); setTool('marker'); };
    document.getElementById('ptl-eraser').onclick = function (e) { e.stopPropagation(); setTool('eraser'); };
    document.getElementById('ptl-clear').onclick = function (e) {
      e.stopPropagation(); delete S.strokes[curPage().pid]; inkRedraw(); saveSoon();
    };
    document.getElementById('ptl-off').onclick = function (e) { e.stopPropagation(); setTool('cursor'); };

    document.getElementById('pwrap').addEventListener('click', onStageClick);
    document.getElementById('pstage').addEventListener('pointerdown', onDown);
    document.getElementById('pstage').addEventListener('pointermove', onMove);
    document.getElementById('pstage').addEventListener('pointerup', onUp);
    document.getElementById('pstage').addEventListener('pointercancel', onUp);
    window.addEventListener('keydown', onKey);
    var rzT = 0;
    window.addEventListener('resize', function () {
      clearTimeout(rzT);
      rzT = setTimeout(function () {
        if (S.cur < 0) return;
        var st = stageSize();
        if (st.w === S.lastW && st.h === S.lastH) return;
        showPage(S.cur);
      }, 160);
    });
    document.addEventListener('pointermove', wakeUI);
    window.addEventListener('beforeunload', flushSave);

    setInterval(function () {
      var ptEl = document.getElementById('ptime');
      if (S.timerOn && ptEl) ptEl.textContent = fmtT(Math.floor((Date.now() - S.t0) / 1000));
    }, 1000);
    setTimeout(function () { var h = document.getElementById('phint'); if (h) h.classList.add('off'); }, 4200);
    setModeDot();
    wakeUI();
  }

  function exitPresent() {
    flushSave();
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      (document.exitFullscreen || document.webkitExitFullscreen || function () { }).call(document);
    }
    if (window.history.length > 1) window.history.back(); else window.close();
  }
  function toggleFull() {
    var d = document.documentElement;
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      (document.exitFullscreen || document.webkitExitFullscreen || function () { }).call(document);
    } else {
      var fn = d.requestFullscreen || d.webkitRequestFullscreen;
      if (fn) fn.call(d);
    }
  }
  function toggleTools() {
    var t = document.getElementById('ptools');
    t.classList.toggle('hidden');
    if (!t.classList.contains('hidden') && S.tool === 'cursor') setTool('pen');
  }
  function setTool(t) {
    S.tool = t;
    document.getElementById('ptools').classList.toggle('hidden', t === 'cursor' || t === 'laser');
    var pen = document.getElementById('pbtn-pen');
    if (pen) pen.classList.toggle('on', t !== 'cursor' && t !== 'laser');
    var lz = document.getElementById('pbtn-laser');
    if (lz) lz.classList.toggle('on', t === 'laser');
    var st = document.getElementById('pstage');
    st.style.cursor = t === 'eraser' ? 'cell' : (t === 'cursor' ? 'default' : 'crosshair');
    var dot = document.getElementById('plaser');
    if (dot) dot.style.display = t === 'laser' ? 'block' : 'none';
  }
  function toggleCompare() {
    S.compare = !S.compare;
    document.getElementById('pbtn-cmp').classList.toggle('on', S.compare);
    showPage(S.cur);
  }
  function wakeUI() {
    var top = document.getElementById('ptop');
    if (top) top.classList.remove('idle');
    var bar = document.getElementById('pbar');
    if (bar) bar.classList.remove('idle');
    clearTimeout(S.hideT);
    S.hideT = setTimeout(function () {
      if (S.tool === 'cursor') {
        var t = document.getElementById('ptop'); if (t) t.classList.add('idle');
        var b = document.getElementById('pbar'); if (b) b.classList.add('idle');
      }
    }, 2600);
  }

  /* ---------- 页面呈现 ---------- */
  function stageSize() {
    return { w: window.innerWidth, h: window.innerHeight };
  }

  function showPage(i) {
    if (i < 0 || i >= S.pages.length) return;
    flushAuto();
    flushSave();
    var p = S.pages[i];
    S.cur = i;
    var pw = p.w || 1280, ph = p.h || 720;
    var old = S.boxEl;
    var mySeq = ++S.seq;

    var box = el('div', 'ppage');
    box.style.width = '0px'; box.style.height = '0px';
    document.getElementById('pstage').appendChild(box);
    S.boxEl = box;

    var canvas = el('canvas', 'pbg');
    box.appendChild(canvas);
    var layer = el('div', 'pelayer');
    box.appendChild(layer);
    var ink = el('canvas', 'pink');
    box.appendChild(ink);

    S.ink = ink; S.ictx = ink.getContext('2d');
    S.pageEls = []; S.playQueue = []; S.pi = 0;

    var render;
    if (p.t === 'pdf') {
      render = renderPdfPage(p, canvas, box, layer, mySeq);
    } else if (p.t === 'blank') {
      render = renderBlankPage(p, canvas, box, layer);
    } else if (p.t === 'image') {
      render = renderImagePage(p, canvas, box);
    } else if (p.t === 'media') {
      render = renderMediaPage(p, canvas, box);
    } else {
      render = renderStubPage(p, canvas, box, layer);
    }
    render.then(function () {
      if (mySeq !== S.seq) { if (box.parentNode) box.parentNode.removeChild(box); return; }
      layoutInk(p);
      inkRedraw();
      transSwap(old, box, p.t === 'pdf' ? mPage(p.n) : null);
      document.getElementById('ppg').textContent = (i + 1) + ' / ' + S.pages.length;
      document.getElementById('pbarin').style.width = ((i + 1) / S.pages.length * 100) + '%';
      playAuto();
      setModeDot();
    }).catch(function (e) {
      if (mySeq !== S.seq) { if (box.parentNode) box.parentNode.removeChild(box); return; }
      box.innerHTML = '<div class="perr" style="display:flex">' + (e.message || '页面渲染失败') + '</div>';
      transSwap(old, box, null);
    });
  }

  function applyBox(pw, ph) {
    var st = stageSize(), ar = pw / ph;
    var w = st.w, h = st.h;
    if (w / h > ar) w = h * ar; else h = w / ar;
    S.box = { w: Math.round(w), h: Math.round(h) };
    S.lastW = st.w; S.lastH = st.h;
    S.boxEl.style.width = S.box.w + 'px';
    S.boxEl.style.height = S.box.h + 'px';
    return S.box;
  }

  function renderPdfPage(p, canvas, box, layer, mySeq) {
    var n = p.n || 0, mp = mPage(n);
    var hasElements = mp && mp.elements && mp.elements.length;
    var useBg = hasElements && !S.compare && !mp.bgfail && !S.bgFailed;
    var docP = useBg ? ensureBg() : Promise.resolve(S.doc);
    return docP.then(function (doc) {
      if (mySeq !== S.seq) throw { stale: true };
      var targetDoc = (useBg && doc) ? doc : S.doc;
      var pageNum = Math.min(n + 1, targetDoc.numPages);
      return targetDoc.getPage(pageNum);
    }).then(function (page) {
      if (mySeq !== S.seq) throw { stale: true };
      var vp = page.getViewport({ scale: 1 });
      p.w = vp.width; p.h = vp.height;
      applyBox(p.w, p.h);
      var cssScale = S.box.w / p.w;
      var vp2 = page.getViewport({ scale: cssScale });
      S.dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(vp2.width * S.dpr);
      canvas.height = Math.round(vp2.height * S.dpr);
      canvas.style.width = S.box.w + 'px';
      canvas.style.height = S.box.h + 'px';
      var ctx = canvas.getContext('2d');
      return page.render({
        canvasContext: ctx, viewport: vp2,
        transform: [S.dpr, 0, 0, S.dpr, 0, 0]
      }).promise;
    }).then(function () {
      if (mySeq !== S.seq) throw { stale: true };
      if (hasElements && !S.compare) return buildElements(mp, layer, p, useBg);
    });
  }

  function renderBlankPage(p, canvas, box, layer) {
    p.w = p.w || 1280; p.h = p.h || 720;
    applyBox(p.w, p.h);
    S.dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(S.box.w * S.dpr);
    canvas.height = Math.round(S.box.h * S.dpr);
    canvas.style.width = S.box.w + 'px';
    canvas.style.height = S.box.h + 'px';
    var ctx = canvas.getContext('2d');
    ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
    ctx.fillStyle = BLANK_BG[p.bg] || BLANK_BG.w;
    ctx.fillRect(0, 0, S.box.w, S.box.h);
    return Promise.resolve();
  }

  /* v1.26: 图片页 — 原生 <img> 渲染(清晰且无 canvas 尺寸上限问题) */
  function renderImagePage(p, canvas, box) {
    canvas.style.display = 'none';
    var img = el('img', 'pimg');
    img.alt = '';
    return bget('/api/files/' + FID + '/raw').then(function (blob) {
      return new Promise(function (res, rej) {
        var url = URL.createObjectURL(blob);
        img.onload = function () {
          p.w = img.naturalWidth || 1280; p.h = img.naturalHeight || 720;
          applyBox(p.w, p.h);
          img.style.width = S.box.w + 'px';
          img.style.height = S.box.h + 'px';
          res();
        };
        img.onerror = function () { rej(new Error('图片加载失败')); };
        img.src = url;
      });
    }).then(function () {
      box.insertBefore(img, box.firstChild);
      return;
    });
  }

  /* v1.26: 音视频页 — 原生控件 + Range 流式(自动用服务端转码后的 mp4) */
  function renderMediaPage(p, canvas, box) {
    canvas.style.display = 'none';
    var src = '/api/files/' + FID + '/raw?token=' + encodeURIComponent(TOKEN);
    var isVideo = S.meta.kind === 'video';
    return new Promise(function (res) {
      if (isVideo) {
        var v = el('video', 'pmedia');
        v.controls = true; v.playsInline = true; v.preload = 'metadata';
        v.src = src;
        v.addEventListener('loadedmetadata', function () {
          p.w = v.videoWidth || 1280; p.h = v.videoHeight || 720;
          applyBox(p.w, p.h);
          v.style.width = S.box.w + 'px';
          v.style.height = S.box.h + 'px';
          res();
        });
        v.addEventListener('error', function () {
          p.w = 1280; p.h = 720; applyBox(p.w, p.h);
          res();  /* 仍显示播放器(浏览器自带"无法播放"提示), 不整页报错 */
        });
        /* 点击播放器不触发翻页 */
        v.addEventListener('click', function (e) { e.stopPropagation(); });
        box.insertBefore(v, box.firstChild);
      } else {
        p.w = 1280; p.h = 720;
        applyBox(p.w, p.h);
        var w = el('div', 'paudio');
        w.innerHTML = '<div class="paudio-ico">♪</div>';
        var a = el('audio');
        a.controls = true; a.preload = 'metadata'; a.src = src;
        w.appendChild(a);
        w.addEventListener('click', function (e) { e.stopPropagation(); });
        box.insertBefore(w, box.firstChild);
        res();
      }
    });
  }

  function renderStubPage(p, canvas, box, layer) {
    p.w = 1280; p.h = 720;
    applyBox(p.w, p.h);
    S.dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(S.box.w * S.dpr);
    canvas.height = Math.round(S.box.h * S.dpr);
    canvas.style.width = S.box.w + 'px';
    canvas.style.height = S.box.h + 'px';
    var ctx = canvas.getContext('2d');
    ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
    ctx.fillStyle = '#101114';
    ctx.fillRect(0, 0, S.box.w, S.box.h);
    ctx.fillStyle = 'rgba(255,255,255,.55)';
    ctx.font = '16px ' + FONT_STACK;
    ctx.textAlign = 'center';
    ctx.fillText(p.t === 'image' ? '图片页 · 请在查看器中查看原图' : '音视频页 · 请在查看器中播放', S.box.w / 2, S.box.h / 2);
    return Promise.resolve();
  }

  /* ---------- 度量换算 ---------- */
  function ptFactor() {
    var sh = (S.manifest && S.manifest.slideH) || 6858000;
    return S.box.h / (sh / 12700);
  }
  function emuFactor() {
    var sw = (S.manifest && S.manifest.slideW) || 12192000;
    return S.box.w / sw;
  }

  /* ---------- 动画元素层 ---------- */
  function mainPageCanvas(n, p) {
    /* 主 PDF 页 → 离屏画布(2x 清晰度), 供 crop 元素裁剪 */
    return S.doc.getPage(n + 1).then(function (page) {
      var vp = page.getViewport({ scale: 1 });
      var scale = (2 * S.box.w) / vp.width;
      var vp2 = page.getViewport({ scale: scale });
      var c = el('canvas');
      c.width = Math.round(vp2.width);
      c.height = Math.round(vp2.height);
      return page.render({ canvasContext: c.getContext('2d'), viewport: vp2 }).promise.then(function () {
        return c;
      });
    });
  }

  function buildElements(mp, layer, p, useBg) {
    var ef = emuFactor();
    var recs = [];
    (mp.elements || []).forEach(function (e) {
      var d = el('div', 'pel');
      d.style.left = (e.x * 100) + '%';
      d.style.top = (e.y * 100) + '%';
      d.style.width = (e.w * 100) + '%';
      d.style.height = (e.h * 100) + '%';
      if (e.rot) d.style.transform = 'rotate(' + e.rot + 'deg)';
      var hasWipe = (e.steps || []).some(function (st) { return st.t === 'in' && st.e === 'wipe'; });
      if (hasWipe) d.style.overflow = 'hidden';
      var inner = el('div', 'pel-in');
      d.appendChild(inner);
      buildContent(e, inner, ef);
      var visibleInitially = !(e.steps || []).some(function (st) { return st.t === 'in'; });
      d.style.visibility = visibleInitially ? 'visible' : 'hidden';
      if (!visibleInitially && !useBg) {
        d.setAttribute('data-masked', '1');
        d.style.backgroundColor = '#ffffff';
      }
      layer.appendChild(d);
      recs.push({ e: e, d: d, inner: inner });
    });
    S.pageEls = recs;
    var gs = {};
    recs.forEach(function (r) {
      (r.e.steps || []).forEach(function (st) {
        if (st.g >= 0) gs[st.g] = 1;
      });
    });
    S.playQueue = Object.keys(gs).map(Number).sort(function (a, b) { return a - b; });
    S.pi = 0;
    var cropRecs = recs.filter(function (r) { return r.e.kind === 'crop'; });
    if (!cropRecs.length) return Promise.resolve();
    return mainPageCanvas(p.n || 0, p).then(function (cv) {
      if (!cv) return;
      cropRecs.forEach(function (r) {
        try {
          var c2 = el('canvas');
          c2.width = Math.max(2, Math.round(r.e.w * cv.width));
          c2.height = Math.max(2, Math.round(r.e.h * cv.height));
          var cx = c2.getContext('2d');
          cx.drawImage(cv,
            Math.round(r.e.x * cv.width), Math.round(r.e.y * cv.height),
            c2.width, c2.height,
            0, 0, c2.width, c2.height);
          var img = el('img');
          img.src = c2.toDataURL('image/png');
          img.style.width = '100%'; img.style.height = '100%';
          img.draggable = false;
          r.inner.appendChild(img);
        } catch (err) { }
      });
    }).catch(function () { });
  }

  function buildContent(e, inner, ef) {
    if (e.kind === 'pic') {
      var url = S.imgMap[e.img];
      if (url) {
        var im = el('img');
        im.src = url; im.style.width = '100%'; im.style.height = '100%';
        im.style.objectFit = 'fill'; im.draggable = false;
        inner.appendChild(im);
      } else {
        inner.style.border = '1px dashed rgba(120,120,130,.5)';
      }
      return;
    }
    if (e.kind === 'crop') {
      inner.style.background = 'rgba(148,163,184,.08)';
      return; // 图片由 buildElements 异步裁剪填充
    }
    if (e.kind === 'table') {
      buildTable(e, inner, ef);
      return;
    }
    if (e.kind === 'rect' || e.kind === 'ellipse') {
      var sh = el('div', 'pel-shape');
      if (e.fill) sh.style.background = e.fill;
      if (e.line && e.lw) sh.style.border = e.lw + 'pt solid ' + e.line;
      if (e.kind === 'ellipse') sh.style.borderRadius = '50%';
      inner.appendChild(sh);
      if (e.paras) appendText(e, inner, ef);
      return;
    }
    if (e.kind === 'shape') {
      if (e.rr) {
        var rb = el('div', 'pel-shape');
        if (e.fill) rb.style.background = e.fill;
        if (e.line && e.lw) rb.style.border = e.lw + 'pt solid ' + e.line;
        var bw = e.w * S.box.w, bh = e.h * S.box.h;
        rb.style.borderRadius = Math.min(bw, bh) * 0.1667 + 'px';
        inner.appendChild(rb);
      } else if (e.pts) {
        var svgNS = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('viewBox', '0 0 100 100');
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.setAttribute('class', 'pel-svg');
        var poly = document.createElementNS(svgNS, 'polygon');
        poly.setAttribute('points', e.pts.map(function (p) { return p[0] + ',' + p[1]; }).join(' '));
        poly.setAttribute('fill', e.fill || 'none');
        if (e.line && e.lw) {
          poly.setAttribute('stroke', e.line);
          poly.setAttribute('stroke-width', Math.max(1, e.lw));
          poly.setAttribute('vector-effect', 'non-scaling-stroke');
        } else {
          poly.setAttribute('stroke', 'none');
        }
        svg.appendChild(poly);
        inner.appendChild(svg);
      }
      if (e.paras) appendText(e, inner, ef);
      return;
    }
    // text
    appendText(e, inner, ef);
  }

  function appendText(e, inner, ef) {
    var t = el('div', 'pel-text');
    t.style.justifyContent = ({ t: 'flex-start', ctr: 'center', b: 'flex-end' })[e.anchor || 't'];
    if (e.nowrap) t.style.whiteSpace = 'nowrap';
    if (e.ins) {
      t.style.paddingTop = (e.ins[1] * ef) + 'px';
      t.style.paddingRight = (e.ins[2] * ef) + 'px';
      t.style.paddingBottom = (e.ins[3] * ef) + 'px';
      t.style.paddingLeft = (e.ins[0] * ef) + 'px';
    }
    parasInto(t, e.paras, (e.fitScale || 1), ef, e.tc);
    inner.appendChild(t);
  }

  function parasInto(holder, paras, fitScale, ef, tc) {
    var kf = ptFactor();
    var numCtr = 0;
    (paras || []).forEach(function (pa) {
      var pd = el('div', 'pel-para');
      pd.style.textAlign = pa.align === 'ctr' ? 'center' : pa.align === 'r' ? 'right' : 'left';
      if (pa.lnSpc) pd.style.lineHeight = String(pa.lnSpc);
      else if (pa.lnSpcPt) pd.style.lineHeight = pa.lnSpcPt + 'pt';
      if (pa.sb) pd.style.marginTop = (pa.sb * kf) + 'px';
      if (pa.sa) pd.style.marginBottom = (pa.sa * kf) + 'px';
      var marL = (pa.marL || 0) * (ef || 0);
      if (marL > 0) pd.style.paddingLeft = marL + 'px';
      if (pa.bu) {
        if (pa.bu === 'num') numCtr++;
        var bsp = el('span');
        bsp.textContent = (pa.bu === 'num' ? (numCtr + '.') : pa.bu) + '\u00A0';
        if (pa.ind) bsp.style.marginLeft = (pa.ind * (ef || 0)) + 'px';
        bsp.style.color = (pa.runs && pa.runs[0] && pa.runs[0].c) || tc || '#111111';
        pd.appendChild(bsp);
      }
      (pa.runs || []).forEach(function (r) {
        var sp = el('span');
        sp.textContent = r.t === '' ? '\u00A0' : r.t;
        var f = r.fe || r.f || '';
        sp.style.fontFamily = f ? '"' + f + '",' + FONT_STACK : FONT_STACK;
        sp.style.fontSize = Math.max(4, Math.round(r.sz * kf * fitScale * 100) / 100) + 'px';
        if (r.b) sp.style.fontWeight = '700';
        if (r.i) sp.style.fontStyle = 'italic';
        sp.style.color = r.c || tc || '#111111';
        pd.appendChild(sp);
      });
      holder.appendChild(pd);
    });
  }

  function buildTable(e, inner, ef) {
    var kf = ptFactor();
    var tb = el('table', 'peltbl');
    var sum = (e.cols || []).reduce(function (a, b) { return a + b; }, 0) || 1;
    var cg = el('colgroup');
    (e.cols || []).forEach(function (c) {
      var col = el('col');
      col.style.width = (c / sum * 100) + '%';
      cg.appendChild(col);
    });
    tb.appendChild(cg);
    (e.rows || []).forEach(function (r) {
      var tr = el('tr');
      if (r.h) tr.style.height = Math.max(1, r.h * ef) + 'px';
      (r.cells || []).forEach(function (c) {
        var td = el('td');
        if (c.cs > 1) td.colSpan = c.cs;
        if (c.rs > 1) td.rowSpan = c.rs;
        if (c.fill) td.style.background = c.fill;
        var bd = c.bd || (e.tborder ? { l: 1, r: 1, t: 1, b: 1 } : null);
        if (bd) {
          function bs(side, defw) {
            var b = (c.bd && c.bd[side]) || (e.tborder ? { w: defw, c: '#555555' } : null);
            if (!b) return '';
            if (!b.w || b.c === null) return 'none';
            return b.w + 'pt solid ' + (b.c || '#555555');
          }
          td.style.borderTop = bs('t', 0.75);
          td.style.borderRight = bs('r', 0.75);
          td.style.borderBottom = bs('b', 0.75);
          td.style.borderLeft = bs('l', 0.75);
        }
        if (c.ins) {
          td.style.paddingTop = (c.ins[1] * ef) + 'px';
          td.style.paddingRight = (c.ins[2] * ef) + 'px';
          td.style.paddingBottom = (c.ins[3] * ef) + 'px';
          td.style.paddingLeft = (c.ins[0] * ef) + 'px';
        }
        td.style.verticalAlign = ({ t: 'top', ctr: 'middle', b: 'bottom' })[c.anchor || 'ctr'];
        if (c.img && S.imgMap[c.img]) {
          var im = el('img');
          im.src = S.imgMap[c.img];
          im.style.height = Math.max(6, Math.round((c.ih || 300000) * ef)) + 'px';
          im.style.maxWidth = '100%';
          im.draggable = false;
          td.appendChild(im);
        } else if (c.paras) {
          parasInto(td, c.paras, 1, ef, null);
        }
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    inner.appendChild(tb);
  }

  /* ---------- 动画执行 ---------- */
  function applyStep(rec, st, instant) {
    if (st.t === 'out') { runExit(rec, st); return; }
    if (st.t === 'pulse') { if (!instant) runPulse(rec, st); return; }
    runEnter(rec, instant, st);
  }

  function runEnter(rec, instant, st) {
    st = st || { e: 'appear', dur: 0 };
    var d = rec.d, inner = rec.inner;
    var dur = instant ? 0 : (st.dur || 500);
    d.style.visibility = 'visible';
    if (d.getAttribute('data-masked')) {
      d.style.backgroundColor = 'transparent';
      d.removeAttribute('data-masked');
    }
    d.offsetHeight;
    if (st.e === 'appear' || dur <= 0) {
      d.style.transition = 'none'; d.style.opacity = '';
      inner.style.transition = 'none'; inner.style.transform = '';
      return;
    }
    if (st.e === 'wipe') {
      var dir = st.dir || 'right';
      inner.style.transition = 'none';
      inner.style.transform = dir === 'left' ? 'translateX(100%)' :
        dir === 'up' ? 'translateY(100%)' : dir === 'down' ? 'translateY(-100%)' : 'translateX(-100%)';
      inner.offsetHeight;
      inner.style.transition = 'transform ' + dur + 'ms ease';
      inner.style.transform = 'translate(0,0)';
      setTimeout(function () { d.style.overflow = ''; }, dur + 80);
      return;
    }
    var fromT = '', fromO = '';
    if (st.e === 'fade') { fromO = '0'; }
    else if (st.e === 'fly') {
      var m = String(st.dir || '0,1').split(',');
      var dx = parseFloat(m[0]) || 0, dy = parseFloat(m[1]) || 0;
      fromT = 'translate(' + Math.round(dx * S.box.w) + 'px,' + Math.round(dy * S.box.h) + 'px)';
    } else if (st.e === 'zoom') {
      fromT = 'scale(' + (st.scale || 0.25) + ')';
      fromO = '0';
    } else { fromO = '0'; }
    if (fromO) { d.style.transition = 'none'; d.style.opacity = fromO; }
    if (fromT) { inner.style.transition = 'none'; inner.style.transform = fromT; }
    d.offsetHeight;
    d.style.transition = 'opacity ' + dur + 'ms ease';
    d.style.opacity = '';
    inner.style.transition = 'transform ' + dur + 'ms ease';
    inner.style.transform = '';
  }

  function runExit(rec, st) {
    var d = rec.d;
    d.style.visibility = 'visible';
    d.style.transition = 'opacity ' + (st.dur || 400) + 'ms ease';
    d.style.opacity = '0';
  }

  function runPulse(rec, st) {
    var inner = rec.inner;
    inner.style.transition = 'transform 150ms ease';
    inner.style.transform = 'scale(1.08)';
    setTimeout(function () {
      inner.style.transition = 'transform 220ms ease';
      inner.style.transform = '';
    }, 170);
  }

  function playAuto() {
    flushAuto();
    S.pageEls.forEach(function (rec) {
      (rec.e.steps || []).forEach(function (st) {
        if (st.g !== -1) return;
        S.autoTimers.push(setTimeout(function () { applyStep(rec, st, false); }, st.delay || 0));
      });
    });
  }
  function flushAuto() {
    S.autoTimers.forEach(clearTimeout);
    S.autoTimers = [];
  }

  function advance() {
    if (S.pi < S.playQueue.length) {
      var g = S.playQueue[S.pi++];
      S.pageEls.forEach(function (rec) {
        (rec.e.steps || []).forEach(function (st) {
          if (st.g !== g) return;
          var dl = st.delay || 0;
          if (dl) {
            setTimeout(function () { applyStep(rec, st, false); }, dl);
          } else {
            applyStep(rec, st, false);
          }
        });
      });
      wakeUI();
    } else {
      nextPage();
    }
  }
  function revealAll() {
    while (S.pi < S.playQueue.length) {
      var g = S.playQueue[S.pi++];
      S.pageEls.forEach(function (rec) {
        (rec.e.steps || []).forEach(function (st) {
          if (st.g !== g) return;
          if (st.t === 'out') { rec.d.style.transition = 'none'; rec.d.style.opacity = '0'; }
          else if (st.t === 'in') runEnter(rec, true, st);
        });
      });
    }
  }
  function nextPage() { if (S.cur < S.pages.length - 1) showPage(S.cur + 1); else wakeUI(); }
  function prevPage() { if (S.cur > 0) showPage(S.cur - 1); else showPage(0); }

  /* ---------- 页间切换 ---------- */
  function transSwap(oldBox, newBox, tr) {
    var t = (tr && tr.transition && tr.transition.type) || 'fade';
    var dur = t === 'none' ? 0 : ((tr && tr.transition && tr.transition.dur) || 400);
    if (oldBox && oldBox.parentNode) {
      if (dur <= 0) { oldBox.parentNode.removeChild(oldBox); }
      else if (t === 'push' || t === 'cover' || t === 'wipe') {
        var dmap = { l: [1, 0], r: [-1, 0], u: [0, 1], d: [0, -1] };
        var m = dmap[(tr && tr.transition && tr.transition.dir) || 'l'] || dmap.l;
        var st = stageSize();
        var fx = m[0] * st.w, fy = m[1] * st.h;
        oldBox.style.zIndex = 1; newBox.style.zIndex = 2;
        newBox.style.transform = 'translate(' + fx + 'px,' + fy + 'px)';
        newBox.offsetHeight;
        newBox.style.transition = 'transform ' + dur + 'ms ease';
        newBox.style.transform = '';
        if (t === 'push') {
          oldBox.style.transition = 'transform ' + dur + 'ms ease';
          oldBox.style.transform = 'translate(' + (-fx) + 'px,' + (-fy) + 'px)';
        }
        setTimeout(function () { if (oldBox.parentNode) oldBox.parentNode.removeChild(oldBox); }, dur + 60);
      } else {
        oldBox.style.zIndex = 1; newBox.style.zIndex = 2;
        newBox.style.opacity = '0';
        newBox.offsetHeight;
        newBox.style.transition = 'opacity ' + dur + 'ms ease';
        newBox.style.opacity = '';
        oldBox.style.transition = 'opacity ' + dur + 'ms ease';
        oldBox.style.opacity = '0';
        setTimeout(function () { if (oldBox.parentNode) oldBox.parentNode.removeChild(oldBox); }, dur + 60);
      }
    }
  }

  /* ---------- 板书(与 viewer 数据互通) ---------- */
  function layoutInk(p) {
    S.ink.style.width = S.box.w + 'px';
    S.ink.style.height = S.box.h + 'px';
    S.ink.width = Math.round(S.box.w * S.dpr);
    S.ink.height = Math.round(S.box.h * S.dpr);
  }

  function strokePath(ctx, pts) {
    ctx.beginPath();
    if (pts.length === 1) { ctx.arc(pts[0][0], pts[0][1], .6, 0, Math.PI * 2); return; }
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (var i = 1; i < pts.length - 1; i++) {
      ctx.quadraticCurveTo(pts[i][0], pts[i][1], (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2);
    }
    var L = pts[pts.length - 1];
    ctx.lineTo(L[0], L[1]);
  }

  function drawStroke(ctx, s) {
    if (!s || !s.pts || !s.pts.length) return;
    ctx.save();
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    if (s.tool === 'text') {
      ctx.fillStyle = s.color;
      ctx.textBaseline = 'top';
      ctx.font = s.width + 'px ' + FONT_STACK;
      var lines = String(s.text || '').split('\n');
      for (var i = 0; i < lines.length; i++) ctx.fillText(lines[i], s.pts[0][0], s.pts[0][1] + i * s.width * 1.3);
      ctx.restore(); return;
    }
    if (s.tool === 'marker') ctx.globalAlpha = .42;
    ctx.strokeStyle = s.color;
    ctx.lineWidth = s.width;
    strokePath(ctx, s.pts);
    ctx.stroke();
    if (s.pts.length === 1) {
      ctx.fillStyle = s.color;
      ctx.beginPath(); ctx.arc(s.pts[0][0], s.pts[0][1], (s.width || 2) / 2, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }

  function inkRedraw() {
    if (!S.ictx) return;
    var p = curPage();
    var k = S.box.w / (p.w || 1280);
    S.ictx.setTransform(S.dpr * k, 0, 0, S.dpr * k, 0, 0);
    S.ictx.clearRect(0, 0, p.w || 1280, p.h || 720);
    var arr = S.strokes[p.pid] || [];
    for (var i = 0; i < arr.length; i++) drawStroke(S.ictx, arr[i]);
  }

  function evPage(e) {
    var p = curPage();
    var r = S.boxEl.getBoundingClientRect();
    return {
      x: (e.clientX - r.left) * (p.w || 1280) / (r.width || 1),
      y: (e.clientY - r.top) * (p.h || 720) / (r.height || 1)
    };
  }
  function strokesOf() {
    var pid = curPage().pid;
    return S.strokes[pid] || (S.strokes[pid] = []);
  }

  var downPt = null;
  function onDown(e) {
    wakeUI();
    downPt = { x: e.clientX, y: e.clientY, t: Date.now() };
    if (S.tool === 'pen' || S.tool === 'marker') {
      var cfg = S.tool === 'pen' ? S.pen : S.marker;
      var pt = evPage(e);
      S.drawing = { tool: S.tool, pts: [[pt.x, pt.y]], color: cfg.color, width: cfg.width };
      strokesOf().push(S.drawing);
    } else if (S.tool === 'eraser') {
      S.erasing = true;
      eraseAt(evPage(e));
    }
  }
  function onMove(e) {
    if (S.tool === 'laser') moveLaser(e);
    if (S.drawing) {
      var pt = evPage(e);
      var pts = S.drawing.pts;
      var L = pts[pts.length - 1];
      if (Math.abs(pt.x - L[0]) + Math.abs(pt.y - L[1]) > 1.2) pts.push([pt.x, pt.y]);
      inkRedraw();
    } else if (S.erasing) {
      eraseAt(evPage(e));
    }
  }
  function onUp(e) {
    if (S.drawing) {
      if (S.drawing.pts.length === 1) {
        var pt = evPage(e);
        S.drawing.pts.push([pt.x + .5, pt.y + .5]);
      }
      S.drawing = null;
      inkRedraw(); saveSoon();
    }
    S.erasing = false;
    if (downPt && S.tool === 'cursor') {
      var dx = e.clientX - downPt.x, dy = e.clientY - downPt.y;
      if (Math.abs(dx) < 9 && Math.abs(dy) < 9) advance();
      else if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.4) {
        if (dx < 0) advance(); else prevPage();
      }
    }
    downPt = null;
  }
  function onStageClick(e) {
    if (e.target && e.target.closest && e.target.closest('#ptop,#ptools,#pblack')) return;
  }

  function segDist(p, a, b) {
    var dx = b[0] - a[0], dy = b[1] - a[1];
    var l2 = dx * dx + dy * dy;
    if (!l2) return Math.hypot(p[0] - a[0], p[1] - a[1]);
    var t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
  }
  function eraseAt(pt) {
    if (!pt) return;
    var pid = curPage().pid;
    var arr = S.strokes[pid] || [];
    var r = (S.eraser.width || 28) / 2;
    var px = (typeof pt.x === 'number') ? pt.x : (typeof pt[0] === 'number' ? pt[0] : 0);
    var py = (typeof pt.y === 'number') ? pt.y : (typeof pt[1] === 'number' ? pt[1] : 0);
    var p = [px, py];
    var changed = false;
    for (var i = arr.length - 1; i >= 0; i--) {
      var s = arr[i], hit = false;
      var half = (s.width || 3) / 2;
      if (s.tool === 'text') {
        hit = px >= s.pts[0][0] - 20 && px <= s.pts[0][0] + (String(s.text || '').length * s.width * .6 + 20) &&
              py >= s.pts[0][1] - 10 && py <= s.pts[0][1] + s.width * 1.4;
      } else if (s.pts && s.pts.length) {
        if (s.pts.length === 1) {
          hit = Math.hypot(px - s.pts[0][0], py - s.pts[0][1]) <= r + half;
        } else {
          for (var j = 0; j < s.pts.length - 1; j++) {
            if (segDist(p, s.pts[j], s.pts[j + 1]) <= r + half) { hit = true; break; }
          }
        }
      }
      if (hit) { arr.splice(i, 1); changed = true; }
    }
    if (changed) { saveSoon(); inkRedraw(); }
  }

  /* ---------- 激光笔 ---------- */
  function moveLaser(e) {
    var dot = document.getElementById('plaser');
    if (!dot) return;
    dot.style.display = 'block';
    dot.style.left = (e.clientX - 7) + 'px';
    dot.style.top = (e.clientY - 7) + 'px';
    dot.classList.add('hot');
    clearTimeout(S.lid);
    S.lid = setTimeout(function () { dot.classList.remove('hot'); }, 900);
  }

  /* ---------- 保存 ---------- */
  function serialize() {
    var out = {
      pages: S.pages.map(function (p) { return { t: p.t, n: p.n, pid: p.pid, bg: p.bg || 'w' }; }),
      strokes: S.strokes
    };
    if (S.bbN && S.bbN > 1) out.bb = { n: S.bbN };
    return out;
  }
  function saveSoon() {
    S.dirty = true;
    clearTimeout(S.saveT);
    S.saveT = setTimeout(saveNow, 900);
  }
  function saveNow() {
    if (!S.dirty) return Promise.resolve();
    return fetch('/api/files/' + FID + '/annotations', {
      method: 'PUT', headers: Object.assign({ 'Content-Type': 'application/json' }, AUTH),
      body: JSON.stringify(serialize())
    }).then(function () { S.dirty = false; }).catch(function () { });
  }
  function flushSave() {
    if (!S.dirty) return;
    try {
      fetch('/api/files/' + FID + '/annotations', {
        method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + TOKEN },
        body: JSON.stringify(serialize()), keepalive: true
      });
      S.dirty = false;
    } catch (e) { }
  }

  /* ---------- 键盘 ---------- */
  function onKey(e) {
    if (e.target && /INPUT|TEXTAREA|SELECT|VIDEO|AUDIO/.test(e.target.tagName)) return;
    var k = e.key;
    var isNext = (k === 'ArrowRight' || k === ' ' || k === 'PageDown' || k === 'Enter' || k === 'ArrowDown' ||
                  k === 'Right' || k === 'Down' || k === 'Next' || e.code === 'PageDown' || e.code === 'ArrowDown' ||
                  e.keyCode === 34 || e.keyCode === 40 || e.keyCode === 39 || e.keyCode === 32 || e.keyCode === 13);
    var isPrev = (k === 'ArrowLeft' || k === 'PageUp' || k === 'ArrowUp' ||
                  k === 'Left' || k === 'Up' || k === 'Prior' || e.code === 'PageUp' || e.code === 'ArrowUp' ||
                  e.keyCode === 33 || e.keyCode === 38 || e.keyCode === 37);
    if (isNext) { e.preventDefault(); advance(); }
    else if (isPrev) { e.preventDefault(); prevPage(); }
    else if (k === '.') { e.preventDefault(); revealAll(); }
    else if (k === 'Home') showPage(0);
    else if (k === 'End') showPage(S.pages.length - 1);
    else if (k === 'Escape') exitPresent();
    else if (k === 'b' || k === 'B') blackout('#000');
    else if (k === 'w' || k === 'W') blackout('#fff');
    else if (k === 'p' || k === 'P') toggleTools();
    else if (k === 'l' || k === 'L') setTool(S.tool === 'laser' ? 'cursor' : 'laser');
    else if (k === 's' || k === 'S') toggleCompare();
    else if (k === 'f' || k === 'F') toggleFull();
    else if (k === 't' || k === 'T') { S.t0 = Date.now(); }
    else if (k === 'd' || k === 'D') { debugInfo(); }
  }
  function setModeDot() {
    var d = document.getElementById('pmode');
    if (!d) return;
    var p = curPage();
    var mp = p.t === 'pdf' ? mPage(p.n) : null;
    var mode = mp ? mp.mode : (S.manifest ? 'static' : 'none');
    var cnt = mp ? (mp.elements || []).length : 0;
    if (mode === 'elements') {
      d.style.color = '#4ade80';
      d.title = '\u52a8\u753b\u5143\u7d20\u6a21\u5f0f \u00b7 ' + cnt + ' \u4e2a\u5143\u7d20';
    } else {
      d.style.color = '#f87171';
      d.title = '\u9759\u6001PDF\u6a21\u5f0f(' +
        (S.manifest ? '\u672c\u9875\u65e0\u5f62\u72b6' : '\u65e0\u52a8\u753b\u6570\u636e, \u8bf7\u5728\u8bfe\u4ef6\u5361\u7247\u70b9\u91cd\u8bd5') + ')';
    }
  }

  function debugInfo() {
    var p = curPage();
    var mp = p.t === 'pdf' ? mPage(p.n) : null;
    var lines = [
      '\u9875 ' + (S.cur + 1) + ' / ' + S.pages.length,
      '\u6e05\u5355\u7248\u672c: v' + (S.manifest ? S.manifest.v : '-'),
      '\u672c\u9875\u6a21\u5f0f: ' + (mp ? mp.mode : (S.manifest ? 'static' : 'none')),
      '\u5143\u7d20\u6570: ' + (mp ? (mp.elements || []).length : 0),
      '\u80cc\u666f: ' + (S.bgFailed ? '\u5931\u8d25(\u5df2\u7528\u4e3bPDF)' : (S.bgDoc ? 'bg.pdf' : '\u4e3bPDF'))
    ];
    var h = document.getElementById('phint');
    if (h) {
      h.textContent = lines.join(' \u00b7 ');
      h.classList.remove('off');
      setTimeout(function () { h.classList.add('off'); }, 5000);
    }
  }

  function blackout(color) {
    var b = document.getElementById('pblack');
    if (!b) return;
    if (b.style.display === 'block' && b.style.background === color) { b.style.display = 'none'; return; }
    b.style.display = 'block';
    b.style.background = color;
    b.onclick = function () { b.style.display = 'none'; };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
