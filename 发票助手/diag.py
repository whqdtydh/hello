import sys, os, traceback, faulthandler, time

os.chdir(r"D:\AI\git\发票助手")
DIAG = r"D:\AI\git\发票助手\crash_diag.log"
faulthandler.enable(open(DIAG, "w"))

def _log(msg):
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

sys.excepthook = lambda *a: _log("EXC: " + "".join(traceback.format_exception(*a)))

_log("=== STEP 1: QApplication ===")
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    _log("QApplication OK")
except Exception as e:
    _log(f"QApplication FAIL: {e}"); sys.exit(1)

_log("=== STEP 2: QWebEngineProfile ===")
try:
    from PySide6.QtWebEngineCore import QWebEngineProfile
    profile = QWebEngineProfile("test", app)
    _log("QWebEngineProfile OK")
except Exception as e:
    _log(f"QWebEngineProfile FAIL: {e}"); sys.exit(1)

_log("=== STEP 3: MainWindow ===")
try:
    from app.ui.main_window import MainWindow
    win = MainWindow(profile=profile)
    _log("MainWindow OK")
except Exception as e:
    _log(f"MainWindow FAIL: {e}"); sys.exit(1)

_log("=== STEP 4: show ===")
try:
    win.show()
    _log(f"show OK visible={win.isVisible()} size={win.width()}x{win.height()}")
except Exception as e:
    _log(f"show FAIL: {e}"); sys.exit(1)

_log("=== STEP 5: entering event loop ===")

from PySide6.QtCore import QTimer
def _alive():
    _log(f"ALIVE visible={win.isVisible()}")
for t in [2000, 5000]:
    QTimer.singleShot(t, _alive)

try:
    rc = app.exec()
    _log(f"EXIT code={rc}")
except Exception as e:
    _log(f"event loop FAIL: {e}")
