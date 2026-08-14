"""mail_parse 纯函数测试。"""
import email
from email.message import EmailMessage

from app.engine import mail_parse


def _make_msg(subject="", from_="", body="", attachments=None):
    msg = EmailMessage()
    if subject:
        msg["Subject"] = subject
    if from_:
        msg["From"] = from_
    msg["Date"] = "Tue, 11 Aug 2026 12:03:00 +0800"
    if attachments:
        msg.set_content(body or "")
        for name, data in attachments:
            msg.add_attachment(data, maintype="application",
                               subtype="octet-stream", filename=name)
    else:
        msg.set_payload(body or "")
    return msg


class TestDecodeMime:
    def test_utf8_b64(self):
        assert mail_parse.decode_mime("=?UTF-8?B?55S15a2Q5Y+R56Wo?=") == "电子发票"

    def test_plain_ascii(self):
        assert mail_parse.decode_mime("hello") == "hello"

    def test_empty(self):
        assert mail_parse.decode_mime("") == ""
        assert mail_parse.decode_mime(None) == ""


class TestHeaderDate:
    def test_valid(self):
        assert mail_parse.header_date(_make_msg()) == "2026-08-11"

    def test_empty_msg(self):
        assert mail_parse.header_date(email.message.Message()) == ""


class TestSubjectHash:
    def test_found(self):
        text = "浙江通行费电子发票_5dbad55aaabbccddeeff001122334455"
        assert mail_parse.subject_hash(text) == "5dbad55aaabbccddeeff001122334455"

    def test_not_found(self):
        assert mail_parse.subject_hash("普通邮件") == ""


class TestTextKeywords:
    def test_company_extracted(self):
        kws = mail_parse.text_keywords("【电子发票】来自【上海华铁旅客服务有限公司】价税合计")
        assert "上海华铁旅客服务有限公司" in kws

    def test_sorted_by_len(self):
        kws = mail_parse.text_keywords("【公司A】【很长很长的公司名】")
        assert kws[0] == "很长很长的公司名"

    def test_empty(self):
        assert mail_parse.text_keywords("") == []


class TestTextDate:
    def test_chinese_full(self):
        assert mail_parse.text_date("2026年8月11日") == "2026-08-11"

    def test_month_day(self):
        assert mail_parse.text_date("8月11日") == "08-11"

    def test_dash(self):
        assert mail_parse.text_date("2026-08-09") == "2026-08-09"

    def test_slash(self):
        assert mail_parse.text_date("2026/08/09") == "2026-08-09"

    def test_empty(self):
        assert mail_parse.text_date("") == ""
        assert mail_parse.text_date(None) == ""


class TestIsRailway:
    def test_positive(self):
        assert mail_parse.is_railway("12306网上购票信息") is True

    def test_negative(self):
        assert mail_parse.is_railway("普通邮件") is False


class TestInvoiceKind:
    def test_itinerary_priority(self):
        assert mail_parse.invoice_kind(subj="高铁行程单") == "电子行程单"

    def test_railway(self):
        assert mail_parse.invoice_kind(subj="12306网上购票") == "高铁发票"

    def test_highway(self):
        assert mail_parse.invoice_kind(subj="浙江通行费电子发票") == "高速发票"

    def test_taxi(self):
        assert mail_parse.invoice_kind(subj="打车出行") == "打车发票"

    def test_hotel(self):
        assert mail_parse.invoice_kind(subj="酒店住宿发票") == "酒店发票"

    def test_fallback(self):
        assert mail_parse.invoice_kind(subj="") == "电子发票"
        assert mail_parse.invoice_kind(subj="普通文本") == "电子发票"

    def test_att_name_source(self):
        assert mail_parse.invoice_kind(att_name="高铁.pdf") == "高铁发票"


class TestExtractAttachments:
    def test_extract(self):
        msg = _make_msg(attachments=[("82.94元.pdf", b"%PDF-1.4xx")])
        atts = mail_parse.extract_attachments(msg)
        assert len(atts) == 1
        assert atts[0][0] == "82.94元.pdf"
        assert atts[0][1] == b"%PDF-1.4xx"

    def test_none(self):
        assert mail_parse.extract_attachments(_make_msg()) == []


class TestAttAmount:
    def test_from_att_name(self):
        msg = _make_msg(attachments=[("82.94元.pdf", b"x")])
        assert mail_parse.att_amount(msg) == 82.94

    def test_zero(self):
        assert mail_parse.att_amount(_make_msg()) == 0.0


class TestRailwayInfo:
    HTML = (
        "<html><table>"
        "<tr><td>发票号</td><td>2026年8月9日</td><td>G901</td>"
        "<td>上海虹桥-杭州东</td><td>87.00</td><td class='amount'>87.00</td></tr>"
        "<tr><td colspan='6'>2026年8月11日</td></tr>"
        "</table></html>"
    )

    @staticmethod
    def _rail_msg():
        msg = EmailMessage()
        msg["Subject"] = "网上购票信息"
        msg["Date"] = "Tue, 11 Aug 2026 12:03:00 +0800"
        msg.set_content(TestRailwayInfo.HTML, subtype="html")
        return msg

    def test_fields(self):
        info = mail_parse.railway_info(self._rail_msg())
        assert info["date"] == "2026-08-09"
        assert info["issue_date"] == "2026-08-11"
        assert info["train"] == "G901"
        assert info["route"] == "上海虹桥-杭州东"
        assert info["amount"] == 87.0

    def test_ticket_amount(self):
        assert mail_parse.ticket_amount(self._rail_msg()) == 87.0

    def test_non_railway(self):
        assert mail_parse.railway_info(_make_msg()) == {
            "date": "", "issue_date": "", "train": "", "route": "", "amount": 0.0,
        }


class TestBodyText:
    def test_plain(self):
        msg = EmailMessage()
        msg.set_content("纯文本正文", charset="utf-8")
        assert mail_parse.body_text(msg).strip() == "纯文本正文"

    def test_html_stripped(self):
        msg = EmailMessage()
        msg.set_content("<p>正文内容</p><b>加粗</b>", subtype="html", charset="utf-8")
        body = mail_parse.body_text(msg)
        assert "正文内容" in body
        assert "加粗" in body
        assert "<p>" not in body


class TestAlipayPdfLink:
    def test_found(self):
        msg = EmailMessage()
        msg.set_content(
            '<a href="https://mdn.alipayobjects.com/uri/file/invoice.pdf">下载</a>',
            subtype="html")
        assert mail_parse.alipay_pdf_link(msg).endswith("invoice.pdf")

    def test_not_found(self):
        assert mail_parse.alipay_pdf_link(_make_msg()) == ""


class TestAlipayFilename:
    def test_af_filename(self):
        link = ("https://x.com/invoice.pdf?af_fileName="
                "%E8%AF%A5%E6%94%AF%E4%BB%98%E5%AE%9D.pdf")
        assert mail_parse.alipay_filename(link, "d.pdf") == "该支付宝.pdf"

    def test_path_fallback(self):
        assert mail_parse.alipay_filename("https://x.com/a/b/c.pdf", "d.pdf") == "c.pdf"

    def test_default(self):
        assert mail_parse.alipay_filename("https://x.com/noext", "d.pdf") == "d.pdf"