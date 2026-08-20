"""WebView2 版页面控制（CDP 驱动）— 替代 QtWebEngine 版 WebClient。

对外 API 与旧版完全一致（run_js / run_js_obj / run_js_threadsafe /
run_js_obj_threadsafe / qt_sleep / navigate / current_url / get_sid /
cookies / _install_tracker / 接口观察 / get_selected_mails），
内部改为走 CDP（Runtime.evaluate / Network.getCookies / Network 事件 /
Page.addScriptToEvaluateOnNewDocument）。
"""

import json
import re
import threading
import time

import websocket
from PySide6.QtCore import QObject, Signal

from app.engine import msgid_service
from app.engine.webview2_host import CDP_PORT

# 页面自动化 JS（与旧版一致，纯页面脚本不依赖 Qt）
TRACKER_SCRIPT = r'''
(function(){
  if(window.__invoiceTrackerInstalled)return;
  window.__invoiceTrackerInstalled=true;
  var KEY='invoice_selected';
  var MIDMAP='invoice_msgid_map';
  var SESSION_KEY='invoice_session_start';
  try{
    // 注意：不再清空勾选记录、不重置会话起点。
    // 页面重载/重复注入会触发本脚本，若清空 KEY/重置 SESSION，
    // 勾选过程中任何一次导航都会把已记录的勾选全部丢弃（历史 bug：1000 封只读到 14 封）。
    // 会话起点仅在缺失时设置；会话推进由读取端 SELECTED_MAILS_JS 在消费后负责前移。
    if(!localStorage.getItem(SESSION_KEY)) localStorage.setItem(SESSION_KEY, String(Date.now()));
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
  function fetchRaw(mailid){
    var sid=currentSid();
    var enc=encodeURIComponent(mailid);
    var urls=[
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
  function syncItem(item, skipMid){
    var mailid=item.getAttribute('data-mailid')||'';
    if(!mailid)return;
    var all=readAll();
    if(trulyChecked(item)){
      if(!all[mailid]){
        all[mailid]={mailid:mailid,sender:senderOf(item),subject:subjOf(item),
                     time:timeOf(item),fulltext:(item.innerText||'').slice(0,400),
                     ts:Date.now()};
        saveAll(all);
        try{
          fetch('http://127.0.0.1:__PRELOAD_PORT__/check',
                {method:'POST', headers:{'Content-Type':'application/json'},
                 body: JSON.stringify({mailid: mailid})});
        }catch(e){}
        try{
          var _dm='';
          var _a=['data-messageid','data-mid','data-msgid','data-message-id'];
          for(var _i=0;_i<_a.length;_i++){var _v=item.getAttribute(_a[_i]);if(_v&&/xmmx/.test(_v)){_dm=_v;break;}}
          if(!_dm){var _s=item.querySelector('[data-messageid],[data-mid],[data-msgid],[data-message-id]'); if(_s){_dm=_s.getAttribute('data-messageid')||_s.getAttribute('data-mid')||'';}}
          if(_dm){saveMid(mailid,_dm);}
          else if(!skipMid && !midMap()[mailid]){grabMsgID(mailid);}
        }catch(e){}
      }
    }else{
      if(all[mailid]){
        delete all[mailid];
        saveAll(all);
      }
    }
  }
  // 全量同步当前 DOM 可见行（定时轮询/整页全选用；skipMid 避免高频重复抓 message-id）
  function syncAllVisible(){
    var rows=document.querySelectorAll('div[class*=list-item]');
    for(var i=0;i<rows.length;i++){ syncItem(rows[i], true); }
  }
  document.addEventListener('click', function(e){
    var t=e.target;
    var item=t.closest?t.closest('div[class*=list-item]'):null;
    if(!item)return;
    var mailid=item.getAttribute('data-mailid')||'';
    if(!mailid)return;
    setTimeout(function(){ syncItem(item); }, 100);
  }, true);
  document.addEventListener('change', function(e){
    var t=e.target;
    var item=t.closest?t.closest('div[class*=list-item]'):null;
    if(!item){ syncAllVisible(); return; }   // 表头全选等批量控件：整页刷新补录
    var mailid=item.getAttribute('data-mailid')||'';
    if(!mailid)return;
    setTimeout(function(){ syncItem(item, true); }, 100);
  }, true);
  setInterval(syncAllVisible, 400);
})()
'''

