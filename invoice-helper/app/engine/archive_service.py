"""命名与归档纯函数：发票文件命名、去重、金额归档文件夹。

从 api_downloader 拆出（任务5），保证行为与拆分前完全一致。
依赖：app.config（关键词）、app.engine.mail_parse（ticket_amount）。
"""

import os
import re

from app import config
from app.engine.mail_parse import ticket_amount


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


def build_filename(kind, display_name, date_str, msg=None, rw=None, amt=None):
    """简化命名：8.6号_发票_31.27.pdf / 8.6号_行程单_31.27.pdf / 8.6号_高速发票_21.00.pdf /
    8.6号_打车发票_82.94.pdf；高铁：8月9_高铁_上海虹桥-杭州东_120.00.pdf

    规则：所有文件命名必须标注价格；提取不到金额时标注「未识别出金额」。
    amt: PDF 票面金额（float），最准，优先于附件名/正文金额。
    """
    company, amount = parse_original_name(display_name)
    no_amount_label = "未识别出金额"
    if "高铁" in kind:
        if rw:
            date_part = _month_date_label(rw.get("date") or rw.get("issue_date") or date_str)
            route = rw.get("route", "")
            amt_v = rw.get("amount")
            amt_label = f"{amt_v:.2f}" if amt_v else no_amount_label
            parts = [p for p in [date_part, "高铁", route, amt_label] if p]
            if parts:
                return "_".join(parts) + ".pdf"
        label = "高铁发票"
    elif "行程单" in kind:
        label = "行程单"
    elif "发票" in kind:
        label = kind
    else:
        label = kind
    if amt is not None and amt > 0:
        amount = f"{amt:.2f}"  # 票面金额优先（价税合计，最准）
    elif not amount and msg is not None:
        try:
            amt2 = ticket_amount(msg)
            if amt2:
                amount = f"{amt2:.2f}"
        except Exception:
            pass
    parts = [date_label(date_str), label]
    # 强制标注价格：提取不到则标注「未识别出金额」
    parts.append(amount if amount else no_amount_label)
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
