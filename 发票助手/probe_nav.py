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


def ev(expr, timeout=15):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    end = time.time() + timeout
    while time.time() < end:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            return m.get("result", {}).get("result", {}).get("value")
    return "TIMEOUT"


def cmd(method, params=None):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            return m


print("导航前 url:", ev("location.href"))
r = cmd("Page.navigate", {"url": "https://wx.mail.qq.com/home/index"})
print("Page.navigate:", json.dumps(r.get("result", {}))[:120])
time.sleep(8)
print("导航后 url:", ev("location.href"))
print("导航后 title:", ev("document.title"))
print("tracker:", ev("window.__invoiceTrackerInstalled"))
ws.close()
