# -*- mode: python ; coding: utf-8 -*-
#
# 发票助手 WebView2 版打包配置
# 策略：核心代码打包，大文件/特殊文件在 build.ps1 后处理复制
# 彻底规避 PyInstaller 中文/特殊路径校验 Bug

import os
from pathlib import Path

ROOT = Path(os.getcwd())

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ('wallpaper.png', '.'),
        ('config/invoice_rules.json', 'config'),
        ('config/extract_rules.json', 'config'),
    ],
    hiddenimports=[
        'clr', 'System', 'System.Windows.Forms',
        'app.engine.ocr_light', 'app.engine.ocr_light.main',
        'app.engine.ocr_light.ch_ppocr_det', 'app.engine.ocr_light.ch_ppocr_rec',
        'app.engine.ocr_light.ch_ppocr_cls', 'app.engine.ocr_light.cal_rec_boxes',
        'app.engine.ocr_light.utils.infer_engine', 'app.engine.ocr_light.utils.load_image',
        'app.engine.ocr_light.utils.process_img', 'app.engine.ocr_light.utils.vis_res',
    ],
    excludes=[
        'tkinter','_tkinter','tcl','pydoc','doctest','unittest','pytest',
        'imaplib','ftplib','telnetlib','smtplib','poplib','nntplib','mailbox','smtpd',
        'pydoc_data','lib2to3','xmlrpc','test',
        'pdfplumber','pdfminer','pypdfium2','pandas','scipy','numpy','matplotlib',
        'IPython','jedi','parso','lxml','sqlalchemy','openpyxl','fsspec','PIL',
        'bcrypt','cryptography','dask','bs4',
        # 彻底移除 QtWebEngine
        'PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngine',
        'PySide6.QtWebEngineQuick','PySide6.QtWebChannel','PySide6.QtWebSockets',
        # 无用 Qt 模块
        'PySide6.Qt3DAnimation','PySide6.Qt3DCore','PySide6.Qt3DExtras','PySide6.Qt3DInput',
        'PySide6.Qt3DLogic','PySide6.Qt3DRender','PySide6.QtAxContainer','PySide6.QtBluetooth',
        'PySide6.QtCharts','PySide6.QtDataVisualization','PySide6.QtDesigner',
        'PySide6.QtGraphs','PySide6.QtGraphsWidgets','PySide6.QtHelp','PySide6.QtHttpServer',
        'PySide6.QtLocation','PySide6.QtMultimedia','PySide6.QtMultimediaWidgets',
        'PySide6.QtNetworkAuth','PySide6.QtNfc','PySide6.QtPdf','PySide6.QtPdfWidgets',
        'PySide6.QtQuick3D','PySide6.QtQuickControls2','PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects','PySide6.QtScxml','PySide6.QtSensors','PySide6.QtSerialBus',
        'PySide6.QtSerialPort','PySide6.QtSpatialAudio','PySide6.QtSql','PySide6.QtStateMachine',
        'PySide6.QtSvg','PySide6.QtSvgWidgets','PySide6.QtTest','PySide6.QtTextToSpeech',
        'PySide6.QtUiTools','PySide6.QtXml',
        'PySide6.QtQml','PySide6.QtQmlModels','PySide6.QtQmlWorkerScript',
        'PySide6.QtQuick','PySide6.QtQuickWidgets','PySide6.QtQuick3D','PySide6.QtQuickControls2',
        'PySide6.QtPositioning','PySide6.QtPrintSupport','PySide6.QtSensors',
        'PySide6.QtSerialBus','PySide6.QtSerialPort','PySide6.QtSpatialAudio',
        'PySide6.QtNfc','PySide6.QtBluetooth',
    ],
    noarchive=False, optimize=2,
)

# 只保留 Qt 核心 DLL
_QT_KEEP = {'Qt6Core.dll','Qt6Gui.dll','Qt6Widgets.dll','Qt6Network.dll',
            'Qt6OpenGL.dll','Qt6PrintSupport.dll','Qt6Svg.dll','Qt6SvgWidgets.dll'}
def _keep_bin(e):
    n = os.path.basename(e[0])
    if n.startswith('Qt6') and n.endswith('.dll'): return n in _QT_KEEP
    d = e[0].replace('\\','/')
    for bad in ['/qml/','/plugins/qmltooling/','webengine','/translations/qtwebengine_locales/',
                'opencv_videoio_ffmpeg','/position/','/sensors/','/serialport/',
                '/serialbus/','/nfc/','/bluetooth/']:
        if bad in d: return False
    return True
a.binaries = [b for b in a.binaries if _keep_bin(b)]

def _keep_data(d):
    dest = d[0].replace('\\','/')
    if '/translations/' in d[0]:
        n = d[0].rsplit('\\',1)[-1].rsplit('/',1)[-1]
        if n.endswith('.qm'): return 'zh_CN' in n or 'zh_TW' in n
        return True
    for bad in ['/qml/','webengine','devtools']:
        if bad in d[0].replace('\\','/'): return False
    return True
a.datas = [d for d in a.datas if _keep_data(d)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='invoice-helper',
          debug=False, upx=False, console=False, icon=['icon.ico'])
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='invoice-helper')