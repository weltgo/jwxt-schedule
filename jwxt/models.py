"""领域模型：把不同教务系统的返回统一成同一份课表结构。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Course(BaseModel):
    """一门课在某个时间段的一次安排。"""

    name: str
    teacher: str = ""
    location: str = ""
    # 1=周一 ... 7=周日
    day: int | None = Field(default=None, ge=1, le=7)
    start_period: int | None = Field(default=None, ge=1, le=20)
    end_period: int | None = Field(default=None, ge=1, le=20)
    # 有的系统直接给时间，而不是节次
    start_time: str = ""
    end_time: str = ""
    # 周次，例如 [1, 2, 3, 5]；空列表表示系统未给出/需要人工确认
    weeks: list[int] = Field(default_factory=list)
    note: str = ""
    # 保留原始字段，方便二次开发或排查问题
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def day_name(self) -> str:
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return names[self.day - 1] if self.day else ""

    @property
    def period_text(self) -> str:
        if self.start_period is not None:
            if self.end_period is None or self.end_period == self.start_period:
                return f"第{self.start_period}节"
            return f"第{self.start_period}-{self.end_period}节"
        if self.start_time and self.end_time:
            return f"{self.start_time}-{self.end_time}"
        return ""

    @property
    def week_text(self) -> str:
        if not self.weeks:
            return ""
        from .parsing import compact_numbers

        return compact_numbers(self.weeks) + "周"


class Schedule(BaseModel):
    """一份完整的课表。"""

    term: str = ""
    student: str = ""
    source: str = ""
    fetched_at: datetime = Field(default_factory=datetime.now)
    courses: list[Course] = Field(default_factory=list)

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent, exclude_none=False)

    @classmethod
    def from_json(cls, text: str) -> "Schedule":
        return cls.model_validate_json(text)

    def save(self, path: str | Path, indent: int = 2) -> None:
        Path(path).write_text(self.to_json(indent=indent), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Schedule":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def summary(self) -> str:
        return (
            f"{self.term or '未知学期'} / {self.student or '未知学生'} / "
            f"{len(self.courses)} 条安排（来源：{self.source or '未知'}）"
        )
