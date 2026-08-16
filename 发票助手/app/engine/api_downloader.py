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
import time
import urllib.parse

import requests

from app import config
from app.engine.mail_parse import invoice_kind, ticket_amount, consume_date
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


def normalize_date(date_str):
    try:
        y, mo, d = date_str.split("-")
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    except Exception:
        return ""


def date_label(date_str):
    """把 2026-08-06 简化为 8.6号。"""
    try:
        parts = date_str.split("-")
        return f"{int(parts[1])}.{int(parts[2])}号"
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
            date_part = _month_date_label(rw.get("issue_date") or rw.get("date") or date_str)
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
        return float(val)
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


def extract_invoice_no(subject):
    """从主题提取发票号码（如 发票号码:11901140 / 发票号码：2631...）。"""
    m = re.search(r"发票号码[:：]\s*(\d{8,25})", subject or "")
    if m:
        return m.group(1)
    return ""


# ---------- API 客户端 ----------

class QQMailApi:
    """封装 QQ 邮箱网页接口（纯 requests，不依赖网页 UI）。"""

    BASE = "https://wx.mail.qq.com"

    def __init__(self, cookies, sid, on_log=None):
        self.cookies = cookies          # requests CookieJar
        self.sid = sid or ""
        self.on_log = on_log or (lambda m: None)
        self.session = requests.Session()
        self.session.cookies = cookies
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
            "Referer": "https://wx.mail.qq.com/home/index",
        })

    def _url(self, path):
        sep = "&" if "?" in path else "?"
        return f"{self.BASE}{path}{sep}sid={urllib.parse.quote(self.sid)}&r={int(time.time()*1000)}"

    def fetch_maillist(self, page_now=0, page_size=50, dirid=1):
        """拉取一页邮件列表。返回 list（可能为空）。"""
        url = self._url(
            f"/list/maillist?dir=1&dirid={dirid}&func=1&sort_type=1&sort_direction=1"
            f"&page_now={page_now}&page_size={page_size}&enable_topmail=true")
        r = self.session.get(url, timeout=20)
        r.raise_for_status()
        j = r.json()
        head = j.get("head", {})
        if head.get("ret") != 0:
            self.on_log(f"⚠ maillist ret={head.get('ret')} msg={head.get('msg','')[:60]}")
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
        url = self._url("/read/readmail")
        r = self.session.post(url, data={"mailid": mailid, "func": func}, timeout=20)
        r.raise_for_status()
        j = r.json()
        head = j.get("head", {})
        if head.get("ret") != 0:
            self.on_log(f"⚠ readmail ret={head.get('ret')} msg={head.get('msg','')[:60]}")
            return {}
        item = j.get("body", {}).get("item", {}) or {}
        if str(item.get("ret", "0")) != "0":
            self.on_log(f"⚠ readmail item.ret={item.get('ret')}")
            return {}
        return item

    def download_attach(self, download_url, dest_path):
        """附件型：download_url 形如 /attach/download?mailid=..&fileid=..&name=.."""
        url = download_url
        if url.startswith("/"):
            url = self._url(url)
        return self._download(url, dest_path)

    def download_fapiao(self, no, code, name, dest_path):
        """已知发票号码：/fapiao/download?fapiao_list={"fapiao":[{"no","code"}]}"""
        fapiao_list = urllib.parse.quote(json.dumps({"fapiao": [{"no": no, "code": code or ""}]}))
        url = self._url(f"/fapiao/download?fapiao_list={fapiao_list}&name={urllib.parse.quote(name)}")
        return self._download(url, dest_path)

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
            self.on_log(f"    51fapiao 直接下载失败，回退 sig 流程: signatureString={'✓' if sig else '✗(空)'} dlj={dlj}")
            if not sig:
                return False
            perm_url = f"{base}/dlj/v7/checkDownloadPermissions/getFile/{dlj}/{sig}"
            r2 = requests.get(perm_url, headers=h, timeout=15)
            if r2.text.strip() != "success":
                self.on_log(f"⚠ 51fapiao 权限检查失败: {r2.text[:80]}")
                return False
            dl_url = f"{base}/dlj/v7/downloadFile/{dlj}?signatureString={sig}"
            return self._download(dl_url, dest_path, extra_headers=h)
        except Exception as e:
            self.on_log(f"⚠ 51fapiao 下载异常: {str(e)[:80]}")
            return False

    def _download(self, url, dest_path, extra_headers=None):
        """下载 URL 内容到 dest_path。返回 True/False。"""
        try:
            headers = dict(self.session.headers)
            if extra_headers:
                headers.update(extra_headers)
            with self.session.get(url, headers=headers, stream=True, timeout=60) as r:
                r.raise_for_status()
                os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
            return os.path.exists(dest_path) and os.path.getsize(dest_path) > 0
        except Exception as e:
            self.on_log(f"⚠ 下载失败 {os.path.basename(dest_path)}: {str(e)[:80]}")
            return False


