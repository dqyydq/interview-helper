import argparse
import uuid
from datetime import timedelta

import anyio
import structlog

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.models.common import utc_now
from app.db.session import dispose_engine
from app.workers.context_summary_jobs import run_once as run_summary_once
from app.workers.discovery_jobs import run_once as run_discovery_once
from app.workers.discovery_retention import run_once as run_discovery_retention_once
from app.workers.evaluation_jobs import run_once as run_evaluation_once
from app.workers.heartbeat import (
    WORKER_IDLE,
    WORKER_PROCESSING,
    WORKER_STOPPED,
    WorkerRuntime,
    publish_worker_runtime,
)
from app.workers.plan_jobs import run_once as run_plan_once
from app.workers.resume_jobs import run_once as run_resume_once

logger = structlog.get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Interview Helper background jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    return parser.parse_args()


async def _publish_runtime_safely(worker_id: str, runtime: WorkerRuntime) -> None:
    try:
        await publish_worker_runtime(worker_id, runtime)
    except Exception as exc:  # A database outage must not turn a liveness write into a crash loop.
        await logger.awarning(
            "worker_heartbeat_write_failed",
            worker_id=worker_id,
            error_type=type(exc).__name__,
        )


async def _heartbeat_loop(
    worker_id: str,
    runtime: WorkerRuntime,
    stopped: anyio.Event,
) -> None:
    while not stopped.is_set():
        await _publish_runtime_safely(worker_id, runtime)
        with anyio.move_on_after(settings.worker_heartbeat_interval_seconds):
            await stopped.wait()


async def _run_next_job(worker_id: str, runtime: WorkerRuntime) -> bool:
    now = utc_now()
    if (
        runtime.last_discovery_cleanup_at is None
        or now - runtime.last_discovery_cleanup_at
        >= timedelta(seconds=settings.discovery_cleanup_interval_seconds)
    ):
        runtime.state = WORKER_PROCESSING
        removed = await run_discovery_retention_once()
        runtime.last_discovery_cleanup_at = now
        if removed:
            runtime.last_job_type = "discovery_retention"
            runtime.state = WORKER_IDLE
            return True

    runners = (
        ("interview_evaluation", run_evaluation_once),
        ("context_summary", run_summary_once),
        ("resume_parse", run_resume_once),
        ("plan_generation", run_plan_once),
        ("question_discovery", run_discovery_once),
    )
    for job_type, runner in runners:
        runtime.state = WORKER_PROCESSING
        if await runner(worker_id):
            runtime.last_job_type = job_type
            runtime.state = WORKER_IDLE
            return True
    runtime.state = WORKER_IDLE
    return False


async def _worker_loop(worker_id: str, runtime: WorkerRuntime, *, once: bool) -> None:
    while True:
        try:
            processed = await _run_next_job(worker_id, runtime)
        except Exception as exc:
            # Job-specific failures are handled by their runner; this is the process guard.
            runtime.record_error(exc)
            await logger.aexception(
                "worker_cycle_failed",
                worker_id=worker_id,
                error_type=type(exc).__name__,
            )
            if once:
                return
            await anyio.sleep(settings.job_poll_interval_seconds)
            continue
        if once:
            return
        if not processed:
            await anyio.sleep(settings.job_poll_interval_seconds)


async def async_main(once: bool, *, worker_id: str | None = None) -> None:
    configure_logging()
    worker_id = worker_id or f"local-{uuid.uuid4().hex[:10]}"
    runtime = WorkerRuntime()
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(worker_id=worker_id)
    await _publish_runtime_safely(worker_id, runtime)
    await logger.ainfo("worker_started", worker_id=worker_id)
    try:
        async with anyio.create_task_group() as task_group:
            stopped = anyio.Event()
            task_group.start_soon(_heartbeat_loop, worker_id, runtime, stopped)
            try:
                await _worker_loop(worker_id, runtime, once=once)
            finally:
                stopped.set()
    finally:
        runtime.state = WORKER_STOPPED
        await _publish_runtime_safely(worker_id, runtime)
        await logger.ainfo("worker_stopped", worker_id=worker_id)
        await dispose_engine()
        structlog.contextvars.clear_contextvars()


def main() -> None:
    args = parse_args()
    anyio.run(async_main, args.once)


if __name__ == "__main__":
    main()
