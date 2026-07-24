"""Firecrawl v2 adapter with fixed endpoints and bounded response reads.

The adapter deliberately exposes only Firecrawl's public search and single-page
scrape operations.  Connector credentials are sent in the bearer header, and
callers cannot provide a base URL, proxy, arbitrary headers, or browser actions.
"""

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

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v2"


class FirecrawlSearchProvider(SearchProvider):
    """A fixed-endpoint Firecrawl v2 Search and Scrape adapter.

    Firecrawl's v2 ``/scrape`` endpoint accepts one URL per request.  The
    provider-neutral ``ExtractRequest`` is therefore fulfilled sequentially so
    one failed public page can be returned as a bounded per-source failure
    without bypassing the surrounding discovery policy.
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
            base_url=FIRECRAWL_BASE_URL,
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        if not query.query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= query.max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")

        payload: dict[str, Any] = {
            "query": query.query.strip(),
            "limit": query.max_results,
            "sources": [{"type": "web"}],
            # Search remains metadata-only.  The policy-checked URLs are then
            # scraped through ``extract`` instead of spending scrape credits for
            # results that the application will reject.
            "ignoreInvalidURLs": True,
            "highlights": False,
        }
        # Firecrawl v2 declares these filters mutually exclusive.  The worker
        # still validates every result against the complete allow/deny policy,
        # so preferring the allow-list cannot turn a denied URL into a source.
        if query.include_domains:
            payload["includeDomains"] = list(query.include_domains)
        elif query.exclude_domains:
            payload["excludeDomains"] = list(query.exclude_domains)
        country = _country_code(query.country)
        if country is not None:
            payload["country"] = country

        body = await self._post_json("/search", payload)
        data = _success_data(body, operation="search")
        raw_results = data.get("web")
        if not isinstance(raw_results, list):
            raise _response_invalid("搜索服务返回了无法解析的结果")

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
                    title=_title(item.get("title")) or url,
                    content=_truncate(
                        _text(item.get("description")) or _text(item.get("markdown")),
                        self._max_source_characters,
                    ),
                )
            )
        return tuple(results)

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        if not request.urls:
            return ExtractResponse()

        sources: list[ExtractedSource] = []
        failures: list[ExtractFailure] = []
        for url in request.urls:
            try:
                extracted = await self._scrape_url(url)
            except DiscoveryProviderError as exc:
                if exc.status_code in {400, 404, 410, 422}:
                    failures.append(ExtractFailure(url=url, code="unreadable"))
                    continue
                raise
            if extracted is None:
                failures.append(ExtractFailure(url=url, code="unreadable"))
                continue
            sources.append(extracted)
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

    async def _scrape_url(self, url: str) -> ExtractedSource | None:
        body = await self._post_json(
            "/scrape",
            {
                "url": url,
                "formats": [{"type": "markdown"}],
                "onlyMainContent": True,
                # This beta LLM cleanup is unnecessary for source cards and can
                # spend additional credits.  The bounded Researcher handles
                # curation after source policy checks instead.
                "onlyCleanContent": False,
                "removeBase64Images": True,
                "blockAds": True,
                # Public-source discovery should not opt users into a remote
                # persistent cache merely to create short-lived local cards.
                "storeInCache": False,
                # Avoid automatic paid enhanced-proxy retries in the default
                # student/local workflow.
                "proxy": "basic",
            },
        )
        data = _success_data(body, operation="scrape")
        metadata = data.get("metadata")
        if not isinstance(metadata, Mapping):
            raise _response_invalid("链接提取服务返回了无法解析的结果")
        if _text(metadata.get("error")):
            return None

        content = _text(data.get("markdown"))
        if not content:
            return None
        canonical_url = _text(metadata.get("url")) or url
        return ExtractedSource(
            # Preserve the policy-checked request URL for worker-side matching;
            # ``canonical_url`` carries the connector-reported redirect target.
            url=url,
            canonical_url=canonical_url,
            title=_title(metadata.get("title")) or canonical_url,
            content=_truncate(content, self._max_source_characters),
        )

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client.stream(
                "POST",
                path,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as response:
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
            raise _response_invalid("搜索服务返回了无法解析的结果") from exc
        if not isinstance(decoded, dict):
            raise _response_invalid("搜索服务返回了无法解析的结果")
        return decoded


def _success_data(body: Mapping[str, object], *, operation: str) -> Mapping[str, object]:
    if body.get("success") is not True:
        raise _response_invalid(f"{operation} 服务返回了无法解析的结果")
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise _response_invalid(f"{operation} 服务返回了无法解析的结果")
    return data


def _response_error(status_code: int) -> DiscoveryProviderError:
    if status_code in {401, 403}:
        return DiscoveryProviderError(
            code="discovery_connector_authentication_failed",
            message="搜索服务认证失败，请检查连接器密钥",
            status_code=status_code,
        )
    if status_code == 402:
        return DiscoveryProviderError(
            code="discovery_connector_quota_exhausted",
            message="搜索服务额度不足，请切换或补充自己的连接器密钥",
            status_code=status_code,
        )
    if status_code == 429:
        return DiscoveryProviderError(
            code="discovery_connector_rate_limited",
            message="搜索服务请求过于频繁，请稍后重试",
            retryable=True,
            status_code=status_code,
        )
    if status_code in {400, 404, 410, 422}:
        return DiscoveryProviderError(
            code="discovery_connector_request_rejected",
            message="搜索服务拒绝了当前请求",
            status_code=status_code,
        )
    return DiscoveryProviderError(
        code="discovery_connector_unavailable",
        message="搜索服务暂时不可用",
        retryable=status_code >= 500,
        status_code=status_code,
    )


def _response_invalid(message: str) -> DiscoveryProviderError:
    return DiscoveryProviderError(
        code="discovery_connector_response_invalid",
        message=message,
    )


def _country_code(value: str | None) -> str | None:
    candidate = _text(value)
    if len(candidate) == 2 and candidate.isascii() and candidate.isalpha():
        return candidate.upper()
    return None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _title(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list | tuple):
        for item in value:
            text = _text(item)
            if text:
                return text
    return ""


def _truncate(value: str, limit: int) -> str:
    return value[:limit]


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000))
