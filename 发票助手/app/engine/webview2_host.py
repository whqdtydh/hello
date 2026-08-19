"""WebView2 宿主：pythonnet 封装（系统内核渲染，Qt 只做窗口宿主）。

职责：
- 加载 WebView2 SDK（WebView2Loader.dll + Core + WinForms）
- 创建自定义环境（CDP 端口 9333，供自动化/抓包/cookie 读取）
- 创建 WebView2 控件，返回其 HWND 供 Qt 嵌入（QWindow.fromWinId）

打包注意：WebView2Loader.dll 需与 exe 同级或在其搜索路径；
Core/WinForms dll 随包即可（<2MB）。运行时内核走系统 WebView2 Runtime。
"""

import os
import sys
import time

# webview2_host.py 位于 app/engine/ → 项目根 = dirname × 3
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SDK_DIR = os.path.join(_PROJECT_ROOT, "webview2sdk")
NET462 = os.path.join(SDK_DIR, "lib", "net462")
NATIVE = os.path.join(SDK_DIR, "runtimes", "win-x64", "native")
CDP_PORT = 9333

_initialized = False


def _ensure_loaded():
    """加载 SDK（幂等）：Core/WinForms 程序集 + Loader dll 搜索路径。"""
    global _initialized
    if _initialized:
        return
    # .NET 找 WebView2Loader.dll：当前目录 / PATH / System32
    loader_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    for d in (loader_dir, NATIVE):
        if d and d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    sys.path.append(NET462)
    if not os.path.exists(os.path.join(NET462, "Microsoft.Web.WebView2.Core.dll")):
        raise RuntimeError("WebView2 SDK 缺失: %s" % NET462)
    import clr  # noqa
    clr.AddReference("Microsoft.Web.WebView2.Core")
    clr.AddReference("Microsoft.Web.WebView2.WinForms")
    _initialized = True


def create_view(user_data_folder=None):
    """创建 WebView2 控件并初始化（含 CDP 端口）。

    返回 (wv_control, hwnd, cdp_port)：
    - wv_control：WinForms WebView2 控件（须保持引用，防 GC）
    - hwnd：控件窗口句柄（交给 QWindow.fromWinId）
    """
    _ensure_loaded()
    from System import Uri
    from Microsoft.Web.WebView2.Core import (CoreWebView2Environment,
                                             CoreWebView2EnvironmentOptions)
    from Microsoft.Web.WebView2.WinForms import WebView2

    opts = CoreWebView2EnvironmentOptions()
    opts.AdditionalBrowserArguments = "--remote-debugging-port=%d" % CDP_PORT
    opts.AreBrowserExtensionsEnabled = False
    env = CoreWebView2Environment.CreateAsync(
        None, user_data_folder or None, opts).GetAwaiter().GetResult()

    wv = WebView2()
    wv.EnsureCoreWebView2Async(env)
    wv.Source = Uri("about:blank")
    hwnd = int(wv.Handle.ToInt64())
    # 等待 CoreWebView2 内核就绪（供原生注入 API 使用）
    core = None
    for _ in range(60):
        try:
            core = wv.CoreWebView2
            if core is not None:
                break
        except Exception:
            pass
        time.sleep(0.25)
    # 等待 CDP 端口就绪（保证返回后 WebClient 构造不阻塞 GUI 线程）
    _wait_cdp_ready(CDP_PORT, timeout=20)
    return wv, hwnd, CDP_PORT


def _wait_cdp_ready(port, timeout=20):
    """轮询 /json 直到 CDP 端口可用（WebClient 依赖它）。"""
    import json
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            json.load(urllib.request.urlopen(
                "http://127.0.0.1:%d/json" % port, timeout=1))
            return True
        except Exception:
            time.sleep(0.3)
    return False


def add_script_on_document_created(wv, source):
    """WebView2 原生注入：所有文档加载前执行（CDP 的 addScript 在 WebView2 不生效）。

    必须在 CoreWebView2 就绪后调用；返回注入标识（失败返回 None）。
    """
    try:
        core = wv.CoreWebView2
        if core is None:
            return None
        task = core.AddScriptToExecuteOnDocumentCreatedAsync(source)
        return task.GetAwaiter().GetResult()
    except Exception:
        return None


def navigate(wv, url):
    """导航到 url（页面级，同 CDP Page.navigate 语义）。"""
    from System import Uri
    wv.Source = Uri(url)
