import tempfile
import unittest
from datetime import date
from pathlib import Path

from jwxt.config import load_config
from jwxt.wizard import normalize_url, suggest_start_date, suggest_term_name, write_config
from jwxt.config import AppConfig


class WizardHelperTests(unittest.TestCase):
    def test_suggest_term_name(self):
        self.assertEqual(suggest_term_name(date(2025, 9, 1)), "2025-2026-1")
        self.assertEqual(suggest_term_name(date(2026, 3, 1)), "2025-2026-2")
        self.assertEqual(suggest_term_name(date(2026, 1, 5)), "2025-2026-1")

    def test_suggest_start_date_is_monday(self):
        self.assertEqual(suggest_start_date(date(2025, 9, 1)).weekday(), 0)
        self.assertEqual(suggest_start_date(date(2026, 3, 1)).weekday(), 0)

    def test_normalize_url(self):
        self.assertEqual(normalize_url("jw.example.edu.cn"), "https://jw.example.edu.cn")
        self.assertEqual(normalize_url("http://jw.example.edu.cn/"), "http://jw.example.edu.cn")

    def test_write_minimal_config(self):
        config = AppConfig()
        config.system.base_url = "https://jw.example.edu.cn"
        config.account.username = "20250001"
        config.term.name = "2025-2026-1"
        config.term.start_date = date(2025, 9, 1)
        config.http.session_file = "/tmp/jwxt-test.cookies.json"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jwxt.toml"
            write_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.system.base_url, "https://jw.example.edu.cn")
            self.assertEqual(loaded.account.username, "20250001")
            self.assertEqual(loaded.term.start_date, date(2025, 9, 1))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
