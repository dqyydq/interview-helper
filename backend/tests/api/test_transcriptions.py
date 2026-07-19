import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.api.routes import transcriptions as route
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.session import async_session_factory, engine
from app.main import app
from app.providers.speech_base import (
    SpeechToTextProvider,
    TranscriptionRequest,
    TranscriptionResult,
)


class StubTranscriptionProvider(SpeechToTextProvider):
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        assert request.audio == b"recorded-answer"
        assert request.content_type == "audio/webm"
        return TranscriptionResult(
            text="先确认容量目标，再讨论分片策略。",
            language=request.language,
            duration_seconds=4.2,
        )

    async def aclose(self) -> None:
        return None


async def clear_transcription_settings() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(ModelRoleBinding))
        await session.execute(delete(ModelConnection))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_transcription_settings():
    await clear_transcription_settings()
    yield
    await clear_transcription_settings()
    await engine.dispose()


def connection_payload(provider_type: str = "openai_compatible") -> dict:
    return {
        "name": f"stt-{provider_type}",
        "provider_type": provider_type,
        "base_url": "https://speech.example.test/v1",
        "api_key": "local-secret",
        "model_name": "gpt-4o-mini-transcribe",
        "context_window_tokens": 16_000,
        "max_output_tokens": 1_024,
    }


@pytest.mark.asyncio
async def test_transcription_requires_explicit_stt_binding_and_keeps_text_fallback() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/transcriptions",
            files={
                "file": (
                    "answer.webm",
                    b"recorded-answer",
                    "audio/webm;codecs=opus",
                )
            },
            data={"language": "zh"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "transcription_unavailable"
    assert "文字回答" in response.json()["message"]


@pytest.mark.asyncio
async def test_transcription_route_returns_editable_text_without_persisting_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        route,
        "build_transcription_provider",
        lambda _: StubTranscriptionProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        connection = await client.post(
            "/api/model-connections",
            json=connection_payload(),
        )
        await client.put(
            "/api/model-connections/roles/transcriber",
            json={"connection_id": connection.json()["id"]},
        )
        response = await client.post(
            "/api/transcriptions",
            files={"file": ("answer.webm", b"recorded-answer", "audio/webm")},
            data={"language": "zh"},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "先确认容量目标，再讨论分片策略。"
    assert response.json()["language"] == "zh"


@pytest.mark.asyncio
async def test_transcription_rejects_anthropic_binding_and_unknown_audio_type() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        unsupported = await client.post(
            "/api/transcriptions",
            files={"file": ("answer.bin", b"recorded-answer", "application/octet-stream")},
        )
        connection = await client.post(
            "/api/model-connections",
            json=connection_payload("anthropic_compatible"),
        )
        await client.put(
            "/api/model-connections/roles/transcriber",
            json={"connection_id": connection.json()["id"]},
        )
        anthropic = await client.post(
            "/api/transcriptions",
            files={"file": ("answer.webm", b"recorded-answer", "audio/webm")},
        )

    assert unsupported.status_code == 415
    assert unsupported.json()["code"] == "audio_type_unsupported"
    assert anthropic.status_code == 409
    assert anthropic.json()["code"] == "transcription_provider_invalid"
