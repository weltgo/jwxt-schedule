import unittest

from jwxt.adapters.zhengfang import ZhengfangAdapter
from jwxt.config import AppConfig


class ZhengfangParserTests(unittest.TestCase):
    def test_parse_kb_item(self):
        adapter = ZhengfangAdapter()
        course = adapter._parse_kb_item(
            {
                "kcmc": "高等数学",
                "xm": "王老师",
                "cdmc": "教一101",
                "xqj": "1",
                "xqjmc": "星期一",
                "jcs": "1-2",
                "zcd": "1-16周(单)",
            }
        )
        self.assertEqual(course.name, "高等数学")
        self.assertEqual(course.teacher, "王老师")
        self.assertEqual(course.location, "教一101")
        self.assertEqual(course.day, 1)
        self.assertEqual((course.start_period, course.end_period), (1, 2))
        self.assertEqual(course.weeks, [1, 3, 5, 7, 9, 11, 13, 15])

    def test_parse_jcs(self):
        adapter = ZhengfangAdapter()
        self.assertEqual(adapter._parse_jcs("1-2"), (1, 2))
        self.assertEqual(adapter._parse_jcs("第3-4节"), (3, 4))
        self.assertEqual(adapter._parse_jcs("5"), (5, 5))

    def test_term_display(self):
        self.assertEqual(ZhengfangAdapter._term_display("2025", "3"), "2025-2026-1")
        self.assertEqual(ZhengfangAdapter._term_display("2025-2026", "12"), "2025-2026-2")

    def test_resolve_term_from_config(self):
        adapter = ZhengfangAdapter()
        config = AppConfig()
        config.system.xnm = "2025"
        config.system.xqm = "12"
        self.assertEqual(adapter._resolve_term(config), ("2025", "12"))


if __name__ == "__main__":
    unittest.main()
