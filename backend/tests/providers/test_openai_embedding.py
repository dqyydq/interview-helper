import json

import httpx
import pytest

from app.db.models.common import ProviderType
from app.db.models.model_connection import ModelConnection
from app.providers.base import ProviderError
from app.providers.factory import build_embedding_provider
from app.providers.openai_embedding import OpenAICompatibleEmbeddingProvider
from app.providers.types import EmbeddingRequest


@pytest.mark.asyncio
async def test_openai_embedding_posts_bounded_input_and_orders_vectors_by_index() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {
            "model": "embedding-test",
            "input": ["first", "second"],
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [3, 4]},
                    {"index": 0, "embedding": [1, 2]},
                ],
                "usage": {"prompt_tokens": 7},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="embedding-test",
            expected_dimensions=2,
            client=client,
        )
        result = await provider.embed(EmbeddingRequest(texts=["first", "second"]))

    assert result.vectors == [[1.0, 2.0], [3.0, 4.0]]
    assert result.usage.input_tokens == 7


@pytest.mark.asyncio
async def test_openai_embedding_rejects_nonfinite_or_incomplete_provider_data() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"data":[{"index":0,"embedding":[NaN]}]}',
            headers={"Content-Type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="embedding-test",
            client=client,
        )
        with pytest.raises(ProviderError) as exc_info:
            await provider.embed(EmbeddingRequest(texts=["first", "second"]))

    assert exc_info.value.code == "provider_invalid_response"
    assert "test-key" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_embedding_rejects_inconsistent_dimensions_and_huge_numbers() -> None:
    replies = iter(
        [
            {"data": [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": [2, 3]}]},
            {"data": [{"index": 0, "embedding": [10**400]}]},
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(replies))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="embedding-test",
            client=client,
        )
        for request in (
            EmbeddingRequest(texts=["first", "second"]),
            EmbeddingRequest(texts=["first"]),
        ):
            with pytest.raises(ProviderError) as exc_info:
                await provider.embed(request)
            assert exc_info.value.code == "provider_invalid_response"


@pytest.mark.asyncio
async def test_loopback_embedding_does_not_require_or_send_a_user_key() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url="http://127.0.0.1:8081/v1",
            api_key=None,
            model="interview-helper-local-embedding",
            client=client,
        )
        result = await provider.embed(EmbeddingRequest(texts=["local"]))

    assert result.vectors == [[1.0]]


def test_anthropic_connection_cannot_be_built_as_embedding_provider() -> None:
    connection = ModelConnection(
        profile_id="00000000-0000-0000-0000-000000000001",
        name="Anthropic chat",
        provider_type=ProviderType.ANTHROPIC_COMPATIBLE,
        base_url="https://api.anthropic.com/v1",
        model_name="claude-test",
        context_window_tokens=100_000,
    )

    with pytest.raises(ProviderError) as exc_info:
        build_embedding_provider(connection)

    assert exc_info.value.code == "embedding_provider_unsupported"
