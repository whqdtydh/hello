// ==UserScript==
// @name         发票助手 · QQ邮箱 Message-ID 登记
// @namespace    invoice-assistant
// @version      2.0.0
// @description  勾选邮件时捕获 mailid，用浏览器同源 fetch 调 QQ“查看原文/导出eml”接口提取真实 Message-ID，POST 到本机服务
// @match        https://wx.mail.qq.com/*
// @match        https://mail.qq.com/*
// @match        https://*.mail.qq.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_notification
// @grant        GM_download
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  'use strict';
  if (window.__invoiceMsgidTampermonkey) return;
  window.__invoiceMsgidTampermonkey = true;

  const LOCAL = 'http://127.0.0.1:18765';
  const DBG_KEY = 'invoice_msgid_dbg';
  const SEEN_KEY = 'invoice_msgid_seen';

  function dbg(msg) {
    let arr = [];
    try { arr = JSON.parse(localStorage.getItem(DBG_KEY) || '[]'); } catch (e) {}
    arr.push('[' + new Date().toTimeString().slice(0, 8) + '] ' + msg);
    if (arr.length > 100) arr = arr.slice(-100);
    try { localStorage.setItem(DBG_KEY, JSON.stringify(arr)); } catch (e) {}
  }

  function seenSet() {
    try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '{}'); } catch (e) { return {}; }
  }
  function seenAdd(mailid) {
    const s = seenSet();
    s[mailid] = (Date.now() / 1000) | 0;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(s)); } catch (e) {}
  }

  function sniffMessageID(text) {
    if (!text) return '';
    let m = text.match(/Message[\s\-]?ID\s*:\s*<([^>]{3,200})>/i);
    if (m && m[1]) return m[1];
    m = text.match(/["']?(?:message[Ii][dD]|message_id|msgid|mid)["']?\s*[:=]\s*["']<([^"']{3,200})>["']/);
    if (m && m[1]) return m[1];
    m = text.match(/["']?(?:message[Ii][dD]|message_id|msgid|mid)["']?\s*[:=]\s*["']([^"']{5,200})["']/);
    if (m && m[1] && /@/.test(m[1])) return m[1];
    return '';
  }

  function currentSid() {
    const u = location.href;
    let m = u.match(/[?&]sid=([^&]+)/);
    if (m && m[1]) return decodeURIComponent(m[1]);
    try {
      const sc = document.querySelectorAll('script');
      for (let i = 0; i < sc.length; i++) {
        m = (sc[i].textContent || '').match(/sid['":= ]+['"]?([A-Za-z0-9_\-]{20,})/);
        if (m && m[1]) return m[1];
      }
    } catch (e) {}
    return '';
  }

  // ---------- 浏览器同源 fetch 尝试 QQ 接口（自动携带 cookie） ----------
  function fetchRaw(mailid) {
    const sid = currentSid();
    const enc = encodeURIComponent(mailid);
    const urls = [
      { name: 'readmail-mode-text', u: 'https://mail.qq.com/cgi-bin/readmail?t=readmail&mailid=' + enc + '&mode=text' },
      { name: 'readmail', u: 'https://mail.qq.com/cgi-bin/readmail?t=readmail&mailid=' + enc },
      { name: 'readmail-sid', u: 'https://mail.qq.com/cgi-bin/readmail?sid=' + sid + '&t=readmail&mailid=' + enc + '&mode=text' },
      { name: 'wx-readmail', u: 'https://wx.mail.qq.com/cgi-bin/readmail?t=readmail&mailid=' + enc + '&mode=text' },
      { name: 'showmail', u: 'https://wx.mail.qq.com/cgi-bin/bizmail_showmail?t=showmail&mailid=' + enc },
    ];
    function tryFetch(i) {
      if (i >= urls.length) return Promise.resolve(null);
      const { name, u } = urls[i];
      return fetch(u, { credentials: 'include', redirect: 'follow' })
        .then(r => r.text())
        .then(text => {
          const mid = sniffMessageID(text);
          dbg('接口 ' + name + ' → len=' + text.length + ' mid=' + (mid || '无'));
          if (mid) return { mid: mid, name: name, len: text.length };
          return tryFetch(i + 1);
        })
        .catch(e => { dbg('接口 ' + name + ' 错误: ' + e.message); return tryFetch(i + 1); });
    }
    return tryFetch(0);
  }

  // ---------- 尝试触发“导出 eml”下载，从下载 URL/内容里拿 Message-ID ----------
  // QQ 邮箱新版在邮件详情页“更多”菜单里有“导出为eml文件”。下载内容就是原始邮件，
  // 一定含 Message-ID。我们 hook 下载完成后的 Blob 无法直接读，改为：
  //   用 GM_download 下载到本地临时文件，再用 fetch 读回（同源 GM_download 支持 onload）。
  function tryExportEml(mailid) {
    return new Promise((resolve) => {
      // 构造导出 URL（不同版本接口）
      const sid = currentSid();
      const enc = encodeURIComponent(mailid);
      const urls = [
        'https://mail.qq.com/cgi-bin/readmail?t=readmail&mailid=' + enc + '&mode=eml',
        'https://mail.qq.com/cgi-bin/readmail?t=readmail&mailid=' + enc + '&mode=download',
        'https://wx.mail.qq.com/cgi-bin/readmail?t=readmail&mailid=' + enc + '&mode=eml',
      ];
      let idx = 0;
      const tryOne = () => {
        if (idx >= urls.length) { resolve(null); return; }
        const u = urls[idx++];
        fetch(u, { credentials: 'include' })
          .then(r => {
            const ct = r.headers.get('content-type') || '';
            if (ct.indexOf('text/html') >= 0) {
              return r.text().then(t => {
                const mid = sniffMessageID(t);
                dbg('eml ' + u.slice(-60) + ' → html mid=' + (mid || '无'));
                if (mid) return { mid };
                return tryOne();
              });
            }
            return r.arrayBuffer().then(buf => {
              const bytes = new Uint8Array(buf);
              // 从二进制里找 ASCII "Message-ID:"
              let text = '';
              try { text = new TextDecoder('latin1').decode(bytes.slice(0, 300000)); } catch (e) {}
              const mid = sniffMessageID(text);
              dbg('eml ' + u.slice(-60) + ' → bytes=' + buf.byteLength + ' mid=' + (mid || '无'));
              if (mid) return { mid };
              return tryOne();
            });
          })
          .catch(e => { dbg('eml 错误: ' + e.message); return tryOne(); });
      };
      tryOne();
    });
  }

  function postLocal(data) {
    return new Promise((resolve) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: LOCAL + '/record',
        data: JSON.stringify(data),
        headers: { 'Content-Type': 'application/json' },
        timeout: 3000,
        onload: () => resolve(true),
        onerror: () => resolve(false),
        ontimeout: () => resolve(false),
      });
    });
  }

  async function handleChecked(item, mailid, sender, subject, time) {
    if (seenSet()[mailid]) return;
    dbg('勾选: ' + mailid + ' | ' + subject);
    let message_id = '';
    try {
      const res = await fetchRaw(mailid);
      if (res) message_id = res.mid;
    } catch (e) {}
    if (!message_id) {
      // 兜底 1：网络 hook 历史
      try {
        const net = JSON.parse(localStorage.getItem('invoice_msgids') || '{}');
        const keys = Object.keys(net);
        if (keys.length) message_id = keys[0];
      } catch (e) {}
    }
    if (!message_id) {
      // 兜底 2：导出 eml
      try {
        const eml = await tryExportEml(mailid);
        if (eml) message_id = eml.mid;
      } catch (e) {}
    }
    if (!message_id) {
      dbg('全部途径都未提取到 Message-ID: ' + mailid);
      try { if (item) item.click(); } catch (e) {} // 打开详情，触发读信接口
      return;
    }
    const ok = await postLocal({ mailid, message_id, subject, time });
    dbg('POST ' + (ok ? '成功' : '失败') + ' -> ' + message_id);
    if (ok) seenAdd(mailid);
    if (ok && GM_notification) {
      try {
        GM_notification({ title: '发票助手', text: '已登记 Message-ID：' + message_id, timeout: 2000 });
      } catch (e) {}
    }
  }

  function readItemInfo(item) {
    const mailid = (item.getAttribute('data-mailid') || '').trim();
    let sender = '', subject = '', time = '';
    const s = item.querySelector('.mail-sender,.mail-name,[class*=sender]');
    if (s) sender = (s.innerText || '').trim().slice(0, 80);
    const sj = item.querySelector('[class*=subject],[class*=title],[class*=topic],[class*=summary]');
    if (sj) subject = (sj.innerText || '').trim().slice(0, 120);
    const tm = item.querySelector('[class*=time],[class*=date]');
    if (tm) time = (tm.innerText || '').trim().slice(0, 20);
    return { sender, subject, time };
  }

  function isChecked(item) {
    const cls = item.className || '';
    const cbIcon = item.querySelector('.ui-checkbox-icon-checked');
    return /mail-item-checked/.test(cls) || !!cbIcon || /sel|selected|current|active|checked/i.test(cls);
  }

  // hook 网络：所有响应里找 Message-ID（QQ 读信接口若返回原文就有）
  function tryRecord(url, body) {
    if (!body) return;
    const mid = sniffMessageID(body);
    if (!mid) return;
    dbg('网络命中 Message-ID: ' + mid + '  @ ' + String(url).slice(0, 120));
    try {
      const map = JSON.parse(localStorage.getItem('invoice_msgids') || '{}');
      map[mid] = mid;
      localStorage.setItem('invoice_msgids', JSON.stringify(map));
    } catch (e) {}
  }
  const _fetch = window.fetch;
  if (_fetch) {
    window.fetch = function () {
      const args = arguments;
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
      return _fetch.apply(this, args).then(function (resp) {
        try { resp.clone().text().then(function (t) { tryRecord(url, t); }); } catch (e) {}
        return resp;
      });
    };
  }
  const _open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, url) { this._url = url; return _open.apply(this, arguments); };
  const _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    const self = this;
    this.addEventListener('load', function () {
      try { tryRecord(self._url, self.responseText || ''); } catch (e) {}
    });
    return _send.apply(this, arguments);
  };

  // 监听勾选
  document.addEventListener('click', function (e) {
    const cb = e.target.closest ? e.target.closest('.mail-checkbox,.xmail-ui-checkbox') : null;
    if (!cb) return;
    const item = cb.closest ? cb.closest('div[class*=list-item]') : null;
    if (!item) return;
    const mailid = (item.getAttribute('data-mailid') || '').trim();
    if (!mailid) return;
    if (!isChecked(item)) return;
    const info = readItemInfo(item);
    handleChecked(item, mailid, info.sender, info.subject, info.time);
  }, true);

  // MutationObserver 补登记（全选等场景）
  const _observer = new MutationObserver(function () {
    try {
      const items = document.querySelectorAll('div[class*=list-item]');
      for (let i = 0; i < items.length; i++) {
        const el = items[i];
        const mailid = (el.getAttribute('data-mailid') || '').trim();
        if (!mailid || !isChecked(el) || seenSet()[mailid]) continue;
        const info = readItemInfo(el);
        handleChecked(el, mailid, info.sender, info.subject, info.time);
      }
    } catch (e) {}
  });
  try { _observer.observe(document.body, { childList: true, subtree: true }); } catch (e) {}

  setTimeout(function () {
    try {
      const items = document.querySelectorAll('div[class*=list-item]');
      for (let i = 0; i < items.length; i++) {
        const el = items[i];
        const mailid = (el.getAttribute('data-mailid') || '').trim();
        if (!mailid || !isChecked(el) || seenSet()[mailid]) continue;
        const info = readItemInfo(el);
        handleChecked(el, mailid, info.sender, info.subject, info.time);
      }
    } catch (e) {}
  }, 1500);

  dbg('油猴脚本 v2 已加载');
})();