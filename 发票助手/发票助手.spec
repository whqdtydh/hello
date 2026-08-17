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
    datas=[('wallpaper.png', '.'), ('config/invoice_rules.json', 'config')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'tcl', 'pydoc', 'doctest',
              'unittest', 'pytest', 'imaplib', 'ftplib', 'telnetlib',
              'smtplib', 'poplib', 'nntplib', 'mailbox', 'smtpd',
              'pydoc_data', 'lib2to3', 'xmlrpc', 'test',
              # 已用 PyMuPDF 替代 pdfplumber，排除其重型依赖链
              'pdfplumber', 'pdfminer', 'pypdfium2', 'pandas', 'scipy',
              'numpy', 'matplotlib', 'IPython', 'jedi', 'parso',
              'lxml', 'sqlalchemy', 'openpyxl', 'fsspec', 'PIL',
              'bcrypt', 'cryptography', 'dask', 'bs4',
              # ── QtWebEngine 轻量化（保守方案）──
              # 排除与本项目无关的 PySide6 模块（DLL/pyd/插件随之不收集）。
              # 注意：QtQml/QtQuick/QtPositioning/QtWebChannel/QtOpenGL 及其
              # 依赖（QmlMeta/QmlModels/QmlWorkerScript）是 Qt6WebEngineCore.dll
              # 的硬依赖，绝不能排除（否则启动即崩）。
              'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras',
              'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender',
              'PySide6.QtAxContainer', 'PySide6.QtBluetooth',
              'PySide6.QtCharts', 'PySide6.QtDataVisualization',
              'PySide6.QtDesigner', 'PySide6.QtGraphs', 'PySide6.QtGraphsWidgets',
              'PySide6.QtHelp', 'PySide6.QtHttpServer',
              'PySide6.QtLocation', 'PySide6.QtMultimedia',
              'PySide6.QtMultimediaWidgets', 'PySide6.QtNetworkAuth',
              'PySide6.QtNfc', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
              'PySide6.QtQuick3D', 'PySide6.QtQuickControls2',
              'PySide6.QtQuickWidgets', 'PySide6.QtRemoteObjects',
              'PySide6.QtScxml', 'PySide6.QtSensors', 'PySide6.QtSerialBus',
              'PySide6.QtSerialPort', 'PySide6.QtSpatialAudio',
              'PySide6.QtSql', 'PySide6.QtStateMachine',
              'PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'PySide6.QtTest',
              'PySide6.QtTextToSpeech', 'PySide6.QtUiTools',
              'PySide6.QtWebEngineQuick', 'PySide6.QtWebSockets',
              'PySide6.QtXml'],
    noarchive=False,
    optimize=2,
)
# 过滤 QtWebEngine 的 debug 资源（release 运行不需要；节省约 77 MB）
# devtools_resources.pak（11 MB，非 debug）留待激进方案处理
_keep = lambda d: not (d[0].endswith('.debug.pak') or d[0].endswith('.debug.bin'))
a.datas = [d for d in a.datas if _keep(d)]

# ── QtWebEngine 轻量化：只保留依赖链上的 Qt DLL ──
# 从 Qt6WebEngineCore/WebEngineWidgets 出发做 BFS 依赖分析，得到必需 DLL 白名单。
# excludes 只能拦截 Python 模块，无法拦截 Qt hook 收集的 DLL，需在此直接过滤。
_QT_KEEP_DLL = {
    'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Network.dll', 'Qt6OpenGL.dll',
    'Qt6Positioning.dll', 'Qt6PrintSupport.dll', 'Qt6Qml.dll',
    'Qt6QmlMeta.dll', 'Qt6QmlModels.dll', 'Qt6QmlWorkerScript.dll',
    'Qt6Quick.dll', 'Qt6QuickWidgets.dll', 'Qt6WebChannel.dll',
    'Qt6WebEngineCore.dll', 'Qt6WebEngineWidgets.dll', 'Qt6Widgets.dll',
}
import os as _os
def _keep_bin(entry):
    # entry 是 (dest, src, typecode) 三元组，dest 为相对目标路径
    name = _os.path.basename(entry[0])
    if name.startswith('Qt6') and name.endswith('.dll'):
        return name in _QT_KEEP_DLL
    return True
a.binaries = [b for b in a.binaries if _keep_bin(b)]
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