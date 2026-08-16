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
    consume_date,
)

IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993

# 临时诊断：匹配失败时打印每个候选的校验细节（跑完问题定位后可关）
_DEBUG_MATCH = False

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


def _imap_search(mail, criteria, uid=False):
    """执行搜索。

    纯 ASCII 条件（如主题哈希）直接用原样搜索，避免多余往返；
    含中文的条件用 UTF-8 字节编码搜索（imaplib 对 str 参数会做
    ASCII 编码而抛错），失败回退到剔除中文后的 ASCII 条件。
    uid=True 时用 UID SEARCH，返回 UID（文件夹内永久稳定）。
    """
    try:
        if not _is_ascii(criteria):
            try:
                r, data = mail.uid("search", "UTF-8", criteria.encode("utf-8")) if uid \
                    else mail.search("UTF-8", criteria.encode("utf-8"))
                if r == "OK":
                    return r, data
            except Exception:
                pass
            criteria = _ascii_criteria(criteria)
        r, data = mail.uid("search", None, criteria) if uid else mail.search(None, criteria)
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


def _mail_message_id(msg):
    """提取邮件的 Message-ID 头（去尖括号，无则空串）。"""
    raw = msg.get("Message-ID", "")
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1]
    return raw


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

    列表显示可能是绝对时刻（HH:MM）或相对时间（昨天 11:59 / 前天 HH:MM /
    刚刚 / N分钟前）。相对时间按今天的日期偏移换算成绝对日期再比较。
    无法解析返回 9999（视为不匹配）。
    """
    if not time_str:
        return 9999
    ts = str(time_str)
    try:
        raw = msg.get("Date", "")
        dt = parsedate_to_datetime(raw)
    except Exception:
        return 9999
    # 解析列表显示的时间
    m = re.search(r"(\d{1,2}):(\d{2})", ts)
    if not m:
        # 无时刻（"刚刚"/"昨天"无具体时间）：无法比较
        return 9999
    list_h, list_m = int(m.group(1)), int(m.group(2))
    list_min_total = list_h * 60 + list_m
    # 相对日期偏移（天）：今天 0，昨天 -1，前天 -2
    day_offset = 0
    if "昨天" in ts:
        day_offset = -1
    elif "前天" in ts:
        day_offset = -2
    try:
        import datetime as _dt
        list_date = _dt.date.today() + _dt.timedelta(days=day_offset)
        msg_date = dt.date()
        day_gap = abs((msg_date - list_date).days)
        # 日期差折算成分钟 + 时刻差
        diff = day_gap * 24 * 60
        diff += abs((dt.hour * 60 + dt.minute) - list_min_total)
        return diff
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
        self.matched_msgids = []  # 本次匹配成功邮件的 Message-ID 列表

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
        """按 UID 拉取一封邮件，返回 parsed email.message。

        num 是 build_msgid_index 记录的 UID（文件夹内永久稳定，不随新邮件漂移）。
        若当前已选中同一文件夹则跳过 select（减少 IMAP 往返）。
        """
        if self._cur_folder != folder:
            self.mail.select(_utf7_encode(folder), readonly=True)
            self._cur_folder = folder
        r, data = self.mail.uid("fetch", num, "(RFC822)")
        if r != "OK" or not data or data[0] is None:
            return None
        raw = data[0][1]
        return email.message_from_bytes(raw)

    def _fetch_many(self, nums, folder):
        """批量拉取多封邮件，返回 {num: parsed message}。

        nums 为 UID 列表；用逗号分隔的 UID 一次 UID FETCH（减少 IMAP 往返），
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
                r, data = self.mail.uid("fetch", ",".join(chunk), "(RFC822)")
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
                    m = re.search(rb"UID\s+(\d+)", meta, re.I)
                    num = m.group(1).decode() if m else ""
                    if num:
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
                r, data = _imap_search(self.mail, criteria, uid=True)
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

    def build_msgid_index(self, folders=None):
        """一次性抓取目标文件夹所有发票候选邮件的头部，建立 Message-ID 索引。

        只拉 HEADER（Message-ID/Subject/From/Date，BODY.PEEK 不改变已读标记），
        返回 [{num, folder, message_id, subject, from_addr, date, date_raw}]。
        之后勾选邮件在该索引上离线匹配，命中条目后按 Message-ID 锚定下载。
        """
        self.log("建立邮件索引（拉取候选邮件头部）…")
        target = list(folders) if folders else self._target_folders()
        self.log(f"  目标文件夹: {target}")
        criteria = _search_criteria()
        index = []
        for folder in target:
            folder_utf7 = _utf7_encode(folder)
            try:
                r, _ = self.mail.select(folder_utf7, readonly=True)
            except Exception:
                continue
            if r != "OK":
                continue
            r, data = _imap_search(self.mail, criteria, uid=True)
            if r != "OK" or not data or not data[0]:
                continue
            nums = [n.decode() for n in data[0].split()]
            if not nums:
                continue
            CHUNK = 50
            for i in range(0, len(nums), CHUNK):
                if self.stop_flag:
                    break
                chunk = nums[i:i + CHUNK]
                try:
                    r, data = self.mail.uid(
                        "fetch",
                        ",".join(chunk),
                        "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM MESSAGE-ID DATE X-QQ-MID)])",
                    )
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
                        m = re.search(rb"UID\s+(\d+)", meta, re.I)
                        uid = m.group(1).decode() if m else ""
                        hdr = email.message_from_bytes(item[1])
                        index.append({
                            "num": uid,  # 用 UID 作为稳定标识（不再用会漂移的序号）
                            "uid": uid,
                            "folder": folder,
                            "message_id": _mail_message_id(hdr),
                            # QQ 内部邮件 ID（X-QQ-mid），与网页 maillist 的 messageid 一致，
                            # 是 100% 精确匹配的关键
                            "xqq_mid": (hdr.get("X-QQ-mid", "") or "").strip(),
                            "subject": decode_mime(hdr.get("Subject", "")),
                            "from_addr": decode_mime(hdr.get("From", "")),
                            "date": header_date(hdr),
                            "date_raw": hdr.get("Date", "") or "",
                        })
                    except Exception:
                        continue
        self.log(f"  索引建立完成：{len(index)} 封候选邮件")
        return index

    def _header_msg(self, e):
        """由索引条目构造轻量 email.message（仅 From/Subject/Date/Message-ID），
        供 _verify_match / _time_diff / header_date 复用。"""
        m = email.message.Message()
        if e.get("from_addr"):
            m["From"] = e["from_addr"]
        if e.get("subject"):
            m["Subject"] = e["subject"]
        if e.get("date_raw"):
            m["Date"] = e["date_raw"]
        if e.get("message_id"):
            m["Message-ID"] = f"<{e['message_id']}>"
        return m

    def _index_match(self, feat, index, matched_nums, matched_msgids):
        """在 Message-ID 索引中离线匹配勾选邮件，返回最优条目或 None。

        匹配优先级与 _build_criteria 一致：
          - message_id：索引中 Message-ID 精确相等
          - hash      ：索引主题包含该哈希
          - from      ：发件人地址包含 + 主题相似度校验
          - subject   ：主题包含关键词
        候选排序：业务日期命中 > 时刻差。已下载的编号/Message-ID 跳过。
        """
        criteria, verify = self._build_criteria(feat)
        if not criteria and verify[0] != "xqq_mid":
            return None
        kind, val = verify
        best = None  # (key, entry)
        for e in index:
            num, folder = e["num"], e["folder"]
            if (num, folder) in matched_nums:
                continue
            if kind == "xqq_mid":
                # QQ 内部邮件 ID 与网页 maillist messageid 精确一致 → 100% 唯一命中
                ok = val == (e.get("xqq_mid") or "").strip()
            elif kind == "message_id":
                emid = (e.get("message_id") or "").strip()
                if emid.startswith("<") and emid.endswith(">"):
                    emid = emid[1:-1]
                ok = val == emid
            elif kind == "hash":
                h = str(feat.get("hash") or "").strip()
                ok = bool(h) and h in (e["subject"] or "")
            else:
                hm = self._header_msg(e)
                ok = self._verify_match(feat, hm, verify)
                if not ok and _DEBUG_MATCH:
                    self.log(
                        f"    [dbg] {folder}#{num} verify={ok} "
                        f"from={e.get('from_addr','')!r} subj={e.get('subject','')!r}"
                        f"  feat.subject={feat.get('subject','')!r}"
                        f"  feat.from={feat.get('from','')!r} keywords={feat.get('keywords',[])[:3]}")
            if not ok:
                continue
            msgid = e["message_id"]
            if msgid and msgid in matched_msgids:
                continue
            date_ok = 1
            if feat.get("date") and e["date"]:
                date_ok = 0 if (e["date"] == feat["date"]) else 1
            diff = _time_diff(self._header_msg(e), feat.get("time", ""))
            key = (date_ok, diff)
            if best is None or key < best[0]:
                best = (key, e)
        if _DEBUG_MATCH and best is None:
            self.log(
                f"    [dbg] 无候选通过校验: criteria={criteria!r} "
                f"verify={verify!r} feat.subject={feat.get('subject','')!r} "
                f"feat.from={feat.get('from','')!r} index_amap={sum(1 for x in index if 'amap' in (x.get('from_addr','') or ''))}")
        return best[1] if best else None

    def download_selected_pdfs(self, save_dir, selected_mails):
        """根据网页勾选的邮件（含发件人/主题/时间特征）匹配并下载其 PDF 附件。

        selected_mails: [{text, time, ...}] 来自网页 list_mails。
        匹配策略：先用 IMAP 建立全量 Message-ID 索引（一次拉取所有候选邮件
        头部），勾选邮件在索引上离线匹配到唯一条目，再按需拉取该邮件全文下载。
        每条命中邮件以其 Message-ID 锚定，避免跨文件夹/重复错配。
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
                    "mailid": m.get("mailid", ""),
                    "message_id": (m.get("message_id", "") or "").strip(),
                    # web 侧真实主题/发件人显示名（最可靠，用于精确匹配）
                    "subject": (m.get("subject", "") or "").strip(),
                    "sender": (m.get("sender", "") or "").strip()}
            feat["keywords"] = text_keywords(text)
            # 提取勾选全文中的业务日期（如「2026年8月11日」→ 2026-08-11），
            # 用于在多个候选（如多封 12306）间精确区分
            feat["date"] = text_date(text)
            # 提取发件人完整邮箱地址（优先 <xxx@domain> 形式，避免砍成关键词误匹配）
            em = re.search(r"<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+)>", text)
            if em:
                feat["from"] = em.group(1)
            else:
                dom = re.search(r"([a-zA-Z0-9]+@[a-zA-Z0-9.-]+)", text)
                if dom:
                    feat["from"] = dom.group(1)
                else:
                    for kw in ("itinerary", "fapiao", "12306", "rails", "alipay"):
                        if kw.lower() in text.lower():
                            feat["from"] = kw + "@"
                            break
            features.append(feat)

        # 一次性建立全量 Message-ID 索引（目标文件夹全部发票候选邮件）
        index = self.build_msgid_index()

        downloaded = []
        pdf_count = 0
        total_amount = 0.0  # 本次勾选邮件金额合计
        matched_nums = set()  # 已下载的 (num, folder)，避免重复
        matched_msgids = []  # 已下载的 Message-ID，避免跨文件夹重复
        total = len(features)

        for i, feat in enumerate(features):
            if self.stop_flag:
                self.log("已手动停止。")
                break
            self.log(f"▶ 匹配邮件: {feat['text'][:40]}")

            entry = self._index_match(feat, index, matched_nums, matched_msgids)
            if entry is None:
                self.log(f"  ⚠ 未匹配到 {feat['text'][:30]}")
                if self.on_progress:
                    self.on_progress(i + 1, total, pdf_count)
                continue

            msg = self.fetch_mail(entry["num"], entry["folder"])
            if msg is None:
                self.log(f"  ⚠ 拉取邮件失败 {entry['folder']}#{entry['num']}")
                if self.on_progress:
                    self.on_progress(i + 1, total, pdf_count)
                continue
            n = self._save_mail_pdfs(entry["num"], entry["folder"], msg, save_dir)
            pdf_count += n
            matched_nums.add((entry["num"], entry["folder"]))
            matched_msgids.append(entry["message_id"])
            downloaded.append(entry["num"])
            if feat.get("mailid"):
                self._downloaded_ok.append(feat["mailid"])
            amt = att_amount(msg)
            if amt:
                total_amount += amt
            self.log(f"  ✓ 匹配 {entry['folder']}#{entry['num']}"
                     + (f"，Message-ID {entry['message_id']}" if entry["message_id"] else "")
                     + f"（PDF {n} 个" + (f"，金额 {amt} 元" if amt else "") + "）")
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

    def _consume_date_from_pdf(self, data):
        """从 PDF 附件内容提取消费日期（行程时间/通行时间/出行日期），失败返回 ""。

        用 pdfplumber 逐页提取文本，从文本中识别带标签的消费日期。
        """
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
        # 消费当天日期：优先邮件正文，其次 PDF 附件内容；取不到退回邮件日期
        consume = consume_date(body_text(msg))
        rw = railway_info(msg) if is_railway(subj) else {}
        atts = extract_attachments(msg)
        for fname, data in atts:
            if self.stop_flag:
                break
            low = fname.lower()
            if low.endswith(".pdf"):
                n += self._save_one_pdf(fname, data, date_str, save_dir, subj, msg, rw, consume)
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
                        n += self._save_one_pdf(fname, zf.read(i), date_str, save_dir, subj, msg, rw, consume)
                except Exception as e:
                    self.log(f"  ⚠ 解压失败 {fname}: {str(e)[:40]}")
        if n == 0 and not self.stop_flag:
            n += self._save_alipay_pdf(msg, save_dir, subj)
        return n

    def _save_one_pdf(self, att_name, data, date_str, save_dir, subj="", msg=None, rw=None, consume=""):
        """按发票内容自动判断类型并命名保存单个 PDF，返回 1。

        类型判断来源：附件名 + 邮件主题 + 邮件正文（综合 invoice_kind）。
        命名日期优先用消费日期 consume（YYYY-MM-DD），取不到退回邮件日期 date_str。
        """
        body = ""
        if msg is not None:
            body = body_text(msg)
        kind = invoice_kind(subj=subj, att_name=att_name, body=body)
        if not consume:
            consume = _consume_date_from_pdf(data)
        use_date = consume or date_str
        dest_name = build_filename(kind, att_name, use_date, msg=msg, rw=rw)
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
          0) 显式 Message-ID → HEADER Message-ID 精确命中（100% 不串）
          1) 主题哈希 → SUBJECT 精确命中
          2) 已知发件人 → FROM 搜索（12306 / itinerary / fapiao / alipay）
          3) 未知发件人 → 用中文关键词做 SUBJECT 搜索
        verify = (kind, value)，供 _verify_match 二次校验。
        """
        # QQ 网页 messageid（如 xmmxza47-1t1786593840tqa062z9x）对应 IMAP 的
        # X-QQ-mid 字段，是 100% 精确匹配的关键，优先级最高。
        if feat.get("message_id"):
            mid = str(feat["message_id"]).strip()
            if mid.startswith("<") and mid.endswith(">"):
                mid = mid[1:-1]
            if mid and mid.startswith("xmmx"):
                return None, ("xqq_mid", mid)
            # 标准 Message-ID（含 @域名）→ HEADER Message-ID 精确命中
            if mid and "@" in mid and re.search(r"@[\w.-]+\.[a-zA-Z]{2,}", mid):
                return f'(HEADER Message-ID "{mid}")', ("message_id", mid)
        if feat.get("hash"):
            return f'(SUBJECT "{feat["hash"]}")', ("hash", None)
        if feat.get("from"):
            from_val = str(feat["from"]).strip()
            if from_val.endswith("@"):
                # 只有关键词兜底（如 itinerary@/fapiao@），用关键词
                from_kw = from_val[:-1]
                return f'(FROM "{from_kw}")', ("from", from_kw)
            if "@" in from_val:
                # 完整邮箱地址 → 精确匹配发件人（避免砍成关键词误命中其他发件人）
                return f'(FROM "{from_val}")', ("from", from_val)
            # 其他关键词
            return f'(FROM "{from_val}")', ("from", from_val)
        # 支付宝提醒（仅当文本明确含「支付宝」或发件人关键词 alipay 时）
        text = feat.get("text", "")
        if ("支付宝" in text or "alipay" in text.lower()
                or "service@mail.alipay.com" in text):
            return '(FROM "alipay")', ("from", "alipay")
        if feat.get("keywords"):
            kw = feat["keywords"][0]
            return f'(SUBJECT "{kw}")', ("subject", kw)
        return None, (None, None)

    def _verify_match(self, feat, msg, verify):
        """校验 IMAP 搜索结果是否真的对应勾选的邮件。

        verify = (kind, value)：
          - message_id : HEADER Message-ID 已精确命中，直接通过
          - hash       : SUBJECT 已精确命中哈希，直接通过
          - from       : 邮件 From 必须包含发件人关键词
          - subject    : 邮件 Subject 必须与勾选文本关键词有交集
        返回 True 表示可下载。
        """
        if verify is None:
            return True
        kind, val = verify
        if kind in ("message_id", "hash", "xqq_mid"):
            return True
        if kind == "from":
            from_hdr = decode_mime(msg.get("From", ""))
            if val.lower() not in from_hdr.lower():
                return False
            # 同发件人可能有多封发票：再用主题相似度区分。
            # 只有短到通用的关键词（如「发票」）不算数，避免误匹配其他发件人。
            subj = decode_mime(msg.get("Subject", ""))
            ws = feat.get("subject", "").strip()
            if ws:
                w_norm = re.sub(r"[^\w]", "", ws.lower())
                s_norm = re.sub(r"[^\w]", "", subj.lower())
                if w_norm and s_norm and (w_norm in s_norm or s_norm in w_norm):
                    return True
                # 有区分度的中文关键词（>=3 字，如「第三方发票及行程单」「花小猪」）
                for kw in feat.get("keywords", []):
                    if len(kw) >= 3 and kw in subj:
                        return True
                # 兜底：勾选文本里出现的公司名出现在主题中
                text = feat.get("text", "")
                for m in re.finditer(r"【([^】]+)】", text):
                    if len(m.group(1)) >= 3 and m.group(1) in subj:
                        return True
                return False
            return True
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