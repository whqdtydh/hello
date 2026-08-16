#!/usr/bin/env python3
"""
IMAP 版 Message-ID 提取脚本（替代原网页嗅探逆向方案）
授权：最高权限，用户邮箱，用户会话
功能：直连 QQ 邮箱 IMAP，搜索发票/行程单邮件，直接读取每封邮件的
      Message-ID（RFC 5322 头）、主题哈希、发件人、时间。

用法：
  python reverse_message_id.py                    搜索全部发票邮件并打印 Message-ID
  python reverse_message_id.py --from 12306       只看某发件人（itinerary/fapiao/12306/alipay）
  python reverse_message_id.py --hash 5dbad55a..  按主题哈希精确搜一封
  python reverse_message_id.py --all-folders      扫描全部文件夹（默认只扫报销/收件箱）
"""
import argparse
import json
import os
import re
import sys

import imaplib
from email.header import decode_header

IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
CRED_FILE = os.path.expanduser("~/.invoice_assistant/imap_cred.json")

SEARCH_TERMS = [
    ("FROM", "itinerary@ridesharing.amap.com"),
    ("FROM", "fapiao"),
    ("SUBJECT", "发票"),
    ("SUBJECT", "行程单"),
    ("SUBJECT", "电子"),
]


def decode_mime(s):
    """解码 MIME 编码头（=?UTF-8?B?...?=）。"""
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


def subject_hash(text):
    """从主题提取哈希（如「浙江通行费电子发票_5dbad55a...」→ 5dbad55a...）。"""
    if not text:
        return ""
    m = re.search(r"[_-]([a-fA-F0-9]{16,64})", text)
    return m.group(1) if m else ""


def is_ascii(s):
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _imap_search(mail, criteria):
    """执行 IMAP 搜索；中文条件用 UTF-8，失败回退纯 ASCII。"""
    if not is_ascii(criteria):
        try:
            r, data = mail.search("UTF-8", criteria.encode("utf-8"))
            if r == "OK":
                return r, data
        except Exception:
            pass
        ascii_parts = []
        for field, val in re.findall(r'\((\w+)\s+"([^"]*)"\)', criteria):
            if is_ascii(val):
                ascii_parts.append(f'({field} "{val}")')
        if ascii_parts:
            criteria = " OR ".join(ascii_parts)
    try:
        return mail.search(None, criteria)
    except Exception:
        return "NO", b""


def build_criteria(from_kw="", hash_val=""):
    """构造搜索条件：优先主题哈希精确命中，其次发件人。"""
    if hash_val:
        return f'(SUBJECT "{hash_val}")'
    if from_kw:
        return f'(FROM "{from_kw}")'
    ors = [f'({field} "{val}")' for field, val in SEARCH_TERMS]
    return " OR ".join(ors)


def target_folders(mail, all_folders):
    """默认只扫报销/收件箱（报销优先）；--all-folders 时返回全部。"""
    if all_folders:
        return all_folders
    target = []
    for f in all_folders:
        if "报销" in f or f == "INBOX":
            target.append(f)
    if not target:
        target = [f for f in all_folders
                  if any(k in f for k in ("报销", "发票", "行程单", "税"))]
    if not target:
        target = ["INBOX"]
    target.sort(key=lambda f: 0 if "报销" in f else (1 if f == "INBOX" else 2))
    return target


def list_folders(mail):
    """列出全部文件夹名（IMAP UTF-7 解码回中文）。"""
    folders = []
    r, data = mail.list()
    for item in data:
        if not item:
            continue
        line = item.decode("utf-8", "replace")
        m = re.search(r'"([^"]+)"\s*$', line)
        if m:
            folders.append(m.group(1))
    return folders


def utf7_decode(name):
    """IMAP UTF-7 文件夹名解码。优先 imapclient，回退内置实现。"""
    try:
        from imapclient import imap_utf7
        return imap_utf7.decode(name)
    except Exception:
        try:
            import base64
            # 简易实现：只处理 &xxxx- 形式的 base64 片段
            def _dec(match):
                b64 = match.group(1).replace(",", "/")
                return base64.b64decode(b64 + "===").decode("utf-16-be", "replace")
            return re.sub(r"&([^-]+)-", _dec, name)
        except Exception:
            return name


