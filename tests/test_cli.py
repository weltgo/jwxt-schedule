import unittest

from jwxt.cli import build_advanced_parser, build_parser


class CLIParserTests(unittest.TestCase):
    def test_fetch_defaults_are_minimal(self):
        args = build_parser().parse_args(["fetch", "https://jw.example.edu.cn"])
        self.assertEqual(args.url_pos, "https://jw.example.edu.cn")
        self.assertEqual(args.out, "schedule.json")
        self.assertEqual(args.ics, "schedule.ics")
        self.assertEqual(args.csv, "schedule.csv")
        self.assertFalse(args.no_ics)
        self.assertFalse(args.no_csv)

    def test_parse_uses_positional_input(self):
        args = build_parser().parse_args(["parse", "课表.html"])
        self.assertEqual(args.input, "课表.html")

    def test_export_uses_positional_input(self):
        args = build_parser().parse_args(["export", "schedule.json", "--format", "csv"])
        self.assertEqual(args.input, "schedule.json")
        self.assertEqual(args.format, "csv")

    def test_advanced_probe_parser(self):
        args = build_advanced_parser().parse_args(["probe", "--url", "https://jw.example.edu.cn"])
        self.assertEqual(args.command, "probe")
        self.assertEqual(args.url, "https://jw.example.edu.cn")


if __name__ == "__main__":
    unittest.main()
