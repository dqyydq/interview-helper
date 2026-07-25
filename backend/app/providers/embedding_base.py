from abc import ABC, abstractmethod

from app.providers.types import EmbeddingRequest, EmbeddingResponse, ProviderHealth


class EmbeddingProvider(ABC):
    """A capability-specific embedding transport, separate from chat models."""

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...

    @abstractmethod
    async def health_check(self) -> ProviderHealth: ...
