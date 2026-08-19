import json
import sys
import time
import urllib.request
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

data = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=3))
print("target 数量:", len(data))
for i, t in enumerate(data):
    print("--- target %d ---" % i)
    print("  type:", t.get("type"))
    print("  title:", t.get("title", ""))
    print("  url:", t.get("url", "")[:80])
    print("  ws:", t.get("webSocketDebuggerUrl", "")[:60])
    ws_url = t.get("webSocketDebuggerUrl")
    if not ws_url:
        continue
    try:
        ws = websocket.create_connection(ws_url, timeout=8, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": "location.href",
                                       "returnByValue": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                print("  实际 href:", m.get("result", {}).get("result", {}).get("value"))
                break
        ws.close()
    except Exception as e:
        print("  连接失败:", str(e)[:80])
