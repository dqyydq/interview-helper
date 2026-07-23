import uuid

import pytest
from sqlalchemy import delete, select

from app.db.models.worker import WorkerHeartbeat
from app.db.session import async_session_factory, engine
from app.workers import run as worker_run
from app.workers.heartbeat import WorkerRuntime, publish_worker_runtime


async def _delete_heartbeat(worker_id: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id))
        await session.commit()


@pytest.mark.asyncio
async def test_heartbeat_persists_only_exception_type() -> None:
    worker_id = f"heartbeat-persist-{uuid.uuid4().hex}"
    private_answer = "candidate answer api_key=not-for-diagnostics"
    runtime = WorkerRuntime()
    runtime.record_error(RuntimeError(private_answer))
    try:
        heartbeat = await publish_worker_runtime(worker_id, runtime)
        assert heartbeat.last_error_type == "RuntimeError"

        async with async_session_factory() as session:
            stored = await session.scalar(
                select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id)
            )
        assert stored is not None
        assert stored.last_error_type == "RuntimeError"
        assert private_answer not in str(stored)
    finally:
        await _delete_heartbeat(worker_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_runner_records_a_guarded_exception_without_persisting_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = f"heartbeat-runner-{uuid.uuid4().hex}"
    private_answer = "candidate answer api_key=not-for-diagnostics"

    async def fail_next_job(*_args: object) -> bool:
        raise RuntimeError(private_answer)

    monkeypatch.setattr(worker_run, "_run_next_job", fail_next_job)
    try:
        await worker_run.async_main(once=True, worker_id=worker_id)

        async with async_session_factory() as session:
            stored = await session.scalar(
                select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id)
            )
        assert stored is not None
        assert stored.state == "stopped"
        assert stored.last_error_type == "RuntimeError"
        assert private_answer not in str(stored)
    finally:
        await _delete_heartbeat(worker_id)
        await engine.dispose()
