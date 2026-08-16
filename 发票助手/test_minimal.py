import sys, os, traceback, faulthandler, time
os.chdir(r"D:/AI/git/发票助手")
sys.path.insert(0, r"D:/AI/git/发票助手")
DIAG = r"D:/AI/git/发票助手/crash_diag.log"
faulthandler.enable(open(DIAG, "w"))

def _log(msg):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write("[{}] {}\n".format(time.strftime("%H:%M:%S"), msg))

_log("=== TEST: MainWindow minimal ===")
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEngineProfile
    app = QApplication(sys.argv)
    profile = QWebEngineProfile("test", app)

    from app.ui.main_window import MainWindow
    win = MainWindow(profile=profile)
    _log("MainWindow created OK")

    # Disable blur
    win._apply_blur = lambda: None
    # Disable on_load_mail
    win.on_load_mail = lambda: None

    win.show()
    _log("show OK, visible={}, size={}x{}".format(win.isVisible(), win.width(), win.height()))

    from PySide6.QtCore import QTimer
    QTimer.singleShot(5000, lambda: (_log("ALIVE visible={}".format(win.isVisible())), app.quit()))
    rc = app.exec()
    _log("EXIT code={}".format(rc))
except Exception:
    _log("FAIL: " + traceback.format_exc())
