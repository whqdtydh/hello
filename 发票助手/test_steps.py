import sys, os, traceback, faulthandler, time
os.chdir(r"D:/AI/git/发票助手")
sys.path.insert(0, r"D:/AI/git/发票助手")
DIAG = r"D:/AI/git/发票助手/crash_diag.log"
faulthandler.enable(open(DIAG, "w"))

def _log(msg):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write("[{}] {}\n".format(time.strftime("%H:%M:%S"), msg))

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    app = QApplication(sys.argv)

    _log("STEP 1: _GlassCard")
    from app.ui.main_window import _GlassCard
    card = _GlassCard()
    card.resize(200, 100)
    card.show()
    _log("  OK")

    _log("STEP 2: Full MainWindow")
    from app.ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    _log("  OK visible={} size={}x{}".format(win.isVisible(), win.width(), win.height()))

    _log("ALL STEPS PASSED")
    QTimer.singleShot(5000, app.quit)
    rc = app.exec()
    _log("EXIT code={}".format(rc))

except Exception:
    _log("FATAL: " + traceback.format_exc())
