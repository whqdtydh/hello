"""基于 QtWebEngine 的邮箱页面控制。

自动化由 runJavaScript 驱动；勾选监听脚本注入页面，记录用户勾选的邮件
（含 Message-ID 提取），供 API 下载引擎（api_downloader）使用。
"""

import json
import os
import re
import time

import requests
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

from app import config
from app.engine import msgid_service
from app.engine.cookie_store import CookieStore


class WebClient(QObject):
    """封装 QWebEngineView，提供邮箱自动化所需的原子操作 + 会话管理。"""

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

        # cookie 收集器（请求时供 requests 复用会话）
        self.cookies = CookieStore(self.profile, config.COOKIE_FILE)

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

    # ---------- 勾选监听 ----------
    TRACKER_SCRIPT = r'''
(function(){
  if(window.__invoiceTrackerInstalled)return;
  window.__invoiceTrackerInstalled=true;
  var KEY='invoice_selected';
  var MIDMAP='invoice_msgid_map';
  var SESSION_KEY='invoice_session_start';
  // ===== 会话隔离：页面加载即清空旧的选择记录，杜绝跨会话残留 =====
  try{
    localStorage.removeItem(KEY);
    localStorage.setItem(SESSION_KEY, String(Date.now()));
  }catch(e){}
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
      return fetch(urls[i], {credentials:'include', redirect:'follow'})
        .then(function(r){ return r.text(); })
        .then(function(t){
          var mid=sniffMessageID(t);
          if(mid) return {mid:mid};
          return tryFetch(i+1);
        })
        .catch(function(){ return tryFetch(i+1); });
    }
    return tryFetch(0);
  }
  function grabMsgID(mailid){
    if(midMap()[mailid]) return Promise.resolve(midMap()[mailid]);
    return fetchRaw(mailid).then(function(res){
      if(res&&res.mid){saveMid(mailid,res.mid);return res.mid;}
      return '';
    });
  }
  // ===== 勾选状态判定 =====
  function trulyChecked(el){
    if(el.querySelector('.ui-checkbox-icon-checked'))return true;
    if(el.querySelector('.checkbox-checked, .checkbox-selected, .checked-icon'))return true;
    if(el.querySelector('[class*="checkbox"][class*="checked"], [class*="checkbox"][class*="selected"]'))return true;
    if(el.querySelector('[aria-checked="true"], [aria-selected="true"]'))return true;
    if(/mail-item-checked|item-checked|selected-item|checked-row/.test(el.className||''))return true;
    var cb=el.querySelector('input[type="checkbox"]');
    if(cb&&cb.checked)return true;
    return false;
  }
  function syncItem(item){
    var mailid=item.getAttribute('data-mailid')||'';
    if(!mailid)return;
    var all=readAll();
    if(trulyChecked(item)){
      if(!all[mailid]){
        all[mailid]={mailid:mailid,sender:senderOf(item),subject:subjOf(item),
                     time:timeOf(item),fulltext:(item.innerText||'').slice(0,400),
                     ts:Date.now()};
        saveAll(all);
        // 勾选时上报本机服务 → 后台预读详情（边勾边读，下载时零等待）
        try{
          fetch('http://127.0.0.1:__PRELOAD_PORT__/check',
                {method:'POST', headers:{'Content-Type':'application/json'},
                 body: JSON.stringify({mailid: mailid})});
        }catch(e){}
        // 勾选时立即尝试提取 Message-ID（异步）
        try{
          var _dm='';
          var _a=['data-messageid','data-mid','data-msgid','data-message-id'];
          for(var _i=0;_i<_a.length;_i++){var _v=item.getAttribute(_a[_i]);if(_v&&/xmmx/.test(_v)){_dm=_v;break;}}
          if(!_dm){var _s=item.querySelector('[data-messageid],[data-mid],[data-msgid],[data-message-id]'); if(_s){_dm=_s.getAttribute('data-messageid')||_s.getAttribute('data-mid')||'';}}
          if(_dm){saveMid(mailid,_dm);}
          else if(!midMap()[mailid]){grabMsgID(mailid);}
        }catch(e){}
      }
    }else{
      if(all[mailid]){
        delete all[mailid];
        saveAll(all);
      }
    }
  }
  // ===== click 延迟校验：点击后 100ms 检查勾选状态是否真的变化 =====
  //  - 点 checkbox 勾选 → 状态变化 → syncItem 记录（不漏识别）
  //  - 点 checkbox 取消 → 状态变化 → syncItem 删除（不残留）
  //  - 点邮件行打开查看 → 勾选状态不变 → 不记录（不多识别）
  document.addEventListener('click', function(e){
    var t=e.target;
    var item=t.closest?t.closest('div[class*=list-item]'):null;
    if(!item)return;
    var mailid=item.getAttribute('data-mailid')||'';
    if(!mailid)return;
    setTimeout(function(){ syncItem(item); }, 100);
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
        # 端口运行时替换（避免硬编码漂移）
        src = self.TRACKER_SCRIPT.replace(
            "__PRELOAD_PORT__", str(msgid_service.PORT))
        script.setSourceCode(src)
        self.profile.scripts().insert(script)

    # ---------- 接口观察（失效自动学习） ----------

    API_OBSERVER_SCRIPT = r'''
