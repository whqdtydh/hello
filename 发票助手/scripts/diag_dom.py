"""诊断：登录后打印页面中疑似邮件列表容器的 DOM 结构 + iframe 情况。"""
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
win.setWindowTitle("诊断-DOM结构")
win.resize(1100, 700)
lay = QVBoxLayout(win)
view = QWebEngineView()
lay.addWidget(view, 4)
log = QTextEdit(); log.setReadOnly(True)
lay.addWidget(log, 1)

web = WebClient(view)
web.navigate(config.MAIL_HOME)


def dump():
    log.append("=== iframes ===")
    js_frames = (
        "(function(){"
        "var o=[];"
        "for(var a of document.querySelectorAll('iframe')){"
        "  o.push(a.src||'');"
        "}"
        "return o;})()"
    )
    for f in web.run_js(js_frames, timeout=8000) or []:
        log.append("iframe: " + f)

    log.append("\n=== 含 list/mail/item 的容器 class（前30） ===")
    js = (
        "(function(){"
        "var out=[];"
        "var all=document.querySelectorAll('div,ul,li');"
        "for(var i=0;i<all.length;i++){"
        "  var a=all[i];"
        "  var c=a.className||'';"
        "  if(typeof c!=='string') continue;"
        "  if(/list|item|mail|folder|mbox/i.test(c)){"
        "     out.push({tag:a.tagName, cls:c.slice(0,80), n:a.children.length});"
        "  }"
        "}"
        "return out.slice(0,30);})()"
    )
    res = web.run_js(js, timeout=10000) or []
    for r in res:
        log.append(f"<{r['tag']}> cls={r['cls']} children={r['n']}")

    log.append("\n=== 含发票/打车 文本的节点（前20） ===")
    js2 = (
        "(function(){"
        "var out=[];"
        "var all=document.querySelectorAll('div,span,li');"
        "for(var i=0;i<all.length;i++){"
        "  var t=(all[i].innerText||'').trim();"
        "  if((t.indexOf('发票')>=0||t.indexOf('打车')>=0)&&t.length<200){"
        "    out.push({tag:all[i].tagName,cls:(all[i].className||'').slice(0,60),t:t.slice(0,60)});"
        "  }"
        "}"
        "return out.slice(0,20);})()"
    )
    res2 = web.run_js(js2, timeout=10000) or []
    for r in res2:
        log.append(f"<{r['tag']}> cls={r['cls']} text={r['t']}")

    log.append("\n=== 当前 URL ===")
    log.append(web.current_url())


log.append("请登录后进入收件箱，再点『Dump结构』。若存在登录/iframe，请在下方窗口操作。")
b = QPushButton("Dump结构")
b.clicked.connect(dump)
lay.addWidget(b)

win.show()
sys.exit(app.exec())