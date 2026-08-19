"""TaskStore 任务状态机单测：状态流转 / 幂等 / 崩溃恢复 / 审计。"""

import os
import tempfile

from app.engine.task_store import TaskStore


def _store(tmp_path):
    db = os.path.join(tmp_path, "tasks.db")
    return TaskStore(db)


def test_state_flow(tmp_path):
    st = _store(tmp_path)
    url = "/attach/download?name=a.pdf"
    assert st.file_start(url, "m1", "主题", "C:/tmp/a.pdf") is True
    st.file_finish(url, "C:/save/a.pdf", sha256="abc", size=10, amount=31.27,
                   trip_date="2026-08-06", kind="电子发票")
    assert st.has_archived_url(url) is True
    # 已归档后再次 start 应拒绝（幂等）
    assert st.file_start(url, "m2", "主题2", "C:/tmp/b.pdf") is False


def test_file_fail(tmp_path):
    st = _store(tmp_path)
    url = "/attach/download?name=b.pdf"
    st.file_start(url, "m1", "", "C:/tmp/b.pdf")
    st.file_fail(url, reason="下载失败")
    assert st.has_archived_url(url) is False


def test_cleanup_interrupted(tmp_path):
    st = _store(tmp_path)
    url = "/attach/download?name=c.pdf"
    fake_tmp = os.path.join(tmp_path, "c.pdf")
    with open(fake_tmp, "wb") as f:
        f.write(b"%PDF-1.4 fake")
    st.file_start(url, "m1", "", fake_tmp)
    cleaned = st.cleanup_interrupted()
    assert cleaned == 1
    assert not os.path.exists(fake_tmp)  # 残留临时文件被清理
    assert st.has_archived_url(url) is False


def test_mail_flow(tmp_path):
    st = _store(tmp_path)
    st.mail_start("mailid-1", "主题", "发件人")
    st.mail_finish("mailid-1", total_amount=178.79, files_count=2, ok=True)
    st.archive_record("178.79元", 178.79, 2)
    stats = st.extract_stats()
    assert stats["total"] == 0  # 无 archived 文件
    # 归档文件统计
    st.file_start("/x.pdf", "mailid-1", "", "t.pdf")
    st.file_finish("/x.pdf", "s.pdf", amount=100.0, trip_date="2026-08-06")
    st.file_start("/y.pdf", "mailid-1", "", "t2.pdf")
    st.file_finish("/y.pdf", "s2.pdf", amount=None, trip_date="")
    stats = st.extract_stats()
    assert stats["total"] == 2
    assert stats["amount_ok"] == 1
    assert stats["date_ok"] == 1