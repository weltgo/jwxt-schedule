import unittest

from jwxt.parsing import (
    compact_numbers,
    parse_cell_text,
    parse_day,
    parse_periods,
    parse_time_range,
    parse_weeks,
)


class ParsingTests(unittest.TestCase):
    def test_weeks_ranges(self):
        self.assertEqual(parse_weeks("1-16周"), list(range(1, 17)))
        self.assertEqual(parse_weeks("第1,3,5周"), [1, 3, 5])
        self.assertEqual(parse_weeks("1-4,6,8-10周"), [1, 2, 3, 4, 6, 8, 9, 10])
        self.assertEqual(parse_weeks("大学英语2 1-16周"), list(range(1, 17)))

    def test_weeks_parity(self):
        self.assertEqual(parse_weeks("1-16周(单)"), [1, 3, 5, 7, 9, 11, 13, 15])
        self.assertEqual(parse_weeks("1-16单周"), [1, 3, 5, 7, 9, 11, 13, 15])
        self.assertEqual(parse_weeks("1-16周(双)"), [2, 4, 6, 8, 10, 12, 14, 16])

    def test_weeks_unknown(self):
        self.assertEqual(parse_weeks(""), [])
        self.assertEqual(parse_weeks("全周"), [])
        self.assertEqual(parse_weeks("课程名称没有周次"), [])

    def test_periods(self):
        self.assertEqual(parse_periods("第1-2节"), (1, 2))
        self.assertEqual(parse_periods("3-4节"), (3, 4))
        self.assertEqual(parse_periods("第9节"), (9, 9))
        self.assertEqual(parse_periods("0102节"), (1, 2))
        self.assertEqual(parse_periods("1011节"), (10, 11))
        self.assertEqual(parse_periods("第一节"), (1, 1))
        self.assertEqual(parse_periods("高等数学"), (None, None))

    def test_day_and_time(self):
        self.assertEqual(parse_day("星期一"), 1)
        self.assertEqual(parse_day("周三"), 3)
        self.assertEqual(parse_day("礼拜日"), 7)
        self.assertEqual(parse_day("第1-2节"), None)
        self.assertEqual(parse_time_range("08:00-09:40"), ("08:00", "09:40"))
        self.assertEqual(parse_time_range("8：00—8：45"), ("08:00", "08:45"))

    def test_compact_numbers(self):
        self.assertEqual(compact_numbers([1, 2, 3, 5, 6]), "1-3,5-6")
        self.assertEqual(compact_numbers([]), "")

    def test_parse_cell_text(self):
        courses = parse_cell_text(
            "高等数学\n王老师\n1-16周\n教一101",
            day=1,
            start_period=1,
            end_period=2,
        )
        self.assertEqual(len(courses), 1)
        course = courses[0]
        self.assertEqual(course["name"], "高等数学")
        self.assertEqual(course["teacher"], "王老师")
        self.assertEqual(course["location"], "教一101")
        self.assertEqual(course["day"], 1)
        self.assertEqual(course["start_period"], 1)
        self.assertEqual(course["end_period"], 2)
        self.assertEqual(len(course["weeks"]), 16)

    def test_parse_cell_text_split_by_separator(self):
        courses = parse_cell_text(
            "高等数学\n王老师\n1-16周\n---\n大学英语\n李老师\n1-8周",
            day=2,
            start_period=3,
            end_period=4,
        )
        self.assertEqual([c["name"] for c in courses], ["高等数学", "大学英语"])


if __name__ == "__main__":
    unittest.main()
