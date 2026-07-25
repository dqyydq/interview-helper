import anyio
from fastapi import APIRouter

from app.core.config import settings
from app.local_ai.catalog import get_local_ai_preset_catalog
from app.local_ai.docker import collect_docker_diagnostics
from app.schemas.local_ai import LocalAiDockerDiagnostics, LocalAiPresetCatalog

router = APIRouter(prefix="/local-ai", tags=["local-ai"])


@router.get("/presets", response_model=LocalAiPresetCatalog)
async def list_local_ai_presets() -> LocalAiPresetCatalog:
    """List supported local Docker presets without inspecting the host."""

    return get_local_ai_preset_catalog()


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
