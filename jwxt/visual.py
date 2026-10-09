"""从浏览器文本节点坐标恢复课表（非 table 布局的兜底解析）。

浏览器桥会把每个可见文本节点的 text/x/y/w/h 发回来。对于用 div/CSS
网格/绝对定位画的课表，HTML 里往往没有 <table>，这里用“星期表头 + 节次行
锚点 + 坐标分栏”的方式重建课程单元格。
"""

from __future__ import annotations

from typing import Any

from .models import Course, Schedule
from .parsing import (
    parse_cell_text,
    parse_day,
    parse_periods,
    parse_time_range,
)
from .tables import ScheduleParseError


def _as_items(elements: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in elements or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text or len(text) > 120:
            continue
        try:
            x = float(raw.get("x", 0))
            y = float(raw.get("y", 0))
            w = max(float(raw.get("w", 0)), 1.0)
            h = max(float(raw.get("h", 0)), 1.0)
        except (TypeError, ValueError):
            continue
        items.append({"text": text, "x": x, "y": y, "w": w, "h": h})
    return items


def _overlap_y(a: dict[str, Any], b: dict[str, Any]) -> bool:
    top = max(a["y"], b["y"])
    bottom = min(a["y"] + a["h"], b["y"] + b["h"])
    return bottom - top >= min(a["h"], b["h"]) * 0.4


def _group_rows(items: list[dict[str, Any]], tolerance: float = 6.0) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for item in sorted(items, key=lambda e: (e["y"], e["x"])):
        for group in groups:
            if abs(item["y"] - group[0]["y"]) <= tolerance or any(_overlap_y(item, g) for g in group):
                group.append(item)
                break
        else:
            groups.append([item])
    return [sorted(g, key=lambda e: e["x"]) for g in groups]


def _find_day_header(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [it for it in items[:80] if parse_day(it["text"]) is not None]
    if not candidates:
        return None
    for row in _group_rows(candidates):
        day_items = [(it, parse_day(it["text"])) for it in row]
        day_items = [(it, day) for it, day in day_items if day]
        distinct = {day for _, day in day_items}
        if len(distinct) >= 3:
            cols: dict[int, list[dict[str, Any]]] = {}
            for it, day in day_items:
                cols.setdefault(day, []).append(it)
            centers = []
            for day, group in cols.items():
                centers.append(
                    {
                        "day": day,
                        "x": sum(g["x"] + g["w"] / 2 for g in group) / len(group),
                    }
                )
            centers.sort(key=lambda c: c["x"])
            top = min(it["y"] for it, _ in day_items)
            bottom = max(it["y"] + it["h"] for it, _ in day_items)
            return {"top": top, "bottom": bottom, "cols": centers}
    return None


def _parse_anchor(text: str) -> tuple[int | None, int | None]:
    start, end = parse_periods(text)
    if start is not None:
        return start, end
    # 兼容纯 “1-2” 表示节次
    import re

    m = re.fullmatch(r"\s*(\d{1,2})\s*[-~—–至到]\s*(\d{1,2})\s*", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= b <= 20:
            return a, b
    if re.fullmatch(r"\s*\d{1,2}\s*", text):
        value = int(text.strip())
        if 1 <= value <= 20:
            return value, value
    return None, None


def _is_anchor_item(item: dict[str, Any]) -> bool:
    period = _parse_anchor(item["text"])
    if period != (None, None):
        return True
    return bool(parse_time_range(item["text"])[0])


def _anchor_rows(items: list[dict[str, Any]], header: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        it
        for it in items
        if it["y"] >= header["bottom"] - 2 and _is_anchor_item(it)
    ]
    if not candidates:
        return []
    groups = _group_rows(candidates, tolerance=8.0)
    anchors: list[dict[str, Any]] = []
    for group in groups:
        group.sort(key=lambda e: e["y"])
        text = " ".join(e["text"] for e in group)
        start, end = _parse_anchor(text)
        if start is None:
            # 只有时间没有节次时也保留；时间由单元格自己解析
            start_time, end_time = parse_time_range(text)
            if not start_time:
                continue
        top = min(e["y"] for e in group)
        bottom = max(e["y"] + e["h"] for e in group)
        anchors.append({"top": top, "bottom": bottom, "start": start, "end": end})
    # 合并 y 很近的锚点（少数系统把“节次”和“时间”拆成两行）
    merged: list[dict[str, Any]] = []
    for anchor in sorted(anchors, key=lambda a: a["top"]):
        if merged and abs(anchor["top"] - merged[-1]["top"]) <= 34:
            merged[-1]["bottom"] = max(merged[-1]["bottom"], anchor["bottom"])
            if merged[-1]["start"] is None:
                merged[-1]["start"] = anchor["start"]
                merged[-1]["end"] = anchor["end"]
            continue
        merged.append(anchor)
    return merged


def _column_bounds(centers: list[dict[str, Any]]) -> list[tuple[int, float, float]]:
    bounds: list[tuple[int, float, float]] = []
    count = len(centers)
    for i, col in enumerate(centers):
        if count == 1:
            left, right = col["x"] - 150, col["x"] + 150
        elif i == 0:
            gap = centers[1]["x"] - col["x"]
            left, right = col["x"] - gap / 2, (col["x"] + centers[1]["x"]) / 2
        elif i == count - 1:
            gap = col["x"] - centers[i - 1]["x"]
            left, right = (centers[i - 1]["x"] + col["x"]) / 2, col["x"] + gap / 2
        else:
            left = (centers[i - 1]["x"] + col["x"]) / 2
            right = (col["x"] + centers[i + 1]["x"]) / 2
        bounds.append((col["day"], left, right))
    return bounds


def parse_visual_schedule(
    elements: list[Any],
    term: str = "",
    source: str = "browser:visual",
) -> Schedule:
    items = _as_items(elements)
    items.sort(key=lambda e: (e["y"], e["x"]))
    if len(items) < 5:
        raise ScheduleParseError("浏览器发送的文本坐标太少，无法恢复课表")

    header = _find_day_header(items)
    if header is None:
        raise ScheduleParseError("在页面文本里找不到“星期一/周二”等星期表头")

    anchors = _anchor_rows(items, header)
    if len(anchors) < 1:
        raise ScheduleParseError("找到了星期表头，但找不到“第1-2节”等节次行")

    column_bounds = _column_bounds(header["cols"])
    courses: list[Course] = []
    for idx, anchor in enumerate(anchors):
        band_top = anchor["top"] - 3
        band_bottom = anchors[idx + 1]["top"] - 3 if idx + 1 < len(anchors) else 1e9
        for day, left, right in column_bounds:
            selected = [
                it
                for it in items
                if band_top <= it["y"] < band_bottom
                and left <= (it["x"] + it["w"] / 2) < right
                and it["text"] not in {f"星期{x}" for x in "一二三四五六日"}
            ]
            if not selected:
                continue
            selected.sort(key=lambda e: (e["y"], e["x"]))
            text = "\n".join(it["text"] for it in selected)
            start, end = _parse_anchor(text)
            start = start if start is not None else anchor["start"]
            end = end if end is not None else anchor["end"]
            infos = parse_cell_text(text, day=day, start_period=start, end_period=end)
            for info in infos:
                if not info.get("name"):
                    continue
                time_start, time_end = parse_time_range(text)
                info["start_time"] = info.get("start_time") or time_start
                info["end_time"] = info.get("end_time") or time_end
                courses.append(Course(**info))

    if not courses:
        raise ScheduleParseError("根据坐标没有恢复出课程，可能是课表布局比较特殊")

    # 去重
    deduped: list[Course] = []
    seen: set[tuple] = set()
    for c in courses:
        key = (c.name, c.teacher, c.location, c.day, c.start_period, c.end_period, tuple(c.weeks))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return Schedule(term=term, source=source, courses=deduped)


__all__ = ["parse_visual_schedule"]
