"""updater 版本比较与静默失败测试（不访问真实网络）。"""

from app.engine import updater


def test_ver_tuple():
    assert updater._ver_tuple("v1.2.3") == (1, 2, 3)
    assert updater._ver_tuple("1.10.0") == (1, 10, 0)
    assert updater._ver_tuple("v0.9.1") == (0, 9, 1)
    assert updater._ver_tuple("abc") == (0,)
    assert updater._ver_tuple("") == (0,)


def test_check_update_silent_fail(monkeypatch):
    """网络失败/解析失败 → (None, None)，不抛异常（静默）。"""
    def _boom(url, timeout=15):
        raise OSError("offline")
    monkeypatch.setattr(updater, "_api", _boom)
    tag, url = updater.check_update()
    assert tag is None and url is None


def test_check_update_newer(monkeypatch):
    """本地版本低于 release tag 且资产名匹配 → 返回 (tag, url)。"""
    monkeypatch.setattr(updater, "_api", lambda url, timeout=15: {
        "tag_name": "v9.9.9",
        "assets": [{"name": "invoice-helper-v9.9.9.zip",
                    "browser_download_url": "https://x/invoice-helper.zip"}],
    })
    monkeypatch.setattr(updater.config, "APP_VERSION", "1.0.0")
    tag, url = updater.check_update()
    assert tag == "v9.9.9"
    assert "invoice-helper.zip" in url


def test_check_update_current(monkeypatch):
    """本地版本已最新 → (None, None)。"""
    monkeypatch.setattr(updater, "_api", lambda url, timeout=15: {
        "tag_name": "v1.0.0",
        "assets": [{"name": "invoice-helper.zip", "browser_download_url": "https://x/z.zip"}],
    })
    monkeypatch.setattr(updater.config, "APP_VERSION", "1.1.0")
    tag, url = updater.check_update()
    assert tag is None and url is None