"""downloader 命名与筛选函数测试。"""
import email
from email.message import EmailMessage

from app.engine.downloader import (
    build_filename, date_label, is_invoice_mail, normalize_date,
    parse_original_name, unique_path,
)


class TestIsInvoiceMail:
    def test_skip_report(self):
        assert is_invoice_mail("工作日报") is False
        assert is_invoice_mail("今日日报") is False

    def test_invoice_keyword(self):
        assert is_invoice_mail("【电子发票】xx") is True
        assert is_invoice_mail("行程单通知") is True

    def test_sender_keyword(self):
        assert is_invoice_mail("itinerary@ridesharing.amap.com 行程单") is True

    def test_normal(self):
        assert is_invoice_mail("普通邮件") is False


class TestNormalizeDate:
    def test_padded(self):
        assert normalize_date("2026-8-6") == "2026-08-06"

    def test_invalid(self):
        assert normalize_date("") == ""
        assert normalize_date("not-a-date") == ""


class TestDateLabel:
    def test_label(self):
        assert date_label("2026-08-06") == "8.6号"

    def test_invalid(self):
        assert date_label("") == ""


class TestParseOriginalName:
    def test_company_and_amount(self):
        company, amount = parse_original_name("【测试公司】31.27元")
        assert company == "测试公司"
        assert amount == "31.27"

    def test_no_match(self):
        company, amount = parse_original_name("普通文件名.pdf")
        assert company == ""
        assert amount == ""


class TestBuildFilename:
    def test_basic(self):
        name = build_filename("电子发票", "【测试公司】31.27元.pdf", "2026-08-06")
        assert name == "8.6号_电子发票_31.27.pdf"

    def test_itinerary(self):
        name = build_filename("行程单", "行程单.pdf", "2026-08-11")
        assert name == "8.11号_行程单_0.00.pdf"

    def test_kind_label(self):
        name = build_filename("酒店发票", "酒店.pdf", "2026-08-11")
        assert name == "8.11号_酒店发票_0.00.pdf"

    def test_railway(self):
        rw = {"issue_date": "2026-08-11", "date": "2026-08-09",
              "route": "上海虹桥-杭州东", "amount": 87.0}
        name = build_filename("高铁发票", "高铁.pdf", "2026-08-11", rw=rw)
        assert name == "8月11_高铁_上海虹桥-杭州东_87.00.pdf"

    def test_railway_no_rw(self):
        name = build_filename("高铁发票", "高铁.pdf", "2026-08-11")
        assert name == "8.11号_高铁发票_0.00.pdf"


class TestUniquePath:
    def test_no_collision(self, tmp_path):
        assert unique_path(str(tmp_path), "a.pdf") == str(tmp_path / "a.pdf")

    def test_collision(self, tmp_path):
        (tmp_path / "a.pdf").write_bytes(b"x")
        assert unique_path(str(tmp_path), "a.pdf") == str(tmp_path / "a_1.pdf")
        (tmp_path / "a_1.pdf").write_bytes(b"x")
        assert unique_path(str(tmp_path), "a.pdf") == str(tmp_path / "a_2.pdf")