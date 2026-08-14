"""IMAP 发票下载引擎。

通过标准 IMAP 协议直连 QQ 邮箱，搜索发票/行程单邮件并并行拉取 PDF 附件，
不依赖网页操作，速度远快于浏览器方案。

IMAP 账号：QQ邮箱需在网页版「设置 → 账户 → 开启 IMAP/SMTP 服务」生成授权码。
服务器：imap.qq.com:993（SSL），账号 = QQ号@qq.com，密码 = 授权码。
"""

import email
import imaplib
import io
import os
import re
import zipfile
from email.utils import parsedate_to_datetime

import requests

from app.engine import imap_utf7
from app.engine.downloader import (
    build_filename,
    date_label,
    normalize_date,
    parse_original_name,
    unique_path,
)
from app.engine.mail_parse import (
    alipay_filename,
    alipay_pdf_link,
    att_amount,
    body_text,
    decode_mime,
    extract_attachments,
    header_date,
    invoice_kind,
    is_railway,
    railway_info,
    subject_hash,
    text_date,
    text_keywords,
    ticket_amount,
)

IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993

SEARCH_TERMS = [
    ("FROM", "itinerary@ridesharing.amap.com"),
    ("FROM", "fapiao"),
    ("SUBJECT", "发票"),
    ("SUBJECT", "行程单"),
    ("SUBJECT", "电子"),
]


# ---------- IMAP 搜索辅助 ----------

def _search_criteria():
    """构建 IMAP 搜索条件：任意关键词命中即可。

    中文关键词由调用方通过 CHARSET UTF-8 传递（mail.search('UTF-8', criteria)），
    这里仅构造关键词结构。
    """
    ors = []
    for field, val in SEARCH_TERMS:
        ors.append(f'({field} "{val}")')
    if not ors:
        return "ALL"
    return " OR ".join(ors)


def _is_ascii(s):
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _utf7_encode(folder):
    """把文件夹名转为 IMAP UTF-7 编码（中文文件夹必需）。"""
    try:
        from imapclient import imap_utf7 as _m
        return _m.encode(folder)
    except ImportError:
        return imap_utf7.encode(folder)


def _utf7_decode(folder):
    """把 IMAP UTF-7 文件夹名解码回 unicode。"""
    try:
        from imapclient import imap_utf7 as _m
        return _m.decode(folder)
    except ImportError:
        return imap_utf7.decode(folder)


def _imap_search(mail, criteria):
    """执行搜索。

    纯 ASCII 条件（如主题哈希）直接用原样搜索，避免多余往返；
    含中文的条件用 UTF-8 字节编码搜索（imaplib 对 str 参数会做
    ASCII 编码而抛错），失败回退到剔除中文后的 ASCII 条件。
    """
    try:
        if not _is_ascii(criteria):
            try:
                r, data = mail.search("UTF-8", criteria.encode("utf-8"))
                if r == "OK":
                    return r, data
            except Exception:
                pass
            criteria = _ascii_criteria(criteria)
        r, data = mail.search(None, criteria)
        return r, data
    except Exception:
        return "NO", b""


def _ascii_criteria(criteria):
    """把含中文的关键词从条件里剔除，仅保留 ASCII 部分（保留原条件结构）。

    仅支持简单的 (FIELD "value") 或 OR 连接的简单条件；无法解析时
    回退到纯 ASCII 的通用发票搜索条件。
    """
    ascii_parts = []
    for field, val in re.findall(r'\((\w+)\s+"([^"]*)"\)', criteria):
        if _is_ascii(val):
            ascii_parts.append(f'({field} "{val}")')
    if ascii_parts:
        return " OR ".join(ascii_parts)
    # 全部含中文：退而求其次用发件人关键词兜底
    fallback = []
    for field, val in SEARCH_TERMS:
        if _is_ascii(val):
            fallback.append(f'({field} "{val}")')
    if fallback:
        return " OR ".join(fallback)
    return "ALL"


