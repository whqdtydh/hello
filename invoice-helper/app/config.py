"""全局配置：路径、选择器、常量。

选择器集中在此便于 QQ 邮箱改版后快速适配。
"""

import json
import os
import sys

# 数据根目录（登录会话 / cookie / 凭据统一存放）
DATA_DIR = os.path.join(os.path.expanduser("~"), ".invoice_assistant")

# 系统 Edge 浏览器路径（复用本机浏览器，免下载 Playwright Chromium）
EDGE_EXECUTABLE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 邮箱登录会话 profile 保存目录（实现二次免登录）
PROFILE_DIR = os.path.join(DATA_DIR, "edge_profile")

# QtWebEngine 持久化目录（cookie / 缓存 / 本地存储）
PROFILE_CACHE_DIR = os.path.join(DATA_DIR, "webengine", "cache")
PROFILE_STORAGE_DIR = os.path.join(DATA_DIR, "webengine", "storage")

# 手动持久化的 cookie 文件（requests 拉取附件时复用会话）
COOKIE_FILE = os.path.join(DATA_DIR, "web_cookies.json")

# QQ 邮箱首页（登录入口 + 收件箱）
MAIL_HOME = "https://wx.mail.qq.com/home/index"

# 报销文件夹 URL（QQ 邮箱 -> 我的文件夹 -> 报销）
DEFAULT_MAIL_URL = "https://wx.mail.qq.com/home/index#/list/2000"

# 默认保存目录：桌面\车辆报销
DEFAULT_SAVE_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "车辆报销")

# ---------- 版本与自动更新 ----------
APP_NAME = "invoice-helper"
APP_VERSION = "1.1.0"                      # 打包/更新比较用（语义化版本）
GITHUB_REPO = "whqdtydh/hello"             # GitHub Releases 仓库（owner/repo）
UPDATE_ASSET_PREFIX = "invoice-helper"            # 发布资产名前缀（zip）

# ---------- CSS 选择器（QQ 邮箱网页版，2026-08 验证可用） ----------
MAIL_ITEM = "div[class*=list-item]"                 # 邮件列表项
ATTACH_LIST = "div.mail-detail-attaches"            # 附件列表容器
ATTACH_CARD = ".mail-detail-attach-card"            # 单个附件卡片
ATTACH_NAME = ".attach-name"                        # 附件名（不含后缀）
ATTACH_SUFFIX = ".attach-suffix"                    # 附件后缀（.pdf/.xml/.ofd）
ATTACH_SIZE = ".attach-size"                        # 附件大小
CARD_DOWNLOAD_BTN = "text=下载"                     # 卡片上的下载按钮
BACK_BTN = "text=返回"                              # 返回列表按钮

# 登录成功判定：页面标题或正文出现该标记
LOGIN_MARKER = "收件箱"

# 需要跳过的非发票邮件关键字
SKIP_KEYWORDS = ("工作日报", "日报", "汇报")

# 视为发票邮件的发件人关键字（为空则不限制发件人，仅按附件筛选）
INVOICE_FROM_KEYWORDS = ("itinerary", "fapiao")

# 每封邮件仅下载这些后缀的附件（电子发票 PDF + 电子行程单 PDF）
PDF_SUFFIX = ".pdf"


# ---------------------------------------------------------------------------
# 规则与 LLM 辅助配置（可在部署时自行修改）
# ---------------------------------------------------------------------------
# 规则与 LLM 辅助配置（可在部署时自行修改）
# ---------------------------------------------------------------------------
# 规则文件路径（JSON）：打包后位于 _MEIPASS/config/，开发模式位于项目根/config/
_APP_BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RULES_FILE = os.path.join(_APP_BASE, "config", "invoice_rules.json")
# 是否启用 LLM（MIMO v2.5 free）建议功能，默认关闭（防止意外网络调用）
ENABLE_LLM_SUGGESTION = True

# 日志记录（使用标准库 logging，外部可自行配置日志输出）
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG_UNMATCHED = logging.getLogger("unmatched")
LOG_LLM_SUGGESTION = logging.getLogger("llm_suggestion")
LOG_ERROR = logging.getLogger("error")

# 加载规则（在首次使用时调用）
def load_invoice_rules():
    """读取 `invoice_rules.json` 并返回规则列表。
    若文件不存在或解析错误，返回空列表并记录错误。"""
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            rules = json.load(f)
        return rules
    except Exception as e:
        LOG_ERROR.error(f"加载发票规则失败: {e}")
        return []

# 预先加载到全局变量，供整个进程使用
INVOICE_RULES = load_invoice_rules()
