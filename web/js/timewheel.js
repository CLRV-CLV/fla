/* FLA - iOS 风格时间滚轮 (时 / 分 / 秒)
 * ----------------------------------------------------------------
 * 为什么自己写: 学校机房离线运行, 不能依赖 CDN; 市面开源轮播库要么依赖
 * jquery/React, 要么不支持"拖动 + 键盘 + 滚轮 + ±1"混合操作。
 * 交互对齐 iOS 计时器 / UIPickerView:
 *   · 上下拖动滚轮, 松手带惯性吸附到整格
 *   · 鼠标滚轮 / 触摸板滑动
 *   · 键盘 ↑↓ 微调, ←→ 切换时/分/秒列
 *   · 点列内数字直接跳转
 *   · 右侧 ± 按钮 (时/分/秒 各自加减), 长按连续加减
 *   · 三列循环滚动 (59 → 00 无缝)
 * 纯 ES5, 无依赖, 兼容 Chrome 60+ (学校旧机器)。
 * 用法:
 *   var c = TimeWheel.mount(el, { value: 300, max: {h:23,m:59,s:59}, onChange: fn });
 *   c.get()  -> 秒;  c.set(600);  c.setRunning(true) 时禁用拖动(计时中)
 * ---------------------------------------------------------------- */
(function () {
  'use strict';

  var ITEM_H = 44;          /* 每格高度 px */
  var VISIBLE = 3;          /* 中间可见整格数 */
  var REP = 5;              /* 复制份数, 保证上下都能无限滑 */
  var MASK = 0.55;          /* 滚动时相邻项的模糊/缩放 */
  var uid = 0;

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function mod(v, n) { return ((v % n) + n) % n; }

  function el(tag, cls) {
    var d = document.createElement(tag);
    if (cls) d.className = cls;
    return d;
  }

  /* ---------------- 单列滚轮 ---------------- */
  function Wheel(host, opt) {
    this.host = host;
    this.max = opt.max || 59;
    this.label = opt.label || '';
    this.n = this.max + 1;
    this.height = ITEM_H * VISIBLE;
    this.onChange = opt.onChange || function () {};
    this.disabled = false;
    this.id = 'tw' + (++uid);
    this._build();
    this.set(opt.value || 0, true);
  }

  Wheel.prototype._build = function () {
    var self = this;
    this.host.className = 'tw-col';
    this.host.setAttribute('tabindex', '0');
    this.host.setAttribute('role', 'spinbutton');
    this.host.setAttribute('aria-label', this.label || '时间');

    var col = el('div', 'tw-colbody');
    col.style.height = this.height + 'px';
    var band = el('div', 'tw-band');
    var strip = el('div', 'tw-strip');
    strip.style.height = (this.height) + 'px';

    var total = this.n * REP;
    for (var i = 0; i < total; i++) {
      var it = el('div', 'tw-item');
      it.textContent = (i % this.n < 10 ? '0' : '') + (i % this.n);
      it.style.height = ITEM_H + 'px';
      it.style.lineHeight = ITEM_H + 'px';
      strip.appendChild(it);
      (function (idx) { it.onclick = function () { if (!self.disabled) self._snapTo(idx); }; })(i);
    }

    col.appendChild(strip);
    col.appendChild(band);
    col.appendChild(el('div', 'tw-fade tw-fade-top'));
    col.appendChild(el('div', 'tw-fade tw-fade-bot'));
    this.host.appendChild(col);
    if (this.label) {
      var lb = el('div', 'tw-label');
      lb.textContent = this.label;
      this.host.appendChild(lb);
    }

    this.col = col;
    this.strip = strip;
    this.items = strip.children;
    this.pos = this.n * 2;      /* 中间那一份 */
    this._apply(0);

    /* --- 拖动 --- */
    var dragging = false, startY = 0, startPos = 0, lastY = 0, lastT = 0, vel = 0, moved = 0;
    var raf = 0;

    function pointY(e) { return e.touches ? e.touches[0].clientY : e.clientY; }

    function down(e) {
      if (self.disabled) return;
      if (e.button != null && e.button !== 0) return;
      dragging = true; moved = 0;
      startY = lastY = pointY(e);
      startPos = self.pos;
      lastT = Date.now(); vel = 0;
      self.col.classList.add('tw-grabbing');
      self.host.focus();
      if (e.preventDefault) e.preventDefault();
    }
    function move(e) {
      if (!dragging) return;
      var y = pointY(e);
      moved = Math.max(moved, Math.abs(y - startY));
      var dy = y - startY;
      var p = startPos - dy / ITEM_H;
      var now = Date.now();
      if (now - lastT > 0) vel = (lastY - y) / (now - lastT);
      lastY = y; lastT = now;
      self._setPos(clamp(p, 0, self.n * REP - 1), 0);
      if (e.preventDefault) e.preventDefault();
    }
    function up() {
      if (!dragging) return;
      dragging = false;
      self.col.classList.remove('tw-grabbing');
      /* 惯性: 松手速度够快就多滑几格 */
      var p = self.pos + (Math.abs(vel) > 0.12 ? clamp(vel * 9, -6, 6) : 0);
      self._snapTo(Math.round(p));
    }

    col.addEventListener('mousedown', down);
    col.addEventListener('touchstart', down, { passive: true });
    window.addEventListener('mousemove', move);
    window.addEventListener('touchmove', move, { passive: false });
    window.addEventListener('mouseup', up);
    window.addEventListener('touchend', up);

    /* --- 鼠标滚轮 --- */
    col.addEventListener('wheel', function (e) {
      if (self.disabled) return;
      e.preventDefault();
      self._snapTo(Math.round(self.pos) - (e.deltaY > 0 ? -1 : 1) * (Math.abs(e.deltaY) > 90 ? 2 : 1));
    }, { passive: false });

    /* --- 键盘 --- */
    this.host.addEventListener('keydown', function (e) {
      if (self.disabled) return;
      if (e.key === 'ArrowUp') { e.preventDefault(); self._snapTo(Math.round(self.pos) - 1); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); self._snapTo(Math.round(self.pos) + 1); }
      else if (e.key === 'PageUp') { e.preventDefault(); self._snapTo(Math.round(self.pos) - 10); }
      else if (e.key === 'PageDown') { e.preventDefault(); self._snapTo(Math.round(self.pos) + 10); }
      else if (e.key === 'Home') { e.preventDefault(); self.set(0); }
    });

    /* 触摸滚动时阻止页面跟着滚 */
    col.addEventListener('touchmove', function (e) { if (dragging) e.preventDefault(); }, { passive: false });
  };

  Wheel.prototype._apply = function (anim) {
    var d = -this.pos * ITEM_H;
    this.strip.style.transition = anim ? 'transform .28s cubic-bezier(.22,.9,.3,1.02)' : 'none';
    this.strip.style.transform = 'translate3d(0,' + d + 'px,0)';
    var center = Math.round(this.pos);
    for (var i = 0; i < this.items.length; i++) {
      var dist = Math.abs(i - this.pos);
      var it = this.items[i];
      var scale = dist < 0.5 ? 1 : Math.max(.72, 1 - dist * 0.12);
      it.style.transform = 'scale(' + scale.toFixed(3) + ')';
      it.style.opacity = String(Math.max(.18, 1 - dist * 0.3));
      it.className = 'tw-item' + (i === center ? ' tw-on' : '');
    }
  };

  Wheel.prototype._setPos = function (p, anim) {
    this.pos = p;
    this._apply(anim);
    var v = this.value();
    if (v !== this.lastValue) {
      this.lastValue = v;
      this.onChange(v);
    }
  };

  Wheel.prototype._snapTo = function (idx) {
    var self = this;
    /* 吸附到整格 → 动画结束后把 pos 拉回中间份, 保证还能继续滑 */
    this._setPos(idx, 1);
    clearTimeout(this._normT);
    this._normT = setTimeout(function () {
      var v = self.value();
      self.pos = self.n * 2 + v;
      self._apply(0);
    }, 300);
  };

  Wheel.prototype.value = function () {
    return mod(Math.round(this.pos), this.n);
  };

  Wheel.prototype.set = function (v, silent) {
    v = clamp(Math.round(Number(v) || 0), 0, this.max);
    this.pos = this.n * 2 + v;
    this.lastValue = v;
    this._apply(0);
    if (!silent) this.onChange(v);
  };

  Wheel.prototype.setDisabled = function (d) {
    this.disabled = !!d;
    this.host.classList.toggle('tw-disabled', !!d);
  };

  /* ---------------- 三列组合 ---------------- */
  function TimeWheel(root, opt) {
    opt = opt || {};
    var self = this;
    root.className = 'tw-wrap';
    root.innerHTML =
      '<div class="tw-cols">' +
        '<div class="tw-seg" data-u="h"><div class="tw-host"></div><div class="tw-side">' +
          '<button class="tw-plus" data-step="h" data-dir="1" title="加 1 小时">+</button>' +
          '<button class="tw-minus" data-step="h" data-dir="-1" title="减 1 小时">−</button></div></div>' +
        '<div class="tw-seg" data-u="m"><div class="tw-host"></div><div class="tw-side">' +
          '<button class="tw-plus" data-step="m" data-dir="1" title="加 1 分钟">+</button>' +
          '<button class="tw-minus" data-step="m" data-dir="-1" title="减 1 分钟">−</button></div></div>' +
        '<div class="tw-seg" data-u="s"><div class="tw-host"></div><div class="tw-side">' +
          '<button class="tw-plus" data-step="s" data-dir="1" title="加 10 秒 (Shift: 1 秒)">+</button>' +
          '<button class="tw-minus" data-step="s" data-dir="-1" title="减 10 秒">−</button></div></div>' +
      '</div>' +
      '<div class="tw-quick">' +
        '<button data-q="60">1 分</button><button data-q="180">3 分</button>' +
        '<button data-q="300">5 分</button><button data-q="600">10 分</button>' +
        '<button data-q="900">15 分</button><button data-q="1800">30 分</button>' +
        '<button data-q="2700">45 分</button><button data-q="0" class="tw-zero">清零</button>' +
      '</div>';

    this.root = root;
    this.onChange = opt.onChange || function () {};
    this.max = { h: 23, m: 59, s: 59 };

    function mk(unit, max, label, host) {
      return new Wheel(host, {
        max: max, label: label, value: 0,
        onChange: function () { self._emit(); }
      });
    }
    this.h = mk('h', 23, '时', root.querySelector('[data-u=h] .tw-host'));
    this.m = mk('m', 59, '分', root.querySelector('[data-u=m] .tw-host'));
    this.s = mk('s', 59, '秒', root.querySelector('[data-u=s] .tw-host'));
    this.wheels = [this.h, this.m, this.s];

    /* ± 按钮: 单击一步, 长按连加 */
    root.querySelectorAll('.tw-side button').forEach(function (b) {
      var t = 0, iv = 0;
      var unit = b.getAttribute('data-step');
      var dir = parseInt(b.getAttribute('data-dir'), 10);
      function step(shift) {
        var w = self[unit];
        if (w.disabled) return;
        /* 换算成秒: 时 ±3600, 分 ±60, 秒 ±10 (Shift 为 ±1) */
        var amount = unit === 'h' ? 3600 : (unit === 'm' ? 60 : (shift ? 1 : 10));
        var total = self.get() + dir * amount;
        self.set(total);            /* 需要触发 onChange, 否则外部显示不刷新 */
      }
      b.addEventListener('click', function (e) { step(e.shiftKey); });
      b.addEventListener('mousedown', function (e) {
        if (e.button !== 0) return;
        t = setTimeout(function () {
          iv = setInterval(function () { step(e.shiftKey); }, 90);
        }, 420);
      });
      ['mouseup', 'mouseleave'].forEach(function (ev) {
        b.addEventListener(ev, function () { clearTimeout(t); clearInterval(iv); });
      });
    });

    root.querySelectorAll('.tw-quick button').forEach(function (b) {
      b.onclick = function () { self.set(parseInt(b.getAttribute('data-q'), 10), false); };
    });

    this.set(opt.value != null ? opt.value : 300, true);
  }

  TimeWheel.prototype.get = function () {
    return this.h.value() * 3600 + this.m.value() * 60 + this.s.value();
  };

  TimeWheel.prototype.set = function (sec, silent) {
    sec = Math.max(0, Math.min(86399, Math.round(Number(sec) || 0)));   /* 23:59:59 */
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    this.h.set(h, true); this.m.set(m, true); this.s.set(s, true);
    if (!silent) this._emit();
  };

  TimeWheel.prototype._emit = function () {
    var v = this.get();
    if (v === this._last) return;
    this._last = v;
    this.onChange(v);
  };

  TimeWheel.prototype.setDisabled = function (d) {
    this.wheels.forEach(function (w) { w.setDisabled(d); });
    this.root.classList.toggle('tw-locked', !!d);
  };

  TimeWheel.prototype.focus = function (u) {
    var w = this[u || 'm'];
    if (w) w.host.focus();
  };

  window.TimeWheel = {
    mount: function (root, opt) { return new TimeWheel(root, opt); },
    ITEM_H: ITEM_H
  };
})();