def _match_mail_msg(msg, time_str):
    """根据勾选邮件的列表时间（如 12:03）匹配 IMAP 邮件的 Date 头。

    返回 True 表示命中（Date 头时间与列表时间在同一天内基本一致）。
    """
    if not time_str:
        return True
    try:
        raw = msg.get("Date", "")
        dt = parsedate_to_datetime(raw)
        hm = f"{dt.hour:02d}:{dt.minute:02d}"
        return abs(int(hm.replace(':', '')) - int(time_str.replace(':', ''))) <= 5
    except Exception:
        return True


def _time_diff(msg, time_str):
    """返回 IMAP Date 时间与列表时间的分钟差（绝对值）。

    列表只显示时刻（HH:MM），无法跨天，因此仅比较当日时刻；
    优先匹配同一天且时刻接近的。无时间或"刚刚"则返回 9999（视为不匹配）。
    """
    if not time_str or "刚刚" in str(time_str) or "分钟" in str(time_str):
        return 9999
    try:
        raw = msg.get("Date", "")
        dt = parsedate_to_datetime(raw)
        list_min = int(time_str.replace(':', ''))
        msg_min = dt.hour * 100 + dt.minute
        diff_h = abs(dt.hour * 60 + dt.minute - (list_min // 100 * 60 + list_min % 100))
        return diff_h
    except Exception:
        return 9999


def _folder_priority(folder):
    """返回文件夹优先级：报销文件夹优先于收件箱。"""
    if "报销" in folder:
        return 0
    if folder == "INBOX":
        return 1
    return 2


def _rename_dir_with_amount(dir_path, amount):
    """在保存目录下新建子文件夹「总金额元」，把文件移入。返回子文件夹路径。"""
    sub = os.path.join(dir_path, f"{amount:.2f}元")
    try:
        os.makedirs(sub, exist_ok=True)
    except Exception:
        return dir_path
    return sub


class ImapEngine:
    """IMAP 发票下载引擎。"""

    def __init__(self, account, auth_code, on_log=None, on_progress=None):
        self.account = account
        self.auth_code = auth_code
        self.on_log = on_log or (lambda m: None)
        self.on_progress = on_progress or (lambda done, total, count: None)
        self.mail = None
        self.stop_flag = False
        self._cur_folder = None
        self.last_save_dir = None
        self._saved_files = []
        self._downloaded_ok = []  # 成功下载的 mailid 列表

    def log(self, msg):
        self.on_log(msg)

    # ---------- 连接 / 会话 ----------
    def connect(self):
        """连接 QQ 邮箱 IMAP。"""
        self.mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        self.mail.login(self.account, self.auth_code)
        return True

    def logout(self):
        try:
            if self.mail:
                self.mail.logout()
        except Exception:
            pass

    def list_folders(self):
        """列出所有文件夹名（解码回中文）。"""
        try:
            r, data = self.mail.list()
            folders = []
            for item in data:
                if not item:
                    continue
                line = item.decode("utf-8", "replace")
                # 形如 (\HasNoChildren) "/" "INBOX" 或 /xxx/yyy
                m = re.search(r'"([^"]+)"\s*$', line)
                if m:
                    name = m.group(1)
                    folders.append(_utf7_decode(name))
            return folders
        except Exception as e:
            self.log(f"  列文件夹失败: {str(e)[:40]}")
            return []

    def _target_folders(self):
        """返回可用的目标文件夹（模糊匹配报销/收件箱，报销优先）。"""
        all_folders = self.list_folders() or ["INBOX"]
        target = []
        for f in all_folders:
            if "报销" in f or f == "INBOX":
                target.append(f)
        if not target:
            for f in all_folders:
                if any(k in f for k in ("报销", "发票", "行程单", "税")):
                    target.append(f)
        if not target:
            target = ["INBOX"]
        # 报销文件夹优先
        target.sort(key=_folder_priority)
        return target

    def fetch_mail(self, num, folder):
        """按序号拉取一封邮件，返回 parsed email.message。

        若当前已选中同一文件夹则跳过 select（减少 IMAP 往返）。
        """
        if self._cur_folder != folder:
            self.mail.select(_utf7_encode(folder), readonly=True)
            self._cur_folder = folder
        r, data = self.mail.fetch(num, "(RFC822)")
        if r != "OK" or not data or data[0] is None:
            return None
        raw = data[0][1]
        return email.message_from_bytes(raw)

    def _fetch_many(self, nums, folder):
        """批量拉取多封邮件，返回 {num: parsed message}。

        用逗号分隔的编号一次 FETCH（减少 IMAP 往返），
        对返回的每段 RFC822 单独解析。
        """
        if not nums:
            return {}
        if self._cur_folder != folder:
            try:
                self.mail.select(_utf7_encode(folder), readonly=True)
                self._cur_folder = folder
            except Exception:
                return {}
        out = {}
        # 分块拉取，避免单次数据量过大
        CHUNK = 30
        for i in range(0, len(nums), CHUNK):
            if self.stop_flag:
                break
            chunk = nums[i:i + CHUNK]
            try:
                r, data = self.mail.fetch(",".join(chunk), "(RFC822)")
            except Exception:
                continue
            if r != "OK":
                continue
            for item in data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                meta = item[0]
                if not isinstance(meta, bytes):
                    continue
                try:
                    raw = item[1]
                    m = re.search(rb"\b(\d+)\b", meta)
                    num = m.group(1).decode()
                    out[num] = email.message_from_bytes(raw)
                except Exception:
                    continue
        return out

    # ---------- 搜索 ----------
    def search_invoice_mails(self, folders=("INBOX",)):
        """在指定文件夹中搜索发票/行程单邮件，返回 [(uid, folder)]。

        若未指定具体文件夹，自动探测：报销/收件箱（模糊匹配）。
        """
        found = []
        criteria = _search_criteria()

        if folders and folders != ("INBOX",):
            target = list(folders)
        else:
            target = self._target_folders()
        self.log(f"  目标文件夹: {target}")

        for folder in target:
            folder_utf7 = _utf7_encode(folder)
            try:
                r, _ = self.mail.select(folder_utf7, readonly=True)
            except Exception as e:
                self.log(f"  打开文件夹失败: {folder} ({str(e)[:40]})")
                continue
            if r != "OK":
                self.log(f"  文件夹不存在: {folder}")
                continue
            try:
                r, data = _imap_search(self.mail, criteria)
            except Exception as e:
                self.log(f"  搜索失败 {folder}: {str(e)[:40]}")
                continue
            if r != "OK":
                self.log(f"  搜索未成功 {folder}: {r}")
                continue
            for num in data[0].split():
                found.append((num.decode(), folder))
        return found

    # ---------- 下载 ----------
    def download_pdfs(self, save_dir):
        """下载所有发票邮件的 PDF 附件到 save_dir。返回下载文件列表。

        需先调用 connect() 登录。
        """
        os.makedirs(save_dir, exist_ok=True)
        self.log("搜索发票/行程单邮件…")

        mail_items = self.search_invoice_mails()
        total = len(mail_items)
        if total == 0:
            self.log("未找到符合条件的邮件。")
            self.logout()
            return []
        self.log(f"找到 {total} 封候选邮件，拉取附件…")

        downloaded = []
        pdf_count = 0
        for i, (num, folder) in enumerate(mail_items):
            if self.stop_flag:
                self.log("已手动停止。")
                break
            n = self._process_one(num, folder, save_dir)
            if n:
                downloaded.append(num)
            pdf_count += n
            if self.on_progress:
                self.on_progress(i + 1, total, pdf_count)

        self.logout()
        self.log(f"🏁 完成，共下载 {pdf_count} 个 PDF → {save_dir}")
        return downloaded

    def download_selected_pdfs(self, save_dir, selected_mails):
        """根据网页勾选的邮件（含发件人/主题/时间特征）匹配并下载其 PDF 附件。

        selected_mails: [{text, time, ...}] 来自网页 list_mails。
        匹配策略：提取勾选文本中的发件人域名关键词（itinerary/fapiao），
        IMAP 搜索该发件人 + 主题含「发票/行程单」，再用时间（HH:MM）二次筛选。
        需先调用 connect() 登录。
        """
        os.makedirs(save_dir, exist_ok=True)
        self._saved_files = []
        self.log("搜索匹配的发票邮件…")

        # 从勾选邮件提取发件人/主题特征
        features = []
        for m in selected_mails:
            text = m.get("text", "")
            time_str = m.get("time", "")
            feat = {"time": time_str, "text": text, "hash": subject_hash(text),
                    "mailid": m.get("mailid", "")}
            feat["keywords"] = text_keywords(text)
            # 提取勾选全文中的业务日期（如「2026年8月11日」→ 2026-08-11），
            # 用于在多个候选（如多封 12306）间精确区分
            feat["date"] = text_date(text)
            # 提取发件人域名关键词（显示名中可能带 email）
            dom = re.search(r"([a-zA-Z0-9]+@[a-zA-Z0-9.-]+)", text)
            if dom:
                feat["from"] = dom.group(1)
            else:
                for kw in ("itinerary", "fapiao", "12306"):
                    if kw.lower() in text.lower():
                        feat["from"] = kw + "@"
                        break
            features.append(feat)

        downloaded = []
        pdf_count = 0
        total_amount = 0.0  # 本次勾选邮件金额合计
        matched_nums = set()  # 已下载的邮件编号，避免重复
        total = len(features)
        # 缓存目标文件夹（仅列一次，避免每封邮件重复 list_folders）
        target_folders = self._target_folders()

        # 按搜索条件分组：同一 criteria 只搜索/拉取一次，再在本组内逐个匹配。
        # 把 search+fetch 的 IMAP 往返从「每封邮件两次」降到「每组两次」。
        groups = {}
        for i, feat in enumerate(features):
            criteria, verify = self._build_criteria(feat)
            groups.setdefault((criteria, verify[0]), []).append((i, feat, verify))
        cache = {}  # (folder, criteria) -> {num: msg}

        for (criteria, _vkind), members in groups.items():
            if not criteria:
                continue
            for folder in target_folders:
                if self.stop_flag:
                    break
                try:
                    if self._cur_folder != folder:
                        self.mail.select(_utf7_encode(folder), readonly=True)
                        self._cur_folder = folder
                except Exception:
                    continue
                r, data = _imap_search(self.mail, criteria)
                if r != "OK" or not data or not data[0]:
                    continue
                nums = [n.decode() for n in data[0].split()]
                if not nums:
                    continue
                # 批量拉取候选邮件（一次 fetch 多封，减少往返）
                msgs = self._fetch_many(nums, folder)
                if msgs:
                    cache[(folder, criteria)] = msgs

        for i, feat in enumerate(features):
            if self.stop_flag:
                self.log("已手动停止。")
                break
            self.log(f"▶ 匹配邮件: {feat['text'][:40]}")

            criteria, verify = self._build_criteria(feat)
            if not criteria:
                self.log(f"  ⚠ 未匹配到 {feat['text'][:30]}")
                if self.on_progress:
                    self.on_progress(i + 1, total, pdf_count)
                continue

            matched = 0
            best = None  # (key, num, folder, msg)
            for folder in target_folders:
                if self.stop_flag:
                    break
                pool = cache.get((folder, criteria))
                if not pool:
                    continue
                for num, msg in pool.items():
                    if num in matched_nums:
                        continue
                    if not self._verify_match(feat, msg, verify):
                        continue
                    # 优先级：勾选文本中的业务日期命中 > 当日时刻差
                    date_ok = 1
                    if feat.get("date"):
                        date_ok = 0 if (header_date(msg) == feat["date"]) else 1
                    diff = _time_diff(msg, feat.get("time", ""))
                    key = (date_ok, diff)
                    if best is None or key < best[0]:
                        best = (key, num, folder, msg)
                if best is not None:
                    break
            if best is not None:
                n = self._save_mail_pdfs(best[1], best[2], best[3], save_dir)
                pdf_count += n
                matched = 1
                matched_nums.add(best[1])
                downloaded.append(best[1])
                if feat.get("mailid"):
                    self._downloaded_ok.append(feat["mailid"])
                amt = att_amount(best[3])
                if amt:
                    total_amount += amt
                self.log(f"  ✓ 匹配 {best[2]}#{best[1]}（时间差 {best[0][1]} 分，PDF {n} 个"
                         + (f"，金额 {amt} 元" if amt else "") + "）")
            if not matched:
                self.log(f"  ⚠ 未匹配到 {feat['text'][:30]}")
            if self.on_progress:
                self.on_progress(i + 1, total, pdf_count)

        self.logout()
        self.log(f"🏁 完成，共下载 {pdf_count} 个 PDF → {save_dir}")
        self.last_save_dir = save_dir
        if total_amount and self._saved_files:
            self.log(f"  本次勾选合计金额：{total_amount:.2f} 元")
            sub = _rename_dir_with_amount(save_dir, total_amount)
            if sub != save_dir:
                moved = 0
                for f in self._saved_files:
                    try:
                        os.replace(f, os.path.join(sub, os.path.basename(f)))
                        moved += 1
                    except Exception:
                        pass
                self.log(f"  已新建文件夹「{os.path.basename(sub)}」，移入 {moved} 个 PDF")
        return downloaded

    def _process_one(self, num, folder, save_dir):
        """拉取一封邮件并保存其 PDF 附件。"""
        if self.stop_flag:
            return 0
        try:
            msg = self.fetch_mail(num, folder)
        except Exception as e:
            self.log(f"  拉取失败: {str(e)[:60]}")
            return 0
        if msg is None:
            return 0
        return self._save_mail_pdfs(num, folder, msg, save_dir)

    # ---------- 附件保存 ----------
    def _save_mail_pdfs(self, num, folder, msg, save_dir):
        """保存一封邮件的 PDF 附件，返回保存数量。

        支持三种形态：
          - 直连 .pdf 附件
          - .zip 压缩包（内含 .pdf/.ofd/.xml），仅提取包内 .pdf 保存
          - 无附件但正文含 alipayobjects.com PDF 直链（支付宝电子发票通知）：
            直接 requests 下载，无需扫码

        命名分类：
          - 高速费（主题含「通行费」）→ 高速发票
          - 行程单附件 → 行程单
          - 其余 → 发票
        """
        subj = decode_mime(msg.get("Subject", ""))
        n = 0
        date_str = normalize_date(header_date(msg))
        rw = railway_info(msg) if is_railway(subj) else {}
        atts = extract_attachments(msg)
        for fname, data in atts:
            if self.stop_flag:
                break
            low = fname.lower()
            if low.endswith(".pdf"):
                n += self._save_one_pdf(fname, data, date_str, save_dir, subj, msg, rw)
            elif low.endswith(".zip") and data[:4] == b"PK\x03\x04":
                try:
                    zf = zipfile.ZipFile(io.BytesIO(data))
                    inner = [i for i in zf.infolist()
                             if not i.is_dir() and i.filename.lower().endswith(".pdf")]
                    if not inner:
                        self.log(f"  ⏭ 压缩包内无 PDF: {fname}")
                        continue
                    for i in inner:
                        if self.stop_flag:
                            break
                        n += self._save_one_pdf(fname, zf.read(i), date_str, save_dir, subj, msg, rw)
                except Exception as e:
                    self.log(f"  ⚠ 解压失败 {fname}: {str(e)[:40]}")
        if n == 0 and not self.stop_flag:
            n += self._save_alipay_pdf(msg, save_dir, subj)
        return n

    def _save_one_pdf(self, att_name, data, date_str, save_dir, subj="", msg=None, rw=None):
        """按发票内容自动判断类型并命名保存单个 PDF，返回 1。

        类型判断来源：附件名 + 邮件主题 + 邮件正文（综合 invoice_kind）。
        """
        body = ""
        if msg is not None:
            body = body_text(msg)
        kind = invoice_kind(subj=subj, att_name=att_name, body=body)
        dest_name = build_filename(kind, att_name, date_str, msg=msg, rw=rw)
        dest = unique_path(save_dir, dest_name)
        with open(dest, "wb") as f:
            f.write(data)
        self._saved_files.append(dest)
        self.log(f"  ✓ 已保存：{os.path.basename(dest)} ({len(data)}B)")
        return 1

    def _save_alipay_pdf(self, msg, save_dir, subj=""):
        """支付宝电子发票通知：正文含 alipayobjects.com PDF 直链，直接下载。

        链接形如：
          https://mdn.alipayobjects.com/aliinvoicecore/uri/file/.../invoice_xxx.pdf
          ?af_fileName=63.30元-上海青浦区孙叶饮食店（个体工商户）-2026年08月07日-电子发票.pdf
        公开 CDN 无需扫码。从 URL 参数 af_fileName 提取规范文件名。
        返回 1 表示已下载。
        """
        link = alipay_pdf_link(msg)
        if not link:
            return 0
        try:
            r = requests.get(link, timeout=60, stream=True)
            r.raise_for_status()
            data = r.content
            if not data or data[:5] != b"%PDF-":
                self.log(f"  ⚠ 支付宝链接非 PDF 内容")
                return 0
            # 文件名优先取 URL af_fileName 参数（含金额/商家/日期）
            default = f"支付宝电子发票_{header_date(msg)}.pdf"
            fn = alipay_filename(link, default)
            date_str = normalize_date(header_date(msg))
            body = body_text(msg)
            kind = invoice_kind(subj=subj, att_name=fn, body=body)
            dest_name = build_filename(kind, fn, date_str, msg=msg)
            dest = unique_path(save_dir, dest_name)
            with open(dest, "wb") as f:
                f.write(data)
            self._saved_files.append(dest)
            self.log(f"  ✓ 已保存：{os.path.basename(dest)} ({len(data)}B, 支付宝直链)")
            return 1
        except Exception as e:
            self.log(f"  ⚠ 支付宝 PDF 下载失败: {str(e)[:60]}")
            return 0

    # ---------- 勾选匹配 ----------
    def _build_criteria(self, feat):
        """根据勾选特征构造 IMAP 搜索条件，返回 (criteria, verify)。

        匹配策略分级：
          1) 主题哈希 → SUBJECT 精确命中
          2) 已知发件人 → FROM 搜索（12306 / itinerary / fapiao / alipay）
          3) 未知发件人 → 用中文关键词做 SUBJECT 搜索
        verify = (kind, value)，供 _verify_match 二次校验。
        """
        if feat.get("hash"):
            return f'(SUBJECT "{feat["hash"]}")', ("hash", None)
        if feat.get("from"):
            from_kw = feat["from"].split("@")[0]
            return f'(FROM "{from_kw}")', ("from", from_kw)
        # 支付宝提醒（无邮箱域名，但正文含「支付宝」或「电子发票」+ 商家名）
        text = feat.get("text", "")
        if ("支付宝" in text or "饮食店" in text or "个体工商户" in text
                or "收到" in text and "电子发票" in text):
            return '(FROM "alipay")', ("from", "alipay")
        if feat.get("keywords"):
            kw = feat["keywords"][0]
            return f'(SUBJECT "{kw}")', ("subject", kw)
        return None, (None, None)

    def _verify_match(self, feat, msg, verify):
        """校验 IMAP 搜索结果是否真的对应勾选的邮件。

        verify = (kind, value)：
          - hash    : SUBJECT 已精确命中哈希，直接通过
          - from    : 邮件 From 必须包含发件人关键词
          - subject : 邮件 Subject 必须与勾选文本关键词有交集
        返回 True 表示可下载。
        """
        if verify is None:
            return True
        kind, val = verify
        if kind == "hash":
            return True
        if kind == "from":
            from_hdr = decode_mime(msg.get("From", ""))
            return val.lower() in from_hdr.lower()
        if kind == "subject":
            subj = decode_mime(msg.get("Subject", ""))
            # 主题与勾选文本的关键词之一有交集（去掉纯数字哈希后比较）
            for kw in feat.get("keywords", []):
                if kw in subj:
                    return True
            # 兜底：勾选文本里出现的公司名出现在主题中
            text = feat.get("text", "")
            for m in re.finditer(r"【([^】]+)】", text):
                if m.group(1) in subj:
                    return True
            return False
        return True