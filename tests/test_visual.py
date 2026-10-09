import unittest

from jwxt.visual import parse_visual_schedule


class VisualParserTests(unittest.TestCase):
    def _element(self, text, x, y, w=60, h=18):
        return {"text": text, "x": x, "y": y, "w": w, "h": h}

    def test_div_grid_layout(self):
        elements = [
            self._element("星期一", 200, 100, 40),
            self._element("星期二", 350, 100, 40),
            self._element("星期三", 500, 100, 40),
            self._element("上课时间", 30, 60, 50),
            # 第一大节
            self._element("第1-2节", 20, 150, 60),
            self._element("08:00-09:40", 20, 172, 80),
            self._element("高等数学", 180, 155, 70),
            self._element("王老师", 180, 177, 50),
            self._element("1-16周", 180, 199, 60),
            self._element("教一101", 180, 221, 70),
            # 第二大节
            self._element("第3-4节", 20, 300, 60),
            self._element("10:00-11:40", 20, 322, 80),
            self._element("程序设计基础", 330, 305, 90),
            self._element("张老师", 330, 327, 50),
            self._element("1-8周", 330, 349, 60),
            self._element("实验楼A305", 330, 371, 90),
        ]
        schedule = parse_visual_schedule(elements, term="2025-2026-1")
        self.assertEqual(len(schedule.courses), 2)
        math = [c for c in schedule.courses if c.name == "高等数学"][0]
        self.assertEqual(math.day, 1)
        self.assertEqual((math.start_period, math.end_period), (1, 2))
        self.assertEqual(math.teacher, "王老师")
        self.assertEqual(math.location, "教一101")


if __name__ == "__main__":
    unittest.main()
