"""诊断：从邮件详情页 DOM 查找 mailid / fileid 相关信息，探索免点击直接构造下载URL。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.engine.web_client import WebClient
from app import config

app = QApplication(sys.argv)
win = QWidget(); win.setWindowTitle("诊断-mailid/fileid"); win.resize(1000, 680)
lay = QVBoxLayout(win)
view = QWebEngineView(); lay.addWidget(view, 4)
log = QTextEdit(); log.setReadOnly(True); lay.addWidget(log, 1)

web = WebClient(view)

def probe():
    log.append("===== 查找 fileid/mailid =====")
    # 1. 附件卡片是否有 data 属性
    html = web.run_js(
        "(function(){"
        "var els=document.querySelectorAll('.mail-detail-attach-card');"
        "var out=[];"
        "for(var i=0;i<els.length;i++){"
        "  var e=els[i];var o={i:i,attrs:{}};"
        "  for(var j=0;j<e.attributes.length;j++){o.attrs[e.attributes[j].name]=e.attributes[j].value;}"
        "  var svg=e.innerHTML.match(/svg/)?'has_svg':'';"
        "  out.push(JSON.stringify(o));"
        "}"
        "return out;})()", timeout=8000)
    log.append("attach-card attrs: " + str(html))

    # 2. 页面里所有包含 fileid 或 mailid 的文本/属性
    res = web.run_js(
        "(function(){"
        "var out=[];"
        "var all=document.querySelectorAll('*');"
        "for(var i=0;i<all.length;i++){"
        "  var e=all[i];"
        "  for(var j=0;j<e.attributes.length;j++){"
        "    var a=e.attributes[j];"
        "    if(/fileid|mailid|attachid|file_id|mail_id/i.test(a.name)){"
        "      out.push(e.tagName+'['+a.name+'='+a.value.slice(0,80)+']');"
        "    }"
        "  }"
        "}"
        "return out.slice(0,40);})()", timeout=8000)
    log.append("attr fileid/mailid: " + str(res))

    # 3. 脚本标签里的 mailid
    res2 = web.run_js(
        "(function(){"
        "var s=document.querySelectorAll('script');var out=[];"
        "for(var i=0;i<s.length;i++){"
        "  var t=s[i].textContent||'';"
        "  if(/mailid|fileid/i.test(t)){out.push('script'+i+': '+t.slice(0,300));}"
        "}"
        "return out.slice(0,10);})()", timeout=8000)
    log.append("script mailid: " + str(res2))

b = QPushButton("打开第一封并探测"); lay.addWidget(b)
def go():
    web.click_mail(0)
    import time as _t
    dl=_t.time()+30
    while _t.time()<dl:
        if web.mail_detail_ready(): break
        web.qt_sleep(0.5)
    probe()
b.clicked.connect(go)
log.append("已打开。登录后点『打开第一封并探测』。")
web.navigate(config.MAIL_HOME)
win.show()
sys.exit(app.exec())