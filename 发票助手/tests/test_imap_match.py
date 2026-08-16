"""imap_engine 匹配辅助函数测试（无需 IMAP 连接）。"""
import datetime
import email
from email.message import EmailMessage

from app.engine.imap_engine import ImapEngine, _time_diff


def _make_msg_date(days_delta, h=11, m=59):
    """生成相对今天偏移 days_delta 天的邮件 Date 头。"""
    dt = datetime.date.today() + datetime.timedelta(days=days_delta)
    d = datetime.datetime(dt.year, dt.month, dt.day, h, m, 0)
    return email.utils.format_datetime(d)


def _msg(date_str):
    msg = EmailMessage()
    msg["Date"] = date_str
    return msg


class TestTimeDiff:
    def test_same_day_exact(self):
        msg = _msg(_make_msg_date(0, 11, 59))
        assert _time_diff(msg, "11:59") == 0

    def test_same_day_off_by_few_min(self):
        msg = _msg(_make_msg_date(0, 12, 3))
        assert _time_diff(msg, "12:00") == 3

    def test_yesterday_matches_previous_day(self):
        msg = _msg(_make_msg_date(-1, 11, 59))
        assert _time_diff(msg, "昨天 11:59") == 0

    def test_yesterday_rejects_same_day(self):
        msg = _msg(_make_msg_date(0, 11, 59))
        assert _time_diff(msg, "昨天 11:59") == 24 * 60

    def test_yesterday_rejects_old_mail(self):
        msg = _msg(_make_msg_date(-15, 11, 59))
        assert _time_diff(msg, "昨天 11:59") > 24 * 60

    def test_day_before_yesterday(self):
        msg = _msg(_make_msg_date(-2, 15, 30))
        assert _time_diff(msg, "前天 15:30") == 0

    def test_unparseable_returns_9999(self):
        msg = _msg(_make_msg_date(-1, 11, 59))
        assert _time_diff(msg, "刚刚") == 9999
        assert _time_diff(msg, "") == 9999
        assert _time_diff(msg, "昨天") == 9999


class TestBuildCriteriaFrom:
    def test_full_email_not_truncated(self):
        """完整邮箱地址必须原样保留，不能砍成 @ 前的关键词。"""
        eng = ImapEngine("a", "b")
        crit, verify = eng._build_criteria({"from": "fapiao@mailgate.hongyibo.com.cn"})
        assert "fapiao@mailgate.hongyibo.com.cn" in crit
        assert verify == ("from", "fapiao@mailgate.hongyibo.com.cn")

    def test_keyword_fallback_keeps_keyword(self):
        eng = ImapEngine("a", "b")
        crit, verify = eng._build_criteria({"from": "fapiao@"})
        assert "fapiao" in crit
        assert verify == ("from", "fapiao")


class TestVerifyFrom:
    def _mk(self, subject="", from_=""):
        msg = EmailMessage()
        if subject:
            msg["Subject"] = subject
        if from_:
            msg["From"] = from_
        return msg

    def test_wrong_sender_rejected(self):
        """勾选花小猪（fapiao@mailgate.hongyibo.com.cn），
        浙江通行费（fapiao@zjetc.net）必须被拒绝。"""
        eng = ImapEngine("a", "b")
        feat = {"from": "fapiao@mailgate.hongyibo.com.cn",
                "subject": "第三方发票及行程单",
                "keywords": ["第三方发票及行程单", "花小猪打车", "发票"],
                "text": "第三方发票及行程单 花小猪打车 发票"}
        msg = self._mk(subject="浙江通行费电子发票_5dbad55a", from_="fapiao@zjetc.net")
        assert eng._verify_match(feat, msg, ("from", "fapiao@mailgate.hongyibo.com.cn")) is False

    def test_correct_sender_accepted(self):
        """勾选花小猪，如祺出行（fapiao@mailgate.hongyibo.com.cn）应通过。"""
        eng = ImapEngine("a", "b")
        feat = {"from": "fapiao@mailgate.hongyibo.com.cn",
                "subject": "第三方发票及行程单",
                "keywords": ["第三方发票及行程单", "花小猪打车", "发票"],
                "text": "第三方发票及行程单 花小猪打车 发票"}
        msg = self._mk(subject="第三方发票及行程单",
                       from_="如祺出行 <fapiao@mailgate.hongyibo.com.cn>")
        assert eng._verify_match(feat, msg, ("from", "fapiao@mailgate.hongyibo.com.cn")) is True


