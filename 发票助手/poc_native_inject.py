"""验证 WebView2 原生注入（AddScriptToExecuteOnDocumentCreatedAsync）是否生效。"""
import os
import sys
import time

SDK = r"D:\AI\git\发票助手\webview2sdk"
sys.path.append(os.path.join(SDK, "lib", "net462"))
os.environ["PATH"] = os.path.join(SDK, "runtimes", "win-x64", "native") + os.pathsep + os.environ.get("PATH", "")

import clr
clr.AddReference("Microsoft.Web.WebView2.Core")
clr.AddReference("Microsoft.Web.WebView2.WinForms")

from System import Uri
from Microsoft.Web.WebView2.Core import (CoreWebView2Environment,
                                         CoreWebView2EnvironmentOptions)
from Microsoft.Web.WebView2.WinForms import WebView2

PORT = 9334
opts = CoreWebView2EnvironmentOptions()
opts.AdditionalBrowserArguments = "--remote-debugging-port=%d" % PORT
env = CoreWebView2Environment.CreateAsync(None, None, opts).GetAwaiter().GetResult()

wv = WebView2()
wv.EnsureCoreWebView2Async(env)
wv.Source = Uri("about:blank")
hwnd = int(wv.Handle.ToInt64())
print("HWND:", hex(hwnd))

core = None
for _ in range(40):
    try:
        core = wv.CoreWebView2
        if core is not None:
            break
    except Exception:
        pass
    time.sleep(0.25)
print("CoreWebView2 就绪:", core is not None)

# 原生注入
try:
    rid = core.AddScriptToExecuteOnDocumentCreatedAsync("window.__nativeInject='OK';")
    print("addScript 标识:", rid.GetAwaiter().GetResult())
except Exception as e:
    print("addScript 失败:", repr(e))

# 导航（原生 API）
core.Navigate("data:text/html,<title>inject-test</title><body>hi</body>")
print("已导航 data: URL")
time.sleep(4)

# 通过 CDP 查注入结果
import json
import urllib.request
import websocket
d = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=3))
ws_url = next(t["webSocketDebuggerUrl"] for t in d if t.get("type") == "page")
ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": "window.__nativeInject + '|' + document.title",
                               "returnByValue": True}}))
while True:
    m = json.loads(ws.recv())
    if m.get("id") == 1:
        print("注入检查:", m.get("result", {}).get("result", {}).get("value"))
        break
ws.close()