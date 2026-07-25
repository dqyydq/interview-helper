import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import local_ai as local_ai_route
from app.main import app
from app.schemas.local_ai import (
    DockerComponentDiagnostics,
    DockerComponentState,
    DockerEngineDiagnostics,
    LocalAiCapability,
    LocalAiCapabilityStatus,
    LocalAiDockerDiagnostics,
    LocalAiDockerDiagnosticsStatus,
)


@pytest.mark.asyncio
async def test_local_ai_preset_catalog_is_public_and_static() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/local-ai/presets")

    assert response.status_code == 200
    body = response.json()
    assert body["catalog_version"] == 1
    assert {preset["key"] for preset in body["presets"]} == {
        "multilingual-e5-small",
        "bge-m3",
        "sensevoice-small",
    }
    assert all("local_path" not in preset for preset in body["presets"])


@pytest.mark.asyncio
async def test_local_ai_capabilities_use_fixed_catalog_and_bounded_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_probe_all(*, timeout_seconds: float) -> list[LocalAiCapability]:
        assert timeout_seconds > 0
        return [
            LocalAiCapability(
                key="sensevoice-small",
                role="transcriber",
                title="Local ASR",
                summary="fixed loopback target",
                runtime="funasr",
                compose_profile="local-asr",
                model_name="sensevoice-small",
                revision="a" * 40,
                vector_dimensions=None,
                status=LocalAiCapabilityStatus.UNAVAILABLE,
                latency_ms=2,
                error_code="provider_connection_failed",
            )
        ]

    monkeypatch.setattr(local_ai_route, "probe_all_local_capabilities", fake_probe_all)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/local-ai/capabilities")

    assert response.status_code == 200
    assert response.json() == [
        {
            "key": "sensevoice-small",
            "role": "transcriber",
            "title": "Local ASR",
            "summary": "fixed loopback target",
            "runtime": "funasr",
            "compose_profile": "local-asr",
            "model_name": "sensevoice-small",
            "revision": "a" * 40,
            "vector_dimensions": None,
            "status": "unavailable",
            "latency_ms": 2,
            "error_code": "provider_connection_failed",
        }
    ]
    assert "127.0.0.1" not in response.text
    assert "local_path" not in response.text


@pytest.mark.asyncio
async def test_single_local_capability_probe_rejects_unknown_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_probe(capability: object, *, timeout_seconds: float) -> LocalAiCapability:
        assert timeout_seconds > 0
        return LocalAiCapability(
            key="sensevoice-small",
            role="transcriber",
            title="Local ASR",
            summary="fixed loopback target",
            runtime="funasr",
            compose_profile="local-asr",
            model_name="sensevoice-small",
            revision="a" * 40,
            vector_dimensions=None,
            status=LocalAiCapabilityStatus.READY,
            latency_ms=1,
            error_code=None,
        )

    monkeypatch.setattr(local_ai_route, "probe_local_capability", fake_probe)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        ready = await client.post("/api/local-ai/capabilities/sensevoice-small/test")
        unknown = await client.post("/api/local-ai/capabilities/not-a-preset/test")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "local_capability_not_found"


@pytest.mark.asyncio
async def test_local_ai_docker_diagnostics_does_not_run_real_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, float] = {}

    def fake_collect(*, timeout_seconds: float, cache_seconds: float) -> LocalAiDockerDiagnostics:
        captured["timeout_seconds"] = timeout_seconds
        captured["cache_seconds"] = cache_seconds
        return LocalAiDockerDiagnostics(
            checked_at="2026-07-25T00:00:00Z",
            status=LocalAiDockerDiagnosticsStatus.DEGRADED,
            docker=DockerComponentDiagnostics(state=DockerComponentState.AVAILABLE),
            compose=DockerComponentDiagnostics(state=DockerComponentState.UNAVAILABLE),
            engine=DockerEngineDiagnostics(
                state=DockerComponentState.NOT_CHECKED,
                container_os="unknown",
                locality="unknown",
            ),
            compose_project=DockerComponentDiagnostics(state=DockerComponentState.NOT_CHECKED),
            gpu_check="not_checked",
            next_step="enable_docker_compose",
        )

    monkeypatch.setattr(local_ai_route, "collect_docker_diagnostics", fake_collect)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/local-ai/docker-diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert captured["timeout_seconds"] > 0
    assert captured["cache_seconds"] >= 0
    assert body["status"] == "degraded"
    assert body["docker"] == {"state": "available"}
    assert body["compose"] == {"state": "unavailable"}
    assert body["engine"] == {
        "state": "not_checked",
        "container_os": "unknown",
        "locality": "unknown",
    }
    assert "stderr" not in response.text
    assert "stdout" not in response.text
    assert "local_path" not in response.text
