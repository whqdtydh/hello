"""PySide6 plugins/resources/translations 细分。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

p6 = r"D:\AI\git\invoice-helper\dist\invoice-helper\_internal\PySide6"
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

print("=== plugins 细分 ===")
for name in sorted(os.listdir(os.path.join(p6, "plugins"))):
    p = os.path.join(p6, "plugins", name)
    s = dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
    print("%-42s %9.1f MB" % (name, s / MB))

print("\n=== resources 细分 ===")
for name in sorted(os.listdir(os.path.join(p6, "resources"))):
    p = os.path.join(p6, "resources", name)
    s = dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
    print("%-42s %9.1f MB" % (name, s / MB))

print("\n=== translations（按体积排序 Top15）===")
tr = os.path.join(p6, "translations")
ts = []
for fn in os.listdir(tr):
    ts.append((fn, os.path.getsize(os.path.join(tr, fn))))
for fn, s in sorted(ts, key=lambda x: -x[1])[:15]:
    print("%-42s %9.1f MB" % (fn, s / MB))

print("\n=== qml 细分 ===")
qml = os.path.join(p6, "qml")
for name in sorted(os.listdir(qml)):
    p = os.path.join(qml, name)
    s = dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
    print("%-42s %9.1f MB" % (name, s / MB))