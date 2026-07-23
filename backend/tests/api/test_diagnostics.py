import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.api.routes import diagnostics as diagnostics_route
from app.db.models.common import utc_now
from app.db.models.worker import WorkerHeartbeat
from app.db.session import async_session_factory, engine
from app.main import app
from app.workers.heartbeat import record_worker_heartbeat


@pytest.mark.asyncio
async def test_diagnostic_bundle_contains_only_redacted_operational_state() -> None:
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/diagnostics/bundle")

        assert response.status_code == 200
        body = response.json()
        assert body["snapshot"]["database"]["status"] in {"connected", "unavailable"}
        assert body["snapshot"]["privacy"] == {
            "redaction_applied": True,
            "contains_secrets": False,
            "contains_answer_content": False,
            "contains_local_paths": False,
        }
        rendered = response.text.lower()
        assert "database_url" not in rendered
        assert "encrypted_api_key" not in rendered
        assert "storage_path" not in rendered
        assert "parsed_text" not in rendered
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_returns_a_safe_snapshot_when_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable() -> str:
        return "unavailable"

    async def profile_lookup_must_not_run(*_args: object) -> object:
        raise AssertionError("profile lookup should not run after a failed health check")

    monkeypatch.setattr(diagnostics_route, "database_healthcheck", unavailable)
    monkeypatch.setattr(diagnostics_route, "ensure_local_profile", profile_lookup_must_not_run)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/diagnostics/bundle")

        assert response.status_code == 200
        snapshot = response.json()["snapshot"]
        assert snapshot["database"] == {"status": "unavailable"}
        assert snapshot["worker"]["state"] == "unavailable"
        assert snapshot["models"]["status"] == "unavailable"
        assert snapshot["privacy"]["contains_answer_content"] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_masks_a_database_query_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_error = "candidate answer api_key=not-for-diagnostics"

    async def connected() -> str:
        return "connected"

    async def broken_profile_lookup(*_args: object) -> object:
        raise RuntimeError(private_error)

    monkeypatch.setattr(diagnostics_route, "database_healthcheck", connected)
    monkeypatch.setattr(diagnostics_route, "ensure_local_profile", broken_profile_lookup)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/diagnostics")

        assert response.status_code == 200
        assert response.json()["database"] == {"status": "unavailable"}
        assert private_error not in response.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_exposes_a_live_heartbeat_without_content() -> None:
    worker_id = f"diagnostics-live-{uuid.uuid4().hex}"
    private_answer = "candidate answer api_key=not-for-diagnostics"
    try:
        await record_worker_heartbeat(
            worker_id,
            state="idle",
            last_job_type="plan_generation",
        )
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/diagnostics")

        assert response.status_code == 200
        worker = response.json()["worker"]
        assert worker["active_workers"] >= 1
        assert worker["last_heartbeat_at"] is not None
        assert worker["heartbeat_stale_after_seconds"] > 0
        assert private_answer not in response.text
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id)
            )
            await session.commit()
        await engine.dispose()


def test_worker_liveness_distinguishes_healthy_stale_and_stopped_workers() -> None:
    now = utc_now()
    stale_before = now - timedelta(seconds=15)
    healthy = WorkerHeartbeat(
        worker_id="worker-healthy",
        state="idle",
        last_seen_at=now,
        last_job_type="plan_generation",
    )
    healthy_summary = diagnostics_route._worker_liveness(
        [healthy],
        stale_before=stale_before,
        stale_running_jobs=0,
        recent_failed_jobs=0,
    )
    assert healthy_summary["state"] == "healthy"
    assert healthy_summary["active_workers"] == 1
    assert healthy_summary["last_job_type"] == "plan_generation"

    stale = WorkerHeartbeat(
        worker_id="worker-stale",
        state="processing",
        last_seen_at=stale_before - timedelta(seconds=1),
    )
    stale_summary = diagnostics_route._worker_liveness(
        [stale],
        stale_before=stale_before,
        stale_running_jobs=0,
        recent_failed_jobs=0,
    )
    assert stale_summary["state"] == "stale"
    assert stale_summary["active_workers"] == 0
    assert stale_summary["stale_workers"] == 1

    stopped = WorkerHeartbeat(
        worker_id="worker-stopped",
        state="stopped",
        last_seen_at=now,
    )
    stopped_summary = diagnostics_route._worker_liveness(
        [stopped],
        stale_before=stale_before,
        stale_running_jobs=0,
        recent_failed_jobs=0,
    )
    assert stopped_summary["state"] == "not_running"


def test_worker_liveness_surfaces_recent_errors_without_error_content() -> None:
    now = utc_now()
    secret_answer = "candidate answer with api_key=secret-that-must-not-persist"
    heartbeat = WorkerHeartbeat(
        worker_id="worker-degraded",
        state="degraded",
        last_seen_at=now,
        last_error_type="RuntimeError",
        last_error_at=now,
    )

    summary = diagnostics_route._worker_liveness(
        [heartbeat],
        stale_before=now - timedelta(seconds=15),
        stale_running_jobs=0,
        recent_failed_jobs=0,
    )

    assert summary["state"] == "degraded"
    assert summary["recent_worker_errors"] == 1
    assert summary["last_error_type"] == "RuntimeError"
    assert secret_answer not in str(summary)
