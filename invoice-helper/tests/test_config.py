"""config 常量测试。"""
from app import config


class TestConstants:
    def test_pdf_suffix(self):
        assert config.PDF_SUFFIX == ".pdf"

    def test_mail_home_https(self):
        assert config.MAIL_HOME.startswith("https://")

    def test_default_save_dir(self):
        assert "车辆报销" in config.DEFAULT_SAVE_DIR