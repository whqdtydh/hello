"""ocr_light 冒烟：导入 + 用 PIL 生成中文文字图，识别验证。"""
import sys
import time

sys.path.insert(0, r"D:\AI\git\发票助手")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

t0 = time.time()
from app.engine.ocr_light import RapidOCR  # noqa: E402
print("导入耗时: %.1fs" % (time.time() - t0))

t0 = time.time()
engine = RapidOCR()
print("引擎加载耗时: %.1fs" % (time.time() - t0))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

# 生成一张白底黑字测试图（宋体/黑体，48px）
font = None
for cand in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
             r"C:\Windows\Fonts\simsun.ttc"]:
    try:
        font = ImageFont.truetype(cand, 48)
        break
    except Exception:
        continue
img = Image.new("RGB", (900, 120), "white")
d = ImageDraw.Draw(img)
d.text((30, 20), "货拉拉电子发票 2026年08月18日 金额 123.45", font=font, fill="black")
import numpy as np  # noqa: E402
arr = np.array(img)

t0 = time.time()
res, _ = engine(arr)
print("识别耗时: %.1fs" % (time.time() - t0))
if res:
    print("识别结果:")
    for box, text, score in res:
        print("  %s  (%.2f)" % (text, score))
else:
    print("未识别到文字")