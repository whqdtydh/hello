"""纯 API 版发票下载引擎（勾选模式，不走网页 UI 点击）。

用户在内嵌网页勾选邮件 -> 程序读取勾选 mailid -> 用 QQ 邮箱网页接口
（list/maillist、read/readmail、attach/download、fapiao/download）批量下载。

已验证（2026-08-15）：
  - GET  /list/maillist?sid=..&dir=1&dirid=1&page_now=N&page_size=50  -> 邮件列表（含附件 download_url）
  - POST /read/readmail {mailid, func:1}                              -> 邮件正文 HTML（含 51fapiao/alipay 直链）
  - GET  /attach/download?mailid=..&fileid=..&name=..                 -> 附件文件（zip/pdf）
  - GET  /fapiao/download?fapiao_list={"fapiao":[{"no","code"}]}      -> 发票 PDF
  - 51fapiao 链接型：checkDownloadPermissions -> downloadFile           -> 发票 PDF
"""

import json
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

from app import config
from app.engine.acl_util import grant_current_user_access
from app.engine.api_registry import ApiRegistry
from app.engine.mail_parse import invoice_kind, ticket_amount, consume_date, kind_from_pdf
from app.engine.web_client import WebClient


# ---------- 纯函数：命名解析（复用 downloader 逻辑） ----------

def is_invoice_mail(item_text):
    """根据邮件正文判断是否发票邮件。"""
    if any(kw in item_text for kw in config.SKIP_KEYWORDS):
        return False
    low = item_text.lower()
    if any(kw in low for kw in config.INVOICE_FROM_KEYWORDS):
        return True
    return "发票" in item_text or "行程单" in item_text


def date_label(date_str):
    """把 2026-08-06 简化为 8.6号。"""
    try:
        parts = date_str.split("-")
        return f"{int(parts[1])}.{int(parts[2])}号"
    except Exception:
        return ""


def _cn_date_to_iso(cn):
    """把 2026年08月06日 转为 2026-08-06，失败返回空串。"""
    try:
        m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", cn or "")
        if not m:
            return ""
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    except Exception:
        return ""


def parse_original_name(display_name):
    company, amount = "", ""
    m_company = re.search(r"【([^】]*)】", display_name)
    if m_company:
        raw = m_company.group(1)
        company = re.split(r"-\d+(?:\.\d+)?元", raw)[0].strip(" -")
    # 金额：兼容「33.00元」「金额为33.00的」「33.00元的」等多种写法
    m_amount = re.search(r"(\d+(?:\.\d+)?)元|金额(?:为|是)?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)的电子发票", display_name)
    if m_amount:
        amount = next((g for g in m_amount.groups() if g), "") or ""
    return company, amount


def _month_date_label(date_str):
    """把 2026-08-11 简化为 8月11（无「号」字）。"""
    try:
        parts = date_str.split("-")
        return f"{int(parts[1])}月{int(parts[2])}"
    except Exception:
        return ""


def build_filename(kind, display_name, date_str, msg=None, rw=None):
    """简化命名：8.6号_发票_31.27.pdf / 8.6号_行程单_31.27.pdf / 8.6号_高速发票_21.00.pdf /
    8.6号_打车发票_82.94.pdf；高铁：8月9_高铁_上海虹桥-杭州东_120.00.pdf

    规则：所有文件命名必须标注价格；提取不到金额时标注 0.00。
    """
    company, amount = parse_original_name(display_name)
    if "高铁" in kind:
        if rw:
            date_part = _month_date_label(rw.get("date") or rw.get("issue_date") or date_str)
            route = rw.get("route", "")
            amt = rw.get("amount") or 0.0
            parts = [p for p in [date_part, "高铁", route, f"{amt:.2f}"] if p]
            if parts:
                return "_".join(parts) + ".pdf"
        label = "高铁发票"
    elif "行程单" in kind:
        label = "行程单"
    elif "发票" in kind:
        label = kind
    else:
        label = kind
    if not amount and msg is not None:
        try:
            amt = ticket_amount(msg)
            if amt:
                amount = f"{amt:.2f}"
        except Exception:
            pass
    parts = [date_label(date_str), label]
    # 强制标注价格：提取不到则 0.00
    parts.append(amount if amount else "0.00")
    return "_".join(p for p in parts if p) + ".pdf"


def unique_path(dest_dir, filename):
    base, ext = os.path.splitext(filename)
    cand = os.path.join(dest_dir, filename)
    n = 1
    while os.path.exists(cand):
        cand = os.path.join(dest_dir, f"{base}_{n}{ext}")
        n += 1
    return cand


