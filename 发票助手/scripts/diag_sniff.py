"""诊断：点击下载按钮 -> 拦截器捕获 URL -> requests+cookie 拉取，验证整条链路。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.engine.web_client import WebClient
from app import config

app = QApplication(sys.argv)
win = QWidget(); win.setWindowTitle("诊断-嗅探拉取链路"); win.resize(1000, 680)
lay = QVBoxLayout(win)
view = QWebEngineView(); lay.addWidget(view, 4)
log = QTextEdit(); log.setReadOnly(True); lay.addWidget(log, 1)

web = WebClient(view)
web.log_signal.connect(lambda m: log.append(m))

def open_first():
    log.append(f"click_mail(0) -> {web.click_mail(0)}")
    import time as _t
    dl = _t.time() + 30
    while _t.time() < dl:
        if web.mail_detail_ready():
            log.append("详情已就绪")
            return
        web.qt_sleep(1)
    log.append("详情未就绪")

def wait_attachments(timeout=15):
    import time as _t
    dl = _t.time() + timeout
    while _t.time() < dl:
        atts = web.get_attachments()
        if atts:
            return atts
        web.qt_sleep(1)
    return []

def test_download():
    log.append("=== 测试下载第一个PDF附件（并行线程拉取） ===")
    atts = wait_attachments()
    log.append(f"附件数: {len(atts)}")
    for i, a in enumerate(atts):
        log.append(f"  [{i}] {a.get('suffix','')} | {a.get('name','')[:30]} | {a.get('size','')}")
    pdf_idx = None
    for i, a in enumerate(atts):
        if a.get("suffix", "").strip().lower() == ".pdf":
            pdf_idx = i
            break
    if pdf_idx is None:
        log.append("未找到PDF附件"); return
    log.append(f"PDF 卡片索引: {pdf_idx}")
    ok = web.click_download(pdf_idx)
    log.append(f"click_download({pdf_idx}) -> {ok}")
    urls = web.consume_download_url(timeout=8)
    log.append(f"嗅探到 {len(urls)} 个URL:")
    for u in urls:
        log.append("  " + u[:150])
    if urls:
        from concurrent.futures import ThreadPoolExecutor
        dest = r"C:\Users\48641\Desktop\车辆报销\_test_sniff.pdf"
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut = pool.submit(web.fetch_url, urls[0], dest)
                fut.result(timeout=60)
            size = os.path.getsize(dest) if os.path.exists(dest) else 0
            log.append(f"并行线程 requests 拉取成功! size={size}")
        except Exception as e:
            log.append(f"并行线程 requests 拉取失败: {str(e)[:200]}")

def test_native():
    log.append("=== 测试原生下载 ===")
    atts = wait_attachments()
    log.append(f"附件数: {len(atts)}")
    pdf_idx = None
    for i, a in enumerate(atts):
        if a.get("suffix", "").strip().lower() == ".pdf":
            pdf_idx = i; break
    if pdf_idx is None:
        log.append("未找到PDF"); return
    dest = r"C:\Users\48641\Desktop\车辆报销\_test_native.pdf"
    web.add_pending_dest(dest)
    ok = web.click_download(pdf_idx)
    log.append(f"click_download -> {ok}")
    web.wait_downloads(1, timeout=15)
    log.append(f"完成状态: 存在={os.path.exists(dest)} size={os.path.getsize(dest) if os.path.exists(dest) else 0}")
    log.append(f"finished_dests={web._finished_dests}")

b1 = QPushButton("打开第一封邮件"); b1.clicked.connect(open_first); lay.addWidget(b1)
b2 = QPushButton("测试嗅探拉取"); b2.clicked.connect(test_download); lay.addWidget(b2)
b3 = QPushButton("测试原生下载"); b3.clicked.connect(test_native); lay.addWidget(b3)

log.append("已打开。登录后：点『打开第一封邮件』→『测试嗅探拉取』/『测试原生下载』。")
web.navigate(config.MAIL_HOME)
win.show()
sys.exit(app.exec())