"""诊断：dump 邮件详情页附件卡片的 outerHTML，确认能否直接读到下载链接。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.engine.web_client import WebClient
from app import config

app = QApplication(sys.argv)
win = QWidget()
win.setWindowTitle("诊断-附件DOM")
win.resize(1000, 680)
lay = QVBoxLayout(win)
view = QWebEngineView()
lay.addWidget(view, 4)
log = QTextEdit()
log.setReadOnly(True)
lay.addWidget(log, 1)

web = WebClient(view)

def dump():
    log.append("===== 当前状态 =====")
    st = web.get_page_state()
    log.append(f"title={st.get('title')} items={st.get('items')} hasInbox={st.get('hasInbox')}")
    log.append("===== 附件卡片 outerHTML =====")
    html = web.run_js(
        "(function(){"
        "var els=document.querySelectorAll('.mail-detail-attach-card');"
        "var out=[];"
        "for(var i=0;i<els.length;i++){"
        "  out.push('--- card '+i+' ---\\n'+els[i].outerHTML.slice(0,2000));"
        "}"
        "return out.join('\\n');})()", timeout=8000)
    log.append(str(html))
    log.append("===== 全部 a[href] =====")
    links = web.run_js(
        "(function(){"
        "var as=document.querySelectorAll('a[href]');"
        "var out=[];"
        "for(var i=0;i<as.length;i++){"
        "  var h=as[i].getAttribute('href')||'';"
        "  if(/attach|download|file|disp/i.test(h)){"
        "    out.push(as[i].innerText.trim()+' => '+h.slice(0,160));"
        "  }"
        "}"
        "return out.join('\\n');})()", timeout=8000)
    log.append(str(links))

b = QPushButton("打开第一封邮件并Dump附件"); lay.addWidget(b)
def click_first_and_dump():
    ok = web.click_mail(0)
    log.append(f"click_mail(0) -> {ok}")
    web.wait_page_ready(timeout=30)
    dl = time.time() + 30
    while time.time() < dl:
        if web.mail_detail_ready():
            log.append("详情已就绪")
            break
        web.qt_sleep(1)
    dump()
b.clicked.connect(click_first_and_dump)

def dump_now():
    dump()
b2 = QPushButton("直接Dump当前页附件"); b2.clicked.connect(dump_now); lay.addWidget(b2)

log.append("已打开。若未登录请先登录，点『打开第一封邮件并Dump附件』。")
web.navigate(config.MAIL_HOME)
win.show()
sys.exit(app.exec())