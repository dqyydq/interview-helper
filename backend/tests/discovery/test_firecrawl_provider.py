import json

import httpx
import pytest

from app.discovery.providers import (
    DiscoveryProviderError,
    ExtractRequest,
    FirecrawlSearchProvider,
    SearchQuery,
)


@pytest.mark.asyncio
async def test_firecrawl_search_uses_fixed_v2_bearer_contract_and_safe_filters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v2/search"
        assert request.headers["authorization"] == "Bearer test-key"
        assert "api_key" not in payload
        assert payload == {
            "query": "字节跳动 二面 LLM",
            "limit": 2,
            "sources": [{"type": "web"}],
            "ignoreInvalidURLs": True,
            "highlights": False,
            # Firecrawl declares include/exclude mutually exclusive.  The
            # downstream URLPolicy enforces the omitted deny rule.
            "includeDomains": ["example.cn"],
            "country": "CN",
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "url": "https://example.cn/post",
                            "title": "面经",
                            "description": "题目摘要",
                        }
                    ]
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.firecrawl.test/v2",
    ) as client:
        provider = FirecrawlSearchProvider(api_key="test-key", client=client)
        results = await provider.search(
            SearchQuery(
                query="字节跳动 二面 LLM",
                max_results=2,
                include_domains=("example.cn",),
                exclude_domains=("blocked.example.cn",),
                country="cn",
            )
        )

    assert len(results) == 1
    assert results[0].url == "https://example.cn/post"
    assert results[0].title == "面经"
    assert results[0].content == "题目摘要"
    assert results[0].score is None


@pytest.mark.asyncio
async def test_firecrawl_extract_scrapes_each_url_and_returns_bounded_partial_results() -> None:
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v2/scrape"
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload["formats"] == [{"type": "markdown"}]
        assert payload["onlyMainContent"] is True
        assert payload["onlyCleanContent"] is False
        assert payload["removeBase64Images"] is True
        assert payload["blockAds"] is True
        assert payload["storeInCache"] is False
        assert payload["proxy"] == "basic"
        requested_urls.append(payload["url"])
        if payload["url"] == "https://example.cn/post":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "x" * 32,
                        "metadata": {
                            "title": ["面经", "unused title"],
                            "sourceURL": "https://example.cn/post",
                            "url": "https://example.cn/final-post",
                        },
                    },
                },
            )
        return httpx.Response(404, text="private source failure")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.firecrawl.test/v2",
    ) as client:
        provider = FirecrawlSearchProvider(
            api_key="test-key",
            client=client,
            max_source_characters=12,
        )
        response = await provider.extract(
            ExtractRequest(
                urls=("https://example.cn/post", "https://example.cn/missing"),
            )
        )

    assert requested_urls == ["https://example.cn/post", "https://example.cn/missing"]
    assert response.sources[0].url == "https://example.cn/post"
    assert response.sources[0].canonical_url == "https://example.cn/final-post"
    assert response.sources[0].title == "面经"
    assert response.sources[0].content == "x" * 12
    assert response.failures[0].url == "https://example.cn/missing"
    assert response.failures[0].code == "unreadable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "discovery_connector_authentication_failed", False),
        (402, "discovery_connector_quota_exhausted", False),
        (429, "discovery_connector_rate_limited", True),
    ],
)
async def test_firecrawl_errors_are_sanitized_and_stably_mapped(
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="private failure detail: test-key")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.firecrawl.test/v2",
    ) as client:
        provider = FirecrawlSearchProvider(api_key="test-key", client=client)
        with pytest.raises(DiscoveryProviderError) as exc_info:
            await provider.search(SearchQuery(query="test", max_results=1))

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable
    assert "test-key" not in str(exc_info.value)
    assert "private failure" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_firecrawl_rejects_oversized_response_before_json_decoding() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + (b"x" * 2_048) + b"}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.firecrawl.test/v2",
    ) as client:
        provider = FirecrawlSearchProvider(
            api_key="test-key",
            client=client,
            max_response_bytes=1_024,
        )
        with pytest.raises(DiscoveryProviderError) as exc_info:
            await provider.search(SearchQuery(query="test", max_results=1))

    assert exc_info.value.code == "discovery_connector_response_too_large"
