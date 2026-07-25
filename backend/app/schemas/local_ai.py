from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel


class LocalAiPreset(ApiModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    capability: Literal["embedding", "transcription"]
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=240)
    runtime: Literal["tei", "funasr"]
    model_source: Literal["modelscope"]
    model_id: str = Field(min_length=1, max_length=255)
    quality_tier: Literal["light", "balanced", "quality"]
    vector_dimensions: int | None = Field(default=None, ge=1, le=4_096)
    cpu_supported: bool
    gpu_recommended: bool


class LocalAiPresetCatalog(ApiModel):
    catalog_version: int = Field(ge=1)
    presets: list[LocalAiPreset]


class DockerComponentState(StrEnum):
    AVAILABLE = "available"
    EXECUTABLE_MISSING = "executable_missing"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"
    NOT_CHECKED = "not_checked"


class DockerComponentDiagnostics(ApiModel):
    state: DockerComponentState


class DockerEngineDiagnostics(ApiModel):
    """Facts needed to decide whether local Linux AI containers can run."""

    state: DockerComponentState
    container_os: Literal["linux", "windows", "unknown"]
    locality: Literal["local", "remote", "unknown"]


class LocalAiDockerDiagnosticsStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class LocalAiDockerDiagnostics(ApiModel):
    checked_at: datetime
    status: LocalAiDockerDiagnosticsStatus
    docker: DockerComponentDiagnostics
    compose: DockerComponentDiagnostics
    engine: DockerEngineDiagnostics
    compose_project: DockerComponentDiagnostics
    # Phase A deliberately does not pull a CUDA image merely to check this.
    # Keep the API forward-compatible with the explicit GPU probe in Phase B.
    gpu_check: Literal["available", "unavailable", "not_checked"]
    next_step: Literal[
        "configure_local_ai",
        "enable_docker_compose",
        "install_or_start_docker",
        "repair_compose_project",
        "switch_to_local_linux_docker",
    ]