class TestIndexMatch:
    """IMAP 侧全量 Message-ID 索引的离线匹配测试。"""

    def _mk_entry(self, num="1", folder="INBOX", message_id="<mid1@qq.com>",
                  subject="", from_addr="", date_raw=""):
        import datetime as _dt
        if not date_raw:
            d = _dt.datetime.now() - _dt.timedelta(hours=1)
            date_raw = email.utils.format_datetime(d)
        return {"num": num, "folder": folder, "message_id": message_id,
                "subject": subject, "from_addr": from_addr,
                "date": "", "date_raw": date_raw}

    def test_message_id_exact(self):
        eng = ImapEngine("a", "b")
        feat = {"message_id": "<mid1@qq.com>", "text": "发票", "time": ""}
        index = [self._mk_entry(message_id="<mid1@qq.com>", subject="电子发票"),
                 self._mk_entry(num="2", message_id="<mid2@qq.com>", subject="电子发票")]
        e = eng._index_match(feat, index, set(), [])
        assert e is not None and e["message_id"] == "<mid1@qq.com>"

    def test_message_id_strips_angle_brackets(self):
        eng = ImapEngine("a", "b")
        feat = {"message_id": "mid1@qq.com", "text": "发票", "time": ""}
        index = [self._mk_entry(message_id="<mid1@qq.com>", subject="电子发票")]
        e = eng._index_match(feat, index, set(), [])
        assert e is not None

    def test_hash_matches_subject(self):
        eng = ImapEngine("a", "b")
        feat = {"hash": "5dbad55a", "text": "浙江通行费电子发票_5dbad55a", "time": ""}
        index = [self._mk_entry(subject="浙江通行费电子发票_5dbad55a"),
                 self._mk_entry(num="2", subject="浙江通行费电子发票_aaaa"),
                 self._mk_entry(num="3", subject="第三方发票及行程单")]
        e = eng._index_match(feat, index, set(), [])
        assert e is not None and e["num"] == "1"

    def test_from_address_distinguishes(self):
        eng = ImapEngine("a", "b")
        feat = {"from": "fapiao@mailgate.hongyibo.com.cn",
                "subject": "第三方发票及行程单",
                "keywords": ["第三方发票及行程单", "花小猪打车"],
                "text": "第三方发票及行程单 花小猪打车"}
        index = [self._mk_entry(num="1", subject="浙江通行费电子发票_5dbad55a",
                                from_addr="fapiao@zjetc.net"),
                 self._mk_entry(num="2", subject="第三方发票及行程单",
                                from_addr="如祺出行 <fapiao@mailgate.hongyibo.com.cn>")]
        e = eng._index_match(feat, index, set(), [])
        assert e is not None and e["num"] == "2"

    def test_already_downloaded_skipped(self):
        eng = ImapEngine("a", "b")
        feat = {"hash": "5dbad55a", "text": "浙江通行费电子发票_5dbad55a", "time": ""}
        index = [self._mk_entry(num="1", subject="浙江通行费电子发票_5dbad55a")]
        e = eng._index_match(feat, index, {("1", "INBOX")}, [])
        assert e is None

    def test_no_match_returns_none(self):
        eng = ImapEngine("a", "b")
        feat = {"from": "fapiao@mailgate.hongyibo.com.cn", "subject": "第三方发票及行程单",
                "keywords": ["第三方发票及行程单"], "text": "第三方发票及行程单"}
        index = [self._mk_entry(subject="浙江通行费电子发票_5dbad55a",
                                from_addr="fapiao@zjetc.net")]
        e = eng._index_match(feat, index, set(), [])
        assert e is None
