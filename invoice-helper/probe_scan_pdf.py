"""扫描件 PDF 全链路：PIL 画文字 → 渲染成无文本层 PDF → pdf_text 触发 OCR 降级。"""
import sys

sys.path.insert(0, r"D:\AI\git\invoice-helper")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw, ImageFont

font = None
for cand in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
             r"C:\Windows\Fonts\simsun.ttc"]:
    try:
        font = ImageFont.truetype(cand, 40)
        break
    except Exception:
        continue
img = Image.new("RGB", (1000, 200), "white")
d = ImageDraw.Draw(img)
d.text((40, 60), "铁路电子客票 票价:￥87.00", font=font, fill="black")
d.text((40, 120), "乘车日期 2026年08月06日", font=font, fill="black")
tmp_png = r"D:\AI\git\invoice-helper\_scan_test.png"
img.save(tmp_png)

# 用 PyMuPDF 把图片插成 PDF（无文本层 = 扫描件）
import pymupdf
doc = pymupdf.open()
page = doc.new_page(width=595, height=420)
page.insert_image(page.rect, filename=tmp_png)
scan_pdf = r"D:\AI\git\invoice-helper\_scan_test.pdf"
doc.save(scan_pdf)
doc.close()
import os
print("扫描件 PDF 大小: %.1f KB" % (os.path.getsize(scan_pdf) / 1024))

# 直接检查文本层应为空
doc = pymupdf.open(scan_pdf)
t = doc[0].get_text()
doc.close()
print("文本层内容(%d字符): %r" % (len(t), t[:30]))

# 全链路：pdf_text 应触发 OCR 降级并提取文字
from app.engine.pdf_service import pdf_text, amount_from_pdf, consume_date_from_file, railway_info_from_pdf

txt = pdf_text(scan_pdf)
print("\npdf_text 结果(%d字符):" % len(txt), txt.strip())
print("金额:", amount_from_pdf(scan_pdf))
print("消费日期:", consume_date_from_file(scan_pdf))
print("铁路信息:", railway_info_from_pdf(scan_pdf))

os.remove(tmp_png)