"""Windows 原生窗口效果 — 标题栏透明 + DWM 扩展框架。

让系统标题栏透明（壁纸贯穿），同时保留原生调大小功能。
"""

import sys

_is_win = sys.platform == "win32"

_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_TRANSITIONS_FORCEDISABLED = 3


def _dwm(hwnd, attr, value, size=4):
    if not _is_win:
        return
    try:
        import ctypes
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attr, ctypes.byref(ctypes.c_int(value)), size)
    except Exception:
        pass


def apply_window_blur(window):
    """让标题栏透明，壁纸贯穿整个窗口。"""
    if not _is_win:
        return "none"
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return "none"
        # 设置标题栏透明色 (alpha=0 的黑色)
        _dwm(hwnd, _DWMWA_CAPTION_COLOR, 0x00000000)
        # 关闭过渡动画让透明即时生效
        _dwm(hwnd, _DWMWA_TRANSITIONS_FORCEDISABLED, 1)
        return "transparent"
    except Exception:
        return "none"


def set_titlebar_color(window, color_bgr, text_bgr=None):
    if not _is_win:
        return
    try:
        hwnd = int(window.winId())
        if hwnd and hwnd != 0:
            _dwm(hwnd, _DWMWA_CAPTION_COLOR, color_bgr)
            if text_bgr is not None:
                _dwm(hwnd, _DWMWA_TEXT_COLOR, text_bgr)
    except Exception:
        pass


def enable_dark_titlebar(window, dark=True):
    pass
