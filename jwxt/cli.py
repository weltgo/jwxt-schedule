"""命令行入口。"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .bridge import run_browser_bridge
from .config import SAMPLE_CONFIG, AppConfig, load_config
from .display import format_schedule
from .exporters.csv_out import save_csv
from .exporters.ics import ICSExportError, save_ics
from .http import JWXTClient, response_text
from .models import Schedule
from .tables import ScheduleParseError, parse_html_schedule
from .wizard import ask_cookie, ask_password, cmd_setup, run_interactive
from .workflow import export_schedule, fetch_with_user_assist, has_saved_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jwxt",
        description=(
            "教务系统课表获取器。\n"
            "第一次使用直接运行 `jwxt`（不带参数）进入交互向导；"
            "已有 jwxt.toml 时运行 `jwxt fetch` 即可。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=False)

    # setup：交互式配置
    p_setup = sub.add_parser("setup", help="交互式配置向导（推荐首次使用）")
    p_setup.add_argument("--path", default="jwxt.toml", help="配置文件路径，默认 jwxt.toml")
    p_setup.set_defaults(func=cmd_setup)

    # browser：从设备浏览器读取
    p_browser = sub.add_parser(
        "browser",
        help="从浏览器读取课表（适合当前无法自动识别的教务系统）",
    )
    p_browser.add_argument("--port", type=int, default=8765, help="本机桥接端口，默认 8765")
    p_browser.add_argument("--outdir", default=".", help="导出目录，默认当前目录")
    p_browser.add_argument("--no-open", action="store_true", help="不自动打开浏览器说明页")
    p_browser.add_argument("--no-snapshot", action="store_true", help="不保存原始页面快照")
    p_browser.add_argument("--verbose", action="store_true", help="打印服务端请求日志")
    p_browser.set_defaults(func=cmd_browser)

    # fetch：抓取
    p_fetch = sub.add_parser(
        "fetch",
        help="抓取课表（首次运行且没有配置时会自动进入向导）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fetch.add_argument("url_pos", nargs="?", default="", metavar="URL",
                         help="教务系统地址，例如 https://jw.example.edu.cn")
    _add_common_options(p_fetch, mode="fetch")
    p_fetch.add_argument("--out", default="schedule.json", help="JSON 输出路径")
    p_fetch.add_argument("--ics", default="schedule.ics", help="iCal 输出路径")
    p_fetch.add_argument("--csv", default="schedule.csv", help="CSV 输出路径")
    p_fetch.add_argument("--no-ics", action="store_true", help="不导出 iCal")
    p_fetch.add_argument("--no-csv", action="store_true", help="不导出 CSV")
    p_fetch.add_argument("--no-login", action="store_true", help="跳过登录，仅使用已有 Cookie/会话")
    p_fetch.add_argument("--login", action="store_true", help=argparse.SUPPRESS)
    p_fetch.add_argument("--password-stdin", action="store_true", help=argparse.SUPPRESS)
    p_fetch.add_argument("--print", dest="print_json", action="store_true", help="在终端打印 JSON")
    p_fetch.set_defaults(func=cmd_fetch)

    # parse：本地 HTML
    p_parse = sub.add_parser("parse", help="解析本地保存的课表 HTML，无需登录")
    p_parse.add_argument("input", help="HTML 文件路径")
    p_parse.add_argument("--out", default="schedule.json", help="JSON 输出路径")
    p_parse.add_argument("--ics", default="", help="同时导出 iCal 到该路径")
    p_parse.add_argument("--csv", default="", help="同时导出 CSV 到该路径")
    p_parse.add_argument("--config", default="", help="可选配置文件（用于读取学期/节次设置）")
    p_parse.add_argument("--term-name", default="", help="学期名，例如 2025-2026-1")
    p_parse.add_argument("--start-date", type=_date_arg, default=None, help="第一周周一，YYYY-MM-DD")
    p_parse.add_argument("--weeks-count", type=int, default=None, help="学期周数")
    p_parse.set_defaults(func=cmd_parse)

    # export：格式转换
    p_export = sub.add_parser("export", help="把已保存的 JSON 课表导出为其他格式")
    p_export.add_argument("input", help="schedule JSON 文件")
    p_export.add_argument("--format", choices=["ics", "csv", "json"], default="ics", help="目标格式")
    p_export.add_argument("--out", default="", help="输出路径；默认按格式自动命名")
    p_export.add_argument("--config", default="", help="可选配置文件（导出 iCal 时需要 start_date/节次时间）")
    p_export.add_argument("--start-date", type=_date_arg, default=None, help="第一周周一，YYYY-MM-DD")
    p_export.add_argument("--weeks-count", type=int, default=None, help="学期周数")
    p_export.set_defaults(func=cmd_export)

    return parser


def build_advanced_parser() -> argparse.ArgumentParser:
    """隐藏的高级命令：probe / init。普通用户不需要用到。"""
    parser = argparse.ArgumentParser(prog="jwxt", description="高级工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="探测教务系统类型（不登录，仅用于排查）")
    _add_common_options(p_probe)
    p_probe.set_defaults(func=cmd_probe)

    p_init = sub.add_parser("init", help="生成传统配置模板")
    p_init.add_argument("--path", default="jwxt.toml", help="配置文件输出路径")
    p_init.add_argument("--url", default="", help="教务系统地址，写入模板")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在的配置")
    p_init.set_defaults(func=cmd_init)
    return parser


def _add_common_options(parser: argparse.ArgumentParser, mode: str = "full") -> None:
    """添加公共选项。

    mode="fetch" 时只展示常用选项，其余高级参数仍可用但不出现在 --help 中，
    避免第一次使用的人被大量参数吓到。
    """
    show_all = mode != "fetch"

    def help_text(text: str) -> str:
        return text if show_all else argparse.SUPPRESS

    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--url", default="", help=help_text("教务系统地址（也可以直接用位置参数 URL）"))
    parser.add_argument("--username", default="", help="学号/用户名")
    parser.add_argument("--cookie-string", default="", help="手动提供 Cookie，如 'JSESSIONID=xxx'")
    parser.add_argument("--cookie-file", default="", help="Cookie 文件（name=value 或 JSON）")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整堆栈")

    # 以下为高级选项：fetch 帮助里隐藏，但依然可以正常传参
    parser.add_argument("--type", default="", help=help_text("适配器：auto/zhengfang/generic_html"))
    parser.add_argument("--password-env", default=None, help=help_text("保存密码的环境变量名"))
    parser.add_argument("--session-file", default=None, help=help_text("Cookie 会话缓存路径"))
    parser.add_argument("--login-path", default=None, help=help_text("登录页路径/URL（定制系统）"))
    parser.add_argument("--schedule-path", default=None, help=help_text("课表页或接口路径/URL（定制系统）"))
    parser.add_argument("--term-name", default=None, help=help_text("学期名，例如 2025-2026-1"))
    parser.add_argument("--start-date", type=_date_arg, default=None, help=help_text("第一周周一，YYYY-MM-DD"))
    parser.add_argument("--weeks-count", type=int, default=None, help=help_text("学期周数"))
    parser.add_argument("--xnm", default=None, help=help_text("正方学年，例如 2025"))
    parser.add_argument("--xqm", default=None, help=help_text("正方学期代码：3=第一学期，12=第二学期"))
    parser.add_argument("--min-interval", type=float, default=None, help=help_text("请求间隔秒数，默认 1.2"))
    parser.add_argument("--timeout", type=float, default=None, help=help_text("HTTP 超时秒数"))
    parser.add_argument("--insecure", action="store_true", help=help_text("跳过 TLS 证书校验（不推荐）"))
    parser.add_argument("--proxy", default=None, help=help_text("HTTP 代理，例如 http://127.0.0.1:7890"))
    parser.add_argument("--no-env-proxy", action="store_true", help=help_text("忽略环境变量里的代理设置"))


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{value}") from exc


def _make_config(args) -> AppConfig:
    url = getattr(args, "url_pos", "") or getattr(args, "url", "")
    overrides = {
        "system.type": getattr(args, "type", "") or None,
        "system.base_url": url or None,
        "system.login_path": getattr(args, "login_path", None),
        "system.schedule_path": getattr(args, "schedule_path", None),
        "system.xnm": getattr(args, "xnm", None),
        "system.xqm": getattr(args, "xqm", None),
        "account.username": getattr(args, "username", "") or None,
        "account.password_env": getattr(args, "password_env", None),
        "account.cookie_string": getattr(args, "cookie_string", "") or None,
        "account.cookie_file": getattr(args, "cookie_file", "") or None,
        "http.session_file": getattr(args, "session_file", None),
        "http.min_interval": getattr(args, "min_interval", None),
        "http.timeout": getattr(args, "timeout", None),
        "http.proxy": getattr(args, "proxy", None),
        "term.name": getattr(args, "term_name", None),
        "term.start_date": getattr(args, "start_date", None),
        "term.weeks_count": getattr(args, "weeks_count", None),
    }
    if getattr(args, "insecure", False):
        overrides["http.verify"] = False
    if getattr(args, "no_env_proxy", False):
        overrides["http.trust_env"] = False
    config = load_config(getattr(args, "config", "") or None, **overrides)
    config.system.type = config.system.type or "auto"
    return config


# ---------------- commands ----------------
def cmd_init(args) -> int:
    path = Path(args.path).expanduser()
    if path.exists() and not args.force:
        print(f"配置文件已存在：{path}（使用 --force 覆盖）", file=sys.stderr)
        return 1
    text = SAMPLE_CONFIG
    if args.url:
        text = text.replace('base_url = "https://jw.example.edu.cn"', f'base_url = "{args.url.rstrip("/")}"')
    path.write_text(text, encoding="utf-8")
    print(f"已生成配置模板：{path}")
    print("下一步：填写学号；导出密码到环境变量，例如 export JWXT_PASSWORD='...'")
    print(f"然后运行：jwxt probe --config {path}")
    return 0


def cmd_probe(args) -> int:
    config = _make_config(args)
    try:
        with JWXTClient(config) as client:
            response = client.get(config.ensure_base_url())
            final_url = str(response.url)
            html = response_text(response)
    except Exception as exc:
        print(f"探测失败：{exc}", file=sys.stderr)
        if args.debug:
            raise
        return 2

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    from .adapters import ADAPTERS

    scores = sorted(
        ((cls.detect(html, final_url), name) for name, cls in ADAPTERS.items()),
        reverse=True,
    )
    labels = {"zhengfang": "正方教务系统", "generic_html": "通用表单+HTML 表格系统"}
    sso = any(h in final_url.lower() for h in ("cas", "authserver", "oauth", "sso", "idp", "统一身份"))
    has_captcha = False
    import re as _re

    for inp in soup.find_all("input"):
        ident = " ".join(str(inp.get(a, "")) for a in ("id", "name", "placeholder", "class"))
        if _re.search(r"captcha|verify|checkcode|yzm|vcode|验证码", ident, _re.I):
            has_captcha = True
            break
    print("== 探测结果 ==")
    print(f"最终地址: {final_url}")
    print(f"页面标题: {title or '(空)'}")
    print(f"页面大小: {len(html)} 字符")
    if sso:
        print("⚠ 当前地址看起来是统一身份认证/单点登录页面，登录请使用浏览器 Cookie 导入方式。")
    other_hints = []
    low_html = html.lower()
    low_url = final_url.lower()
    if "强智" in html or any(x in low_url for x in ("qgjw", "qzkq", "qzdataservice")):
        other_hints.append("强智教务（暂未内置专用 adapter）")
    if "urp" in low_html or "urp" in low_url:
        other_hints.append("URP 教务（暂未内置专用 adapter）")
    if other_hints:
        print("其他线索: " + "；".join(other_hints))
    print("特征:")
    print(f"  - 包含 jwglxt: {'jwglxt' in html.lower() or 'jwglxt' in final_url.lower()}")
    print(f"  - 包含密码框: {'type=\"password\"' in html.lower()}")
    print(f"  - 包含验证码: {has_captcha}")
    print("适配器评分:")
    for score, name in scores:
        print(f"  - {labels.get(name, name)} ({name}): {score}")
    best_score, best_name = scores[0]
    if best_score >= 40:
        print(f"建议 system.type = \"{best_name}\"")
    elif "jwglxt" in final_url.lower() or "jwglxt" in html.lower():
        print('建议 system.type = "zhengfang"')
    else:
        print('无法确定类型。可在配置里先写 system.type = "generic_html"，')
        print("并用浏览器开发者工具找到课表页地址填入 system.schedule_path。")
    print("\n下一步:")
    print(f"  1. 运行 `jwxt fetch --config <配置>` 尝试抓取")
    print(f"  2. 若需要验证码/统一身份认证，请在浏览器登录后用 --cookie-string 或 --cookie-file 导入会话")
    return 0


def cmd_browser(args) -> int:
    return run_browser_bridge(
        port=getattr(args, "port", 8765),
        outdir=Path(getattr(args, "outdir", ".")),
        open_browser=not getattr(args, "no_open", False),
        save_snapshots=not getattr(args, "no_snapshot", False),
        verbose=getattr(args, "verbose", False),
    )


def _find_default_config() -> str:
    candidates = [
        Path("jwxt.toml"),
        Path.home() / ".config" / "jwxt" / "config.toml",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return ""


def cmd_fetch(args) -> int:
    # 没有 --config 时自动找当前目录/用户目录的配置；找不到就进入向导
    if not getattr(args, "config", ""):
        found = _find_default_config()
        if found:
            args.config = found
            print(f"使用配置：{found}", file=sys.stderr)
        elif not (getattr(args, "url_pos", "") or getattr(args, "url", "")) and sys.stdin.isatty():
            print("没有找到 jwxt.toml，进入交互向导……\n")
            return run_interactive()

    try:
        config = _make_config(args)
    except Exception as exc:
        print(f"配置读取失败：{exc}", file=sys.stderr)
        return 1

    try:
        config.ensure_base_url()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        print("运行 `jwxt setup` 可以先完成配置。", file=sys.stderr)
        return 1

    if not config.account.username:
        if sys.stdin.isatty():
            config.account.username = input("学号 / 用户名: ").strip()
        if not config.account.username:
            print("缺少学号/用户名：请加 --username，或在 jwxt.toml 中填写。", file=sys.stderr)
            return 1

    json_path = getattr(args, "out", "schedule.json") or ""
    ics_path = "" if getattr(args, "no_ics", False) else getattr(args, "ics", "")
    csv_path = "" if getattr(args, "no_csv", False) else getattr(args, "csv", "")

    session_available = has_saved_session(config)
    skip_login = getattr(args, "no_login", False) or (
        session_available and not getattr(args, "login", False)
    )
    password = ""
    if getattr(args, "password_stdin", False):
        password = sys.stdin.read().strip()
    elif not skip_login and not config.resolve_password() and sys.stdin.isatty():
        password = ask_password()

    outcome = fetch_with_user_assist(
        config,
        skip_login_initial=skip_login,
        password=password,
        ask_password=ask_password if sys.stdin.isatty() else None,
        ask_cookie=ask_cookie if sys.stdin.isatty() else None,
        note=lambda text: print(text, file=sys.stderr),
    )
    if not outcome.ok or outcome.schedule is None:
        print(f"\n抓取失败：{outcome.error}", file=sys.stderr)
        if getattr(args, "debug", False) and outcome.error is not None:
            raise outcome.error
        return 2 if (outcome.needs_user_action or outcome.is_login_error) else 3

    schedule = outcome.schedule
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
        print(f"[iCal 未导出] {error}", file=sys.stderr)
    if getattr(args, "print_json", False):
        print(schedule.to_json())
    print()
    print(format_schedule(schedule))
    return 0


def _export_extras(schedule: Schedule, config: AppConfig, args) -> None:
    if getattr(args, "csv", ""):
        save_csv(schedule, args.csv)
        print(f"已保存 CSV：{args.csv}")
    if getattr(args, "ics", ""):
        try:
            save_ics(schedule, config, args.ics)
            print(f"已保存 iCal：{args.ics}")
        except ICSExportError as exc:
            print(f"[iCal 未导出] {exc}", file=sys.stderr)


def cmd_parse(args) -> int:
    path = Path(args.input).expanduser()
    if not path.exists():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 1
    config = load_config(args.config or None) if args.config else AppConfig()
    if args.term_name:
        config.term.name = args.term_name
    if args.start_date:
        config.term.start_date = args.start_date
    if args.weeks_count:
        config.term.weeks_count = args.weeks_count
    try:
        schedule = parse_html_schedule(
            path.read_text(encoding="utf-8", errors="replace"),
            term=config.term.name,
            source=f"file:{path.name}",
        )
    except ScheduleParseError as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return 2
    schedule.save(args.out)
    print(f"解析到 {len(schedule.courses)} 条安排，已保存 JSON：{args.out}")
    _export_extras(schedule, config, args)
    return 0


def cmd_export(args) -> int:
    try:
        schedule = Schedule.load(args.input)
    except Exception as exc:
        print(f"读取 JSON 失败：{exc}", file=sys.stderr)
        return 1
    config = load_config(args.config or None) if args.config else AppConfig()
    if args.start_date:
        config.term.start_date = args.start_date
    if args.weeks_count:
        config.term.weeks_count = args.weeks_count

    if args.format == "json":
        out = args.out or str(Path(args.input).with_suffix(".json"))
        schedule.save(out)
    elif args.format == "csv":
        out = args.out or str(Path(args.input).with_suffix(".csv"))
        save_csv(schedule, out)
    else:
        out = args.out or str(Path(args.input).with_suffix(".ics"))
        try:
            save_ics(schedule, config, out)
        except ICSExportError as exc:
            print(f"导出 iCal 失败：{exc}", file=sys.stderr)
            return 2
    print(f"已导出：{out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_advanced_parser() if raw and raw[0] in {"init", "probe"} else build_parser()
    args = parser.parse_args(raw)
    if getattr(args, "command", None) is None:
        if not sys.stdin.isatty():
            print("检测到当前不是交互终端，无法启动问答向导。")
            print("可以改用：")
            print("  jwxt fetch https://你的教务系统地址 --username 你的学号")
            print("  jwxt setup --path jwxt.toml")
            print("  或用 `--no-login` / `--cookie-file` 配合已有会话。")
            return 1
        return run_interactive()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
