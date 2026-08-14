"""内嵌浏览器版发票下载控制器（勾选模式 + 嗅探批量下载）。

流程：用户在内嵌网页勾选邮件 -> 程序遍历勾选邮件 -> 打开发票邮件
     -> 读取附件 -> 批量触发 PDF 下载并嗅探 URL -> 并行 requests 拉取保存 -> 返回列表。
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from app import config
from app.engine.mail_parse import ticket_amount
from app.engine.web_client import WebClient


# ---------- 纯函数：命名解析（可独立测试） ----------

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
    m_amount = re.search(r"(\d+(?:\.\d+)?)元", display_name)
    if m_amount:
        amount = m_amount.group(1)
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

    其余类型（酒店发票/餐饮发票/机票发票等）直接沿用 kind 作为标签。
    rw（铁路信息 dict）非空时使用乘车日期/发到站/票价生成高铁文件名。
    """
    company, amount = parse_original_name(display_name)
    if "高铁" in kind:
        if rw:
            # 用开票日期（票面上的开票日期），取不到再退回乘车日期/邮件日期
            date_part = _month_date_label(rw.get("issue_date") or rw.get("date") or date_str)
            route = rw.get("route", "")
            amt = rw.get("amount") or 0.0
            parts = [p for p in [date_part, "高铁", route, f"{amt:.2f}" if amt else ""] if p]
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
    if amount:
        parts.append(amount)
    return "_".join(p for p in parts if p) + ".pdf"


def unique_path(dest_dir, filename):
    base, ext = os.path.splitext(filename)
    cand = os.path.join(dest_dir, filename)
    n = 1
    while os.path.exists(cand):
        cand = os.path.join(dest_dir, f"{base}_{n}{ext}")
        n += 1
    return cand


# ---------- 控制器 ----------

class DownloadController:
    """驱动内嵌浏览器，下载勾选邮件中的 PDF 附件（嗅探式）。"""

    def __init__(self, client: WebClient, save_dir, on_log=None, on_progress=None):
        self.client = client
        self.save_dir = save_dir
        self.on_log = on_log or (lambda m: None)
        self.on_progress = on_progress or (lambda p, t, d: None)

        self.downloaded_files = []
        self.downloaded_pdf_count = 0
        self.stop_flag = False

    def log(self, msg):
        self.on_log(msg)

    # ---------- 主流程 ----------
    def run(self, selected_mails):
        """selected_mails: 用户勾选的邮件 [{index, text}]"""
        os.makedirs(self.save_dir, exist_ok=True)

        if not selected_mails:
            self.log("没有检测到勾选的邮件。请先在左侧网页中勾选需要下载的邮件。")
            return []

        total = len(selected_mails)
        self.log(f"检测到勾选 {total} 封邮件，开始处理…")
        processed = 0

        for m in selected_mails:
            if self.stop_flag:
                self.log("已手动停止。")
                break
            idx = m["index"]
            text = m.get("text", "")

            if not is_invoice_mail(text):
                self.log(f"⏭ 跳过非发票邮件: {text[:30]}")
                continue

            self.log(f"▶ 打开发票邮件: {text[:40]}…")
            if not self.client.click_mail(idx):
                self.log("    打开失败")
                continue
            self._wait_detail_ready()

            date_str = normalize_date(self.client.get_mail_date())
            attachments = self.client.get_attachments()
            n_pdf = 0

            # 批量嗅探：先为所有 PDF 附件规划目标路径并逐个点击下载按钮
            # （不等待），然后统一收集拦截器捕获的 URL，再并行拉取。
            tasks = []  # [(dest, url)]
            clicked_cards = []  # 已成功点击的附件卡片索引（顺序）

            for c, att in enumerate(attachments):
                if self.stop_flag:
                    break
                name = att.get("name", "")
                suffix = att.get("suffix", "").strip().lower()
                display = att.get("suffix", "").strip()
                size = att.get("size", "").strip()

                if suffix != config.PDF_SUFFIX:
                    self.log(f"    ⏭ 跳过非PDF {name}{display} ({size})")
                    continue

                kind = "电子行程单" if "行程单" in (name + display) else "电子发票"
                fname = build_filename(kind, name, date_str)
                dest = unique_path(self.save_dir, fname)
                tasks.append((dest, None))

                if self.client.click_download(c):
                    clicked_cards.append(c)
                else:
                    self.log(f"    ✗ 无法触发下载 {name}{display}")

            # 统一收集：点击后拦截器立即捕获，通常很快
            if clicked_cards:
                self.client.qt_sleep(0.3)
                pending_urls = self.client.consume_download_url(timeout=3.0)
                for i in range(min(len(tasks), len(pending_urls))):
                    tasks[i] = (tasks[i][0], pending_urls[i])
                for dest, url in tasks:
                    if not url:
                        self.log(f"    ✗ 未嗅探到下载URL {os.path.basename(dest)}")

            # 并行拉取所有已嗅探到 URL 的下载
            urls_to_fetch = [(dest, url) for dest, url in tasks if url]
            if urls_to_fetch:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {}
                    for dest, url in urls_to_fetch:
                        self.log(f"    ↓ 拉取 {os.path.basename(dest)}")
                        futures[pool.submit(self.client.fetch_url, url, dest)] = dest
                    for fut in futures:
                        dest = futures[fut]
                        try:
                            fut.result()
                            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                                self.report_downloaded(dest)
                                n_pdf += 1
                            else:
                                self.log(f"    ✗ 拉取结果为空 {os.path.basename(dest)}")
                        except Exception as e:
                            self.log(f"    ✗ 拉取失败 {os.path.basename(dest)}: {str(e)[:60]}")

            processed += 1
            self.log(f"    本邮件完成（PDF {n_pdf} 个）")
            self.on_progress(processed, total, self.downloaded_pdf_count)

            if not self.client.back_to_list():
                self.log("    返回列表失败（将尝试重新加载）")
            self.client.qt_sleep(0.3)

        self.log(f"🏁 全部完成，共下载 {len(self.downloaded_files)} 个 PDF → {self.save_dir}")
        return self.downloaded_files

    def report_downloaded(self, path):
        if path and path not in self.downloaded_files:
            self.downloaded_files.append(path)
            self.downloaded_pdf_count += 1
            self.log(f"    ✓ 已保存：{os.path.basename(path)}")
            self.on_progress(0, 0, self.downloaded_pdf_count)

    def _wait_detail_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.client.mail_detail_ready():
                return True
            self.client.qt_sleep(0.2)
        return False