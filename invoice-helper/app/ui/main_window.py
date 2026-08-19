"""invoice-helper主窗口 — 无边框壁纸玻璃风格。

壁纸直接在 MainWindow.paintEvent 绘制（无独立 _bg widget）。
FramelessWindowHint + nativeEvent 处理拖拽和缩放。
彩色按钮浮动在右上角。
仅修改 UI 层，业务逻辑、WebView 内 QQ 邮箱完全不动。
"""

import os
import sys
import ctypes
import ctypes.wintypes
import threading
import time
import html

from PySide6.QtCore import Qt, Signal, QTimer, QRect, QSize, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QLinearGradient, QPixmap, QFont, QCursor, QBrush, QKeySequence, QWindow
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QTextEdit, QMessageBox, QTableWidget, QHeaderView,
    QCheckBox, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView,
)

from app import config
from app.engine import msgid_service
from app.engine.web_client_wv2 import WebClient
from app.engine.api_downloader import ApiDownloadController
from app.ui.theme import ThemeManager
from app.ui.windows_blur import apply_window_blur

_is_win = sys.platform == "win32"

# Windows API
if _is_win:
    user32 = ctypes.windll.user32
    GWL_STYLE = -16
    WS_THICKFRAME = 0x00040000
    WS_CAPTION = 0x00C0000
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    SWP_FRAMECHANGED = 0x0020
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    HWND_TOP = 0
    WM_SYSCOMMAND = 0x0112
    WM_NCHITTEST = 0x0084
    WM_NCLBUTTONDOWN = 0x00A1
    WM_GETMINMAXINFO = 0x0024
    SPI_GETWORKAREA = 0x0030
    SC_MOVE = 0xF010
    SC_SIZE = 0xF000
    HTCAPTION = 0x0002
    HTCLIENT = 0x0001
    HTLEFT = 0x000A
    HTRIGHT = 0x000B
    HTTOP = 0x000C
    HTTOPLEFT = 0x000D
    HTTOPRIGHT = 0x000E
    HTBOTTOM = 0x000F
    HTBOTTOMLEFT = 0x0010
    HTBOTTOMRIGHT = 0x0011
    _EDGE = 8

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [
            ("ptReserved", ctypes.wintypes.POINT),
            ("ptMaxSize", ctypes.wintypes.POINT),
            ("ptMaxPosition", ctypes.wintypes.POINT),
            ("ptMinTrackSize", ctypes.wintypes.POINT),
            ("ptMaxTrackSize", ctypes.wintypes.POINT),
        ]


def _hit_test_edges(hwnd, msg):
    """在 WM_NCHITTEST 时检测鼠标在窗口边缘，返回系统缩放常量。"""
    x = msg.lParam & 0xFFFF
    y = (msg.lParam >> 16) & 0xFFFF
    pt = ctypes.wintypes.POINT(x, y)
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    # 获取窗口尺寸
    rc = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    cx, cy = pt.x, pt.y
    w, h = rc.right, rc.bottom
    b = _EDGE
    on_l = cx < b
    on_r = cx >= w - b
    on_t = cy < b
    on_b = cy >= h - b
    if on_t and on_l:
        return HTTOPLEFT
    if on_t and on_r:
        return HTTOPRIGHT
    if on_b and on_l:
        return HTBOTTOMLEFT
    if on_b and on_r:
        return HTBOTTOMRIGHT
    if on_t:
        return HTTOP
    if on_b:
        return HTBOTTOM
    if on_l:
        return HTLEFT
    if on_r:
        return HTRIGHT
    return None


# ═══════════════════════════════════════════════════════════════
#  玻璃卡片
# ═══════════════════════════════════════════════════════════════

class _GlassCard(QWidget):
    def __init__(self, parent=None, tint_alpha=170):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._tint_alpha = tint_alpha

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 8))
        p.drawRect(r.adjusted(-1, -1, 1, 6))
        p.setBrush(QColor(255, 255, 255, self._tint_alpha))
        p.drawRect(r)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        p.setBrush(Qt.NoBrush)
        p.drawLine(r.x() + 4, r.y() + 1, r.right() - 4, r.y() + 1)
        p.end()


