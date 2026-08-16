"""基于 QtWebEngine 的邮箱页面控制。

自动化由 runJavaScript 驱动；下载采用「批量原生下载」：
点击附件下载按钮后，让 QtWebEngine 原生把文件保存到指定命名路径
（不重放 URL、不依赖 cookie 抓取，速度快且稳定），同时保留嗅探 URL 兜底。
"""

import json
import os
import re
import time

import requests
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from app import config
from app.engine.cookie_store import CookieStore


class AttachmentInterceptor(QWebEngineUrlRequestInterceptor):
    """嗅探附件下载请求：捕获真正的附件下载 URL（mail.qq.com/attach/download）。

    拦截器捕获到的下载 URL 会附带 sid 会话参数，可直接用 requests 携带 cookie
    重放拉取。为避免误捕获性能监控 / 静态资源，只匹配下载接口路径。
    """

    DOWNLOAD_HINTS = ("/attach/download", "attach/download", "disp=down", "file_download")

    def __init__(self, urls: list, req_urls: list = None):
        super().__init__()
        self._urls = urls
        self._req_urls = req_urls if req_urls is not None else []

    def interceptRequest(self, info):
        url = info.requestUrl().toString().lower()
        if any(h in url for h in self.DOWNLOAD_HINTS):
            self._urls.append(info.requestUrl().toString())
            # 不 block：让浏览器继续发请求（保持会话一致），下载结果由 requests 拉取。
        # 诊断：记录所有 mail.qq.com 域名的请求 URL（定位列表/详情接口）
        if "mail.qq.com" in url and not any(s in url for s in
                (".js", ".css", ".png", ".jpg", ".gif", ".woff", ".wasm", ".svg", "icon")):
            full = info.requestUrl().toString()
            if full not in self._req_urls:
                self._req_urls.append(full)
                if len(self._req_urls) > 500:
                    del self._req_urls[:100]


