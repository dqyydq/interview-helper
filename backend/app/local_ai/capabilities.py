"""Fixed, Docker-managed local capability definitions.

These targets are intentionally not user-editable model connections.  The
browser cannot supply a URL, Docker argument, path, or credential: every local
target resolves to one loopback endpoint controlled by this application.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.db.models.common import ModelRole


@dataclass(frozen=True, slots=True)
class LocalCapabilityDefinition:
    key: str
    role: ModelRole
    title: str
    summary: str
    runtime: str
    compose_profile: str
    model_name: str
    revision: str
    vector_dimensions: int | None = None

    @property
    def base_url(self) -> str:
        if self.role is ModelRole.TRANSCRIBER:
            return f"http://127.0.0.1:{settings.local_asr_port}/v1"
        return f"http://127.0.0.1:{settings.local_embeddings_port}/v1"

    @property
    def health_url(self) -> str:
        return self.base_url.removesuffix("/v1") + "/health"


LOCAL_CAPABILITIES: tuple[LocalCapabilityDefinition, ...] = (
    LocalCapabilityDefinition(
        key="sensevoice-small",
        role=ModelRole.TRANSCRIBER,
        title="SenseVoice 本地语音转写",
        summary="Docker 内离线 FunASR；适合短回答，默认单并发以保障面试稳定性。",
        runtime="funasr",
        compose_profile="local-asr",
        model_name="sensevoice-small",
        revision="43d0ed61231c41f8393fa347b838a1f6e2d264f6",
    ),
    LocalCapabilityDefinition(
        key="multilingual-e5-small",
        role=ModelRole.EMBEDDING,
        title="E5 轻量本地检索",
        summary="384 维多语言 dense embedding，适合资源有限的题库和记忆检索。",
        runtime="tei",
        compose_profile="local-embedding-e5",
        model_name="interview-helper-local-embedding",
        revision="bdd905ef05181adf3ebbfaac5cd5bd4ed9a58760",
        vector_dimensions=384,
    ),
    LocalCapabilityDefinition(
        key="bge-m3",
        role=ModelRole.EMBEDDING,
        title="BGE-M3 高质量本地检索",
        summary="1024 维 dense embedding，质量优先；CPU 可运行，GPU 或充足内存体验更好。",
        runtime="tei",
        compose_profile="local-embedding-bge",
        model_name="interview-helper-local-embedding",
        revision="e44369c5623cc146f016da906583db4ee0e3488d",
        vector_dimensions=1024,
    ),
)

_CAPABILITY_BY_KEY = {capability.key: capability for capability in LOCAL_CAPABILITIES}


def get_local_capability(key: str) -> LocalCapabilityDefinition | None:
    return _CAPABILITY_BY_KEY.get(key)
