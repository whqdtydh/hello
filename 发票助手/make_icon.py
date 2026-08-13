"""生成像素风邮件图标（较粗像素，32x32 网格），输出 PNG + 多尺寸 ICO。"""
import os
from PIL import Image, ImageDraw

# 画布网格：32x32 像素格，每格放大 12 倍 => 384x384 渲染（粗像素）
GRID = 32
SCALE = 12
SIZE = GRID * SCALE

# 调色板
BG = (245, 247, 251)
MAIL_BLUE = (74, 124, 255)
MAIL_DARK = (48, 86, 200)
MAIL_LIGHT = (160, 190, 255)
FLAP = (66, 110, 235)
FLAP_LIGHT = (190, 210, 255)
RED = (233, 78, 78)
RED_DARK = (190, 55, 55)
WHITE = (255, 255, 255)
LINE = (60, 70, 90)
DARK = (30, 38, 54)

img = Image.new("RGB", (SIZE, SIZE), BG)
d = ImageDraw.Draw(img)


def px(x, y, color, w=1, h=1):
    d.rectangle([x * SCALE, y * SCALE, (x + w) * SCALE - 1, (y + h) * SCALE - 1], fill=color)


# ---- 信封主体（约 26x20 格，居中偏左上，为发票纸让位）----
X0, Y0 = 2, 7
W, H = 26, 20

for i in range(W):
    px(X0 + i, Y0, MAIL_DARK)
    px(X0 + i, Y0 + H - 1, MAIL_DARK)
for j in range(H):
    px(X0, Y0 + j, MAIL_DARK)
    px(X0 + W - 1, Y0 + j, MAIL_DARK)

for i in range(1, W - 1):
    for j in range(1, H - 1):
        px(X0 + i, Y0 + j, MAIL_BLUE)

# 上部高光
for j in range(1, 6):
    c = tuple(int(a + (b - a) * (1 - j / 6)) for a, b in zip(MAIL_BLUE, MAIL_LIGHT))
    for i in range(1, W - 1):
        px(X0 + i, Y0 + j, c)

# 封盖 V 形
half = W // 2
for x in range(1, half):
    ymax = min(13, x * 12 // half)
    for y in range(1, ymax + 1):
        px(X0 + x, Y0 + y, FLAP)
        px(X0 + W - 1 - x, Y0 + y, FLAP)
# 封盖高光
for x in range(1, half):
    ymax = min(13, x * 12 // half)
    for y in range(1, min(3, ymax + 1)):
        px(X0 + x, Y0 + y, FLAP_LIGHT)
        px(X0 + W - 1 - x, Y0 + y, FLAP_LIGHT)
# 封盖描边
for x in range(1, half):
    y = min(13, x * 12 // half)
    px(X0 + x, Y0 + y, MAIL_DARK)
    px(X0 + W - 1 - x, Y0 + y, MAIL_DARK)

# 下缘阴影
for i in range(2, W - 2):
    px(X0 + i, Y0 + H - 2, MAIL_DARK)

# ---- 发票纸：从信封右下伸出 ----
PX0, PY0 = X0 + W - 9, Y0 + H - 11
PW, PH = 9, 11
for i in range(PW):
    for j in range(PH):
        if not (i in (0, PW - 1) and j in (0, PH - 1)):
            px(PX0 + i, PY0 + j, WHITE)
for i in range(PW):
    px(PX0 + i, PY0, LINE)
    px(PX0 + i, PY0 + PH - 1, LINE)
for j in range(PH):
    px(PX0, PY0 + j, LINE)
    px(PX0 + PW - 1, PY0 + j, LINE)

# 纸上文字线
for j, (sx, sw) in enumerate([(2, 5), (2, 3), (2, 4), (2, 3)]):
    for i in range(sw):
        px(PX0 + sx + i, PY0 + 2 + j * 2, LINE)

# 红印章
for a in range(3):
    for b in range(3):
        px(PX0 + 5 + a, PY0 + 6 + b, RED)
px(PX0 + 5, PY0 + 6, RED_DARK)
px(PX0 + 7, PY0 + 6, RED_DARK)
px(PX0 + 5, PY0 + 8, RED_DARK)
px(PX0 + 7, PY0 + 8, RED_DARK)

# ---- 底部三枚邮票 ----
stamps = [(6, 27), (15, 27), (24, 27)]
for sx, sy in stamps:
    for i in range(5):
        px(sx + i, sy, RED)
        px(sx + i, sy + 4, RED)
    for j in range(5):
        px(sx, sy + j, RED)
        px(sx + 4, sy + j, RED)
    for i in range(1, 4):
        for j in range(1, 4):
            px(sx + i, sy + j, WHITE)
    px(sx + 2, sy + 1, DARK)
    px(sx + 1, sy + 2, DARK)
    px(sx + 2, sy + 2, DARK)
    px(sx + 3, sy + 2, DARK)

# 顶部左侧：红点/标题小方块（点缀）
px(2, 2, RED)
px(3, 2, RED)
px(2, 3, RED)
px(3, 3, RED)

base = os.path.dirname(os.path.abspath(__file__))
png_path = os.path.join(base, "icon.png")
ico_path = os.path.join(base, "icon.ico")
img.save(png_path)
img.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("saved", png_path, ico_path, os.path.getsize(ico_path))