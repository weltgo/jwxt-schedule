import unittest
from datetime import date
from pathlib import Path

from jwxt.config import AppConfig
from jwxt.exporters.ics import ICSExportError, schedule_to_ics
from jwxt.tables import parse_html_schedule

ROOT = Path(__file__).resolve().parents[1]


class ICSExportTests(unittest.TestCase):
    def setUp(self):
        html = (ROOT / "examples" / "demo_schedule.html").read_text(encoding="utf-8")
        self.schedule = parse_html_schedule(html, term="2025-2026-1", source="test")
        self.config = AppConfig()
        self.config.term.start_date = date(2025, 9, 1)

    def test_event_count(self):
        ics = schedule_to_ics(self.schedule, self.config)
        expected = sum(len(c.weeks) for c in self.schedule.courses)
        self.assertEqual(ics.count("BEGIN:VEVENT"), expected)

    def test_known_event_time(self):
        ics = schedule_to_ics(self.schedule, self.config)
        # 第1周周一 08:00-09:40 的高等数学
        self.assertIn("DTSTART:20250901T080000", ics)
        self.assertIn("DTEND:20250901T094000", ics)
        self.assertIn("SUMMARY:高等数学", ics)

    def test_lines_folded(self):
        ics = schedule_to_ics(self.schedule, self.config)
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def test_missing_start_date(self):
        self.config.term.start_date = None
        with self.assertRaises(ICSExportError):
            schedule_to_ics(self.schedule, self.config)


if __name__ == "__main__":
    unittest.main()