# ═══════════════════════════════════════════════════════════════
#  进度条
# ═══════════════════════════════════════════════════════════════

class _ProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._ratio = 0.0

    def setRatio(self, r):
        self._ratio = max(0.0, min(1.0, r))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(79, 172, 254, 25))
        p.drawRect(r)
        if self._ratio > 0.001:
            w = max(1, int(r.width() * self._ratio))
            fill = QRect(r.x(), r.y(), w, r.height())
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, QColor(45, 90, 142))
            grad.setColorAt(1.0, QColor(30, 58, 95))
            p.setBrush(grad)
            p.drawRect(fill)
        p.end()


# ═══════════════════════════════════════════════════════════════
#  浮动彩色按钮
# ═══════════════════════════════════════════════════════════════

class _FloatingButtons(QWidget):
    close_clicked = Signal()
    maximize_clicked = Signal()
    minimize_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(70, 20)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 0, 0)
        lay.setSpacing(6)

        btn_min = self._make_btn("—", "#1FA830", "#15803D")
        btn_min.clicked.connect(self.minimize_clicked)
        lay.addWidget(btn_min)

        btn_max = self._make_btn("□", "#DDA020", "#B45309")
        btn_max.clicked.connect(self.maximize_clicked)
        lay.addWidget(btn_max)

        btn_close = self._make_btn("×", "#E0443C", "#B91C1C")
        btn_close.clicked.connect(self.close_clicked)
        lay.addWidget(btn_close)

    @staticmethod
    def _make_btn(glyph, color, hover):
        b = QPushButton(glyph)
        b.setFixedSize(14, 14)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton {{ background: {c}; border: none; border-radius: 7px; "
            "color: rgba(255,255,255,0.0); font-size: 10px; font-weight: 700; }}"
            "QPushButton:hover {{ background: {h}; color: white; "
            "border: 1px solid rgba(0,0,0,0.30); }}"
            "QPushButton:pressed {{ background: rgba(0,0,0,0.25); }}"
            "QPushButton:focus {{ outline: none; }}".format(c=color, h=hover))
        return b


# ═══════════════════════════════════════════════════════════════
#  主窗口
# ═══════════════════════════════════════════════════════════════

class MainWindow(QWidget):
    log_signal = Signal(str, str)   # (msg, group)：group 为空表示全局日志
    progress_signal = Signal(int, int, int)
    done_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(self, web_container=None, wv=None):
        super().__init__()
        self.engine = None
        self._running = False
        self._web_container = web_container  # WebView2 控件容器（由 run() 创建嵌入）
        self._wv = wv                          # WebView2 控件（原生注入用）
        self.setObjectName("Root")
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._wallpaper = self._load_wallpaper()
        self._theme = ThemeManager(self)
        self.setStyleSheet(self._theme.qss())
        self._normal_geometry = None
        self._was_maximized = False
        self._setup_shortcuts()
        self._build_ui()
        # 边勾边读：后台预读器（勾选时立即拉详情，下载时零等待）
        from app.engine.preloader import Preloader
        self.preloader = Preloader(self.web)
        self.preloader.start()
        self.log_signal.connect(self._log)
        self.progress_signal.connect(self._progress_slot)
        self.done_signal.connect(self._done_slot)
        self.error_signal.connect(self._error_slot)
        # 自动更新：启动后台检查，发现新版在结果树提示；点击该条自动下载安装
        self.result_tree.itemClicked.connect(self._on_tree_click)
        from app.engine import updater
        updater.check_update_async(on_found=self._on_update_found)
        QTimer.singleShot(100, self._setup_win32)
        QTimer.singleShot(2000, self._apply_blur)
        QTimer.singleShot(3000, self.on_load_mail)

    def _on_update_found(self, tag, url):
        """发现新版本：结果树顶部提示（点击自动更新）。"""
        self._update_url = url
        self._log(f"[更新] 发现新版本 {tag}，点击此条自动更新", "__update")

    def _on_tree_click(self, item, col):
        """结果树点击：更新提示条 → 确认后后台下载安装并重启。"""
        try:
            if item.data(0, Qt.UserRole) == "update" and getattr(self, "_update_url", None):
                if QMessageBox.question(self, "自动更新",
                                        "将下载并安装新版本，完成后自动重启应用，继续？") \
                        != QMessageBox.Yes:
                    return
                self._log("  开始下载更新…", "__update")
                self._log("  下载与安装期间请勿关闭窗口", "__update")
                threading.Thread(target=self._do_update, daemon=True).start()
        except Exception:
            pass

    def _do_update(self):
        """后台执行：下载 → 安装 → 重启。日志经 log_signal 回到结果树。"""
        from app.engine import updater
        url = self._update_url
        dest = os.path.join(os.path.expanduser("~"), ".invoice_assistant", "update_tmp")
        zip_path = updater.download_update(url, dest)
        if not zip_path:
            self.log_signal.emit("  更新下载失败（网络或超时），请稍后重试", "__update")
            return
        ok = updater.install_update(zip_path, log=lambda m: self.log_signal.emit(m, "__update"))
        if not ok:
            self.log_signal.emit("  更新安装失败，已保持当前版本", "__update")

    def _load_wallpaper(self):
        # PyInstaller 打包后资源在 sys._MEIPASS；开发模式在项目根目录
        base = getattr(sys, "_MEIPASS", None)
        if not base:
            base = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
        for name in ("wallpaper.png", "wallpaper.jpg", "wallpaper.jpeg", "wallpaper.bmp"):
            path = os.path.join(base, name)
            if os.path.exists(path):
                return QPixmap(path)
        return None

    def _setup_win32(self):
        if not _is_win:
            return
        try:
            hwnd = int(self.winId())
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            # 只去掉标题栏和系统菜单，保留最大化/最小化标志（拖拽还原需要）
            style = style & ~WS_CAPTION & ~WS_SYSMENU
            style = style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
            user32.SetWindowLongW(hwnd, GWL_STYLE, style)
            user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        except Exception:
            pass

    def _apply_blur(self):
        apply_window_blur(self)

    # ─── 壁纸绘制（直接画在主窗口，无独立 widget）────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        if self._wallpaper and not self._wallpaper.isNull():
            scaled = self._wallpaper.scaled(
                QSize(r.width(), r.height()),
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (r.width() - scaled.width()) // 2
            y = (r.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
            p.fillRect(r, QColor(255, 255, 255, 25))
        else:
            grad = QLinearGradient(0, 0, r.width(), r.height())
            grad.setColorAt(0.0, QColor(228, 235, 246))
            grad.setColorAt(1.0, QColor(235, 228, 240))
            p.fillRect(r, grad)
        p.end()

    # ─── nativeEvent: 拖拽 + 缩放 ──────────────────────────

    def nativeEvent(self, eventType, message):
        if _is_win:
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == WM_GETMINMAXINFO:
                    # 最大化时限制在屏幕工作区（不含任务栏），保证还原时比例正确
                    hwnd = int(self.winId())
                    mmi = _MINMAXINFO.from_address(int(message))
                    work = ctypes.wintypes.RECT()
                    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
                    mmi.ptMaxSize.x = work.right - work.left
                    mmi.ptMaxSize.y = work.bottom - work.top
                    mmi.ptMaxPosition.x = work.left
                    mmi.ptMaxPosition.y = work.top
                    mmi.ptMaxTrackSize.x = work.right - work.left
                    mmi.ptMaxTrackSize.y = work.bottom - work.top
                    return True, 0
                if msg.message == WM_NCHITTEST:
                    hwnd = int(self.winId())
                    # lParam 是屏幕物理像素坐标
                    x = msg.lParam & 0xFFFF
                    y = (msg.lParam >> 16) & 0xFFFF
                    pt = ctypes.wintypes.POINT(x, y)
                    user32.ScreenToClient(hwnd, ctypes.byref(pt))
                    rc = ctypes.wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(rc))
                    # DPI 缩放：物理像素 → 逻辑像素（与 Qt 坐标一致）
                    scx = rc.right / max(self.width(), 1)
                    scy = rc.bottom / max(self.height(), 1)
                    rx = pt.x / scx
                    ry = pt.y / scy
                    w = self.width()
                    h = self.height()
                    b = _EDGE
                    # 边缘缩放热区
                    on_l = rx < b
                    on_r = rx >= w - b
                    on_t = ry < b
                    on_b = ry >= h - b
                    if on_t and on_l:
                        return True, HTTOPLEFT
                    if on_t and on_r:
                        return True, HTTOPRIGHT
                    if on_b and on_l:
                        return True, HTBOTTOMLEFT
                    if on_b and on_r:
                        return True, HTBOTTOMRIGHT
                    if on_t:
                        return True, HTTOP
                    if on_b:
                        return True, HTBOTTOM
                    if on_l:
                        return True, HTLEFT
                    if on_r:
                        return True, HTRIGHT
                    # 顶部 36px（逻辑像素）：左侧拖拽，按钮区域放行
                    if ry < 36:
                        if rx < w - 80:
                            return True, HTCAPTION
                        else:
                            return True, HTCLIENT
                if msg.message == WM_NCLBUTTONDOWN:
                    # 最大化状态下按标题栏 → 先还原再移动
                    if msg.wParam == HTCAPTION and self.isMaximized():
                        ng = self._normal_geometry
                        hwnd = int(self.winId())
                        def _restore_and_move():
                            self.showNormal()
                            if ng is not None:
                                self.setGeometry(ng)
                            user32.ReleaseCapture()
                            user32.SendMessageW(hwnd, WM_SYSCOMMAND, SC_MOVE | HTCAPTION, 0)
                        QTimer.singleShot(0, _restore_and_move)
                        return True, 0
            except Exception:
                pass
        return False, 0

    # ─── 鼠标光标 ───────────────────────────────────────────

    def mouseMoveEvent(self, event):
        if not _is_win:
            return super().mouseMoveEvent(event)
        pos = event.position().toPoint()
        r = self.rect()
        x, y = pos.x(), pos.y()
        b = _EDGE
        on_l = x < b
        on_r = x > r.width() - b
        on_t = y < b
        on_b = y > r.height() - b
        if on_t and on_l:
            self.setCursor(Qt.SizeFDiagCursor)
        elif on_t and on_r:
            self.setCursor(Qt.SizeBDiagCursor)
        elif on_b and on_l:
            self.setCursor(Qt.SizeBDiagCursor)
        elif on_b and on_r:
            self.setCursor(Qt.SizeFDiagCursor)
        elif on_t or on_b:
            self.setCursor(Qt.SizeVerCursor)
        elif on_l or on_r:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    # ─── 构建 UI ────────────────────────────────────────────

    def _build_ui(self, profile=None):
        self.setWindowTitle("invoice-helper")
        self.resize(1400, 860)
        self.setMinimumSize(1100, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget()
        content.setAttribute(Qt.WA_TranslucentBackground, True)
        content.setStyleSheet("background: transparent;")
        cl = QHBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)

        self._web_card = _GlassCard(tint_alpha=190)
        wc_lay = QVBoxLayout(self._web_card)
        wc_lay.setContentsMargins(0, 0, 0, 0)
        wc_lay.setSpacing(0)
        # 左侧 = WebView2（系统内核）内嵌 QQ 邮箱网页（登录 / 勾选邮件）
        self.web = WebClient(wv=self._wv)
        self.web.log_signal.connect(lambda m: None)  # 网页加载日志不进结果树
        if self._web_container is not None:
            wc_lay.addWidget(self._web_container, 1)
        else:
            # 无容器（测试环境）：占位提示
            placeholder = QLabel("WebView2 未初始化（开发测试模式）")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("background: white; color: #9CA3AF; font-size: 14px;")
            wc_lay.addWidget(placeholder, 1)
        cl.addWidget(self._web_card, 7)

        right_panel = QWidget()
        right_panel.setAttribute(Qt.WA_TranslucentBackground, True)
        right_panel.setStyleSheet("background: transparent;")
        rp = QVBoxLayout(right_panel)
        rp.setContentsMargins(0, 0, 0, 0)
        rp.setSpacing(6)
        rp.addWidget(self._build_dir_card())
        rp.addWidget(self._build_action_card())
        rp.addWidget(self._build_log_card(), 1)
        cl.addWidget(right_panel, 3)
        root.addWidget(content, 1)

        # 浮动按钮 — 放在 content 内最顶层
        self._float_btns = _FloatingButtons(content)
        self._float_btns.close_clicked.connect(self._close_app)
        self._float_btns.minimize_clicked.connect(self.showMinimized)
        self._float_btns.maximize_clicked.connect(self._toggle_maximize)
        self._float_btns.raise_()

    def _setup_shortcuts(self):
        """ESC 键：从最大化还原到原始尺寸。"""
        try:
            from PySide6.QtGui import QShortcut, QKeySequence
            sc = QShortcut(QKeySequence(Qt.Key_Escape), self)
            sc.activated.connect(self._restore_from_max)
        except Exception:
            pass

    def _restore_from_max(self):
        if self.isMaximized():
            self.showNormal()
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)

    def _close_app(self):
        """关闭按钮：正常关闭窗口并退出应用。"""
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event):
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            if hasattr(self, "_normal_geometry") and self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            if _is_win:
                try:
                    hwnd = int(self.winId())
                    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                    style = style & ~WS_CAPTION & ~WS_SYSMENU
                    style = style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
                    user32.SetWindowLongW(hwnd, GWL_STYLE, style)
                    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0,
                        SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                except Exception:
                    pass
        else:
            self._normal_geometry = self.geometry()
            self.showMaximized()

    def changeEvent(self, event):
        """最大化后通过拖拽标题栏还原时，恢复原始几何比例。"""
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            was_max = getattr(self, "_was_maximized", False)
            self._was_maximized = self.isMaximized()
            if was_max and not self.isMaximized() and not self.isMinimized():
                ng = getattr(self, "_normal_geometry", None)
                if ng is not None:
                    QTimer.singleShot(0, lambda g=ng: self.setGeometry(g))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_float_btns"):
            self._float_btns.move(self.width() - 80, 4)

    # ─── 右侧面板卡片 ────────────────────────────────────────

    def _card(self, title_text, tint_alpha=170):
        card = _GlassCard(tint_alpha=tint_alpha)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        t = QLabel(title_text)
        t.setStyleSheet("font-size: 13px; font-weight: 700; color: #1E293B; background: transparent;")
        v.addWidget(t)
        return card, v

    # ---------- 右侧控制 ----------

    def _build_dir_card(self):
        card, v = self._card("保存目录")
        row = QHBoxLayout()
        row.setSpacing(6)
        self.dir_edit = QLineEdit(config.DEFAULT_SAVE_DIR)
        # 边框加深加粗 + 路径字体加粗，便于查看
        self.dir_edit.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.75); "
            "border: 2px solid #111827; border-radius: 8px; "
            "padding: 8px 12px; color: #111827; font-size: 12px; font-weight: 700; "
            "selection-background-color: #1E3A5F; selection-color: white; }")
        row.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("浏览")
        browse_btn.setMinimumHeight(32)
        browse_btn.setStyleSheet(
            "QPushButton { border: 2px solid #111827; border-radius: 8px; "
            "background: rgba(255,255,255,0.50); font-size: 12px; font-weight: 700; color: #374151; }"
            "QPushButton:hover { background: rgba(255,255,255,0.80); }"
            "QPushButton:pressed { background: rgba(30,58,95,0.15); }")
        browse_btn.clicked.connect(self.on_browse)
        row.addWidget(browse_btn)
        v.addLayout(row)
        # 邮箱操作：打开邮箱网页 / 更换 QQ 邮箱
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        open_mail_btn = QPushButton("打开邮箱网页")
        open_mail_btn.setMinimumHeight(32)
        open_mail_btn.setStyleSheet(
            "QPushButton { border: 1px solid rgba(200,210,220,0.50); border-radius: 8px; "
            "background: rgba(255,255,255,0.50); font-size: 12px; font-weight: 600; color: #374151; }"
            "QPushButton:hover { background: rgba(255,255,255,0.70); border-color: #6B7280; }")
        open_mail_btn.clicked.connect(self.on_load_mail)
        btn_row.addWidget(open_mail_btn, 1)
        switch_mail_btn = QPushButton("更换 QQ 邮箱")
        switch_mail_btn.setMinimumHeight(32)
        switch_mail_btn.setStyleSheet(
            "QPushButton { border: 1px solid rgba(200,210,220,0.50); border-radius: 8px; "
            "background: rgba(255,255,255,0.50); font-size: 12px; font-weight: 600; color: #374151; }"
            "QPushButton:hover { background: rgba(255,255,255,0.70); border-color: #6B7280; }")
        switch_mail_btn.clicked.connect(self.on_switch_account)
        btn_row.addWidget(switch_mail_btn, 1)
        v.addLayout(btn_row)
        return card

    def _build_action_card(self):
        card, v = self._card("下载控制")
        prog_row = QHBoxLayout()
        prog_row.setSpacing(6)
        self._prog_label = QLabel("下载进度")
        self._prog_label.setStyleSheet("font-size: 11px; color: #374151; background: transparent;")
        prog_row.addWidget(self._prog_label)
        prog_row.addStretch(1)
        self._prog_pct = QLabel("0%")
        self._prog_pct.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #1E3A5F; "
            "font-family: 'Cascadia Mono', monospace; background: transparent;")
        prog_row.addWidget(self._prog_pct)
        v.addLayout(prog_row)
        self._prog_bar = _ProgressBar()
        v.addWidget(self._prog_bar)
        self.start_btn = QPushButton("开始下载")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #2D5A8E,stop:1 #1E3A5F); "
            "color: #ffffff; border: none; border-radius: 8px; font-size: 13px; font-weight: 700; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3A6FA0,stop:1 #2D5A8E); }"
            "QPushButton:pressed { background: #1E3A5F; }"
            "QPushButton:disabled { background: rgba(30,58,95,0.30); color: rgba(255,255,255,0.60); }")
        self.start_btn.clicked.connect(self.on_start)
        v.addWidget(self.start_btn)
        self.progress = _DummyProgress()
        return card

    def _build_log_card(self):
        card, v = self._card("下载结果")
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("font-size: 11px; color: #1F2937; background: transparent;")
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_dot.setStyleSheet("background: #6B7280; border-radius: 4px;")
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        v.addLayout(status_row)
        # 结果树：顶部总结 + 每封邮件一个分组（可 Ctrl+C 复制选中内容）
        self.result_tree = _CopyableTree()
        self.result_tree.setHeaderHidden(True)
        self.result_tree.setIndentation(14)
        self.result_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_tree.setStyleSheet(
            "QTreeWidget { background: rgba(255,255,255,0.40); border: 1px solid rgba(200,210,220,0.30); "
            "border-radius: 8px; color: #111827; font-size: 12px; }"
            "QTreeWidget::item { padding: 3px 4px; }"
            "QTreeWidget::item:selected { background: rgba(30,58,95,0.12); color: #111827; }")
        v.addWidget(self.result_tree, 1)
        self._tree_map = {}   # group → QTreeWidgetItem
        return card

    def _log(self, msg, group=""):
        if not group:
            group = "__global__"  # 全局日志归入「系统」组
        top = self._tree_map.get(group)
        if top is None:
            top = QTreeWidgetItem([msg])
            self._tree_map[group] = top
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setForeground(0, QBrush(QColor("#111827")))
            if group.startswith("__summary"):
                # 总结行：依次排在所有邮件分组之前（保持先后顺序）
                idx = 0
                while idx < self.result_tree.topLevelItemCount():
                    if self.result_tree.topLevelItem(idx).data(0, Qt.UserRole) != "summary":
                        break
                    idx += 1
                self.result_tree.insertTopLevelItem(idx, top)
                top.setData(0, Qt.UserRole, "summary")
            elif group == "__update":
                # 更新提示条：置顶显示，点击触发自动更新
                self.result_tree.insertTopLevelItem(0, top)
                top.setData(0, Qt.UserRole, "update")
                top.setForeground(0, QBrush(QColor("#B45309")))
            else:
                self.result_tree.addTopLevelItem(top)
            return
        if group.startswith("__summary"):
            # 顶部总结行：随下载进度更新（占位 → 完整内容）
            top.setText(0, msg)
            return
        if msg.startswith("[处理]"):
            top.setText(0, msg[len("[处理]"):].strip())
            top.setForeground(0, QBrush(QColor("#1E3A5F")))
        elif msg.startswith("[成功]"):
            child = QTreeWidgetItem([msg[len("[成功]"):].strip()])
            child.setForeground(0, QBrush(QColor("#059669")))
            f = child.font(0)
            f.setBold(True)
            child.setFont(0, f)
            top.addChild(child)
        elif msg.startswith("[失败]"):
            child = QTreeWidgetItem([msg[len("[失败]"):].strip()])
            child.setForeground(0, QBrush(QColor("#DC2626")))
            f = child.font(0)
            f.setBold(True)
            child.setFont(0, f)
            top.addChild(child)
        else:
            top.addChild(QTreeWidgetItem([msg]))

    def _progress_slot(self, processed, total, downloaded):
        self._set_status(f"下载中 {processed}/{total} · {downloaded} 个 PDF", "busy")
        if total > 0:
            ratio = processed / total
            self._prog_bar.setRatio(ratio)
            self._prog_pct.setText(f"{ratio * 100:.1f}%")

    def _error_slot(self, msg):
        self._set_status("下载失败", "error")

    def _set_status(self, text, kind="idle"):
        colors = {"ready": "#059669", "busy": "#1E3A5F", "error": "#DC2626", "idle": "#6B7280"}
        self.status_dot.setStyleSheet(f"background:{colors.get(kind, '#6B7280')}; border-radius: 4px;")
        self.status_label.setText(text)

    def _start_busy(self, text):
        self._set_status(text, "busy")
    def _stop_busy(self):
        pass

    def _done_slot(self, files):
        self._set_running(False)
        ctrl = getattr(self, "api_ctrl", None)
        if ctrl and ctrl.save_dir:
            self.dir_edit.setText(ctrl.save_dir)
        self._prog_bar.setRatio(0.0)
        self._prog_pct.setText("0%")
        if files:
            self._set_status(f"完成 · {len(files)} 个 PDF", "ready")
        else:
            self._set_status("未匹配到邮件", "error")
        QTimer.singleShot(3000, lambda: self._set_status("未连接"))

    def on_browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def on_load_mail(self):
        url = config.MAIL_HOME
        self._set_status("加载邮箱…", "busy")
        self.web.navigate(url)
        self._log(f"已打开：{url}")
        self._log("请在左侧勾选需要下载的邮件（可进入 报销 文件夹）。")

    def on_switch_account(self):
        """更换 QQ 邮箱：清除本地登录凭证并重新打开登录页。"""
        self._log("正在清除登录凭证，准备更换 QQ 邮箱…")
        try:
            self.web.cookies.clear_all()
            self._log("已清除登录凭证，正在打开登录页…")
        except Exception as e:
            self._log(f"清除凭证失败: {str(e)[:60]}")
        self.web.navigate(config.MAIL_HOME)
        self._log(f"已打开：{config.MAIL_HOME}")

    def on_start(self):
        if self._running:
            return
        self._running = True
        self.start_btn.setEnabled(False)
        self._prog_bar.setRatio(0.0)
        self._prog_pct.setText("0%")
        self._start_busy("准备…")
        try:
            self._start_impl()
        except Exception as e:
            self._stop_busy()
            self._log("" + str(e))
            self._running = False
            self.start_btn.setEnabled(True)

    def _start_impl(self):
        dest = self.dir_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "提示", "请填写保存目录。")
            self._running = False
            self.start_btn.setEnabled(True)
            return
        # 接口失效提醒：若注册表中有 failed 接口，提示用户手动操作网页供自动学习
        try:
            from app.engine.api_registry import ApiRegistry
            failed = ApiRegistry().failed_endpoints()
            if failed:
                self._log(f"检测到接口可能已变更: {', '.join(failed)}")
                self._log("若本次下载失败，请在左侧网页手动操作一次（打开收件箱/打开邮件/下载发票），"
                          "再点击开始下载，程序将自动学习新接口。")
        except Exception:
            pass
        self._log("读取勾选的邮件…")
        self._start_busy("读取勾选的邮件…")
        # 读取网页中勾选的邮件（click 延迟校验 + DOM 扫描双保险）
        mails = self.web.get_selected_mails()
        if not mails:
            self._stop_busy()
            self._set_status("无勾选邮件", "error")
            QMessageBox.warning(self, "提示", "没有检测到勾选的邮件。请在左侧 QQ 邮箱网页中勾选要下载的邮件。")
            self._running = False
            self.start_btn.setEnabled(True)
            return
        sid = self.web.get_sid()
        if not sid:
            self._stop_busy()
            self._set_status("未登录网页", "error")
            QMessageBox.warning(self, "提示", "未检测到登录状态。请先打开邮箱网页并登录，再刷新列表。")
            self._running = False
            self.start_btn.setEnabled(True)
            return
        self._log(f"勾选邮件 {len(mails)} 封，开始 API 下载 → {dest}")
        for _m in mails:
            self._log(f"  [勾选] mailid={_m.get('mailid','')[:40]} subj={(_m.get('subject') or '')[:30]!r}")
        self._stop_busy()
        self.progress.setRange(0, max(len(mails), 1))
        self.progress.setValue(0)
        self.progress.setFormat(f"读取勾选 {len(mails)} 封 · 开始下载…")
        self._set_status("下载中…", "busy")
        threading.Thread(target=self._run_api_download, args=(dest, mails, sid), daemon=True).start()

    def _run_api_download(self, dest, mails, sid):
        try:
            ctrl = ApiDownloadController(self.web, dest, on_log=self.log_signal.emit,
                                         on_progress=self.progress_signal.emit,
                                         preloader=self.preloader)
            self.api_ctrl = ctrl
            files = ctrl.run(mails, sid=sid)
            self.done_signal.emit(files)
        except Exception as e:
            self.log_signal.emit("失败 " + str(e), "")
            self.done_signal.emit([])
            self.error_signal.emit(str(e))

    def _set_running(self, running):
        self._running = running
        self.start_btn.setEnabled(not running)


