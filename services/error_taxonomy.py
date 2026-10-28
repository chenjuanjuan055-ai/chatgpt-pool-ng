"""Error taxonomy for upstream / pool failures.

Pure helpers so they can be unit tested and reused by metrics and alerts.
"""

from __future__ import annotations

AUTH = "auth"
RATE_LIMIT = "rate_limit"
RISK_CONTROL = "risk_control"
UPSTREAM = "upstream"
NETWORK = "network"
CLIENT = "client"
POOL = "pool"
UNKNOWN = "unknown"

LABELS = {
    AUTH: "凭证失效",
    RATE_LIMIT: "限流",
    RISK_CONTROL: "风控拦截",
    UPSTREAM: "上游异常",
    NETWORK: "网络/代理",
    CLIENT: "请求参数",
    POOL: "号池无可用账号",
    UNKNOWN: "未知",
}

HINTS = {
    AUTH: "账号 token 失效或被踢，需要重新登录或刷新",
    RATE_LIMIT: "触发上游频率/额度限制，冷却或换号",
    RISK_CONTROL: "上游风控（WAF/异常行为），降低频率或换出口",
    UPSTREAM: "上游服务端错误，稍后重试",
    NETWORK: "连接/代理失败，检查出口池与网络",
    CLIENT: "请求参数不合法，检查入参",
    POOL: "号池里没有可用账号承接该模型，检查账号健康与模型映射",
    UNKNOWN: "未分类错误，查看原文",
}

_RISK_MARKERS = (
    "request illegal",
    "11140",
    "unusual activity",
    "账户存在异常",
    "账号存在异常",
    "risk",
    "captcha",
    "arkose",
    "sentinel",
    "waf",
    "cloudflare",
    "just a moment",
)
_RATE_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "usage limit",
    "usage exceeds",
    "frequency limit",
    "6004",
    "限流",
    "频率限制",
)
_AUTH_MARKERS = (
    "401",
    "403",
    "invalidated",
    "unauthorized",
    "forbidden",
    "token_expired",
    "invalid_grant",
    "session has ended",
    "access_token",
    "refresh_token",
    "credentials",
    "登录",
)
_NETWORK_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "tls",
    "ssl",
    "proxy",
    "econnrefused",
    "econnreset",
    "curl: (",
    "dns",
    "unreachable",
    "network",
)
_POOL_MARKERS = (
    "not available to any active account",
    "no available account",
    "no_available_account",
    "no healthy account",
    "no_healthy_account",
    "无可用账号",
)
_CLIENT_MARKERS = (
    "400",
    "422",
    "invalid request",
    "validation",
    "missing",
    "required",
    "参数",
)


def classify(error_text: str = "", *, status_code: int | None = None, status: str = "") -> str:
    """Return an error category code."""
    text = str(error_text or "").lower()
    if not text and status != "failed" and status_code is None:
        return ""
    if status_code is None:
        status_code = _guess_status(text)

    for marker in _RISK_MARKERS:
        if marker in text:
            return RISK_CONTROL
    for marker in _RATE_MARKERS:
        if marker in text:
            return RATE_LIMIT
    for marker in _NETWORK_MARKERS:
        if marker in text:
            return NETWORK

    for marker in _POOL_MARKERS:
        if marker in text:
            return POOL

    if status_code in (401, 403):
        return AUTH
    if status_code == 429:
        return RATE_LIMIT
    if status_code is not None and 500 <= status_code <= 599:
        return UPSTREAM
    if status_code is not None and 400 <= status_code <= 499:
        return CLIENT

    for marker in _AUTH_MARKERS:
        if marker in text:
            return AUTH
    for marker in _CLIENT_MARKERS:
        if marker in text:
            return CLIENT
    if status == "failed" and not text:
        return UNKNOWN
    return UNKNOWN if text else ""


def _guess_status(text: str) -> int | None:
    import re

    match = re.search(r"\b([45]\d{2})\b", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def describe(category: str) -> dict[str, str]:
    if not category:
        return {"category": "", "label": "", "hint": ""}
    return {"category": category, "label": LABELS.get(category, category), "hint": HINTS.get(category, "")}