def utf7_encode(name):
    """IMAP UTF-7 文件夹名编码。"""
    try:
        from imapclient import imap_utf7
        return imap_utf7.encode(name)
    except Exception:
        return name


def main():
    ap = argparse.ArgumentParser(description="IMAP 提取 QQ 邮箱发票邮件 Message-ID")
    ap.add_argument("--from", dest="from_kw", default="",
                    help="发件人关键词（itinerary/fapiao/12306/alipay）")
    ap.add_argument("--hash", dest="hash_val", default="",
                    help="主题哈希（精确匹配一封）")
    ap.add_argument("--all-folders", action="store_true",
                    help="扫描全部文件夹（默认只扫报销/收件箱）")
    args = ap.parse_args()

    if not os.path.exists(CRED_FILE):
        print(f"[错误] 找不到凭据文件：{CRED_FILE}")
        sys.exit(1)
    cred = json.load(open(CRED_FILE, encoding="utf-8"))
    account = cred.get("account", "")
    auth_code = cred.get("auth_code", "")
    if not account or not auth_code:
        print("[错误] 凭据文件缺少 account/auth_code 字段")
        sys.exit(1)

    print(f"=== IMAP 提取 Message-ID ===")
    print(f"账号：{account}")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(account, auth_code)

    try:
        raw_folders = list_folders(mail)
        folders = target_folders(mail, raw_folders) if args.all_folders else \
            [f for f in raw_folders if "报销" in utf7_decode(f) or
             utf7_decode(f) == "INBOX"]
        folders = folders or [f for f in raw_folders]
        criteria = build_criteria(args.from_kw, args.hash_val)
        print(f"搜索条件：{criteria}")
        print(f"目标文件夹：{[utf7_decode(f) for f in folders]}")

        found = []
        for raw_f in folders:
            folder = utf7_decode(raw_f)
            try:
                r, _ = mail.select(utf7_encode(folder), readonly=True)
            except Exception as e:
                print(f"  打开失败 {folder}: {str(e)[:40]}")
                continue
            if r != "OK":
                print(f"  文件夹不存在: {folder}")
                continue
            r, data = _imap_search(mail, criteria)
            if r != "OK" or not data or not data[0]:
                continue
            nums = [n.decode() for n in data[0].split()]
            if not nums:
                continue
            for num in nums:
                try:
                    r2, d = mail.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
                    if r2 != "OK" or not d or not d[0]:
                        continue
                    hdr = d[0][1] if isinstance(d[0], tuple) else b""
                    msg_text = hdr.decode("utf-8", "replace")
                    subj = decode_mime(re.search(r"Subject:\s*(.*)", msg_text, re.I).group(1)) \
                        if re.search(r"Subject:\s*(.*)", msg_text, re.I) else ""
                    sender = re.search(r"From:\s*(.*)", msg_text, re.I)
                    mid = re.search(r"Message-ID:\s*<([^>]+)>", msg_text, re.I)
                    date = re.search(r"Date:\s*(.*)", msg_text, re.I)
                    found.append({
                        "folder": folder,
                        "num": num,
                        "message_id": mid.group(1) if mid else "",
                        "subject_hash": subject_hash(subj),
                        "sender": sender.group(1).strip() if sender else "",
                        "subject": subj,
                        "date": date.group(1).strip() if date else "",
                    })
                except Exception as e:
                    print(f"  读取 #{num} 失败: {str(e)[:40]}")

        print(f"\n[结果] 共 {len(found)} 封邮件：")
        for m in found:
            print(f"  {m['folder']}#{m['num']}")
            print(f"    Message-ID : {m['message_id'] or '(无)'}")
            print(f"    主题哈希   : {m['subject_hash'] or '(无)'}")
            print(f"    发件人     : {m['sender']}")
            print(f"    主题       : {m['subject'][:60]}")
            print(f"    时间       : {m['date']}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()