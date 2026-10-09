"""抓取/导出共享流程：CLI、交互向导都复用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adapters import AdapterError, CaptchaRequired, LoginError, SSORequired, detect_adapter
from .config import AppConfig
from .exporters.csv_out import save_csv
from .exporters.ics import ICSExportError, save_ics
from .http import JWXTClient
from .models import Schedule


@dataclass
class FetchOutcome:
    schedule: Schedule | None = None
    error: Exception | None = None
    adapter_name: str = ""

    @property
    def ok(self) -> bool:
        return self.schedule is not None

    @property
    def needs_user_action(self) -> bool:
        """需要用户去浏览器登录/输入 Cookie。"""
        return isinstance(self.error, (CaptchaRequired, SSORequired))

    @property
    def is_login_error(self) -> bool:
        return isinstance(self.error, LoginError)

    @property
    def is_adapter_error(self) -> bool:
        return isinstance(self.error, AdapterError)


def fetch_schedule(config: AppConfig, *, skip_login: bool = False) -> FetchOutcome:
    """执行一次完整的“识别 adapter → 登录 → 取课表”。"""
    adapter_name = ""
    try:
        with JWXTClient(config) as client:
            adapter = detect_adapter(client, config)
            adapter_name = adapter.name
            if not skip_login:
                adapter.login(client, config)
            schedule = adapter.fetch(client, config)
        return FetchOutcome(schedule=schedule, adapter_name=adapter_name)
    except (CaptchaRequired, SSORequired, LoginError, AdapterError) as exc:
        return FetchOutcome(error=exc, adapter_name=adapter_name)
    except Exception as exc:  # 网络/解析等
        return FetchOutcome(error=exc, adapter_name=adapter_name)


def fetch_with_user_assist(
    config: AppConfig,
    *,
    skip_login_initial: bool = False,
    password: str = "",
    ask_password: Callable[[str], str] | None = None,
    ask_cookie: Callable[[], str] | None = None,
    note: Callable[[str], None] = print,
) -> FetchOutcome:
    """自动抓取；失败时按情况提示用户输入密码或 Cookie。

    这个循环同时服务于 `jwxt fetch` 和交互向导。
    """
    skip_login = skip_login_initial
    outcome = FetchOutcome()
    for attempt in range(1, 5):
        if password:
            config.account.password = password
        outcome = fetch_schedule(config, skip_login=skip_login)
        if outcome.ok:
            return outcome

        if outcome.needs_user_action:
            note(f"需要人工处理：{outcome.error}")
            if ask_cookie is None:
                return outcome
            cookie = ask_cookie()
            if not cookie:
                return outcome
            config.account.cookie_string = cookie
            config.account.password = ""
            skip_login = True
            continue

        if outcome.is_login_error:
            if ask_password is None or ask_cookie is None:
                return outcome
            if skip_login:
                note("上次会话可能已过期，需要重新登录。")
                skip_login = False
                password = ask_password("重新输入密码（回车改用 Cookie）")
                if password:
                    config.account.cookie_string = ""
                    continue
            else:
                note(f"登录失败：{outcome.error}")
                password = ask_password("重新输入密码（回车改用 Cookie）")
                if password:
                    config.account.cookie_string = ""
                    continue
            cookie = ask_cookie()
            if cookie:
                config.account.cookie_string = cookie
                config.account.password = ""
                skip_login = True
                continue
            return outcome

        note(f"抓取失败（第 {attempt} 次）：{outcome.error}")
        return outcome
    return outcome


@dataclass
class ExportOutcome:
    saved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def export_schedule(
    schedule: Schedule,
    config: AppConfig,
    *,
    json_path: str = "",
    ics_path: str = "",
    csv_path: str = "",
) -> ExportOutcome:
    outcome = ExportOutcome()
    if json_path:
        schedule.save(json_path)
        outcome.saved.append(f"JSON: {json_path}")
    if csv_path:
        save_csv(schedule, csv_path)
        outcome.saved.append(f"CSV:  {csv_path}")
    if ics_path:
        try:
            save_ics(schedule, config, ics_path)
            outcome.saved.append(f"iCal: {ics_path}")
        except ICSExportError as exc:
            outcome.errors.append(str(exc))
    return outcome


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def session_file_for(base_url: str) -> str:
    """选择第一个可写的会话缓存目录，避免只读 HOME 导致整体失败。"""
    import os
    import tempfile
    from urllib.parse import urlparse

    host = urlparse(base_url).hostname or "default"
    candidates = [
        Path.home() / ".config" / "jwxt",
        Path.home() / ".local" / "share" / "jwxt",
        Path.cwd() / ".jwxt",
        Path(tempfile.gettempdir()) / f"jwxt-{os.getuid()}",
    ]
    for directory in candidates:
        if _writable_dir(directory):
            return str(directory / f"{host}.cookies.json")
    # 最后兜底：即使不可写也返回，save_cookies 会安全忽略
    return str(Path(tempfile.gettempdir()) / f"jwxt-{os.getuid()}" / f"{host}.cookies.json")


def has_saved_session(config: AppConfig) -> bool:
    if config.account.cookie_string:
        return True
    if config.account.cookie_file:
        return Path(config.account.cookie_file).expanduser().exists()
    if not config.http.session_file:
        return False
    path = Path(config.http.session_file).expanduser()
    try:
        return path.exists() and path.stat().st_size > 2
    except OSError:
        return False
