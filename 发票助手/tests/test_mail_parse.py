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


class TestKindFromPdf:
    """票面识别：从 PDF 文本（项目名称/票面标识）判断发票类型。"""

    def test_railway(self):
        t = "国家税务总局 全国统一发票监制章 买票请到12306 电子发票（铁路电子客票） 票价:￥87.00"
        assert mail_parse.kind_from_pdf(t) == "高铁发票"

    def test_air(self):
        t = "电子发票（航空运输电子客票行程单） 旅客姓名 航班号 承运人"
        assert mail_parse.kind_from_pdf(t) == "机票发票"

    def test_highway(self):
        t = "浙江通行费电子发票 通行时间 2026-08-12 高速公路通行费 21.00元"
        assert mail_parse.kind_from_pdf(t) == "高速发票"

    def test_catering(self):
        t = "上海增值税电子普通发票 *生产生活服务*美式Iced American（冰） 金额31.13"
        assert mail_parse.kind_from_pdf(t) == "餐饮发票"

    def test_hotel(self):
        t = "电子发票 住宿服务*标准间 酒店 入住时间"
        assert mail_parse.kind_from_pdf(t) == "酒店发票"

    def test_taxi(self):
        t = "滴滴出行 电子发票 运输服务*客运服务费 行程单"
        assert mail_parse.kind_from_pdf(t) == "打车发票"

    def test_unknown_keep_original(self):
        # 识别不到返回空串，调用方保持原分类
        assert mail_parse.kind_from_pdf("普通电子发票 货物或应税劳务、服务名称 软件服务费") == ""
        assert mail_parse.kind_from_pdf("") == ""


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

    def test_ticket_amount_text(self):
        assert mail_parse.ticket_amount("价税合计金额为33.00") == 33.0
        assert mail_parse.ticket_amount("票价 87.00") == 87.0
        assert mail_parse.ticket_amount("33.00元") == 33.0
        assert mail_parse.ticket_amount("普通文本") == 0.0

    def test_non_railway(self):
        assert mail_parse.railway_info(_make_msg()) == {
            "date": "", "issue_date": "", "train": "", "route": "", "amount": 0.0,
        }


class TestConsumeDate:
    def test_行程时间(self):
        assert mail_parse.consume_date(
            "行程时间：2026-07-30 11:28 至 2026-07-30 11:44") == "2026-07-30"

    def test_通行时间(self):
        assert mail_parse.consume_date(
            "通行时间：2026-08-13 10:28 - 2026-08-13 10:54") == "2026-08-13"

    def test_消费日期标签(self):
        assert mail_parse.consume_date(
            "消费日期：2026年8月11日") == "2026-08-11"

    def test_无标签年月日(self):
        assert mail_parse.consume_date(
            "开票日期：2026年8月13日") == "2026-08-13"

    def test_点分日期(self):
        assert mail_parse.consume_date("行程日期：2026.08.04") == "2026-08-04"

    def test_空(self):
        assert mail_parse.consume_date("") == ""
        assert mail_parse.consume_date("尊敬的高德用户，感谢您使用打车服务") == ""

    def test_带标签优先于开票日期(self):
        # 既有消费日期又有开票日期，应取带标签的消费日期
        text = "行程时间：2026-07-30 11:28\n开票日期：2026年8月4日"
        assert mail_parse.consume_date(text) == "2026-07-30"