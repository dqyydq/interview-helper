import json

import httpx
import pytest

from app.discovery.providers import (
    DiscoveryProviderError,
    ExtractRequest,
    SearchQuery,
    TavilySearchProvider,
)


@pytest.mark.asyncio
async def test_tavily_search_uses_fixed_contract_and_domain_filters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/search"
        assert payload["api_key"] == "test-key"
        assert payload["query"] == "字节跳动 二面 LLM"
        assert payload["include_domains"] == ["example.cn"]
        assert payload["exclude_domains"] == ["blocked.example.cn"]
        assert payload["max_results"] == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.cn/post",
                        "title": "面经",
                        "content": "题目摘要",
                        "score": 0.9,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.tavily.test",
    ) as client:
        provider = TavilySearchProvider(api_key="test-key", client=client)
        results = await provider.search(
            SearchQuery(
                query="字节跳动 二面 LLM",
                max_results=2,
                include_domains=("example.cn",),
                exclude_domains=("blocked.example.cn",),
            )
        )

    assert len(results) == 1
    assert results[0].url == "https://example.cn/post"
    assert results[0].score == 0.9


@pytest.mark.asyncio
async def test_tavily_extract_returns_bounded_sources_and_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/extract"
        assert payload["urls"] == ["https://example.cn/post"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.cn/post",
                        "canonical_url": "https://example.cn/post",
                        "title": "面经",
                        "raw_content": "x" * 32,
                    }
                ],
                "failed_results": [{"url": "https://failed.example.cn/post"}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.tavily.test",
    ) as client:
        provider = TavilySearchProvider(
            api_key="test-key",
            client=client,
            max_source_characters=12,
        )
        response = await provider.extract(ExtractRequest(urls=("https://example.cn/post",)))

    assert response.sources[0].content == "x" * 12
    assert response.failures[0].url == "https://failed.example.cn/post"


@pytest.mark.asyncio
async def test_tavily_errors_are_sanitized_and_never_include_provider_body_or_key() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="private failure detail: test-key")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.tavily.test",
    ) as client:
        provider = TavilySearchProvider(api_key="test-key", client=client)
        with pytest.raises(DiscoveryProviderError) as exc_info:
            await provider.search(SearchQuery(query="test", max_results=1))

    assert exc_info.value.code == "discovery_connector_authentication_failed"
    assert "test-key" not in str(exc_info.value)
    assert "private failure" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_rejects_oversized_response_before_json_decoding() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + (b"x" * 2_048) + b"}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.tavily.test",
    ) as client:
        provider = TavilySearchProvider(
            api_key="test-key",
            client=client,
            max_response_bytes=1_024,
        )
        with pytest.raises(DiscoveryProviderError) as exc_info:
            await provider.search(SearchQuery(query="test", max_results=1))

    assert exc_info.value.code == "discovery_connector_response_too_large"
