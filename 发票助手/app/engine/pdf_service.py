"""PDF 票面解析服务：文本提取 / 金额提取 / 消费日期提取 / 铁路客票信息提取。

从 api_downloader 拆出（任务5），全部为纯函数，便于独立测试与回归。
依赖：app.engine.archive_service（_cn_date_to_iso）、app.engine.mail_parse（consume_date）。
"""

import os
import re
import threading

from app.engine.archive_service import _cn_date_to_iso
from app.engine.mail_parse import consume_date

# OCR 引擎懒加载（首次使用才加载，约 3-4 秒；加载后复用）
_ocr_engine = None
_ocr_lock = threading.Lock()


def _pymupdf_text(filepath):
    """读取 PDF 文本层全文文本，失败返回空串。"""
    try:
        import pymupdf  # PyMuPDF
        doc = pymupdf.open(filepath)
        try:
            return "".join(page.get_text() or "" for page in doc)
        finally:
            doc.close()
    except Exception:
        return ""


def _get_ocr_engine():
    """懒加载 RapidOCR 引擎（线程安全，失败返回 None）。"""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                try:
                    from app.engine.ocr_light import RapidOCR
                    _ocr_engine = RapidOCR()
                except Exception:
                    _ocr_engine = False  # 加载失败，不再重试
    return _ocr_engine or None


def ocr_text(filepath, dpi=200, max_pages=1):
    """图片型/扫描件 PDF 降级 OCR：渲染前 N 页为图片识别，拼接文本。

    仅文本层为空或过少时调用（见 pdf_text）。失败返回空串（静默降级）。
    """
    try:
        engine = _get_ocr_engine()
        if engine is None:
            return ""
        import pymupdf
        doc = pymupdf.open(filepath)
        parts = []
        try:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                pix = page.get_pixmap(dpi=dpi)
                import numpy as np
                img = np.frombuffer(pix.samples, dtype=np.uint8)
                img = img.reshape(pix.height, pix.width, pix.n)
                if pix.n == 4:  # RGBA → RGB（OCR 不需要 alpha）
                    img = img[:, :, :3]
                res, _ = engine(img)
                if res:
                    parts.append("".join(r[1] for r in res))
        finally:
            doc.close()
        return "\n".join(parts)
    except Exception:
        return ""


def pdf_text(filepath, ocr_fallback=True):
    """读取 PDF 全文文本；文本层为空或过少（图片型/扫描件）时降级 OCR。

    OCR 结果文本优于文本层时才采用，避免噪声文本覆盖有效内容。
    """
    txt = _pymupdf_text(filepath)
    if ocr_fallback and len(txt.strip()) < 30:
        ocr = ocr_text(filepath)
        if len(ocr.strip()) > len(txt.strip()):
            return ocr
    return txt


def amount_from_pdf(filepath):
    """从 PDF 票面提取金额（规则配置化，见 config/extract_rules.json 与
    app.engine.extract_rules.extract_amount）。

    优先级：价税合计 → 小写 → 总计（行程单）→ 合计 → 票价 → ¥最大值兜底。
    返回 float；提不到返回 0.0。
    """
    from app.engine.extract_rules import extract_amount
    txt = pdf_text(filepath)
    if not txt:
        return 0.0
    return extract_amount(txt)


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