def _sanitize_folder_name(name, max_len=40):
    """清理文件夹名中的非法字符并截断。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" ._")
    return name or "未命名"


def failed_folder_name(subject, date_str="", sender=""):
    """失败标记文件夹名：日期 + 主题摘要 + 发件人（简短，可作文件夹名）。

    例：8.6号_【电子发票】上海华铁旅客服务…_dzfp@51fapiao.cloud
    """
    parts = []
    if date_str:
        parts.append(date_label(date_str))
    subj = _sanitize_folder_name(subject or "", max_len=30)
    if subj:
        parts.append(subj)
    if sender:
        parts.append(_sanitize_folder_name(sender, max_len=20))
    return "_".join(parts) if parts else "未下载邮件"


def unique_dir(dest_dir, folder_name):
    """避免文件夹名冲突：同名则追加 _1/_2。"""
    cand = os.path.join(dest_dir, folder_name)
    n = 1
    while os.path.exists(cand):
        cand = os.path.join(dest_dir, f"{folder_name}_{n}")
        n += 1
    return cand


def extract_amount_from_text(text):
    """从文本（附件名/主题/文件名）提取金额。兼容「82.94元」「金额为33.00的」
    「33.00元」以及文件名格式「6.15号_打车发票_19.96.pdf」「..._19.96_1.pdf」。

    返回 float 或 0.0。
    """
    if not text:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)元|金额(?:为|是)?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)的电子发票", text)
    if not m:
        # 兜底：文件名格式（label 后的金额，可选 _N 去重后缀）
        m = re.search(r"_(\d+(?:\.\d+)?)(?:_\d+)?\.pdf$", text)
    if not m:
        return 0.0
    val = next((g for g in m.groups() if g), "")
    try:
        v = float(val)
        # 金额上限保护：订单号/发票号等长数字（如 26339190041008832512）误入时归 0
        return v if 0 < v < 999999 else 0.0
    except (ValueError, TypeError):
        return 0.0


def rename_dir_with_amount(dir_path, amount):
    """在保存目录下新建子文件夹「总金额元」，把文件移入。返回子文件夹路径。"""
    sub = os.path.join(dir_path, f"{amount:.2f}元")
    try:
        os.makedirs(sub, exist_ok=True)
    except Exception:
        return dir_path
    return sub


# ---------- 链接提取 ----------

def extract_51fapiao_links(content):
    """从邮件正文 HTML 提取 51fapiao 下载链接（dlj.51fapiao.cn/dlj/v7/<id>）。

    兼容：链接可能被 HTML 实体转义（&amp;）、被 <wbr> 断词标签拆断、
    被零宽空格拆分、或带 query 参数。返回完整 URL（含 dlj id）。
    """
    links = set()
    if not content:
        return []
    # 1) 还原常见 HTML 实体
    text = content.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    # 2) 去掉 <wbr> 断词标签与零宽空格（QQ 邮箱给长链接插断词点，导致 id 被拆成两段）
    text = re.sub(r"<wbr\s*/?>", "", text, flags=re.I)
    text = text.replace("\u200b", "").replace("\u200e", "").replace("\u200f", "")
    for m in re.finditer(r'https?://dlj\.51fapiao\.cn/dlj/v7/([A-Za-z0-9]+)', text):
        links.add(m.group(0))
    # 3) 兜底：去全部 HTML 标签后再匹配（href 属性里的完整链接会变为纯文本）
    plain = re.sub(r"<[^>]+>", "", text)
    for m in re.finditer(r'https?://dlj\.51fapiao\.cn/dlj/v7/([A-Za-z0-9]{20,})', plain):
        links.add(m.group(0))
    # 4) 最终兜底：裸 dlj id（尽量取最长者，避免截断残留）
    best = ""
    for m in re.finditer(r"dlj\.51fapiao\.cn/dlj/v7/([A-Za-z0-9]{20,})", text):
        if len(m.group(1)) > len(best):
            best = m.group(1)
    if best:
        links.add(f"https://dlj.51fapiao.cn/dlj/v7/{best}")
    return sorted(links, key=len, reverse=True)


def extract_qrcode_images(content):
    """从邮件正文 HTML 提取二维码图片 URL（51fapiao 等平台用二维码承载下载链接）。"""
    links = set()
    for m in re.finditer(r'https?://[^\s"\'<>]+\.(?:png|jpg|jpeg|gif)(?:\?[^\s"\'<>]*)?', content or ""):
        links.add(m.group(0))
    return sorted(links)


def extract_alipay_links(content):
    """从邮件正文 HTML 提取支付宝发票直链（mdn.alipayobjects.com/...pdf）。"""
    links = set()
    for m in re.finditer(r'https?://mdn\.alipayobjects\.com/[^\s"\'<>]+?\.pdf(?:\?[^\s"\'<>]*)?', content or ""):
        links.add(m.group(0))
    return sorted(links)


def extract_oss_links(content):
    """从邮件正文 HTML 提取阿里云 OSS 商家发票直链（淘宝闪购等平台）。

    特征：fin-invoice-*.oss-cn-*.aliyuncs.com/cInvoice/manualInvoice/*.jpg
    链接带签名参数（Expires/OSSAccessKeyId/Signature），须从正文实时提取。
    """
    links = set()
    if not content:
        return []
    # 还原常见 HTML 实体（&amp; 等）
    text = content.replace("&amp;", "&")
    # OSS 域名下的 cInvoice/manualInvoice 图片
    for m in re.finditer(
        r'https?://[a-z0-9-]*\.?oss-cn-[a-z0-9-]+\.aliyuncs\.com/'
        r'cInvoice/[^\s"\'<>]+?\.(?:jpg|jpeg|png)(?:\?[^\s"\'<>]*)?',
        text, re.I):
        links.add(m.group(0))
    return sorted(links)


def extract_invoice_no(subject):
    """从主题提取发票号码（如 发票号码:11901140 / 发票号码：2631...）。"""
    m = re.search(r"发票号码[:：]\s*(\d{8,25})", subject or "")
    if m:
        return m.group(1)
    return ""


# ---------- API 客户端 ----------

class QQMailApi:
    """封装 QQ 邮箱网页接口（纯 requests，不依赖网页 UI）。

    接口路径从 ApiRegistry 读取（默认值为逆向基准），腾讯改版后可通过
    失效自动学习机制更新，无需改代码。
    """

    BASE = "https://wx.mail.qq.com"

    def __init__(self, cookies, sid, on_log=None, registry=None):
        self.cookies = cookies          # requests CookieJar
        self.sid = sid or ""
        self.on_log = on_log or (lambda m: None)
        self.registry = registry        # ApiRegistry（可选）
        self.session = requests.Session()
        self.session.cookies = cookies
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
            "Referer": "https://wx.mail.qq.com/home/index",
        })

    # ---------- 接口路径（注册表优先） ----------
    def _ep_path(self, name):
        """返回接口路径（可能已被学习覆盖），默认回退基准值。"""
        if self.registry:
            p = self.registry.get_path(name)
            if p:
                return p
        from app.engine.api_registry import DEFAULT_ENDPOINTS
        return DEFAULT_ENDPOINTS.get(name, {}).get("path", "/" + name)

    def _mark_failed(self, name):
        """标记接口失效（触发自动学习流程）。"""
        if self.registry:
            self.registry.mark_failed(name)

    def _url(self, path):
        sep = "&" if "?" in path else "?"
        return f"{self.BASE}{path}{sep}sid={urllib.parse.quote(self.sid)}&r={int(time.time()*1000)}"

    def fetch_maillist(self, page_now=0, page_size=50, dirid=1):
        """拉取一页邮件列表。返回 list（可能为空）。"""
        url = self._url(
            f"{self._ep_path('maillist')}?dir=1&dirid={dirid}&func=1&sort_type=1&sort_direction=1"
            f"&page_now={page_now}&page_size={page_size}&enable_topmail=true")
        try:
            r = self.session.get(url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            self.on_log(f"maillist 请求异常: {str(e)[:60]}")
            self._mark_failed("maillist")
            return []
        j = r.json()
        head = j.get("head", {})
        if head.get("ret") != 0:
            self.on_log(f"maillist ret={head.get('ret')} msg={head.get('msg','')[:60]}")
            self._mark_failed("maillist")
            return []
        return j.get("body", {}).get("list", []) or []

    def fetch_maillist_all(self, max_pages=40, page_size=50, dirid=1):
        """分页拉取全部邮件列表，返回 {emailid: item}。"""
        out = {}
        for page in range(max_pages):
            lst = self.fetch_maillist(page_now=page, page_size=page_size, dirid=dirid)
            if not lst:
                break
            for it in lst:
                eid = it.get("emailid") or it.get("mailid") or ""
                if eid:
                    out[eid] = it
            if len(lst) < page_size:
                break
        return out

    def fetch_readmail(self, mailid, func=1):
        """拉取邮件详情。返回 body.item dict（含 content 正文 HTML）。"""
        url = self._url(self._ep_path("readmail"))
        try:
            r = self.session.post(url, data={"mailid": mailid, "func": func}, timeout=20)
            r.raise_for_status()
        except Exception as e:
            self.on_log(f"readmail 请求异常: {str(e)[:60]}")
            self._mark_failed("readmail")
            return {}
        j = r.json()
        head = j.get("head", {})
        if head.get("ret") != 0:
            self.on_log(f"readmail ret={head.get('ret')} msg={head.get('msg','')[:60]}")
            self._mark_failed("readmail")
            return {}
        item = j.get("body", {}).get("item", {}) or {}
        if str(item.get("ret", "0")) != "0":
            self.on_log(f"readmail item.ret={item.get('ret')}")
            self._mark_failed("readmail")
            return {}
        return item

    def download_attach(self, download_url, dest_path, session=None):
        """附件型：download_url 形如 /attach/download?mailid=..&fileid=..&name=..
        session 可选：并发下载时传入独立会话，避免共享 Session 线程竞争。"""
        url = download_url
        if url.startswith("/"):
            url = self._url(url)
        return self._download(url, dest_path, session=session)

    def download_fapiao(self, no, code, name, dest_path):
        """已知发票号码：/fapiao/download?fapiao_list={"fapiao":[{"no","code"}]}"""
        fapiao_list = urllib.parse.quote(json.dumps({"fapiao": [{"no": no, "code": code or ""}]}))
        url = self._url(f"{self._ep_path('fapiao')}?fapiao_list={fapiao_list}&name={urllib.parse.quote(name)}")
        ok = self._download(url, dest_path)
        if not ok:
            self._mark_failed("fapiao")
        return ok

    def download_51fapiao(self, dlj_url, dest_path):
        """51fapiao 链接型下载。

        2026-08-15 实测：GET {base}/dlj/v7/downloadFile/{dlj} 无需 signatureString
        直接返回 PDF（200, application/pdf）。checkDownloadPermissions 非必需。
        若直接下载失败，再退回「提取 sig -> 权限检查 -> 带 sig 下载」流程。
        """
        m = re.search(r"/dlj/v7/([A-Za-z0-9]+)", dlj_url)
        if not m:
            return False
        dlj = m.group(1)
        base = "https://dlj.51fapiao.cn"
        h = {"User-Agent": self.session.headers["User-Agent"],
             "Referer": f"{base}/dlj/v7/{dlj}"}
        try:
            # 主路径：直接下载（实测无需 sig）
            dl_url = f"{base}/dlj/v7/downloadFile/{dlj}"
            if self._download(dl_url, dest_path, extra_headers=h):
                return True
            # 兜底：提取 signatureString 后重试
            r = requests.get(f"{base}/dlj/v7/{dlj}", headers=h, timeout=15)
            sig = ""
            m_sig = re.search(r'name=["\']signatureString["\'][^>]*value=["\']([^"\']*)["\']', r.text)
            if not m_sig:
                m_sig = re.search(r'value=["\']([^"\']*)["\'][^>]*name=["\']signatureString["\']', r.text)
            if m_sig:
                sig = m_sig.group(1)
            if not sig:
                m_sig2 = re.search(r"signatureString\s*[:=]\s*['\"]([^'\"]+)['\"]", r.text)
                if m_sig2:
                    sig = m_sig2.group(1)
            self.on_log(f"    51fapiao 直接下载失败，回退 sig 流程: signatureString={'✓' if sig else '(空)'} dlj={dlj}")
            if not sig:
                return False
            perm_url = f"{base}/dlj/v7/checkDownloadPermissions/getFile/{dlj}/{sig}"
            r2 = requests.get(perm_url, headers=h, timeout=15)
            if r2.text.strip() != "success":
                self.on_log(f"51fapiao 权限检查失败: {r2.text[:80]}")
                return False
            dl_url = f"{base}/dlj/v7/downloadFile/{dlj}?signatureString={sig}"
            return self._download(dl_url, dest_path, extra_headers=h)
        except Exception as e:
            self.on_log(f"51fapiao 下载异常: {str(e)[:80]}")
            return False

    def _download(self, url, dest_path, extra_headers=None, session=None):
        """下载 URL 内容到 dest_path。返回 True/False。session 可选（并发时用独立会话）。"""
        try:
            s = session or self.session
            headers = dict(s.headers)
            if extra_headers:
                headers.update(extra_headers)
            with s.get(url, headers=headers, stream=True, timeout=60) as r:
                r.raise_for_status()
                os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
            grant_current_user_access(dest_path)
            return os.path.exists(dest_path) and os.path.getsize(dest_path) > 0
        except Exception as e:
            self.on_log(f"警告 下载失败 {os.path.basename(dest_path)}: {str(e)[:80]}")
            return False


# ---------- 控制器 ----------

class ApiDownloadController:
    """纯 API 下载控制器：勾选 -> 拉列表 -> 分类下载。

    集成接口失效自动学习：
      本次运行检测到接口失败 -> 标记 failed + 日志提示用户手动操作网页
      （JS hook 自动记录请求）；下次运行开始时读取观察记录，特征匹配
      更新注册表后，用新接口路径继续下载。
    """

    def __init__(self, client: WebClient, save_dir, on_log=None, on_progress=None):
        self.client = client
        self.save_dir = save_dir
        self.on_log = on_log or (lambda m, g="": None)
        self.on_progress = on_progress or (lambda p, t, d: None)
        self.registry = ApiRegistry()

        self.downloaded_files = []
        self.downloaded_pdf_count = 0
        self.stop_flag = False
        self._last_51_ok = False
        self._total_amount = 0.0
        self._lock = threading.Lock()          # 保护共享计数（并发下载）
        self._cache_lock = threading.Lock()    # 保护 maillist 缓存
        self._cache_done = threading.Event()   # 列表拉取完成标记（边拉边处理）
        self._max_pages = 40
        self._cur_group = ""                   # 当前处理邮件的日志分组
        self._processed_count = 0

    def log(self, msg):
        try:
            self.on_log(msg, self._cur_group)
        except TypeError:
            self.on_log(msg)

    def _maybe_learn_api(self):
        """运行前检查：若有 failed 接口，从观察记录学习新接口路径。

        学习成功返回更新的接口名列表；无 failed 或未学习到返回 []。
        注意：本方法不阻塞，学习失败只是日志提示，不中断主流程。
        """
        failed = self.registry.failed_endpoints()
        if not failed:
            return []
        self.log(f"检测到 {len(failed)} 个接口可能已变更: {', '.join(failed)}，尝试从网页观察记录学习…")
        observations = self.client.get_observed_requests()
        if not observations:
            self.log("  暂无网页请求记录。请在左侧网页中手动操作一次"
                     "（打开收件箱 / 打开一封邮件 / 下载一次发票），再点击开始下载。")
            # 开启观察，为下次学习做准备
            try:
                self.client.start_api_observe()
            except Exception:
                pass
            return []
        updated = self.registry.learn_from_observations(observations)
        if updated:
            self.registry.clear_failed()
            self.log(f"已学习到新接口: {', '.join(updated)}，本次将使用新接口重试")
        else:
            self.log("  观察记录未能匹配到新接口（可能页面未产生同类请求）。"
                     "请手动操作一次后再试。")
        return updated

    def _build_api(self, sid=None):
        """从 WebClient 的 cookie store 构建 QQMailApi。sid 优先用外部传入（GUI 线程已取好）。"""
        jar = self.client.cookies.get_cookie_jar("https://wx.mail.qq.com/")
        if not sid:
            sid = self.client.cookies.get_cookie_value("xm_sid") or ""
        return QQMailApi(jar, sid, on_log=self.log, registry=self.registry)

    def run(self, selected_mails, sid=None):
        """selected_mails: 用户勾选的邮件 [{mailid, subject, sender, text}]
        sid: 可选，GUI 线程已取好的会话 sid。
        多封邮件并发处理（2 worker），附件内部再并行下载（4 worker）。"""
        os.makedirs(self.save_dir, exist_ok=True)
        if not selected_mails:
            self.log("没有检测到勾选的邮件。")
            return []
        # 接口失效学习：运行前检查 failed 接口，尝试从网页观察记录更新路径
        self._maybe_learn_api()
        self.prepare(selected_mails, sid=sid)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail") as pool:
            futures = [pool.submit(self._safe_process_one, m, i)
                       for i, m in enumerate(selected_mails)]
            for f in futures:
                f.result()
        self.log(f"全部完成，共下载 {len(self.downloaded_files)} 个 PDF → {self.save_dir}")
        # 简单总结：读取到的邮件数量 + 下载的文件数量
        self.log(f"本次总结：读取邮件 {len(selected_mails)} 封，下载文件 {len(self.downloaded_files)} 个")
        self._archive_by_amount()
        return self.downloaded_files

    def _safe_process_one(self, m, i):
        try:
            self._process_one(m, i)
        except Exception as e:
            self.log(f"  处理异常: {str(e)[:80]}")
        finally:
            with self._lock:
                self._processed_count += 1
                self.on_progress(self._processed_count, self._total, self.downloaded_pdf_count)

    def _archive_by_amount(self):
        """下载完成后按合计金额归档：建「xx.xx元」子文件夹，把已下载 PDF 移入。"""
        if not self._total_amount or not self.downloaded_files:
            return
        self.log(f"  本次勾选合计金额：{self._total_amount:.2f} 元")
        sub = rename_dir_with_amount(self.save_dir, self._total_amount)
        if sub == self.save_dir:
            return
        moved = 0
        for f in list(self.downloaded_files):
            try:
                os.replace(f, os.path.join(sub, os.path.basename(f)))
                moved += 1
                new_f = os.path.join(sub, os.path.basename(f))
                self.downloaded_files[self.downloaded_files.index(f)] = new_f
                grant_current_user_access(new_f)
            except Exception:
                pass
        self.log(f"  已新建文件夹「{os.path.basename(sub)}」，移入 {moved} 个 PDF")

    def prepare(self, selected_mails, sid=None):
        """初始化处理队列。必须在 GUI 线程调用（读取 cookie）。sid 可选（GUI 线程已取好）。
        边拉边处理：先同步拉前 2 页即可开始处理，后台线程继续拉剩余页填缓存。"""
        os.makedirs(self.save_dir, exist_ok=True)
        self._mails = selected_mails
        self._i = 0
        self._total = len(selected_mails)
        self._api = self._build_api(sid=sid)
        # 预拉全量 maillist，建立 emailid → item 映射（含附件 download_url）
        self._mail_cache = {}
        self._cache_done = threading.Event()
        try:
            self.log("拉取邮件列表建立附件映射…")
            for page in range(2):
                lst = self._api.fetch_maillist(page_now=page, page_size=50)
                if not lst:
                    self._cache_done.set()
                    break
                self._merge_cache(lst)
                if len(lst) < 50:
                    self._cache_done.set()
                    break
        except Exception as e:
            self.log(f"  警告 拉取邮件列表失败: {str(e)[:60]}（将逐封查询）")
            self._cache_done.set()
        if not self._cache_done.is_set():
            def _fetch_rest():
                try:
                    for page in range(2, self._max_pages):
                        lst = self._api.fetch_maillist(page_now=page, page_size=50)
                        if not lst:
                            break
                        self._merge_cache(lst)
                        if len(lst) < 50:
                            break
                except Exception:
                    pass
                finally:
                    self._cache_done.set()
                    with self._cache_lock:
                        n = len(self._mail_cache)
                    self.log(f"  邮件列表缓存 {n} 封")
            threading.Thread(target=_fetch_rest, daemon=True).start()
        else:
            with self._cache_lock:
                n = len(self._mail_cache)
            self.log(f"  邮件列表缓存 {n} 封")
        # 若存在 failed 接口，开启网页观察并提示用户手动操作（供下次学习）
        if self.registry.failed_endpoints():
            self.log("  提示 接口失效：请手动在左侧网页操作一次（打开收件箱/打开邮件/下载发票），"
                     "程序会自动记录并学习新接口，下次下载将自动使用新接口。")
            try:
                self.client.start_api_observe()
            except Exception:
                pass
        self.log(f"检测到勾选 {self._total} 封邮件，开始处理…")

    def _merge_cache(self, lst):
        with self._cache_lock:
            for it in lst:
                eid = it.get("emailid") or it.get("mailid") or ""
                if eid:
                    self._mail_cache[eid] = it

    def _process_one(self, m, processed):
        """处理单封邮件：按类型下载。若本封未成功下载任何文件，建失败标记文件夹。
        并发安全：共享计数通过 self._lock 保护。"""
        mailid = m.get("mailid", "")
        subject = m.get("subject", "") or m.get("text", "")[:60]
        text = m.get("text", "") or subject
        sender = m.get("sender", "") or m.get("from", "") or ""
        self._cur_group = f"mail_{processed}"
        self.log(f"[处理] {subject[:40]}")

        if not is_invoice_mail(text):
            self.log(f"  跳过非发票邮件: {subject[:40]}")
            self._cur_group = ""
            return

        with self._lock:
            before = self.downloaded_pdf_count
        date_str = ""
        self.log(f"[详情] {subject[:50]}…")
        try:
            # 1) 先拉详情（readmail func=1 拿正文 + 附件信息）
            item = self._api.fetch_readmail(mailid, func=1)
            if not item:
                self.log("  详情获取失败")
                self._cur_group = ""
                return
            content = item.get("content", "") or ""
            info = item.get("info", {}) or {}
            date_ts = info.get("totime") or info.get("fromtime") or 0
            email_date = time.strftime("%Y-%m-%d", time.localtime(date_ts)) if date_ts else ""
            # 消费日期：从正文提取（行程时间/通行时间/出行日期/上车时间），取不到退回邮件日期
            # content 是 HTML，先去标签再匹配
            plain = re.sub(r"<[^>]+>", " ", content)
            consume = consume_date(plain) or consume_date(text)
            date_str = consume or email_date

            # 2) 附件型：readmail 返回里没有附件，需从 maillist 拿
            attach_urls = self._get_attach_urls(mailid)
            if attach_urls:
                mail_amounts = self._download_attaches(attach_urls, subject, date_str, text, sender)
                with self._lock:
                    now_count = self.downloaded_pdf_count
                if now_count > before:
                    # 按金额去重累加（发票+行程单同金额只算一次）
                    self._total_amount += sum(mail_amounts)
                self._cur_group = ""
                return

            # 3) 链接型：51fapiao / alipay
            dlj_links = extract_51fapiao_links(content)
            if dlj_links:
                amounts = self._download_51fapiao_links(dlj_links, subject, date_str, text, sender)
                # 诊断：下载失败时输出正文中 51fapiao 相关片段，便于定位链接截断
                with self._lock:
                    now_count = self.downloaded_pdf_count
                if now_count == 0 and not self._last_51_ok:
                    self._dump_51fapiao_context(content)
                if now_count > before:
                    self._total_amount += sum(amounts)
                self._cur_group = ""
                return

            alipay_links = extract_alipay_links(content)
            if alipay_links:
                amounts = self._download_alipay_links(alipay_links, subject, date_str, text, sender)
                with self._lock:
                    now_count = self.downloaded_pdf_count
                if now_count > before:
                    self._total_amount += sum(amounts)
                self._cur_group = ""
                return

            # 3.5) 阿里云 OSS 商家发票图片（淘宝闪购等平台）
            oss_links = extract_oss_links(content)
            if oss_links:
                amounts = self._download_oss_links(oss_links, subject, date_str, text, sender)
                with self._lock:
                    now_count = self.downloaded_pdf_count
                if now_count > before:
                    self._total_amount += sum(amounts)
                self._cur_group = ""
                return

            # 4) 已知发票号码：fapiao/download
            no = extract_invoice_no(subject)
            if no:
                self.log(f"  尝试按发票号码下载: {no}")
                kind = invoice_kind(subj=subject, body=text, sender=sender)
                fname = build_filename(kind, subject, date_str, msg=text)
                dest = unique_path(self.save_dir, fname)
                if self._api.download_fapiao(no, "", fname, dest):
                    self.report_downloaded(dest)
                self._cur_group = ""
                return

            self.log("  未找到附件/链接/发票号码，跳过")
        except Exception as e:
            self.log(f"  处理异常: {str(e)[:80]}")
        finally:
            with self._lock:
                now_count = self.downloaded_pdf_count
            if now_count == before:
                # 本封邮件未成功下载任何文件 → 建失败标记文件夹（文件夹名承载邮件信息）
                self._mark_failed(subject, date_str, sender)
                self.log("[失败] 未下载到文件")
            else:
                self.log(f"[成功] 下载文件 {now_count - before} 个")
            self._cur_group = ""

    def _mark_failed(self, subject, date_str, sender):
        """下载失败：建一个空文件夹，文件夹名 = 邮件简短信息。"""
        try:
            folder = unique_dir(self.save_dir, failed_folder_name(subject, date_str, sender))
            os.makedirs(folder, exist_ok=True)
            self.log(f"    未下载成功，已建标记文件夹：{os.path.basename(folder)}")
        except Exception as e:
            self.log(f"    建失败标记文件夹异常: {str(e)[:60]}")

    def _get_attach_urls(self, mailid):
        """从 maillist 缓存获取邮件的附件下载 URL 列表。
        边拉边处理：缓存未命中时等待拉取线程（最多 15 秒），超时后逐页单查。"""
        it = self._cache_get(mailid)
        if it:
            return [a.get("download_url", "") for a in it.get("normal_attach", []) or [] if a.get("download_url")]
        # 缓存未命中且列表还在拉取：轮询等待（最多 15 秒）
        waited = 0
        while not self._cache_done.is_set() and waited < 15000:
            time.sleep(0.2)
            waited += 200
            it = self._cache_get(mailid)
            if it:
                return [a.get("download_url", "") for a in it.get("normal_attach", []) or [] if a.get("download_url")]
        # 拉取完成仍未命中：逐页单查
        try:
            for page in range(5):
                lst = self._api.fetch_maillist(page_now=page, page_size=50)
                for it in lst:
                    if (it.get("emailid") or it.get("mailid")) == mailid:
                        return [a.get("download_url", "") for a in it.get("normal_attach", []) or [] if a.get("download_url")]
                if len(lst) < 50:
                    break
        except Exception as e:
            self.log(f"  警告 拉取附件信息失败: {str(e)[:60]}")
        return []

    def _cache_get(self, mailid):
        with self._cache_lock:
            return self._mail_cache.get(mailid)

    def _new_session(self):
        """为并发下载创建独立 requests 会话（共享 cookie，避免 Session 线程竞争）。"""
        s = requests.Session()
        s.cookies = self._api.cookies
        s.headers.update(dict(self._api.session.headers))
        return s

    def _append_pending_date_check(self, shared_date, email_date, temp_pdfs):
        """兜底日期（来自邮件日期）时，把文件名记入待确认列表，提示用户核对。"""
        if shared_date == email_date or shared_date is None:
            try:
                pending = os.path.join(self.save_dir, "待确认日期.txt")
                mode = "a" if os.path.exists(pending) else "w"
                with open(pending, "a", encoding="utf-8") as f:
                    if mode == "w":
                        f.write("以下文件命名日期来自邮件日期，可能不是消费当天，请核对后手动改名：\n\n")
                    for tmp_path, kind, orig_name in temp_pdfs:
                        f.write(f"  {os.path.basename(tmp_path)}  ({kind})\n")
            except Exception:
                pass

    def _download_attaches(self, attach_urls, subject, date_str, text, sender=""):
        """下载附件型发票（PDF/zip）。zip 附件解压后提取 PDF。

        两阶段策略：
        1. 并行下载所有 PDF 到临时目录（4 worker），提取消费日期，确定共享日期
        2. 再用共享日期统一命名保存

        日期优先级（行程单优先）：行程单票面日期（= 消费当天，最准）
        > 其他 PDF 票面日期（高速发票票面有通行时间）> 正文日期 > 邮件日期。
        """
        import tempfile, shutil

        # 去重：同名附件只下载一次
        seen_names = set()
        unique_urls = []
        for u in attach_urls:
            m = re.search(r"name=([^&]+)", u)
            key = urllib.parse.unquote(m.group(1)) if m else u
            if key not in seen_names:
                seen_names.add(key)
                unique_urls.append(u)
        if len(unique_urls) < len(attach_urls):
            self.log(f"  去重: {len(attach_urls)} → {len(unique_urls)} 个附件")
        attach_urls = unique_urls

        # ---- 阶段1：并发下载到临时目录 ----
        tmp_dir = tempfile.mkdtemp(prefix="fapiao_")
        temp_pdfs = []  # [(临时路径, kind, 原始文件名)]
        self.log(f"  附件 {len(attach_urls)} 个，并行下载…")

        def _dl_one(u):
            if self.stop_flag:
                return None
            name = ""
            m = re.search(r"name=([^&]+)", u)
            if m:
                try:
                    name = urllib.parse.unquote(m.group(1))
                except Exception:
                    name = m.group(1)
            suffix = os.path.splitext(name)[1].lower() if name else ".pdf"
            if suffix not in (".pdf", ".zip"):
                return None
            kind = invoice_kind(subj=subject, att_name=name, body=text, sender=sender)
            tmp_path = os.path.join(tmp_dir, name or f"tmp_{len(temp_pdfs)}.pdf")
            self.log(f"  下载 {name or '附件'}…")
            if not self._api.download_attach(u, tmp_path, session=self._new_session()):
                self.log(f"  下载失败 {name}")
                return None
            dl_ok = os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0
            self.log(f"  写入 {os.path.basename(tmp_path)} ok={dl_ok} size={os.path.getsize(tmp_path) if dl_ok else 0}")
            return (tmp_path, kind, name)

        results = []
        if attach_urls:
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="att") as ex:
                results = list(ex.map(_dl_one, attach_urls))
        for res in results:
            if not res:
                continue
            tmp_path, kind, name = res
            if self._is_zip(tmp_path):
                self.log("  解压 PDF…")
                amount_hint = f"{extract_amount_from_text(name):.2f}元" if extract_amount_from_text(name) else ""
                extracted = self._extract_pdfs_from_zip(tmp_path, tmp_dir, kind, date_str, amount_hint=amount_hint)
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                for p in extracted:
                    temp_pdfs.append((p, kind, os.path.basename(p)))
            else:
                temp_pdfs.append((tmp_path, kind, name))

        # ---- 阶段1.5：提取消费日期（行程单优先）+ 用 PDF 票面识别修正发票类型 ----
        rail_infos = {}  # tmp_path → railway_info（高铁 PDF 专用）
        pdf_kinds = {}  # tmp_path → kind_from_pdf 修正后的类型
        itinerary_dates = []  # 行程单票面日期（消费当天，最准）
        pdf_dates = {}  # tmp_path → 票面日期
        self.log(f"  临时文件 {len(temp_pdfs)} 个")
        for tmp_path, kind, orig_name in temp_pdfs:
            exists = os.path.exists(tmp_path)
            sz = os.path.getsize(tmp_path) if exists else 0
            self.log(f"  [{kind}] {orig_name} size={sz}")
            if tmp_path.lower().endswith(".pdf") and exists:
                pdf_text = self._pdf_text(tmp_path)
                # 票面类型识别（铁路/航空/通行费/餐饮等），覆盖邮件关键词粗判
                pdf_kind = kind_from_pdf(pdf_text) if pdf_text else ""
                if pdf_kind and pdf_kind != kind:
                    self.log(f"  票面识别: {kind} → {pdf_kind}（依据 PDF 项目名称）")
                    kind = pdf_kind
                    pdf_kinds[tmp_path] = pdf_kind
                d = ""
                # 高铁发票：优先从 PDF 提取乘车日期/票价（邮件正文常含订单号干扰）
                if "高铁" in kind or "铁路" in kind:
                    rw = self._railway_info_from_pdf(tmp_path)
                    if rw.get("date") or rw.get("amount"):
                        rail_infos[tmp_path] = rw
                        d = rw.get("date") or ""
                        self.log(f"  铁路客票PDF: 乘车={d or '?'} 票价={rw.get('amount') or 0}")
                if not d:
                    d = self._consume_date_from_file(tmp_path)
                    self.log(f"  票面日期 result={d!r}")
                if d:
                    pdf_dates[tmp_path] = d
                    if "行程单" in kind:
                        itinerary_dates.append(d)

        # 行程单优先：行程单票面日期 = 消费当天（发票开票日期可能不等于消费日）
        if itinerary_dates:
            shared_date = min(itinerary_dates)
            self.log(f"  消费日期: {shared_date}（行程单票面）")
        elif pdf_dates:
            shared_date = min(pdf_dates.values())
            self.log(f"  消费日期: {shared_date}（PDF 票面）")
        else:
            shared_date = date_str
            self.log(f"  警告 命名日期来自邮件日期: {date_str}，可能不是消费当天")
        self._append_pending_date_check(shared_date, date_str, temp_pdfs)

        # ---- 阶段2：用共享日期命名，移动到目标目录 ----
        mail_amounts = set()
        self.log(f"  保存 {len(temp_pdfs)} 个文件")
        for tmp_path, kind, orig_name in temp_pdfs:
            if self.stop_flag:
                break
            if not os.path.exists(tmp_path):
                self.log(f"  跳过不存在: {tmp_path}")
                continue
            kind = pdf_kinds.get(tmp_path, kind)  # 票面识别结果优先
            fname = build_filename(kind, orig_name, shared_date, msg=text, rw=rail_infos.get(tmp_path))
            dest = unique_path(self.save_dir, fname)
            self.log(f"  移动 {os.path.basename(tmp_path)} → {os.path.basename(dest)}")
            try:
                shutil.move(tmp_path, dest)
            except Exception as e:
                self.log(f"    移动失败: {e}")
                dest = tmp_path
            grant_current_user_access(dest)
            self.report_downloaded(dest)
            mail_amounts.add(extract_amount_from_text(os.path.basename(dest)))

        # 清理临时目录
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        return mail_amounts

    def _pdf_text(self, filepath):
        """读取 PDF 全文文本，失败返回空串。"""
        try:
            import pymupdf  # PyMuPDF
            doc = pymupdf.open(filepath)
            try:
                return "".join(page.get_text() or "" for page in doc)
            finally:
                doc.close()
        except Exception:
            return ""

    def _consume_date_from_file(self, filepath):
        """从本地 PDF 文件提取消费日期。"""
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            d = self._consume_date_from_pdf(data)
            if not d:
                self.log(f"    PyMuPDF未提取到日期: {os.path.basename(filepath)}")
            return d
        except Exception as e:
            self.log(f"    读取PDF失败: {str(e)[:60]}")
            return ""

    def _consume_date_from_pdf(self, data):
        """从 PDF 二进制内容提取消费日期（行程时间/通行时间/出行日期/上车时间）。

        使用 PyMuPDF 提取文本（轻量、无重依赖，避免 pdfplumber 引入 pandas 全家桶）。
        """
        if not data:
            return ""
        try:
            import pymupdf  # PyMuPDF
            doc = pymupdf.open(stream=data, filetype="pdf")
            try:
                for page in doc:
                    t = page.get_text() or ""
                    d = consume_date(t)
                    if d:
                        return d
            finally:
                doc.close()
        except Exception:
            pass
        return ""

    def _railway_info_from_pdf(self, filepath):
        """从铁路电子客票 PDF 提取乘车信息（乘车日期/开票日期/车次/路线/票价）。

        12306 铁路电子客票 PDF 有两种文本布局：
          A) 车次与日期相邻 + 票价同行：G258\n2026年08月06日 / 票价:￥87.00
          B) 车次与日期隔英文站名 + 票价分离：G901\nShanghaihongqiao\nHangzhouxi\n
             2026年08月09日 / 票价:\n...\n￥120.00（金额独立行）
        返回 dict：{date, issue_date, train, route, amount}，提取不到为空串/0。
        """
        info = {"date": "", "issue_date": "", "train": "", "route": "", "amount": 0.0}
        try:
            t = self._pdf_text(filepath)
            if not t:
                return info
            # 票价：优先「票价:￥87.00」同行；否则找独立「￥120.00」金额行
            m = re.search(r"票价[：:\s]*[¥￥]?\s*(\d+(?:\.\d+)?)", t)
            if not m:
                m = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", t)
            if m:
                try:
                    info["amount"] = float(m.group(1))
                except ValueError:
                    pass
            # 车次：任意 [A-Z]1-4 位数字（如 G258 / G901）
            m = re.search(r"([A-Z]\d{1,4})", t)
            if m:
                info["train"] = m.group(1)
            # 乘车日期：独立匹配「20xx年x月x日」，排除开票日期行
            # （先剔除开票日期段，避免把开票日期当乘车日期）
            rest = re.sub(r"开票日期[：:\s]*20\d{2}年\d{1,2}月\d{1,2}日", "", t)
            m = re.search(r"(20\d{2}年\d{1,2}月\d{1,2}日)", rest)
            if m:
                info["date"] = _cn_date_to_iso(m.group(1))
            # 开票日期
            m = re.search(r"开票日期[：:\s]*(20\d{2}年\d{1,2}月\d{1,2}日)", t)
            if m:
                info["issue_date"] = _cn_date_to_iso(m.group(1))
            # 路线：两个「xxx站」
            stations = re.findall(r"[\u4e00-\u9fa5]{2,6}站", t)
            if len(stations) >= 2:
                info["route"] = f"{stations[0]}-{stations[1]}"
        except Exception:
            pass
        return info

    def _rename_with_date(self, filepath, new_date):
        """用新日期重命名文件，返回新路径。"""
        try:
            base = os.path.basename(filepath)
            # 替换日期部分：8.13号_xxx → 新日期_xxx
            new_base = re.sub(r"^\d+\.\d+号", date_label(new_date), base)
            if new_base == base:
                return filepath
            new_path = unique_path(os.path.dirname(filepath), new_base)
            os.rename(filepath, new_path)
            return new_path
        except Exception:
            return filepath

    @staticmethod
    def _is_zip(path):
        """按文件头判断是否 ZIP（PK\x03\x04）。"""
        try:
            with open(path, "rb") as f:
                head = f.read(4)
            return head[:2] == b"PK" and head[2:4] in (b"\x03\x04", b"\x05\x06", b"\x07\x08")
        except Exception:
            return False

    def _extract_pdfs_from_zip(self, zip_path, dest_dir, kind, date_str, depth=0, amount_hint=None):
        """解压 zip，提取其中的 PDF（含嵌套 zip 递归）。返回保存的 PDF 路径列表。

        amount_hint: 外层附件名/主题提取的金额文本，用于给 zip 内无金额的 PDF 命名补价。
        """
        import zipfile
        out = []
        if depth > 3:
            return out
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if self.stop_flag:
                        break
                    low = info.filename.lower()
                    if info.is_dir():
                        continue
                    if low.endswith(".pdf"):
                        base = os.path.basename(info.filename) or "发票.pdf"
                        # 若 zip 内文件名无金额，用外层金额补
                        if amount_hint and not extract_amount_from_text(base):
                            base = f"{base} {amount_hint}"
                        fname = build_filename(kind, base, date_str)
                        dest = unique_path(dest_dir, fname)
                        with zf.open(info) as src, open(dest, "wb") as dst:
                            dst.write(src.read())
                        out.append(dest)
                    elif low.endswith(".zip"):
                        # 嵌套 zip：解到临时文件再递归
                        tmp = os.path.join(dest_dir, f"_nested_{depth}_{os.path.basename(info.filename)}")
                        with zf.open(info) as src, open(tmp, "wb") as dst:
                            dst.write(src.read())
                        out.extend(self._extract_pdfs_from_zip(tmp, dest_dir, kind, date_str, depth + 1, amount_hint))
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
        except Exception as e:
            self.log(f"    解压失败: {str(e)[:80]}")
        return out

    def _download_51fapiao_links(self, links, subject, date_str, text, sender=""):
        """下载 51fapiao 链接型发票。返回金额集合（从实际保存文件名提取，去重）。"""
        amounts = set()
        for u in links:
            if self.stop_flag:
                break
            kind = invoice_kind(subj=subject, body=text, sender=sender)
            fname = build_filename(kind, subject, date_str, msg=text)
            dest = unique_path(self.save_dir, fname)
            self.log(f"    51fapiao 下载 {os.path.basename(dest)}")
            if self._api.download_51fapiao(u, dest):
                self._last_51_ok = True
                self.report_downloaded(dest)
                amounts.add(extract_amount_from_text(os.path.basename(dest)))
            else:
                self.log(f"    51fapiao 下载失败 {u[:60]}")
        return amounts

    def _dump_51fapiao_context(self, content):
        """诊断：输出正文中 51fapiao 相关片段，用于定位链接截断/二维码场景。"""
        try:
            import io as _io
            buf = _io.StringIO()
            for m in re.finditer(r'.{80}51fapiao.{120}', content or "", re.S):
                buf.write(m.group(0).replace("\n", " ")[:220] + "\n")
            for m in re.finditer(r'.{60}dlj\.51fapiao.{100}', content or "", re.S):
                buf.write(m.group(0).replace("\n", " ")[:180] + "\n")
            snippet = buf.getvalue().strip()
            if snippet:
                self.log("    [诊断] 51fapiao 正文片段:")
                for line in snippet.split("\n")[:6]:
                    self.log(f"      {line}")
            else:
                self.log("    [诊断] 正文未找到 51fapiao 文本（可能是二维码图片承载）")
                qr = extract_qrcode_images(content)
                if qr:
                    self.log(f"    [诊断] 二维码图片 {len(qr)} 个:")
                    for u in qr[:5]:
                        self.log(f"      {u[:120]}")
        except Exception:
            pass

    def _download_alipay_links(self, links, subject, date_str, text, sender=""):
        """下载支付宝链接型发票。返回金额集合（从实际保存文件名提取，去重）。"""
        amounts = set()
        for u in links:
            if self.stop_flag:
                break
            kind = invoice_kind(subj=subject, body=text, sender=sender)
            fname = build_filename(kind, subject, date_str, msg=text)
            dest = unique_path(self.save_dir, fname)
            self.log(f"    支付宝发票下载 {os.path.basename(dest)}")
            if self._api.download_attach(u, dest):
                self.report_downloaded(dest)
                amounts.add(extract_amount_from_text(os.path.basename(dest)))
            else:
                self.log(f"    支付宝下载失败 {u[:60]}")
        return amounts

    def _download_oss_links(self, links, subject, date_str, text, sender=""):
        """下载阿里云 OSS 商家发票图片（淘宝闪购等平台手动上传的发票照片/扫描件）。

        图片可能是 jpg/png，保存时保留原扩展名。返回金额集合（从实际保存文件名提取）。
        """
        amounts = set()
        for u in links:
            if self.stop_flag:
                break
            kind = invoice_kind(subj=subject, body=text, sender=sender)
            ext = os.path.splitext(urllib.parse.urlparse(u).path)[1] or ".jpg"
            fname = build_filename(kind, subject, date_str, msg=text)
            base, _ = os.path.splitext(fname)
            dest = unique_path(self.save_dir, f"{base}{ext}")
            self.log(f"    OSS 商家发票下载 {os.path.basename(dest)}")
            if self._api.download_attach(u, dest):
                self.report_downloaded(dest)
                amounts.add(extract_amount_from_text(os.path.basename(dest)))
            else:
                self.log(f"    OSS 下载失败 {u[:60]}")
        return amounts

    def report_downloaded(self, path):
        with self._lock:
            if path and path not in self.downloaded_files:
                self.downloaded_files.append(path)
                self.downloaded_pdf_count += 1
                self.log(f"  已保存：{os.path.basename(path)}")
                self.on_progress(0, 0, self.downloaded_pdf_count)