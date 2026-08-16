# Playwright 脚本：捕获 QQ 邮箱选中邮件的 Message-ID
# 授权：最高权限，用户邮箱，用户会话
# 执行命令：python -m playwright run capture_qq_mail.py

from playwright.sync_api import sync_playwright
import re

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=2000)
        context = browser.new_context()
        page = context.new_page()

        # 监听所有网络响应，捕获包含 Message-ID 的响应
        message_ids = []

        def handle_response(response):
            url = response.url
            # 捕获邮件相关 API 响应
            if "mail" in url or "qqmail" in url or "wx.mail" in url:
                try:
                    body = response.text()
                    # 提取 Message-ID（常见格式：<...@qq.com>）
                    matches = re.findall(r'<[^>]+@(?:qq\.com|foxmail\.com|mail\.qq\.com)[^>]*>', body)
                    if matches:
                        print(f"[发现 Message-ID] URL: {url}")
                        for m in matches:
                            print(f"  -> {m}")
                            message_ids.append(m)
                except Exception as e:
                    print(f"[解析响应错误] {url}: {e}")

        page.on("response", handle_response)

        # 打开用户提供的 QQ 邮箱 URL（含 sid 会话）
        url = "https://wx.mail.qq.com/home/index?sid=zUxmWYwhM2Qu-kpXABxxZwAA#/list/1"
        print(f"[访问] {url}")
        page.goto(url, wait_until="networkidle", timeout=30000)

        # 等待页面加载，尝试观察列表
        print("[等待] 页面加载中，观察网络响应...")
        page.wait_for_timeout(5000)

        # 尝试点击第一封邮件（模拟勾选）
        try:
            # QQ 邮箱列表通常有邮件项，尝试点击第一个可见元素
            first_item = page.query_selector(".mailItem, .listItem, [data-mid], .mail-list-item")
            if first_item:
                print("[操作] 点击第一封邮件（模拟勾选）")
                first_item.click()
                page.wait_for_timeout(3000)
            else:
                print("[提示] 未找到可点击的邮件列表项（可能需要登录或页面结构不同）")
        except Exception as e:
            print(f"[点击操作] {e}")

        # 再等待捕获响应
        page.wait_for_timeout(5000)

        print(f"\n[结果] 捕获到 Message-ID 数量: {len(message_ids)}")
        for mid in message_ids:
            print(f"  Message-ID: {mid}")

        browser.close()

if __name__ == "__main__":
    run()
