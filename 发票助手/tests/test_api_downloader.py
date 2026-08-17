"""api_downloader 纯函数测试：命名、链接提取、发票号码、URL 构造。"""

import os
import sys

import pytest

from app.engine.api_downloader import (
    QQMailApi,
    build_filename,
    extract_51fapiao_links,
    extract_alipay_links,
    extract_amount_from_text,
    extract_invoice_no,
    extract_oss_links,
    failed_folder_name,
    is_invoice_mail,
    parse_original_name,
    rename_dir_with_amount,
    unique_dir,
)


class TestParseOriginalName:
    def test_amount_with_yuan(self):
        company, amount = parse_original_name("【测试公司】31.27元")
        assert company == "测试公司"
        assert amount == "31.27"

    def test_amount_without_yuan(self):
        # 华铁/51发票正文：价税合计金额为33.00的电子发票（无「元」字）
        company, amount = parse_original_name(
            "【上海华铁旅客服务有限公司】价税合计金额为33.00的电子发票")
        assert amount == "33.00"

    def test_no_match(self):
        company, amount = parse_original_name("普通文件名.pdf")
        assert company == ""
        assert amount == ""


class TestBuildFilename:
    def test_basic(self):
        name = build_filename("电子发票", "【测试公司】31.27元.pdf", "2026-08-06")
        assert name == "8.6号_电子发票_31.27.pdf"

    def test_huatie(self):
        name = build_filename(
            "电子发票",
            "【上海华铁旅客服务有限公司】价税合计金额为33.00的电子发票"
            "[购方名称:小核智能机器人（杭州）有限公司 发票号码:11901140]",
            "2026-08-06")
        assert name == "8.6号_电子发票_33.00.pdf"

    def test_itinerary(self):
        name = build_filename("行程单", "行程单.pdf", "2026-08-11")
        assert name == "8.11号_行程单_0.00.pdf"

    def test_kind_label(self):
        name = build_filename("酒店发票", "酒店.pdf", "2026-08-11")
        assert name == "8.11号_酒店发票_0.00.pdf"

    def test_railway(self):
        # 高铁发票用乘车日期 date（消费日期）命名，优先于开票日期 issue_date
        rw = {"issue_date": "2026-08-11", "date": "2026-08-09",
              "route": "上海虹桥-杭州东", "amount": 87.0}
        name = build_filename("高铁发票", "高铁.pdf", "2026-08-11", rw=rw)
        assert name == "8月9_高铁_上海虹桥-杭州东_87.00.pdf"

    def test_railway_no_date(self):
        # 只有开票日期时退回 issue_date
        rw = {"issue_date": "2026-08-11", "date": "", "route": "上海虹桥-杭州东", "amount": 87.0}
        name = build_filename("高铁发票", "高铁.pdf", "2026-08-11", rw=rw)
        assert name == "8月11_高铁_上海虹桥-杭州东_87.00.pdf"

    def test_railway_no_rw(self):
        name = build_filename("高铁发票", "高铁.pdf", "2026-08-11")
        assert name == "8.11号_高铁发票_0.00.pdf"

    def test_msg_amount_text(self):
        # 纯文本 msg（API 路径）：价税合计金额为33.00 → 应提取价格
        name = build_filename("电子发票", "未命名.pdf", "2026-08-06",
                              msg="您的电子发票已开具成功，价税合计金额为33.00的电子发票")
        assert name == "8.6号_电子发票_33.00.pdf"

    def test_extract_amount_filename(self):
        # 从文件名提取金额（含去重后缀 _1）
        assert extract_amount_from_text("6.15号_打车发票_19.96.pdf") == 19.96
        assert extract_amount_from_text("6.15号_行程单_19.96_1.pdf") == 19.96
        assert extract_amount_from_text("8.13号_行程单_0.00.pdf") == 0.0

    def test_invoice_kind_attname_priority(self):
        # 附件名优先：正文含"行程单"说明词，但发票附件应识别为"打车发票"
        from app.engine.mail_parse import invoice_kind
        body = "您的电子行程单已生成，请查看附件。高德打车为您服务。"
        kind = invoice_kind(subj="高德打车电子发票",
                            att_name="【鹿鹿达出行-19.96元-1个行程】高德打车电子发票.pdf",
                            body=body)
        assert kind == "打车发票"
        kind2 = invoice_kind(subj="高德打车电子发票",
                             att_name="【鹿鹿达出行-19.96元-1个行程】高德打车电子行程单.pdf",
                             body=body)
        assert kind2 == "电子行程单"

    def test_flight_sender_priority(self):
        # 航空公司/航旅平台发件人 → 机票发票（即使主题含"电子行程单"）
        from app.engine.mail_parse import invoice_kind
        kind = invoice_kind(subj="【电子行程单PDF版】- 行程单号：26948731111043132568",
                            att_name="电子行程单_26948731111043132568.pdf",
                            body="您的电子行程单已开具成功",
                            sender="航旅官方邮箱 umetrip@travelsky.com")
        assert kind == "机票发票"
        name = build_filename(kind, "电子行程单_26948731111043132568.pdf", "2026-06-15",
                              msg="行程单金额：1100.00元")
        assert name == "6.15号_机票发票_1100.00.pdf", name
        # 非航空发件人不受影响
        kind2 = invoice_kind(subj="高德打车电子发票",
                             att_name="【T3出行-31.04元-1个行程】高德打车电子发票.pdf",
                             body="您的电子行程单已生成",
                             sender="高德打车 service@amap.com")
        assert kind2 == "打车发票"


