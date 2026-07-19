import httpx
import pytest

from app.db.models.common import ProviderType
from app.db.models.model_connection import ModelConnection
from app.providers.base import ProviderError
from app.providers.factory import build_transcription_provider
from app.providers.openai_transcription import OpenAICompatibleTranscriptionProvider
from app.providers.speech_base import TranscriptionRequest


@pytest.mark.asyncio
async def test_openai_transcription_posts_audio_multipart_and_normalizes_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = request.content
        assert b'name="model"' in body
        assert b"gpt-4o-mini-transcribe" in body
        assert b'filename="answer.webm"' in body
        assert b"audio/webm" in body
        return httpx.Response(
            200,
            json={"text": "我会先确认系统边界。", "language": "zh", "duration": 8.4},
            headers={"x-request-id": "transcription-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleTranscriptionProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="gpt-4o-mini-transcribe",
            client=client,
        )
        result = await provider.transcribe(
            TranscriptionRequest(
                audio=b"webm-audio",
                filename="answer.webm",
                content_type="audio/webm",
                language="zh",
            )
        )

    assert result.text == "我会先确认系统边界。"
    assert result.duration_seconds == 8.4
    assert result.provider_request_id == "transcription-1"


@pytest.mark.asyncio
async def test_transcription_errors_are_sanitized() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="private provider detail test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleTranscriptionProvider(
            base_url="https://provider.test/v1",
            api_key="test-key",
            model="whisper-test",
            client=client,
        )
        with pytest.raises(ProviderError) as exc_info:
            await provider.transcribe(
                TranscriptionRequest(
                    audio=b"audio",
                    filename="answer.wav",
                    content_type="audio/wav",
                )
            )

    assert exc_info.value.code == "provider_authentication_failed"
    assert "test-key" not in str(exc_info.value)
    assert "provider detail" not in str(exc_info.value)


def test_anthropic_connection_cannot_be_built_as_transcription_provider() -> None:
    connection = ModelConnection(
        profile_id="00000000-0000-0000-0000-000000000001",
        name="Anthropic chat",
        provider_type=ProviderType.ANTHROPIC_COMPATIBLE,
        base_url="https://api.anthropic.com/v1",
        model_name="claude-test",
        context_window_tokens=100_000,
    )

    with pytest.raises(ProviderError) as exc_info:
        build_transcription_provider(connection)

    assert exc_info.value.code == "transcription_provider_unsupported"