# ---------- 控制器 ----------

class ApiDownloadController:
    """纯 API 下载控制器：勾选 -> 拉列表 -> 分类下载。"""

    def __init__(self, client: WebClient, save_dir, on_log=None, on_progress=None):
        self.client = client
        self.save_dir = save_dir
        self.on_log = on_log or (lambda m: None)
        self.on_progress = on_progress or (lambda p, t, d: None)

        self.downloaded_files = []
        self.downloaded_pdf_count = 0
        self.stop_flag = False
        self._last_51_ok = False
        self._total_amount = 0.0

    def log(self, msg):
        self.on_log(msg)

    def _build_api(self, sid=None):
        """从 WebClient 的 cookie store 构建 QQMailApi。sid 优先用外部传入（GUI 线程已取好）。"""
        jar = self.client.cookies.get_cookie_jar("https://wx.mail.qq.com/")
        if not sid:
            sid = self.client.cookies.get_cookie_value("xm_sid") or ""
        return QQMailApi(jar, sid, on_log=self.log)

    def run(self, selected_mails, sid=None):
        """selected_mails: 用户勾选的邮件 [{mailid, subject, sender, text}]
        sid: 可选，GUI 线程已取好的会话 sid。"""
        os.makedirs(self.save_dir, exist_ok=True)
        if not selected_mails:
            self.log("没有检测到勾选的邮件。请先在左侧网页中勾选需要下载的邮件。")
            return []
        self.prepare(selected_mails, sid=sid)
        while self.process_next():
            pass
        self.log(f"🏁 全部完成，共下载 {len(self.downloaded_files)} 个 PDF → {self.save_dir}")
        self._archive_by_amount()
        return self.downloaded_files

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
                self.downloaded_files[self.downloaded_files.index(f)] = os.path.join(sub, os.path.basename(f))
            except Exception:
                pass
        self.log(f"  已新建文件夹「{os.path.basename(sub)}」，移入 {moved} 个 PDF")

    def prepare(self, selected_mails, sid=None):
        """初始化处理队列。必须在 GUI 线程调用（读取 cookie）。sid 可选（GUI 线程已取好）。"""
        os.makedirs(self.save_dir, exist_ok=True)
        self._mails = selected_mails
        self._i = 0
        self._total = len(selected_mails)
        self._api = self._build_api(sid=sid)
        # 预拉全量 maillist，建立 emailid → item 映射（含附件 download_url）
        self._mail_cache = {}
        try:
            self.log("拉取邮件列表建立附件映射…")
            self._mail_cache = self._api.fetch_maillist_all(max_pages=40)
            self.log(f"  maillist 缓存 {len(self._mail_cache)} 封")
        except Exception as e:
            self.log(f"  ⚠ 拉取邮件列表失败: {str(e)[:60]}（将逐封查询）")
        self.log(f"检测到勾选 {self._total} 封邮件，开始处理…")

    def process_next(self):
        """在 GUI 线程处理下一封邮件。返回 True 表示还有更多待处理。"""
        if self.stop_flag:
            self.log("已手动停止。")
            return False
        if self._i >= self._total:
            return False
        i = self._i
        m = self._mails[i]
        self._i += 1
        self._process_one(m, i)
        return self._i < self._total

    def _process_one(self, m, processed):
        """处理单封邮件：按类型下载。若本封未成功下载任何文件，建失败标记文件夹。"""
        mailid = m.get("mailid", "")
        subject = m.get("subject", "") or m.get("text", "")[:60]
        text = m.get("text", "") or subject
        sender = m.get("sender", "") or m.get("from", "") or ""

        if not is_invoice_mail(text):
            self.log(f"⏭ 跳过非发票邮件: {subject[:40]}")
            return

        before = self.downloaded_pdf_count
        date_str = ""
        self.log(f"▶ 处理发票邮件: {subject[:50]}…")
        try:
            # 1) 先拉详情（readmail func=1 拿正文 + 附件信息）
            item = self._api.fetch_readmail(mailid, func=1)
            if not item:
                self.log("    详情获取失败")
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
            if consume:
                self.log(f"    📅 消费日期: {consume} (邮件日期: {email_date})")
            else:
                self.log(f"    📅 邮件日期: {email_date} (未提取到消费日期)")

            # 2) 附件型：readmail 返回里没有附件，需从 maillist 拿
            #    先尝试从 maillist 缓存/拉取附件信息
            attach_urls = self._get_attach_urls(mailid)
            if attach_urls:
                mail_amounts = self._download_attaches(attach_urls, subject, date_str, text, sender)
                if self.downloaded_pdf_count > before:
                    # 按金额去重累加（发票+行程单同金额只算一次）
                    self._total_amount += sum(mail_amounts)
                self.on_progress(processed + 1, self._total, self.downloaded_pdf_count)
                return

            # 3) 链接型：51fapiao / alipay
            dlj_links = extract_51fapiao_links(content)
            if dlj_links:
                amounts = self._download_51fapiao_links(dlj_links, subject, date_str, text, sender)
                # 诊断：下载失败时输出正文中 51fapiao 相关片段，便于定位链接截断
                if self.downloaded_pdf_count == 0 and not self._last_51_ok:
                    self._dump_51fapiao_context(content)
                if self.downloaded_pdf_count > before:
                    self._total_amount += sum(amounts)
                self.on_progress(processed + 1, self._total, self.downloaded_pdf_count)
                return

            alipay_links = extract_alipay_links(content)
            if alipay_links:
                amounts = self._download_alipay_links(alipay_links, subject, date_str, text, sender)
                if self.downloaded_pdf_count > before:
                    self._total_amount += sum(amounts)
                self.on_progress(processed + 1, self._total, self.downloaded_pdf_count)
                return

            # 4) 已知发票号码：fapiao/download
            no = extract_invoice_no(subject)
            if no:
                self.log(f"    尝试按发票号码下载: {no}")
                kind = invoice_kind(subj=subject, body=text, sender=sender)
                fname = build_filename(kind, subject, date_str, msg=text)
                dest = unique_path(self.save_dir, fname)
                if self._api.download_fapiao(no, "", fname, dest):
                    self.report_downloaded(dest)
                self.on_progress(processed + 1, self._total, self.downloaded_pdf_count)
                return

            self.log("    未找到附件/链接/发票号码，跳过")
        except Exception as e:
            self.log(f"    ✗ 处理异常: {str(e)[:80]}")
        finally:
            # 本封邮件未成功下载任何文件 → 建失败标记文件夹（文件夹名承载邮件信息）
            if self.downloaded_pdf_count == before:
                self._mark_failed(subject, date_str, sender)
            self.on_progress(processed + 1, self._total, self.downloaded_pdf_count)

    def _mark_failed(self, subject, date_str, sender):
        """下载失败：建一个空文件夹，文件夹名 = 邮件简短信息。"""
        try:
            folder = unique_dir(self.save_dir, failed_folder_name(subject, date_str, sender))
            os.makedirs(folder, exist_ok=True)
            self.log(f"    ⚠ 未下载成功，已建标记文件夹：{os.path.basename(folder)}")
        except Exception as e:
            self.log(f"    ⚠ 建失败标记文件夹异常: {str(e)[:60]}")

    def _get_attach_urls(self, mailid):
        """从 maillist 缓存获取邮件的附件下载 URL 列表。"""
        it = self._mail_cache.get(mailid)
        if it:
            return [a.get("download_url", "") for a in it.get("normal_attach", []) or [] if a.get("download_url")]
        # 缓存未命中：逐页查询
        try:
            for page in range(5):
                lst = self._api.fetch_maillist(page_now=page, page_size=50)
                for it in lst:
                    if (it.get("emailid") or it.get("mailid")) == mailid:
                        return [a.get("download_url", "") for a in it.get("normal_attach", []) or [] if a.get("download_url")]
                if len(lst) < 50:
                    break
        except Exception as e:
            self.log(f"    ⚠ 拉取附件信息失败: {str(e)[:60]}")
        return []

    def _download_attaches(self, attach_urls, subject, date_str, text, sender=""):
        """下载附件型发票（PDF/zip）。zip 附件解压后提取 PDF。

        两阶段策略：
        1. 先下载所有 PDF 到临时目录，提取消费日期，确定共享日期
        2. 再用共享日期统一命名保存

        这样行程单先提取到消费日期后，发票也能用正确的日期命名。
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
            self.log(f"    🔗 去重: {len(attach_urls)} → {len(unique_urls)} 个附件")
        attach_urls = unique_urls

        # ---- 阶段1：下载到临时目录，提取消费日期 ----
        tmp_dir = tempfile.mkdtemp(prefix="fapiao_")
        temp_pdfs = []  # [(临时路径, kind, 原始文件名)]
        shared_date = date_str
        self.log(f"    🔗 附件URL {len(attach_urls)} 个")

        for u in attach_urls:
            if self.stop_flag:
                break
            name = ""
            m = re.search(r"name=([^&]+)", u)
            if m:
                try:
                    name = urllib.parse.unquote(m.group(1))
                except Exception:
                    name = m.group(1)
            suffix = os.path.splitext(name)[1].lower() if name else ".pdf"
            if suffix not in (".pdf", ".zip"):
                continue
            kind = invoice_kind(subj=subject, att_name=name, body=text, sender=sender)
            tmp_path = os.path.join(tmp_dir, name or f"tmp_{len(temp_pdfs)}.pdf")
            self.log(f"    ↓ 下载 {name or '附件'}…")
            if not self._api.download_attach(u, tmp_path):
                self.log(f"    ✗ 下载失败 {name}")
                continue
            # 验证下载结果
            dl_ok = os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0
            self.log(f"    ↓ 写入 {os.path.basename(tmp_path)} ok={dl_ok} size={os.path.getsize(tmp_path) if dl_ok else 0}")
            if self._is_zip(tmp_path):
                self.log(f"    ↦ 解压 PDF…")
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

        # ---- 阶段1.5：提取消费日期（取最早的作为真实消费日期）----
        self.log(f"    📋 临时文件 {len(temp_pdfs)} 个")
        for tmp_path, kind, orig_name in temp_pdfs:
            exists = os.path.exists(tmp_path)
            sz = os.path.getsize(tmp_path) if exists else 0
            self.log(f"    🔍 [{kind}] {orig_name} → {tmp_path} exists={exists} size={sz}")
            if tmp_path.lower().endswith(".pdf") and exists:
                d = self._consume_date_from_file(tmp_path)
                self.log(f"    🔍 consume_date result={d!r}")
                if d:
                    # 消费日期通常 ≤ 开票日期，取最早的
                    if not shared_date or d < shared_date:
                        shared_date = d

        if shared_date != date_str:
            self.log(f"    📅 共享消费日期: {shared_date} (原邮件日期: {date_str})")
        else:
            self.log(f"    📅 使用邮件日期: {date_str}")

        # ---- 阶段2：用共享日期命名，移动到目标目录 ----
        mail_amounts = set()
        self.log(f"    💾 保存循环 {len(temp_pdfs)} 个文件")
        for tmp_path, kind, orig_name in temp_pdfs:
            if self.stop_flag:
                break
            if not os.path.exists(tmp_path):
                self.log(f"    💾 跳过不存在: {tmp_path}")
                continue
            fname = build_filename(kind, orig_name, shared_date, msg=text)
            dest = unique_path(self.save_dir, fname)
            self.log(f"    💾 移动 {os.path.basename(tmp_path)} → {os.path.basename(dest)}")
            try:
                shutil.move(tmp_path, dest)
            except Exception as e:
                self.log(f"    💾 移动失败: {e}")
                dest = tmp_path
            self.log(f"    ✓ 已保存：{os.path.basename(dest)}")
            self.report_downloaded(dest)
            mail_amounts.add(extract_amount_from_text(os.path.basename(dest)))

        # 清理临时目录
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        return mail_amounts

    def _consume_date_from_file(self, filepath):
        """从本地 PDF 文件提取消费日期。"""
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            d = self._consume_date_from_pdf(data)
            if not d:
                self.log(f"    📅 pdfplumber未提取到日期: {os.path.basename(filepath)}")
            return d
        except Exception as e:
            self.log(f"    📅 读取PDF失败: {str(e)[:60]}")
            return ""

    def _consume_date_from_pdf(self, data):
        """从 PDF 二进制内容提取消费日期（行程时间/通行时间/出行日期/上车时间）。"""
        if not data:
            return ""
        try:
            import io as _io
            import pdfplumber
            with pdfplumber.open(_io.BytesIO(data)) as pdf:
                for pg in pdf.pages:
                    t = pg.extract_text() or ""
                    d = consume_date(t)
                    if d:
                        return d
        except Exception:
            pass
        return ""

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
            self.log(f"    ⚠ 解压失败: {str(e)[:80]}")
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
            self.log(f"    ↓ 51fapiao 下载 {os.path.basename(dest)}")
            if self._api.download_51fapiao(u, dest):
                self._last_51_ok = True
                self.report_downloaded(dest)
                amounts.add(extract_amount_from_text(os.path.basename(dest)))
            else:
                self.log(f"    ✗ 51fapiao 下载失败 {u[:60]}")
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
            self.log(f"    ↓ 支付宝发票下载 {os.path.basename(dest)}")
            if self._api.download_attach(u, dest):
                self.report_downloaded(dest)
                amounts.add(extract_amount_from_text(os.path.basename(dest)))
            else:
                self.log(f"    ✗ 支付宝下载失败 {u[:60]}")
        return amounts

    def report_downloaded(self, path):
        if path and path not in self.downloaded_files:
            self.downloaded_files.append(path)
            self.downloaded_pdf_count += 1
            self.log(f"    ✓ 已保存：{os.path.basename(path)}")
            self.on_progress(0, 0, self.downloaded_pdf_count)