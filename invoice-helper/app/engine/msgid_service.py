"""本机 Message-ID 登记服务。

油猴脚本在浏览器里把「勾选邮件的 mailid + 从原文提取的 Message-ID + 主题/时间」
POST 到本服务，写入 SQLite。主程序下载前用 mailid 查询回填真实 Message-ID，
交给 IMAP 引擎做 HEADER Message-ID 精确匹配（100% 不串）。

接口：
  POST /record   {"mailid": str, "message_id": str, "subject": str, "time": str}
  GET  /get      ?mailid=xxx    → {"ok": true, "data": {...}} 或 {"ok": false}
  GET  /all      → 全部记录
  GET  /health   → {"ok": true}
  POST /check    {"mailid": str}          → 登记「待预读」邮件（边勾边读）
  GET  /pending                          → 待预读 mailid 列表
  POST /done     {"mailid": str}          → 预读完成，移出队列
"""

import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "127.0.0.1"
PORT = 18765
DB_FILE = os.path.join(os.path.expanduser("~"), ".invoice_assistant", "msgid_db.sqlite3")


def _connect():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS msgid_map ("
            "  mailid TEXT PRIMARY KEY,"
            "  message_id TEXT NOT NULL,"
            "  subject TEXT DEFAULT '',"
            "  time TEXT DEFAULT '',"
            "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS preload_pending ("
            "  mailid TEXT PRIMARY KEY,"
            "  ts INTEGER DEFAULT 0"
            ")"
        )
        conn.commit()
    finally:
        conn.close()


def upsert(mailid, message_id, subject="", time_str=""):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO msgid_map (mailid, message_id, subject, time) VALUES (?,?,?,?)"
            " ON CONFLICT(mailid) DO UPDATE SET"
            " message_id=excluded.message_id, subject=excluded.subject, time=excluded.time"
            ", created_at=CURRENT_TIMESTAMP",
            (mailid, message_id, subject or "", time_str or ""),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get(mailid):
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT mailid, message_id, subject, time FROM msgid_map WHERE mailid=?",
            (mailid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"mailid": row[0], "message_id": row[1],
                "subject": row[2], "time": row[3]}
    finally:
        conn.close()


def all_records():
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT mailid, message_id, subject, time, created_at FROM msgid_map"
            " ORDER BY created_at DESC"
        )
        return [{"mailid": r[0], "message_id": r[1], "subject": r[2],
                 "time": r[3], "created_at": r[4]} for r in cur.fetchall()]
    finally:
        conn.close()


# ---------- 预读队列（边勾边读） ----------

def enqueue_preload(mailid):
    """登记待预读邮件（勾选时由网页 JS 上报）。"""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO preload_pending (mailid, ts) VALUES (?,?)",
            (mailid, int(time.time())),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def pending_preload():
    conn = _connect()
    try:
        cur = conn.execute("SELECT mailid FROM preload_pending ORDER BY ts ASC")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def done_preload(mailid):
    conn = _connect()
    try:
        conn.execute("DELETE FROM preload_pending WHERE mailid=?", (mailid,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            self._send(400, {"ok": False, "error": "bad json"})
            return
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        # 预读登记（边勾边读）：仅需 mailid
        if path == "/check":
            mid = str(data.get("mailid", "")).strip()
            if not mid:
                self._send(400, {"ok": False, "error": "mailid required"})
                return
            ok = enqueue_preload(mid)
            self._send(200, {"ok": ok, "mailid": mid})
            return
        # 预读完成通知
        if path == "/done":
            mid = str(data.get("mailid", "")).strip()
            ok = done_preload(mid) if mid else False
            self._send(200, {"ok": ok, "mailid": mid})
            return
        # 默认：Message-ID 登记
        mailid = str(data.get("mailid", "")).strip()
        message_id = str(data.get("message_id", "")).strip()
        if not mailid or not message_id:
            self._send(400, {"ok": False, "error": "mailid/message_id required"})
            return
        ok = upsert(mailid, message_id,
                    str(data.get("subject", "")),
                    str(data.get("time", "")))
        self._send(200, {"ok": ok, "mailid": mailid, "message_id": message_id})

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            self._send(200, {"ok": True})
        elif u.path == "/get":
            mid = (q.get("mailid") or [""])[0].strip()
            row = get(mid)
            if row:
                self._send(200, {"ok": True, "data": row})
            else:
                self._send(404, {"ok": False, "error": "not found"})
        elif u.path == "/all":
            self._send(200, {"ok": True, "data": all_records()})
        elif u.path == "/pending":
            self._send(200, {"ok": True, "data": pending_preload()})
        else:
            self._send(404, {"ok": False, "error": "unknown"})
        self._save = None

    def _save(self, *a):
        pass


_server = None
_thread = None


def start_server():
    """启动本机服务（线程）。可重复调用，幂等。"""
    global _server, _thread
    if _thread and _thread.is_alive():
        return True
    init_db()
    try:
        _server = HTTPServer((HOST, PORT), _Handler)
    except OSError:
        # 端口可能已被占用（上次实例未退出）：尝试直接复用
        return False
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    return True


def stop_server():
    global _server, _thread
    if _server:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None
    _thread = None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    init_db()
    print(f"服务启动: http://{HOST}:{PORT}  (DB: {DB_FILE})")
    start_server()
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_server()
        print("已停止")
