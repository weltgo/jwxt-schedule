"""把课表导出为 iCalendar (.ics)，可导入手机/电脑日历。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import AppConfig
from ..models import Course, Schedule


class ICSExportError(RuntimeError):
    pass


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str, limit: int = 74) -> list[str]:
    """RFC 5545 要求行不超过 75 个八位组，中文也要按 UTF-8 长度计算。"""
    lines: list[str] = []
    current = ""
    size = 0
    for ch in line:
        ch_size = len(ch.encode("utf-8"))
        if size + ch_size > limit and current:
            lines.append(current)
            current = " " + ch
            size = 1 + ch_size
        else:
            current += ch
            size += ch_size
    lines.append(current)
    return lines


def _ics_datetime(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _course_times(course: Course, config: AppConfig) -> tuple[str, str] | None:
    if course.start_time and course.end_time:
        return course.start_time, course.end_time
    if course.start_period is None:
        return None
    start_cfg = config.period_map.get(course.start_period)
    end_index = course.end_period or course.start_period
    end_cfg = config.period_map.get(end_index)
    if not start_cfg or not end_cfg:
        return None
    return start_cfg.start, end_cfg.end


def schedule_to_ics(schedule: Schedule, config: AppConfig) -> str:
    if config.term.start_date is None:
        raise ICSExportError(
            "生成日历时需要知道第一周周一，请在配置里设置 term.start_date（例如 2025-09-01）"
        )

    assume_all = bool(config.extras.get("ics_assume_all_weeks", False))
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//jwxt-schedule//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(schedule.term or '课程表')}",
        f"X-WR-CALDESC:{_escape(schedule.summary())}",
    ]

    skipped: list[str] = []
    for course in schedule.courses:
        times = _course_times(course, config)
        if course.day is None or times is None:
            skipped.append(course.name)
            continue

        weeks = course.weeks
        if not weeks:
            if assume_all:
                weeks = list(range(1, config.term.weeks_count + 1))
            else:
                skipped.append(course.name)
                continue

        for week in sorted(set(weeks)):
            if week < 1:
                continue
            day_date = config.term.start_date + timedelta(days=(week - 1) * 7 + (course.day - 1))
            start_h, start_m = _split_time(times[0])
            end_h, end_m = _split_time(times[1])
            start = datetime(day_date.year, day_date.month, day_date.day, start_h, start_m)
            end = datetime(day_date.year, day_date.month, day_date.day, end_h, end_m)
            if end <= start:
                end = start + timedelta(minutes=45)

            uid_src = f"{course.name}|{course.day}|{course.start_period}|{week}|{course.location}"
            uid = hashlib.sha1(uid_src.encode("utf-8")).hexdigest()[:16] + "@jwxt-schedule"
            description = "；".join(
                x
                for x in [
                    f"教师：{course.teacher}" if course.teacher else "",
                    f"周次：第{week}周" + (f"（{course.week_text}）" if course.week_text else ""),
                    f"节次：{course.period_text}" if course.period_text else "",
                    f"备注：{course.note}" if course.note else "",
                    f"来源：{schedule.source}",
                ]
                if x
            )

            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{now}",
                    f"DTSTART:{_ics_datetime(start)}",
                    f"DTEND:{_ics_datetime(end)}",
                    f"SUMMARY:{_escape(course.name)}",
                    f"LOCATION:{_escape(course.location)}",
                    f"DESCRIPTION:{_escape(description)}",
                    "END:VEVENT",
                ]
            )

    lines.append("END:VCALENDAR")
    if skipped:
        # 日历里放一条备注，避免静默丢课
        lines.insert(
            7,
            f"X-WR-NOTE:{_escape('以下课程因缺少周次或时间未导出：' + '、'.join(sorted(set(skipped))))}",
        )

    output: list[str] = []
    for line in lines:
        output.extend(_fold(line))
    return "\r\n".join(output) + "\r\n"


def _split_time(value: str) -> tuple[int, int]:
    try:
        h, m = value.strip().split(":", 1)
        return int(h), int(m)
    except (ValueError, AttributeError):
        return 8, 0


def save_ics(schedule: Schedule, config: AppConfig, path: str | Path) -> None:
    Path(path).write_text(schedule_to_ics(schedule, config), encoding="utf-8")
