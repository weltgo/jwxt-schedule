"""HTML 课表解析：支持“网格表”和“列表表”两种常见结构。

不同学校的教务系统 DOM 差异很大。这里的解析器尽量做到通用，
但正式接入时建议根据学校页面补一套专用 adapter 或规则。
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from .models import Course, Schedule
from .parsing import (
    normalize_ws,
    parse_cell_text,
    parse_day,
    parse_periods,
    parse_time_range,
    parse_weeks,
)

HeaderMap = dict[int, int]  # 列序号 -> 星期几


class ScheduleParseError(Exception):
    pass


def cell_text(cell: Tag) -> str:
    """保留 <br> 等块级换行，便于按行拆分课程信息。"""
    for br in cell.find_all("br"):
        br.replace_with("\n")
    text = cell.get_text("\n", strip=True)
    return normalize_ws(text)


def _exact_day(text: str) -> int | None:
    cleaned = re.sub(r"[\s\u3000]+", "", text)
    if not cleaned or len(cleaned) > 5:
        return None
    m = re.fullmatch(r"(?:星期|周|礼拜)?([一二三四五六日天1-7])", cleaned)
    if not m:
        return None
    return parse_day(m.group(0))


def _find_grid_header(rows: list[list[Tag | None]]) -> tuple[int, HeaderMap, int | None]:
    """在前几行里找“星期几 × 节次”的表头。"""
    best: tuple[int, HeaderMap, int | None] | None = None
    for idx, row in enumerate(rows[:8]):
        mapping: HeaderMap = {}
        time_col: int | None = None
        for col, cell in enumerate(row):
            if cell is None:
                continue
            text = cell_text(cell)
            day = _exact_day(text)
            if day:
                mapping[col] = day
            elif re.search(r"时间|节次|节数|时段", text):
                time_col = col
        if len(mapping) >= 2 and (best is None or len(mapping) > len(best[1])):
            best = (idx, mapping, time_col)
    if best is None:
        # 放宽：有些系统表头写“周一/周二”且第一列是“时间”
        for idx, row in enumerate(rows[:8]):
            mapping = {}
            for col, cell in enumerate(row):
                if cell is None:
                    continue
                day = _exact_day(cell_text(cell))
                if day:
                    mapping[col] = day
            if len(mapping) >= 3:
                return idx, mapping, None
        raise ScheduleParseError("未找到星期表头")
    return best


FIELD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("name", re.compile(r"课程名称|课程名|课程|科目")),
    ("teacher", re.compile(r"教师|老师|任课")),
    ("day", re.compile(r"星期|周几|上课日")),
    ("period", re.compile(r"节次|节数|节")),
    ("weeks", re.compile(r"周次|上课周|周数")),
    ("location", re.compile(r"地点|教室|上课地点|场地")),
    ("time", re.compile(r"时间|时段")),
]


def _find_flat_header(rows: list[list[Tag | None]]) -> tuple[int, dict[int, str]] | None:
    best: tuple[int, dict[int, str], int] | None = None
    for idx, row in enumerate(rows[:8]):
        fields: dict[int, str] = {}
        for col, cell in enumerate(row):
            if cell is None:
                continue
            text = cell_text(cell)
            if not text or len(text) > 8:
                continue
            for field, pattern in FIELD_PATTERNS:
                if pattern.search(text):
                    # 时间列本身也可能是节次，按更具体的先后放
                    fields[col] = field
                    break
        if len(fields) >= 3 and "name" in fields.values():
            if best is None or len(fields) > len(best[1]):
                best = (idx, fields, len(fields))
    if best:
        return best[0], best[1]
    return None


def parse_html_schedule(html: str, term: str = "", source: str = "") -> Schedule:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise ScheduleParseError("页面中没有 <table>，可能是纯 JS 渲染或需要登录")

    # 优先解析网格表；网格表通常课程更多
    grid_best: list[Course] = []
    flat_best: list[Course] = []
    for table in tables:
        rows = _table_rows(table)
        if not rows:
            continue
        try:
            courses = _parse_grid_table(rows)
            if len(courses) > len(grid_best):
                grid_best = courses
        except ScheduleParseError:
            pass
        courses = _parse_flat_table(rows)
        if len(courses) > len(flat_best):
            flat_best = courses

    if grid_best:
        courses = grid_best
    elif flat_best:
        courses = flat_best
    else:
        raise ScheduleParseError("找到表格，但没有解析出课程；请把该页面保存为 HTML 后反馈以适配")

    return Schedule(term=term, source=source or "html", courses=courses)


def _span(cell: Tag, name: str) -> int:
    try:
        value = int(str(cell.get(name, "1")).strip())
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


def _table_rows(table: Tag) -> list[list[Tag | None]]:
    """把 HTML 表格展开成矩阵，处理 rowspan/colspan，避免单元格错位。"""
    raw_rows: list[list[Tag]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            # 兼容部分系统把 td 包在别的标签里
            cells = tr.find_all(["th", "td"])
        if cells:
            raw_rows.append(cells)

    rows: list[list[Tag | None]] = []
    active: dict[int, tuple[Tag, int]] = {}  # 列号 -> (单元格, 后续还要跨几行)
    for cells in raw_rows:
        row: list[Tag | None] = []
        col = 0
        ci = 0
        while True:
            if col in active:
                tag, left = active.pop(col)
                row.append(tag)
                if left > 1:
                    active[col] = (tag, left - 1)
                col += 1
                continue
            if ci >= len(cells):
                break
            tag = cells[ci]
            ci += 1
            colspan = _span(tag, "colspan")
            rowspan = _span(tag, "rowspan")
            row.append(tag)
            if rowspan > 1:
                for k in range(colspan):
                    active[col + k] = (tag, rowspan - 1)
            for _ in range(1, colspan):
                row.append(tag)
            col += colspan

        # 当前行单元格放完后，填充行尾仍处于 rowspan 中的单元格
        while active:
            remaining = sorted(c for c in active if c >= len(row))
            if not remaining:
                break
            target = remaining[0]
            while len(row) < target:
                row.append(None)
            tag, left = active.pop(target)
            row.append(tag)
            if left > 1:
                active[target] = (tag, left - 1)
        if row:
            rows.append(row)
    return rows


def _parse_grid_table(rows: list[list[Tag | None]]) -> list[Course]:
    header_idx, day_map, time_col_hint = _find_grid_header(rows)
    time_col = time_col_hint if time_col_hint is not None else 0
    courses: list[Course] = []

    for row in rows[header_idx + 1:]:
        if not row:
            continue
        time_cell = row[time_col] if time_col < len(row) else None
        time_text = cell_text(time_cell) if time_cell is not None else ""
        start_period, end_period = parse_periods(time_text)
        row_start_time, row_end_time = parse_time_range(time_text)

        for col, day in day_map.items():
            if col >= len(row):
                continue
            cell = row[col] if col < len(row) else None
            if cell is None:
                continue
            text = cell_text(cell)
            if not text or text in {"-", "—", "/", "无"}:
                continue
            cell_start, cell_end = parse_periods(text)
            start = cell_start or start_period
            end = cell_end or end_period
            start_time, end_time = parse_time_range(text)
            infos = parse_cell_text(text, day=day, start_period=start, end_period=end)
            for info in infos:
                if not info.get("name"):
                    continue
                info["day"] = parse_day(text) or info.get("day") or day
                info["start_period"] = info.get("start_period") or start
                info["end_period"] = info.get("end_period") or end
                info["start_time"] = start_time or row_start_time
                info["end_time"] = end_time or row_end_time
                info["weeks"] = info.get("weeks") or parse_weeks(text)
                courses.append(Course(**info))

    return _dedupe(courses)


def _parse_flat_table(rows: list[list[Tag | None]]) -> list[Course]:
    header = _find_flat_header(rows)
    if not header:
        return []
    header_idx, fields = header
    courses: list[Course] = []
    for row in rows[header_idx + 1:]:
        values = {
            fields[col]: cell_text(row[col])
            for col in fields
            if col < len(row) and row[col] is not None
        }
        if not values.get("name"):
            continue
        day = parse_day(values.get("day", ""))
        start_period, end_period = parse_periods(values.get("period", "") or values.get("time", ""))
        start_time, end_time = parse_time_range(values.get("time", "") or values.get("period", ""))
        courses.append(
            Course(
                name=values.get("name", ""),
                teacher=values.get("teacher", ""),
                location=values.get("location", ""),
                day=day,
                start_period=start_period,
                end_period=end_period,
                start_time=start_time,
                end_time=end_time,
                weeks=parse_weeks(values.get("weeks", "")),
                raw=values,
            )
        )
    return _dedupe(courses)


def _dedupe(courses: list[Course]) -> list[Course]:
    seen: set[Any] = set()
    result: list[Course] = []
    for course in courses:
        key = (
            course.name,
            course.teacher,
            course.location,
            course.day,
            course.start_period,
            course.end_period,
            tuple(course.weeks),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(course)
    return result
