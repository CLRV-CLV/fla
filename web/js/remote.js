/* ================================================================
 * FLA v1.28 — 手机投屏与远程授课遥控 (Remote Control)
 * 手机端与电脑大屏幕放映实时双向同步：
 * - 翻页与动画步进/步退 (大尺寸触控按键)
 * - 实时激光指示 (手机触控板模拟大屏红外激光点)
 * - 白板切换与笔迹清屏
 * - 黑屏幕布开关
 * - WebSocket 高速传输 + HTTP 轮询自愈
 * ================================================================ */
(function () {
  'use strict';

  var Remote = {
    view: viewRemote
  };

  var curSession = null;
  var ws = null;
  var pollTimer = null;
  var state = {
    title: '课堂放映',
    page: 1,
    total: 1,
    black: false,
    connected: false
  };

  function parseHashParams() {
    var hash = location.hash || '';
    var qIdx = hash.indexOf('?');
    if (qIdx === -1) return {};
    var qs = hash.slice(qIdx + 1);
    var params = {};
    qs.split('&').forEach(function (pair) {
      var parts = pair.split('=');
      if (parts[0]) params[decodeURIComponent(parts[0])] = decodeURIComponent(parts[1] || '');
    });
    return params;
  }

  async function viewRemote() {
    document.title = '手机课堂遥控器 - FLA';
    var app = document.getElementById('app');
    var params = parseHashParams();
    var sid = params.sid || '';
    var code = params.code || '';

    if (!sid && code) {
      try {
        var pairRes = await API.get('/api/remote/pair/' + encodeURIComponent(code));
        if (pairRes && pairRes.session_id) {
          sid = pairRes.session_id;
        }
      } catch (err) {}
    }

    if (!sid) {
      renderPairView(app);
      return;
    }

    try {
      var info = await API.get('/api/remote/' + sid + '/info' + (code ? '?code=' + code : ''));
      disconnect(); // 防御: 重新进入时清理上一个会话的轮询/WS
      curSession = { id: sid, code: code, title: info.title };
      state.title = info.title || '课堂放映';
      state.page = info.page || 1;
      state.total = info.total || 1;
      state.black = !!info.black;
      state.connected = true;

      renderControllerView(app);
      connectWs(sid);
      // 离开 #/remote 页面时自动断开, 避免轮询/WS 永久残留
      App.setCleanup(disconnect);
    } catch (e) {
      renderPairView(app, e.message || '连接已过期，请重新配对');
    }
  }

  function renderPairView(app, errMsg) {
    app.innerHTML =
      '<div class="fla-remote-wrap" style="justify-content:center; align-items:center; padding:24px;">' +
        '<div style="width:100%; max-width:340px; background:#111827; border:1px solid #1f2937; border-radius:14px; padding:24px 20px; text-align:center;">' +
          '<div style="font-size:20px; font-weight:700; color:#ffffff; margin-bottom:6px;">FLA 课堂遥控器</div>' +
          '<div style="font-size:13px; color:#9ca3af; margin-bottom:20px;">手机远程同步控制电脑 PPT 与白板</div>' +
          (errMsg ? '<div style="font-size:13px; color:#ef4444; margin-bottom:14px;">' + UI.esc(errMsg) + '</div>' : '') +
          '<input type="text" id="flaRmCodeInput" maxlength="6" placeholder="输入 4 位配对码" ' +
            'style="width:100%; padding:12px; font-size:20px; font-weight:700; text-align:center; letter-spacing:4px; ' +
            'background:#1f2937; border:1px solid #374151; border-radius:10px; color:#ffffff; outline:none; margin-bottom:16px;">' +
          '<button id="flaRmConnectBtn" class="fla-pk-draw-btn" style="margin-bottom:12px;">连接电脑放映</button>' +
          '<button id="flaRmBackBtn" class="fla-tbtn fla-tbtn-sec" style="width:100%;">返回课件库</button>' +
        '</div>' +
      '</div>';

    var btn = app.querySelector('#flaRmConnectBtn');
    var inp = app.querySelector('#flaRmCodeInput');
    var backBtn = app.querySelector('#flaRmBackBtn');

    if (backBtn) backBtn.onclick = function () { location.hash = '#/library'; };

    if (btn && inp) {
      btn.onclick = async function () {
        var c = inp.value.trim();
        if (!c) { UI.toast('请输入配对码'); return; }
        btn.disabled = true;
        btn.textContent = '连接中…';
        try {
          var pair = await API.get('/api/remote/pair/' + encodeURIComponent(c));
          if (pair && pair.session_id) {
            location.hash = '#/remote?sid=' + encodeURIComponent(pair.session_id) + '&code=' + encodeURIComponent(c);
            viewRemote();
            return;
          }
          throw new Error('未找到该会话');
        } catch (e) {
          UI.toast('配对码错误或会话已结束: ' + (e.message || ''), 'err');
          btn.disabled = false;
          btn.textContent = '连接电脑放映';
        }
      };
    }
  }

  function renderControllerView(app) {
    app.innerHTML =
      '<div class="fla-remote-wrap">' +
        '<header class="fla-remote-head">' +
          '<div class="fla-remote-title" id="rmTitle">' + UI.esc(state.title) + '</div>' +
          '<div class="fla-remote-status">' +
            '<span class="fla-remote-dot ' + (state.connected ? '' : 'offline') + '" id="rmDot"></span>' +
            '<span id="rmStatusTxt">' + (state.connected ? '已连接' : '重连中') + '</span>' +
          '</div>' +
        '</header>' +

        '<main class="fla-remote-body">' +
          '<div class="fla-remote-card">' +
            '<div class="fla-remote-page-num" id="rmPageTxt">' + state.page + ' / ' + state.total + '</div>' +
            '<div class="fla-remote-page-sub">当前幻灯片页码</div>' +
          '</div>' +

          '<div class="fla-remote-nav-grid">' +
            '<button class="fla-remote-btn" id="rmBtnPrev">' +
              UI.icon('chevL', 24) + '<span>上一页</span>' +
            '</button>' +
            '<button class="fla-remote-btn primary" id="rmBtnNext">' +
              UI.icon('chevR', 24) + '<span>下一页</span>' +
            '</button>' +
          '</div>' +

          '<div class="fla-remote-nav-grid" style="grid-template-columns: 1fr 1fr;">' +
            '<button class="fla-remote-btn" id="rmBtnStepPrev" style="padding:14px; font-size:14px;">' +
              '<span>上一步 (动画)</span>' +
            '</button>' +
            '<button class="fla-remote-btn" id="rmBtnStepNext" style="padding:14px; font-size:14px;">' +
              '<span>下一步 (动画)</span>' +
            '</button>' +
          '</div>' +

          '<div class="fla-remote-touchpad" id="rmTouchpad">' +
            '<div class="fla-remote-touchpad-label">激光笔触控板 · 滑动指示大屏红点</div>' +
            '<div class="fla-remote-touch-cursor" id="rmCursor"></div>' +
          '</div>' +

          '<div class="fla-remote-tools">' +
            '<button class="fla-remote-tool-btn" id="rmToolBlack">' +
              UI.icon('eye', 18) + '<span id="rmBlackTxt">' + (state.black ? '恢复' : '黑屏') + '</span>' +
            '</button>' +
            '<button class="fla-remote-tool-btn" id="rmToolClear">' +
              UI.icon('trash', 18) + '<span>清屏</span>' +
            '</button>' +
            '<button class="fla-remote-tool-btn" id="rmToolWhiteboard">' +
              UI.icon('board', 18) + '<span>白板</span>' +
            '</button>' +
            '<button class="fla-remote-tool-btn" id="rmToolExit">' +
              UI.icon('back', 18) + '<span>退出</span>' +
            '</button>' +
          '</div>' +
        '</main>' +
      '</div>';

    bindControllerEvents(app);
  }

  function bindControllerEvents(app) {
    var btnPrev = app.querySelector('#rmBtnPrev');
    var btnNext = app.querySelector('#rmBtnNext');
    var btnStepPrev = app.querySelector('#rmBtnStepPrev');
    var btnStepNext = app.querySelector('#rmBtnStepNext');
    var btnBlack = app.querySelector('#rmToolBlack');
    var btnClear = app.querySelector('#rmToolClear');
    var btnWb = app.querySelector('#rmToolWhiteboard');
    var btnExit = app.querySelector('#rmToolExit');

    if (btnPrev) btnPrev.onclick = function () { sendAction('prev'); };
    if (btnNext) btnNext.onclick = function () { sendAction('next'); };
    if (btnStepPrev) btnStepPrev.onclick = function () { sendAction('stepPrev'); };
    if (btnStepNext) btnStepNext.onclick = function () { sendAction('stepNext'); };

    if (btnBlack) {
      btnBlack.onclick = function () {
        state.black = !state.black;
        var txt = app.querySelector('#rmBlackTxt');
        if (txt) txt.textContent = state.black ? '恢复' : '黑屏';
        sendAction('black');
      };
    }

    if (btnClear) {
      btnClear.onclick = function () {
        sendAction('clear');
        UI.toast('已请求清空大屏板书');
      };
    }

    if (btnWb) {
      btnWb.onclick = async function () {
        try {
          var f = await API.post('/api/files/board', {});
          if (f && f.id) {
            location.hash = '#/view/' + f.id;
          }
        } catch (e) {
          sendAction('whiteboard');
        }
      };
    }

    if (btnExit) {
      btnExit.onclick = function () {
        disconnect();
        location.hash = '#/library';
      };
    }

    // 激光笔触控板交互
    var pad = app.querySelector('#rmTouchpad');
    var cur = app.querySelector('#rmCursor');
    if (pad && cur) {
      var isTouching = false;
      var lastSend = 0;

      function updateTouch(e) {
        var rect = pad.getBoundingClientRect();
        var clientX = e.touches ? e.touches[0].clientX : e.clientX;
        var clientY = e.touches ? e.touches[0].clientY : e.clientY;
        var x = Math.max(0, Math.min(rect.width, clientX - rect.left));
        var y = Math.max(0, Math.min(rect.height, clientY - rect.top));

        cur.style.display = 'block';
        cur.style.left = x + 'px';
        cur.style.top = y + 'px';

        var nx = x / (rect.width || 1);
        var ny = y / (rect.height || 1);

        var now = Date.now();
        if (now - lastSend > 40) {
          lastSend = now;
          sendAction('laser', { x: nx, y: ny });
        }
      }

      pad.addEventListener('pointerdown', function (e) {
        isTouching = true;
        pad.setPointerCapture(e.pointerId);
        updateTouch(e);
      });
      pad.addEventListener('pointermove', function (e) {
        if (!isTouching) return;
        updateTouch(e);
      });
      function endTouch() {
        if (!isTouching) return;
        isTouching = false;
        cur.style.display = 'none';
      }
      pad.addEventListener('pointerup', endTouch);
      pad.addEventListener('pointercancel', endTouch);
    }
  }

  function sendAction(action, data) {
    if (!curSession) return;
    var msg = { action: action, data: data || {} };
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    } else {
      API.post('/api/remote/' + curSession.id + '/action', msg).catch(function () {});
    }
  }

  function connectWs(sid) {
    if (ws) {
      try { ws.close(); } catch (e) {}
    }
    var wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var wsUrl = wsProto + '//' + location.host + '/api/remote/ws/' + sid;

    try {
      ws = new WebSocket(wsUrl);
      ws.onopen = function () {
        setConnectedUI(true);
      };
      ws.onmessage = function (e) {
        try {
          var msg = JSON.parse(e.data);
          handleServerMessage(msg);
        } catch (err) {}
      };
      ws.onclose = function () {
        setConnectedUI(false);
        startPolling(sid);
      };
    } catch (err) {
      setConnectedUI(false);
      startPolling(sid);
    }
  }

  function handleServerMessage(msg) {
    if (msg.type === 'state') {
      var s = msg.state || {};
      if (s.title) state.title = s.title;
      if (s.page !== undefined) state.page = s.page;
      if (s.total !== undefined) state.total = s.total;
      if (s.black !== undefined) state.black = s.black;
      updateUI();
    }
  }

  function updateUI() {
    var titleEl = document.getElementById('rmTitle');
    if (titleEl) titleEl.textContent = state.title;

    var pageEl = document.getElementById('rmPageTxt');
    if (pageEl) pageEl.textContent = state.page + ' / ' + state.total;

    var blackTxt = document.getElementById('rmBlackTxt');
    if (blackTxt) blackTxt.textContent = state.black ? '恢复' : '黑屏';
  }

  function setConnectedUI(online) {
    state.connected = online;
    var dot = document.getElementById('rmDot');
    var txt = document.getElementById('rmStatusTxt');
    if (dot) dot.className = 'fla-remote-dot ' + (online ? '' : 'offline');
    if (txt) txt.textContent = online ? '已连接' : '离线重连中';
  }

  function startPolling(sid) {
    if (pollTimer) return;
    pollTimer = setInterval(async function () {
      if (!curSession) return;
      try {
        var info = await API.get('/api/remote/' + sid + '/info');
        if (info) {
          state.title = info.title || state.title;
          state.page = info.page || state.page;
          state.total = info.total || state.total;
          state.black = !!info.black;
          setConnectedUI(true);
          updateUI();
        }
      } catch (e) {
        setConnectedUI(false);
      }
    }, 1500);
  }

  function disconnect() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (ws) { try { ws.close(); } catch (e) {} ws = null; }
    curSession = null;
  }

  window.Remote = Remote;

})();
