"""文本解析工具：星期、节次、周次、课程单元格。"""

from __future__ import annotations

import re
from typing import Iterable

WEEKDAY_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
}

DAY_RE = re.compile(r"(?:星期|周|礼拜)\s*([一二三四五六日天1-7])")
PERIOD_RE = re.compile(
    r"(?:第\s*)?(\d{1,2})\s*(?:[-~—–至到,，、]\s*(\d{1,2}))?\s*(?:节|课|时)"
)
WEEK_RANGE_RE = re.compile(r"(\d{1,2})\s*[-~—–至到]\s*(\d{1,2})")

LOCATION_HINTS = (
    "楼", "教", "室", "区", "馆", "厅", "场", "中心", "基地", "实验室", "校区", "号",
)


def normalize_ws(text: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", text.replace("\r", "\n")).strip()


WEEK_SEQ_RE = re.compile(
    r"(?P<seq>(?:\d{1,2}\s*[-~—–至到,，、]\s*)*\d{1,2})"
    r"\s*(?P<parity_before>[单双])?\s*周"
    r"\s*[（(]?\s*(?P<parity_after>[单双])?\s*[）)]?"
)


def parse_weeks(text: str) -> list[int]:
    """从“1-16周(单)”“第1,3,5周”等文本中提取周次列表。

    找不到明确周次时返回 []，表示“未知”，不要当成“全周”，以免生成错误的日历。
    """
    if not text:
        return []
    t = normalize_ws(text)
    weeks: set[int] = set()
    odd = False
    even = False
    matched = False

    for m in WEEK_SEQ_RE.finditer(t):
        matched = True
        weeks.update(_expand_week_sequence(m.group("seq")))
        for parity in (m.group("parity_before"), m.group("parity_after")):
            if parity == "单":
                odd = True
            elif parity == "双":
                even = True

    # 容错：格式为“1-16”“1,3,5”时，若整串看起来就是周次，也接受
    if not matched and re.fullmatch(r"[\d\s,，、\-~—–至到]+", t):
        weeks.update(_expand_week_sequence(t))
        matched = True
        if "单周" in t:
            odd = True
        if "双周" in t:
            even = True

    if not matched or not weeks:
        return []

    if odd and not even:
        weeks = {w for w in weeks if w % 2 == 1}
    elif even and not odd:
        weeks = {w for w in weeks if w % 2 == 0}
    return sorted(w for w in weeks if 1 <= w <= 40)


def _expand_week_sequence(seq: str) -> set[int]:
    """展开 '1-4,6,8-10' 这样的周次串。"""
    result: set[int] = set()
    # 先把范围替换成占位，再解析剩余单个数
    def _range_repl(m: re.Match) -> str:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= b <= 40:
            result.update(range(a, b + 1))
        return " "

    singles = re.sub(r"(\d{1,2})\s*[-~—–至到]\s*(\d{1,2})", _range_repl, seq)
    for m in re.finditer(r"\d{1,2}", singles):
        n = int(m.group(0))
        if 1 <= n <= 40:
            result.add(n)
    return result


def compact_numbers(numbers: Iterable[int]) -> str:
    """[1,2,3,5,6] -> '1-3,5-6'"""
    nums = sorted(set(int(n) for n in numbers))
    if not nums:
        return ""
    parts: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(parts)


def parse_day(text: str) -> int | None:
    if not text:
        return None
    m = DAY_RE.search(text)
    if m:
        return WEEKDAY_MAP.get(m.group(1))
    # 允许“周一”之外的形式，如单独的“一”或“1”
    stripped = normalize_ws(text)
    if stripped in WEEKDAY_MAP:
        return WEEKDAY_MAP[stripped]
    return None


CN_PERIOD_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
    "九": 9, "十": 10, "十一": 11, "十二": 12,
}


def parse_periods(text: str) -> tuple[int | None, int | None]:
    """从“第1-2节”“3-4节”“0102节”“第一节”等文本中提取节次。"""
    if not text:
        return None, None

    # 部分旧系统用连续两位数表示节次，如 0102=1-2 节
    compact = re.fullmatch(r"\s*(?:第)?\s*(\d{2})(\d{2})\s*[节课]\s*", text)
    if compact:
        start, end = int(compact.group(1)), int(compact.group(2))
        if 1 <= start <= end <= 20:
            return start, end

    m = PERIOD_RE.search(text)
    if m:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if 1 <= start <= end <= 20:
            return start, end

    cn = re.search(r"(?:第)?\s*([一二三四五六七八九十]{1,3})\s*[节课]", text)
    if cn and cn.group(1) in CN_PERIOD_MAP:
        value = CN_PERIOD_MAP[cn.group(1)]
        return value, value
    return None, None


def parse_cell_text(
    text: str,
    day: int | None = None,
    start_period: int | None = None,
    end_period: int | None = None,
) -> list[dict]:
    """把表格单元格里的课程文本拆成较规范的字段。

    这是启发式解析：不同学校格式差异很大，正式使用时应针对学校
    自定义 adapter，或者用这里解析出的结果再人工修正。
    """
    lines = [normalize_ws(x) for x in re.split(r"[\n\r]+", text) if normalize_ws(x)]
    if not lines:
        return []

    # 一些系统把多个课程塞进一个单元格，用空行或分隔线隔开
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if re.fullmatch(r"[-—–_=]{3,}", line):
            if current:
                chunks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        chunks.append(current)

    result: list[dict] = []
    for chunk in chunks:
        result.extend(_parse_chunk(chunk, day, start_period, end_period))
    return result


def _parse_chunk(
    lines: list[str],
    day: int | None,
    start_period: int | None,
    end_period: int | None,
) -> list[dict]:
    if not lines:
        return []

    name = lines[0]
    teacher = ""
    location = ""
    week_text = ""
    leftovers: list[str] = []

    for line in lines[1:]:
        if not week_text and "周" in line and re.search(r"\d", line):
            week_text = line
            continue
        if not location and any(h in line for h in LOCATION_HINTS):
            location = line
            continue
        if not teacher and len(line) <= 20 and not re.search(r"\d{2}", line):
            teacher = line
            continue
        leftovers.append(line)

    # 如果第一行里混了“课程名 周次”，做一次拆分
    if "周" in name and not week_text:
        m = re.search(r"\d{1,2}(?:\s*[-~—–至到,，、]\s*\d{1,2})*\s*周", name)
        if m:
            week_text = m.group(0)
            name = (name[: m.start()] + name[m.end():]).strip(" -()（）")

    week_end = end_period
    if start_period is not None and end_period is None:
        week_end = start_period
    return [
        {
            "name": name,
            "teacher": teacher,
            "location": location,
            "day": day,
            "start_period": start_period,
            "end_period": week_end,
            "weeks": parse_weeks(week_text) if week_text else [],
            "note": " / ".join(leftovers),
        }
    ]


TIME_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})\s*[-~—–至到]\s*(\d{1,2})\s*[:：]\s*(\d{2})")


def parse_time_range(text: str) -> tuple[str, str]:
    """提取 '08:00-08:45' 或 '8：00-8：45' 形式的时间。"""
    if not text:
        return "", ""
    m = TIME_RE.search(text)
    if not m:
        return "", ""
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return "", ""
    return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"
