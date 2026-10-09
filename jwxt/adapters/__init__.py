"""adapter 注册与自动识别。"""

from __future__ import annotations

from .base import (
    AdapterError,
    BaseAdapter,
    CaptchaRequired,
    LoginError,
    ParseError,
    SSORequired,
)
from .generic_html import GenericHtmlAdapter
from .zhengfang import ZhengfangAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "zhengfang": ZhengfangAdapter,
    "generic_html": GenericHtmlAdapter,
}

__all__ = [
    "ADAPTERS",
    "AdapterError",
    "BaseAdapter",
    "CaptchaRequired",
    "GenericHtmlAdapter",
    "LoginError",
    "ParseError",
    "SSORequired",
    "ZhengfangAdapter",
    "detect_adapter",
    "get_adapter",
]


def get_adapter(name: str) -> BaseAdapter:
    key = (name or "auto").lower()
    if key not in ADAPTERS:
        raise AdapterError(f"未知 adapter：{name}。可选：{', '.join(ADAPTERS)}")
    return ADAPTERS[key]()


def detect_adapter(client, config) -> BaseAdapter:
    """根据配置和探测结果选择 adapter。"""
    system_type = (config.system.type or "auto").lower()
    if system_type != "auto":
        return get_adapter(system_type)

    base = config.ensure_base_url()
    if "jwglxt" in base.lower():
        return ZhengfangAdapter()

    from ..http import response_text

    try:
        response = client.get(base)
    except Exception as exc:
        raise AdapterError(f"探测 {base} 失败：{exc}") from exc

    final_url = str(response.url)
    if any(h in final_url.lower() for h in ("cas", "authserver", "oauth", "sso", "idp", "统一身份")):
        raise SSORequired(
            f"系统跳转到了统一身份认证：{final_url}\n"
            "请浏览器登录后导出 Cookie，再通过 --cookie-string / --cookie-file 使用。"
        )

    html = response_text(response)
    scored = sorted(
        ((adapter_cls.detect(html, final_url), name, adapter_cls) for name, adapter_cls in ADAPTERS.items()),
        key=lambda x: x[0],
        reverse=True,
    )
    best_score, best_name, best_cls = scored[0]
    if best_score >= 40:
        return best_cls()
    if "jwglxt" in html.lower():
        return ZhengfangAdapter()
    if GenericHtmlAdapter.detect(html, final_url) > 0:
        return GenericHtmlAdapter()
    raise AdapterError(
        "无法自动识别教务系统类型。请运行 `jwxt probe --url ...` 查看页面特征，"
        "然后在配置里把 system.type 设为 zhengfang 或 generic_html，并视情况填写 login_path / schedule_path。"
    )