class _CopyableTree(QTreeWidget):
    """支持 Ctrl+C 复制选中条目文本的结果树。

    折叠状态下复制顶级项时，会递归收集其下所有子日志（带缩进层级），
    配合 Ctrl+A 全选即可一次复制整棵树的全部内容。
    """

    def _collect_text(self, item, depth=0):
        lines = [("  " * depth) + item.text(0)]
        for i in range(item.childCount()):
            lines.extend(self._collect_text(item.child(i), depth + 1))
        return lines

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Copy):
            items = self.selectedItems()
            if items:
                lines = []
                for it in reversed(items):
                    lines.extend(self._collect_text(it))
                QApplication.clipboard().setText("\n".join(lines))
                e.accept()
                return
        super().keyPressEvent(e)


class _DummyProgress:
    def setRange(self, *a): pass
    def setValue(self, *a): pass
    def setFormat(self, *a): pass
    def value(self): return 0


def run():
    app = QApplication(sys.argv)
    # WebView2 控件（pythonnet 宿主）：创建 → 句柄嵌入 Qt 容器
    from app.engine import webview2_host
    wv, hwnd, _port = webview2_host.create_view(
        user_data_folder=config.PROFILE_DIR)
    qwin = QWindow.fromWinId(hwnd)
    container = QWidget.createWindowContainer(qwin)
    msgid_service.start_server()
    win = MainWindow(web_container=container, wv=wv)
    win.show()
    sys.exit(app.exec())
