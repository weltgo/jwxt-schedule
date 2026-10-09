import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jwxt.config import AppConfig, load_config
from jwxt.http import JWXTClient


class ConfigTests(unittest.TestCase):
    def test_sample_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jwxt.toml"
            path.write_text(
                """
[system]
type = "zhengfang"
base_url = "https://jw.example.edu.cn/jwglxt"

[account]
username = "20250001"
password_env = "TEST_JWXT_PASSWORD"

[term]
name = "2025-2026-1"
start_date = 2025-09-01
weeks_count = 18
                """,
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.system.base_url, "https://jw.example.edu.cn/jwglxt")
            self.assertEqual(config.term.start_date.year, 2025)
            self.assertEqual(config.term.weeks_count, 18)

    def test_password_from_env(self):
        config = AppConfig()
        with mock.patch.dict(os.environ, {"TEST_JWXT_PASSWORD": "secret"}):
            config.account.password_env = "TEST_JWXT_PASSWORD"
            self.assertEqual(config.resolve_password(), "secret")

    def test_client_cookie_helpers(self):
        config = AppConfig()
        config.system.base_url = "https://jw.example.edu.cn"
        with JWXTClient(config) as client:
            client.set_cookie_string("JSESSIONID=abc; route=def")
            text = client.cookies_as_string()
            self.assertIn("JSESSIONID=abc", text)
            self.assertIn("route=def", text)


if __name__ == "__main__":
    unittest.main()
