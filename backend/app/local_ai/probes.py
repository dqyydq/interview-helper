"""Bounded, capability-specific probes for fixed loopback local services."""

from __future__ import annotations

import asyncio
import time

import httpx

from app.db.models.common import ModelRole
from app.local_ai.capabilities import LOCAL_CAPABILITIES, LocalCapabilityDefinition
from app.providers.openai_embedding import OpenAICompatibleEmbeddingProvider
from app.providers.types import ProviderHealthStatus
from app.schemas.local_ai import LocalAiCapability, LocalAiCapabilityStatus


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
        if health.status is ProviderHealthStatus.HEALTHY:
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


async def probe_all_local_capabilities(*, timeout_seconds: float) -> list[LocalAiCapability]:
    return list(
        await asyncio.gather(
            *(
                probe_local_capability(capability, timeout_seconds=timeout_seconds)
                for capability in LOCAL_CAPABILITIES
            )
        )
    )
