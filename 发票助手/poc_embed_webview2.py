"""P0' 验证：PySide6 窗口内嵌 WebView2 控件（pythonnet + WebView2 SDK）

验证要点：
1. Qt 布局能包住 WebView2 的 HWND（QWindow.fromWinId + createWindowContainer）
2. 页面正常加载 mail.qq.com（登录态/渲染）
3. 后续可走 CDP 拿 cookie / 抓请求 / JS 注入（端口方式同 P0）

用法：python poc_embed_webview2.py
"""
import os
import sys

SDK = r"D:\AI\git\发票助手\webview2sdk"
sys.path.append(os.path.join(SDK, "lib", "net462"))
os.add_dll_directory(os.path.join(SDK, "runtimes", "win-x64", "native"))

import clr
clr.AddReference("Microsoft.Web.WebView2.Core")
clr.AddReference("Microsoft.Web.WebView2.WinForms")

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel,
                               QHBoxLayout)
from PySide6.QtGui import QWindow, QColor
from PySide6.QtCore import Qt, QTimer

from Microsoft.Web.WebView2.WinForms import WebView2


def build():
    # 自定义环境：开 CDP 端口（后续 cookie/请求抓取/JS 注入都走它）
    from Microsoft.Web.WebView2.Core import (CoreWebView2Environment,
                                             CoreWebView2EnvironmentOptions)
    opts = CoreWebView2EnvironmentOptions()
    opts.AdditionalBrowserArguments = "--remote-debugging-port=9333"
    env = CoreWebView2Environment.CreateAsync(
        None, None, opts).GetAwaiter().GetResult()

    wv = WebView2()
    wv.EnsureCoreWebView2Async(env)
    from System import Uri
    wv.Source = Uri("https://mail.qq.com")
    # 触发 HWND 创建（此时 WebView2 后台开始异步初始化内核）
    hwnd = int(wv.Handle.ToInt64())
    print("WebView2 HWND:", hex(hwnd), flush=True)

    qwin = QWindow.fromWinId(hwnd)
    container = QWidget.createWindowContainer(qwin)
    container.setMinimumSize(400, 300)

    main = QWidget()
    main.setWindowTitle("P0' 嵌入验证 · PySide6 + WebView2")
    lay = QVBoxLayout(main)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(container, 1)

    status = QLabel("初始化中…")
    status.setStyleSheet("background: #1E3A5F; color: white; padding: 6px;")
    lay.addWidget(status)
    main.resize(1400, 860)
    main.show()

    def probe():
        """每 3 秒探测一次：JS 注入 + 标题回读（经 CDP 端口）。"""
        import json
        import urllib.request
        try:
            data = json.load(urllib.request.urlopen(
                "http://127.0.0.1:9333/json", timeout=2))
            page = next(t for t in data if t.get("type") == "page")
            title = page.get("title", "?")
            status.setText("已加载: %s | %s" % (title, page.get("url", "")[:60]))
            print("页面标题:", title, flush=True)
        except Exception as e:
            status.setText("CDP 探测: %s" % str(e)[:60])

    QTimer.singleShot(5000, probe)
    return main


def main():
    app = QApplication(sys.argv)
    win = build()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()