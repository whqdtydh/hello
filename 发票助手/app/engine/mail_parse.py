"""邮件解析纯函数：MIME 解码、正文/附件提取、文本特征与发票信息解析。

本模块不依赖 IMAP 连接或 Qt，全部为无副作用函数，可独立测试。
供 imap_engine（IMAP 下载）与 downloader（网页嗅探命名）共同使用，
避免两者互相 import 造成循环依赖。
"""

import html
import os
import re
import json
import requests
from email.header import decode_header
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse


def decode_mime(s):
    """解码 MIME 编码的字符串（=?UTF-8?B?...?= 或 =?GBK?...?=）。"""
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", "replace"))
            except (LookupError, TypeError):
                out.append(text.decode("utf-8", "replace"))
        else:
            out.append(text)
    return "".join(out)


def header_date(msg):
    """从邮件 header Date 提取 YYYY-MM-DD。"""
    raw = msg.get("Date", "")
    try:
        dt = parsedate_to_datetime(raw)
        return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
    except Exception:
        return ""


def extract_attachments(msg):
    """从邮件对象提取附件列表 [(filename, data)]。"""
    atts = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        fname = part.get_filename()
        if not fname:
            continue
        fname = decode_mime(fname)
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        atts.append((fname, payload))
    return atts


def _decode_html(part):
    """解码 text/html part 的 payload，容错错误的 charset 标注。"""
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset)
    except Exception:
        # GBK 网页常被误标 utf-8，回退尝试常见中文编码
        for enc in ("gb18030", "gbk", "utf-8", "latin-1"):
            try:
                return payload.decode(enc)
            except Exception:
                continue
    return ""


