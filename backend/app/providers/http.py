import time
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from app.providers.base import ProviderError


def trust_environment_for_provider_url(base_url: str) -> bool:
    """Use configured proxies for remote providers, never for loopback endpoints.

    A local OpenAI-compatible server is a supported development/runtime option.  Some
    desktop environments set an all-traffic proxy without a correct ``NO_PROXY``
    value, which would otherwise route ``127.0.0.1`` through that proxy and make a
    healthy local provider appear unavailable.
    """

    hostname = urlsplit(base_url).hostname
    if not hostname:
        return True
    if hostname.casefold() == "localhost":
        return False
    try:
        return not ip_address(hostname).is_loopback
    except ValueError:
        return True


def provider_error_from_response(response: httpx.Response) -> ProviderError:
    status_code = response.status_code
    if status_code in {401, 403}:
        return ProviderError(
            code="provider_authentication_failed",
            message="模型服务认证失败，请检查密钥与地址",
            status_code=status_code,
        )
    if status_code == 429:
        return ProviderError(
            code="provider_rate_limited",
            message="模型服务请求过于频繁，请稍后重试",
            status_code=status_code,
            retryable=True,
        )
    return ProviderError(
        code="provider_unavailable",
        message="模型服务暂时不可用",
        status_code=status_code,
        retryable=status_code >= 500,
    )


def provider_transport_error(exc: httpx.HTTPError) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            code="provider_timeout",
            message="模型服务响应超时",
            retryable=True,
        )
    return ProviderError(
        code="provider_connection_failed",
        message="无法连接模型服务",
        retryable=True,
    )


def elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1_000))
