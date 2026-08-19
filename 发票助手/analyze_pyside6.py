"""WebView2 改造体积预估：精确列出 PySide6/Qt 全部构成，区分可砍/保留。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

qt = r"D:\AI\git\发票助手\dist\发票助手\_internal\PySide6"
MB = 1024 * 1024

def dir_size(p):
    s = 0
    for dp, _, fns in os.walk(p):
        for fn in fns:
            try:
                s += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return s

def fmt(n):
    return "%.1f MB" % (n / MB)

print("=== PySide6 顶层（按体积排序）===")
items = []
for name in os.listdir(qt):
    p = os.path.join(qt, name)
    s = dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
    items.append((name, s, "dir" if os.path.isdir(p) else "file"))
for name, s, typ in sorted(items, key=lambda x: -x[1]):
    print("%-40s %9s  [%s]" % (name, fmt(s), typ))

print("\n=== PySide6/Qt/plugins 细分（按体积排序）===")
qp = os.path.join(qt, "Qt", "plugins")
if os.path.isdir(qp):
    for name in sorted(os.listdir(qp)):
        p = os.path.join(qp, name)
        s = dir_size(p)
        print("%-40s %9s" % (name, fmt(s)))