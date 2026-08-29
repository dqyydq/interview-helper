"""Bounded, capability-specific probes for fixed loopback local services."""

from __future__ import annotations

import asyncio
import time

import httpx

from app.db.models.common import ModelRole
from app.local_ai.capabilities import LOCAL_CAPABILITIES, LocalCapabilityDefinition
from app.providers.base import ProviderError
from app.providers.http import elapsed_ms
from app.providers.openai_embedding import OpenAICompatibleEmbeddingProvider
from app.providers.types import EmbeddingRequest, ProviderHealthStatus
from app.schemas.local_ai import LocalAiCapability, LocalAiCapabilityStatus

_capability_cache: tuple[float, tuple[LocalAiCapability, ...]] | None = None
_capability_probe_task: asyncio.Task[tuple[LocalAiCapability, ...]] | None = None


def _latency_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1_000))


def _public(
    capability: LocalCapabilityDefinition,
    *,
    status: LocalAiCapabilityStatus,
    latency_ms: int | None,
    error_code: str | None,
) -> LocalAiCapability:
    return LocalAiCapability(
        key=capability.key,
        role=capability.role,
        title=capability.title,
        summary=capability.summary,
        runtime=capability.runtime,
        compose_profile=capability.compose_profile,
        model_name=capability.model_name,
        revision=capability.revision,
        vector_dimensions=capability.vector_dimensions,
        status=status,
        latency_ms=latency_ms,
        error_code=error_code,
    )


async def probe_local_capability(
    capability: LocalCapabilityDefinition,
    *,
    timeout_seconds: float,
) -> LocalAiCapability:
    """Probe only the capability's fixed loopback endpoint.

    This never launches Docker, downloads a model, or follows a user-provided
    URL.  A TEI probe requests one tiny embedding so the expected dimensionality
    can distinguish E5 from BGE-M3 even though they share a host port.
    """

    started_at = time.perf_counter()
    if capability.role is ModelRole.EMBEDDING:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url=capability.base_url,
            api_key=None,
            model=capability.model_name,
            expected_dimensions=capability.vector_dimensions,
            timeout_seconds=timeout_seconds,
        )
        try:
            health = await provider.health_check()
        finally:
            await provider.aclose()
        if health.status == ProviderHealthStatus.HEALTHY:
            status = LocalAiCapabilityStatus.READY
        elif health.error_code == "provider_invalid_response":
            status = LocalAiCapabilityStatus.MISMATCH
        else:
            status = LocalAiCapabilityStatus.UNAVAILABLE
        return _public(
            capability,
            status=status,
            latency_ms=health.latency_ms,
            error_code=health.error_code,
        )

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout_seconds) as client:
            response = await client.get(capability.health_url)
    except httpx.TimeoutException:
        return _public(
            capability,
            status=LocalAiCapabilityStatus.UNAVAILABLE,
            latency_ms=_latency_ms(started_at),
            error_code="provider_timeout",
        )
    except httpx.HTTPError:
        return _public(
            capability,
            status=LocalAiCapabilityStatus.UNAVAILABLE,
            latency_ms=_latency_ms(started_at),
            error_code="provider_connection_failed",
        )
    if response.status_code != 200:
        return _public(
            capability,
            status=LocalAiCapabilityStatus.UNAVAILABLE,
            latency_ms=_latency_ms(started_at),
            error_code="local_asr_unavailable",
        )
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or {
        "status": payload.get("status"),
        "model": payload.get("model"),
        "revision": payload.get("revision"),
    } != {
        "status": "ready",
        "model": capability.model_name,
        "revision": capability.revision,
    }:
        return _public(
            capability,
            status=LocalAiCapabilityStatus.MISMATCH,
            latency_ms=_latency_ms(started_at),
            error_code="local_asr_mismatch",
        )
    return _public(
        capability,
        status=LocalAiCapabilityStatus.READY,
        latency_ms=_latency_ms(started_at),
        error_code=None,
    )


