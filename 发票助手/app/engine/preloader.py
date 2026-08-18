"""边勾边读：后台预读器。

用户在网页勾选邮件的瞬间，网页 JS 把 mailid POST 到本机 msgid_service 的
/check 登记为「待预读」；本模块的后台线程轮询队列，对每封邮件：
  1) 预拉 readmail 详情（正文 HTML、消费日期、下载链接）
  2) 预拉 maillist 全量（附件 download_url 缓存）
等用户勾完点「开始下载」时，ApiDownloadController 直接复用这里的缓存，
跳过详情/列表拉取，立即进入下载阶段。

预读失败静默处理（不打断用户勾选、不影响下载兜底流程）。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.engine import msgid_service
from app.engine.api_downloader import QQMailApi
from app.engine.api_registry import ApiRegistry


class Preloader:
    """后台预读器（常驻 daemon 线程）。"""

    def __init__(self, client, log=None):
        self.client = client            # WebClient：提供 cookie store 访问
        self.log = log or (lambda *a: None)
        self._detail = {}               # mailid → readmail body.item dict
        self._mail_cache = {}           # mailid → maillist item（含附件 URL）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._api = None

    # ---------- 生命周期 ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---------- 下载侧查询 ----------

    def get_detail(self, mailid):
        """返回预读的 readmail 详情（与 fetch_readmail 返回结构一致），未命中返回 None。"""
        with self._lock:
            return self._detail.get(mailid)

    def get_attach_urls(self, mailid):
        """返回预读的附件 download_url 列表；未命中返回 None（由下载侧兜底）。"""
        with self._lock:
            it = self._mail_cache.get(mailid)
        if not it:
            return None
        urls = [a.get("download_url", "") for a in it.get("normal_attach", []) or []
                if a.get("download_url")]
        return urls

    def preloaded_count(self):
        with self._lock:
            return len(self._detail)

    # ---------- 后台循环 ----------

    def _ensure_api(self):
        if self._api is not None:
            return
        # 探测登录状态：sid/cookie 为空说明还没登录，等下次轮询再试
        sid = self.client.cookies.get_cookie_value("xm_sid") or ""
        if not sid and not self.client.cookies.get_cookie_value("sid"):
            return False
        jar = self.client.cookies.get_cookie_jar("https://wx.mail.qq.com/")
        self._api = QQMailApi(jar, sid, on_log=self.log,
                              registry=ApiRegistry(memory=True))  # 内存注册表：不污染共享接口状态
        return True

    def _loop(self):
        # 0) 等登录（未登录时轮询等待，避免启动阶段误判接口失效）
        waited = 0
        while not self._stop.is_set() and waited < 120:
            if self._ensure_api():
                break
            time.sleep(2)
            waited += 2
        if self._stop.is_set():
            return
        # 1) 先预拉 maillist 全量（附件 URL 缓存），失败静默
        try:
            self.log("后台预读：拉取邮件列表…")
            for page in range(40):
                if self._stop.is_set():
                    break
                lst = self._api.fetch_maillist(page_now=page, page_size=50)
                if not lst:
                    break
                with self._lock:
                    for it in lst:
                        eid = it.get("emailid") or it.get("mailid") or ""
                        if eid:
                            self._mail_cache[eid] = it
                if len(lst) < 50:
                    break
            with self._lock:
                self.log(f"后台预读：邮件列表缓存 {len(self._mail_cache)} 封")
        except Exception:
            pass
        # 2) 轮询待预读队列（勾选 → 立即并发拉详情）
        while not self._stop.is_set():
            try:
                pending = msgid_service.pending_preload()
                if not pending:
                    time.sleep(0.5)
                    continue
                self._ensure_api()
                if self._api is None:
                    time.sleep(2)
                    continue
                # 多封并发预读（4 worker），跟上用户勾选速度
                with ThreadPoolExecutor(max_workers=4, thread_name_prefix="pre") as ex:
                    list(ex.map(self._preload_one, pending))
            except Exception:
                time.sleep(1)

    def _preload_one(self, mailid):
        """单封邮件预读：命中缓存直接移除，否则拉详情入缓存。"""
        try:
            with self._lock:
                hit = mailid in self._detail
            if hit:
                msgid_service.done_preload(mailid)
                return
            item = self._api.fetch_readmail(mailid, func=1)
            if item:
                with self._lock:
                    self._detail[mailid] = item
            msgid_service.done_preload(mailid)
        except Exception:
            pass
