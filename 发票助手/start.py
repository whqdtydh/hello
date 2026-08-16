import sys, os, traceback, threading, time, faulthandler

os.chdir(r"D:\AI\git\发票助手")

LOG = os.path.join(os.environ["TEMP"], "invoice_crash.log")
DIAG = r"D:\AI\git\发票助手\crash_diag.log"

faulthandler.enable(open(DIAG, "w"))

def _log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
    with open(DIAG, "a", encoding="utf-8") as f:
        f.write(line)

# Global exception hook
def _excepthook(tp, val, tb):
    txt = "".join(traceback.format_exception(tp, val, tb))
    _log("UNCAUGHT: " + txt)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(txt)

sys.excepthook = _excepthook

# Thread exception hook
_orig_thread_run = threading.Thread.run
def _thread_run(self):
    try:
        _orig_thread_run(self)
    except Exception:
        _log("THREAD ERROR: " + traceback.format_exc())
        raise
threading.Thread.run = _thread_run

_log("=== START ===")

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile

    app = QApplication(sys.argv)

    # App-level exception handling
    app.aboutToQuit.connect(lambda: _log("aboutToQuit"))

    profile = QWebEngineProfile("invoice_profile", app)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    profile.setCachePath(os.path.join(os.path.expanduser("~"), ".invoice_cache"))
    profile.setPersistentStoragePath(os.path.join(os.path.expanduser("~"), ".invoice_data"))

    from app.engine import msgid_service
    msgid_service.start_server()

    from app.ui.main_window import MainWindow
    win = MainWindow(profile=profile)
    win.show()
    _log(f"SHOW OK size={win.width()}x{win.height()} alive={win.isVisible()}")

    # Periodic alive check
    def _alive():
        if win.isVisible():
            _log(f"ALIVE ok")
        else:
            _log(f"ALIVE window gone!")

    for i in [2000, 5000, 8000]:
        QTimer.singleShot(i, _alive)

    rc = app.exec()
    _log(f"EXIT code={rc}")
except Exception:
    _log("FATAL: " + traceback.format_exc())
