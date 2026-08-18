import re
import unicodedata
from datetime import datetime
from collections import Counter

# ------------------------------------------------------------
# 1️⃣ Unicode 标准化 + 半角化
# ------------------------------------------------------------
def normalize_halfwidth(txt: str) -> str:
    """将全角字符、中文标点统一转为半角/英文标点。
    使用 NFKC 正规化后手动替换未被覆盖的中文标点。
    """
    txt = unicodedata.normalize('NFKC', txt)
    punctuation_map = {
        '，': ',', '。': '.', '！': '!', '？': '?',
        '（': '(', '）': ')', '【': '[', '】': ']',
        '《': '<', '》': '>', '～': '~', '－': '-',
        '＿': '_', '；': ';', '：': ':', '‘': "'", '’': "'",
        '“': '"', '”': '"', '、': ',', '·': '-', ' ': ' '
    }
    for cn, en in punctuation_map.items():
        txt = txt.replace(cn, en)
    return txt

# ------------------------------------------------------------
# 2️⃣ 中文数字 → 阿拉伯数字 & 统一货币/单位
# ------------------------------------------------------------
_CHINESE_NUM_MAP = {
    '零': '0', '〇': '0',
    '一': '1', '壹': '1',
    '二': '2', '贰': '2', '貳': '2',
    '三': '3', '叁': '3',
    '四': '4', '肆': '4',
    '五': '5', '伍': '5',
    '六': '6', '陆': '6',
    '七': '7', '柒': '7',
    '八': '8', '捌': '8',
    '九': '9', '玖': '9',
    '十': '10'
}

def replace_chinese_numbers(txt: str) -> str:
    pattern = re.compile('|'.join(_CHINESE_NUM_MAP.keys()))
    return pattern.sub(lambda m: _CHINESE_NUM_MAP[m.group()], txt)

def unify_money_units(txt: str) -> str:
    # 统一货币符号为 “元”
    txt = re.sub(r'[¥￥]|RMB|人民币', '元', txt, flags=re.I)
    # 去除千分位逗号
    txt = re.sub(r'(?<=\d),(?=\d)', '', txt)
    # 去掉 “元” 前后的空格
    txt = re.sub(r'\s*元\s*', '元', txt)
    return txt

# ------------------------------------------------------------
# 3️⃣ 日期统一化
# ------------------------------------------------------------
_DATE_PATTERNS = [
    # 2026-07-28 / 2026/07/28 / 2026.07.28
    r'(?P<y>\d{4})[\/\-\.\s]+(?P<m>\d{1,2})[\/\-\.\s]+(?P<d>\d{1,2})',
    # 2026年7月28日
    r'(?P<y>\d{4})年\s*(?P<m>\d{1,2})月\s*(?P<d>\d{1,2})日',
    # 07-28（缺省年份）
    r'(?P<m>\d{1,2})[\/\-\.\s]+(?P<d>\d{1,2})'
]

def normalize_dates(txt: str, default_year: int = None) -> str:
    def repl(m):
        y = m.group('y')
        mth = m.group('m')
        day = m.group('d')
        if not y:
            y = str(default_year or datetime.now().year)
        return f"{int(y):04d}-{int(mth):02d}-{int(day):02d}"
    for pat in _DATE_PATTERNS:
        txt = re.sub(pat, repl, txt)
    return txt

# ------------------------------------------------------------
# 4️⃣ 噪声过滤（保留关键行）
# ------------------------------------------------------------
_KEEP_KEYWORDS = [
    '元', '人民币', '发票', '行程单', '车牌', '票号', '金额', '合计', '费用',
    '付款', '收款', '出行', '日期', '时间', '起点', '终点', '公司', '名称',
    '项目', '订单号'
]

def filter_noise_lines(txt: str) -> str:
    lines = txt.splitlines()
    filtered = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line) < 5 or set(line) <= {'-', '=', '*', '_'}:
            continue
        if any(kw in line for kw in _KEEP_KEYWORDS):
            filtered.append(line)
    return '\n'.join(filtered)

# ------------------------------------------------------------
# 5️⃣ 关键字段抽取（金额、发票号、日期、公司、车牌/行程号）
# ------------------------------------------------------------
def extract_core_fields(txt: str) -> dict:
    result = {}
    # 金额（首个出现的金额）
    amt_match = re.search(r'(\d{1,3}(?:,\d{3})*|\d+)(\.\d{1,2})?\s*元', txt)
    if amt_match:
        amt = amt_match.group(1).replace(',', '') + (amt_match.group(2) or '.00')
        result['amount'] = f"{float(amt):.2f}"
    # 发票号
    inv_match = re.search(r'发票号[:\s]*([A-Z0-9]{6,})', txt, flags=re.I)
    if not inv_match:
        inv_match = re.search(r'票号[:\s]*([0-9]{8,})', txt)
    if inv_match:
        result['invoice_no'] = inv_match.group(1)
    # 日期（已统一的日期）
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', txt)
    if date_match:
        result['date'] = date_match.group()
    # 公司/机构名称（统计出现最多的中文词组）
    cn_words = re.findall(r'[\u4e00-\u9fa5]{2,6}', txt)
    if cn_words:
        result['company'] = Counter(cn_words).most_common(1)[0][0]
    # 车牌或行程编号（常见车牌格式）
    vehicle_match = re.search(r'([京津沪渝冀豫辽吉黑苏浙皖闽赣鲁蜀晋蒙云桂藏川贵青宁新琼][A-Z][A-Z0-9]{5,})', txt)
    if vehicle_match:
        result['vehicle'] = vehicle_match.group(1)
    return result

# ------------------------------------------------------------
# 6️⃣ 综合预处理入口（返回清洗文本 + 结构化字段）
# ------------------------------------------------------------
def preprocess_invoice_text(raw_text: str, default_year: int = None) -> dict:
    txt = normalize_halfwidth(raw_text)
    txt = replace_chinese_numbers(txt)
    txt = unify_money_units(txt)
    txt = normalize_dates(txt, default_year=default_year)
    txt = filter_noise_lines(txt)
    fields = extract_core_fields(txt)
    return {
        "clean_text": txt,
        "fields": fields
    }
