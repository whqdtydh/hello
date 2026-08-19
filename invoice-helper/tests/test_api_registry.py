"""api_registry 测试：注册表读写、失效标记、特征匹配、观察学习。"""

import json
import os

from app.engine.api_registry import ApiRegistry, DEFAULT_ENDPOINTS


class TestRegistryDefaults:
    def test_default_paths(self):
        reg = ApiRegistry()
        assert reg.get_path("maillist") == "/list/maillist"
        assert reg.get_path("readmail") == "/read/readmail"
        assert reg.get_path("fapiao") == "/fapiao/download"

    def test_get_method(self):
        reg = ApiRegistry()
        assert reg.get_method("maillist") == "GET"
        assert reg.get_method("readmail") == "POST"

    def test_unknown_endpoint(self):
        reg = ApiRegistry()
        assert reg.get_path("unknown") == ""


class TestRegistryPersistence:
    def test_save_and_load(self, tmp_path):
        path = os.path.join(str(tmp_path), "api_registry.json")
        reg = ApiRegistry(path)
        reg.set_path("maillist", "/list/new_maillist")
        reg2 = ApiRegistry(path)
        assert reg2.get_path("maillist") == "/list/new_maillist"

    def test_load_corrupt_file(self, tmp_path):
        path = os.path.join(str(tmp_path), "api_registry.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{corrupt json!!")
        reg = ApiRegistry(path)
        # 损坏文件回退默认
        assert reg.get_path("maillist") == "/list/maillist"

    def test_set_path_empty_ignored(self):
        reg = ApiRegistry()
        assert not reg.set_path("maillist", "")
        assert reg.get_path("maillist") == "/list/maillist"


class TestFailureMarking:
    def test_mark_and_failed(self, tmp_path):
        path = os.path.join(str(tmp_path), "api_registry.json")
        reg = ApiRegistry(path)
        reg.mark_failed("readmail")
        assert "readmail" in reg.failed_endpoints()

    def test_clear_failed(self, tmp_path):
        path = os.path.join(str(tmp_path), "api_registry.json")
        reg = ApiRegistry(path)
        reg.mark_failed("maillist")
        reg.mark_failed("readmail")
        reg.clear_failed()
        assert reg.failed_endpoints() == []

    def test_no_failed_by_default(self):
        reg = ApiRegistry()
        assert reg.failed_endpoints() == []


class TestMatchEndpoint:
    def test_maillist_same_path(self):
        reg = ApiRegistry()
        hit = reg.match_endpoint("https://wx.mail.qq.com/list/maillist?dirid=1&page_now=0&page_size=50", "GET")
        assert hit == ("maillist", "/list/maillist")

    def test_maillist_learned_path(self):
        # 腾讯改版：/list/maillist 换成 /cgi-bin/mail_list
        reg = ApiRegistry()
        hit = reg.match_endpoint(
            "https://wx.mail.qq.com/cgi-bin/mail_list?dir=1&dirid=1&page_now=0&page_size=50", "GET")
        assert hit is not None
        assert hit[0] == "maillist"
        assert "mail_list" in hit[1]

    def test_readmail(self):
        reg = ApiRegistry()
        hit = reg.match_endpoint("https://wx.mail.qq.com/read/readmail", "POST")
        assert hit == ("readmail", "/read/readmail")

    def test_fapiao(self):
        reg = ApiRegistry()
        hit = reg.match_endpoint(
            "https://wx.mail.qq.com/fapiao/download?fapiao_list=xxx&name=yyy", "GET")
        assert hit == ("fapiao", "/fapiao/download")

    def test_no_match(self):
        reg = ApiRegistry()
        assert reg.match_endpoint("https://other.com/foo/bar", "GET") is None

    def test_static_resource_no_match(self):
        reg = ApiRegistry()
        assert reg.match_endpoint("https://wx.mail.qq.com/res/css/app.css", "GET") is None

    def test_params_scoring(self):
        # 只有参数命中（无路径关键词）得分不够，不匹配
        reg = ApiRegistry()
        assert reg.match_endpoint("https://wx.mail.qq.com/x/y?mailid=m1", "GET") is None


class TestLearnFromObservations:
    def test_learn_new_maillist_path(self, tmp_path):
        path = os.path.join(str(tmp_path), "api_registry.json")
        reg = ApiRegistry(path)
        obs = [{"u": "https://wx.mail.qq.com/cgi-bin/mail_list?dirid=1&page_now=0&page_size=50",
                "m": "GET", "t": 1}]
        updated = reg.learn_from_observations(obs)
        assert updated == ["maillist"]
        assert reg.get_path("maillist") == "/cgi-bin/mail_list"
        # 学习成功后应清除 failed 标记
        reg.mark_failed("maillist")
        reg.clear_failed()
        assert reg.failed_endpoints() == []

    def test_no_relearn_same_default(self, tmp_path):
        path = os.path.join(str(tmp_path), "api_registry.json")
        reg = ApiRegistry(path)
        obs = [{"u": "https://wx.mail.qq.com/list/maillist?dirid=1", "m": "GET"}]
        # 路径与默认相同 → 不算更新
        assert reg.learn_from_observations(obs) == []

    def test_ignore_unrelated(self):
        reg = ApiRegistry()
        obs = [{"u": "https://cdn.qq.com/js/app.js", "m": "GET"}]
        assert reg.learn_from_observations(obs) == []

    def test_empty_observations(self):
        reg = ApiRegistry()
        assert reg.learn_from_observations([]) == []
        assert reg.learn_from_observations(None) == []

    def test_learn_multiple(self, tmp_path):
        path = os.path.join(str(tmp_path), "api_registry.json")
        reg = ApiRegistry(path)
        obs = [
            {"u": "https://wx.mail.qq.com/cgi-bin/mail_list?dirid=1&page_now=0", "m": "GET"},
            {"u": "https://wx.mail.qq.com/cgi-bin/read_mail?mailid=m1", "m": "POST"},
        ]
        updated = reg.learn_from_observations(obs)
        assert "maillist" in updated
        assert "readmail" in updated
        assert reg.get_path("maillist") == "/cgi-bin/mail_list"
        assert reg.get_path("readmail") == "/cgi-bin/read_mail"


class TestDefaultEndpointsConsistency:
    def test_all_endpoints_have_path(self):
        for name, ep in DEFAULT_ENDPOINTS.items():
            assert ep.get("path"), f"{name} 缺少默认 path"
            assert ep.get("path_kw"), f"{name} 缺少 path_kw"
            assert ep.get("params"), f"{name} 缺少 params"

    def test_default_path_matches_itself(self):
        reg = ApiRegistry()
        for name, ep in DEFAULT_ENDPOINTS.items():
            url = f"https://wx.mail.qq.com{ep['path']}"
            hit = reg.match_endpoint(url, ep.get("method", "GET"))
            assert hit is not None and hit[0] == name
