import os
import uuid
from datetime import datetime, timedelta

import anyio
import structlog
from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.security import upload_root
from app.db.models.common import JobStatus, ModelRole, utc_now
from app.db.models.job import BackgroundJob
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.models.worker import WorkerHeartbeat
from app.db.session import database_healthcheck
from app.schemas.diagnostics import DiagnosticBundle, DiagnosticSnapshot
from app.services.model_connections import ensure_local_profile, model_readiness

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])
logger = structlog.get_logger(__name__)


def _file_status() -> dict:
    root = upload_root()
    if not root.exists():
        return {
            "configured": True,
            "exists": False,
            "writable": False,
            "file_count": 0,
            "total_bytes": 0,
        }
    file_count = 0
    total_bytes = 0
    try:
        for item in root.rglob("*"):
            if item.is_file():
                file_count += 1
                total_bytes += item.stat().st_size
    except OSError:
        return {
            "configured": True,
            "exists": True,
            "writable": False,
            "file_count": file_count,
            "total_bytes": total_bytes,
        }
    return {
        "configured": True,
        "exists": True,
        "writable": os.access(root, os.W_OK),
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _worker_liveness(
    heartbeats: list[WorkerHeartbeat],
    *,
    stale_before: datetime,
    stale_running_jobs: int,
    recent_failed_jobs: int,
) -> dict:
    """Turn durable heartbeats into a truthful, content-free diagnostics summary."""

    ordered = sorted(heartbeats, key=lambda worker: worker.last_seen_at, reverse=True)
    active_workers = [
        worker
        for worker in ordered
        if worker.state != "stopped" and worker.last_seen_at >= stale_before
    ]
    stale_workers = [
        worker
        for worker in ordered
        if worker.state != "stopped" and worker.last_seen_at < stale_before
    ]
    recent_worker_errors = [
        worker
        for worker in active_workers
        if worker.last_error_at is not None and worker.last_error_at >= stale_before
    ]
    latest_worker = (active_workers or ordered)[0] if ordered else None
    if active_workers:
        state = (
            "degraded"
            if stale_running_jobs or stale_workers or recent_worker_errors or recent_failed_jobs
            else "healthy"
        )
    elif stale_workers:
        state = "stale"
    else:
        state = "not_running"
    return {
        "state": state,
        "active_workers": len(active_workers),
        "stale_workers": len(stale_workers),
        "recent_worker_errors": len(recent_worker_errors),
        "last_heartbeat_at": latest_worker.last_seen_at if latest_worker else None,
        "last_job_type": latest_worker.last_job_type if latest_worker else None,
        "last_error_type": latest_worker.last_error_type if latest_worker else None,
        "last_error_at": latest_worker.last_error_at if latest_worker else None,
    }


async def _unavailable_snapshot() -> DiagnosticSnapshot:
    """Return a useful diagnostics payload even when PostgreSQL cannot be queried."""

    files = await anyio.to_thread.run_sync(_file_status)
    return DiagnosticSnapshot(
        generated_at=utc_now(),
        application={
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
        },
        database={"status": "unavailable"},
        worker={
            "state": "unavailable",
            "job_counts": {},
            "stale_running_jobs": 0,
            "recent_failed_jobs": 0,
            "active_workers": 0,
            "stale_workers": 0,
            "recent_worker_errors": 0,
            "last_heartbeat_at": None,
            "last_job_type": None,
            "last_error_type": None,
            "last_error_at": None,
            "heartbeat_stale_after_seconds": settings.worker_heartbeat_stale_after_seconds,
        },
        models={
            "status": "unavailable",
            "connection_count": 0,
            "binding_count": 0,
            "status_counts": {},
            "required_ready": False,
            "missing_required_roles": [],
            "degraded_required_roles": [],
            "transcriber_configured": False,
        },
        files=files,
        privacy={
            "redaction_applied": True,
            "contains_secrets": False,
            "contains_answer_content": False,
            "contains_local_paths": False,
        },
    )


async def _snapshot(session: SessionDep) -> DiagnosticSnapshot:
    database_status = await database_healthcheck()
    if database_status != "connected":
        return await _unavailable_snapshot()
    profile = await ensure_local_profile(session)
    job_rows = (
        await session.execute(
            select(BackgroundJob.status, func.count(BackgroundJob.id))
            .where(BackgroundJob.deleted_at.is_(None))
            .group_by(BackgroundJob.status)
        )
    ).all()
    job_counts = {str(status): int(count) for status, count in job_rows}
    now = utc_now()
    stale_before = now - timedelta(seconds=settings.worker_heartbeat_stale_after_seconds)
    stale_running = int(
        await session.scalar(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.status == JobStatus.RUNNING,
                or_(
                    BackgroundJob.locked_at.is_(None),
                    BackgroundJob.locked_at < stale_before,
                ),
                BackgroundJob.deleted_at.is_(None),
            )
        )
        or 0
    )
    recent_failed_jobs = int(
        await session.scalar(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.status == JobStatus.FAILED,
                BackgroundJob.updated_at >= stale_before,
                BackgroundJob.deleted_at.is_(None),
            )
        )
        or 0
    )
    heartbeat_rows = list(
        (
            await session.scalars(
                select(WorkerHeartbeat)
                .where(WorkerHeartbeat.deleted_at.is_(None))
                .order_by(WorkerHeartbeat.last_seen_at.desc())
            )
        ).all()
    )
    worker = _worker_liveness(
        heartbeat_rows,
        stale_before=stale_before,
        stale_running_jobs=stale_running,
        recent_failed_jobs=recent_failed_jobs,
    )
    model_rows = (
        await session.execute(
            select(ModelConnection.status, func.count(ModelConnection.id))
            .where(
                ModelConnection.profile_id == profile.id,
                ModelConnection.deleted_at.is_(None),
            )
            .group_by(ModelConnection.status)
        )
    ).all()
    connection_count = sum(int(count) for _, count in model_rows)
    binding_count = int(
        await session.scalar(
            select(func.count(ModelRoleBinding.id)).where(
                ModelRoleBinding.profile_id == profile.id,
                ModelRoleBinding.deleted_at.is_(None),
            )
        )
        or 0
    )
    readiness = await model_readiness(session, profile.id)
    files = await anyio.to_thread.run_sync(_file_status)
    transcriber_binding_count = int(
        await session.scalar(
            select(func.count(ModelRoleBinding.id)).where(
                ModelRoleBinding.profile_id == profile.id,
                ModelRoleBinding.role == ModelRole.TRANSCRIBER,
                ModelRoleBinding.deleted_at.is_(None),
            )
        )
        or 0
    )
    return DiagnosticSnapshot(
        generated_at=utc_now(),
        application={
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
        },
        database={"status": database_status},
        worker={
            **worker,
            "job_counts": job_counts,
            "stale_running_jobs": stale_running,
            "recent_failed_jobs": recent_failed_jobs,
            "heartbeat_stale_after_seconds": settings.worker_heartbeat_stale_after_seconds,
        },
        models={
            "connection_count": connection_count,
            "binding_count": binding_count,
            "status_counts": {str(status): int(count) for status, count in model_rows},
            "required_ready": readiness.ready,
            "missing_required_roles": [str(role) for role in readiness.missing_roles],
            "degraded_required_roles": [str(role) for role in readiness.degraded_roles],
            "transcriber_configured": transcriber_binding_count > 0,
        },
        files=files,
        privacy={
            "redaction_applied": True,
            "contains_secrets": False,
            "contains_answer_content": False,
            "contains_local_paths": False,
        },
    )


async def _safe_snapshot(session: SessionDep) -> DiagnosticSnapshot:
    try:
        return await _snapshot(session)
    except Exception as exc:  # Diagnostics must remain available during database failures.
        await logger.awarning(
            "diagnostics_database_query_failed",
            error_type=type(exc).__name__,
        )
        return await _unavailable_snapshot()


@router.get("", response_model=DiagnosticSnapshot)
async def get_diagnostics(session: SessionDep) -> DiagnosticSnapshot:
    return await _safe_snapshot(session)


@router.get("/bundle", response_model=DiagnosticBundle)
async def get_diagnostic_bundle(
    request: Request,
    session: SessionDep,
) -> DiagnosticBundle:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return DiagnosticBundle(request_id=request_id, snapshot=await _safe_snapshot(session))
