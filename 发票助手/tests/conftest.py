"""pytest 根配置：保证从任意目录运行都能导入 app 包。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))