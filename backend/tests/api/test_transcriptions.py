import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.api.routes import transcriptions as route
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.session import async_session_factory, engine
from app.local_ai.capabilities import LocalCapabilityDefinition
from app.main import app
from app.providers.speech_base import (
    SpeechToTextProvider,
    TranscriptionRequest,
    TranscriptionResult,
)
from app.schemas.local_ai import LocalAiCapability, LocalAiCapabilityStatus


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


def local_probe_result(
    status: LocalAiCapabilityStatus,
    *,
    error_code: str | None,
) -> LocalAiCapability:
    return LocalAiCapability(
        key="sensevoice-small",
        role="transcriber",
        title="Local ASR",
        summary="fixed loopback target",
        runtime="funasr",
        compose_profile="local-asr",
        model_name="sensevoice-small",
        revision="43d0ed61231c41f8393fa347b838a1f6e2d264f6",
        vector_dimensions=None,
        status=status,
        latency_ms=1,
        error_code=error_code,
    )


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


@pytest.mark.asyncio
async def test_transcription_can_use_local_capability_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ready_probe(*_: object, **__: object) -> LocalAiCapability:
        return local_probe_result(LocalAiCapabilityStatus.READY, error_code=None)

    monkeypatch.setattr(route, "probe_local_capability", ready_probe)
    monkeypatch.setattr(
        route,
        "OpenAICompatibleTranscriptionProvider",
        lambda **_: StubTranscriptionProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        binding = await client.put(
            "/api/model-connections/roles/transcriber",
            json={"local_capability_key": "sensevoice-small"},
        )
        response = await client.post(
            "/api/transcriptions",
            files={"file": ("answer.webm", b"recorded-answer", "audio/webm")},
            data={"language": "zh"},
        )

    assert binding.status_code == 200
    assert response.status_code == 200
    assert response.json()["language"] == "zh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_code", "expected_code"),
    [
        (
            LocalAiCapabilityStatus.UNAVAILABLE,
            "provider_connection_failed",
            "local_transcription_service_unavailable",
        ),
        (
            LocalAiCapabilityStatus.MISMATCH,
            "local_asr_mismatch",
            "local_transcription_service_mismatch",
        ),
    ],
)
async def test_local_transcription_rejects_unverified_service_without_cloud_fallback(
    monkeypatch: pytest.MonkeyPatch,
    status: LocalAiCapabilityStatus,
    error_code: str,
    expected_code: str,
) -> None:
    async def failed_probe(
        capability: LocalCapabilityDefinition,
        *,
        timeout_seconds: float,
    ) -> LocalAiCapability:
        assert capability.key == "sensevoice-small"
        assert capability.model_name == "sensevoice-small"
        assert capability.revision == "43d0ed61231c41f8393fa347b838a1f6e2d264f6"
        assert timeout_seconds > 0
        return local_probe_result(status, error_code=error_code)

    def unexpected_provider(**_: object) -> SpeechToTextProvider:
        raise AssertionError("audio must not be sent to an unverified local service")

    def unexpected_cloud_fallback(*_: object) -> SpeechToTextProvider:
        raise AssertionError("local transcription must not fall back to a cloud provider")

    monkeypatch.setattr(route, "probe_local_capability", failed_probe)
    monkeypatch.setattr(route, "OpenAICompatibleTranscriptionProvider", unexpected_provider)
    monkeypatch.setattr(route, "build_transcription_provider", unexpected_cloud_fallback)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        binding = await client.put(
            "/api/model-connections/roles/transcriber",
            json={"local_capability_key": "sensevoice-small"},
        )
        response = await client.post(
            "/api/transcriptions",
            files={"file": ("answer.webm", b"recorded-answer", "audio/webm")},
        )

    assert binding.status_code == 200
    assert response.status_code == 503
    assert response.json()["code"] == expected_code
    assert response.json()["retryable"] is True
    assert "SenseVoice" in response.json()["message"]