class WebClient(QObject):
    """封装 QWebEngineView，提供邮箱自动化所需的原子操作 + 下载。"""

    log_signal = Signal(str)

    def __init__(self, view: QWebEngineView, parent=None):
        super().__init__(parent)
        self.view = view
        self.page = view.page()
        self.profile = self.page.profile()

        # 持久化会话（cookie 保存到磁盘，重启后免登录）
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setCachePath(config.PROFILE_CACHE_DIR)
        self.profile.setPersistentStoragePath(config.PROFILE_STORAGE_DIR)

        self._sniffed_urls = []          # 拦截器捕获的下载 URL
        self._req_urls = []              # 拦截器捕获的 QQ 接口请求 URL（诊断）
        self._download_items = {}        # 下载句柄队列
        self._interceptor = AttachmentInterceptor(self._sniffed_urls, self._req_urls)
        self.profile.setUrlRequestInterceptor(self._interceptor)
        self.profile.downloadRequested.connect(self._on_download_requested)

        # cookie 收集器（请求时供 requests 复用会话）
        self.cookies = CookieStore(self.profile, config.COOKIE_FILE)

        self.pending_dest = None          # 兼容旧接口：单目标路径
        self._pending_dests = []          # 批量下载目标路径队列
        self._finished_dests = []         # 已完成下载的目标路径列表

    # ---------- JS 执行 ----------
    def run_js(self, script, timeout=20000):
        """执行 JS 并返回结果。

        注意：QtWebEngine 的 runJavaScript 对 JS 对象/数组返回空字符串，
        因此本方法会包裹脚本，把对象/数组统一 JSON.stringify 成字符串返回。
        调用方需用 run_js_obj 解析为 Python 对象。
        """
        holder = {}

        def _cb(result):
            holder["value"] = result

        wrapped = (
            "(function(){"
            "try{"
            "var __r=(" + script + ");"
            "if(__r&&typeof __r==='object'){return JSON.stringify(__r);}"
            "return __r;"
            "}catch(e){return '__JS_ERR__:'+e.message;}"
            "})()"
        )
        self.page.runJavaScript(wrapped, 0, _cb)
        loop = QEventLoop(self)
        QTimer.singleShot(timeout, loop.quit)
        loop.exec()
        value = holder.get("value")
        if isinstance(value, str) and value.startswith("__JS_ERR__:"):
            raise RuntimeError(value[len("__JS_ERR__:"):])
        return value

    def run_js_obj(self, script, timeout=20000):
        """执行 JS，自动把 JSON 字符串解析为 Python 对象。"""
        value = self.run_js(script, timeout=timeout)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    @staticmethod
    def qt_sleep(seconds):
        """保活睡眠：等待期间让 Qt 事件循环持续运转（QtWebEngine 才能处理
        页面加载、JS 回调、登录跳转等）。绝不能使用 time.sleep 阻塞主循环。"""
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def navigate(self, url):
        self.view.load(url)

    def current_url(self):
        return self.page.url().toString()

    def page_info(self):
        """底层诊断：直接读 Qt 层的 URL/title/加载状态（不走 JS，绕过 JS 失效场景）。"""
        try:
            return {
                "url": self.page.url().toString(),
                "title": self.page.title(),
                "loading": self.page.isLoading(),
                "view_visible": self.view.isVisible(),
            }
        except Exception as e:
            return {"error": str(e)}

    def dump_page_html(self):
        """诊断：把当前页面完整 HTML 写到 TEMP 文件，返回 (路径, 大小, 摘要)。
        用于 JS 上下文失效时直接分析页面结构。"""
        js = r"""
(function(){
  var h = document.documentElement ? document.documentElement.outerHTML : '';
  var t = document.body ? (document.body.innerText||'').trim().replace(/\s+/g,' ').slice(0,200) : '';
  // 详情页特征检测
  var detail = {
    has_attach: !!document.querySelector('.mail-detail-attach-card,[class*="attach-card"]'),
    has_mail_body: !!document.querySelector('[class*="mail-detail"],[class*="mail-body"],[class*="detail-body"],[class*="mail-content"]'),
    has_list: !!document.querySelector('div[class*=list-item]'),
    hash: (location.hash||'').slice(0,80)
  };
  return {len: h.length, head: h.slice(0, 3000), body_text: t, url: (location.href||''), detail: detail};
})()
"""
        try:
            r = self.run_js_obj(js, timeout=15000)
            if not r or not r.get("len"):
                return {"ok": False, "reason": "JS 返回空（上下文失效）"}
            p = os.path.join(os.environ.get("TEMP", "/tmp"), "invoice_page_dump.html")
            with open(p, "w", encoding="utf-8", errors="replace") as f:
                f.write(r.get("head", ""))
            return {"ok": True, "path": p, "len": r.get("len"),
                    "body_text": r.get("body_text", "")[:150],
                    "detail": r.get("detail", {})}
        except Exception as e:
            return {"ok": False, "reason": str(e)[:80]}

    def wait_loaded(self, timeout=30000):
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            ready = self.run_js("document.readyState", timeout=3000)
            if ready == "complete":
                return True
            self.qt_sleep(0.5)
        return False

    # ---------- 登录检测 ----------
    def get_page_state(self):
        """返回页面实时状态字典，用于诊断：url/title/body长度/关键文本/列表数。"""
        js = (
            "(function(){"
            "var b=document.body;"
            "var t=b?b.innerText||'':'';"
            "return {"
            "  title:document.title||'',"
            "  len:t.length,"
            "  hasInbox:t.indexOf('收件箱')>=0,"
            "  hasQr:document.querySelector('iframe')!==null,"
            "  items:document.querySelectorAll('div[class*=list-item]').length"
            "};})()"
        )
        state = self.run_js_obj(js, timeout=8000) or {}
        state["url"] = self.current_url()
        return state

    def is_logged_in(self):
        """已登录判定：URL 含 sid= 且页面正文出现「收件箱」。"""
        url = self.current_url()
        if "sid=" not in url:
            return False
        return bool(self.run_js(
            "(function(){var b=document.body;if(!b)return false;"
            "var t=b.innerText;return t.indexOf('收件箱')>=0;} )()",
            timeout=8000))

    def get_sid(self):
        """获取当前 QQ 邮箱会话 sid。

        优先级：
          1) cookie xm_sid
          2) cookie sid
          3) 当前页面 URL 的 sid= 参数（QQ 邮箱页面 URL 必带 sid）
          4) 页面 JS 里常见变量（window.sid / location.href）
        返回 sid 字符串；拿不到返回 ""。
        注意：本方法访问 Qt 对象（current_url），须在 GUI 线程调用。
        """
        sid = ""
        try:
            sid = self.cookies.get_cookie_value("xm_sid") or ""
        except Exception:
            pass
        if not sid:
            try:
                sid = self.cookies.get_cookie_value("sid") or ""
            except Exception:
                pass
        if not sid:
            try:
                url = self.current_url()
                m = re.search(r"[?&]sid=([^&#]+)", url)
                if m:
                    sid = m.group(1)
            except Exception:
                pass
        if not sid:
            try:
                raw = self.run_js(
                    "(function(){"
                    "try{return window.sid||(location.href.match(/[?&]sid=([^&#]+)/)||[])[1]||'';}"
                    "catch(e){return '';}})()",
                    timeout=8000)
                if raw and isinstance(raw, str):
                    sid = raw.strip()
            except Exception:
                pass
        return sid

    def wait_page_ready(self, timeout=120, need_inbox=False, on_status=None):
        """等待页面达到可用状态。

        条件：URL 含 sid= 且（need_inbox 时）正文出现「收件箱」。
        返回 True 表示就绪，False 表示超时。
        on_status: 可选回调(str)，用于报告等待进度。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            url = self.current_url()
            has_sid = "sid=" in url
            if has_sid:
                if not need_inbox:
                    return True
                try:
                    if self.is_logged_in():
                        return True
                    elif on_status:
                        on_status(f"已获取会话，等待邮件界面… (URL: {url[:70]})")
                except Exception as e:
                    if on_status:
                        on_status(f"检测异常: {str(e)[:60]}")
            elif on_status:
                on_status(f"等待登录会话(sid)… (URL: {url[:70]})")
            self.qt_sleep(1.5)
        return False

    # ---------- 邮件列表 / 勾选 ----------
    def list_mails(self):
        js = (
            "(function(){"
            "var els=document.querySelectorAll('div[class*=list-item]');"
            "var out=[];"
            "for(var i=0;i<els.length;i++){"
            "  var el=els[i];"
            "  var cls=el.className||'';"
            "  // QQ 新版勾选标记：item 加 mail-item-checked，勾选图标换 checked"
            "  var cbIcon=el.querySelector('.ui-checkbox-icon-checked');"
            "  var selected=/mail-item-checked/.test(cls) || !!cbIcon"
            "    || /sel|selected|current|active|checked/i.test(cls);"
            "  out.push({index:i,checked:selected,selected:selected,"
            "            mailid:el.getAttribute('data-mailid')||'',"
            "            cls:cls,text:(el.innerText||'').slice(0,200),"
            "            time:(el.innerText||'').match(/(\\d{1,2}:\\d{2})/)?"
            "                 (el.innerText||'').match(/(\\d{1,2}:\\d{2})/)[1]:''});"
            "}"
            "return out;})()"
        )
        return self.run_js_obj(js, timeout=15000) or []

    def wait_mail_list(self, timeout=30):
        """等待邮件列表 DOM 渲染完成（list-item 数量>0）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            n = self.run_js(
                "document.querySelectorAll('div[class*=list-item]').length",
                timeout=8000)
            if n and n > 0:
                return True
            self.qt_sleep(1.5)
        return False

    TRACKER_SCRIPT = r'''
(function(){
  if(window.__invoiceTrackerInstalled)return;
  window.__invoiceTrackerInstalled=true;
  var KEY='invoice_selected';
  var MIDMAP='invoice_msgid_map';
  function senderOf(item){
    var s=item.querySelector('.mail-sender,.mail-name,[class*=sender]');
    return s?(s.innerText||'').trim().slice(0,80):'';
  }
  function subjOf(item){
    var s=item.querySelector('[class*=subject],[class*=title],[class*=topic],[class*=summary]');
    return s?(s.innerText||'').trim().slice(0,120):'';
  }
  function timeOf(item){
    var s=item.querySelector('[class*=time],[class*=date]');
    return s?(s.innerText||'').trim().slice(0,20):'';
  }
  function readAll(){
    try{return JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){return {};}
  }
  function saveAll(o){
    try{localStorage.setItem(KEY, JSON.stringify(o));}catch(e){}
  }
  // ---------- Message-ID 提取 ----------
  function sniffMessageID(text){
    if(!text) return '';
    var m=text.match(/Message[\s\-]?ID\s*:\s*<([^>]{3,200})>/i);
    if(m&&m[1]) return m[1];
    m=text.match(/["']?(?:message[Ii][dD]|message_id|msgid|mid)["']?\s*[:=]\s*["']<([^"']{3,200})>["']/);
    if(m&&m[1]) return m[1];
    m=text.match(/["']?(?:message[Ii][dD]|message_id|msgid|mid)["']?\s*[:=]\s*["']([^"']{5,200})["']/);
    if(m&&m[1]&&/@/.test(m[1])) return m[1];
    return '';
  }
  function currentSid(){
    var u=location.href;
    var m=u.match(/[?&]sid=([^&#]+)/);
    if(m&&m[1]) return decodeURIComponent(m[1]);
    return '';
  }
  function midMap(){
    try{return JSON.parse(localStorage.getItem(MIDMAP)||'{}');}catch(e){return {};}
  }
  var MDBG='invoice_msgid_dbg';
  function mdbg(msg){
    var arr=[];try{arr=JSON.parse(localStorage.getItem(MDBG)||'[]');}catch(e){}
    arr.push('['+new Date().toTimeString().slice(0,8)+'] '+msg);
    if(arr.length>100)arr=arr.slice(-100);
    try{localStorage.setItem(MDBG,JSON.stringify(arr));}catch(e){}
  }
  function saveMid(mailid, mid){
    if(!mid) return;
    var mm=midMap();
    mm[mailid]=mid;
    try{localStorage.setItem(MIDMAP, JSON.stringify(mm));}catch(e){}
  }
  // 依次尝试 QQ 接口，返回 Message-ID 或 ''
    function fetchRaw(mailid){
    var sid=currentSid();
    var enc=encodeURIComponent(mailid);
    var urls=[
      // 导出 eml：返回完整原始邮件（含 Message-ID）
      'https://mail.qq.com/cgi-bin/readmail?t=readmail&mailid='+enc+'&mode=eml',
      'https://mail.qq.com/cgi-bin/readmail?t=readmail&mailid='+enc+'&mode=text',
      'https://mail.qq.com/cgi-bin/readmail?t=readmail&mailid='+enc,
      'https://mail.qq.com/cgi-bin/readmail?sid='+sid+'&t=readmail&mailid='+enc+'&mode=text',
      'https://wx.mail.qq.com/cgi-bin/readmail?t=readmail&mailid='+enc+'&mode=eml',
      'https://wx.mail.qq.com/cgi-bin/readmail?t=readmail&mailid='+enc+'&mode=text',
      'https://wx.mail.qq.com/cgi-bin/bizmail_showmail?t=showmail&mailid='+enc
    ];
    function tryFetch(i){
      if(i>=urls.length) return Promise.resolve(null);
      var name=urls[i].slice(0,80);
      return fetch(urls[i], {credentials:'include', redirect:'follow'})
        .then(function(r){
          mdbg('接口 '+name+' HTTP '+r.status+' ct='+(r.headers.get('content-type')||'').slice(0,20));
          return r.text();
        })
        .then(function(t){
          var mid=sniffMessageID(t);
          if(i===0){
            try{localStorage.setItem('invoice_readmail_snip', (t||'').slice(0,500));}catch(e){}
          }
          mdbg('接口 '+name+' len='+t.length+' mid='+(mid||'无'));
          if(mid) return {mid:mid};
          return tryFetch(i+1);
        })
        .catch(function(e){mdbg('接口 '+name+' 错误: '+(e.message||e));return tryFetch(i+1);});
    }
    return tryFetch(0);
  }
  function grabMsgID(mailid){
    if(midMap()[mailid]) return Promise.resolve(midMap()[mailid]);
    return fetchRaw(mailid).then(function(res){
      if(res&&res.mid){saveMid(mailid,res.mid);return res.mid;}
      // 兜底：网络 hook 捕获的历史
      try{
        var net=JSON.parse(localStorage.getItem('invoice_msgids')||'{}');
        var keys=Object.keys(net);
        if(keys.length){saveMid(mailid,keys[0]);return keys[0];}
      }catch(e){}
      return '';
    });
  }
  // 网络 hook：响应里直接找 Message-ID
  function tryRecord(url, body){
    if(!body) return;
    // 诊断：记录所有 QQ 域名接口的 URL + 响应片段（不限路径，用于定位列表接口）
    var u=(url||'');
    if(/mail\.qq\.com|ex\.mail\.qq\.com|qiye\.mail\.qq\.com|qqmail|\.qq\.com/.test(u)
       && !/\.(png|jpg|jpeg|gif|css|js|woff|svg|webp)/.test(u)){
      var recs=[];
      try{recs=JSON.parse(localStorage.getItem('invoice_net_dbg')||'[]');}catch(e){}
      var head=(body||'').slice(0,80);
      var line='GET '+u.slice(0,160)+' => len='+body.length
               +' head='+JSON.stringify(head);
      // 去重：同 URL 同长度只记一次，避免刷屏
      if(recs.length===0 || recs[recs.length-1]!==line){
        recs.push(line);
        if(recs.length>400)recs=recs.slice(-400);
        try{localStorage.setItem('invoice_net_dbg', JSON.stringify(recs));}catch(e){}
      }
      // 完整保存 list/maillist 响应（诊断加密格式）
      if(/list\/maillist/.test(u) || /list\/top_maillist/.test(u)){
        try{localStorage.setItem('invoice_maillist_raw', body.slice(0, 3000));}catch(e){}
      }
    }
    var mid=sniffMessageID(body);
    if(!mid) return;
    try{
      var map=JSON.parse(localStorage.getItem('invoice_msgids')||'{}');
      map[mid]=mid;
      localStorage.setItem('invoice_msgids', JSON.stringify(map));
    }catch(e){}
  }
  var _fetch=window.fetch;
  if(_fetch){
    window.fetch=function(){
      var args=arguments;
      var url=typeof args[0]==='string'?args[0]:(args[0]&&args[0].url)||'';
      return _fetch.apply(this,args).then(function(resp){
        try{resp.clone().text().then(function(t){tryRecord(url,t);});}catch(e){}
        return resp;
      });
    };
  }
  var _open=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(m,url){
    this._url=url; return _open.apply(this,arguments);
  };
  var _send=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send=function(){
    var self=this;
    this.addEventListener('readystatechange',function(){
      if(self.readyState===4){
        try{
          tryRecord(self._url, self.responseText||'');
          // 完整保存 maillist 响应
          var u=(self._url||'');
          if(/list\/maillist|list\/top_maillist/.test(u)){
            try{localStorage.setItem('invoice_maillist_full', (self.responseText||'').slice(0,100000));}catch(e){}
          }
        }catch(e){}
      }
    });
    return _send.apply(this,arguments);
  };

  // JSON.parse hook：拦截 QQ 解密后的明文 JSON（含邮件列表数据）
  (function(){
    var _jp = window.JSON.parse;
    window.__invoiceInParse = window.__invoiceInParse || false;
    window.JSON.parse = function(text){
      var r;
      try{ r = _jp(text); }catch(e){ throw e; }
      if(window.__invoiceInParse){ return r; }  // 防重入（防无限递归）
      window.__invoiceInParse = true;
      try{
        if(typeof text==='string' && text.length>200 &&
           (/maillist/.test(text) || /fid/.test(text) || /sender/.test(text)
            || /subject/.test(text) || text.indexOf('"data"')>=0)){
          localStorage.setItem('invoice_json_hook', text.slice(0,100000));
          // 独立保存 maillist 全文
          if(/xmlistlogicsvr\/maillist/.test(text)){
            localStorage.setItem('invoice_maillist_json', text.slice(0,200000));
            // 追加保存，避免被覆盖
            try{
              var mls=[];
              try{mls=_jp(localStorage.getItem('invoice_maillists')||'[]');}catch(e){}
              mls.push(text.slice(0,200000));
              if(mls.length>5)mls=mls.slice(-5);
              localStorage.setItem('invoice_maillists', JSON.stringify(mls));
            }catch(e){}
            // ★ 关键：把 maillist 里的 emailid→messageid 写入 msgid 映射
            try{
              var _err='';
              try{
                var ml = _jp(text);
                var bl = ml && ml.body;
                var lst = bl && bl.list;
                if(lst && lst.length){
                  var midmap = {};
                  try{ midmap = _jp(localStorage.getItem('invoice_msgid_map')||'{}'); }catch(e){ midmap={}; }
                  var added = 0;
                  for(var mi=0;mi<lst.length;mi++){
                    var e0 = lst[mi];
                    var eid = e0 && (e0.emailid || e0.mailid || e0.id || '');
                    var emid = e0 && (e0.messageid || e0.messageId || '');
                    if(eid && emid){
                      if(!midmap[eid] || midmap[eid].length < emid.length){
                        midmap[eid] = emid;
                        added++;
                      }
                    }
                  }
                  if(added>0){
                    localStorage.setItem('invoice_msgid_map', JSON.stringify(midmap));
                    _err='OK +'+added+' 共'+(Object.keys(midmap).length||0);
                  } else {
                    _err='list len='+lst.length+' 但无(emailid,messageid)对';
                  }
                } else {
                  _err='body.list 为空: body='+(typeof bl)+' list='+(typeof lst);
                }
              }catch(e){ _err='异常:'+e.message; }
              try{
                var md=[];
                try{md=_jp(localStorage.getItem('invoice_msgid_map_log')||'[]');}catch(e){}
                md.push('maillist 建映射: '+_err+' @'+new Date().toTimeString().slice(0,8));
                if(md.length>30)md=md.slice(-30);
                localStorage.setItem('invoice_msgid_map_log', JSON.stringify(md));
              }catch(e){}
            }catch(e){}
          }
          // 独立保存 mailid 为键的邮件详情对象（如 {"ZL...":{"mailid":...}}）
          if(text.indexOf('"mailid"')>=0 && text.length<20000
             && !/xmlistlogicsvr\/maillist/.test(text)
             && text.indexOf('"head"')<0){
            localStorage.setItem('invoice_mailobj_json', text.slice(0,20000));
            // 追加保存，避免被覆盖
            try{
              var objs=[];
              try{objs=_jp(localStorage.getItem('invoice_mailobjs')||'[]');}catch(e){}
              objs.push(text.slice(0,5000));
              if(objs.length>30)objs=objs.slice(-30);
              localStorage.setItem('invoice_mailobjs', JSON.stringify(objs));
            }catch(e){}
          }
          try{
            var arr=[];
            try{arr=_jp(localStorage.getItem('invoice_json_hook_list')||'[]');}catch(e){}
            arr.push('len='+text.length+' head='+JSON.stringify(text.slice(0,120)));
            if(arr.length>50)arr=arr.slice(-50);
            localStorage.setItem('invoice_json_hook_list', JSON.stringify(arr));
          }catch(e){}
        }
      }catch(e){}
      window.__invoiceInParse = false;
      return r;
    };
  })();

  // WebSocket 钩子：QQ 新版列表可能走 WebSocket 推送
  if(window.WebSocket){
    var _WS=window.WebSocket;
    window.WebSocket=function(url, protocols){
      var ws = (typeof protocols==='undefined') ? new _WS(url) : new _WS(url, protocols);
      var origAdd=ws.addEventListener;
      ws.addEventListener=function(type, fn, opts){
        if(type==='message'){
          ws.addEventListener('message', function(ev){
            try{
              var data = ev.data||'';
              var s = (typeof data==='string')?data:(data&&data.data)?String(data.data):'';
              if(s && /mail|list|msg|json|sender|subject/i.test(s.slice(0,60))){
                tryRecordWS(url, s);
              }
            }catch(e){}
          });
          return;
        }
        return origAdd.call(this, type, fn, opts);
      };
      return ws;
    };
    window.WebSocket.prototype=_WS.prototype;
  }
  function tryRecordWS(url, body){
    if(!body) return;
    try{
      var recs=[];
      try{recs=JSON.parse(localStorage.getItem('invoice_ws_dbg')||'[]');}catch(e){}
      var line='WS '+url.slice(0,120)+' => len='+body.length+' head='+JSON.stringify(body.slice(0,120));
      if(recs.length===0 || recs[recs.length-1]!==line){
        recs.push(line);
        if(recs.length>200)recs=recs.slice(-200);
        localStorage.setItem('invoice_ws_dbg', JSON.stringify(recs));
      }
    }catch(e){}
  }

  document.addEventListener('click', function(e){
    var t=e.target;
    var cb=t.closest?t.closest('.mail-checkbox,.xmail-ui-checkbox'):null;
    if(!cb)return;
    var item=cb.closest?cb.closest('div[class*=list-item]'):null;
    if(!item)return;
    var mailid=item.getAttribute('data-mailid')||'';
    // 探查：勾选那一刻该邮件项上所有属性 + HTML（诊断用）
    try{
      var attrs={}; for(var i=0;i<item.attributes.length;i++){var a=item.attributes[i];attrs[a.name]=a.value;}
      var reactKey=item.getAttribute('_reactKey')||item.getAttribute('key')||'';
      var vue=item.__vue__||(item.parentNode&&item.parentNode.__vue__)||null;
      var probe={mailid:mailid,attrs:attrs,reactKey:reactKey,
                 html:(item.outerHTML||'').slice(0,1500),
                 vueData:vue?(JSON.stringify(vue.$attrs||{}).slice(0,400)):''};
      localStorage.setItem('invoice_item_dump', JSON.stringify(probe));
    }catch(err){}
    if(!mailid)return;
    // 勾选时立即尝试提取 Message-ID（异步）
    if(!midMap()[mailid]){
      // 优先从 DOM 属性直接抓（xmmx 开头），失败再走 readmail 接口
      var _dm='';
      try{
        var _a=['data-messageid','data-mid','data-msgid','data-message-id'];
        for(var _i=0;_i<_a.length;_i++){var _v=item.getAttribute(_a[_i]);if(_v&&/xmmx/.test(_v)){_dm=_v;break;}}
        if(!_dm){var _s=item.querySelector('[data-messageid],[data-mid],[data-msgid],[data-message-id]'); if(_s){_dm=_s.getAttribute('data-messageid')||_s.getAttribute('data-mid')||'';}}
      }catch(e){}
      if(_dm){saveMid(mailid,_dm);}
      else{grabMsgID(mailid);}
    }
    var all=readAll();
    if(all[mailid]){
      delete all[mailid];
    }else{
      all[mailid]={mailid:mailid,sender:senderOf(item),subject:subjOf(item),
                   time:timeOf(item),fulltext:(item.innerText||'').slice(0,400)};
    }
    saveAll(all);
  }, true);
})();
'''

    def _install_tracker(self):
        """把勾选监听脚本注入 profile（所有页面加载都会执行）。"""
        for old in self.profile.scripts().find('invoice_tracker'):
            self.profile.scripts().remove(old)
        script = QWebEngineScript()
        script.setName('invoice_tracker')
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setSourceCode(self.TRACKER_SCRIPT)
        self.profile.scripts().insert(script)

    def get_selected_mails(self, keep_selection=True):
        """返回勾选的邮件项列表。

        数据来源二合一：
          1) 注入到页面的监听脚本维护 localStorage['invoice_selected']（用户在
             DOM 中勾选/取消时实时记录，覆盖滚出视图的邮件）；
          2) 实时扫描当前 DOM 中处于勾选状态的邮件（兜底：脚本注入失败、
             勾选发生在脚本运行前的场景）。
        两者按 mailid 合并去重。
        keep_selection=True（默认）：读取后【保留网页勾选】，方便用户反复下载/
        核对；False：读取后取消勾选（旧行为，只下载本次勾选）。
        返回列表元素结构：{mailid, sender, subject, time, fulltext, text}
        """
        js = r"""
(function(){
  var KEY='invoice_selected';
  var MIDMAP='invoice_msgid_map';
  var KEEP=%s;
  function readAll(){try{return JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){return {};}}
  function readMid(){try{return JSON.parse(localStorage.getItem(MIDMAP)||'{}');}catch(e){return {};}}
  function senderOf(item){
    var s=item.querySelector('.mail-sender,.mail-name,[class*=sender]');
    return s?(s.innerText||'').trim().slice(0,80):'';
  }
  function subjOf(item){
    var s=item.querySelector('[class*=subject],[class*=title],[class*=topic],[class*=summary]');
    return s?(s.innerText||'').trim().slice(0,120):'';
  }
  function timeOf(item){
    var s=item.querySelector('[class*=time],[class*=date]');
    return s?(s.innerText||'').trim().slice(0,20):'';
  }
  function trulyChecked(el){
    // 严格判定：只有出现明确的「已勾选」图标/标记才算勾选，
    // 避免 QQ 列表项的 active/current/selected/hover 类被误判。
    if(el.querySelector('.ui-checkbox-icon-checked'))return true;
    if(el.querySelector('[class*="checkbox"][class*="checked"]'))return true;
    if(el.querySelector('[aria-checked="true"]'))return true;
    if(/mail-item-checked/.test(el.className||''))return true;
    return false;
  }
  function midOfItem(el){
    // 从邮件项 DOM 直接找 QQ messageid：优先 data-* 属性，其次常见类名/文本
    var attrs=['data-messageid','data-mid','data-msgid','data-message-id','data-id'];
    for(var a=0;a<attrs.length;a++){
      var v=el.getAttribute(attrs[a]);
      if(v && /xmmx|message/i.test(v)) return v;
    }
    // 嵌套元素上的 data-* 
    var subs=el.querySelectorAll('[data-messageid],[data-mid],[data-msgid],[data-message-id]');
    for(var s=0;s<subs.length;s++){
      var v2=subs[s].getAttribute('data-messageid')||subs[s].getAttribute('data-mid')
              ||subs[s].getAttribute('data-msgid')||subs[s].getAttribute('data-message-id');
      if(v2 && /xmmx/.test(v2)) return v2;
    }
    return '';
  }
  var merged={};
  var items=document.querySelectorAll('div[class*=list-item]');
  var checkedEls=[];
  var mids=readMid();
  for(var i=0;i<items.length;i++){
    var el=items[i];
    if(!trulyChecked(el))continue;
    var mailid=el.getAttribute('data-mailid')||'';
    if(!mailid)continue;
    var rec={mailid:mailid,sender:senderOf(el),subject:subjOf(el),
             time:timeOf(el),fulltext:(el.innerText||'').slice(0,400)};
    if(mids[mailid])rec.message_id=mids[mailid];
    else{
      var dm=midOfItem(el);
      if(dm)rec.message_id=dm;
    }
    merged[mailid]=rec;
    checkedEls.push(el);
  }
  var stored=readAll();
  // 只采用 localStorage 中「当前 DOM 仍处于勾选状态」的记录，避免取消勾选后残留旧记录。
  // 滚出视图的邮件（DOM 中无此项）无法核对，若之前勾选过则保留（用户主动勾选未取消）。
  for(var k in stored){
    var d=stored[k];
    if(!d||!d.mailid)continue;
    var stillChecked=false;
    for(var ii=0;ii<checkedEls.length;ii++){
      if((checkedEls[ii].getAttribute('data-mailid')||'')===d.mailid){stillChecked=true;break;}
    }
    if(stillChecked){
      if(!d.message_id&&mids[d.mailid])d.message_id=mids[d.mailid];
      merged[k]=d;
    }
    // DOM 中已取消勾选 → 丢弃（不再下载）
  }
  var out=[];
  for(var k2 in merged){out.push(merged[k2]);}
  // 读取后清空 localStorage 勾选记录：DOM 是实时真相源，残留记录会导致「取消勾选后仍被下载」。
  try{localStorage.removeItem(KEY);}catch(e){}
  // 默认保留网页勾选；仅当 KEEP=false 时取消勾选
  if(!KEEP){
    var cbs=[];
    for(var j=0;j<checkedEls.length;j++){
      var cb=checkedEls[j].querySelector('.mail-checkbox,.xmail-ui-checkbox');
      if(cb)cbs.push(cb);
    }
    for(var c=0;c<cbs.length;c++){try{cbs[c].click();}catch(e){}}
  }
  return JSON.stringify(out);
})()
""" % ("true" if keep_selection else "false")
        try:
            value = self.run_js_obj(js, timeout=15000)
        except Exception:
            return []
        if not isinstance(value, list):
            return []
        mails = []
        for d in value:
            if not isinstance(d, dict) or not d.get("mailid"):
                continue
            fulltext = d.get("fulltext", "") or ""
            subject = d.get("subject", "") or ""
            sender = d.get("sender", "") or ""
            mails.append({
                "mailid": d["mailid"],
                "sender": sender,
                "subject": subject,
                "time": d.get("time", ""),
                "fulltext": fulltext,
                # 网页侧如能拿到 Message-ID 则透传（QQ DOM 通常没有，留空走哈希/关键词匹配）
                "message_id": (d.get("message_id", "") or "").strip(),
                # text 保持与旧 list_mails 一致的「全文」结构，
                # IMAP 侧用它做哈希/关键词/发件人特征提取
                "text": fulltext or (subject + " " + sender),
            })
        return mails

    def _read_tracker(self):
        js = "(function(){try{return localStorage.getItem('invoice_selected')||'';}catch(e){return '';}})()"
        raw = self.run_js(js, timeout=8000)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    # ---------- 页面原子操作 ----------
    def _click_el(self, el_js):
        """对指定 JS 表达式选中的元素模拟完整鼠标事件序列（mousedown→mouseup→click）。
        QQ 邮箱 SPA 的点击处理可能绑定在 mousedown/mouseup 上，仅 .click() 不触发。"""
        js = (
            "(function(){"
            "var el=" + el_js + ";"
            "if(!el)return false;"
            "var r=el.getBoundingClientRect();"
            "var opts={bubbles:true,cancelable:true,view:window,"
            "clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};"
            "el.dispatchEvent(new MouseEvent('mousedown',opts));"
            "el.dispatchEvent(new MouseEvent('mouseup',opts));"
            "el.dispatchEvent(new MouseEvent('click',opts));"
            "return true;})()"
        )
        return bool(self.run_js(js, timeout=15000))

    def click_mail_by_id(self, mailid):
        """按 QQ 内部唯一 mailid 打开邮件详情（列表排序变化也不受影响）。"""
        el_js = (
            "(function(){"
            "var els=document.querySelectorAll('div[class*=list-item]');"
            "for(var i=0;i<els.length;i++){"
            "  if((els[i].getAttribute('data-mailid')||'')==='%s'){"
            "    var t=els[i].querySelector('[class*=title],[class*=subject],[class*=summary],[class*=content],[class*=from]');"
            "    return t||els[i];"
            "  }"
            "}"
            "return null;})()" % mailid
        )
        return self._click_el(el_js)

    def click_mail(self, index):
        el_js = (
            "(function(){"
            "var els=document.querySelectorAll('div[class*=list-item]');"
            "if(els.length<=%d)return null;"
            "var t=els[%d].querySelector('[class*=title],[class*=subject],[class*=summary],[class*=content],[class*=from]');"
            "return t||els[%d];})()" % (index, index, index)
        )
        return self._click_el(el_js)

    def mail_detail_ready(self):
        js = (
            "(function(){var el=document.querySelector('div.mail-detail-attaches');"
            "return !!el;})()"
        )
        return bool(self.run_js(js, timeout=8000))

    def probe_message_id(self):
        """诊断：扫描当前页面（邮件详情）寻找 Message-ID，并把可疑片段写到磁盘。

        返回 dict：{found: bool, candidates: [str]}。也把详情页 HTML 存到
        `%TEMP%\\invoice_msgid_dump.html` 供人工分析。
        """
        js = r"""
(function(){
  var dump = document.body ? document.body.innerHTML : '';
  var out = {found:false, candidates:[]};
  var re = /Message[- ]?ID[^<]{0,200}/ig;
  var m, c = [];
  while((m = re.exec(dump))){ c.push(m[0].slice(0,120)); }
  // 常见暴露点：页面上内联脚本里的 messageId / msgid / mid
  var re2 = /[\"']?(?:messageId|msgid|mid)[\"']?\s*[:=]\s*[\"']([^\"']{8,80})[\"']/ig;
  var m2, c2 = [];
  while((m2 = re2.exec(dump))){ c2.push(m2[1]); }
  c2.forEach(function(x){ if(c.indexOf(x)<0) c.push(x); });
  out.candidates = c.slice(0,20);
  out.found = c.length>0;
  return {dump: dump.slice(0, 2000000), out: out};
})()
"""
        try:
            r = self.run_js_obj(js, timeout=15000)
            if not r:
                return {"found": False, "candidates": []}
            dump = r.get("dump", "")
            if dump:
                p = os.path.join(os.environ.get("TEMP", "/tmp"), "invoice_msgid_dump.html")
                try:
                    with open(p, "w", encoding="utf-8", errors="replace") as f:
                        f.write(dump)
                except Exception:
                    pass
            return r.get("out", {"found": False, "candidates": []})
        except Exception:
            return {"found": False, "candidates": []}

    def get_item_dump(self):
        """诊断：读取最近一次勾选时保存的邮件项 HTML/属性/Vue data 转储。"""
        js = ("(function(){try{return localStorage.getItem('invoice_item_dump')||'';}"
              "catch(e){return '';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def get_captured_msgids(self):
        """诊断：读取网络 hook 捕获到的 Message-ID 集合（dict）。"""
        js = ("(function(){try{return localStorage.getItem('invoice_msgids')||'{}';}"
              "catch(e){return '{}';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def get_msgid_tampermonkey_dbg(self):
        """诊断：读取油猴脚本的调试日志（localStorage['invoice_msgid_dbg']）。"""
        js = ("(function(){try{return localStorage.getItem('invoice_msgid_dbg')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_invoice_msgid_map(self):
        """诊断：读取注入脚本提取的 mailid→Message-ID 映射（localStorage['invoice_msgid_map']）。"""
        js = ("(function(){try{return localStorage.getItem('invoice_msgid_map')||'{}';}"
              "catch(e){return '{}';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def get_readmail_snippet(self):
        """诊断：读取 readmail 接口响应首 500 字节。"""
        js = ("(function(){try{return localStorage.getItem('invoice_readmail_snip')||'';}"
              "catch(e){return '';}})()")
        try:
            return self.run_js(js, timeout=8000) or ""
        except Exception:
            return ""

    def get_msgid_map_log(self):
        """诊断：读取 maillist→msgid 映射建立日志。"""
        js = ("(function(){try{return localStorage.getItem('invoice_msgid_map_log')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_net_dbg(self):
        """诊断：读取网络钩子捕获的 QQ 邮件接口 URL+响应片段列表。"""
        js = ("(function(){try{return localStorage.getItem('invoice_net_dbg')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_ws_dbg(self):
        """诊断：读取 WebSocket 钩子捕获的消息。"""
        js = ("(function(){try{return localStorage.getItem('invoice_ws_dbg')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_maillist_raw(self):
        """诊断：读取 list/maillist 接口原始响应（加密前 3000 字节）。"""
        js = ("(function(){try{return localStorage.getItem('invoice_maillist_raw')||'';}"
              "catch(e){return '';}})()")
        try:
            return self.run_js(js, timeout=8000) or ""
        except Exception:
            return ""

    def get_json_hook(self):
        """诊断：读取 JSON.parse hook 捕获的明文数据。"""
        js = ("(function(){try{return localStorage.getItem('invoice_json_hook')||'';}"
              "catch(e){return '';}})()")
        try:
            return self.run_js(js, timeout=8000) or ""
        except Exception:
            return ""

    def get_json_hook_list(self):
        """诊断：读取 JSON.parse hook 捕获的数据摘要列表。"""
        js = ("(function(){try{return localStorage.getItem('invoice_json_hook_list')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_maillist_json(self):
        """诊断：读取 maillist 完整明文 JSON。"""
        js = ("(function(){try{return localStorage.getItem('invoice_maillist_json')||'';}"
              "catch(e){return '';}})()")
        try:
            return self.run_js(js, timeout=8000) or ""
        except Exception:
            return ""

    def get_mailobj_json(self):
        """诊断：读取 mailid 为键的邮件详情对象。"""
        js = ("(function(){try{return localStorage.getItem('invoice_mailobj_json')||'';}"
              "catch(e){return '';}})()")
        try:
            return self.run_js(js, timeout=8000) or ""
        except Exception:
            return ""

    def get_mailobjs(self):
        """诊断：读取最近 30 个 mailid 键邮件详情对象。"""
        js = ("(function(){try{return localStorage.getItem('invoice_mailobjs')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_maillists(self):
        """诊断：读取最近 5 个 maillist 完整明文 JSON。"""
        js = ("(function(){try{return localStorage.getItem('invoice_maillists')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_req_urls(self):
        """诊断：底层 URL 拦截器捕获的所有 mail.qq.com 接口请求。"""
        return list(self._req_urls)

    def get_captured_net(self):
        """诊断：读取网络 hook 捕获的请求特征列表（url/len/flags）。"""
        js = ("(function(){try{return localStorage.getItem('invoice_net')||'[]';}"
              "catch(e){return '[]';}})()")
        try:
            raw = self.run_js(js, timeout=8000)
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def get_mail_date(self):
        js = (
            "(function(){"
            "var b=document.body;if(!b)return '';"
            "var t=b.innerText;"
            "var m=t.match(/(20\\d{2})年(\\d{1,2})月(\\d{1,2})日/);"
            "if(!m)return '';"
            "function p(n){return (n<10?'0':'')+n;}"
            "return m[1]+'-'+p(parseInt(m[2]))+'-'+p(parseInt(m[3]));})()"
        )
        return self.run_js(js, timeout=8000) or ""

    def get_attachments(self):
        js = (
            "(function(){"
            "var els=document.querySelectorAll('.mail-detail-attach-card');"
            "var out=[];"
            "for(var i=0;i<els.length;i++){"
            "  var c=els[i];"
            "  var n=c.querySelector('.attach-name');"
            "  var s=c.querySelector('.attach-suffix');"
            "  var z=c.querySelector('.attach-size');"
            "  out.push({"
            "    name:n?n.innerText:'',"
            "    suffix:s?s.innerText:'',"
            "    size:z?z.innerText:''"
            "  });"
            "}"
            "return out;})()"
        )
        return self.run_js_obj(js, timeout=15000) or []

    def click_download(self, card_index):
        """点击第 card_index 个附件卡片的『下载』按钮。返回是否成功触发。"""
        js = (
            "(function(){"
            "var els=document.querySelectorAll('.mail-detail-attach-card');"
            "if(els.length<=%d)return false;"
            "var c=els[%d];"
            "var btns=c.querySelectorAll('.xmail-ui-btn');"
            "for(var i=0;i<btns.length;i++){"
            "  if((btns[i].innerText||'').trim()==='下载'){btns[i].click();return true;}"
            "}"
            "return false;})()" % (card_index, card_index)
        )
        return bool(self.run_js(js, timeout=15000))

    def back_to_list(self):
        js = (
            "(function(){"
            "var els=document.querySelectorAll('div');"
            "for(var i=0;i<els.length;i++){"
            "  var t=els[i].innerText||'';"
            "  if((t.trim()==='返回')&&els[i].children.length===0){els[i].click();return true;}"
            "}"
            "return false;})()"
        )
        return bool(self.run_js(js, timeout=8000))

    # ---------- 批量原生下载 ----------
    def add_pending_dest(self, dest):
        """把下一个要下载的附件目标路径加入队列（顺序与点击顺序一致）。"""
        self._pending_dests.append(dest)

    def _on_download_requested(self, item: QWebEngineDownloadRequest):
        """浏览器触发附件下载。若队列中有目标路径，则让 Qt 原生下载到该路径；
        否则取消（避免污染默认下载目录）。"""
        url = item.url().toString()
        if url:
            self._sniffed_urls.append(url)
            self.log_signal.emit(f"[下载] 捕获下载请求: {url[:90]}")
        if self._pending_dests:
            dest = self._pending_dests.pop(0)
            item.setDownloadDirectory(os.path.dirname(dest))
            item.setDownloadFileName(os.path.basename(dest))
            self.log_signal.emit(f"[下载] 原生保存到: {os.path.basename(dest)}")
            item.isFinishedChanged.connect(lambda: self._finished_dests.append(dest))
            item.stateChanged.connect(self._on_download_state)
            self._download_items[time.time()] = item
        else:
            item.cancel()
            self.log_signal.emit("[下载] 无目标队列，已取消")

    def _on_download_state(self, state):
        self.log_signal.emit(f"[下载] 状态变化: {state}")

    def wait_downloads(self, count, timeout=120):
        """等待 count 个批量下载完成（依据文件实际落盘 + isFinished）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            done = len(self._finished_dests)
            # 兜底：部分下载可能完成了但信号未刷新，检查磁盘文件
            for item in self._download_items.values():
                if item.isFinished():
                    f = os.path.join(item.downloadDirectory(), item.downloadFileName())
                    if os.path.exists(f) and f not in self._finished_dests:
                        self._finished_dests.append(f)
                        done += 1
            if done >= count:
                return True
            self.qt_sleep(0.3)
        return False

    def probe_detail_dom(self):
        """诊断：探查当前邮件详情页的附件/下载元素结构（新旧版 DOM 兼容）。"""
        js = r"""
(function(){
  var out = {url: (location.href||'').slice(0,160), attach_cards: 0, download_btns: 0,
             samples: [], body_links: [], frames: [], body_head: '', frame_tree: []};
  // 递归收集所有 frame 的 URL（含 iframe）
  function collectFrames(w, depth){
    var res = [];
    if(!w || depth>4) return res;
    try{
      if(w.location && w.location.href){
        res.push({d:depth, url:(w.location.href||'').slice(0,120),
                  hasBody: !!(w.document && w.document.body && w.document.body.children.length)});
      }
      for(var i=0; i<w.frames.length; i++){
        try{ res = res.concat(collectFrames(w.frames[i], depth+1)); }catch(e){}
      }
    }catch(e){
      res.push({d:depth, err:String(e).slice(0,40)});
    }
    return res;
  }
  try{ out.frame_tree = collectFrames(window, 0); }catch(e){ out.frame_tree=[{err:String(e).slice(0,60)}]; }
  // 附件卡片：多种可能的类名/结构
  var cards = document.querySelectorAll(
    '.mail-detail-attach-card,[class*="attach-card"],[class*="attachment"],[class*="file-card"],[class*="enclosure"]');
  out.attach_cards = cards.length;
  for(var i=0;i<Math.min(cards.length,5);i++){
    var c = cards[i];
    out.samples.push({
      cls: (c.className||'').slice(0,80),
      text: (c.innerText||'').slice(0,80),
      html: (c.outerHTML||'').slice(0,300)
    });
  }
  // iframe 探查（QQ 新版详情可能在 iframe 里）
  var ifs = document.querySelectorAll('iframe');
  out.frames = [];
  for(var fi=0; fi<ifs.length; fi++){
    out.frames.push({src:(ifs[fi].src||'').slice(0,120), id: ifs[fi].id||'', cls:(ifs[fi].className||'').slice(0,40)});
  }
  // 整个文档里的下载/附件关键词容器（不限类名）
  var kwEls = [];
  var all = document.querySelectorAll('div,span,a,button');
  for(var ai=0; ai<all.length; ai++){
    var t = (all[ai].innerText||'').trim();
    if((t==='下载' || t==='保存到云盘') && all[ai].children.length===0){
      kwEls.push({tag:all[ai].tagName, cls:(all[ai].className||'').slice(0,60), text:t});
    }
  }
  out.download_words = kwEls;
  // 下载按钮/链接
  var btns = document.querySelectorAll(
    '.xmail-ui-btn,[class*="download"],[class*="btn-download"],[class*="operate-btn"]');
  out.download_btns = btns.length;
  // 附件卡片内的按钮（精确探查下载按钮类名）
  var cardBtns = [];
  for(var ci=0; ci<Math.min(cards.length,4); ci++){
    var cb = cards[ci].querySelectorAll('*');
    var info = [];
    for(var bi=0; bi<cb.length; bi++){
      var el = cb[bi];
      var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
      var cursor = style ? style.cursor : '';
      var clickable = cursor==='pointer' || el.onclick || el.getAttribute('role')==='button'
                      || el.getAttribute('data-click') || el.getAttribute('data-action')
                      || el.getAttribute('data-role');
      if(!clickable) continue;
      info.push({tag: el.tagName, cls: (el.className||'').slice(0,60),
                 text: (el.innerText||'').trim().slice(0,20),
                 cursor: cursor, role: el.getAttribute('role')||'',
                 data: (el.getAttribute('data-action')||el.getAttribute('data-click')||el.getAttribute('data-role')||'').slice(0,40),
                 title: (el.getAttribute('title')||'').slice(0,30),
                 aria: (el.getAttribute('aria-label')||el.getAttribute('aria-label')||'').slice(0,30)});
    }
    cardBtns.push(info);
  }
  out.card_buttons = cardBtns;
  // 卡片完整 HTML（含按钮图标结构，用于识别下载按钮）
  var cardHtmls = [];
  for(var hi=0; hi<Math.min(cards.length,3); hi++){
    cardHtmls.push((cards[hi].outerHTML||'').slice(0,1200));
  }
  out.card_htmls = cardHtmls;
  // 正文直链（51fapiao/alipay 等 PDF 链接）
  var aTags = document.querySelectorAll('a[href]');
  for(var j=0;j<aTags.length;j++){
    var h = aTags[j].href||'';
    if(/\.pdf|dlj\.|alipayobjects|51fapiao|fapiao/i.test(h)){
      out.body_links.push({text:(aTags[j].innerText||'').slice(0,40), href:h.slice(0,160)});
    }
  }
  // 正文文本前 120 字（判断当前打开的邮件内容）
  var bt = (document.body && document.body.innerText || '').trim().replace(/\s+/g,' ').slice(0,150);
  out.body_head = bt;
  return out;
})()
"""
        try:
            r = self.run_js_obj(js, timeout=15000)
            return r or {}
        except Exception:
            return {}

    def consume_download_url(self, timeout=8):
        """返回嗅探到的下载 URL 列表（含先前捕获），并清空当前等待队列。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._sniffed_urls:
                urls = list(self._sniffed_urls)
                self._sniffed_urls.clear()
                return urls
            self.qt_sleep(0.2)
        return []

    def fetch_url(self, url, dest_path, referer=None):
        """用 requests 拉取下载 URL 内容保存到 dest_path。（可在 worker 线程调用，
        仅使用线程安全的只读状态，不触碰 Qt 事件循环。）"""
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
            "Referer": referer or "https://wx.mail.qq.com/",
        }
        cookie_jar = self.cookies.get_cookie_jar(url)
        with requests.get(url, headers=headers, cookies=cookie_jar,
                          stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return dest_path