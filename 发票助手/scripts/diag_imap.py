"""诊断：IMAP 搜索发票邮件并列出主题/发件人/时间/附件，验证匹配数据来源。"""
import sys, os
sys.path.insert(0, r"D:\AI\git 地址\发票助手")

from app.engine.imap_engine import ImapEngine, _decode_mime, _utf7_encode, _imap_search, _search_criteria

account = sys.argv[1] if len(sys.argv) > 1 else input("QQ邮箱: ")
auth = sys.argv[2] if len(sys.argv) > 2 else input("授权码: ")

eng = ImapEngine(account, auth, on_log=print)
eng.connect()

print("=== 文件夹列表 ===")
for f in eng.list_folders():
    print("  ", repr(f))

print("=== 搜索发票邮件 ===")
criteria = _search_criteria()
for folder in ("INBOX", "报销"):
    f7 = _utf7_encode(folder)
    r, _ = eng.mail.select(f7, readonly=True)
    if r != "OK":
        print(f"  [跳过] {folder}")
        continue
    r, data = _imap_search(eng.mail, criteria)
    if r != "OK" or not data or not data[0]:
        print(f"  [无匹配] {folder}")
        continue
    nums = data[0].split()
    print(f"  [{folder}] 匹配 {len(nums)} 封")
    for num in nums[:20]:
        r, d = eng.mail.fetch(num, "(RFC822.HEADER)")
        if r != "OK" or not d or d[0] is None:
            continue
        import email
        from email.utils import parsedate_to_datetime
        msg = email.message_from_bytes(d[0][1])
        dt = msg.get("Date", "")
        try:
            dt = parsedate_to_datetime(dt).strftime("%m-%d %H:%M")
        except Exception:
            dt = "?"
        print(f"    #{num.decode()} [{dt}] {_decode_mime(msg.get('From',''))[:35]} | {_decode_mime(msg.get('Subject',''))[:40]}")
eng.logout()