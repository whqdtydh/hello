"""诊断：IMAP 搜索匹配（从已保存凭据读取，不打印密钥）。"""
import sys, os, json
sys.path.insert(0, r"D:\AI\git 地址\发票助手")

from app.engine.imap_engine import (
    ImapEngine, _decode_mime, _utf7_encode, _imap_search,
    _search_criteria, _ascii_criteria,
)
import email
from email.utils import parsedate_to_datetime

cred = json.load(open(os.path.join(os.path.expanduser("~"), ".invoice_assistant", "imap_cred.json"), encoding="utf-8"))
account = cred["account"]
auth = cred["auth_code"]
print(f"account={account}")

eng = ImapEngine(account, auth, on_log=print)
eng.connect()

print("=== 文件夹 ===")
for f in eng.list_folders():
    print("  ", repr(f))

criteria = _search_criteria()
print("=== criteria ===")
print("  ", criteria)

for folder in ("INBOX", "报销"):
    f7 = _utf7_encode(folder)
    try:
        r, _ = eng.mail.select(f7, readonly=True)
    except Exception as e:
        print(f"[select失败] {folder}: {e}")
        continue
    if r != "OK":
        print(f"[select失败] {folder}: {r}")
        continue
    r, data = _imap_search(eng.mail, criteria)
    n = len(data[0].split()) if r == "OK" and data and data[0] else 0
    print(f"[{folder}] search r={r} count={n}")
    if n:
        for num in data[0].split()[:15]:
            r2, d2 = eng.mail.fetch(num, "(RFC822.HEADER)")
            if r2 != "OK" or not d2 or d2[0] is None:
                continue
            msg = email.message_from_bytes(d2[0][1])
            dt = msg.get("Date", "")
            try:
                dt = parsedate_to_datetime(dt).strftime("%m-%d %H:%M")
            except Exception:
                dt = "?"
            print(f"    #{num.decode()} [{dt}] {_decode_mime(msg.get('From',''))[:40]} | {_decode_mime(msg.get('Subject',''))[:45]}")

print("=== 单独 FROM itinerary 搜索（全部文件夹） ===")
for folder in ("INBOX", "其他文件夹/报销", "其他文件夹/邮件归档"):
    f7 = _utf7_encode(folder)
    r, _ = eng.mail.select(f7, readonly=True)
    print(f"[select {folder}] r={r}")
    if r != "OK":
        continue
    r, data = _imap_search(eng.mail, '(FROM "itinerary@ridesharing.amap.com")')
    n = len(data[0].split()) if r == "OK" and data and data[0] else 0
    print(f"  count={n}")
    if n:
        for num in data[0].split():
            r2, d2 = eng.mail.fetch(num, "(RFC822.HEADER)")
            if r2 != "OK" or not d2 or d2[0] is None:
                continue
            msg = email.message_from_bytes(d2[0][1])
            dt = msg.get("Date", "")
            try:
                dt = parsedate_to_datetime(dt).strftime("%m-%d %H:%M")
            except Exception:
                dt = "?"
            print(f"    #{num.decode()} [{dt}] {_decode_mime(msg.get('From',''))[:40]} | {_decode_mime(msg.get('Subject',''))[:45]}")

eng.logout()