"""通用表单登录 + HTML 表格课表解析。

适用于没有验证码、没有前后端分离的简单教务系统。
复杂系统（正方新版、统一身份认证、扫码登录）请使用专用 adapter 或浏览器 Cookie。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import AppConfig
from ..http import JWXTClient, response_text
from ..models import Schedule
from ..tables import ScheduleParseError, parse_html_schedule
from .base import AdapterError, BaseAdapter, CaptchaRequired, LoginError, SSORequired

USER_FIELD_HINTS = re.compile(r"user|name|account|yhm|zh|login|uname|xh|学号|账号|用户名", re.I)
PASSWORD_FIELD_HINTS = re.compile(r"pass|pwd|mm|密码", re.I)
CAPTCHA_FIELD_HINTS = re.compile(r"captcha|verify|checkcode|yzm|验证码|vcode", re.I)
SSO_URL_HINTS = ("cas", "authserver", "oauth", "sso", "idp", "统一身份")
LOGIN_FAIL_HINTS = (
    "用户名或密码错误",
    "密码错误",
    "账号或密码",
    "账号不存在",
    "用户不存在",
    "验证码错误",
    "登录失败",
    "用户名不能为空",
    "密码不能为空",
)


class GenericHtmlAdapter(BaseAdapter):
    name = "generic_html"

    @classmethod
    def detect(cls, html: str, url: str = "") -> int:
        low = html.lower()
        score = 0
        if 'type="password"' in low or "type='password'" in low:
            score += 40
        if re.search(r"用户名|账号|学号|密码", html):
            score += 20
        if "验证码" in html:
            score += 10
        if "统一身份" in url or "authserver" in url.lower() or "cas" in url.lower():
            score -= 20
        return max(score, 0)

    def login(self, client: JWXTClient, config: AppConfig) -> None:
        base = config.ensure_base_url()
        login_url = config.system.login_path or base
        try:
            response = client.get(login_url)
        except Exception as exc:
            raise LoginError(f"无法访问登录页：{exc}") from exc

        final_url = str(response.url)
        if any(h.lower() in final_url.lower() for h in SSO_URL_HINTS):
            raise SSORequired(
                f"页面跳转到了统一身份认证：{final_url}\n"
                "请用浏览器手动登录后，把 Cookie 写入配置 account.cookie_string，或导出 cookies.txt 后使用 --cookie-file。"
            )

        html = response_text(response)
        soup = BeautifulSoup(html, "lxml")
        form, password_input = self._find_login_form(soup)
        if form is None or password_input is None:
            if any(h in html for h in LOGIN_FAIL_HINTS):
                raise LoginError("当前页面看起来是登录失败提示")
            raise SSORequired(
                "没有找到包含密码框的登录表单，可能是 JavaScript 渲染或统一身份认证。"
                "请在浏览器登录后用 --cookie-string / --cookie-file 导入会话。"
            )

        if self._has_captcha(soup, form):
            raise CaptchaRequired(
                "登录页包含验证码/验证字段。为遵守使用边界，工具不自动绕过验证码；"
                "请手动登录后通过 --cookie-string / --cookie-file 导入会话。"
            )

        action = form.get("action") or final_url
        action_url = urljoin(final_url, action)
        data = self._collect_form_data(form, password_input, config)
        headers = {"Referer": final_url, "Origin": self._origin(final_url)}
        result = client.post(action_url, data=data, headers=headers)
        result_html = response_text(result)
        result_url = str(result.url)
        if any(h in result_html for h in LOGIN_FAIL_HINTS):
            raise LoginError(f"登录失败：{result_url}")
        if "password" in result_html.lower() and "登录" in result_html and "login" in result_url.lower():
            raise LoginError("登录后仍停留在登录页，可能是账号密码错误或需要验证码")
        if not client.cookies_as_string():
            raise LoginError("登录请求成功，但没有拿到会话 Cookie，请检查登录字段映射")

    def fetch(self, client: JWXTClient, config: AppConfig) -> Schedule:
        url = config.system.schedule_path
        if not url:
            raise AdapterError(
                "当前是通用 HTML 系统，但还没有配置课表页地址（system.schedule_path）。"
                "可以：在 jwxt.toml 里填写课表页面路径；或把课表页另存为 HTML 后用 `jwxt parse` 解析。"
            )
        method = str(
            config.system.schedule_method
            or config.extras.get("schedule_method", "GET")
            or "GET"
        ).upper()
        post_data = config.system.schedule_data or config.extras.get("schedule_data") or None
        if method == "POST":
            response = client.post(url, data=post_data)
        else:
            response = client.get(url)
        text = response_text(response)
        if BeautifulSoup(text, "lxml").find("input", attrs={"type": "password"}):
            raise LoginError("课表页面要求重新登录：Cookie 可能已过期，或登录未成功")
        try:
            schedule = parse_html_schedule(text, term=config.term.name, source=self.name)
        except ScheduleParseError as exc:
            raise LoginError(f"课表页面解析失败：{exc}") from exc
        schedule.student = config.account.username
        return schedule

    # ---------- 内部工具 ----------
    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""

    @staticmethod
    def _find_login_form(soup: BeautifulSoup):
        for form in soup.find_all("form"):
            password = form.find("input", attrs={"type": "password"})
            if password is not None:
                return form, password
        return None, None

    @staticmethod
    def _has_captcha(soup: BeautifulSoup, form) -> bool:
        scope = form.find_all("input") if form else soup.find_all("input")
        for inp in scope:
            name = " ".join(
                str(inp.get(attr, "")) for attr in ("name", "id", "placeholder", "class")
            )
            if CAPTCHA_FIELD_HINTS.search(name):
                return True
        # 图片验证码
        for img in soup.find_all("img"):
            src = str(img.get("src", ""))
            if re.search(r"captcha|verify|code|yzm", src, re.I):
                return True
        return False

    def _collect_form_data(self, form, password_input, config: AppConfig) -> dict[str, str]:
        data: dict[str, str] = {}
        username_input = None
        for inp in form.find_all("input"):
            if inp is password_input:
                continue
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype in ("submit", "button", "reset", "image", "checkbox", "radio", "file"):
                if itype == "checkbox" and inp.has_attr("checked"):
                    data[name] = inp.get("value", "on")
                continue
            data[name] = str(inp.get("value", ""))
            if itype in ("text", "email", "tel") and username_input is None:
                if USER_FIELD_HINTS.search(" ".join([name, str(inp.get("id", "")), str(inp.get("placeholder", ""))])):
                    username_input = name
        if username_input is None:
            for inp in form.find_all("input", attrs={"type": ["text", "email", "tel"]}):
                if inp.get("name"):
                    username_input = inp["name"]
                    break
        if not username_input:
            raise LoginError("没有识别出用户名字段，请配置 system.extra_login_fields 或在 issue 中反馈")
        if password_input.get("name") is None:
            raise LoginError("密码输入框没有 name 属性，无法提交")
        username = config.account.username or data.get(username_input, "")
        password = config.resolve_password()
        if not username or not password:
            raise LoginError("缺少账号或密码：请设置 account.username，并通过环境变量提供密码")
        data[username_input] = username
        data[password_input["name"]] = password
        data.update(config.system.extra_login_fields)
        return data
