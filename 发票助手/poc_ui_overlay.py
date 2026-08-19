"""P1 原型：WebView2 单窗口 + 注入式右侧面板 UI（替代 QtWidgets 界面）

架构验证点：
1. 覆盖层注入时机与稳定性（QQ 邮箱 SPA 重绘后仍固定右上）
2. pywebview js_api 双向通信（按钮 → Python、Python → 面板日志）
3. 现有视觉主题复刻（半透明卡片 / 毛玻璃 / 状态点 / 进度条 / 结果树）
4. Ctrl+C 复制选中文本

用法：python poc_ui_overlay.py（已登录态直接进收件箱）
"""
import json
import os
import threading
import time

import webview

PANEL_HTML = r"""
<div id="poc-panel">
  <style>
    #poc-panel{position:fixed;top:14px;right:14px;bottom:14px;width:336px;z-index:2147483000;
      font-family:'Microsoft YaHei',system-ui,sans-serif;color:#111827;user-select:text;}
    #poc-panel *{box-sizing:border-box;margin:0;padding:0;}
    .poc-card{position:absolute;inset:0;display:flex;flex-direction:column;gap:10px;
      background:rgba(255,255,255,0.72);border:1px solid rgba(200,210,220,0.45);
      border-radius:12px;padding:14px;backdrop-filter:blur(18px);
      box-shadow:0 6px 24px rgba(30,58,95,0.10);}
    .poc-status{display:flex;align-items:center;gap:8px;font-size:13px;}
    .poc-dot{width:8px;height:8px;border-radius:4px;background:#6B7280;flex:none;}
    .poc-status-txt{flex:1;color:#111827;}
    .poc-progress{display:none;gap:8px;align-items:center;}
    .poc-bar{flex:1;height:6px;border-radius:3px;background:rgba(30,58,95,0.10);overflow:hidden;}
    .poc-bar-fill{height:100%;width:0%;background:#1E3A5F;border-radius:3px;transition:width .3s;}
    .poc-pct{font-size:11px;color:#1E3A5F;min-width:38px;text-align:right;}
    .poc-btn{width:100%;padding:9px 0;border:0;border-radius:8px;font-size:14px;font-weight:600;
      cursor:pointer;background:#1E3A5F;color:#fff;}
    .poc-btn:active{transform:scale(.98);}
    .poc-btn2{background:rgba(30,58,95,0.08);color:#1E3A5F;}
    .poc-tree{flex:1;overflow-y:auto;background:rgba(255,255,255,0.55);
      border:1px solid rgba(200,210,220,0.35);border-radius:8px;padding:6px 8px;font-size:12px;}
    .poc-tree::-webkit-scrollbar{width:6px;}
    .poc-tree::-webkit-scrollbar-thumb{background:rgba(30,58,95,0.2);border-radius:3px;}
    .poc-node{margin:1px 0;}
    .poc-node-top{cursor:pointer;font-weight:600;color:#111827;padding:2px 4px;border-radius:4px;}
    .poc-node-top:hover{background:rgba(30,58,95,0.06);}
    .poc-node-top::before{content:'▸ ';color:#9CA3AF;}
    .poc-node.open>.poc-node-top::before{content:'▾ ';}
    .poc-node-children{margin-left:14px;border-left:1px dashed rgba(200,210,220,0.8);padding-left:8px;display:none;}
    .poc-node.open>.poc-node-children{display:block;}
    .poc-child{padding:2px 4px;border-radius:4px;}
    .poc-ok{color:#059669;font-weight:600;}
    .poc-err{color:#DC2626;font-weight:600;}
    .poc-warn{color:#B45309;}
    .poc-tip{font-size:11px;color:#6B7280;text-align:center;}
  </style>
  <div class="poc-card">
    <div class="poc-status">
      <span class="poc-dot" id="poc-dot"></span>
      <span class="poc-status-txt" id="poc-status">就绪 · 请勾选左侧邮件</span>
    </div>
    <div class="poc-progress" id="poc-progress">
      <div class="poc-bar"><div class="poc-bar-fill" id="poc-bar-fill"></div></div>
      <span class="poc-pct" id="poc-pct">0%</span>
    </div>
    <button class="poc-btn" id="poc-start">开始下载</button>
    <div class="poc-tree" id="poc-tree"></div>
    <button class="poc-btn poc-btn2" id="poc-switch">更换 QQ 邮箱</button>
    <div class="poc-tip">点击分组展开/收起 · Ctrl+C 复制选中内容</div>
  </div>
</div>
"""

