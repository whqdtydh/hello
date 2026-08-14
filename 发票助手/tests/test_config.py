"""config 凭据读写测试。"""
import os

import pytest

from app import config


class TestImapCred:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        cred_file = str(tmp_path / "cred.json")
        monkeypatch.setattr(config, "CRED_FILE", cred_file)
        assert config.save_imap_cred("user@qq.com", "authcode")
        assert config.load_imap_cred() == ("user@qq.com", "authcode")

    def test_load_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CRED_FILE", str(tmp_path / "missing.json"))
        assert config.load_imap_cred() == ("", "")

    def test_save_invalid_dir(self, monkeypatch):
        monkeypatch.setattr(config, "CRED_FILE",
                            r"Z:\__nonexistent_drive__\cred.json")
        assert config.save_imap_cred("a", "b") is False

    def test_duplicate_key_preserved(self, tmp_path, monkeypatch):
        cred_file = str(tmp_path / "cred.json")
        monkeypatch.setattr(config, "CRED_FILE", cred_file)
        config.save_imap_cred("old@qq.com", "old")
        config.save_imap_cred("new@qq.com", "new")
        assert config.load_imap_cred() == ("new@qq.com", "new")


class TestConstants:
    def test_pdf_suffix(self):
        assert config.PDF_SUFFIX == ".pdf"

    def test_mail_home_https(self):
        assert config.MAIL_HOME.startswith("https://")

    def test_default_save_dir(self):
        assert "车辆报销" in config.DEFAULT_SAVE_DIR