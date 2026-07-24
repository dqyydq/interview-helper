"""Small, provider-neutral contract for controlled public-web discovery.

The application never accepts a provider endpoint from a user.  Implementations receive
only a connector credential and structured query / URL inputs that have already passed
the discovery policy boundary.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


class DiscoveryProviderError(Exception):
    """A sanitised connector failure safe to expose through the API error contract."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SearchProviderCapabilities:
    supports_domain_filters: bool = True
    supports_extract: bool = False
    safe_extract: bool = False


@dataclass(frozen=True, slots=True)
class SearchQuery:
    query: str
    max_results: int = 20
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    country: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str
    content: str
    score: float | None = None


@dataclass(frozen=True, slots=True)
class ExtractRequest:
    urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractedSource:
    url: str
    canonical_url: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class ExtractFailure:
    url: str
    code: Literal["unreadable", "blocked", "provider_error"] = "unreadable"


@dataclass(frozen=True, slots=True)
class ExtractResponse:
    sources: tuple[ExtractedSource, ...] = ()
    failures: tuple[ExtractFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryProviderHealth:
    status: Literal["healthy", "degraded"]
    latency_ms: int
    error_code: str | None = None


class SearchProvider(ABC):
    """Provider capability boundary used by discovery jobs only."""

    capabilities: SearchProviderCapabilities

    @abstractmethod
    async def search(self, query: SearchQuery) -> tuple[SearchResult, ...]: ...

    @abstractmethod
    async def extract(self, request: ExtractRequest) -> ExtractResponse: ...

    @abstractmethod
    async def health_check(self) -> DiscoveryProviderHealth: ...

    async def aclose(self) -> None:
        """Close resources when the implementation owns a network client."""

        return None
