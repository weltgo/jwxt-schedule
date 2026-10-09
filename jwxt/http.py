"""带会话持久化、限速和基础 Cookie 管理的 HTTP 客户端。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .config import AppConfig


class JWXTClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.http_config = config.http
        headers = {
            "User-Agent": self.http_config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        client_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self.http_config.timeout,
            "verify": self.http_config.verify,
            "follow_redirects": True,
            "trust_env": self.http_config.trust_env,
        }
        if self.http_config.proxy:
            client_kwargs["proxy"] = self.http_config.proxy
        try:
            self.client = httpx.Client(**client_kwargs)
        except ValueError as exc:
            # 环境变量里有 httpx 不支持的代理（例如未安装 socks extra 时的 socks://）
            if "proxy" not in str(exc).lower():
                raise
            client_kwargs["trust_env"] = False
            self.client = httpx.Client(**client_kwargs)
        self._last_request = 0.0
        self._default_host = urlparse(config.system.base_url).hostname or ""
        self._load_cookies()

    # ---------- 生命周期 ----------
    def close(self) -> None:
        self.save_cookies()
        self.client.close()

    def __enter__(self) -> "JWXTClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------- 请求 ----------
    def _wait(self) -> None:
        interval = self.http_config.min_interval
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < interval:
            time.sleep(interval - elapsed)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._wait()
        full_url = self.resolve(url)
        response = self.client.request(method, full_url, **kwargs)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def resolve(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        base = self.config.system.base_url.rstrip("/") + "/"
        return urljoin(base, url.lstrip("/"))

    # ---------- Cookie ----------
    def set_cookie_string(self, cookie_string: str) -> None:
        for part in re.split(r"[;\n]+", cookie_string):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name, value = name.strip(), value.strip()
            if name:
                # httpx 以 domain=None 写入时，对当前 host 生效；这里保留 host 提示
                self.client.cookies.set(name, value, domain=self._default_host or "")

    def cookies_as_string(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.client.cookies.jar)

    def _session_path(self) -> Path | None:
        raw = self.http_config.session_file
        if not raw:
            return None
        return Path(raw).expanduser()

    def _load_cookies(self) -> None:
        if self.config.account.cookie_string:
            self.set_cookie_string(self.config.account.cookie_string)
        if self.config.account.cookie_file:
            path = Path(self.config.account.cookie_file).expanduser()
            if path.exists():
                text = path.read_text(encoding="utf-8")
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    self.set_cookie_string(text)
                else:
                    if isinstance(payload, list):
                        for item in payload:
                            if isinstance(item, dict) and item.get("name"):
                                self.client.cookies.set(
                                    str(item["name"]),
                                    str(item.get("value", "")),
                                    domain=str(item.get("domain") or self._default_host or ""),
                                    path=str(item.get("path") or "/"),
                                )
                    elif isinstance(payload, dict):
                        self.set_cookie_string("; ".join(f"{k}={v}" for k, v in payload.items()))
        path = self._session_path()
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data:
                    self.client.cookies.set(
                        item.get("name", ""),
                        item.get("value", ""),
                        domain=item.get("domain") or self._default_host or None,
                        path=item.get("path", "/"),
                    )
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    def save_cookies(self) -> None:
        """保存会话；保存失败不应影响主流程（例如只读目录/沙箱环境）。"""
        path = self._session_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.client.cookies.jar
            ]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            path.chmod(0o600)
        except OSError:
            return

    # 兼容自定义 adapter 直接使用底层 client
    @property
    def session(self) -> httpx.Client:
        return self.client


def response_text(response: httpx.Response) -> str:
    """尽量按网页声明的编码解码。"""
    try:
        return response.text
    except UnicodeDecodeError:
        return response.content.decode("utf-8", errors="replace")
