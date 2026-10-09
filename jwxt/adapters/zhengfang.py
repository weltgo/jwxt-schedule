"""正方教务系统（jwglxt）适配器。

覆盖常见的“新版正方”结构：
  1. 抓登录页，读取 pwdDefaultEncryptSalt / csrftoken；
  2. 密码优先按 salt 做 AES 加密，否则尝试 login_getPublicKey 的 RSA；
  3. 登录后请求 xskbcx_cxXsKb.html 获取 kbList JSON；
  4. 同时兼容课表接口返回 HTML 表格的旧版本。

各校正方版本差异较大（统一身份认证、验证码、二次加密、字段改名等）。
如果登录/取数不成功，建议抓一次浏览器请求，然后按实际接口调整
system.login_path、system.schedule_path 和 [jwxt] 下的扩展项。
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import AppConfig
from ..crypto import CryptoUnavailableError, aes_encrypt_password, rsa_encrypt_password
from ..http import JWXTClient, response_text
from ..models import Course, Schedule
from ..parsing import parse_day, parse_periods, parse_weeks
from ..tables import ScheduleParseError, parse_html_schedule
from .base import BaseAdapter, CaptchaRequired, LoginError, ParseError, SSORequired

LOGIN_FAIL_HINTS = (
    "用户名或密码错误",
    "密码错误",
    "验证码错误",
    "用户不存在",
    "账号不存在",
    "登录失败",
    "用户名不能为空",
    "密码不能为空",
)
SSO_URL_HINTS = ("cas", "authserver", "oauth", "sso", "idp", "iap", "统一身份")
CAPTCHA_FIELD_RE = re.compile(r"captcha|verify|checkcode|yzm|vcode|验证码", re.I)
JCS_RE = re.compile(r"(\d{1,2})\s*[-~—–至到]\s*(\d{1,2})")


class ZhengfangAdapter(BaseAdapter):
    name = "zhengfang"

    @classmethod
    def detect(cls, html: str, url: str = "") -> int:
        score = 0
        low_url = url.lower()
        low = html.lower()
        if "jwglxt" in low_url:
            score += 50
        if "正方" in html or "zhengfang" in low:
            score += 30
        if "jwglxt" in low or "xtgl/login" in low:
            score += 20
        if "pwddefaultencryptsalt" in low or "login_getpublickey" in low:
            score += 20
        return min(score, 100)

    # ---------- 登录 ----------
    def login(self, client: JWXTClient, config: AppConfig) -> None:
        login_url = self._login_url(config)
        try:
            response = client.get(login_url)
        except Exception as exc:
            raise LoginError(f"无法访问正方登录页 {login_url}：{exc}") from exc

        final_url = str(response.url)
        if any(h in final_url.lower() for h in SSO_URL_HINTS):
            raise SSORequired(
                f"正方已跳转到统一身份认证：{final_url}\n"
                "请手动登录后导出 Cookie 使用：--cookie-string 或 account.cookie_file。"
            )

        html = response_text(response)
        soup = BeautifulSoup(html, "lxml")
        form = soup.find("form")
        password_input = soup.find("input", attrs={"type": "password"})
        username_input = (
            soup.find("input", id="yhm")
            or soup.find("input", attrs={"name": "yhm"})
            or soup.find("input", attrs={"type": ["text", "tel", "email"]})
        )
        if form is None or password_input is None or username_input is None:
            if "用户名或密码错误" in html or "登录失败" in html:
                raise LoginError("正方登录页返回了失败提示")
            raise LoginError("未在正方登录页找到用户名/密码表单，页面结构可能已变化")

        if self._find_captcha(soup):
            raise CaptchaRequired(
                "正方登录页包含验证码。工具不自动识别验证码；"
                "请浏览器登录后用 --cookie-string / --cookie-file 导入会话。"
            )

        hidden_inputs = self._hidden_inputs(form)
        username = config.account.username
        password = config.resolve_password()
        if not username or not password:
            raise LoginError("缺少账号或密码：请设置 account.username，并用环境变量提供密码")

        salt = self._find_salt(html)
        encrypted = ""
        if salt:
            try:
                encrypted = aes_encrypt_password(password, salt)
            except CryptoUnavailableError:
                # 少数学校的 salt 不是 AES 长度，退回 RSA/明文
                encrypted = ""
        if not encrypted:
            encrypted = self._encrypt_by_public_key(client, config, password)

        data = dict(hidden_inputs)
        data[username_input.get("name") or "yhm"] = username
        data[password_input.get("name") or "mm"] = encrypted
        data.setdefault("language", "zh_CN")
        data.update(config.system.extra_login_fields)

        action = form.get("action") or final_url
        post_url = urljoin(final_url, action)
        headers = {"Referer": final_url, "Origin": self._origin(final_url)}
        result = client.post(post_url, data=data, headers=headers)
        result_url = str(result.url)
        result_html = response_text(result)

        if any(h in result_html for h in LOGIN_FAIL_HINTS):
            raise LoginError("正方登录失败：账号密码错误或需要验证码")
        if "login_slogin" in result_url and "index" not in result_url:
            # 有的版本失败后 302 回登录页
            if "用户名或密码错误" not in result_html and "密码错误" not in result_html:
                raise LoginError("正方登录后仍停留在登录页，请检查密码加密方式/额外字段")
        if not client.cookies_as_string():
            raise LoginError("正方登录未获得 Cookie，请检查接口参数")

    def _encrypt_by_public_key(self, client: JWXTClient, config: AppConfig, password: str) -> str:
        url = self._url(config, "xtgl/login_getPublicKey.html", query={"time": int(date.today().strftime("%Y%m%d"))})
        try:
            response = client.get(url)
            payload = response.json()
            modulus = payload.get("modulus") or payload.get("modulusBase64")
            exponent = payload.get("exponent") or payload.get("exponentBase64")
            if modulus and exponent:
                value = rsa_encrypt_password(password, modulus, exponent)
                if str(config.extras.get("zhengfang_rsa_output", "base64")).lower() == "hex":
                    import base64

                    value = base64.b64decode(value).hex()
                return value
        except Exception:
            pass
        # 少数老版本直接提交明文
        return password

    # ---------- 取课表 ----------
    def fetch(self, client: JWXTClient, config: AppConfig) -> Schedule:
        xnm, xqm = self._resolve_term(config)
        index_url = self._url(
            config,
            "kbcx/xskbcx_cxXskbIndex.html",
            query={"gnmkdm": "N2151", "layout": "default"},
        )
        data_url = self._url(config, "kbcx/xskbcx_cxXsKb.html", query={"gnmkdm": "N2151"})
        if config.system.schedule_path:
            data_url = self._absolute(config, config.system.schedule_path)

        # 先访问课表首页，建立必要的会话上下文；失败也不立即中断（个别版本无此页）
        try:
            client.get(index_url)
        except Exception:
            pass

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": index_url,
        }
        payload = {"xnm": xnm, "xqm": xqm, "kzlx": "ck"}
        payload.update(config.extras.get("zhengfang_schedule_data", {}) or {})
        response = client.post(data_url, data=payload, headers=headers)
        text = response_text(response)

        if "login_slogin" in str(response.url) or ("<html" in text.lower() and "yhm" in text and "password" in text):
            raise LoginError("取课表时被跳回登录页：Cookie 可能已过期，或登录未成功")

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                schedule = parse_html_schedule(text, term=config.term.name, source=self.name)
            except ScheduleParseError as exc:
                raise ParseError(
                    "正方课表接口既不是 JSON，也不是可识别的 HTML 表格。"
                    "请把该接口响应保存下来，或配置 system.schedule_path 指向正确接口。"
                ) from exc
            schedule.student = config.account.username
            return schedule

        if not isinstance(data, dict):
            raise ParseError(f"正方接口返回了非预期 JSON 类型：{type(data).__name__}")

        kb_list = data.get("kbList") or data.get("kbListX") or []
        sjk_list = data.get("sjkList") or []
        courses = [self._parse_kb_item(item) for item in [*kb_list, *sjk_list] if isinstance(item, dict)]
        courses = [c for c in courses if c.name]
        if not courses and (data.get("jc") or data.get("message")):
            raise ParseError(f"正方接口返回提示：{data.get('jc') or data.get('message')}")

        xsxx = data.get("xsxx") or {}
        student = self._pick(xsxx, "XM", "xm") or config.account.username
        term_name = config.term.name or self._term_display(xnm, xqm)
        return Schedule(
            term=term_name,
            student=student,
            source=self.name,
            courses=courses,
        )

    # ---------- 字段解析 ----------
    def _parse_kb_item(self, item: dict[str, Any]) -> Course:
        start, end = self._parse_jcs(str(item.get("jcs") or item.get("jcor") or ""))
        weeks_text = str(item.get("zcd") or item.get("zcmc") or "")
        return Course(
            name=str(item.get("kcmc") or item.get("kcbm") or "").strip(),
            teacher=str(item.get("xm") or item.get("jsxm") or "").strip(),
            location=str(item.get("cdmc") or "").strip(),
            day=parse_day(str(item.get("xqjmc") or "")) or self._safe_int(item.get("xqj")),
            start_period=start,
            end_period=end,
            weeks=parse_weeks(weeks_text),
            raw={str(k): v for k, v in item.items()},
        )

    @staticmethod
    def _parse_jcs(text: str) -> tuple[int | None, int | None]:
        m = JCS_RE.search(text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 1 <= a <= b <= 20:
                return a, b
        parsed = parse_periods(text)
        if parsed != (None, None):
            return parsed
        n = re.search(r"\d{1,2}", text)
        if n:
            v = int(n.group(0))
            if 1 <= v <= 20:
                return v, v
        return None, None

    # ---------- 地址 / 学期辅助 ----------
    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""

    def _base_parts(self, config: AppConfig) -> tuple[str, str]:
        base = config.ensure_base_url()
        p = urlparse(base)
        origin = f"{p.scheme}://{p.netloc}"
        path = p.path.rstrip("/")
        if path.endswith("/jwglxt") or path == "/jwglxt":
            prefix = path
        elif "/xtgl/" in path:
            # 兼容用户直接把登录页地址填进 base_url 的情况
            prefix = path.split("/xtgl/", 1)[0]
        elif path.endswith("/login_slogin.html"):
            prefix = path.rsplit("/", 2)[0]
        elif path:
            prefix = path
        else:
            prefix = "/jwglxt"
        return origin, prefix

    def _url(self, config: AppConfig, path: str, query: dict[str, Any] | None = None) -> str:
        origin, prefix = self._base_parts(config)
        url = f"{origin}{prefix}/{path.lstrip('/')}"
        if query:
            from urllib.parse import urlencode

            url += "?" + urlencode(query)
        return url

    def _absolute(self, config: AppConfig, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        origin, prefix = self._base_parts(config)
        if path.startswith("/"):
            return origin + path
        return f"{origin}{prefix}/{path}"

    def _login_url(self, config: AppConfig) -> str:
        if config.system.login_path:
            return self._absolute(config, config.system.login_path)
        return self._url(config, "xtgl/login_slogin.html")

    @staticmethod
    def _hidden_inputs(form) -> dict[str, str]:
        data: dict[str, str] = {}
        for inp in form.find_all("input", attrs={"type": "hidden"}):
            name = inp.get("name")
            if name:
                data[str(name)] = str(inp.get("value", ""))
        return data

    @staticmethod
    def _find_salt(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for inp in soup.find_all("input"):
            ident = " ".join(str(inp.get(attr, "")) for attr in ("id", "name", "class"))
            if "pwdDefaultEncryptSalt" in ident:
                value = inp.get("value")
                if value:
                    return str(value)
        m = re.search(r"pwdDefaultEncryptSalt\s*[=:]\s*[\"']([^\"']+)[\"']", html)
        return m.group(1) if m else ""

    @staticmethod
    def _find_captcha(soup: BeautifulSoup) -> bool:
        for inp in soup.find_all("input"):
            ident = " ".join(
                str(inp.get(attr, "")) for attr in ("id", "name", "placeholder", "class")
            )
            if CAPTCHA_FIELD_RE.search(ident):
                return True
        for img in soup.find_all("img"):
            if CAPTCHA_FIELD_RE.search(str(img.get("src", ""))):
                return True
        return False

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return n if 1 <= n <= 7 else None

    @staticmethod
    def _pick(mapping: dict, *keys: str) -> str:
        for key in keys:
            value = mapping.get(key)
            if value:
                return str(value)
        return ""

    def _resolve_term(self, config: AppConfig) -> tuple[str, str]:
        xnm = str(config.system.xnm or "").strip()
        xqm = str(config.system.xqm or config.extras.get("xqm") or "").strip()
        if not xnm:
            m = re.match(r"(\d{4})", config.term.name or "")
            if m:
                xnm = m.group(1)
        if not xnm:
            today = date.today()
            xnm = str(today.year if today.month >= 8 else today.year - 1)
        if not xqm:
            today = date.today()
            xqm = "3" if today.month >= 8 or today.month <= 1 else "12"
        return xnm, xqm

    @staticmethod
    def _term_display(xnm: str, xqm: str) -> str:
        m = re.match(r"(\d{4})", xnm)
        start_year = m.group(1) if m else xnm
        try:
            end_year = str(int(start_year) + 1)
        except ValueError:
            end_year = ""
        sem = {"3": "1", "12": "2", "16": "3"}.get(xqm, xqm)
        return f"{start_year}-{end_year}-{sem}"


__all__ = ["ZhengfangAdapter"]
