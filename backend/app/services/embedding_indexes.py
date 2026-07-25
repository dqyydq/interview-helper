"""Read models for the privacy-safe embedding-index settings API.

This module deliberately performs database reads only.  In particular, a
status refresh must not instantiate an embedding provider, probe Docker, or
send source text to a cloud endpoint.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.common import EmbeddingProfileStatus, JobType
from app.db.models.embedding import EmbeddingProfile
from app.db.models.job import BackgroundJob
from app.memory.embedding_index import profile_has_interviewing_session
from app.schemas.embedding_index import (
    EmbeddingIndexJobPublic,
    EmbeddingIndexStatusPublic,
    EmbeddingProfilePublic,
)

_SAFE_JOB_PHASES = frozenset(
    {
        "queued",
        "resolving_target",
        "indexing_memories",
        "indexing_plan_question",
        "reconciling",
        "waiting_for_interview",
        "retry_wait",
        "completed",
        "failed",
    }
)


def embedding_profile_public(profile: EmbeddingProfile) -> EmbeddingProfilePublic:
    """Project a persisted profile without target IDs or its fingerprint."""

    target_kind = (
        "model_connection" if profile.model_connection_id is not None else "local_capability"
    )
    return EmbeddingProfilePublic(
        id=profile.id,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        version=profile.version,
        target_kind=target_kind,
        model_name=profile.model_name,
        model_revision=profile.model_revision,
        vector_dimensions=profile.vector_dimensions,
        normalized=profile.normalized,
        distance_metric=profile.distance_metric,
        status=profile.status,
        activated_at=profile.activated_at,
        failed_at=profile.failed_at,
        failure_code=profile.failure_code,
        failure_summary=profile.failure_summary,
    )


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _safe_dimensions(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 2_000:
        return value
    return None


def embedding_index_job_public(job: BackgroundJob) -> EmbeddingIndexJobPublic:
    """Whitelist result metadata; never return a generic JSONB ``result`` field."""

    result = job.result if isinstance(job.result, dict) else {}
    raw_phase = result.get("phase")
    phase = raw_phase if isinstance(raw_phase, str) and raw_phase in _SAFE_JOB_PHASES else "queued"
    return EmbeddingIndexJobPublic(
        id=job.id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        version=job.version,
        status=job.status,
        progress=min(1.0, max(0.0, float(job.progress))),
        phase=phase,
        memory_scanned=_safe_nonnegative_int(result.get("memory_scanned")),
        memory_embeddings=_safe_nonnegative_int(result.get("memory_embeddings")),
        plan_question_scanned=_safe_nonnegative_int(result.get("plan_question_scanned")),
        plan_question_embeddings=_safe_nonnegative_int(result.get("plan_question_embeddings")),
        vector_dimensions=_safe_dimensions(result.get("vector_dimensions")),
        error_code=job.error_code,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        available_at=job.available_at,
    )


def _job_targets_profile(job: BackgroundJob, embedding_profile_id: uuid.UUID) -> bool:
    """Correlate a safe scheduler payload with an immutable profile ID."""

    return str(job.payload.get("embedding_profile_id", "")) == str(embedding_profile_id)


async def get_embedding_index_status(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> EmbeddingIndexStatusPublic:
    """Return served/rebuilding index state using database reads only."""

    profiles = list(
        (
            await session.scalars(
                select(EmbeddingProfile)
                .where(
                    EmbeddingProfile.profile_id == profile_id,
                    EmbeddingProfile.deleted_at.is_(None),
                )
                .order_by(EmbeddingProfile.created_at.desc(), EmbeddingProfile.id.desc())
            )
        ).all()
    )
    active = next(
        (item for item in profiles if item.status == EmbeddingProfileStatus.ACTIVE),
        None,
    )
    building = next(
        (item for item in profiles if item.status == EmbeddingProfileStatus.BUILDING),
        None,
    )
    failed = next(
        (item for item in profiles if item.status == EmbeddingProfileStatus.FAILED),
        None,
    )

    # ``latest_failed_profile`` is retained as useful history, but the job in
    # this response must describe the latest *attempt*.  Otherwise, when a
    # new rebuild fails while an older ACTIVE index remains available, the UI
    # would correlate the completed old job and mask the failure/retry state.
    latest_profile = profiles[0] if profiles else None
    current_failure = (
        failed
        if latest_profile is not None
        and failed is not None
        and latest_profile.id == failed.id
        else None
    )

    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob)
                .where(
                    BackgroundJob.profile_id == profile_id,
                    BackgroundJob.job_type == JobType.EMBEDDING_REINDEX,
                    BackgroundJob.deleted_at.is_(None),
                )
                .order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
                .limit(20)
            )
        ).all()
    )
    tracked_profile = building or current_failure or active
    job = (
        next(
            (item for item in jobs if _job_targets_profile(item, tracked_profile.id)),
            None,
        )
        if tracked_profile is not None
        else None
    )

    return EmbeddingIndexStatusPublic(
        active_profile=embedding_profile_public(active) if active else None,
        building_profile=embedding_profile_public(building) if building else None,
        latest_failed_profile=embedding_profile_public(failed) if failed else None,
        job=embedding_index_job_public(job) if job else None,
        interview_active=await profile_has_interviewing_session(session, profile_id),
    )
