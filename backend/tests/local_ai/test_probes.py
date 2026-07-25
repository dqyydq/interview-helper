import httpx
import pytest

from app.local_ai import probes
from app.local_ai.capabilities import get_local_capability
from app.schemas.local_ai import LocalAiCapabilityStatus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (
            {
                "status": "ready",
                "model": "sensevoice-small",
                "revision": "43d0ed61231c41f8393fa347b838a1f6e2d264f6",
            },
            LocalAiCapabilityStatus.READY,
        ),
        (
            {
                "status": "ready",
                "model": "sensevoice-small",
                "revision": "0" * 40,
            },
            LocalAiCapabilityStatus.MISMATCH,
        ),
        (
            {
                "status": "ready",
                "model": "unexpected-asr",
                "revision": "43d0ed61231c41f8393fa347b838a1f6e2d264f6",
            },
            LocalAiCapabilityStatus.MISMATCH,
        ),
        (
            {
                "status": "loading",
                "model": "sensevoice-small",
                "revision": "43d0ed61231c41f8393fa347b838a1f6e2d264f6",
            },
            LocalAiCapabilityStatus.MISMATCH,
        ),
    ],
)
async def test_sensevoice_probe_requires_exact_health_model_and_revision(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
    expected_status: LocalAiCapabilityStatus,
) -> None:
    capability = get_local_capability("sensevoice-small")
    assert capability is not None
    captured: dict[str, object] = {}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            captured["url"] = url
            return httpx.Response(200, json=payload)

    def fake_async_client(*, trust_env: bool, timeout: float) -> FakeClient:
        captured["trust_env"] = trust_env
        captured["timeout"] = timeout
        return FakeClient()

    monkeypatch.setattr(probes.httpx, "AsyncClient", fake_async_client)

    result = await probes.probe_local_capability(capability, timeout_seconds=0.25)

    assert result.status == expected_status
    assert captured == {
        "trust_env": False,
        "timeout": 0.25,
        "url": capability.health_url,
    }
    if expected_status == LocalAiCapabilityStatus.READY:
        assert result.error_code is None
    else:
        assert result.error_code == "local_asr_mismatch"


@pytest.mark.asyncio
async def test_bulk_probe_uses_one_shared_embedding_request_and_short_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes.invalidate_local_capability_probe_cache()
    calls = {"asr": 0, "embedding": 0}

    async def fake_asr_probe(
        capability: object,
        *,
        timeout_seconds: float,
    ) -> object:
        assert timeout_seconds == 0.25
        calls["asr"] += 1
        return probes._public(  # noqa: SLF001 - checks the public bulk behavior deterministically.
            capability,  # type: ignore[arg-type]
            status=LocalAiCapabilityStatus.READY,
            latency_ms=1,
            error_code=None,
        )

    async def fake_embedding_probe(
        capability: object,
        *,
        timeout_seconds: float,
    ) -> tuple[int, int, None]:
        assert timeout_seconds == 0.25
        calls["embedding"] += 1
        return 384, 2, None

    monkeypatch.setattr(probes, "probe_local_capability", fake_asr_probe)
    monkeypatch.setattr(probes, "_probe_embedding_endpoint", fake_embedding_probe)

    first = await probes.probe_all_local_capabilities(
        timeout_seconds=0.25,
        cache_seconds=10,
    )
    second = await probes.probe_all_local_capabilities(
        timeout_seconds=0.25,
        cache_seconds=10,
    )

    assert [item.key for item in first] == [
        "sensevoice-small",
        "multilingual-e5-small",
        "bge-m3",
    ]
    assert first[1].status == LocalAiCapabilityStatus.READY
    assert first[2].status == LocalAiCapabilityStatus.MISMATCH
    assert second == first
    assert calls == {"asr": 1, "embedding": 1}
