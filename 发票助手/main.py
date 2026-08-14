"""发票助手：QQ 邮箱电子发票/行程单 PDF 自动下载工具 入口。"""

import os
import sys
from pathlib import Path

# 保证从任意目录运行都能正确导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ui.main_window import run

if __name__ == "__main__":
    run()