SELECTED_MAILS_JS = r"""
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
    if(el.querySelector('.ui-checkbox-icon-checked'))return true;
    if(el.querySelector('.checkbox-checked, .checkbox-selected, .checked-icon'))return true;
    if(el.querySelector('[class*="checkbox"][class*="checked"], [class*="checkbox"][class*="selected"]'))return true;
    if(el.querySelector('[aria-checked="true"], [aria-selected="true"]'))return true;
    if(/mail-item-checked|item-checked|selected-item|checked-row/.test(el.className||''))return true;
    var cb=el.querySelector('input[type="checkbox"]');
    if(cb&&cb.checked)return true;
    return false;
  }
  function midOfItem(el){
    var attrs=['data-messageid','data-mid','data-msgid','data-message-id','data-id'];
    for(var a=0;a<attrs.length;a++){
      var v=el.getAttribute(attrs[a]);
      if(v && /xmmx|message/i.test(v)) return v;
    }
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
  var sessStart=0;
  try{sessStart=parseInt(localStorage.getItem('invoice_session_start')||'0',10)||0;}catch(e){}
  var domAll={};
  for(var ai=0;ai<items.length;ai++){
    var _mid=items[ai].getAttribute('data-mailid')||'';
    if(_mid)domAll[_mid]=true;
  }
  for(var k in stored){
    var d=stored[k];
    if(!d||!d.mailid)continue;
    if(!d.ts||!sessStart||d.ts<sessStart)continue;
    // 不再用 domAll 二次裁决：取消勾选由 tracker 的 else 分支实时清理，
    // 此处一律合并本地记录，避免虚拟滚动下"当前页未勾选"误杀大量记录
    if(!d.message_id&&mids[d.mailid])d.message_id=mids[d.mailid];
    merged[k]=d;
  }
  var out=[];
  for(var k2 in merged){out.push(merged[k2]);}
  try{
    localStorage.removeItem(KEY);
    localStorage.setItem('invoice_session_start', String(Date.now()));
  }catch(e){}
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
"""


class _CDP:
    """CDP 客户端：请求/响应 + Network 事件收集（websocket 线程安全，断线自动重连）。

    注意：Chromium CDP 同一 target 只允许一个调试器客户端。外部工具
    （如调试脚本）连入会把本连接踢掉，因此 call() 检测到连接断开时
    自动重连并恢复注入/观察状态。
    """

    def __init__(self, port=CDP_PORT):
        self._ws = None
        self._lock = threading.Lock()
        self._id = 0
        self._port = port
        self._net_events = []      # Network.requestWillBeSent 记录
        self._observing = False
        self._page_script_sources = []   # 已注册的 addScript 源码（重连后重放）
        self._connect(port)

    def _connect(self, port):
        import urllib.request
        ws_url = None
        # create_view 已预等端口就绪，这里只需短等待（避免 GUI 线程长时间阻塞）
        for _ in range(20):
            try:
                data = json.load(urllib.request.urlopen(
                    "http://127.0.0.1:%d/json" % port, timeout=2))
                for t in data:
                    if t.get("type") == "page":
                        ws_url = t["webSocketDebuggerUrl"]
                        break
            except Exception:
                pass
            if ws_url:
                break
            time.sleep(0.5)
        if not ws_url:
            raise RuntimeError("CDP 端口 %d 未就绪（WebView2 未初始化）" % port)
        self._ws = websocket.create_connection(ws_url, timeout=10,
                                               suppress_origin=True)
        # 恢复注入脚本（重连后旧注册丢失，须重放）
        for src in self._page_script_sources:
            self._ws.send(json.dumps({
                "id": 900000, "method": "Page.addScriptToEvaluateOnNewDocument",
                "params": {"source": src}}))
        if self._observing:
            self._ws.send(json.dumps({"id": 900001,
                                      "method": "Network.enable", "params": {}}))

    def _reconnect(self):
        """断线后重建连接（带锁保护，避免多线程并发重连）。"""
        try:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None
            self._connect(self._port)
        except Exception:
            pass

    def call(self, method, params=None):
        for attempt in range(2):
            try:
                with self._lock:
                    if self._ws is None:
                        self._connect(self._port)
                    self._id += 1
                    req_id = self._id
                    self._ws.send(json.dumps({"id": req_id, "method": method,
                                              "params": params or {}}))
                    # 响应等待带超时：导航/页面切换窗口期 CDP 响应可能丢失，
                    # 若无超时 recv() 会无限阻塞导致 Qt 主线程卡死（历史 bug）
                    deadline = time.time() + 5.0
                    self._ws.settimeout(0.5)
                    while True:
                        try:
                            msg = json.loads(self._ws.recv())
                        except websocket.WebSocketTimeoutException:
                            if time.time() > deadline:
                                raise TimeoutError("CDP 响应超时: %s" % method)
                            continue
                        if msg.get("id") == req_id:
                            return msg.get("result", {})
                        if msg.get("method") == "Network.requestWillBeSent" and self._observing:
                            p = msg["params"]
                            url = p.get("request", {}).get("url", "")
                            if url and "mail.qq.com" in url:
                                self._net_events.append({
                                    "u": url[:400], "m": p.get("request", {}).get("method", "GET"),
                                    "t": int(time.time() * 1000)})
                                if len(self._net_events) > 500:
                                    self._net_events = self._net_events[-500:]
            except (websocket.WebSocketException, OSError, KeyError, ValueError, TimeoutError):
                # 连接被外部调试器踢断 / 响应超时 / 网络异常 → 重连后重试一次
                self._reconnect()
                continue
        raise RuntimeError("CDP 调用失败（已重连仍失败）: %s" % method)

    def eval(self, expression, timeout=15):
        """执行 JS 并返回（string）结果；异常返回 '__JS_ERR__:<msg>'。"""
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        })
        if "exceptionDetails" in result and result["exceptionDetails"]:
            d = result["exceptionDetails"]
            return "__JS_ERR__:" + (d.get("exception", {}).get("description", "js error")[:200])
        value = result.get("result", {}).get("value")
        return value

    def get_cookies(self):
        return self.call("Network.getCookies").get("cookies", [])

    def clear_cookies(self):
        self.call("Network.clearBrowserCookies")

    def navigate(self, url):
        self.call("Page.navigate", {"url": url})


