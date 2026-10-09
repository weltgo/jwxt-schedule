"""导出 CSV，方便用 Excel/WPS 打开。"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..models import Schedule


def schedule_to_csv(schedule: Schedule) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["课程名称", "教师", "星期", "节次", "周次", "地点", "备注"])
    for c in schedule.courses:
        writer.writerow(
            [
                c.name,
                c.teacher,
                c.day_name,
                c.period_text,
                c.week_text,
                c.location,
                c.note,
            ]
        )
    return buffer.getvalue()


def save_csv(schedule: Schedule, path: str | Path) -> None:
    Path(path).write_text(schedule_to_csv(schedule), encoding="utf-8-sig")
