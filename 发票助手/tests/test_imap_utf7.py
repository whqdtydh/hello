"""imap_utf7 编解码测试。"""
from app.engine import imap_utf7


class TestEncode:
    def test_ascii_unchanged(self):
        assert imap_utf7.encode("INBOX") == "INBOX"

    def test_chinese(self):
        assert imap_utf7.encode("收件箱") == "&ZTZO9nux-"

    def test_ampersand(self):
        assert imap_utf7.encode("A&B") == "A&-B"

    def test_plus(self):
        assert imap_utf7.encode("A+B") == "A+-B"

    def test_empty(self):
        assert imap_utf7.encode("") == ""


class TestDecode:
    def test_ascii_unchanged(self):
        assert imap_utf7.decode("INBOX") == "INBOX"

    def test_chinese(self):
        assert imap_utf7.decode("&ZTZO9nux-") == "收件箱"

    def test_ampersand(self):
        assert imap_utf7.decode("A&-B") == "A&B"

    def test_unterminated(self):
        assert imap_utf7.decode("A&B") == "A&B"

    def test_empty(self):
        assert imap_utf7.decode("") == ""


class TestRoundtrip:
    def test_chinese_folders(self):
        for name in ("收件箱", "报销", "已发送", "草稿箱", "其他文件夹/邮件归档"):
            assert imap_utf7.decode(imap_utf7.encode(name)) == name