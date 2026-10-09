"""adapter 基类与通用异常。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import AppConfig
from ..http import JWXTClient
from ..models import Schedule


class AdapterError(RuntimeError):
    """适配器通用错误。"""


class LoginError(AdapterError):
    """登录失败。"""


class CaptchaRequired(LoginError):
    """系统要求验证码/滑块/短信验证，需要人工处理。"""


class SSORequired(LoginError):
    """系统使用统一身份认证，需要浏览器扫码/双因子，或手动导入 Cookie。"""


class ParseError(AdapterError):
    """页面/接口结构无法解析。"""


class BaseAdapter(ABC):
    name = "base"

    @classmethod
    def detect(cls, html: str, url: str = "") -> int:
        """返回 0-100 的匹配分数，0 表示不适合。"""
        return 0

    @abstractmethod
    def login(self, client: JWXTClient, config: AppConfig) -> None:
        raise NotImplementedError

    @abstractmethod
    def fetch(self, client: JWXTClient, config: AppConfig) -> Schedule:
        raise NotImplementedError