def body_text(msg):
    """提取邮件正文纯文本（优先 text/plain，其次 text/html 去标签）。"""
    try:
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(
                        part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                body = _decode_html(part)
                if body:
                    return re.sub(r"<[^>]+>", " ", html.unescape(body))
    except Exception:
        return ""
    return ""


def subject_hash(text):
    """从勾选邮件文本中提取主题哈希（如 5dbad55a...）。

    浙江通行费主题形如「浙江通行费电子发票_<32位hex>」，
    哈希是唯一标识，可精确匹配 IMAP 邮件。
    """
    if not text:
        return ""
    m = re.search(r"[_-]([a-fA-F0-9]{16,64})", text)
    return m.group(1) if m else ""


def text_keywords(text):
    """从勾选文本提取有区分度的中文关键词（公司名/业务词）。

    例如「【电子发票】您收到一张来自【上海华铁旅客服务有限公司】价税合计」
    提取 → [电子发票, 上海华铁旅客服务有限公司, 价税合计]。
    返回按长度降序排列（越长越有区分度）。
    """
    if not text:
        return []
    candidates = set()
    # 公司名：【xxx】内的内容
    for m in re.finditer(r"【([^】]+)】", text):
        kw = m.group(1).strip()
        if kw:
            candidates.add(kw)
    # 含「电子发票/发票/行程单/价税/旅客」的长片段
    for kw in re.findall(r"[一-龥]{2,20}(?:电子发票|发票|行程单|价税合计|旅客服务)", text):
        if kw:
            candidates.add(kw)
    # 全部中文连续串（去重，>2字）
    for m in re.findall(r"[一-龥]{2,}", text):
        candidates.add(m)
    # 去除非发票相关的通用词
    drop = {"验证您的电子邮箱地址", "网上购票系统", "购票系统"}
    candidates -= drop
    # 按长度降序，优先用长关键词
    return sorted(candidates, key=len, reverse=True)


def text_date(text):
    """从勾选文本提取业务日期（如「2026年8月11日」→ 2026-08-11）。

    12306/高德等邮件正文常含乘车/行程日期，用它区分多封同发件人邮件。
    支持「2026年8月11日」「8月11日」「2026-08-11」「08/11」等格式。
    """
    if not text:
        return ""
    try:
        m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
        if m:
            return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = re.search(r"(20\d{2})/(\d{1,2})/(\d{1,2})", text)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    except Exception:
        return ""
    return ""


def is_railway(subj):
    """判断是否为 12306 高铁邮件（主题含「网上购票」「高铁」「12306」等）。"""
    return any(k in (subj or "") for k in ("网上购票", "12306", "高铁"))


def railway_info(msg):
    """从 12306 高铁邮件 HTML 表格提取乘车信息。

    返回 dict：{date: '2026-08-09'（乘车日期）, issue_date: '2026-08-11'（开票日期）,
                 train: 'G901', route: '上海虹桥-杭州东', amount: 120.0}，
    提取不到对应字段时为空串/0。
    表格结构：
      row4: <td>发票号</td><td>乘车日期</td><td>车次</td><td>发到站</td><td>票价</td><td class="amount">金额</td>
      row8: 开票日期（<td>2026年08月11日</td>）
    """
    info = {"date": "", "issue_date": "", "train": "", "route": "", "amount": 0.0}
    try:
        for part in msg.walk():
            if part.get_content_type() != "text/html" or part.get_filename():
                continue
            body = _decode_html(part)
            if not body:
                continue
            rows = re.findall(r"<tr>(.*?)</tr>", body, re.S)
            for row in rows:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
                cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                cells = [c for c in cells if c]
                if len(cells) >= 5:
                    date_raw, train, route = cells[1], cells[2], cells[3]
                    amount_raw = ""
                    for c in cells[5:]:
                        if re.fullmatch(r"\d+(?:\.\d+)?", c):
                            amount_raw = c
                            break
                    if re.match(r"^20\d{2}", date_raw):
                        m = re.search(
                            r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
                            date_raw)
                        if m:
                            info["date"] = (
                                f"{m.group(1)}-{int(m.group(2)):02d}-"
                                f"{int(m.group(3)):02d}")
                    if re.match(r"^[A-Z]\d+", train):
                        info["train"] = train
                    if "-" in route:
                        info["route"] = route
                    if amount_raw:
                        try:
                            info["amount"] = float(amount_raw)
                        except ValueError:
                            pass
                    continue
                # 开票日期行：仅单个日期单元格（如 2026年08月11日）
                if len(cells) == 1:
                    m = re.search(
                        r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
                        cells[0])
                    if m:
                        info["issue_date"] = (
                            f"{m.group(1)}-{int(m.group(2)):02d}-"
                            f"{int(m.group(3)):02d}")
            break
    except Exception:
        pass
    return info


def ticket_amount(msg):
    """从邮件正文提取金额。

    兼容两种输入：
    - email.message.Message 对象（IMAP 路径）：优先 12306 高铁票价，再回退通用格式
    - 纯文本字符串（API 路径）：通用金额格式（价税合计金额为33.00 / 金额为33.00 /
      票价 87.00 / 33.00元 等）
    返回 float 或 0.0。
    """
    text = ""
    if hasattr(msg, "walk"):
        try:
            for part in msg.walk():
                if part.get_content_type() not in ("text/plain", "text/html") or part.get_filename():
                    continue
                if part.get_content_type() == "text/html":
                    text += _decode_html(part)
                else:
                    payload = part.get_payload(decode=True)
                    if payload:
                        text += payload.decode("utf-8", "replace")
        except Exception:
            pass
    else:
        text = msg or ""
    if not text:
        return 0.0
    # 1) 12306 高铁票价（仅 Message 对象有效）
    try:
        amt = railway_info(msg).get("amount", 0.0)
        if amt:
            return amt
    except Exception:
        pass
    # 2) 通用金额格式：价税合计金额为33.00 / 金额为33.00 / 票价 87.00 / 合计金额：31.27
    m = re.search(r"(?:价税合计|价税|金额|票价|合计|总价)[金额为是:：\s]*(\d+(?:\.\d+)?)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # 3) xx.xx元
    m = re.search(r"(\d+(?:\.\d+)?)元", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 0.0


def alipay_pdf_link(msg):
    """从邮件正文提取支付宝电子发票 PDF 直链（alipayobjects.com）。

    优先返回带 .pdf 后缀的直链；找不到返回空串。
    """
    try:
        for part in msg.walk():
            if part.get_content_type() != "text/html" or part.get_filename():
                continue
            body = _decode_html(part)
            if not body:
                continue
            for m in re.finditer(r'https?://[^\s"<>]+', body):
                url = m.group(0)
                if "alipayobjects.com" in url and ".pdf" in url.lower():
                    return url
            break
    except Exception:
        pass
    return ""


def att_amount(msg):
    """从邮件附件名提取金额（如「82.94元」→ 82.94）。

    高德：附件名含金额；高速费：zip 名含金额。返回 float 或 0.0。
    12306 高铁附件名是发票号.zip（无金额），回退用邮件正文的票价；
    支付宝通知无附件，回退用正文 PDF 直链文件名里的金额。
    """
    for fname, _data in extract_attachments(msg):
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)元", fname)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    # 高铁邮件：附件名无金额，用正文票价
    subj = decode_mime(msg.get("Subject", ""))
    if is_railway(subj):
        return railway_info(msg).get("amount", 0.0)
    # 支付宝通知：从 PDF 直链文件名提取金额
    link = alipay_pdf_link(msg)
    if link:
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)元", link)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return 0.0


def alipay_filename(link, default):
    """从支付宝直链 URL 提取规范文件名（优先 af_fileName / fileName 参数）。

    形如 .../invoice_xxx.pdf?af_fileName=63.30元-上海...-电子发票.pdf
    取不到时回退 default。
    """
    query = parse_qs(urlparse(link).query)
    fn = ""
    if query.get("af_fileName"):
        fn = query["af_fileName"][0]
    elif query.get("fileName"):
        fn = query["fileName"][0]
    if fn:
        fn = unquote(fn)
    if not fn.lower().endswith(".pdf"):
        base = os.path.basename(urlparse(link).path)
        fn = unquote(base)
    if not fn or not fn.lower().endswith(".pdf"):
        return default
    return fn


# 发票类型关键词：按优先级从高到低判断（子串命中即可）
INVOICE_KINDS = [
    ("电子行程单", ("行程单", "行程码", "行程信息")),
    ("高铁发票", ("高铁", "铁路", "网上购票", "火车票", "动车", "华铁")),
    ("高速发票", ("通行费", "高速费", "路桥费", "ETC", "公路")),
    ("打车发票", ("打车", "出租", "网约车", "滴滴", "首汽", "曹操出行", "T3出行")),
    ("酒店发票", ("酒店", "住宿", "宾馆", "旅店", "民宿", "客栈", "旅馆")),
    ("餐饮发票", ("餐饮", "饭店", "餐厅", "饮食", "食堂", "酒楼", "饭庄", "小吃", "咖啡", "奶茶")),
    ("停车发票", ("停车", "停车场", "泊车")),
    ("加油发票", ("加油", "汽油", "柴油", "石油")),
    ("购物发票", ("超市", "百货", "商城", "便利店", "购物", "商店", "零售")),
    ("机票发票", ("机票", "航空", "东航", "南航", "国航", "春秋", "吉祥航空", "航班")),
    ("快递发票", ("快递", "物流", "顺丰", "中通", "圆通", "韵达", "邮政")),
    ("通讯发票", ("话费", "电信", "移动", "联通", "通讯", "宽带")),
    ("水电发票", ("水费", "电费", "燃气", "物业")),
]


def invoice_kind(subj="", att_name="", body="", sender=""):
    """根据发件人/主题/附件名/正文综合判断发票类型。

    优先级：发件人（sender，航空公司/航旅平台 → 机票）> 附件名（att_name）
    > 主题（subj）> 正文（body），逐字段按 INVOICE_KINDS 顺序命中即返回。
    附件名优先于正文可避免正文中的通用说明词（如「行程单」字样）污染判断。
    返回 kind 字符串（如「电子行程单」「高铁发票」「电子发票」）。
    """
    # 发件人优先：航空公司/航旅平台（机票行程单主题常含「电子行程单」，须先识别）
    if sender and any(k in sender for k in (
            "umetrip", "travelsky", "xiamenair", "airchina", "ceair", "csair",
            "sichuanair", "springair", "juneyao", "hainanair", "航空", "航旅", "航班")):
        return "机票发票"
    for field in (att_name, subj, body):
        if not (field or "").strip():
            continue
        for kind, kws in INVOICE_KINDS:
            for kw in kws:
                if kw in field:
                    return kind
    return "电子发票"


def consume_date(text):
    """从邮件正文/PDF 文本提取「消费当天日期」（YYYY-MM-DD），取不到返回 ""。

    出行/通行类票据常标注业务发生日期，与开票（发送）日期不同日，
    例如高德行程单「行程时间：2026-07-30」，高速发票「通行时间：2026-08-13」。
    支持标签：行程时间 / 通行时间 / 出行日期 / 乘车日期 / 消费日期 / 交易时间。
    """
    if not text:
        return ""
    # 优先带标签的消费日期
    m = re.search(
        r"(?:行程时间|通行时间|出行日期|乘车日期|消费日期|交易时间|行程日期|上车时间)"
        r"[：:\s]*((?:20\d{2})[年\-/.]\d{1,2}[月\-/.]\d{1,2})",
        text)
    if m:
        d = _norm_date(m.group(1))
        if d:
            return d
    # 兜底：文本中任意「20xx年M月D日」形式的日期（开票日期优先排除？无标签则返回第一个）
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(20\d{2})[./\-](\d{1,2})[./\-](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _norm_date(s):
    try:
        m = re.search(r"(20\d{2})[年\-/.](\d{1,2})[月\-/.](\d{1,2})", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    except Exception:
        pass
    return ""