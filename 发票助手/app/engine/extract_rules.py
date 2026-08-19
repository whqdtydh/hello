"""提取规则配置加载器（任务2：规则配置化 + 热更新）。

从 config/extract_rules.json 加载金额/日期提取规则，支持 mtime 热重载
（修改保存后下次提取自动生效）。JSON 缺失/损坏时回退代码内默认值，
保证行为与拆分前完全一致。

默认值即当前内置规则的精确拷贝（见 _DEFAULTS），确保无配置也能工作。
"""

import json
import os
import re
import sys
import threading

_DEFAULTS = {
    "amount_rules": [
        {"name": "价税合计", "group": 1, "flags": "S",
         "pattern": r"价税合计[^0-9]{0,60}?[¥￥]?\s*(\d+(?:\.\d{1,2})?)"},
        {"name": "小写", "group": 1,
         "pattern": r"（?小写）?[¥￥]?\s*(\d+(?:\.\d{1,2})?)"},
        {"name": "总计_行程单", "group": 1,
         "pattern": r"总计[¥￥]?\s*(\d+(?:\.\d{1,2})?)"},
        {"name": "合计", "group": 1,
         "pattern": r"(?:金额)?合计[¥￥]?\s*(\d+(?:\.\d{1,2})?)"},
        {"name": "票价", "group": 1,
         "pattern": r"票价[¥￥]?\s*(\d+(?:\.\d{1,2})?)"},
        {"name": "¥兜底", "group": 1, "mode": "max",
         "pattern": r"[¥￥]\s*(\d+(?:\.\d{1,2})?)"},
    ],
    "date_labels": ["行程时间", "通行时间", "出行日期", "乘车日期",
                    "消费日期", "交易时间", "行程日期", "上车时间"],
    "date_range_pattern": r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\s*至\s*20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}",
    "date_cn_pattern": r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
    "date_plain_pattern": r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})",
}

_lock = threading.Lock()
_cached = None
_cached_mtime = None


def rules_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "config", "extract_rules.json")


def load_rules():
    """加载配置（mtime 热重载）。返回 dict，缺省字段用默认值补齐。"""
    global _cached, _cached_mtime
    path = rules_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    with _lock:
        if _cached is not None and _cached_mtime == mtime:
            return _cached
        rules = dict(_DEFAULTS)  # 先拷贝默认
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data.get("amount_rules"), list) and data["amount_rules"]:
                rules["amount_rules"] = data["amount_rules"]
            if isinstance(data.get("date_labels"), list) and data["date_labels"]:
                rules["date_labels"] = data["date_labels"]
            for k in ("date_range_pattern", "date_cn_pattern", "date_plain_pattern"):
                if isinstance(data.get(k), str) and data[k]:
                    rules[k] = data[k]
        except Exception:
            pass  # JSON 损坏 → 用默认
        _cached = rules
        _cached_mtime = mtime
        return rules


# ---------- 编译后的金额规则（供 pdf_service 使用） ----------

def compile_amount_rules():
    """编译金额规则为 [(name, regex, group, mode)]，按配置顺序。"""
    rules = load_rules()
    out = []
    for r in rules.get("amount_rules", []):
        try:
            flags = 0
            if "S" in (r.get("flags") or ""):
                flags |= re.S
            rx = re.compile(r["pattern"], flags)
        except re.error:
            continue
        out.append((r.get("name", "?"), rx, int(r.get("group", 1)),
                    r.get("mode", "first")))
    return out


def extract_amount(text):
    """按配置规则从文本提取金额（行为与原内置规则一致）：

    前 5 条 first-match（组1，100 万上限过滤），末条 mode=max 兜底。
    """
    if not text:
        return 0.0
    for name, rx, group, mode in compile_amount_rules():
        if mode == "max":
            try:
                amounts = [float(m.group(group)) for m in rx.finditer(text)]
            except (ValueError, IndexError):
                amounts = []
            if amounts:
                amt = max(amounts)
                if 0 < amt < 1000000:
                    return amt
        else:
            m = rx.search(text)
            if m:
                try:
                    amt = float(m.group(group))
                except (ValueError, IndexError):
                    continue
                if 0 < amt < 1000000:
                    return amt
    return 0.0
