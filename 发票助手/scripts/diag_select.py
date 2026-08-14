"""诊断勾选检测：加载报销文件夹，用户登录并勾选邮件后，打印每封邮件的 checkbox 状态。
用 QTimer 轮询登录态，登录后自动导航到报销文件夹并等待用户勾选。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.engine.web_client import WebClient

from app import config

app = QApplication(sys.argv)
win = QWidget()
win.setWindowTitle("诊断-勾选检测")
win.resize(1100, 700)
lay = QVBoxLayout(win)
view = QWebEngineView()
lay.addWidget(view, 4)
log = QTextEdit(); log.setReadOnly(True)
lay.addWidget(log, 1)

web = WebClient(view)
web.navigate(config.MAIL_HOME)


def btn_dump():
    res = web.run_js(
        "(function(){"
        "var els=document.querySelectorAll('div[class*=list-item]');"
        "var out=[];"
        "for(var i=0;i<els.length;i++){"
        "  var el=els[i];"
        "  var cb=el.querySelector('input[type=checkbox]');"
        "  var checked=cb?cb.checked:false;"
        "  var cls=el.className||'';"
        "  var selected=checked||/sel|cur|check|active/i.test(cls);"
        "  var box=el.querySelector('[class*=check],[class*=Check]');"
        "  out.push({index:i,checked:checked,sel:selected,cls:cls.slice(0,50),"
        "            boxcls:box?box.className.slice(0,50):'',"
        "            text:(el.innerText||'').slice(0,40)});"
        "}"
        "return out;})()"
    )
    log.append("=== 邮件项状态 ===")
    if not res:
        log.append("(空)")
    for item in res or []:
        log.append(f"idx={item['index']} checked={item['checked']} sel={item['sel']} box={item['boxcls']}")
        log.append(f"    cls={item['cls']}")
        log.append(f"    text={item['text']}")

log.append("如果已自动登录会切到报销文件夹。请勾选邮件后点『打印勾选状态』。")
# 轮询登录态，登录后导航到报销文件夹
def poll():
    if web.is_logged_in():
        log.append("检测到已登录，导航到报销文件夹…")
        web.navigate(config.DEFAULT_MAIL_URL)

t = QTimer(); t.timeout.connect(poll); t.start(3000)

b = QPushButton("打印勾选状态")
b.clicked.connect(btn_dump)
lay.addWidget(b)

win.show()
sys.exit(app.exec())