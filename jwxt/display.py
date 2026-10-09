"""终端里的课表展示。"""

from __future__ import annotations

from .models import Schedule


def format_schedule(schedule: Schedule) -> str:
    """按星期分组输出一个易读的课表。"""
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    lines = [
        f"{schedule.term or '未知学期'}  共 {len(schedule.courses)} 条课程安排",
        "",
    ]
    for day in range(1, 8):
        courses = sorted(
            (c for c in schedule.courses if c.day == day),
            key=lambda c: (c.start_period or 99, c.start_time or "99:99", c.name),
        )
        if not courses:
            continue
        lines.append(names[day - 1])
        for c in courses:
            period = c.period_text or "时间未知"
            meta = "  ".join(x for x in [c.teacher, c.location] if x)
            weeks = c.week_text or "周次未知"
            lines.append(f"  {period:<10} {c.name}  {meta}  [{weeks}]")
        lines.append("")

    missing = [c.name for c in schedule.courses if c.day is None]
    if missing:
        lines.append("未识别星期（可在 JSON 中手工修正）：")
        lines.append("  " + "、".join(missing))
    return "\n".join(lines).rstrip()
