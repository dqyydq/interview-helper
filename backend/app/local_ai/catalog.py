"""Static, user-selectable local AI presets.

The catalog deliberately contains model metadata only.  It does not resolve a
model directory or invoke a downloader; those operations belong to the future
Docker model-delivery workflow.
"""

from app.schemas.local_ai import LocalAiPreset, LocalAiPresetCatalog

LOCAL_AI_PRESETS: tuple[LocalAiPreset, ...] = (
    LocalAiPreset(
        key="multilingual-e5-small",
        capability="embedding",
        title="轻量多语言检索",
        summary="适合中文、英文混合的题库与记忆检索，优先节省本地资源。",
        runtime="tei",
        model_source="modelscope",
        model_id="intfloat/multilingual-e5-small",
        quality_tier="light",
        vector_dimensions=384,
        cpu_supported=True,
        gpu_recommended=False,
    ),
    LocalAiPreset(
        key="bge-m3",
        capability="embedding",
        title="高质量多语言检索",
        summary="适合更重视召回质量的中英文题库与长期记忆检索。",
        runtime="tei",
        model_source="modelscope",
        model_id="BAAI/bge-m3",
        quality_tier="quality",
        vector_dimensions=1024,
        cpu_supported=True,
        gpu_recommended=True,
    ),
    LocalAiPreset(
        key="sensevoice-small",
        capability="transcription",
        title="本地语音转写",
        summary="通过 FunASR 容器提供 OpenAI-compatible 转写接口，适合本地面试录音。",
        runtime="funasr",
        model_source="modelscope",
        model_id="iic/SenseVoiceSmall",
        quality_tier="balanced",
        vector_dimensions=None,
        cpu_supported=True,
        gpu_recommended=True,
    ),
)


def get_local_ai_preset_catalog() -> LocalAiPresetCatalog:
    """Return the versioned catalog without inspecting local model files."""

    return LocalAiPresetCatalog(catalog_version=1, presets=list(LOCAL_AI_PRESETS))
