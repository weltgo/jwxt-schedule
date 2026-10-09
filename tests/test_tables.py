import unittest
from pathlib import Path

from jwxt.tables import ScheduleParseError, parse_html_schedule

ROOT = Path(__file__).resolve().parents[1]


class TableParserTests(unittest.TestCase):
    def test_grid_fixture(self):
        html = (ROOT / "examples" / "demo_schedule.html").read_text(encoding="utf-8")
        schedule = parse_html_schedule(html, term="2025-2026-1", source="test")
        self.assertEqual(len(schedule.courses), 8)
        advanced_math = [c for c in schedule.courses if c.name == "高等数学" and c.day == 1][0]
        self.assertEqual(advanced_math.teacher, "王老师")
        self.assertEqual(advanced_math.location, "教一101")
        self.assertEqual(advanced_math.start_period, 1)
        self.assertEqual(advanced_math.end_period, 2)
        self.assertEqual(advanced_math.start_time, "08:00")
        self.assertEqual(advanced_math.end_time, "09:40")
        self.assertEqual(advanced_math.weeks, list(range(1, 17)))

        english = [c for c in schedule.courses if c.name == "大学英语"][0]
        self.assertEqual(english.weeks, [1, 3, 5, 7, 9, 11, 13, 15])

        lecture = [c for c in schedule.courses if c.name == "通识讲座"][0]
        self.assertEqual(lecture.location, "报告厅")
        self.assertEqual(lecture.start_period, 9)

    def test_flat_fixture(self):
        html = (ROOT / "examples" / "demo_flat.html").read_text(encoding="utf-8")
        schedule = parse_html_schedule(html, term="2025-2026-1", source="test")
        self.assertEqual(len(schedule.courses), 3)
        names = [c.name for c in schedule.courses]
        self.assertIn("程序设计基础", names)
        cs = [c for c in schedule.courses if c.name == "程序设计基础"][0]
        self.assertEqual(cs.day, 2)
        self.assertEqual(cs.start_period, 3)
        self.assertEqual(cs.weeks, [1, 2, 3, 4, 5, 6, 7, 8])

    def test_no_table(self):
        with self.assertRaises(ScheduleParseError):
            parse_html_schedule("<html><body>no table</body></html>")


if __name__ == "__main__":
    unittest.main()