PANEL_JS = r"""
window.__poc = {
  tree: {},
  _esc: s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  status(kind, txt) {
    const colors = {ready:'#059669', busy:'#1E3A5F', error:'#DC2626', idle:'#6B7280'};
    document.getElementById('poc-dot').style.background = colors[kind] || '#6B7280';
    document.getElementById('poc-status').textContent = txt;
  },
  progress(pct) {
    document.getElementById('poc-progress').style.display = pct >= 0 ? 'flex' : 'none';
    document.getElementById('poc-bar-fill').style.width = Math.max(0, Math.min(100, pct)) + '%';
    document.getElementById('poc-pct').textContent = pct.toFixed(1) + '%';
  },
  // group: '' 全局组（不显示标题）；否则显示可折叠分组标题
  log(msg, group) {
    const tree = document.getElementById('poc-tree');
    let top = this.tree[group || '__g'];
    if (!top) {
      if (!group) { top = null; }
      else {
        top = document.createElement('div');
        top.className = 'poc-node';
        const head = document.createElement('div');
        head.className = 'poc-node-top';
        head.textContent = this._esc(msg);
        head.onclick = () => top.classList.toggle('open');
        const ch = document.createElement('div');
        ch.className = 'poc-node-children';
        top.appendChild(head); top.appendChild(ch);
        tree.appendChild(top);
        this.tree[group] = top;
        return;
      }
    }
    const el = document.createElement('div');
    el.className = 'poc-child';
    const text = this._esc(msg);
    if (msg.startsWith('[成功]')) { el.className += ' poc-ok'; el.textContent = text.slice(5); }
    else if (msg.startsWith('[失败]')) { el.className += ' poc-err'; el.textContent = text.slice(5); }
    else if (msg.startsWith('[处理]')) { el.className += ' poc-warn'; el.textContent = text.slice(5); }
    else el.textContent = text;
    const box = top ? top.querySelector('.poc-node-children') : tree;
    box.appendChild(el);
    tree.scrollTop = tree.scrollHeight;
  }
};
"""


_WINDOW = {"w": None}


class Api:
    """pywebview js_api：页面按钮 → Python 回调。"""

    def start_download(self):
        threading.Thread(target=self._fake_download, daemon=True).start()

    def switch_account(self):
        self._push("status", "error", "更换账号功能待接入")

    def _fake_download(self):
        """模拟下载过程，验证 Python → 面板日志链路。"""
        self._push("status", "busy", "下载中 0/3 · 0 个 PDF")
        self._push("progress", 0)
        for i in range(3):
            self._push("log", "开始下载邮件 %d" % (i + 1), "邮件%d" % (i + 1))
            for j in range(5):
                time.sleep(0.4)
                pct = (i * 5 + j + 1) / 15 * 100
                self._push("progress", pct)
                self._push("log", "  附件%d 下载完成" % (j + 1), "邮件%d" % (i + 1))
            self._push("log", "[成功] 命名 2026-08-%02d_电子发票_%.2f.pdf" % (i + 1, 100 + i * 17.79), "邮件%d" % (i + 1))
            self._push("status", "busy", "下载中 %d/3" % (i + 1))
        self._push("progress", 100)
        self._push("status", "ready", "完成 · 3 个 PDF")
        self._push("log", "[成功] 全部完成", "")

    def _push(self, kind, *args):
        """跨线程把数据送回面板（evaluate_js 由 pywebview 内部调度到 GUI 线程）。"""
        if kind == "log":
            js = "window.__poc.log(%s,%s)" % (json.dumps(args[0]), json.dumps(args[1]))
        elif kind == "status":
            js = "window.__poc.status(%s,%s)" % (json.dumps(args[0]), json.dumps(args[1]))
        else:
            js = "window.__poc.progress(%s)" % json.dumps(args[0])
        try:
            _WINDOW["w"].evaluate_js(js)
        except Exception as e:
            print("push 失败:", e, flush=True)


def main():
    window = webview.create_window("发票助手 · WebView2", "https://mail.qq.com",
                                   width=1400, height=900, js_api=Api())
    _WINDOW["w"] = window

    def on_loaded():
        time.sleep(0.5)
        try:
            window.evaluate_js("document.body.insertAdjacentHTML('beforeend', %s)" % json.dumps(PANEL_HTML))
            window.evaluate_js(PANEL_JS)
            window.evaluate_js("window.__poc.status('idle', '就绪 · 请勾选左侧邮件')")
            window.evaluate_js("window.__poc.log('[成功] 面板注入成功（P1 原型）', '')")
            print("面板已注入", flush=True)
        except Exception as e:
            print("注入失败:", e, flush=True)

    window.events.loaded += on_loaded
    webview.start()
    print("P1 原型结束", flush=True)


if __name__ == "__main__":
    main()