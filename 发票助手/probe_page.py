import json
import sys
import urllib.request
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

d = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=3))
ws_url = next(t["webSocketDebuggerUrl"] for t in d if t.get("type") == "page")
ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)


def ev(expr, mid):
    ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            return m.get("result", {}).get("result", {}).get("value")


print("tracker:", ev("window.__invoiceTrackerInstalled", 1))
print("sid:", ev("(location.href.match(/[?&]sid=([^&#]+)/)||[])[1]||''", 2))
print("title:", ev("document.title", 3))
print("url:", ev("location.href", 4))
ws.close()