class TestLinkExtraction:
    def test_51fapiao(self):
        content = '<a href="https://dlj.51fapiao.cn/dlj/v7/abc123def">下载</a>'
        links = extract_51fapiao_links(content)
        assert len(links) == 1
        assert "abc123def" in links[0]

    def test_alipay(self):
        content = ('<a href="https://mdn.alipayobjects.com/aliinvoicecore/uri/file/'
                   'as/tcn/x/invoice_26317200000007265338.pdf?af_fileName=a.pdf">PDF</a>')
        links = extract_alipay_links(content)
        assert len(links) == 1
        assert ".pdf" in links[0]

    def test_oss(self):
        # 淘宝闪购商家发票：阿里云 OSS jpg，带签名参数
        content = ('<a class="link" href="https://fin-invoice-zbprod-zb1-oss-1.'
                   'oss-cn-zhangjiakou.aliyuncs.com/cInvoice/manualInvoice/'
                   'manualInvoiceForC-c97cc4a0-7d25-4b2f-bacf-43223d30efd8.jpg'
                   '?Expires=3357946163&OSSAccessKeyId=LTAIsLzAMnljb8cj'
                   '&Signature=m9N2VRJOgOwG2fHkwXe5r40pfn4%3D">查看电子发票文件</a>')
        links = extract_oss_links(content)
        assert len(links) == 1
        assert "oss-cn-zhangjiakou.aliyuncs.com" in links[0]
        assert links[0].endswith("pfn4%3D")

    def test_oss_html_entity(self):
        # &amp; 实体转义也应还原提取
        content = ('<a href="https://fin-invoice-zbprod-zb1-oss-1.'
                   'oss-cn-hangzhou.aliyuncs.com/cInvoice/x/a.png?Expires=1'
                   '&amp;OSSAccessKeyId=K&amp;Signature=S">x</a>')
        links = extract_oss_links(content)
        assert len(links) == 1
        assert "Expires=1&OSSAccessKeyId=K&Signature=S" in links[0]

    def test_empty(self):
        assert extract_51fapiao_links("") == []
        assert extract_alipay_links("") == []
        assert extract_oss_links("") == []


class TestInvoiceNo:
    def test_colon(self):
        assert extract_invoice_no("【电子发票】发票号码:11901140") == "11901140"

    def test_fullwidth(self):
        assert extract_invoice_no("【电子发票】发票号码：26317200000007265338") == "26317200000007265338"

    def test_none(self):
        assert extract_invoice_no("普通邮件") == ""


class TestIsInvoiceMail:
    def test_invoice(self):
        assert is_invoice_mail("电子发票")

    def test_itinerary(self):
        assert is_invoice_mail("电子行程单")

    def test_not_invoice(self):
        assert not is_invoice_mail("工作日报")


class TestQQMailApiUrl:
    def test_sid_appended(self):
        api = QQMailApi(None, "test_sid_123", on_log=lambda m: None)
        u = api._url("/attach/download?mailid=m1&fileid=f1")
        assert "sid=test_sid_123" in u
        assert "mailid=m1" in u
        assert "fileid=f1" in u


class TestFailedFolder:
    def test_name_with_date(self):
        name = failed_folder_name("【电子发票】上海华铁旅客服务有限公司", "2026-08-06", "dzfp@51fapiao.cloud")
        assert name.startswith("8.6号_")
        assert "华铁" in name
        assert "51fapiao" in name

    def test_name_sanitize(self):
        # 非法字符被替换
        name = failed_folder_name('发票: 测试/公司*名称?', "2026-08-06")
        assert "/" not in name
        assert "*" not in name
        assert "?" not in name

    def test_name_truncate(self):
        name = failed_folder_name("很长的主题" * 20, "2026-08-06")
        assert len(name) <= 60

    def test_unique_dir(self, tmp_path):
        d1 = unique_dir(str(tmp_path), "未下载邮件")
        os.makedirs(d1)
        d2 = unique_dir(str(tmp_path), "未下载邮件")
        assert d1 != d2
        assert d2.endswith("_1")


