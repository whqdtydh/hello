"""UI 主题管理 — 壁纸玻璃风格，纯亮色，无暗色模式。

所有 QSS 只处理控件外观。玻璃材质由 QPainter 手绘。
仅修改 UI 层，业务逻辑、WebView 内 QQ 邮箱完全不动。
"""

import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QColor


_COLORS = {
    "titlebar_color": "#334155",
    "titlebar_text": "#FFFFFF",
    "primary": "#1E3A5F",
    "primary_hover": "#2D5A8E",
    "primary_pressed": "#0F2440",
    "error": "#DC2626",
    "text": "#1E293B",
    "text2": "#475569",
    "bg": "#F1F5F9",
    "card_bg": "#FFFFFF",
    "input_bg": "#F1F5F9",
    "input_border": "#CBD5E1",
}


class ThemeManager(QWidget):
    @property
    def c(self):
        return _COLORS

    def theme_name(self) -> str:
        return "wallpaper-glass"

    def toggle(self):
        pass

    def qss(self) -> str:
        c = self.c
        return f"""
        QWidget#Root {{
            background: transparent;
            color: {c['text']};
            font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
            font-size: 13px;
        }}
        QLabel {{
            background: transparent;
            color: {c['text']};
        }}
        QLineEdit {{
            background: rgba(255,255,255,0.75);
            border: 1px solid #1E293B;
            border-radius: 10px;
            padding: 8px 12px;
            color: {c['text']};
            font-size: 12px;
            font-weight: 500;
            selection-background-color: {c['primary']};
            selection-color: white;
        }}
        QLineEdit:focus {{
            border: 1.5px solid #000000;
        }}
        QLineEdit:hover {{
            border: 1.5px solid #1E293B;
        }}
        QPushButton {{
            border: 1px solid rgba(200,210,220,0.50);
            border-radius: 10px;
            background: rgba(255,255,255,0.50);
            padding: 8px 16px;
            font-size: 12px;
            font-weight: 600;
            color: #475569;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.70);
            border-color: #94A3B8;
        }}
        QPushButton:pressed {{
            background: rgba(255,255,255,0.40);
        }}
        QPushButton:disabled {{
            background: rgba(255,255,255,0.30);
            color: #94A3B8;
            border-color: rgba(200,210,220,0.30);
        }}
        QTextEdit {{
            background: rgba(255,255,255,0.40);
            border: 1px solid rgba(200,210,220,0.30);
            border-radius: 10px;
            padding: 10px 12px;
            color: #1E293B;
            font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 12px;
            selection-background-color: {c['primary']};
            selection-color: white;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 0;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: rgba(100,116,139,0.25);
            border-radius: 4px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: rgba(100,116,139,0.40);
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            background: transparent;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QCheckBox {{
            spacing: 8px;
            color: {c['text']};
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 2px solid {c['input_border']};
            border-radius: 4px;
            background: rgba(255,255,255,0.50);
        }}
        QCheckBox::indicator:checked {{
            background: {c['primary']};
            border-color: {c['primary']};
        }}
        QCheckBox::indicator:hover {{
            border-color: {c['primary']};
        }}
        QMessageBox {{
            background: {c['card_bg']};
        }}
        QMessageBox QLabel {{
            color: {c['text']};
            font-size: 13px;
        }}
        QMessageBox QPushButton {{
            min-width: 80px;
        }}
        QWebEngineView {{
            background: white;
            border: none;
        }}
        """
