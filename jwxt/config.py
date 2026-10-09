"""配置加载与校验。"""

from __future__ import annotations

import os
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AccountConfig(BaseModel):
    username: str = ""
    # 仅用于本次运行的内存密码（CLI 交互输入/--password-stdin），不会写入 JSON/日志
    password: str = Field(default="", exclude=True, repr=False)
    # 推荐：密码只放在环境变量里，不落盘
    password_env: str = "JWXT_PASSWORD"
    # 也可以从命令读取，例如 "pass show jwxt"
    password_cmd: str = ""
    # 如果学校支持，可直接给 cookie，跳过登录
    cookie_string: str = ""
    cookie_file: str = ""


class SystemConfig(BaseModel):
    # auto / zhengfang / generic_html / demo
    type: str = "auto"
    # 例如 https://jw.example.edu.cn 或 https://jw.example.edu.cn/jwglxt
    base_url: str = ""
    login_path: str = ""
    schedule_path: str = ""
    # 学期：2025-2026-1 这类展示名；xnm/xqm 用于正方接口
    xnm: str = ""
    xqm: str = ""
    # 额外表单字段，适配学校定制登录参数
    extra_login_fields: dict[str, str] = Field(default_factory=dict)
    # generic_html：取课表页面的请求方法与额外参数
    schedule_method: str = "GET"
    schedule_data: dict[str, Any] = Field(default_factory=dict)


class HttpConfig(BaseModel):
    timeout: float = 20.0
    # 请求间隔，避免给学校服务器造成压力
    min_interval: float = 1.2
    verify: bool = True
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
    session_file: str = ""
    # 显式代理，例如 "http://127.0.0.1:7890"；留空则读取环境变量
    proxy: str = ""
    trust_env: bool = True


class TermConfig(BaseModel):
    name: str = ""
    # 第一周周一，生成日历时使用
    start_date: date | None = None
    weeks_count: int = 20


class PeriodConfig(BaseModel):
    index: int = Field(ge=1, le=20)
    start: str = ""
    end: str = ""


DEFAULT_PERIODS = [
    PeriodConfig(index=1, start="08:00", end="08:45"),
    PeriodConfig(index=2, start="08:55", end="09:40"),
    PeriodConfig(index=3, start="10:00", end="10:45"),
    PeriodConfig(index=4, start="10:55", end="11:40"),
    PeriodConfig(index=5, start="14:00", end="14:45"),
    PeriodConfig(index=6, start="14:55", end="15:40"),
    PeriodConfig(index=7, start="16:00", end="16:45"),
    PeriodConfig(index=8, start="16:55", end="17:40"),
    PeriodConfig(index=9, start="19:00", end="19:45"),
    PeriodConfig(index=10, start="19:55", end="20:40"),
]


class AppConfig(BaseModel):
    account: AccountConfig = Field(default_factory=AccountConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    term: TermConfig = Field(default_factory=TermConfig)
    periods: list[PeriodConfig] = Field(default_factory=lambda: list(DEFAULT_PERIODS))
    # 保存原始配置，adapter 可读取学校个性化字段
    extras: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("periods")
    @classmethod
    def _sort_periods(cls, value: list[PeriodConfig]) -> list[PeriodConfig]:
        return sorted(value, key=lambda p: p.index)

    @property
    def period_map(self) -> dict[int, PeriodConfig]:
        return {p.index: p for p in self.periods}

    def resolve_password(self) -> str:
        if self.account.password:
            return self.account.password
        if self.account.password_env:
            pw = os.environ.get(self.account.password_env, "")
            if pw:
                return pw
        if self.account.password_cmd:
            import subprocess

            out = subprocess.run(
                self.account.password_cmd,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return out.strip().splitlines()[0] if out.strip() else ""
        return ""

    def ensure_base_url(self) -> str:
        if not self.system.base_url:
            raise ValueError("缺少 system.base_url，请先在配置或命令行中设置教务系统地址")
        return self.system.base_url.rstrip("/")


def load_config(path: str | Path | None = None, **overrides: Any) -> AppConfig:
    """读取 TOML 配置；命令行参数可通过 overrides 覆盖。"""
    data: dict[str, Any] = {}
    if path:
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在：{p}")
        with p.open("rb") as f:
            data = tomllib.load(f)

    for key, value in overrides.items():
        if value is None:
            continue
        if "." in key:
            section, name = key.split(".", 1)
            data.setdefault(section, {})[name] = value
        else:
            data[key] = value

    # 将未知顶层字段收进 extras，方便学校定制 adapter 读取
    known = {"account", "system", "http", "term", "periods", "extras"}
    extras = {k: v for k, v in data.items() if k not in known}
    config = AppConfig.model_validate(data)
    config.extras = {**config.extras, **extras}
    return config


SAMPLE_CONFIG = '''# 教务课表获取器配置示例
# 密码建议放环境变量：export JWXT_PASSWORD='你的密码'
[account]
username = "你的学号"
password_env = "JWXT_PASSWORD"
# password_cmd = "pass show school/jwxt"
# cookie_string = "JSESSIONID=xxx; route=xxx"
# cookie_file = "~/cookies.txt"

[system]
# auto: 探测；zhengfang: 常见 jwglxt；generic_html: 通用表单登录+表格解析；demo: 本地示例
type = "auto"
base_url = "https://jw.example.edu.cn"
# 有些学校部署在 /jwglxt 子路径下，例如 https://jw.example.edu.cn/jwglxt
# base_url 填到子路径也可以，程序会自动兼容
# login_path / schedule_path 留空则由 adapter 决定；定制系统可填写
# xnm = "2025"        # 正方：学年
# xqm = "3"           # 正方：3=第一学期, 12=第二学期

[http]
timeout = 20
min_interval = 1.2
verify = true
# session_file = "~/.config/jwxt/cookies.json"

[term]
name = "2025-2026-1"
start_date = 2025-09-01
weeks_count = 20

# 如果学校节次时间不同，改这里。生成 iCal 时需要。
[[periods]]
index = 1
start = "08:00"
end = "08:45"
[[periods]]
index = 2
start = "08:55"
end = "09:40"
[[periods]]
index = 3
start = "10:00"
end = "10:45"
[[periods]]
index = 4
start = "10:55"
end = "11:40"
[[periods]]
index = 5
start = "14:00"
end = "14:45"
[[periods]]
index = 6
start = "14:55"
end = "15:40"
[[periods]]
index = 7
start = "16:00"
end = "16:45"
[[periods]]
index = 8
start = "16:55"
end = "17:40"
[[periods]]
index = 9
start = "19:00"
end = "19:45"
[[periods]]
index = 10
start = "19:55"
end = "20:40"
'''
