"""PDF 票面解析服务：文本提取 / 金额提取 / 消费日期提取 / 铁路客票信息提取。

从 api_downloader 拆出（任务5），全部为纯函数，便于独立测试与回归。
依赖：app.engine.archive_service（_cn_date_to_iso）、app.engine.mail_parse（consume_date）。
"""

import os
import re

from app.engine.archive_service import _cn_date_to_iso
from app.engine.mail_parse import consume_date


def pdf_text(filepath):
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


def amount_from_pdf(filepath):
    """从 PDF 票面提取金额（普通发票「价税合计」是标准固定字段，最准）。

    规则（按优先级）：
    1. 价税合计 后的金额（跳过「（大写）…」段）
    2. （小写）/小写 后的 ¥ 金额（增值税电子发票固定格式）
    3. 总计/合计/金额合计 后的金额（货拉拉/滴滴行程单：总计178.79元）
    4. 票价 后的金额（高铁/铁路/行程单）
    5. 兜底：票面所有 ¥/￥ 符号后的金额取最大值（价税合计通常是票面最大数字）
    返回 float；提不到返回 0.0。
    """
    txt = pdf_text(filepath)
    if not txt:
        return 0.0
    m = None
    # 1) 价税合计 → 其后最近的数字（非贪婪跳过「（大写）壹佰贰拾叁元…」段）
    m = re.search(r"价税合计[^0-9]{0,60}?[¥￥]?\s*(\d+(?:\.\d{1,2})?)", txt, re.S)
    # 2) （小写）后的金额
    if not m:
        m = re.search(r"（?小写）?[¥￥]?\s*(\d+(?:\.\d{1,2})?)", txt)
    # 3) 总计（货拉拉/滴滴行程单：总计178.79元）/ 合计 / 金额合计
    if not m:
        m = re.search(r"总计[¥￥]?\s*(\d+(?:\.\d{1,2})?)", txt)
    if not m:
        m = re.search(r"(?:金额)?合计[¥￥]?\s*(\d+(?:\.\d{1,2})?)", txt)
    # 4) 票价（铁路/行程单）
    if not m:
        m = re.search(r"票价[¥￥]?\s*(\d+(?:\.\d{1,2})?)", txt)
    if m:
        amt = float(m.group(1))
        if 0 < amt < 1000000:
            return amt
    # 5) 兜底：所有 ¥/￥ 金额取最大值（价税合计通常是票面最大数字）
    amounts = [float(x) for x in re.findall(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)", txt)]
    if amounts:
        amt = max(amounts)
        if 0 < amt < 1000000:
            return amt
    return 0.0


def consume_date_from_pdf(data):
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


def consume_date_from_file(filepath, log=None):
    """从本地 PDF 文件提取消费日期。"""
    try:
        with open(filepath, "rb") as f:
            data = f.read()
        d = consume_date_from_pdf(data)
        if not d and log:
            log(f"    PyMuPDF未提取到日期: {os.path.basename(filepath)}")
        return d
    except Exception as e:
        if log:
            log(f"    读取PDF失败: {str(e)[:60]}")
        return ""


def railway_info_from_pdf(filepath):
    """从铁路电子客票 PDF 提取乘车信息（乘车日期/开票日期/车次/路线/票价）。

    12306 铁路电子客票 PDF 有两种文本布局：
      A) 车次与日期相邻 + 票价同行：G258\n2026年08月06日 / 票价:￥87.00
      B) 车次与日期隔英文站名 + 票价分离：G901\nShanghaihongqiao\nHangzhouxi\n
         2026年08月09日 / 票价:\n...\n￥120.00（金额独立行）
    返回 dict：{date, issue_date, train, route, amount}，提取不到为空串/0。
    """
    info = {"date": "", "issue_date": "", "train": "", "route": "", "amount": 0.0}
    try:
        t = pdf_text(filepath)
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
