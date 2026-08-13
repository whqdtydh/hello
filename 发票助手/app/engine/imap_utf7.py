"""IMAP modified UTF-7 编解码（RFC 3501）。

IMAP 邮箱文件夹名使用 modified UTF-7 编码：中文等非 ASCII 字符被编码为
&XXXX- 形式。此实现为标准 base64 UTF-16BE 的变体：
 - '&' 转义为 '&-'
 - '+' 与 '/' 替换为 ','（modified 部分）
"""

import binascii

_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _b64_char(c):
    idx = _BASE64_ALPHABET.index(c)
    if c == "+":
        return ","  # modified UTF-7 用逗号代替加号
    return c


def encode(s):
    """把 unicode 字符串编码为 IMAP modified UTF-7。"""
    if not s:
        return ""

    out = []
    ascii_buf = []
    nonascii = []

    def flush_nonascii():
        if not nonascii:
            return
        raw = "".join(nonascii).encode("utf-16-be")
        b64 = binascii.b2a_base64(raw)[:-1].decode("ascii")
        # 去掉结尾的 '='（padding），并把 '+' 换成 ','
        b64 = b64.rstrip("=").replace("+", ",")
        out.append("&" + b64 + "-")
        nonascii.clear()

    for ch in s:
        if " " <= ch < "\x7f" and ch != "&":
            flush_nonascii()
            if ch == "+":
                out.append("+-")
            else:
                out.append(ch)
        elif ch == "&":
            flush_nonascii()
            out.append("&-")
        else:
            if ascii_buf:
                out.extend(ascii_buf)
                ascii_buf.clear()
            nonascii.append(ch)

    flush_nonascii()
    return "".join(out)


def decode(s):
    """把 IMAP modified UTF-7 字符串解码为 unicode。"""
    if not s:
        return ""

    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "&":
            # 寻找结尾 '-'
            end = s.find("-", i + 1)
            if end == -1:
                out.append("&")
                i += 1
                continue
            token = s[i + 1:end]
            if token == "":
                out.append("&")
            else:
                b64 = token.replace(",", "+")
                b64 += "=" * ((4 - len(b64) % 4) % 4)
                try:
                    raw = binascii.a2b_base64(b64)
                    out.append(raw.decode("utf-16-be"))
                except Exception:
                    out.append("&" + token)
            i = end + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)