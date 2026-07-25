import math
import time

import httpx

from app.providers.base import ProviderError
from app.providers.embedding_base import EmbeddingProvider
from app.providers.http import (
    elapsed_ms,
    provider_error_from_response,
    provider_transport_error,
    trust_environment_for_provider_url,
)
from app.providers.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderHealth,
    ProviderHealthStatus,
    Usage,
)

INVALID_RESPONSE_MESSAGE = "向量服务返回了无效结果"
INVALID_DIMENSIONS_MESSAGE = "向量服务返回了错误维度"


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Bounded OpenAI-compatible dense embedding client.

    It deliberately accepts an optional API key: trusted loopback TEI services
    do not need a user secret, while cloud connections still supply one through
    the factory. Both paths use the same strict response contract.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        expected_dimensions: int | None = None,
        timeout_seconds: float = 20.0,
        max_texts: int = 16,
        max_text_characters: int = 12_000,
        max_total_characters: int = 48_000,
        extra_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.expected_dimensions = expected_dimensions
        self.timeout_seconds = timeout_seconds
        self.max_texts = max_texts
        self.max_text_characters = max_text_characters
        self.max_total_characters = max_total_characters
        self.extra_headers = extra_headers or {}
        self.client = client or httpx.AsyncClient(
            trust_env=trust_environment_for_provider_url(base_url)
        )
        self._owns_client = client is None

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/embeddings"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _validate_request(self, request: EmbeddingRequest) -> list[str]:
        texts = request.texts
        if not 1 <= len(texts) <= self.max_texts:
            raise ProviderError(
                code="embedding_input_invalid",
                message="向量请求的文本数量超出允许范围",
            )
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ProviderError(
                code="embedding_input_invalid",
                message="向量请求包含空文本",
            )
        if any(len(text) > self.max_text_characters for text in texts):
            raise ProviderError(
                code="embedding_input_too_large",
                message="单段待向量化文本过长",
            )
        if sum(len(text) for text in texts) > self.max_total_characters:
            raise ProviderError(
                code="embedding_input_too_large",
                message="本次待向量化文本总量过大",
            )
        return texts

    @staticmethod
    def _usage(payload: object) -> Usage:
        if not isinstance(payload, dict):
            return Usage()
        prompt_tokens = payload.get("prompt_tokens", 0)
        input_tokens = (
            prompt_tokens
            if (
                isinstance(prompt_tokens, int)
                and not isinstance(prompt_tokens, bool)
                and prompt_tokens >= 0
            )
            else 0
        )
        return Usage(input_tokens=input_tokens)

    @staticmethod
    def _finite_number(value: object) -> bool:
        if not isinstance(value, int | float) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False

    def _vectors(self, payload: object, *, expected_count: int) -> list[list[float]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError(code="provider_invalid_response", message=INVALID_RESPONSE_MESSAGE)
        indexed: dict[int, list[float]] = {}
        observed_dimensions: int | None = None
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise ProviderError(
                    code="provider_invalid_response",
                    message=INVALID_RESPONSE_MESSAGE,
                )
            index = item.get("index")
            vector = item.get("embedding")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= expected_count
                or index in indexed
                or not isinstance(vector, list)
                or not vector
                or not all(self._finite_number(value) for value in vector)
            ):
                raise ProviderError(
                    code="provider_invalid_response",
                    message=INVALID_RESPONSE_MESSAGE,
                )
            normalized_vector = [float(value) for value in vector]
            if observed_dimensions is None:
                observed_dimensions = len(normalized_vector)
            elif len(normalized_vector) != observed_dimensions:
                raise ProviderError(
                    code="provider_invalid_response",
                    message=INVALID_DIMENSIONS_MESSAGE,
                )
            if (
                self.expected_dimensions is not None
                and len(normalized_vector) != self.expected_dimensions
            ):
                raise ProviderError(
                    code="provider_invalid_response",
                    message=INVALID_DIMENSIONS_MESSAGE,
                )
            indexed[index] = normalized_vector
        if set(indexed) != set(range(expected_count)):
            raise ProviderError(code="provider_invalid_response", message=INVALID_RESPONSE_MESSAGE)
        return [indexed[index] for index in range(expected_count)]

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        texts = self._validate_request(request)
        try:
            response = await self.client.post(
                self.endpoint,
                headers=self._headers(),
                json={"model": self.model, "input": texts},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise provider_transport_error(exc) from exc
        if response.is_error:
            raise provider_error_from_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                code="provider_invalid_response",
                message=INVALID_RESPONSE_MESSAGE,
            ) from exc
        return EmbeddingResponse(
            vectors=self._vectors(payload, expected_count=len(texts)),
            usage=self._usage(payload.get("usage") if isinstance(payload, dict) else None),
        )

    async def health_check(self) -> ProviderHealth:
        started_at = time.perf_counter()
        try:
            await self.embed(EmbeddingRequest(texts=["health check"]))
        except ProviderError as exc:
            return ProviderHealth(
                status=ProviderHealthStatus.DEGRADED,
                latency_ms=elapsed_ms(started_at),
                error_code=exc.code,
            )
        return ProviderHealth(
            status=ProviderHealthStatus.HEALTHY,
            latency_ms=elapsed_ms(started_at),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
