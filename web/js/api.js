/* FLA - API 封装 (fetch + XHR 上传进度) */
(function () {
  'use strict';
  // localStorage 不可用时(沙盒预览等)自动降级为内存存储
  const store = (function () {
    try { localStorage.setItem('__t', '1'); localStorage.removeItem('__t'); return localStorage; }
    catch (e) { const m = {}; return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: k => { delete m[k]; } }; }
  })();

  let token = store.getItem('token') || '';

  function errText(d) {
    if (!d || !d.detail) return '';
    if (typeof d.detail === 'string') return d.detail;
    if (Array.isArray(d.detail)) return d.detail.map(x => x.msg || '').filter(Boolean).join('; ');
    return JSON.stringify(d.detail);
  }

  window.API = {
    get token() { return token; },
    setToken(t) { token = t || ''; if (t) store.setItem('token', t); else store.removeItem('token'); },

    async req(path, opts) {
      opts = opts || {};
      const headers = {};
      if (token) headers.Authorization = 'Bearer ' + token;
      if (opts.json !== undefined) {
        headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.json);
        opts.method = opts.method || 'POST';
      } else if (opts.method && opts.method !== 'GET' && opts.method !== 'HEAD' && !opts.form && opts.body === undefined) {
        headers['Content-Type'] = 'application/json';
        opts.body = '{}';
      }
      if (opts.form) { opts.body = opts.form; opts.method = opts.method || 'POST'; }
      let r;
      const timeoutMs = opts.timeout || (opts.form ? 0 : 8000);
      let ctrl = null, timer = null;
      if (timeoutMs > 0 && typeof AbortController !== 'undefined') {
        ctrl = new AbortController();
        opts.signal = ctrl.signal;
        timer = setTimeout(() => ctrl.abort(), timeoutMs);
      }
      try {
        try {
          r = await fetch(path, Object.assign({}, opts, { headers }));
        } catch (e) {
          if (e && e.name === 'AbortError') throw new Error('请求超时，请检查网络');
          throw new Error('网络错误，请检查连接');
        }
        if (r.status === 401 && path.indexOf('/api/auth/login') < 0 && path.indexOf('/api/auth/register') < 0 && path.indexOf('/api/auth/qr/') < 0) {
          API.setToken('');
          location.hash = '#/login';
          throw new Error('登录已过期，请重新登录');
        }
        let data = null;
        const ct = r.headers.get('content-type') || '';
        // Keep the deadline active until the response body is fully consumed. Clearing it after
        // fetch() resolves only covers headers; a stalled JSON body otherwise leaves pages loading forever.
        if (ct.indexOf('json') >= 0) data = await r.json();
        if (!r.ok) throw new Error(errText(data) || ('请求失败 (' + r.status + ')'));
        return data;
      } catch (e) {
        if (e && e.name === 'AbortError') throw new Error('请求超时，请检查网络');
        throw e;
      } finally {
        if (timer) clearTimeout(timer);
      }
    },

    get(p) { return API.req(p); },
    post(p, json, timeout) { return API.req(p, { json, method: 'POST', timeout: timeout || 0 }); },   /* v3.6: 可选超时(AI 生成较慢) */   /* v1.26 修: 无 body 也保持 POST (qr/ticket) */
    put(p, json) { return API.req(p, { json, method: 'PUT' }); },
    patch(p, json) { return API.req(p, { json, method: 'PATCH' }); },   /* v1.26: 论坛/聊天/公告 */
    del(p) { return API.req(p, { method: 'DELETE' }); },

    upload(path, form, onProgress) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', path);
        if (token) xhr.setRequestHeader('Authorization', 'Bearer ' + token);
        xhr.upload.onprogress = e => {
          if (e.lengthComputable && onProgress) onProgress(Math.round(e.loaded / e.total * 100));
        };
        xhr.onload = () => {
          let d = null;
          try { d = JSON.parse(xhr.responseText); } catch (e) { }
          if (xhr.status >= 200 && xhr.status < 300) resolve(d);
          else reject(new Error(errText(d) || ('上传失败 (' + xhr.status + ')')));
        };
        xhr.onerror = () => reject(new Error('网络错误'));
        xhr.send(form);
      });
    },
  };
})();
