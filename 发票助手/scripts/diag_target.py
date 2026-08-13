"""诊断：确认 _target_folders 返回值和各文件夹搜索命中情况。"""
import sys, os, json
sys.path.insert(0, r"D:\AI\git 地址\发票助手")

from app.engine.imap_engine import (
    ImapEngine, _utf7_encode, _imap_search,
)
import email
from email.utils import parsedate_to_datetime

cred = json.load(open(os.path.join(os.path.expanduser("~"), ".invoice_assistant", "imap_cred.json"), encoding="utf-8"))
eng = ImapEngine(cred["account"], cred["auth_code"], on_log=print)
eng.connect()

print("=== list_folders() 原始 ===")
for f in eng.list_folders():
    print("  ", repr(f))

print("=== _target_folders() ===")
print("  ", eng._target_folders())

criteria = '(FROM "itinerary") OR (SUBJECT "发票") OR (SUBJECT "行程单")'
print(f"=== 搜索 criteria: {criteria} ===")
for folder in eng._target_folders():
    try:
        eng.mail.select(_utf7_encode(folder), readonly=True)
    except Exception as e:
        print(f"[{folder}] select失败: {e}")
        continue
    r, data = _imap_search(eng.mail, criteria)
    n = len(data[0].split()) if r == "OK" and data and data[0] else 0
    print(f"[{folder}] count={n}")
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
            subj = str(msg.get("Subject", ""))[:30]
            fr = str(msg.get("From", ""))[:30]
            print(f"    #{num.decode()} [{dt}] {fr} | {subj}")

eng.logout()