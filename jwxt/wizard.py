"""交互式向导：让不熟悉命令行的用户也能完成配置和抓取。"""

from __future__ import annotations

import getpass
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from .adapters import BaseAdapter
from .config import AppConfig, load_config
from .display import format_schedule
from .http import JWXTClient
from .workflow import (export_schedule, fetch_with_user_assist,
                       has_saved_session, session_file_for)


# ---------------- 交互输入 ----------------
def _ask(prompt: str, default: str = "", allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            print()
            return default if default else ""
        if value:
            return value
        if default:
            return default
        if allow_empty:
            return ""
        print("  不能为空，请重新输入。")


def _yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        answer = input(f"{prompt}{suffix}: ").strip().lower()
    except EOFError:
        print()
        return default
    if not answer:
        return default
    return answer in {"y", "yes", "是", "1", "true"}


def ask_password(prompt: str = "密码（不显示输入；直接回车改用 Cookie 登录）") -> str:
    if not sys.stdin.isatty():
        # 管道/CI 等非交互场景：getpass 会尝试打开 /dev/tty 导致卡住
        try:
            return input(f"{prompt}: ").strip()
        except EOFError:
            return ""
    try:
        return getpass.getpass(f"{prompt}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def ask_cookie() -> str:
    print("\n请在浏览器里登录教务系统，按 F12 → Network → 任意请求 →")
    print("复制 Request Headers 里的 Cookie 整行，然后粘贴到这里。")
    try:
        return input("Cookie（直接回车退出）: ").strip()
    except EOFError:
        return ""


# ---------------- 默认值 ----------------
def _next_weekday(start: date, weekday: int = 0) -> date:
    """返回 start 当天或之后的第一个 weekday（0=周一）。"""
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


def suggest_term_name(today: date | None = None) -> str:
    today = today or date.today()
    if today.month >= 8:
        start_year, semester = today.year, "1"
    elif today.month <= 1:
        start_year, semester = today.year - 1, "1"
    else:
        start_year, semester = today.year - 1, "2"
    return f"{start_year}-{start_year + 1}-{semester}"


def suggest_start_date(today: date | None = None) -> date:
    today = today or date.today()
    if today.month >= 8:
        return _next_weekday(date(today.year, 9, 1))
    if today.month <= 1:
        return _next_weekday(date(today.year - 1, 9, 1))
    return _next_weekday(date(today.year, 2, 20))


def normalize_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if raw and not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


# ---------------- 配置读写 ----------------
def _toml_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_config(config: AppConfig, path: str | Path) -> Path:
    """写出一份精简、可读、不含密码的配置。"""
    p = Path(path).expanduser()
    lines = [
        "# 由 jwxt 向导生成；密码不会保存到这里，请用环境变量 JWXT_PASSWORD。",
        "",
        "[account]",
        f"username = {_toml_value(config.account.username)}",
        f"password_env = {_toml_value(config.account.password_env or 'JWXT_PASSWORD')}",
        "",
        "[system]",
        f"type = {_toml_value(config.system.type or 'auto')}",
        f"base_url = {_toml_value(config.system.base_url)}",
    ]
    if config.system.login_path:
        lines.append(f"login_path = {_toml_value(config.system.login_path)}")
    if config.system.schedule_path:
        lines.append(f"schedule_path = {_toml_value(config.system.schedule_path)}")
    if config.system.xnm:
        lines.append(f"xnm = {_toml_value(config.system.xnm)}")
    if config.system.xqm:
        lines.append(f"xqm = {_toml_value(config.system.xqm)}")
    lines.extend(
        [
            "",
            "[http]",
            f"session_file = {_toml_value(config.http.session_file)}",
            "",
            "[term]",
            f"name = {_toml_value(config.term.name)}",
        ]
    )
    if config.term.start_date:
        lines.append(f"start_date = {config.term.start_date.isoformat()}")
    lines.append(f"weeks_count = {config.term.weeks_count}")
    lines.append("")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


# ---------------- 向导主体 ----------------
def probe_adapter(config: AppConfig) -> tuple[str, Exception | None]:
    """只探测不登录，返回 adapter 名称或错误。"""
    try:
        with JWXTClient(config) as client:
            from .adapters import detect_adapter

            adapter: BaseAdapter = detect_adapter(client, config)
            return adapter.name, None
    except Exception as exc:
        return "", exc


def build_config_interactive(path: str | Path = "jwxt.toml") -> tuple[AppConfig, Path]:
    p = Path(path).expanduser()
    is_new = not p.exists()
    config = AppConfig()

    if p.exists():
        print(f"检测到配置文件：{p}")
        if _yes_no("直接使用已有配置？", default=True):
            try:
                config = load_config(p)
                print(f"已读取：{config.system.base_url or '(未填写地址)'}")
            except Exception as exc:
                print(f"读取失败：{exc}，将重新填写。")
        else:
            print("将重新填写配置。")

    print("\n第 1 步 / 共 3 步：教务系统")
    base_url = _ask("教务系统地址（例如 https://jw.example.edu.cn）", config.system.base_url)
    config.system.base_url = normalize_url(base_url)
    config.account.username = _ask("学号 / 用户名", config.account.username)

    if not config.http.session_file:
        config.http.session_file = session_file_for(config.system.base_url)

    print("\n正在探测系统类型……")
    name, error = probe_adapter(config)
    labels = {"zhengfang": "正方教务", "generic_html": "通用 HTML 教务"}
    if name:
        config.system.type = name
        print(f"识别结果：{labels.get(name, name)}（{name}）")
        if name == "generic_html" and not config.system.schedule_path:
            raw_schedule = _ask(
                "课表页面路径或完整 URL（可回车跳过，之后可用 parse 解析本地 HTML）",
                "",
                allow_empty=True,
            )
            if raw_schedule:
                config.system.schedule_path = raw_schedule.strip()
    else:
        print(f"自动探测未完成：{error}")
        print("将在抓取时继续尝试自动识别；如果失败，可运行 `jwxt probe` 查看线索。")

    print("\n第 2 步 / 共 3 步：学期")
    config.term.name = _ask("学期名称", config.term.name or suggest_term_name())
    default_date = (config.term.start_date or suggest_start_date()).isoformat()
    raw_date = _ask("第一周周一（输入 - 跳过 iCal 导出）", default_date)
    if raw_date.strip() in {"-", "skip", "跳过"}:
        config.term.start_date = None
        print("已跳过 iCal：需要时可在 jwxt.toml 里补上 term.start_date。")
    else:
        try:
            config.term.start_date = date.fromisoformat(raw_date)
        except ValueError:
            config.term.start_date = suggest_start_date()
            print(f"日期格式无法识别，暂时使用 {config.term.start_date.isoformat()}")

    print("\n第 3 步 / 共 3 步：保存配置")
    if is_new:
        should_save = _yes_no(f"保存到 {p}？（不含密码，权限设为 600）", default=True)
    else:
        should_save = _yes_no(
            f"是否用本次填写的内容覆盖更新 {p}？（自定义注释/高级配置可能丢失）",
            default=False,
        )
    if should_save:
        try:
            write_config(config, p)
        except OSError as exc:
            print(f"保存失败：{exc}\n本次仅使用内存中的设置。")
        else:
            print(f"已保存：{p}")
    else:
        print("未写入配置文件，本次仅使用内存中的设置。")
    return config, p


def run_interactive() -> int:
    print("=" * 58)
    print("  教务课表获取向导")
    print("  直接回答几个问题即可抓取课表，密码不会保存")
    print("=" * 58)
    try:
        config, path = build_config_interactive()
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130

    json_path = "schedule.json"
    csv_path = "schedule.csv"
    ics_path = "schedule.ics" if config.term.start_date else ""

    print("\n即将开始抓取：")
    print(f"  系统：{config.system.base_url}")
    print(f"  账号：{config.account.username}")
    print(f"  学期：{config.term.name}")
    print(f"  输出：{json_path}" + (f"、{ics_path}" if ics_path else "") + f"、{csv_path}")
    if not _yes_no("开始？", default=True):
        print("已取消。")
        return 0

    skip_login = False
    password = ""
    if has_saved_session(config):
        print("\n检测到上次登录会话，先尝试直接抓取……")
        skip_login = True
    else:
        password = ask_password()
        if not password:
            cookie = ask_cookie()
            if not cookie:
                print("未提供密码或 Cookie，已退出。")
                return 2
            config.account.cookie_string = cookie
            skip_login = True

    outcome = fetch_with_user_assist(
        config,
        skip_login_initial=skip_login,
        password=password,
        ask_password=ask_password,
        ask_cookie=ask_cookie,
        note=print,
    )
    if not outcome.ok or outcome.schedule is None:
        print()
        print(f"抓取未成功：{outcome.error}")
        print("可运行 `jwxt probe --config jwxt.toml` 查看线索，或检查账号/密码/Cookie。")
        return 2 if outcome.needs_user_action or outcome.is_login_error else 3

    schedule = outcome.schedule
    print("\n抓取成功：" + schedule.summary())
    print()
    print(format_schedule(schedule))
    print()
    result = export_schedule(
        schedule,
        config,
        json_path=json_path,
        ics_path=ics_path,
        csv_path=csv_path,
    )
    for item in result.saved:
        print(f"已保存 {item}")
    for error in result.errors:
        print(f"[导出提示] {error}")
    print("\n完成。以后直接运行 `jwxt fetch` 即可复用配置和登录会话。")
    return 0


def cmd_setup(args) -> int:
    try:
        config, path = build_config_interactive(getattr(args, "path", "jwxt.toml"))
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    if _yes_no("现在顺便抓取一次？", default=True):
        return _run_fetch_with_config(config)
    print(f"配置已就绪：{path}")
    print("运行 `jwxt fetch` 开始抓取。")
    return 0


def _run_fetch_with_config(config: AppConfig) -> int:
    """setup 之后直接抓取的简化路径。"""
    session = has_saved_session(config)
    password = "" if session else ask_password()
    skip_login = session
    if not password and not session:
        cookie = ask_cookie()
        if not cookie:
            return 2
        config.account.cookie_string = cookie
        skip_login = True

    outcome = fetch_with_user_assist(
        config,
        skip_login_initial=skip_login,
        password=password,
        ask_password=ask_password,
        ask_cookie=ask_cookie,
        note=print,
    )
    if outcome.ok and outcome.schedule:
        json_path = "schedule.json"
        csv_path = "schedule.csv"
        ics_path = "schedule.ics" if config.term.start_date else ""
        print()
        print(format_schedule(outcome.schedule))
        result = export_schedule(
            outcome.schedule, config, json_path=json_path, ics_path=ics_path, csv_path=csv_path
        )
        for item in result.saved:
            print(f"已保存 {item}")
        return 0
    print(f"\n抓取失败：{outcome.error}")
    print("可运行 `jwxt fetch` 重试，或 `jwxt probe` 查看系统线索。")
    return 2
