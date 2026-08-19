"""深入 _internal 分析：顶层目录聚合 + 顶层大文件 + 关键库归类。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = r"D:\AI\git\发票助手\dist\发票助手\_internal"
MB = 1024 * 1024

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

print("=== _internal 构成（Top 30）===")
grand = 0
for name, size, typ in rows[:30]:
    grand += size
    print("%-42s %8.1f MB  [%s]" % (name, size / MB, typ))
print("-" * 60)
print("Top30 合计: %.1f MB" % (grand / MB))

# Qt 目录细分
qt = os.path.join(root, "PySide6", "Qt")
if os.path.isdir(qt):
    print("\n=== PySide6/Qt 插件+库细分（>5MB）===")
    for name in sorted(os.listdir(qt)):
        p = os.path.join(qt, name)
        s = 0
        if os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                for fn in fns:
                    try:
                        s += os.path.getsize(os.path.join(dp, fn))
                    except OSError:
                        pass
        else:
            s = os.path.getsize(p)
        if s > 5 * MB:
            print("%-42s %8.1f MB" % (name, s / MB))

# PySide6 下的大 dll
p6 = os.path.join(root, "PySide6")
if os.path.isdir(p6):
    print("\n=== PySide6 顶层大文件（>5MB）===")
    for name in sorted(os.listdir(p6)):
        p = os.path.join(p6, name)
        if os.path.isfile(p):
            s = os.path.getsize(p)
            if s > 5 * MB:
                print("%-42s %8.1f MB" % (name, s / MB))
