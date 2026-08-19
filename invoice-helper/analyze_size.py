"""分析打包产物体积构成：顶层目录聚合 + 顶层 dll/exe 按文件名归类。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = r"D:\AI\git\invoice-helper\dist\invoice-helper"
MB = 1024 * 1024

# 目录聚合
dir_sizes = {}
file_sizes = {}
for name in os.listdir(root):
    p = os.path.join(root, name)
    if os.path.isdir(p):
        total = 0
        for dp, _, fns in os.walk(p):
            for fn in fns:
                try:
                    total += os.path.getsize(os.path.join(dp, fn))
                except OSError:
                    pass
        dir_sizes[name] = total
    else:
        try:
            file_sizes[name] = os.path.getsize(p)
        except OSError:
            pass

rows = [(k, v, "dir") for k, v in dir_sizes.items()]
rows += [(k, v, "file") for k, v in file_sizes.items()]
rows.sort(key=lambda x: -x[1])

print("=== 打包产物体积构成（Top 25）===")
grand = 0
for name, size, typ in rows[:25]:
    grand += size
    print("%-42s %8.1f MB" % (name, size / MB))
print("-" * 60)
print("TOP25 合计: %.1f MB" % (grand / MB))

total = 0
for dp, _, fns in os.walk(root):
    for fn in fns:
        try:
            total += os.path.getsize(os.path.join(dp, fn))
        except OSError:
            pass
print("整个目录: %.1f MB" % (total / MB))

# 专项：按特征聚合关键大件
def walk_all():
    for dp, _, fns in os.walk(root):
        for fn in fns:
            yield os.path.join(dp, fn)

patterns = {
    "QtWebEngine(可整体砍掉)": ["QtWebEngine", "qtwebengine", "QtPdf", "QtQuick"],
    "Qt6 基础(Core/Gui/Widgets等)": ["Qt6", "qt6"],
    "opencv/cv2": ["opencv", "cv2", "ffmpeg"],
    "pymupdf/fitz": ["fitz", "mupdf"],
    "onnxruntime": ["onnxruntime", "onnx"],
    "numpy": ["numpy"],
    "pandas": ["pandas"],
    "scipy": ["scipy"],
    "requests等纯py(不细分)": ["requests", "urllib3"],
    "PySide6 支撑(_internal/Qt目录)": [],
}
print("\n=== 按特征聚合 ===")
for label, keys in patterns.items():
    s = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = p.replace(root, "").lower()
            if any(k.lower() in rel for k in keys):
                try:
                    s += os.path.getsize(p)
                except OSError:
                    pass
    if label != "PySide6 支撑(_internal/Qt目录)" or s > 0:
        print("%-40s %8.1f MB" % (label, s / MB))