(function(){
  if(window.__invoiceApiObserverInstalled)return;
  window.__invoiceApiObserverInstalled=true;
  window.__invoiceApiObserving=false;
  var KEY='invoice_api_obs';
  // 只记录 QQ 邮箱网页 CGI 请求，忽略静态资源，减少 localStorage 膨胀
  function isCgi(url){
    try{
      if(!url||url.indexOf('mail.qq.com')<0)return false;
      if(/\.(css|js|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|eot|map|json)(\?|$)/i.test(url))return false;
      if(/\.(css|js|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|eot|map)\?/.test(url))return false;
      // 静态 CDN 域名（qqmail/tencent 静态资源）不记录
      if(/\/res\/|\/mmres\/|\/oa\/|\/home\/index\?/.test(url))return false;
      return true;
    }catch(e){return false;}
  }
  function record(url, method){
    if(!window.__invoiceApiObserving)return;
    if(!isCgi(url))return;
    try{
      var arr=[];
      try{arr=JSON.parse(localStorage.getItem(KEY)||'[]');}catch(e){arr=[];}
      arr.push({u:String(url).slice(0,400),m:String(method||'GET'),t:Date.now()});
      if(arr.length>200)arr=arr.slice(-200);
      localStorage.setItem(KEY,JSON.stringify(arr));
    }catch(e){}
  }
  // hook XMLHttpRequest
  var _open=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(m,u){
    record(u,arguments[0]);
    return _open.apply(this,arguments);
  };
  // hook fetch
  var _fetch=window.fetch;
  window.fetch=function(input,init){
    var u=(typeof input==='string')?input:(input&&input.url)||'';
    record(u,(init&&init.method)||(typeof input==='string'?'GET':''));
    return _fetch.apply(this,arguments);
  };
})();
'''

    def _install_api_observer(self):
        """注入接口观察脚本（常驻，但默认不记录；需 start_api_observe 开启）。"""
        for old in self.profile.scripts().find('invoice_api_observer'):
            self.profile.scripts().remove(old)
        script = QWebEngineScript()
        script.setName('invoice_api_observer')
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setSourceCode(self.API_OBSERVER_SCRIPT)
        self.profile.scripts().insert(script)

    def start_api_observe(self):
        """开启接口观察：清空历史记录，此后页面上的 CGI 请求会被记录。"""
        self._set_api_observing(True)

    def stop_api_observe(self):
        self._set_api_observing(False)

    def _set_api_observing(self, on):
        try:
            self.run_js(
                f"(function(){{"
                f"window.__invoiceApiObserving={'true' if on else 'false'};"
                f"if({'true' if on else 'false'}){{try{{localStorage.removeItem('invoice_api_obs');}}catch(e){{}}}}"
                f"return true;}})()",
                timeout=5000)
        except Exception:
            pass

    def get_observed_requests(self):
        """读取观察到的请求列表：[{u: url, m: method, t: timestamp}]。"""
        try:
            val = self.run_js_obj(
                "(function(){"
                "try{return localStorage.getItem('invoice_api_obs')||'[]';}"
                "catch(e){return '[]';}})()",
                timeout=5000)
        except Exception:
            return []
        if not isinstance(val, str):
            return []
        try:
            arr = json.loads(val)
            return arr if isinstance(arr, list) else []
        except Exception:
            return []

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
    // 增强匹配：覆盖 QQ 邮箱可能更新的多种勾选状态类名变体
    if(el.querySelector('.ui-checkbox-icon-checked'))return true;
    if(el.querySelector('.checkbox-checked, .checkbox-selected, .checked-icon'))return true;
    if(el.querySelector('[class*="checkbox"][class*="checked"], [class*="checkbox"][class*="selected"]'))return true;
    if(el.querySelector('[aria-checked="true"], [aria-selected="true"]'))return true;
    if(/mail-item-checked|item-checked|selected-item|checked-row/.test(el.className||''))return true;
    // 检查嵌套的 input[type=checkbox] 元素是否被选中（某些交互方式用 checkbox 元素）
    var cb=el.querySelector('input[type="checkbox"]');
    if(cb&&cb.checked)return true;
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
  // 会话起点：只合并本次页面加载后产生的记录（跨会话残留一律丢弃）
  var sessStart=0;
  try{sessStart=parseInt(localStorage.getItem('invoice_session_start')||'0',10)||0;}catch(e){}
  // 合并策略：
  //  - localStorage 记录 + DOM 中存在该项 → 用 DOM 当前勾选状态（勾选保留，取消丢弃）
  //  - localStorage 记录 + DOM 中无该项（滚出视图/翻页）→ 保留（用户主动勾选且无法核对取消）
  //    （旧逻辑误把「滚出视图的已勾选邮件」当「已取消勾选」丢弃，导致漏下载）
  var domMailids={};
  for(var di=0;di<checkedEls.length;di++){
    domMailids[checkedEls[di].getAttribute('data-mailid')||'']=true;
  }
  // DOM 中所有 list-item 的 mailid（无论是否勾选，用于判断该项是否在视口内）
  var domAll={};
  for(var ai=0;ai<items.length;ai++){
    var _mid=items[ai].getAttribute('data-mailid')||'';
    if(_mid)domAll[_mid]=true;
  }
  for(var k in stored){
    var d=stored[k];
    if(!d||!d.mailid)continue;
    // 跨会话残留防护：无时间戳或早于会话起点的记录视为残留，直接丢弃
    if(!d.ts||!sessStart||d.ts<sessStart)continue;
    if(domAll[d.mailid]){
      // 在视口内：必须仍处于勾选状态才保留
      if(!domMailids[d.mailid])continue;  // 已取消勾选 → 丢弃
    }
    // 不在视口内（滚出视图）：保留（用户勾选过，未确认取消）
    if(!d.message_id&&mids[d.mailid])d.message_id=mids[d.mailid];
    merged[k]=d;
  }
  var out=[];
  for(var k2 in merged){out.push(merged[k2]);}
  // 读取后清空 localStorage 勾选记录：DOM 是实时真相源，残留记录会导致「取消勾选后仍被下载」。
  try{
    localStorage.removeItem(KEY);
    localStorage.setItem('invoice_session_start', String(Date.now()));
  }catch(e){}
  // 默认保留网页勾选；仅当 KEEP=false 时取消勾选
  if(!KEEP){
    var cbs=[];
    for(var j=0;j<checkedEls.length;j++){
      var cb=checkedEls[j].querySelector('.mail-checkbox,.xmail-ui-checkbox');
      if(cb)cbs.push(cb);
    }
    for(var c=0;c<cbs.length;c++){try{cbs[c].click();}catch(e){}}
  }
  var diag={total_items:items.length, dom_checked:checkedEls.length,
             stored_count:Object.keys(stored).length, merged_count:merged.length,
             out_count:out.length, dom_all_count:Object.keys(domAll).length,
             ts:Date.now()};
  try{localStorage.setItem('invoice_diag_read',JSON.stringify(diag));}catch(e){}
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
                "text": fulltext or (subject + " "+sender),
            })
        # 差异诊断：读取本次扫描统计，计算勾选识别差异并输出到日志面板
        try:
            diag_raw = self.run_js(
                "(function(){try{return localStorage.getItem('invoice_diag_read')||'{}';}catch(e){return '{}';}})()",
                timeout=5000)
            if isinstance(diag_raw, str) and diag_raw.startswith("__JS_ERR__"):
                diag_raw = "{}"
            import json
            diag = json.loads(diag_raw) if isinstance(diag_raw, str) else {}
            total_items = diag.get("total_items", 0)
            dom_checked = diag.get("dom_checked", 0)
            out_count = diag.get("out_count", len(mails))
            stored_count = diag.get("stored_count", 0)
            # 计算差异：如果用户勾选数（从日志判断或用户输入）与识别数不一致，提示可能原因
            diff_note = ""
            if out_count < dom_checked:
                diff_note = "（部分勾选项被丢弃：可能已取消勾选或不在视口内）"
            elif stored_count > 0 and out_count < stored_count + dom_checked:
                diff_note = "（localStorage 记录与 DOM 扫描存在重叠或缺失）"
            msg = f"📊 勾选识别诊断 → 总项:{total_items} DOM勾选:{dom_checked} 识别结果:{out_count} localStorage记录:{stored_count}{diff_note}"
            try:
                self.log_signal.emit(msg)
            except Exception:
                pass
        except Exception:
            pass
        return mails