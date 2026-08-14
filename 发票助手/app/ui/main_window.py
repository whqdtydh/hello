"""发票助手主窗口（邮箱勾选 + IMAP 下载）。

左侧内嵌 QQ 邮箱网页，用户勾选需要下载的发票/行程单邮件；
右侧填写 IMAP 账号授权码后，程序用 IMAP 协议匹配勾选邮件并下载 PDF 附件。
速度远快于网页嗅探方式。
"""

import sys
import threading

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QTextEdit, QMessageBox, QFrame,
)

from app import config
from app.engine.imap_engine import ImapEngine
from app.engine.web_client import WebClient


def _styles():
    """全局样式：简洁、留白充足、克制的色彩。"""
    return """
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 13px;
    color: #2f3542;
}
QWidget#Root {
    background: #f5f6f8;
}
/* 顶栏 */
QFrame#Header {
    background: #ffffff;
    border-bottom: 1px solid #e8eaee;
}
QLabel#AppTitle {
    font-size: 17px;
    font-weight: 600;
    color: #1d2430;
    letter-spacing: 1px;
}
QLabel#AppSub {
    font-size: 12px;
    color: #9aa2b1;
}
QFrame#StatusPill {
    background: #eef1f6;
    border-radius: 12px;
}
QLabel#StatusText {
    font-size: 12px;
    color: #5a6474;
}
QLabel#StatusDot {
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}
/* 分隔器 */
QSplitter::handle {
    background: #e8eaee;
    width: 1px;
}
/* 右侧面板 */
QWidget#Panel {
    background: #f5f6f8;
}
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e8eaee;
    border-radius: 10px;
}
QLabel#CardTitle {
    font-size: 13px;
    font-weight: 600;
    color: #1d2430;
}
QLabel#FieldLabel {
    font-size: 12px;
    color: #6b7484;
}
QLineEdit {
    background: #fafbfc;
    border: 1px solid #dfe3e9;
    border-radius: 8px;
    padding: 8px 12px;
    color: #1d2430;
    selection-background-color: #d7e3ff;
}
QLineEdit:focus {
    border: 1px solid #4f7cff;
    background: #ffffff;
}
QLineEdit:disabled {
    background: #f0f2f5;
    color: #9aa2b1;
}
QPushButton {
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 500;
}
QPushButton#BtnPrimary {
    background: #4f7cff;
    color: #ffffff;
}
QPushButton#BtnPrimary:hover { background: #3d6bf5; }
QPushButton#BtnPrimary:pressed { background: #335be0; }
QPushButton#BtnPrimary:disabled { background: #c9d4f7; color: #f0f3fc; }
QPushButton#BtnGhost {
    background: #ffffff;
    border: 1px solid #dfe3e9;
    color: #2f3542;
}
QPushButton#BtnGhost:hover { background: #f5f7fb; border-color: #c9d1dc; }
QPushButton#BtnGhost:disabled { color: #b8c0cc; background: #f2f4f7; }
QPushButton#BtnDanger {
    background: #ffffff;
    border: 1px solid #e2d3d3;
    color: #d55f5f;
}
QPushButton#BtnDanger:hover { background: #fdf3f3; }
/* 进度条 */
QProgressBar {
    background: #eceef2;
    border: none;
    border-radius: 8px;
    height: 22px;
    text-align: center;
    font-size: 12px;
    font-weight: 600;
    color: #1d2430;
}
QProgressBar::chunk {
    background: #4f7cff;
    border-radius: 8px;
}
/* 日志 */
QTextEdit {
    background: #fafbfc;
    border: 1px solid #e8eaee;
    border-radius: 8px;
    padding: 8px;
    color: #3a4250;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #d3d8e0;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #b8c0cc; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


class MainWindow(QWidget):
    log_signal = Signal(str)
    progress_signal = Signal(int, int, int)
    done_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(self, profile=None):
        super().__init__()
        self.engine = None
        self._running = False
        self.setObjectName("Root")
        self.setStyleSheet(_styles())
        self._build_ui(profile)
        self.log_signal.connect(self._log)
        self.progress_signal.connect(self._progress_slot)
        self.done_signal.connect(self._done_slot)
        self.error_signal.connect(self._error_slot)
        # 加载已保存的 IMAP 凭据
        acc, auth = config.load_imap_cred()
        self.account_edit.setText(acc)
        self.pwd_edit.setText(auth)
        # 启动后自动打开邮箱，免去手动点击「打开邮箱」
        QTimer.singleShot(0, self.on_load_mail)

    # ---------- UI ----------
    def _build_ui(self, profile=None):
        self.setWindowTitle("发票助手")
        self.resize(1280, 820)
        self.setMinimumSize(1080, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(16)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(10)

        # ---- 左侧：内嵌 QQ 邮箱网页（用于勾选邮件） ----
        self.view = QWebEngineView(profile) if profile else QWebEngineView()
        self.web = WebClient(self.view)
        self.web.log_signal.connect(self._log)
        self.web._install_tracker()
        splitter.addWidget(self.view)

        # ---- 右侧：控制面板 ----
        panel = QWidget()
        panel.setObjectName("Panel")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(12)

        pv.addWidget(self._build_account_card())
        pv.addWidget(self._build_dir_card())
        pv.addWidget(self._build_action_card())
        pv.addWidget(self._build_log_card(), 1)

        splitter.addWidget(panel)
        splitter.setSizes([860, 360])
        body.addWidget(splitter, 1)
        root.addLayout(body, 1)

    def _build_header(self):
        header = QFrame()
        header.setObjectName("Header")
        h = QHBoxLayout(header)
        h.setContentsMargins(24, 14, 24, 14)

        left = QVBoxLayout()
        left.setSpacing(2)
        title = QLabel("发票助手")
        title.setObjectName("AppTitle")
        sub = QLabel("邮箱勾选 · IMAP 附件下载")
        sub.setObjectName("AppSub")
        left.addWidget(title)
        left.addWidget(sub)
        h.addLayout(left)
        h.addStretch(1)

        self.status_label = QLabel()
        self.status_label.setObjectName("StatusText")
        self.status_dot = QLabel()
        self.status_dot.setObjectName("StatusDot")
        pill = QFrame()
        pill.setObjectName("StatusPill")
        ph = QHBoxLayout(pill)
        ph.setContentsMargins(12, 5, 12, 5)
        ph.setSpacing(7)
        ph.addWidget(self.status_dot)
        ph.addWidget(self.status_label)
        h.addWidget(pill)
        self._set_status("未连接")
        return header

    def _card(self, title_text):
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        t = QLabel(title_text)
        t.setObjectName("CardTitle")
        v.addWidget(t)
        return card, v

    def _field_label(self, text):
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        return lb

    def _build_account_card(self):
        card, v = self._card("连接配置")

        v.addWidget(self._field_label("邮箱账号"))
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("QQ号@qq.com")
        v.addWidget(self.account_edit)

        v.addWidget(self._field_label("IMAP 授权码"))
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setPlaceholderText("开启 IMAP 服务后生成的授权码")
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        v.addWidget(self.pwd_edit)

        hint = QLabel("首次连接成功后会记住凭据，失效时重新输入即可。")
        hint.setObjectName("FieldLabel")
        v.addWidget(hint)
        return card

    def _build_dir_card(self):
        card, v = self._card("保存目录")

        row = QHBoxLayout()
        row.setSpacing(8)
        self.dir_edit = QLineEdit(config.DEFAULT_SAVE_DIR)
        row.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("选择…")
        browse_btn.setObjectName("BtnGhost")
        browse_btn.setMinimumHeight(34)
        browse_btn.clicked.connect(self.on_browse)
        row.addWidget(browse_btn)
        v.addLayout(row)

        hint = QLabel("每次下载完成后，会按合计金额归档到子文件夹。")
        hint.setObjectName("FieldLabel")
        v.addWidget(hint)
        return card

    def _build_action_card(self):
        card, v = self._card("下载")

        v.addWidget(self._field_label("打开邮箱后在左侧勾选要下载的邮件"))
        self.go_login_btn = QPushButton("打开邮箱")
        self.go_login_btn.setObjectName("BtnGhost")
        self.go_login_btn.setMinimumHeight(36)
        self.go_login_btn.clicked.connect(self.on_load_mail)
        v.addWidget(self.go_login_btn)

        self.start_btn = QPushButton("开始下载")
        self.start_btn.setObjectName("BtnPrimary")
        self.start_btn.setMinimumHeight(38)
        self.start_btn.clicked.connect(self.on_start)
        v.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("BtnDanger")
        self.stop_btn.setMinimumHeight(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)
        v.addWidget(self.stop_btn)

        self.progress = QProgressBar()
        self.progress.setFormat("")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        v.addWidget(self.progress)
        return card

    def _build_log_card(self):
        card, v = self._card("运行日志")
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        v.addWidget(self.log_view, 1)
        return card

    # ---------- 工具 ----------
    def _log(self, msg):
        self.log_view.append(msg)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _progress_slot(self, processed, total, downloaded):
        self._stop_busy()
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(processed)
        self.progress.setFormat(f"邮件 {processed}/{total} · 已下载 {downloaded} 个 PDF")
        self._set_status(f"下载中 {processed}/{total}", "busy")

    def _error_slot(self, msg):
        self._set_status("下载失败", "error")

    # ---------- 忙碌进度动画 ----------
    def _start_busy(self, text):
        """进入忙碌模式：进度条 0-100 循环滑动，文字始终显示。
        用定时器驱动，即使外层有嵌套事件循环（读取勾选时）也能动。"""
        self._busy_text = text
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat(text)
        if not hasattr(self, "_busy_timer"):
            self._busy_timer = QTimer(self)
            self._busy_timer.setInterval(90)
            self._busy_timer.timeout.connect(self._busy_tick)
        if not self._busy_timer.isActive():
            self._busy_timer.start()

    def _busy_tick(self):
        v = self.progress.value() + 4
        if v >= 100:
            v = 0
        self.progress.setValue(v)
        self.progress.setFormat(self._busy_text)

    def _stop_busy(self):
        if hasattr(self, "_busy_timer"):
            self._busy_timer.stop()

    def _done_slot(self, files):
        self._stop_busy()
        self._set_running(False)
        if self.engine and self.engine.last_save_dir:
            self.dir_edit.setText(self.engine.last_save_dir)
        if files:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.progress.setFormat(f"完成：共下载 {len(files)} 个 PDF")
            self._set_status("完成", "ready")
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("完成（无下载）")
            self._set_status("未匹配到邮件", "error")

    # ---------- 事件 ----------
    def on_browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def on_load_mail(self):
        url = config.MAIL_HOME
        self._set_status("加载邮箱…", "busy")
        self.web.navigate(url)
        self._log(f"已打开：{url}")
        self._log("正在加载页面…请在左侧勾选需要下载的邮件（可进入 报销 文件夹）。")

    def on_start(self):
        if self._running:
            return
        # 立即锁定，防止 get_selected_mails 的嵌套事件循环期间
        # 第二次 click 穿透保护导致重复启动两个下载线程。
        self._running = True
        self.start_btn.setEnabled(False)
        # 进入忙碌态：进度条开始滑动动画
        self._start_busy("准备…")
        try:
            self._start_impl()
        except Exception as e:
            self._stop_busy()
            self._log("❌ " + str(e))
            self._running = False
            self.start_btn.setEnabled(True)

    def _start_impl(self):
        account = self.account_edit.text().strip()
        auth = self.pwd_edit.text().strip()
        if not account or not auth:
            QMessageBox.warning(self, "提示", "请填写 IMAP 账号和授权码。")
            self._running = False
            self.start_btn.setEnabled(True)
            return
        dest = self.dir_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "提示", "请填写保存目录。")
            self._running = False
            self.start_btn.setEnabled(True)
            return

        self._log("读取勾选的邮件…")
        self._start_busy("读取勾选的邮件…")
        self._set_status("读取勾选…", "busy")
        mails = self.web.get_selected_mails()
        if not mails:
            self._stop_busy()
            self._set_status("无勾选邮件", "error")
            QMessageBox.warning(self, "提示", "没有检测到勾选的邮件。请先在左侧网页勾选。")
            self._running = False
            self.start_btn.setEnabled(True)
            return

        self._log(f"勾选邮件 {len(mails)} 封，开始 IMAP 下载 → {dest}")
        self._log(f"账号：{account}")
        # 切换到确定进度：读取阶段 0/len，下载过程由 worker 推进
        self._stop_busy()
        self.progress.setRange(0, max(len(mails), 1))
        self.progress.setValue(0)
        self.progress.setFormat(f"读取勾选 {len(mails)} 封 · 开始连接…")
        self._set_status("连接邮箱…", "busy")

        threading.Thread(
            target=self._run_download,
            args=(account, auth, dest, mails),
            daemon=True,
        ).start()

    def _run_download(self, account, auth, dest, mails):
        """后台线程：连接 IMAP 并下载勾选邮件对应的 PDF。"""
        engine = ImapEngine(
            account, auth,
            on_log=self.log_signal.emit,
            on_progress=self.progress_signal.emit,
        )
        self.engine = engine
        try:
            engine.connect()
            self.log_signal.emit("[状态] 已连接邮箱")
            # 连接成功即保存凭据，后续免输入
            config.save_imap_cred(account, auth)
            files = engine.download_selected_pdfs(dest, mails)
            self.done_signal.emit(files)
        except Exception as e:
            self.log_signal.emit("❌ " + str(e))
            if "login" in str(e).lower() or "AUTHENTICATION" in str(e).upper():
                self.log_signal.emit("⚠ 授权码可能已失效，请重新输入后再试。")
                config.save_imap_cred("", "")
            self.done_signal.emit([])
            self.error_signal.emit(str(e))
        finally:
            try:
                engine.logout()
            except Exception:
                pass

    def on_stop(self):
        if self.engine:
            self.engine.stop_flag = True
            self._log("正在停止…")

    # ---------- 状态 ----------
    _STATUS_COLORS = {
        "ready": "#34c374",
        "busy": "#4f7cff",
        "error": "#d55f5f",
        "idle": "#aab2bf",
    }

    def _set_status(self, text, kind="idle"):
        color = self._STATUS_COLORS.get(kind, "#aab2bf")
        self.status_dot.setStyleSheet(f"background:{color};")
        self.status_label.setText(text)

    def _set_running(self, running):
        self._running = running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)


def run():
    app = QApplication(sys.argv)
    # 用自定义 profile 并显式设置持久化路径（默认 profile 在 PySide6 6.11
    # 下持久化设置不生效，导致每次重启都要重新登录）。
    profile = QWebEngineProfile("invoice_profile", app)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    profile.setCachePath(config.PROFILE_CACHE_DIR)
    profile.setPersistentStoragePath(config.PROFILE_STORAGE_DIR)
    win = MainWindow(profile=profile)
    win.show()
    sys.exit(app.exec())