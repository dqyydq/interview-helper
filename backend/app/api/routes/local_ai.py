import anyio
from fastapi import APIRouter

from app.api.errors import AppError
from app.core.config import settings
from app.local_ai.capabilities import get_local_capability
from app.local_ai.catalog import get_local_ai_preset_catalog
from app.local_ai.docker import collect_docker_diagnostics
from app.local_ai.probes import (
    invalidate_local_capability_probe_cache,
    probe_all_local_capabilities,
    probe_local_capability,
)
from app.schemas.local_ai import LocalAiCapability, LocalAiDockerDiagnostics, LocalAiPresetCatalog

router = APIRouter(prefix="/local-ai", tags=["local-ai"])


@router.get("/presets", response_model=LocalAiPresetCatalog)
async def list_local_ai_presets() -> LocalAiPresetCatalog:
    """List supported local Docker presets without inspecting the host."""

    return get_local_ai_preset_catalog()


@router.get("/capabilities", response_model=list[LocalAiCapability])
async def list_local_ai_capabilities() -> list[LocalAiCapability]:
    """Return fixed local capability cards and bounded loopback probe status."""

    return await probe_all_local_capabilities(
        timeout_seconds=settings.local_ai_service_probe_timeout_seconds,
        cache_seconds=settings.local_ai_service_probe_cache_seconds,
    )


@router.post("/capabilities/{capability_key}/test", response_model=LocalAiCapability)
async def test_local_ai_capability(capability_key: str) -> LocalAiCapability:
    capability = get_local_capability(capability_key)
    if capability is None:
        raise AppError(
            code="local_capability_not_found",
            message="本地能力不存在",
            status_code=404,
        )
    result = await probe_local_capability(
        capability,
        timeout_seconds=settings.local_ai_service_probe_timeout_seconds,
    )
    invalidate_local_capability_probe_cache()
    return result


@router.get("/docker-diagnostics", response_model=LocalAiDockerDiagnostics)
async def get_local_ai_docker_diagnostics() -> LocalAiDockerDiagnostics:
    """Return a bounded, read-only Docker readiness summary.

    No request parameter can affect the spawned commands.  The probe runs in a
    worker thread because the underlying Docker CLI call is synchronous.
    """

    return await anyio.to_thread.run_sync(
        lambda: collect_docker_diagnostics(
            timeout_seconds=settings.local_ai_docker_diagnostics_timeout_seconds,
            cache_seconds=settings.local_ai_docker_diagnostics_cache_seconds,
        )
    )
