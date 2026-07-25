"""Background execution for immutable embedding-profile rebuilds.

The worker intentionally yields whenever a profile has a live interview in the
``INTERVIEWING`` state.  It does not wait while holding a worker slot: the job
is returned to the queue with a short delay, so interview latency wins over
memory-index throughput.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.db.models.common import EmbeddingProfileStatus, JobStatus, JobType, utc_now
from app.db.models.embedding import EmbeddingProfile
from app.db.models.job import BackgroundJob
from app.db.session import async_session_factory
from app.memory.embedding_index import (
    EmbeddingBatch,
    build_embedding_provider_for_target,
    current_sources_need_embeddings,
    documents_needing_embeddings,
    embed_dimension_probe,
    mark_embedding_profile_failed,
    next_memory_embedding_batch,
    next_plan_question_embedding_batch,
    persist_embedding_batch,
    persist_verified_embedding_dimensions,
    profile_has_interviewing_session,
    promote_embedding_profile,
    source_counts_for_profile,
    validated_vectors_for_documents,
    verified_embedding_target_for_profile,
)
from app.providers.base import ProviderError
from app.providers.embedding_base import EmbeddingProvider
from app.providers.types import EmbeddingRequest

logger = structlog.get_logger(__name__)

INTERVIEW_YIELD_DELAY_SECONDS = 10
MAX_RECONCILIATION_PASSES = 3


class EmbeddingJobPaused(Exception):
    """Internal control flow: the job was safely requeued for a live interview."""


@dataclass(frozen=True, slots=True)
class EmbeddingJobContext:
    job_id: uuid.UUID
    profile_id: uuid.UUID
    embedding_profile_id: uuid.UUID
    target_fingerprint: str
    normalized: bool
    vector_dimensions: int | None


@dataclass(slots=True)
class EmbeddingProgress:
    memory_scanned: int = 0
    memory_embedded: int = 0
    plan_question_scanned: int = 0
    plan_question_embedded: int = 0

    @property
    def scanned(self) -> int:
        return self.memory_scanned + self.plan_question_scanned


def _safe_result(
    *,
    phase: str,
    progress: EmbeddingProgress,
    vector_dimensions: int | None = None,
) -> dict[str, int | str]:
    """Return UI-safe job state; never put source text or vectors in JSONB."""

    result: dict[str, int | str] = {
        "phase": phase,
        "memory_scanned": progress.memory_scanned,
        "memory_embeddings": progress.memory_embedded,
        "plan_question_scanned": progress.plan_question_scanned,
        "plan_question_embeddings": progress.plan_question_embedded,
    }
    if vector_dimensions is not None:
        result["vector_dimensions"] = vector_dimensions
    return result


def _safe_failure_message(code: str, *, retryable: bool) -> str:
    if code == "embedding_target_changed":
        return "向量模型配置已变化；请重新创建向量索引。"
    if code == "embedding_provider_unsupported":
        return "当前模型连接不支持向量检索。"
    if retryable:
        return "向量索引暂时无法完成，任务将自动重试。"
    return "向量索引未能完成；请检查嵌入服务和模型配置后重试。"


def _embedding_job_lease_seconds() -> float:
    """Return a conservative lease for one bounded embedding batch.

    The provider has a 20-second HTTP timeout and each worker batch contains
    only two short documents.  ``locked_at`` is renewed after every persisted
    batch, so this lease recovers a killed worker without ever reclaiming a
    healthy long-running rebuild merely because it has many batches.
    """

    return max(90.0, settings.worker_heartbeat_stale_after_seconds * 4)


async def _recover_stale_embedding_jobs(session: AsyncSession) -> int:
    """Resume safely recoverable lost jobs and fail repeatedly abandoned ones."""

    now = utc_now()
    stale_before = now - timedelta(seconds=_embedding_job_lease_seconds())
    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob)
                .where(
                    BackgroundJob.job_type == JobType.EMBEDDING_REINDEX,
                    BackgroundJob.status == JobStatus.RUNNING,
                    or_(
                        BackgroundJob.locked_at.is_(None),
                        BackgroundJob.locked_at < stale_before,
                    ),
                    BackgroundJob.deleted_at.is_(None),
                )
                .order_by(BackgroundJob.locked_at, BackgroundJob.created_at)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    if not jobs:
        return 0

    for job in jobs:
        try:
            embedding_profile_id = uuid.UUID(str(job.payload.get("embedding_profile_id")))
        except (TypeError, ValueError):
            embedding_profile_id = None
        embedding_profile = (
            await session.scalar(
                select(EmbeddingProfile)
                .where(EmbeddingProfile.id == embedding_profile_id)
                .with_for_update()
            )
            if embedding_profile_id is not None
            else None
        )
        job.locked_at = None
        job.locked_by = None
        job.error_code = "embedding_worker_lost"
        job.error_message = "向量索引后台任务已恢复或停止；不会影响已启用的旧索引。"
        if job.attempts < job.max_attempts:
            # Existing vectors are keyed by source version/content hash, so a
            # recovered worker can restart from the first page without
            # duplicating valid provider requests or mixing vector spaces.
            job.status = JobStatus.QUEUED
            job.progress = 0.0
            job.available_at = now
            job.result = _safe_result(phase="retry_wait", progress=EmbeddingProgress())
        else:
            job.status = JobStatus.FAILED
            job.progress = 1.0
            job.result = _safe_result(phase="failed", progress=EmbeddingProgress())
            if embedding_profile is not None:
                await mark_embedding_profile_failed(
                    session,
                    embedding_profile.id,
                    failure_code="embedding_worker_lost",
                )
        job.touch(at=now)
    await session.commit()
    return len(jobs)


async def claim_next_embedding_job(session: AsyncSession, worker_id: str) -> uuid.UUID | None:
    """Claim one queued reindex job without blocking other workers."""

    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.EMBEDDING_REINDEX,
            BackgroundJob.status == JobStatus.QUEUED,
            BackgroundJob.available_at <= utc_now(),
            BackgroundJob.deleted_at.is_(None),
        )
        .order_by(BackgroundJob.available_at, BackgroundJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.progress = max(job.progress, 0.02)
    job.attempts += 1
    job.locked_at = utc_now()
    job.locked_by = worker_id
    job.error_code = None
    job.error_message = None
    job.result = _safe_result(phase="resolving_target", progress=EmbeddingProgress())
    job.touch()
    await session.commit()
    return job.id


async def _load_context(job_id: uuid.UUID) -> EmbeddingJobContext | None:
    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        if job is None or job.status != JobStatus.RUNNING:
            return None
        try:
            embedding_profile_id = uuid.UUID(str(job.payload["embedding_profile_id"]))
            target_fingerprint = str(job.payload["target_fingerprint"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                code="embedding_job_payload_invalid",
                message="向量索引任务参数无效",
                status_code=422,
            ) from exc
        if len(target_fingerprint) != 64:
            raise AppError(
                code="embedding_job_payload_invalid",
                message="向量索引任务参数无效",
                status_code=422,
            )
        profile = await session.get(EmbeddingProfile, embedding_profile_id)
        if (
            profile is None
            or profile.deleted_at is not None
            or job.profile_id is None
            or profile.profile_id != job.profile_id
        ):
            raise AppError(
                code="embedding_job_profile_invalid",
                message="向量索引任务关联的配置不存在",
                status_code=409,
            )
        if profile.status != EmbeddingProfileStatus.BUILDING:
            raise AppError(
                code="embedding_profile_not_building",
                message="向量索引已不处于可重建状态",
                status_code=409,
            )
        return EmbeddingJobContext(
            job_id=job.id,
            profile_id=profile.profile_id,
            embedding_profile_id=profile.id,
            target_fingerprint=target_fingerprint,
            normalized=profile.normalized,
            vector_dimensions=profile.vector_dimensions,
        )


async def _pause_for_interview(
    context: EmbeddingJobContext,
    progress: EmbeddingProgress,
    *,
    vector_dimensions: int | None,
) -> bool:
    """Requeue quickly if an interview starts, without consuming a retry attempt."""

    async with async_session_factory() as session:
        if not await profile_has_interviewing_session(session, context.profile_id):
            return False
        job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == context.job_id).with_for_update()
        )
        if job is None or job.status != JobStatus.RUNNING:
            return True
        job.status = JobStatus.QUEUED
        # Claiming increments attempts in the common worker protocol.  A yield
        # for the user's live interview is not a failed provider attempt.
        job.attempts = max(0, job.attempts - 1)
        job.available_at = utc_now() + timedelta(seconds=INTERVIEW_YIELD_DELAY_SECONDS)
        job.locked_at = None
        job.locked_by = None
        job.error_code = None
        job.error_message = None
        job.result = _safe_result(
            phase="waiting_for_interview",
            progress=progress,
            vector_dimensions=vector_dimensions,
        )
        job.touch()
        await session.commit()
        return True


async def _assert_target_unchanged(
    context: EmbeddingJobContext,
) -> tuple[EmbeddingProfile, object]:
    """Load a BUILDING profile and resolve its exact target with no fallback."""

    async with async_session_factory() as session:
        profile = await session.get(EmbeddingProfile, context.embedding_profile_id)
        if profile is None or profile.status != EmbeddingProfileStatus.BUILDING:
            raise AppError(
                code="embedding_profile_not_building",
                message="向量索引已不处于可重建状态",
                status_code=409,
            )
        _snapshot, target = await verified_embedding_target_for_profile(
            session,
            profile,
            expected_fingerprint=context.target_fingerprint,
        )
        return profile, target


async def _read_batch(
    context: EmbeddingJobContext,
    *,
    source_kind: str,
    cursor: uuid.UUID | None,
) -> EmbeddingBatch:
    async with async_session_factory() as session:
        if source_kind == "memory":
            batch = await next_memory_embedding_batch(
                session,
                profile_id=context.profile_id,
                after_id=cursor,
            )
        else:
            batch = await next_plan_question_embedding_batch(
                session,
                profile_id=context.profile_id,
                after_id=cursor,
            )
        if not batch.documents:
            return batch
        pending = await documents_needing_embeddings(
            session,
            embedding_profile_id=context.embedding_profile_id,
            batch=batch,
        )
        return EmbeddingBatch(
            source_kind=batch.source_kind,
            documents=pending,
            next_cursor=batch.next_cursor,
            source_count=batch.source_count,
        )


async def _persist_batch(
    context: EmbeddingJobContext,
    *,
    documents: tuple,
    vectors: list[list[float]],
    dimensions: int,
) -> None:
    async with async_session_factory() as session:
        await persist_embedding_batch(
            session,
            embedding_profile_id=context.embedding_profile_id,
            documents=documents,
            vectors=vectors,
            dimensions=dimensions,
        )
        await session.commit()


async def _persist_probe_dimensions(
    context: EmbeddingJobContext,
    *,
    dimensions: int,
) -> None:
    async with async_session_factory() as session:
        await persist_verified_embedding_dimensions(
            session,
            embedding_profile_id=context.embedding_profile_id,
            dimensions=dimensions,
        )
        await session.commit()


async def _update_progress(
    context: EmbeddingJobContext,
    *,
    progress: EmbeddingProgress,
    total_sources: int,
    phase: str,
    vector_dimensions: int | None,
) -> None:
    # Keep 10% for target re-check + atomic profile promotion.  Totals can move
    # while a user edits memories; clipping gives a stable, honest UI signal.
    completed_ratio = progress.scanned / max(total_sources, 1)
    value = min(0.9, 0.1 + (0.8 * completed_ratio))
    async with async_session_factory() as session:
        job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == context.job_id).with_for_update()
        )
        if job is None or job.status != JobStatus.RUNNING:
            return
        now = utc_now()
        job.progress = value
        job.locked_at = now
        job.result = _safe_result(
            phase=phase,
            progress=progress,
            vector_dimensions=vector_dimensions,
        )
        job.touch(at=now)
        await session.commit()


async def _complete_job(
    context: EmbeddingJobContext,
    *,
    progress: EmbeddingProgress,
    vector_dimensions: int,
) -> None:
    """Promote the profile and complete its job in one transaction."""

    async with async_session_factory() as session:
        job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == context.job_id).with_for_update()
        )
        if job is None or job.status != JobStatus.RUNNING:
            return
        if await profile_has_interviewing_session(session, context.profile_id):
            # This branch cannot call _pause_for_interview because it already
            # holds the job lock.  Mirror the safe requeue transition inline.
            job.status = JobStatus.QUEUED
            job.attempts = max(0, job.attempts - 1)
            job.available_at = utc_now() + timedelta(seconds=INTERVIEW_YIELD_DELAY_SECONDS)
            job.locked_at = None
            job.locked_by = None
            job.result = _safe_result(
                phase="waiting_for_interview",
                progress=progress,
                vector_dimensions=vector_dimensions,
            )
            job.touch()
            await session.commit()
            raise EmbeddingJobPaused
        await promote_embedding_profile(
            session,
            embedding_profile_id=context.embedding_profile_id,
            expected_fingerprint=context.target_fingerprint,
        )
        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.locked_at = None
        job.locked_by = None
        job.error_code = None
        job.error_message = None
        job.result = _safe_result(
            phase="completed",
            progress=progress,
            vector_dimensions=vector_dimensions,
        )
        job.touch()
        await session.commit()


async def _fail_job(
    job_id: uuid.UUID,
    *,
    code: str,
    retryable: bool,
) -> None:
    """Apply a privacy-safe retry/failure result and preserve the old ACTIVE index."""

    async with async_session_factory() as session:
        job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
        )
        if job is None or job.status != JobStatus.RUNNING:
            return
        try:
            embedding_profile_id = uuid.UUID(str(job.payload.get("embedding_profile_id")))
        except (TypeError, ValueError):
            embedding_profile_id = None
        terminal = not retryable or job.attempts >= job.max_attempts
        job.error_code = code[:120]
        job.error_message = _safe_failure_message(code, retryable=retryable)
        job.locked_at = None
        job.locked_by = None
        if terminal:
            job.status = JobStatus.FAILED
            job.progress = 1.0
            job.result = _safe_result(phase="failed", progress=EmbeddingProgress())
            if embedding_profile_id is not None:
                await mark_embedding_profile_failed(
                    session,
                    embedding_profile_id,
                    failure_code=code,
                )
        else:
            job.status = JobStatus.QUEUED
            job.progress = 0.0
            job.available_at = utc_now() + timedelta(seconds=min(30, 2**job.attempts))
            job.result = _safe_result(phase="retry_wait", progress=EmbeddingProgress())
        job.touch()
        await session.commit()


async def _index_source_kind(
    context: EmbeddingJobContext,
    *,
    source_kind: str,
    provider: EmbeddingProvider,
    expected_target_dimensions: int | None,
    current_dimensions: int | None,
    progress: EmbeddingProgress,
    total_sources: int,
) -> int | None:
    cursor: uuid.UUID | None = None
    while True:
        if await _pause_for_interview(
            context,
            progress,
            vector_dimensions=current_dimensions,
        ):
            raise EmbeddingJobPaused
        # A role binding or provider endpoint can change while the worker is
        # alive.  Re-check before every external request rather than promote a
        # vector space that no longer matches the explicit current binding.
        await _assert_target_unchanged(context)
        batch = await _read_batch(context, source_kind=source_kind, cursor=cursor)
        if batch.next_cursor is None:
            break
        cursor = batch.next_cursor
        if source_kind == "memory":
            progress.memory_scanned += batch.source_count
        else:
            progress.plan_question_scanned += batch.source_count
        if batch.documents:
            response = await provider.embed(
                EmbeddingRequest(texts=[document.text for document in batch.documents])
            )
            vectors, dimensions = validated_vectors_for_documents(
                vectors=response.vectors,
                documents=batch.documents,
                expected_dimensions=current_dimensions or expected_target_dimensions,
                normalised=context.normalized,
            )
            await _persist_batch(
                context,
                documents=batch.documents,
                vectors=vectors,
                dimensions=dimensions,
            )
            current_dimensions = dimensions
            if source_kind == "memory":
                progress.memory_embedded += len(batch.documents)
            else:
                progress.plan_question_embedded += len(batch.documents)
        await _update_progress(
            context,
            progress=progress,
            total_sources=total_sources,
            phase=f"indexing_{source_kind}",
            vector_dimensions=current_dimensions,
        )
    return current_dimensions


async def process_embedding_job(job_id: uuid.UUID) -> None:
    """Run one reindex job, yielding to a live interview and never logging content."""

    provider: EmbeddingProvider | None = None
    try:
        context = await _load_context(job_id)
        if context is None:
            return
        progress = EmbeddingProgress()
        if await _pause_for_interview(
            context,
            progress,
            vector_dimensions=context.vector_dimensions,
        ):
            return
        profile, target = await _assert_target_unchanged(context)
        provider = build_embedding_provider_for_target(target)  # type: ignore[arg-type]
        expected_target_dimensions = getattr(target, "vector_dimensions", None)
        async with async_session_factory() as session:
            memory_total, plan_question_total = await source_counts_for_profile(
                session,
                context.profile_id,
            )
        total_sources = memory_total + plan_question_total
        await _update_progress(
            context,
            progress=progress,
            total_sources=total_sources,
            phase="indexing_memories",
            vector_dimensions=profile.vector_dimensions,
        )
        current_dimensions = await _index_source_kind(
            context,
            source_kind="memory",
            provider=provider,
            expected_target_dimensions=expected_target_dimensions,
            current_dimensions=profile.vector_dimensions,
            progress=progress,
            total_sources=total_sources,
        )
        current_dimensions = await _index_source_kind(
            context,
            source_kind="plan_question",
            provider=provider,
            expected_target_dimensions=expected_target_dimensions,
            current_dimensions=current_dimensions,
            progress=progress,
            total_sources=total_sources,
        )
        for _ in range(MAX_RECONCILIATION_PASSES):
            async with async_session_factory() as session:
                needs_reconciliation = await current_sources_need_embeddings(
                    session,
                    profile_id=context.profile_id,
                    embedding_profile_id=context.embedding_profile_id,
                )
                memory_total, plan_question_total = await source_counts_for_profile(
                    session,
                    context.profile_id,
                )
            if not needs_reconciliation:
                break
            total_sources = max(total_sources, memory_total + plan_question_total)
            await _update_progress(
                context,
                progress=progress,
                total_sources=total_sources,
                phase="reconciling",
                vector_dimensions=current_dimensions,
            )
            current_dimensions = await _index_source_kind(
                context,
                source_kind="memory",
                provider=provider,
                expected_target_dimensions=expected_target_dimensions,
                current_dimensions=current_dimensions,
                progress=progress,
                total_sources=total_sources,
            )
            current_dimensions = await _index_source_kind(
                context,
                source_kind="plan_question",
                provider=provider,
                expected_target_dimensions=expected_target_dimensions,
                current_dimensions=current_dimensions,
                progress=progress,
                total_sources=total_sources,
            )
        else:
            async with async_session_factory() as session:
                if await current_sources_need_embeddings(
                    session,
                    profile_id=context.profile_id,
                    embedding_profile_id=context.embedding_profile_id,
                ):
                    raise ProviderError(
                        code="embedding_source_churn",
                        message="Embedding sources changed repeatedly during indexing.",
                        retryable=True,
                    )
        if current_dimensions is None:
            if await _pause_for_interview(context, progress, vector_dimensions=None):
                return
            await _assert_target_unchanged(context)
            _probe, current_dimensions = await embed_dimension_probe(
                provider,
                expected_dimensions=expected_target_dimensions,
                normalised=context.normalized,
            )
            await _persist_probe_dimensions(context, dimensions=current_dimensions)
        await _complete_job(
            context,
            progress=progress,
            vector_dimensions=current_dimensions,
        )
    except EmbeddingJobPaused:
        return
    except AppError as exc:
        await _fail_job(
            job_id,
            code=exc.code,
            retryable=exc.retryable or exc.status_code >= 500,
        )
    except ProviderError as exc:
        await _fail_job(job_id, code=exc.code, retryable=exc.retryable)
    except Exception as exc:
        # Do not use logger.exception/aexception here: a provider exception can
        # include request fragments.  Type + code are enough for diagnostics.
        await logger.awarning(
            "embedding_job_failed",
            job_id=str(job_id),
            error_type=type(exc).__name__,
        )
        await _fail_job(
            job_id,
            code="embedding_reindex_internal_error",
            retryable=True,
        )
    finally:
        if provider is not None:
            close = getattr(provider, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:  # A completed job must remain completed.
                    await logger.awarning(
                        "embedding_provider_close_failed",
                        job_id=str(job_id),
                        error_type=type(exc).__name__,
                    )


async def run_once(worker_id: str = "local-worker") -> bool:
    """Claim and process at most one embedding rebuild job."""

    async with async_session_factory() as session:
        recovered = await _recover_stale_embedding_jobs(session)
        job_id = await claim_next_embedding_job(session, worker_id)
    if job_id is None:
        return recovered > 0
    await process_embedding_job(job_id)
    return True
