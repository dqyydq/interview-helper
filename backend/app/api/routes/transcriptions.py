from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.api.deps import SessionDep
from app.api.errors import AppError
from app.core.config import settings
from app.db.models.common import ProviderType
from app.local_ai.capabilities import LocalCapabilityDefinition
from app.local_ai.probes import probe_local_capability
from app.providers.factory import build_transcription_provider
from app.providers.openai_transcription import OpenAICompatibleTranscriptionProvider
from app.providers.speech_base import TranscriptionRequest, TranscriptionResult
from app.schemas.local_ai import LocalAiCapabilityStatus
from app.services import model_connections

router = APIRouter(prefix="/transcriptions", tags=["transcriptions"])

ALLOWED_AUDIO_TYPES = {
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
    "video/webm",
}


async def _require_ready_local_transcriber(
    capability: LocalCapabilityDefinition,
) -> None:
    """Refuse transcription unless the fixed SenseVoice service proves its identity.

    A local role binding is configuration only: it does not prove that the
    process currently listening on the loopback port is this application's
    expected FunASR/SenseVoice container.  Probe the health endpoint on every
    request, then require its ready state, model name, and immutable revision
    before sending user audio.  There is intentionally no cloud fallback here.
    """

    health = await probe_local_capability(
        capability,
        timeout_seconds=settings.local_ai_service_probe_timeout_seconds,
    )
    if health.status == LocalAiCapabilityStatus.READY:
        return
    if health.status == LocalAiCapabilityStatus.MISMATCH:
        raise AppError(
            code="local_transcription_service_mismatch",
            message=("本地 SenseVoice 服务身份不匹配，请使用本应用的 Docker 配置重启服务后重试。"),
            status_code=503,
            retryable=True,
        )
    raise AppError(
        code="local_transcription_service_unavailable",
        message="本地 SenseVoice 服务暂不可用，请确认 Docker 服务已启动后重试。",
        status_code=503,
        retryable=True,
    )


@router.post("", response_model=TranscriptionResult)
async def create_transcription(
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    language: Annotated[str | None, Form(min_length=2, max_length=20)] = None,
) -> TranscriptionResult:
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_AUDIO_TYPES:
        raise AppError(
            code="audio_type_unsupported",
            message="录音格式不受支持，请使用 WebM、Ogg、WAV、MP3 或 M4A",
            status_code=415,
        )
    audio = await file.read(settings.audio_upload_max_bytes + 1)
    await file.close()
    if not audio:
        raise AppError(code="audio_empty", message="录音内容为空", status_code=422)
    if len(audio) > settings.audio_upload_max_bytes:
        raise AppError(
            code="audio_too_large",
            message="录音片段过大，请缩短后重试",
            status_code=413,
        )

    profile = await model_connections.ensure_local_profile(session)
    try:
        target = await model_connections.resolve_transcription_target(session, profile.id)
    except AppError as exc:
        if exc.code != "model_role_unbound":
            raise
        raise AppError(
            code="transcription_unavailable",
            message="尚未配置语音转写模型，仍可继续使用文字回答",
            status_code=409,
        ) from exc
    if isinstance(target, LocalCapabilityDefinition):
        await _require_ready_local_transcriber(target)
        provider = OpenAICompatibleTranscriptionProvider(
            base_url=target.base_url,
            api_key=None,
            model=target.model_name,
            timeout_seconds=settings.local_asr_request_timeout_seconds,
        )
    elif target.provider_type != ProviderType.OPENAI_COMPATIBLE:
        raise AppError(
            code="transcription_provider_invalid",
            message="语音转写角色必须绑定 OpenAI-compatible 连接",
            status_code=409,
        )
    else:
        provider = build_transcription_provider(target)
    try:
        return await provider.transcribe(
            TranscriptionRequest(
                audio=audio,
                filename=file.filename or "recording.webm",
                content_type=content_type,
                language=language,
            )
        )
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()