class _CookieBridge:
    """兼容旧 CookieStore 的最小接口（get_cookie_value / get_cookie_jar / clear_all）。"""

    def __init__(self, cdp):
        self._cdp = cdp

    def get_cookie_value(self, name, host=None):
        for c in self._cdp.get_cookies():
            if c.get("name") != name:
                continue
            if host and host not in c.get("domain", ""):
                continue
            return c.get("value", "")
        return ""

    def get_cookie_jar(self, url):
        """按域名过滤 CDP cookie，返回 requests CookieJar（语义同旧 CookieStore）。"""
        from urllib.parse import urlparse
        import requests
        host = (urlparse(url).netloc or "").lower()
        jar = requests.cookies.RequestsCookieJar()
        for c in self._cdp.get_cookies():
            d = (c.get("domain") or "").lstrip(".")
            if not d:
                continue
            if host == d or host.endswith("." + d):
                jar.set(c.get("name", ""), c.get("value", ""),
                        domain=d, path=c.get("path", "/"),
                        secure=bool(c.get("secure")))
        return jar

    def clear_all(self):
        self._cdp.clear_cookies()


class WebClient(QObject):
    """CDP 驱动的邮箱页面控制（API 与旧 QtWebEngine 版一致）。"""

    log_signal = Signal(str)

    def __init__(self, cdp=None, wv=None, parent=None):
        """wv：WebView2 控件（pythonnet）。提供时脚本注入走原生 API（CDP addScript 在 WebView2 无效）。"""
        super().__init__(parent)
        self._wv = wv
        if cdp is None:
            cdp = _CDP()
        self._cdp = cdp
        self.cookies = _CookieBridge(cdp)
        self._tracker_installed = False
        self._page_script_ids = []
        self._install_tracker()
        self.start_state_sampler()

    # ---------- JS 执行（CDP 本身线程安全，无需信号槽转发） ----------
    def run_js(self, script, timeout=20000):
        wrapped = (
            "(function(){"
            "try{"
            "var __r=(" + script + ");"
            "if(__r&&typeof __r==='object'){return JSON.stringify(__r);}"
            "return __r;"
            "}catch(e){return '__JS_ERR__:'+e.message;}"
            "})()"
        )
        value = self._cdp.eval(wrapped)
        if isinstance(value, str) and value.startswith("__JS_ERR__:"):
            raise RuntimeError(value[len("__JS_ERR__:"):])
        return value

    def run_js_obj(self, script, timeout=20000):
        value = self.run_js(script, timeout=timeout)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def run_js_threadsafe(self, script, timeout=20000):
        return self.run_js(script, timeout=timeout)

    def run_js_obj_threadsafe(self, script, timeout=20000):
        return self.run_js_obj(script, timeout=timeout)

    @staticmethod
    def qt_sleep(seconds):
        """兼容旧 API：等待（WebView2 渲染在独立进程，不需要 Qt 事件循环保活）。"""
        time.sleep(seconds)

    def navigate(self, url):
        """导航：优先 WebView2 原生 API（CDP Page.navigate 在 WebView2 是假导航，
        只更新 target 元数据、页面不跟随）。"""
        try:
            with open(r"D:\AI\git\invoice-helper\crash_diag.log", "a", encoding="utf-8") as f:
                f.write("[%s] NAVIGATE -> %s\n" % (time.strftime("%H:%M:%S"), url))
        except Exception:
            pass
        if self._wv is not None:
            from app.engine.webview2_host import navigate as native_nav
            native_nav(self._wv, url)
        else:
            self._cdp.navigate(url)
        # 导航后补注入 tracker：AddScript 注册失败时保证当前页可用（幂等，
        # 文档创建时已注入过则 IIFE 直接 return）
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(4000, self._inject_tracker_now)
        except Exception:
            pass

    def current_url(self):
        try:
            v = self._cdp.eval("location.href")
            return v or ""
        except Exception:
            return ""

    def get_sid(self):
        """获取会话 sid：cookie xm_sid / sid → 页面 URL。"""
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
                url = self.current_url() or ""
                m = re.search(r"[?&]sid=([^&#]+)", url)
                if m:
                    sid = m.group(1)
            except Exception:
                pass
        return sid

    # ---------- 勾选监听注入（CDP 版：新文档加载前注入 + 当前页补注入） ----------
    def _install_tracker(self):
        import threading as _th
        src = TRACKER_SCRIPT.replace("__PRELOAD_PORT__", str(msgid_service.PORT))
        # 应用启动清理：清空上次会话残留勾选记录 + 重置会话起点。
        # 注意：tracker 注入脚本本身不再清空（避免页面重载时误清历史 bug），
        # 因此"清空"只能放在这里——每次应用启动仅执行一次。
        # 异步执行：CDP 未就绪时 run_js 可能阻塞数秒，不能卡主线程。
        _th.Thread(target=self._startup_cleanup, daemon=True).start()
        native_ok = False
        # 首选：WebView2 原生注入（所有文档加载前执行，可靠）
        # 注意：必须检查返回值——WebView2 内核未就绪时 AddScriptToExecuteOnDocumentCreatedAsync
        # 返回 None（失败），若忽略会导致 tracker 从未注入（历史 bug：installed=false）
        if self._wv is not None:
            from app.engine.webview2_host import add_script_on_document_created
            try:
                sid = add_script_on_document_created(self._wv, src)
                if sid is not None:
                    native_ok = True
                    self._diag_log(f"TRACKER native inject OK sid={str(sid)[:20]}")
                else:
                    self._diag_log("TRACKER native inject FAILED (core not ready)")
            except Exception as e:
                self._diag_log(f"TRACKER native inject EXC: {e}")
        if not native_ok:
            # 兜底：CDP addScript（对未来的文档尽力而为）+ 采样器自愈注入。
            # 注意：绝不能在后台线程重试访问 wv（WinForms 控件跨线程操作会导致
            # .NET 崩溃、进程无声退出——历史崩溃根因），原生注入失败就放弃，
            # 由采样器每 2 秒检查 installed 并在缺失时通过 CDP run_js 补注入。
            if src not in self._cdp._page_script_sources:
                self._cdp._page_script_sources.append(src)
            try:
                self._cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": src})
            except Exception:
                pass
        # 当前已加载页面补一次注入（幂等：IIFE 开头有 __invoiceTrackerInstalled 防重）
        self._inject_tracker_now()
        self._tracker_installed = True

    def _startup_cleanup(self):
        try:
            self.run_js("(function(){try{localStorage.removeItem('invoice_selected');"
                        "localStorage.setItem('invoice_session_start', String(Date.now()));"
                        "}catch(e){}})()", timeout=5000)
        except Exception:
            pass

    def _inject_tracker_now(self):
        """向当前文档手动注入 tracker（导航完成后调用，幂等）。后台线程执行，避免阻塞 UI。"""
        import threading as _th

        def _run():
            try:
                src = TRACKER_SCRIPT.replace("__PRELOAD_PORT__", str(msgid_service.PORT))
                val = self.run_js(src, timeout=5000)
                self._diag_log(f"INJECT done ret={str(val)[:60]}")
            except Exception as e:
                self._diag_log(f"INJECT err: {e}")
        _th.Thread(target=_run, daemon=True).start()

    # 注意：原生注入重试逻辑已移除——后台线程访问 WinForms 控件(wv)会触发
    # .NET 跨线程崩溃（进程无声退出）；原生注入失败时由采样器自愈（CDP run_js）兜底。

    # ---------- 接口观察（CDP Network 事件替代 JS hook） ----------
    def start_api_observe(self):
        self._cdp._net_events = []
        self._cdp._observing = True

    def stop_api_observe(self):
        self._cdp._observing = False

    def get_observed_requests(self):
        """返回观察到的请求列表：[{u, m, t}]（与旧版语义一致）。"""
        return list(self._cdp._net_events)

    # ---------- 诊断状态采样（应用自身 CDP 连接定时读取页面/勾选状态，写入 msgid_service 供外部 HTTP 查询） ----------
    def start_state_sampler(self, interval_ms=2000):
        """每 interval_ms 采样一次页面勾选状态，写入 msgid_service.DIAG_STATE。
        外部通过 http://127.0.0.1:18765/diag 查询，避免外部另开 CDP 连接干扰运行。
        注意：采样/注入在后台线程执行——CDP 不畅时单次可阻塞数秒，
        放主线程会卡死 Qt 事件循环（历史 bug：启动后无响应）。"""
        import threading as _th
        from PySide6.QtCore import QTimer
        t = QTimer(self)
        t.timeout.connect(lambda: _th.Thread(
            target=self._sample_tracker_state, daemon=True).start())
        t.start(interval_ms)
        self._sampler_timer = t

    def _sample_tracker_state(self):
        try:
            js = (
                "(function(){var out={};"
                "out.installed=!!window.__invoiceTrackerInstalled;"
                "try{out.selectedCount=Object.keys(JSON.parse(localStorage.getItem('invoice_selected')||'{}')).length;}catch(e){out.selectedCount=-1;}"
                "out.sessStart=localStorage.getItem('invoice_session_start')||'';"
                "var rows=document.querySelectorAll('div[class*=list-item]');"
                "out.domRows=rows.length;"
                "var dc=0;"
                "for(var i=0;i<rows.length;i++){"
                "var el=rows[i];"
                "if(el.querySelector('.ui-checkbox-icon-checked')){dc++;continue;}"
                "if(el.querySelector('.checkbox-checked,.checkbox-selected,.checked-icon')){dc++;continue;}"
                "if(/mail-item-checked|item-checked|selected-item|checked-row/.test(el.className||'')){dc++;continue;}"
                "var cb=el.querySelector('input[type=checkbox]');"
                "if(cb&&cb.checked)dc++;"
                "}"
                "out.domChecked=dc;"
                "out.href=location.href;"
                "return JSON.stringify(out);})()"
            )
            val = self.run_js(js, timeout=3000)
            if isinstance(val, str) and val.startswith("{"):
                d = json.loads(val)
                d["ts"] = int(time.time())
                msgid_service.set_diag_state(d)
                # 自愈：页面跳转/重载后 tracker 丢失，自动补注入（最多 2 秒内恢复）
                if not d.get("installed"):
                    self._inject_tracker_now()
        except Exception as e:
            self._diag_log(f"SAMPLER err: {e}")

    @staticmethod
    def _diag_log(msg):
        try:
            import os as _os
            with open(_os.path.join(r"D:\AI\git\invoice-helper", "crash_diag.log"),
                      "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    # ---------- 勾选读取（与旧版完全一致） ----------
    def get_selected_mails(self, keep_selection=True):
        js = SELECTED_MAILS_JS % ("true" if keep_selection else "false")
        try:
            value = self.run_js_obj(js, timeout=15000)
        except Exception:
            self._diag_log("GET_SELECTED err: run_js 失败")
            return []
        if not isinstance(value, list):
            self._diag_log(f"GET_SELECTED 返回非列表: {str(value)[:80]}")
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
                "message_id": (d.get("message_id", "") or "").strip(),
                "text": fulltext or (subject + " " + sender),
            })
        self._diag_log(f"GET_SELECTED 返回 {len(mails)} 封（原始 {len(value)} 条）")
        return mails
