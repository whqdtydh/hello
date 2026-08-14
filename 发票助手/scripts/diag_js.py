"""诊断 run_js 返回值：测试基本类型与复杂对象，定位 JS 返回 None 的原因。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.engine.web_client import WebClient
from app import config

app = QApplication(sys.argv)
win = QWidget()
win.setWindowTitle("诊断-JS返回值")
win.resize(1000, 680)
lay = QVBoxLayout(win)
view = QWebEngineView()
lay.addWidget(view, 4)
log = QTextEdit()
log.setReadOnly(True)
lay.addWidget(log, 1)

web = WebClient(view)

def test():
    log.append("--- 基本类型测试 ---")
    log.append(f"readyState: {web.run_js('document.readyState', timeout=6000)!r}")
    log.append(f"title: {web.run_js('document.title', timeout=6000)!r}")
    log.append(f"bodyLen: {web.run_js('(document.body?document.body.innerText.length:0)', timeout=6000)!r}")
    log.append(f"iframeCount: {web.run_js('document.querySelectorAll(\"iframe\").length', timeout=6000)!r}")
    log.append(f"url: {web.current_url()}")
    log.append("--- 对象测试（修复后） ---")
    log.append(f"obj1: {web.run_js_obj('({a:1,b:\"x\",c:true})', timeout=6000)!r}")
    log.append(f"arr: {web.run_js_obj('(function(){return [1,2,3];})()', timeout=6000)!r}")
    log.append("--- 状态测试 ---")
    try:
        st = web.get_page_state()
        log.append(f"get_page_state: {st}")
    except Exception as e:
        log.append(f"get_page_state ERR: {e}")

b = QPushButton("运行测试"); b.clicked.connect(test); lay.addWidget(b)
log.append("已打开。若未登录请先登录，再点运行测试。")
web.navigate(config.MAIL_HOME)
win.show()
sys.exit(app.exec())