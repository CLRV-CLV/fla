/* FLA v1.28 - AI 视觉与文字识别同步模块 (FLA_OCR)
 * 作用: 识别微软 Office Online 底部状态栏 "第N张幻灯片，共M张" / "幻灯片 N / M" / "Slide N of M",
 *      使板书画布全自动随微软翻页切换，老师无需在"自翻"与"微软"之间手动选择。
 * 特性: 1s 精准采样一次; 兼容整屏分享(含 Windows 任务栏偏移)、标签页分享与全屏放映。
 */
(function (global) {
  'use strict';

  var stream = null;
  var video = null;
  var scanTimer = null;
  var isScanning = false;
  var lastDetectedPage = -1;
  var offCanvas = null;
  var offCtx = null;

  /* ==================================================================
   *  1. 文本解析引擎: 抽取页码与总页数
   * ================================================================== */
  function parseSlideNumber(text) {
    if (!text || typeof text !== 'string') return null;
    var s = text.replace(/[\r\n\t]+/g, ' ').trim();
    if (!s) return null;

    // 格式 1: "第 1 张幻灯片，共 5 张" / "第1张" / "第 1/5 张" / "第 1 页"
    var m = s.match(/第\s*(\d{1,4})\s*[张页](?:幻灯片)?/i);
    if (m) {
      var cur = parseInt(m[1], 10);
      var tm = s.match(/共\s*(\d{1,4})\s*[张页]/i);
      return { cur: cur, total: tm ? parseInt(tm[1], 10) : 0, raw: s };
    }

    // 格式 2: "幻灯片 2 / 10" / "幻灯片 2"
    m = s.match(/幻灯片\s*(\d{1,4})(?:\s*[\/之,，]\s*(\d{1,4}))?/i);
    if (m) {
      return { cur: parseInt(m[1], 10), total: m[2] ? parseInt(m[2], 10) : 0, raw: s };
    }

    // 格式 3: "Slide 3 of 15" / "Slide 3 / 15" / "Slide 3"
    m = s.match(/Slide\s*(\d{1,4})(?:\s*(?:of|\/|之)\s*(\d{1,4}))?/i);
    if (m) {
      return { cur: parseInt(m[1], 10), total: m[2] ? parseInt(m[2], 10) : 0, raw: s };
    }

    // 格式 4: "Page 3 of 15" / "Page 3"
    m = s.match(/Page\s*(\d{1,4})(?:\s*(?:of|\/|之)\s*(\d{1,4}))?/i);
    if (m) {
      return { cur: parseInt(m[1], 10), total: m[2] ? parseInt(m[2], 10) : 0, raw: s };
    }

    // 格式 5: "1 / 5" 或 "1 of 5" 或 "1 之 5"
    m = s.match(/\b(\d{1,4})\s*(?:\/|of|之)\s*(\d{1,4})\b/i);
    if (m) {
      return { cur: parseInt(m[1], 10), total: parseInt(m[2], 10), raw: s };
    }

    // 格式 6: 纯数字独立匹配
    m = s.match(/^\s*(\d{1,4})\s*$/);
    if (m) {
      return { cur: parseInt(m[1], 10), total: 0, raw: s };
    }

    return null;
  }

  /* ==================================================================
   *  2. 画布识别 (TextDetector / Tesseract / 纯 JS 光栅投影分析)
   * ================================================================== */
  async function recognizeCanvas(canvas) {
    if (!canvas || canvas.width < 10 || canvas.height < 8) return null;

    // 方案 A: 浏览器原生 Shape Detection API (Chrome/Edge 内置硬件加速 OCR)
    if ('TextDetector' in window) {
      try {
        var detector = new window.TextDetector();
        var detected = await detector.detect(canvas);
        if (detected && detected.length > 0) {
          for (var i = 0; i < detected.length; i++) {
            var raw = (detected[i].rawValue || '').trim();
            var parsed = parseSlideNumber(raw);
            if (parsed && parsed.cur > 0) return parsed;
          }
        }
      } catch (err) {
        // 部分浏览器权限限制，降级
      }
    }

    // 方案 B: 本地 Tesseract.js 引擎 (若已加载并就绪)
    if (window.Tesseract && typeof window.Tesseract.recognize === 'function') {
      try {
        var res = await window.Tesseract.recognize(canvas, 'eng', {
          tessedit_char_whitelist: '0123456789/Slideof第张幻灯片共页 '
        });
        if (res && res.data && res.data.text) {
          var p = parseSlideNumber(res.data.text);
          if (p && p.cur > 0) return p;
        }
      } catch (e2) {}
    }

    // 方案 C: 毫秒级轻量化纯 JS 投影二值化分析器 (零依赖，离线高可靠)
    return rasterScanDigits(canvas);
  }

  /* 方案 C 的二值化与特征匹配 */
  function rasterScanDigits(canvas) {
    var ctx = canvas.getContext('2d');
    if (!ctx) return null;
    var w = canvas.width, h = canvas.height;
    if (w < 10 || h < 10) return null;

    var img = ctx.getImageData(0, 0, w, h);
    var d = img.data;

    // 1. 判断背景明暗 (以四角取样)
    var bgLum = (lum(d, 0) + lum(d, (w - 1) * 4) + lum(d, (h - 1) * w * 4) + lum(d, (h * w - 1) * 4)) / 4;
    var isDarkBg = bgLum < 128;
    var threshold = isDarkBg ? 155 : 95;

    // 2. 统计每列前景点数量 (垂直投影直方图)
    var colSum = new Int32Array(w);
    for (var x = 0; x < w; x++) {
      var s = 0;
      for (var y = 0; y < h; y++) {
        var l = lum(d, (y * w + x) * 4);
        var isFg = isDarkBg ? (l > threshold) : (l < threshold);
        if (isFg) s++;
      }
      colSum[x] = s;
    }

    // 3. 寻找字符连通块区间
    var boxes = [];
    var inChar = false, startX = 0;
    for (var cx = 0; cx < w; cx++) {
      if (colSum[cx] >= 2) {
        if (!inChar) { inChar = true; startX = cx; }
      } else {
        if (inChar) {
          inChar = false;
          var cw = cx - startX;
          if (cw >= 2 && cw <= 45) {
            boxes.push({ x: startX, w: cw });
          }
        }
      }
    }
    if (inChar && (w - startX >= 2)) {
      boxes.push({ x: startX, w: w - startX });
    }

    if (boxes.length === 0) return null;

    var recognized = '';
    for (var bi = 0; bi < boxes.length; bi++) {
      var b = boxes[bi];
      var ch = matchGlyph(d, w, h, b.x, b.w, isDarkBg, threshold);
      if (ch) recognized += ch;
    }

    return parseSlideNumber(recognized);
  }

  function lum(d, idx) {
    return (d[idx] * 299 + d[idx + 1] * 587 + d[idx + 2] * 114) / 1000;
  }

  function matchGlyph(d, fullW, fullH, gx, gw, isDarkBg, threshold) {
    var minY = fullH, maxY = 0;
    for (var y = 0; y < fullH; y++) {
      for (var x = gx; x < gx + gw; x++) {
        var l = lum(d, (y * fullW + x) * 4);
        var isFg = isDarkBg ? (l > threshold) : (l < threshold);
        if (isFg) {
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    var gh = maxY - minY + 1;
    if (gh < 6) return '';

    var aspect = gw / gh;
    if (aspect < 0.42) return '1';

    var mat = new Uint8Array(8 * 12);
    for (var my = 0; my < 12; my++) {
      for (var mx = 0; mx < 8; mx++) {
        var srcX = Math.min(fullW - 1, Math.floor(gx + (mx / 8) * gw));
        var srcY = Math.min(fullH - 1, Math.floor(minY + (my / 12) * gh));
        var l2 = lum(d, (srcY * fullW + srcX) * 4);
        mat[my * 8 + mx] = (isDarkBg ? (l2 > threshold) : (l2 < threshold)) ? 1 : 0;
      }
    }

    // 斜杠 '/'
    var topRightMass = mat[1 * 8 + 6] + mat[2 * 8 + 5] + mat[3 * 8 + 5];
    var botLeftMass = mat[9 * 8 + 1] + mat[10 * 8 + 2] + mat[11 * 8 + 1];
    var centerHole = mat[5 * 8 + 3] + mat[6 * 8 + 4];
    if (topRightMass >= 2 && botLeftMass >= 2 && centerHole === 0 && aspect < 0.8) {
      return '/';
    }

    // 孔洞与特征
    var midHole = (mat[5 * 8 + 3] === 0 && mat[5 * 8 + 4] === 0 && mat[6 * 8 + 3] === 0 && mat[6 * 8 + 4] === 0);
    var topHole = (mat[3 * 8 + 3] === 0 && mat[3 * 8 + 4] === 0 && mat[4 * 8 + 3] === 0);
    var btmHole = (mat[8 * 8 + 3] === 0 && mat[8 * 8 + 4] === 0 && mat[9 * 8 + 3] === 0);

    if (topHole && btmHole) return '8';
    if (midHole) return '0';
    if (topHole) return '9';
    if (btmHole) return '6';

    var topBar = mat[0 * 8 + 1] + mat[0 * 8 + 2] + mat[0 * 8 + 3] + mat[0 * 8 + 4] + mat[0 * 8 + 5] + mat[0 * 8 + 6];
    if (topBar >= 4) {
      if (mat[11 * 8 + 1] === 0 && mat[11 * 8 + 2] === 0) return '7';
      return '5';
    }

    var botBar = mat[11 * 8 + 1] + mat[11 * 8 + 2] + mat[11 * 8 + 3] + mat[11 * 8 + 4] + mat[11 * 8 + 5] + mat[11 * 8 + 6];
    if (botBar >= 4) return '2';

    var rightCol = mat[4 * 8 + 6] + mat[5 * 8 + 6] + mat[6 * 8 + 6] + mat[7 * 8 + 6] + mat[8 * 8 + 6];
    if (rightCol >= 4 && mat[6 * 8 + 1] === 1) return '4';

    if (mat[1 * 8 + 6] === 1 && mat[6 * 8 + 5] === 1 && mat[10 * 8 + 6] === 1) return '3';

    return '';
  }

  /* ==================================================================
   *  3. 屏幕/标签页视频捕获与实时识别循环 (严格 1000ms 采样一次)
   * ================================================================== */
  async function startCapture(options) {
    options = options || {};
    var onPage = options.onPage;
    var onStatus = options.onStatus;
    var onError = options.onError;

    if (stream) stopCapture();

    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      var err = new Error('当前浏览器环境不支持屏幕捕获 API (getDisplayMedia)');
      if (onError) onError(err);
      throw err;
    }

    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: {
          displaySurface: 'browser'
        },
        audio: false,
        preferCurrentTab: true
      });
    } catch (e) {
      if (onError) onError(e);
      throw e;
    }

    video = document.createElement('video');
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    video.style.position = 'fixed';
    video.style.top = '-9999px';
    video.style.left = '-9999px';
    video.style.opacity = '0';
    video.style.pointerEvents = 'none';
    document.body.appendChild(video);

    // 关键修复: 必须显式调用 play 并等待元数据就绪，否则 video.videoWidth/Height 为 0 导致采样退出
    try {
      await video.play();
    } catch (playErr) {
      console.warn('[OCR] video play warning:', playErr);
    }
    await new Promise(function (resolve) {
      if (video.readyState >= 2 && video.videoWidth > 0) return resolve();
      video.onloadedmetadata = function () {
        video.play().catch(function () {});
        resolve();
      };
      setTimeout(resolve, 800);
    });

    offCanvas = document.createElement('canvas');
    offCtx = offCanvas.getContext('2d', { willReadFrequently: true });

    isScanning = true;
    lastDetectedPage = -1;

    var track = stream.getVideoTracks()[0];
    if (track) {
      track.onended = function () {
        stopCapture();
        if (onStatus) onStatus({ active: false, reason: 'ended' });
      };
    }

    if (onStatus) onStatus({ active: true });

    // 单次识别执行函数
    async function scanOnce() {
      if (!isScanning || !video) return;
      var vw = video.videoWidth;
      var vh = video.videoHeight;
      if (!vw || !vh) return;

      /* 多区域智能梯次采样:
       * 区域 1: 标签页/全屏放映底部栏 (最底 68px, 宽度 70%)
       * 区域 2: 包含 Windows 底部任务栏时的偏移栏 (vh - 125px 至 vh - 45px)
       * 区域 3: 顶部状态指示栏 (前 60px)
       */
      var bands = [
        // 区域 1: 底缘 (常规标签页内放映)
        { x: 0, y: Math.max(0, vh - 68), w: Math.floor(vw * 0.7), h: Math.min(68, vh) },
        // 区域 2: 整屏分享含 Windows 任务栏偏移 (任务栏高约 45px, 状态栏在其上方)
        { x: 0, y: Math.max(0, vh - 128), w: Math.floor(vw * 0.7), h: 65 },
        // 区域 3: 顶缘
        { x: 0, y: 0, w: Math.floor(vw * 0.6), h: Math.min(60, vh) }
      ];

      for (var bi = 0; bi < bands.length; bi++) {
        var b = bands[bi];
        if (b.w <= 0 || b.h <= 0) continue;
        offCanvas.width = b.w;
        offCanvas.height = b.h;
        try {
          offCtx.drawImage(video, b.x, b.y, b.w, b.h, 0, 0, b.w, b.h);
          var res = await recognizeCanvas(offCanvas);
          if (res && res.cur > 0) {
            if (res.cur !== lastDetectedPage) {
              lastDetectedPage = res.cur;
              if (onPage) onPage(res);
            }
            return; // 已精准识别，结束本次扫描
          }
        } catch (err2) {
          // 忽略单帧单区域临时异常
        }
      }
    }

    // 严格按用户要求: 每 1s (1000ms) 采样一次
    scanTimer = setInterval(function () {
      scanOnce().catch(function () {});
    }, 1000);

    // 启动后立即触发首次采样
    setTimeout(function () { scanOnce().catch(function () {}); }, 150);

    return true;
  }

  function stopCapture() {
    isScanning = false;
    if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
    if (stream) {
      stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      stream = null;
    }
    if (video) {
      if (video.parentNode) video.parentNode.removeChild(video);
      video = null;
    }
    offCanvas = null;
    offCtx = null;
    lastDetectedPage = -1;
  }

  function isCapturing() {
    return isScanning && !!stream;
  }

  /* 暴露到全局 */
  global.FLA_OCR = {
    parseSlideNumber: parseSlideNumber,
    recognizeCanvas: recognizeCanvas,
    startCapture: startCapture,
    stopCapture: stopCapture,
    isCapturing: isCapturing
  };

})(typeof window !== 'undefined' ? window : globalThis);
