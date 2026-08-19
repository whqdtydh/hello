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
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QWindow

    app = QApplication(sys.argv)

    # App-level exception handling
    app.aboutToQuit.connect(lambda: _log("aboutToQuit"))

    # WebView2 控件（pythonnet 宿主）：创建 → 句柄嵌入 Qt 容器
    from app.engine import webview2_host
    from app import config
    wv, hwnd, _port = webview2_host.create_view(
        user_data_folder=config.PROFILE_DIR)
    qwin = QWindow.fromWinId(hwnd)
    container = QWidget.createWindowContainer(qwin)

    from app.engine import msgid_service
    msgid_service.start_server()

    from app.ui.main_window import MainWindow
    win = MainWindow(web_container=container, wv=wv)
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
