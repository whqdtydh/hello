# -*- mode: python ; coding: utf-8 -*-
#
# 打包配置（onedir 模式）：
#  - onedir：启动无需把全部文件解压到临时目录，启动速度明显快于 onefile，
#            也是 Qt 官方对 QtWebEngine 应用的建议；产物为 dist/发票助手/ 文件夹。
#  - upx=False：Qt dll 用 UPX 压缩会在运行时二次解压，反而拖慢启动并易被杀毒误报。
#  - excludes：排除未使用的标准库/测试框架，减小体积。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'tcl', 'pydoc', 'doctest',
              'unittest', 'pytest'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='发票助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='发票助手',
)