"""jwxt-schedule: 教务系统课表获取与导出工具。"""

__version__ = "0.4.0"

from .models import Course, Schedule

__all__ = ["Course", "Schedule", "__version__"]
