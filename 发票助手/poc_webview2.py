"""P0 验证：WebView2（pywebview + CDP）能否替代 QtWebEngine

验证三个关键能力（可行则方案 A 成立）：
1. cookie 全量读取（含 HttpOnly 的 skey/p_skey —— 登录态持久化给 requests 的关键）
2. JS 注入（勾选监控/自动化的基础）
3. 网络请求抓取（接口自动学习的替代实现）

用法：python poc_webview2.py
窗口打开后请扫码登录 QQ 邮箱，登录成功后自动执行验证并输出结果。
"""
import json
import os
import sys
import threading
import time
import urllib.request

import webview

CDP_PORT = 9333
LOG = "poc_webview2.log"

# 关键 cookie 白名单（存在即证明 HttpOnly cookie 可读）
# 新版 QQ 邮箱用 xm_skey 系列；老版用 skey/p_skey——两个时代都验证
KEY_COOKIES = ("xm_skey", "xm_sid", "xm_uin", "skey", "p_skey", "p_uin", "pt2gguin", "uin")

# 注入的 JS（模拟勾选上报脚本注入，改写页面标题为已验证标记）
INJECT_JS = "document.title = '[POC-INJECTED] ' + document.title;"


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_page_ws_url():
    """轮询 CDP /json 接口，等待 page target 出现。"""
    for _ in range(120):
        try:
            data = json.load(urllib.request.urlopen(
                "http://127.0.0.1:%d/json" % CDP_PORT, timeout=2))
            for t in data:
                if t.get("type") == "page":
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(1)
    return None


class CDP:
    """极简 CDP 客户端（请求/响应 + 事件）。"""

    def __init__(self, ws_url):
        import websocket
        # suppress_origin：新版 Chromium 默认拒绝带 Origin 的 WS 握手（除非开 --remote-allow-origins）
        self.ws = websocket.create_connection(ws_url, timeout=60,
                                              suppress_origin=True)
        self._id = 0
        self._events = []   # 收到的 Network.* 事件暂存

    def call(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                return msg.get("result", {})
            if msg.get("method", "").startswith("Network."):
                self._events.append(msg)

    def drain_events(self):
        """取出暂存的网络事件。"""
        out, self._events = self._events, []
        return out

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def run_verification(ws_url):
    log("CDP 已连接，等待扫码登录（最长 5 分钟）...")
    cdp = CDP(ws_url)
    try:
        # --- 能力 3：开启网络抓取（先于登录，登录过程的请求也能看到）---
        cdp.call("Network.enable")

        # --- 等待登录：轮询 cookie 直到出现 skey ---
        logged_in = False
        deadline = time.time() + 300
        while time.time() < deadline:
            result = cdp.call("Network.getCookies")
            cookies = result.get("cookies", [])
            names = {c["name"] for c in cookies}
            if "skey" in names or "xm_skey" in names:
                logged_in = True
                break
            time.sleep(3)
        if not logged_in:
            log("验证失败：5 分钟内未检测到登录态")
            return False

        log("===== 已检测到登录（skey 存在）=====")

        # --- 能力 1：HttpOnly cookie 全量读取 ---
        result = cdp.call("Network.getCookies")
        cookies = result.get("cookies", [])
        all_names = {c["name"] for c in cookies}
        # 新版（xm_*）或老版（skey/p_skey）任一整套命中即视为关键 cookie 齐全
        new_gen = {k for k in ("xm_skey", "xm_sid", "xm_uin") if k in all_names}
        old_gen = {k for k in ("skey", "p_skey", "p_uin", "pt2gguin", "uin") if k in all_names}
        gen_ok = len(new_gen) == 3 or len(old_gen) >= 3
        missing = [k for k in KEY_COOKIES if k not in all_names]
        http_only = [c for c in cookies if c.get("httpOnly")]
        log("cookie 总数: %d（其中 HttpOnly: %d 个）" % (len(cookies), len(http_only)))
        log("关键 cookie: %s" % ("全部命中 ✔" if gen_ok else "缺失: %s" % missing))
        skey = next((c for c in cookies if c["name"] == "skey"), None)
        if skey:
            log("skey 示例: %s... (HttpOnly=%s)" % (skey["value"][:12], skey.get("httpOnly")))

        # --- 能力 2：JS 注入（页面标题改写验证）---
        title_before = cdp.call("Runtime.evaluate",
                                {"expression": "document.title"})
        cdp.call("Runtime.evaluate", {"expression": INJECT_JS})
        title_after = cdp.call("Runtime.evaluate",
                               {"expression": "document.title"})
        log("JS 注入: 标题 '%s' -> '%s' %s"
            % (title_before.get("result", {}).get("value", "?"),
               title_after.get("result", {}).get("value", "?"),
               "✔" if "[POC-INJECTED]" in str(title_after.get("result", {}).get("value", "")) else "✘"))

        # --- 能力 3 补充：跳收件箱抓 XHR（接口学习可行性）---
        cdp.call("Page.navigate", {"url": "https://mail.qq.com/cgi-bin/frame_html?sid=&r=&lang=zh"})
        time.sleep(6)
        events = cdp.drain_events()
        xhrs = [e for e in events
                if e.get("method") == "Network.requestWillBeSent"
                and e.get("params", {}).get("type") == "XHR"]
        log("跳转收件箱后抓取 XHR 请求: %d 个" % len(xhrs))
        for e in xhrs[:5]:
            url = e["params"]["request"]["url"][:120]
            log("   XHR: %s" % url)

        ok = gen_ok and xhrs
        log("===== P0 验证结果: %s =====" % ("通过 ✔（cookie 全量 + JS 注入 + 请求抓取）" if ok else "部分失败，见上方明细"))
        return ok
    finally:
        cdp.close()


def main():
    # pywebview 通过 settings 传 remote-debugging-port（会覆盖环境变量方式）
    webview.settings['REMOTE_DEBUGGING_PORT'] = CDP_PORT
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("")
    log("启动 WebView2 窗口，请扫码登录 QQ 邮箱...")

    window = webview.create_window("POC WebView2", "https://mail.qq.com",
                                   width=1280, height=880)

    def worker():
        ws_url = get_page_ws_url()
        if not ws_url:
            log("验证失败：CDP 端口 %d 未出现" % CDP_PORT)
            return
        ok = run_verification(ws_url)
        log("关闭窗口")
        window.destroy()

    threading.Thread(target=worker, daemon=True).start()
    webview.start()
    log("POC 结束")


if __name__ == "__main__":
    main()