async def _probe_embedding_endpoint(
    capability: LocalCapabilityDefinition,
    *,
    timeout_seconds: float,
) -> tuple[int | None, int | None, str | None]:
    """Probe a shared TEI endpoint once and return its observed vector size.

    E5 and BGE-M3 intentionally share one port and can never both be running.
    Their bulk settings status should therefore perform one tiny inference, not
    two competing ones.  The returned dimension identifies which preset owns
    the endpoint.
    """

    started_at = time.perf_counter()
    provider = OpenAICompatibleEmbeddingProvider(
        base_url=capability.base_url,
        api_key=None,
        model=capability.model_name,
        timeout_seconds=timeout_seconds,
    )
    try:
        response = await provider.embed(EmbeddingRequest(texts=["health check"]))
    except ProviderError as exc:
        return None, elapsed_ms(started_at), exc.code
    finally:
        await provider.aclose()
    return len(response.vectors[0]), elapsed_ms(started_at), None


async def _probe_all_uncached(*, timeout_seconds: float) -> tuple[LocalAiCapability, ...]:
    embedding_capabilities = tuple(
        capability for capability in LOCAL_CAPABILITIES if capability.role == ModelRole.EMBEDDING
    )
    other_capabilities = tuple(
        capability for capability in LOCAL_CAPABILITIES if capability.role != ModelRole.EMBEDDING
    )

    async def probe_embedding_group() -> dict[str, LocalAiCapability]:
        if not embedding_capabilities:
            return {}
        dimensions, latency_ms, error_code = await _probe_embedding_endpoint(
            embedding_capabilities[0],
            timeout_seconds=timeout_seconds,
        )
        results: dict[str, LocalAiCapability] = {}
        for capability in embedding_capabilities:
            if dimensions is None:
                status = (
                    LocalAiCapabilityStatus.MISMATCH
                    if error_code == "provider_invalid_response"
                    else LocalAiCapabilityStatus.UNAVAILABLE
                )
                results[capability.key] = _public(
                    capability,
                    status=status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                )
                continue
            if dimensions == capability.vector_dimensions:
                results[capability.key] = _public(
                    capability,
                    status=LocalAiCapabilityStatus.READY,
                    latency_ms=latency_ms,
                    error_code=None,
                )
            else:
                results[capability.key] = _public(
                    capability,
                    status=LocalAiCapabilityStatus.MISMATCH,
                    latency_ms=latency_ms,
                    error_code="embedding_dimension_mismatch",
                )
        return results

    other_results, embedding_results = await asyncio.gather(
        asyncio.gather(
            *(
                probe_local_capability(capability, timeout_seconds=timeout_seconds)
                for capability in other_capabilities
            )
        ),
        probe_embedding_group(),
    )
    results_by_key = {result.key: result for result in other_results}
    results_by_key.update(embedding_results)
    return tuple(results_by_key[capability.key] for capability in LOCAL_CAPABILITIES)


def invalidate_local_capability_probe_cache() -> None:
    """Forget only UI status; this never stops, starts, or reconfigures Docker."""

    global _capability_cache
    _capability_cache = None


async def probe_all_local_capabilities(
    *,
    timeout_seconds: float,
    cache_seconds: float,
) -> list[LocalAiCapability]:
    """Return a short-lived, single-flight local capability status snapshot."""

    global _capability_cache, _capability_probe_task
    now = time.monotonic()
    if cache_seconds > 0 and _capability_cache and now - _capability_cache[0] <= cache_seconds:
        return list(_capability_cache[1])

    task = _capability_probe_task
    if task is None or task.done():
        task = asyncio.create_task(_probe_all_uncached(timeout_seconds=timeout_seconds))
        _capability_probe_task = task
    try:
        results = await asyncio.shield(task)
    finally:
        if task.done() and _capability_probe_task is task:
            _capability_probe_task = None
    _capability_cache = (time.monotonic(), results)
    return list(results)
