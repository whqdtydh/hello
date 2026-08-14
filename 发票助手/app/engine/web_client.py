"""基于 QtWebEngine 的邮箱页面控制。

自动化由 runJavaScript 驱动；下载采用「批量原生下载」：
点击附件下载按钮后，让 QtWebEngine 原生把文件保存到指定命名路径
（不重放 URL、不依赖 cookie 抓取，速度快且稳定），同时保留嗅探 URL 兜底。
"""

import json
import os
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

    def __init__(self, urls: list):
        super().__init__()
        self._urls = urls

    def interceptRequest(self, info):
        url = info.requestUrl().toString().lower()
        if any(h in url for h in self.DOWNLOAD_HINTS):
            self._urls.append(info.requestUrl().toString())
            # 不 block：让浏览器继续发请求（保持会话一致），下载结果由 requests 拉取。


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
        self._download_items = {}        # 下载句柄队列
        self._interceptor = AttachmentInterceptor(self._sniffed_urls)
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
  document.addEventListener('click', function(e){
    var t=e.target;
    var cb=t.closest?t.closest('.mail-checkbox,.xmail-ui-checkbox'):null;
    if(!cb)return;
    var item=cb.closest?cb.closest('div[class*=list-item]'):null;
    if(!item)return;
    var mailid=item.getAttribute('data-mailid')||'';
    if(!mailid)return;
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

    def get_selected_mails(self):
        """返回勾选的邮件项列表，读取后自动取消所有勾选。

        数据来源二合一：
          1) 注入到页面的监听脚本维护 localStorage['invoice_selected']（用户在
             DOM 中勾选/取消时实时记录，覆盖滚出视图的邮件）；
          2) 实时扫描当前 DOM 中处于勾选状态的邮件（兜底：脚本注入失败、
             勾选发生在脚本运行前的场景）。
        两者按 mailid 合并去重。读取完成后【取消网页勾选 + 清空记录】，
        保证「只下载本次勾选的邮件」——下次未勾选则不会重复下载。
        返回列表元素结构：{mailid, sender, subject, time, fulltext, text}
        """
        js = r"""
(function(){
  var KEY='invoice_selected';
  function readAll(){try{return JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){return {};}}
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
  var merged={};
  var items=document.querySelectorAll('div[class*=list-item]');
  var checkedEls=[];
  for(var i=0;i<items.length;i++){
    var el=items[i];
    var cls=el.className||'';
    var cbIcon=el.querySelector('.ui-checkbox-icon-checked');
    var checked=/mail-item-checked/.test(cls)||!!cbIcon
      ||/sel|selected|current|active|checked/i.test(cls);
    if(!checked)continue;
    var mailid=el.getAttribute('data-mailid')||'';
    if(!mailid)continue;
    merged[mailid]={mailid:mailid,sender:senderOf(el),subject:subjOf(el),
                    time:timeOf(el),fulltext:(el.innerText||'').slice(0,400)};
    checkedEls.push(el);
  }
  var stored=readAll();
  for(var k in stored){
    var d=stored[k];
    if(d&&d.mailid){merged[k]=d;}
  }
  var out=[];
  for(var k2 in merged){out.push(merged[k2]);}
  // 取消网页上所有勾选（模拟点击勾选框，同步 QQ 内部状态）
  var cbs=[];
  for(var j=0;j<checkedEls.length;j++){
    var cb=checkedEls[j].querySelector('.mail-checkbox,.xmail-ui-checkbox');
    if(cb)cbs.push(cb);
  }
  for(var c=0;c<cbs.length;c++){try{cbs[c].click();}catch(e){}}
  try{localStorage.removeItem(KEY);}catch(e){}
  return JSON.stringify(out);
})()
"""
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
    def click_mail(self, index):
        js = (
            "(function(){"
            "var els=document.querySelectorAll('div[class*=list-item]');"
            "if(els.length<=%d)return false;"
            "els[%d].click();return true;})()" % (index, index)
        )
        return bool(self.run_js(js, timeout=15000))

    def mail_detail_ready(self):
        js = (
            "(function(){var el=document.querySelector('div.mail-detail-attaches');"
            "return !!el;})()"
        )
        return bool(self.run_js(js, timeout=8000))

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