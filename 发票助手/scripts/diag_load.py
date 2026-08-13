"""检查 QtWebEngine 是否正常加载页面：轮询 URL 与 loadFinished 信号。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QTimer

app = QApplication(sys.argv)
win = QWidget(); win.setWindowTitle("加载测试"); win.resize(1000, 650)
lay = QVBoxLayout(win)
view = QWebEngineView()
lay.addWidget(view, 4)
log = QTextEdit(); log.setReadOnly(True); lay.addWidget(log, 1)

view.loadFinished.connect(lambda ok: log.append(f"loadFinished ok={ok} url={view.url().toString()}"))
view.titleChanged.connect(lambda t: log.append(f"titleChanged: {t}"))

log.append("测试加载 https://wx.mail.qq.com/home/index …")
view.load("https://wx.mail.qq.com/home/index")

def poll():
    u = view.url().toString()
    log.append(f"[poll] url={u}")

t = QTimer()
t.timeout.connect(poll)
t.start(5000)
# 停止轮询
QTimer.singleShot(60000, t.stop)

win.show()
sys.exit(app.exec())