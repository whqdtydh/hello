import json
import sys
import time
import urllib.request
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

d = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=3))
ws_url = next(t["webSocketDebuggerUrl"] for t in d if t.get("type") == "page")
ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
mid = 0


def cmd(method, params=None):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            return m


def ev(expr):
    r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("result", {}).get("value")


# 1. 注册测试脚本
r = cmd("Page.addScriptToEvaluateOnNewDocument",
        {"source": "window.__probeInjected = 'YES';"})
print("addScript:", json.dumps(r.get("result", {}))[:80])
# 2. 导航触发新文档
cmd("Page.navigate", {"url": "https://wx.mail.qq.com/home/index"})
time.sleep(8)
print("注入检查:", ev("window.__probeInjected"))
print("tracker:", ev("window.__invoiceTrackerInstalled"))
print("url:", ev("location.href"))
ws.close()