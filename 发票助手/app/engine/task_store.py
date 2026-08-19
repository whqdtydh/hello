"""SQLite 任务状态机（任务1：数据可靠性）。

记录下载任务（邮件/文件/归档）状态，提供：
- 幂等去重：同 URL 已归档则不再重复下载
- 崩溃恢复：进程中断后清理残留临时文件，任务标记 failed 可重下
- 审计统计：金额/日期提取率（驱动规则改进）

DB 位置：~/.invoice_assistant/tasks.db
线程安全：单连接 + 锁（下载管线多线程并发写）。
"""

import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mails (
  mailid TEXT PRIMARY KEY,
  subject TEXT, sender TEXT, mail_date TEXT,
  state TEXT DEFAULT 'queued',          -- queued/downloading/done/failed
  total_amount REAL DEFAULT 0,
  files_count INT DEFAULT 0, downloaded_count INT DEFAULT 0,
  retries INT DEFAULT 0, fail_reason TEXT,
  created_at TEXT, done_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  mailid TEXT,
  subject TEXT,
  state TEXT DEFAULT 'pending',         -- pending/downloading/archived/failed
  tmp_path TEXT, final_path TEXT,
  sha256 TEXT, size INT,
  amount REAL, trip_date TEXT, kind TEXT,
  retries INT DEFAULT 0, fail_reason TEXT,
  created_at TEXT, done_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_url_sha ON files(url, sha256);
CREATE TABLE IF NOT EXISTS archives (
  folder_name TEXT PRIMARY KEY,
  total_amount REAL, files_count INT, created_at TEXT
);
"""


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class TaskStore:
    """下载任务状态记录与恢复。所有方法线程安全。"""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(
            os.path.expanduser("~"), ".invoice_assistant", "tasks.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ---------- 邮件任务 ----------
    def mail_start(self, mailid, subject="", sender="", mail_date=""):
        if not mailid:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO mails(mailid, subject, sender, mail_date, state, created_at) "
                "VALUES(?,?,?,?, 'downloading', ?)",
                (mailid, subject, sender, mail_date, _now()))
            self._conn.commit()

    def mail_finish(self, mailid, total_amount=0.0, files_count=0, ok=True, reason=""):
        if not mailid:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE mails SET state=?, total_amount=?, files_count=?, "
                "downloaded_count=?, fail_reason=?, done_at=? WHERE mailid=?",
                ("done" if ok else "failed", total_amount, files_count,
                 files_count if ok else 0, reason, _now(), mailid))
            self._conn.commit()

    # ---------- 文件任务 ----------
    def has_archived_url(self, url):
        """同 URL 是否已有成功归档记录（幂等去重）。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM files WHERE url=? AND state='archived' LIMIT 1", (url,))
            return cur.fetchone() is not None

    def file_start(self, url, mailid="", subject="", tmp_path=""):
        """记录文件开始下载（幂等：已归档过的 URL 不重复记录）。返回是否应继续。"""
        if self.has_archived_url(url):
            return False
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO files(url, mailid, subject, state, tmp_path, created_at) "
                "VALUES(?,?,?, 'downloading', ?, ?)",
                (url, mailid, subject, tmp_path, _now()))
            self._conn.commit()
        return True

    def file_finish(self, url, final_path, sha256="", size=0, amount=None,
                    trip_date="", kind=""):
        """文件归档完成。"""
        with self._lock:
            self._conn.execute(
                "UPDATE files SET state='archived', final_path=?, sha256=?, size=?, "
                "amount=?, trip_date=?, kind=?, done_at=? WHERE url=? AND state='downloading'",
                (final_path, sha256, size, amount, trip_date, kind, _now(), url))
            self._conn.commit()

    def file_fail(self, url, reason=""):
        with self._lock:
            self._conn.execute(
                "UPDATE files SET state='failed', fail_reason=?, done_at=? "
                "WHERE url=? AND state IN ('downloading','pending')",
                (reason, _now(), url))
            self._conn.commit()

    # ---------- 归档 ----------
    def archive_record(self, folder_name, total_amount=0.0, files_count=0):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO archives(folder_name, total_amount, files_count, created_at) "
                "VALUES(?,?,?,?)",
                (folder_name, total_amount, files_count, _now()))
            self._conn.commit()

    # ---------- 崩溃恢复 ----------
    def cleanup_interrupted(self):
        """启动时调用：处理上次进程中断留下的半成品。

        - state=downloading 且临时文件已不存在 → 标 failed（可重下）
        - state=downloading 且临时文件仍在（临时目录残留）→ 删除残留文件并标 failed
        - 返回清理数量（供日志）。
        """
        cleaned = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, url, tmp_path FROM files WHERE state='downloading'").fetchall()
            for fid, url, tmp_path in rows:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)  # 残留临时文件（进程中断）
                    except Exception:
                        pass
                    cleaned += 1
                self._conn.execute(
                    "UPDATE files SET state='failed', fail_reason='进程中断，可重新勾选下载', "
                    "done_at=? WHERE id=?", (_now(), fid))
            self._conn.commit()
        return cleaned

    # ---------- 审计 ----------
    def extract_stats(self, limit=200):
        """最近 N 个文件的金额/日期提取率（amount>0 / trip_date 非空 比例）。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT amount, trip_date FROM files WHERE state='archived' "
                "ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        if not rows:
            return {"total": 0, "amount_ok": 0, "date_ok": 0,
                    "amount_rate": 0.0, "date_rate": 0.0}
        amount_ok = sum(1 for a, _ in rows if a)
        date_ok = sum(1 for _, d in rows if d)
        return {"total": len(rows), "amount_ok": amount_ok, "date_ok": date_ok,
                "amount_rate": amount_ok / len(rows), "date_rate": date_ok / len(rows)}