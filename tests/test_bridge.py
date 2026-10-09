import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from jwxt.bridge import import_from_browser, parse_browser_payload
from jwxt.config import AppConfig
from jwxt.tables import ScheduleParseError

ROOT = Path(__file__).resolve().parents[1]


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "examples" / "demo_schedule.html").read_text(encoding="utf-8")
        self.config = AppConfig()
        self.config.term.name = "2025-2026-1"
        self.config.term.start_date = date(2025, 9, 1)

    def test_parse_payload_from_table(self):
        schedule = parse_browser_payload({"url": "https://jw.example.edu.cn", "tables": [{"html": self.html}]}, self.config)
        self.assertEqual(len(schedule.courses), 8)

    def test_import_from_browser_exports_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = import_from_browser(
                {"url": "https://jw.example.edu.cn", "title": "课表", "html": self.html},
                outdir=Path(tmp),
                config=self.config,
                save_raw=True,
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["count"], 8)
            self.assertTrue((Path(tmp) / "schedule.json").exists())
            self.assertTrue((Path(tmp) / "schedule.ics").exists())
            self.assertTrue((Path(tmp) / "schedule.csv").exists())
            self.assertTrue(Path(result["snapshot"]).exists())

    def test_import_failure_saves_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = import_from_browser(
                {"url": "https://jw.example.edu.cn", "html": "<html><body>canvas</body></html>"},
                outdir=Path(tmp),
                config=self.config,
                save_raw=True,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result["snapshot"])
            self.assertTrue(Path(result["snapshot"]).exists())


if __name__ == "__main__":
    unittest.main()
