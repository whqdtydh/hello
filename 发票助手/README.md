# 发票助手（InvoiceAssistant）

一款桌面软件，自动登录 QQ 邮箱 → 打开「我的文件夹-报销」→ 扫描发票邮件 → **只下载电子发票 PDF 与电子行程单 PDF**（跳过 .xml / .ofd）→ 按规则命名保存到指定目录。

## 功能

- 复用系统 Microsoft Edge 登录 QQ 邮箱（免去下载 Playwright Chromium），**首次扫码，之后免登录**。
- 自动遍历报销文件夹内所有邮件，跳过非发票邮件（如工作日报）。
- 每封发票邮件自动筛出 2 个 PDF：`电子发票` 与 `电子行程单`。
- 命名规则：`电子发票_公司名_金额_日期.pdf` / `电子行程单_公司名_金额_日期.pdf`
  - 例：`电子发票_旅程约车特选_82.94元_2026-08-13.pdf`
  - 同目录重名自动追加 `_1`、`_2`。
- 桌面 GUI（PySide6），实时日志 + 进度，支持停止。

## 运行环境

- Windows + 本机已安装 **Microsoft Edge**（系统自带）
- Python 3.10+（已在 3.12 验证）

## 安装与运行

```bat
pip install -r requirements.txt
python main.py
```

运行后：

1. 确认「邮箱来源」URL（默认 QQ 邮箱 → 我的文件夹 → 报销）。
2. 确认「保存目录」（默认 `桌面\车辆报销`，可浏览更改）。
3. 点「▶ 开始下载」—— 会自动弹出 Edge 窗口。
   - 首次使用：在 Edge 中扫码登录 QQ 邮箱，登录后本软件自动继续。
   - 之后使用：会话已保存，自动进入。
4. 等待完成，核对文件中命名与数量。

## 项目结构

```
发票助手/
├── main.py                  # 入口
├── requirements.txt
├── app/
│   ├── config.py            # 路径 + CSS 选择器（邮箱改版请优先改这里）
│   ├── engine/
│   │   ├── email_client.py  # Playwright 封装：登录 / 导航 / 扫描邮件
│   │   └── downloader.py    # 遍历邮件 → 筛选 PDF → 命名 → 下载
│   ├── worker.py            # QThread 后台工作线程
│   └── ui/main_window.py    # 主窗口界面
```

## 常见问题

- **Cloudflare / 验证码拦截**：在弹窗中手动完成验证即可，随后软件自动继续。
- **登录超时**：重新点「开始下载」，在 Edge 中扫码，软件会自动进入。
- **界面改版导致失效**：打开 `app/config.py`，按当前网页元素调整 `MAIL_ITEM` 等选择器。

## 备注

- 本软件仅用于读取本人邮箱并下载发票附件，请在授权范围内使用。
- 会话数据保存在 `C:\Users\<用户名>\.invoice_assistant\edge_profile`。