"""邮件解析纯函数：正文/附件文本提取与发票信息解析。

本模块不依赖 IMAP 连接或 Qt，全部为无副作用函数，可独立测试。
供 api_downloader（API 下载）使用。
"""

import re


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
    - email.message.Message 对象：优先 12306 高铁票价，再回退通用格式
    - 纯文本字符串：通用金额格式（价税合计金额为33.00 / 金额为33.00 /
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
