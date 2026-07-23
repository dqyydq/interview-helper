"""Durable, privacy-safe liveness signals for background worker processes."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.db.models.common import utc_now
from app.db.models.worker import WorkerHeartbeat
from app.db.session import async_session_factory

WORKER_STARTING = "starting"
WORKER_IDLE = "idle"
WORKER_PROCESSING = "processing"
WORKER_DEGRADED = "degraded"
WORKER_STOPPED = "stopped"


@dataclass
class WorkerRuntime:
    """In-process state periodically persisted by the heartbeat task."""

    state: str = WORKER_STARTING
    last_job_type: str | None = None
    last_error_type: str | None = None
    last_error_at: datetime | None = None

    def record_error(self, error: BaseException) -> None:
        self.state = WORKER_DEGRADED
        self.last_error_type = type(error).__name__
        self.last_error_at = utc_now()


async def record_worker_heartbeat(
    worker_id: str,
    *,
    state: str,
    last_job_type: str | None = None,
    last_error_type: str | None = None,
    last_error_at: datetime | None = None,
) -> WorkerHeartbeat:
    """Upsert one worker's liveness record without recording job payloads or errors."""

    now = utc_now()
    async with async_session_factory() as session:
        heartbeat = await session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id).with_for_update()
        )
        if heartbeat is None:
            heartbeat = WorkerHeartbeat(
                worker_id=worker_id,
                state=state,
                started_at=now,
                last_seen_at=now,
                last_job_type=last_job_type,
                last_error_type=last_error_type,
                last_error_at=last_error_at,
            )
            session.add(heartbeat)
        else:
            heartbeat.state = state
            heartbeat.last_seen_at = now
            if last_job_type is not None:
                heartbeat.last_job_type = last_job_type
            if last_error_type is not None:
                heartbeat.last_error_type = last_error_type
                heartbeat.last_error_at = last_error_at or now
            heartbeat.touch(at=now)
        await session.commit()
        await session.refresh(heartbeat)
        return heartbeat


async def publish_worker_runtime(worker_id: str, runtime: WorkerRuntime) -> WorkerHeartbeat:
    return await record_worker_heartbeat(
        worker_id,
        state=runtime.state,
        last_job_type=runtime.last_job_type,
        last_error_type=runtime.last_error_type,
        last_error_at=runtime.last_error_at,
    )
