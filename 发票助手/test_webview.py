import sys, os, traceback, faulthandler, time
os.chdir(r"D:/AI/git/发票助手")
DIAG = r"D:/AI/git/发票助手/crash_diag.log"
faulthandler.enable(open(DIAG, "w"))

def _log(msg):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write("[{}] {}\n".format(time.strftime("%H:%M:%S"), msg))

_log("=== TEST: QWebEngineView alone ===")
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineProfile
    app = QApplication(sys.argv)
    profile = QWebEngineProfile("test", app)
    view = QWebEngineView(profile)
    view.setHtml("<h1>test</h1>")
    view.resize(800, 600)
    view.show()
    _log("QWebEngineView shown OK")
    from PySide6.QtCore import QTimer
    QTimer.singleShot(3000, lambda: (_log("ALIVE"), app.quit()))
    rc = app.exec()
    _log("EXIT code={}".format(rc))
except Exception:
    _log("FAIL: " + traceback.format_exc())
