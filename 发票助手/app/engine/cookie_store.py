"""QtWebEngine cookie 收集与持久化。

PySide6 6.11 已移除 cookiesForUrl，通过监听 cookieAdded + loadAllCookies
持续收集 cookie；QtWebEngine 内置持久化在 PySide6 下不可靠，因此
改为把收集到的 cookie 存 JSON，启动时注入回 cookie store。

get_cookie_jar 只读内存，可在 worker 线程安全调用（不含 Qt 事件循环）。
"""

import json
import os
import threading
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QByteArray, QDateTime, QObject, QUrl
from PySide6.QtNetwork import QNetworkCookie


class CookieStore(QObject):
    """收集 QtWebEngine cookie 并持久化，供 requests 拉取附件时复用会话。"""

    def __init__(self, profile, cookie_file, parent=None):
        super().__init__(parent)
        self._cookies = {}
        self._lock = threading.Lock()
        self._file = cookie_file
        self._store = profile.cookieStore()
        self._store.cookieAdded.connect(self._on_cookie_added)
        self._store.loadAllCookies()
        self._load()

    @staticmethod
    def _to_str(value):
        if isinstance(value, QByteArray):
            return bytes(value).decode("utf-8", "replace")
        return str(value)

    # ---------- 收集 ----------
    def _on_cookie_added(self, cookie):
        """收集 cookie store 中的全部 cookie（键 = domain+name）。"""
        try:
            domain = self._to_str(cookie.domain())
            name = self._to_str(cookie.name())
            if name:
                with self._lock:
                    self._cookies[(domain, name)] = cookie
        except Exception:
            pass
        self._save()

    # ---------- 持久化 ----------
    def _load(self):
        """启动时把已保存的 cookie 注入 QtWebEngine cookie store。"""
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            return
        now = QDateTime.currentDateTime().toSecsSinceEpoch()
        for item in items:
            try:
                if item.get("expires") and float(item["expires"]) < now:
                    continue
                domain = item.get("domain", "")
                name = item.get("name", "")
                value = item.get("value", "")
                path = item.get("path", "/")
                cookie = QNetworkCookie(
                    QByteArray(bytes(name, "utf-8")),
                    QByteArray(bytes(value, "utf-8")),
                )
                cookie.setDomain(domain)
                cookie.setPath(path)
                if item.get("secure"):
                    cookie.setSecure(True)
                self._store.setCookie(cookie, QUrl("https://" + domain.lstrip(".")))
            except Exception:
                continue

    def _save(self):
        """把收集到的 cookie 写入 JSON 文件。"""
        try:
            now = QDateTime.currentDateTime().toSecsSinceEpoch()
            with self._lock:
                items = list(self._cookies.values())
            out = []
            for c in items:
                try:
                    expires = c.expirationDate()
                    out.append({
                        "domain": self._to_str(c.domain()),
                        "name": self._to_str(c.name()),
                        "value": self._to_str(c.value()),
                        "path": self._to_str(c.path()),
                        "secure": bool(c.isSecure()),
                        "expires": expires.toSecsSinceEpoch()
                        if expires.isValid() and expires.toSecsSinceEpoch() > now else 0,
                    })
                except Exception:
                    continue
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- 提供给 requests ----------
    def get_cookie_value(self, name, host=None):
        """按 cookie 名取值（如 xm_sid）。host 可选，用于限定域名。"""
        with self._lock:
            items = list(self._cookies.items())
        for (domain, n), c in items:
            if n != name:
                continue
            if host and host not in domain:
                continue
            return self._to_str(c.value())
        return ""

    def get_cookie_jar(self, url):
        """从已收集的 cookie 中按域名过滤，返回 requests CookieJar。

        只读内存，可安全在 worker 线程调用。
        """
        host = (urlparse(url).netloc or "").lower()
        jar = requests.cookies.RequestsCookieJar()
        with self._lock:
            items = list(self._cookies.items())
        for (domain, name), c in items:
            d = self._to_str(c.domain()).lstrip(".")
            if host == d or host.endswith("." + d):
                value = self._to_str(c.value())
                path = self._to_str(c.path()) or "/"
                secure = bool(c.isSecure())
                jar.set(name, value, domain=d, path=path, secure=secure)
        return jar