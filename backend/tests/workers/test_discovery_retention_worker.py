import pytest

from app.workers import run as worker_run
from app.workers.heartbeat import WorkerRuntime


@pytest.mark.asyncio
async def test_worker_runner_processes_due_discovery_retention_before_queued_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = WorkerRuntime()
    calls: list[bool] = []

    async def cleanup() -> int:
        calls.append(True)
        return 1

    monkeypatch.setattr(worker_run, "run_discovery_retention_once", cleanup)

    processed = await worker_run._run_next_job("retention-test-worker", runtime)

    assert processed is True
    assert calls == [True]
    assert runtime.last_job_type == "discovery_retention"
    assert runtime.last_discovery_cleanup_at is not None
