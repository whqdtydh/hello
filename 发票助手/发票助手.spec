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
    datas=[('wallpaper.png', '.'), ('config/invoice_rules.json', 'config'),
           ('config/extract_rules.json', 'config')],
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
# 激进方案：同时过滤 devtools_resources.pak（不用 F12 开发者工具）
def _keep_data(d):
    dest = d[0].replace('\\', '/')
    if dest.endswith('.debug.pak') or dest.endswith('.debug.bin'):
        return False
    # 非 debug 版 devtools 前端（11 MB），普通使用不需要
    if dest.endswith('qtwebengine_devtools_resources.pak'):
        return False
    # ── 激进方案：裁剪 translations ──
    # 只保留中文翻译 + WebEngine 必用的 locales 中 zh/en 语言包
    if '/translations/' in dest:
        if 'qtwebengine_locales' in dest:
            # Chromium 语言包：仅保留 zh-CN / zh-TW / en-US / en-GB
            base = dest.rsplit('/', 1)[-1].lower()
            return any(k in base for k in ('zh-cn.pak', 'zh-tw.pak', 'en-us.pak', 'en-gb.pak'))
        # qt*.qm 翻译：只保留中文
        name = dest.rsplit('/', 1)[-1]
        if name.endswith('.qm') and 'zh_CN' not in name and 'zh_TW' not in name:
            return False
        return True
    # ── 激进方案：裁剪 qml 目录（WebEngine 不需要 QML UI 组件）──
    if '/qml/' in dest:
        # 保留 WebEngine 运行必需的最小 QML 核心
        keep_prefixes = (
            '/qml/QtWebEngine/',
            '/qml/QtQuick/Window',
            '/qml/QtQml/',
            '/qml/QtCore/',
            '/qml/QtNetwork/',
            '/qml/Qt/labs/',
        )
        for p in keep_prefixes:
            if p in dest:
                return True
        # qml 目录中的其他内容全部删除
        return False
    return True
a.datas = [d for d in a.datas if _keep_data(d)]
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
    dest = entry[0].replace('\\', '/')
    name = _os.path.basename(entry[0])
    # 1) 顶层 Qt DLL 白名单
    if name.startswith('Qt6') and name.endswith('.dll'):
        return name in _QT_KEEP_DLL
    # 2) qmltooling 调试插件（QML 调试工具，运行不需要）
    if '/plugins/qmltooling/' in dest:
        return False
    # 3) qml 插件 DLL：只保留 WebEngine 运行必需的最小 QML 核心
    if '/qml/' in dest:
        keep_qml = (
            '/qml/QtWebEngine/',
            '/qml/QtWebChannel/',
            '/qml/QtQml/',
            '/qml/QtCore/',
            '/qml/QtNetwork/',
            '/qml/QtQuick/Window',
            '/qml/QtQuick.2/',
            '/qml/Qt/labs/',
        )
        for p in keep_qml:
            if p in dest:
                return True
        return False
    # 4) qtwebengine_locales 语言包：仅保留 zh-CN / zh-TW / en（其余全删）
    if '/translations/qtwebengine_locales/' in dest:
        base = name.lower()
        return any(k in base for k in ('zh-cn.pak', 'zh-tw.pak', 'en-us.pak'))
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