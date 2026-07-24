"""Tavily adapter with a fixed endpoint and bounded response reads."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import httpx

from app.discovery.providers.base import (
    DiscoveryProviderError,
    DiscoveryProviderHealth,
    ExtractedSource,
    ExtractFailure,
    ExtractRequest,
    ExtractResponse,
    SearchProvider,
    SearchProviderCapabilities,
    SearchQuery,
    SearchResult,
)

TAVILY_BASE_URL = "https://api.tavily.com"


class TavilySearchProvider(SearchProvider):
    """A server-configured Tavily Search and Extract adapter.

    The adapter intentionally has no configurable base URL.  That prevents a discovery
    connector from being repurposed as a general-purpose HTTP proxy.
    """

    capabilities = SearchProviderCapabilities(
        supports_domain_filters=True,
        supports_extract=True,
        safe_extract=True,
    )

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 1_048_576,
        max_source_characters: int = 16_384,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes < 1_024:
            raise ValueError("max_response_bytes must be at least 1024")
        if max_source_characters < 1:
            raise ValueError("max_source_characters must be positive")
        self._api_key = api_key
        self._max_response_bytes = max_response_bytes
        self._max_source_characters = max_source_characters
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=TAVILY_BASE_URL,
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        if not query.query.strip():
            raise ValueError("query must not be empty")
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query.query.strip(),
            "max_results": query.max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        if query.include_domains:
            payload["include_domains"] = list(query.include_domains)
        if query.exclude_domains:
            payload["exclude_domains"] = list(query.exclude_domains)
        if query.country:
            payload["country"] = query.country

        body = await self._post_json("/search", payload)
        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            raise DiscoveryProviderError(
                code="discovery_connector_response_invalid",
                message="搜索服务返回了无法解析的结果",
            )

        results: list[SearchResult] = []
        for item in raw_results[: query.max_results]:
            if not isinstance(item, Mapping):
                continue
            url = _text(item.get("url"))
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=_text(item.get("title")) or url,
                    content=_truncate(_text(item.get("content")), self._max_source_characters),
                    score=_number_or_none(item.get("score")),
                )
            )
        return tuple(results)

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        if not request.urls:
            return ExtractResponse()
        payload = {
            "api_key": self._api_key,
            "urls": list(request.urls),
            "extract_depth": "basic",
            "include_images": False,
        }
        body = await self._post_json("/extract", payload)
        raw_results = body.get("results", [])
        raw_failures = body.get("failed_results", [])
        if not isinstance(raw_results, list) or not isinstance(raw_failures, list):
            raise DiscoveryProviderError(
                code="discovery_connector_response_invalid",
                message="链接提取服务返回了无法解析的结果",
            )

        sources: list[ExtractedSource] = []
        for item in raw_results:
            if not isinstance(item, Mapping):
                continue
            url = _text(item.get("url"))
            content = _text(item.get("raw_content")) or _text(item.get("content"))
            if not url or not content:
                continue
            canonical_url = _text(item.get("canonical_url")) or url
            sources.append(
                ExtractedSource(
                    url=url,
                    canonical_url=canonical_url,
                    title=_text(item.get("title")) or canonical_url,
                    content=_truncate(content, self._max_source_characters),
                )
            )

        failures: list[ExtractFailure] = []
        for item in raw_failures:
            if not isinstance(item, Mapping):
                continue
            url = _text(item.get("url"))
            if url:
                failures.append(ExtractFailure(url=url))
        return ExtractResponse(sources=tuple(sources), failures=tuple(failures))

    async def health_check(self) -> DiscoveryProviderHealth:
        started = time.perf_counter()
        try:
            await self.search(SearchQuery(query="interview preparation", max_results=1))
        except DiscoveryProviderError as exc:
            return DiscoveryProviderHealth(
                status="degraded",
                latency_ms=_elapsed_ms(started),
                error_code=exc.code,
            )
        return DiscoveryProviderHealth(status="healthy", latency_ms=_elapsed_ms(started))

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client.stream("POST", path, json=payload) as response:
                if response.status_code >= 400:
                    raise _response_error(response.status_code)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_response_bytes:
                        raise DiscoveryProviderError(
                            code="discovery_connector_response_too_large",
                            message="搜索服务返回内容过大",
                        )
        except DiscoveryProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise DiscoveryProviderError(
                code="discovery_connector_timeout",
                message="搜索服务响应超时",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise DiscoveryProviderError(
                code="discovery_connector_unavailable",
                message="暂时无法连接搜索服务",
                retryable=True,
            ) from exc

        try:
            decoded = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DiscoveryProviderError(
                code="discovery_connector_response_invalid",
                message="搜索服务返回了无法解析的结果",
            ) from exc
        if not isinstance(decoded, dict):
            raise DiscoveryProviderError(
                code="discovery_connector_response_invalid",
                message="搜索服务返回了无法解析的结果",
            )
        return decoded


def _response_error(status_code: int) -> DiscoveryProviderError:
    if status_code in {401, 403}:
        return DiscoveryProviderError(
            code="discovery_connector_authentication_failed",
            message="搜索服务认证失败，请检查连接器密钥",
            status_code=status_code,
        )
    if status_code == 429:
        return DiscoveryProviderError(
            code="discovery_connector_rate_limited",
            message="搜索服务请求过于频繁，请稍后重试",
            retryable=True,
            status_code=status_code,
        )
    return DiscoveryProviderError(
        code="discovery_connector_unavailable",
        message="搜索服务暂时不可用",
        retryable=status_code >= 500,
        status_code=status_code,
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _truncate(value: str, limit: int) -> str:
    return value[:limit]


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000))