class TestAmountExtract:
    def test_yuan(self):
        assert extract_amount_from_text("【滴滴出行】82.94元") == 82.94

    def test_no_yuan(self):
        assert extract_amount_from_text("【华铁】价税合计金额为33.00的电子发票") == 33.00

    def test_in_zip_name(self):
        assert extract_amount_from_text("浙ABD5103+202608121230+21.00元.zip") == 21.00

    def test_none(self):
        assert extract_amount_from_text("普通邮件") == 0.0

    def test_first_attach_only(self):
        # 一封邮件含发票+行程单两个附件，金额应只取一次（模拟 _download_attaches 逻辑）
        names = [
            "【旅程约车特选-82.94元-1个行程】高德打车电子发票.pdf",
            "【旅程约车特选-82.94元-1个行程】高德打车电子行程单.pdf",
        ]
        mail_amount = 0.0
        for name in names:
            if not mail_amount:
                mail_amount = extract_amount_from_text(name)
        assert mail_amount == 82.94
        assert abs(mail_amount - 82.94) < 0.001

    def test_zip_name(self):
        assert extract_amount_from_text("浙ABD5103[渐变绿]+202608121230+21.00元.zip") == 21.00

    def test_itinerary_with_amount(self):
        # 行程单附件名含金额 → 命名应带价格
        name = build_filename("电子行程单", "【旅程约车特选-82.94元-1个行程】高德打车电子行程单.pdf", "2026-08-13")
        assert name == "8.13号_行程单_82.94.pdf"

    def test_zip_pdf_without_amount_hint(self):
        # zip 内 PDF 无金额，用外层金额补（模拟 _extract_pdfs_from_zip 的拼接逻辑）
        base = "行程单.pdf"
        hint = "21.00元"
        if not extract_amount_from_text(base):
            base = f"{base} {hint}"
        name = build_filename("电子行程单", base, "2026-08-13")
        assert name == "8.13号_行程单_21.00.pdf"


class TestInvoiceKindNaming:
    def test_taxi(self):
        # 打车 → 打车发票
        kind = "打车发票"
        name = build_filename(kind, "【曹操出行-67.33元-1个行程】高德打车电子发票.pdf", "2026-08-13")
        assert name == "8.13号_打车发票_67.33.pdf"

    def test_highway(self):
        # 高速 → 高速发票
        kind = "高速发票"
        name = build_filename(kind, "浙ABD5103+202608121230+21.00元.zip", "2026-08-13")
        assert name == "8.13号_高速发票_21.00.pdf"

    def test_railway(self):
        # 高铁 → 高铁发票
        kind = "高铁发票"
        name = build_filename(kind, "高铁.pdf", "2026-08-13")
        assert name == "8.13号_高铁发票_0.00.pdf"

    def test_invoice_kind_dispatch(self):
        # 综合判断：附件名含「曹操出行」→ 打车发票
        from app.engine.mail_parse import invoice_kind
        kind = invoice_kind(subj="高德打车电子发票", att_name="【曹操出行-67.33元】.pdf", body="")
        assert kind == "打车发票"
        # 高速费
        kind2 = invoice_kind(subj="浙江通行费电子发票", att_name="21.00元.zip", body="")
        assert kind2 == "高速发票"
        # 华铁（高铁餐车餐饮服务商）→ 餐饮发票
        kind3 = invoice_kind(subj="【上海华铁旅客服务有限公司】价税合计金额为33.00的电子发票", att_name="", body="")
        assert kind3 == "餐饮发票"

    def test_51fapiao_wbr_extract(self):
        # QQ 邮箱正文长链接被 <wbr> 拆断 → 应恢复完整 36 位 id
        content = ('<a href="https://dlj.51fapiao.cn/dlj/v7/06d7d3864834101af76c6f5e40368b367c2264">'
                   'https://dlj.51fapiao.cn/dlj/v7/06d7d3864834101af7<wbr>6c6f5e40368<wbr>b367c2264</a>')
        links = extract_51fapiao_links(content)
        assert links
        assert "b367c2264" in links[0]

    def test_huatie_naming(self):
        # 华铁（高铁餐车餐饮）→ 餐饮发票，命名带价格
        subj = ("【电子发票】您收到一张来自【上海华铁旅客服务有限公司】"
                "价税合计金额为33.00的电子发票[购方名称:小核智能机器人（杭州）有限公司 发票号码:11901140]")
        from app.engine.mail_parse import invoice_kind
        kind = invoice_kind(subj=subj)
        assert kind == "餐饮发票"
        name = build_filename(kind, subj, "2026-08-06", msg="")
        assert name == "8.6号_餐饮发票_33.00.pdf", name


class TestRenameDirWithAmount:
    def test_create(self, tmp_path):
        sub = rename_dir_with_amount(str(tmp_path), 128.50)
        assert os.path.basename(sub) == "128.50元"
        assert os.path.isdir(sub)