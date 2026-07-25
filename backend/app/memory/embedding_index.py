"""Durable, profile-scoped embedding-index lifecycle helpers.

This module deliberately keeps embedding work outside the interview request
path.  A rebuild creates a new immutable ``EmbeddingProfile`` in ``BUILDING``
state and leaves the previously active profile untouched.  The worker can
therefore fail, pause, or retry without making long-term-memory retrieval
unavailable to an interview that is already under way.

The helpers here never select a chat-model fallback.  They resolve only the
explicit ``EMBEDDING`` role and snapshot a non-secret target fingerprint before
the background job is created.  A worker verifies that fingerprint again before
it writes vectors and before it promotes the profile.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import (
    EmbeddingProfileStatus,
    JobStatus,
    JobType,
    MemoryStatus,
    ModelRole,
    ProviderType,
    SessionStatus,
    utc_now,
)
from app.db.models.embedding import EmbeddingProfile, MemoryEmbedding, PlanQuestionEmbedding
from app.db.models.interview import InterviewConfig, InterviewPlan, InterviewSession, PlanQuestion
from app.db.models.job import BackgroundJob
from app.db.models.memory import MemoryItem, MemorySource
from app.db.models.model_connection import ModelConnection
from app.db.models.profile import UserProfile
from app.local_ai.capabilities import LocalCapabilityDefinition
from app.providers.base import ProviderError
from app.providers.embedding_base import EmbeddingProvider
from app.providers.factory import build_embedding_provider
from app.providers.openai_embedding import OpenAICompatibleEmbeddingProvider
from app.providers.types import EmbeddingRequest
from app.services.model_connections import resolve_embedding_target

# These limits are deliberately lower than the generic OpenAI-compatible
# provider limits.  Local TEI is configured with MAX_BATCH_TOKENS=4096 and
# Chinese frequently approaches one token per character, so 2 x 1,600 source
# characters keeps the *aggregate* request conservatively beneath that cap
# after tokenizer special tokens.  Background indexing must make bounded
# progress and never monopolise an API connection or a local TEI container
# while a user is waiting.
EMBEDDING_BATCH_SIZE = 2
MAX_DOCUMENT_CHARACTERS = 1_600
MAX_BATCH_CHARACTERS = 3_200
MAX_QUERY_INSTRUCTION_CHARACTERS = 512
VALID_DISTANCE_METRICS = frozenset({"cosine", "l2", "inner_product"})
DIMENSION_PROBE_TEXT = "embedding dimension verification"


@dataclass(frozen=True, slots=True)
class EmbeddingTargetSnapshot:
    """A non-secret exact-target snapshot persisted with one rebuild job."""

    target_kind: Literal["model_connection", "local_capability"]
    model_connection_id: uuid.UUID | None
    local_capability_key: str | None
    model_name: str
    model_revision: str
    expected_dimensions: int | None
    fingerprint: str


@dataclass(frozen=True, slots=True)
class EmbeddingRebuildEnqueueResult:
    """The durable work created (or replayed) for an explicit rebuild request."""

    embedding_profile: EmbeddingProfile
    job: BackgroundJob
    created: bool


@dataclass(frozen=True, slots=True)
class EmbeddingDocument:
    """A bounded source snapshot held only in worker memory, never in job JSON."""

    source_kind: Literal["memory", "plan_question"]
    source_id: uuid.UUID
    source_version: int
    content_hash: str
    text: str
    plan_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    role_name: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """One homogeneous, bounded batch of source documents."""

    source_kind: Literal["memory", "plan_question"]
    documents: tuple[EmbeddingDocument, ...]
    next_cursor: uuid.UUID | None
    source_count: int


def _profile_rebuild_lock_key(profile_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"embedding-rebuild:{profile_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _lock_profile_rebuild(session: AsyncSession, profile_id: uuid.UUID) -> None:
    """Serialize the one allowed BUILDING profile for a user profile."""

    await session.execute(select(func.pg_advisory_xact_lock(_profile_rebuild_lock_key(profile_id))))


def _canonical_fingerprint(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _snapshot_for_target(
    target: ModelConnection | LocalCapabilityDefinition,
) -> EmbeddingTargetSnapshot:
    """Capture only configuration that changes the vector space or endpoint.

    We intentionally do not include connection health or its mutable database
    version.  Testing a connection should not invalidate a nearly complete
    rebuild; changing its protocol, endpoint, model, or role binding does.
    API keys are neither stored nor fingerprinted.
    """

    if isinstance(target, LocalCapabilityDefinition):
        if target.role != ModelRole.EMBEDDING:
            raise AppError(
                code="embedding_target_invalid",
                message="所选本地能力不能用于向量检索",
                status_code=409,
            )
        payload = {
            "target_kind": "local_capability",
            "local_capability_key": target.key,
            "model_name": target.model_name,
            "model_revision": target.revision,
            "base_url": target.base_url,
            "expected_dimensions": target.vector_dimensions,
        }
        return EmbeddingTargetSnapshot(
            target_kind="local_capability",
            model_connection_id=None,
            local_capability_key=target.key,
            model_name=target.model_name,
            model_revision=target.revision,
            expected_dimensions=target.vector_dimensions,
            fingerprint=_canonical_fingerprint(payload),
        )

    if ProviderType(target.provider_type) != ProviderType.OPENAI_COMPATIBLE:
        raise AppError(
            code="embedding_provider_unsupported",
            message="向量检索目前仅支持 OpenAI-compatible 模型连接",
            status_code=409,
        )
    if not target.encrypted_api_key:
        raise AppError(
            code="embedding_provider_key_missing",
            message="向量检索模型连接尚未配置 API Key",
            status_code=409,
        )
    payload = {
        "target_kind": "model_connection",
        "model_connection_id": str(target.id),
        "provider_type": ProviderType(target.provider_type).value,
        "base_url": target.base_url.rstrip("/"),
        "model_name": target.model_name,
    }
    return EmbeddingTargetSnapshot(
        target_kind="model_connection",
        model_connection_id=target.id,
        local_capability_key=None,
        model_name=target.model_name,
        # Most OpenAI-compatible APIs do not expose a durable model revision.
        # The fingerprint above is the immutable compatibility contract.
        model_revision="provider-configured",
        expected_dimensions=None,
        fingerprint=_canonical_fingerprint(payload),
    )


async def snapshot_current_embedding_target(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> EmbeddingTargetSnapshot:
    """Resolve the exact Embedding binding and return its non-secret snapshot."""

    target = await resolve_embedding_target(session, profile_id)
    return _snapshot_for_target(target)


def _validate_rebuild_options(
    *,
    normalized: bool,
    query_instruction: str,
    distance_metric: str,
) -> tuple[bool, str, str]:
    if not isinstance(normalized, bool):
        raise AppError(
            code="embedding_rebuild_options_invalid",
            message="向量归一化选项无效",
            status_code=422,
        )
    if not isinstance(query_instruction, str):
        raise AppError(
            code="embedding_rebuild_options_invalid",
            message="检索指令必须是文本",
            status_code=422,
        )
    instruction = query_instruction.strip()
    if len(instruction) > MAX_QUERY_INSTRUCTION_CHARACTERS:
        raise AppError(
            code="embedding_rebuild_options_invalid",
            message="检索指令过长",
            status_code=422,
        )
    if distance_metric not in VALID_DISTANCE_METRICS:
        raise AppError(
            code="embedding_rebuild_options_invalid",
            message="不支持的向量距离度量",
            status_code=422,
        )
    return normalized, instruction, distance_metric


def _reindex_idempotency_key(embedding_profile_id: uuid.UUID) -> str:
    return f"embedding-reindex:{embedding_profile_id}"


async def enqueue_embedding_rebuild(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    normalized: bool = True,
    query_instruction: str = "",
    distance_metric: str = "cosine",
) -> EmbeddingRebuildEnqueueResult:
    """Create a new BUILDING profile and its durable reindex job.

    At most one building profile is permitted per user profile.  An identical
    request made while it is queued/running replays that work rather than
    spawning a second vector build.  Importantly, the old ACTIVE profile is not
    retired here; promotion happens only after the worker has verified a batch
    and persisted all current source pages.
    """

    normalized, instruction, metric = _validate_rebuild_options(
        normalized=normalized,
        query_instruction=query_instruction,
        distance_metric=distance_metric,
    )
    await _lock_profile_rebuild(session, profile_id)

    building = await session.scalar(
        select(EmbeddingProfile)
        .where(
            EmbeddingProfile.profile_id == profile_id,
            EmbeddingProfile.status == EmbeddingProfileStatus.BUILDING,
            EmbeddingProfile.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if building is not None:
        job = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key == _reindex_idempotency_key(building.id),
                BackgroundJob.deleted_at.is_(None),
            )
        )
        if job is not None and job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            await session.commit()
            return EmbeddingRebuildEnqueueResult(
                embedding_profile=building,
                job=job,
                created=False,
            )
        # A previous process stopped after allocating a BUILDING profile but
        # before completing its job.  Preserve its audit record and make room
        # for a new immutable build instead of reusing ambiguous partial data.
        building.status = EmbeddingProfileStatus.FAILED
        building.failed_at = utc_now()
        building.failure_code = "embedding_rebuild_abandoned"
        building.failure_summary = "此前的向量重建未能完成，已由新的重建请求替代。"
        building.touch()

    snapshot = await snapshot_current_embedding_target(session, profile_id)
    embedding_profile = EmbeddingProfile(
        profile_id=profile_id,
        model_connection_id=snapshot.model_connection_id,
        local_capability_key=snapshot.local_capability_key,
        target_fingerprint=snapshot.fingerprint,
        model_name=snapshot.model_name,
        model_revision=snapshot.model_revision,
        # The first verified provider response sets this while BUILDING.  It is
        # deliberately not trusted from a local preset or a cloud model label.
        vector_dimensions=None,
        normalized=normalized,
        query_instruction=instruction,
        distance_metric=metric,
        status=EmbeddingProfileStatus.BUILDING,
    )
    session.add(embedding_profile)
    await session.flush()
    job = BackgroundJob(
        profile_id=profile_id,
        job_type=JobType.EMBEDDING_REINDEX,
        status=JobStatus.QUEUED,
        payload={
            "embedding_profile_id": str(embedding_profile.id),
            "target_fingerprint": snapshot.fingerprint,
        },
        # IDs/fingerprints are safe scheduling metadata.  No source text,
        # prompt, API credential, or vector leaves the vector tables.
        result={"phase": "queued"},
        idempotency_key=_reindex_idempotency_key(embedding_profile.id),
    )
    session.add(job)
    await session.commit()
    await session.refresh(embedding_profile)
    await session.refresh(job)
    return EmbeddingRebuildEnqueueResult(
        embedding_profile=embedding_profile,
        job=job,
        created=True,
    )


async def get_active_embedding_profile(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> EmbeddingProfile | None:
    """Return only a fully promoted profile suitable for retrieval."""

    return await session.scalar(
        select(EmbeddingProfile).where(
            EmbeddingProfile.profile_id == profile_id,
            EmbeddingProfile.status == EmbeddingProfileStatus.ACTIVE,
            EmbeddingProfile.deleted_at.is_(None),
        )
    )


async def profile_has_interviewing_session(session: AsyncSession, profile_id: uuid.UUID) -> bool:
    """Whether a rebuild must yield so the live interview keeps priority."""

    session_id = await session.scalar(
        select(InterviewSession.id)
        .where(
            InterviewSession.profile_id == profile_id,
            InterviewSession.status == SessionStatus.INTERVIEWING,
            InterviewSession.deleted_at.is_(None),
        )
        .limit(1)
    )
    return session_id is not None


async def verified_embedding_target_for_profile(
    session: AsyncSession,
    embedding_profile: EmbeddingProfile,
    *,
    expected_fingerprint: str,
) -> tuple[EmbeddingTargetSnapshot, ModelConnection | LocalCapabilityDefinition]:
    """Resolve and verify the *current* target before using a profile's vectors."""

    if embedding_profile.target_fingerprint != expected_fingerprint:
        raise AppError(
            code="embedding_target_changed",
            message="向量模型配置已变化，请重新创建向量索引。",
            status_code=409,
        )
    target = await resolve_embedding_target(session, embedding_profile.profile_id)
    snapshot = _snapshot_for_target(target)
    if snapshot.fingerprint != expected_fingerprint:
        raise AppError(
            code="embedding_target_changed",
            message="向量模型配置已变化，请重新创建向量索引。",
            status_code=409,
        )
    # The stored snapshot must agree with the target shape as well as its hash.
    if (
        embedding_profile.model_connection_id != snapshot.model_connection_id
        or embedding_profile.local_capability_key != snapshot.local_capability_key
        or embedding_profile.model_name != snapshot.model_name
        or embedding_profile.model_revision != snapshot.model_revision
    ):
        raise AppError(
            code="embedding_target_changed",
            message="向量模型配置已变化，请重新创建向量索引。",
            status_code=409,
        )
    return snapshot, target


def build_embedding_provider_for_target(
    target: ModelConnection | LocalCapabilityDefinition,
) -> EmbeddingProvider:
    """Build only the capability-specific embedding client for an exact target."""

    if isinstance(target, LocalCapabilityDefinition):
        return OpenAICompatibleEmbeddingProvider(
            base_url=target.base_url,
            model=target.model_name,
            api_key=None,
            expected_dimensions=target.vector_dimensions,
            max_texts=EMBEDDING_BATCH_SIZE,
            max_text_characters=MAX_DOCUMENT_CHARACTERS,
            max_total_characters=MAX_BATCH_CHARACTERS,
        )
    return build_embedding_provider(target)


def _bounded_document_text(*parts: str) -> str:
    # Normalise surrounding whitespace only; internal newlines can encode useful
    # resume/memory structure and should remain visible to the embedding model.
    text = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    if not text:
        # Source model constraints should make this unreachable.  Raising here
        # is safer than emitting an empty provider request or a meaningless hash.
        raise AppError(
            code="embedding_source_invalid",
            message="待索引内容为空，无法创建向量。",
            status_code=409,
        )
    return text[:MAX_DOCUMENT_CHARACTERS]


def _document_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


async def next_memory_embedding_batch(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    after_id: uuid.UUID | None,
) -> EmbeddingBatch:
    """Read one bounded page of retrievable memory source snapshots."""

    source_exists = (
        select(MemorySource.id)
        .where(
            MemorySource.memory_id == MemoryItem.id,
            MemorySource.deleted_at.is_(None),
        )
        .exists()
    )
    statement = (
        select(MemoryItem)
        .join(UserProfile, UserProfile.id == MemoryItem.profile_id)
        .where(
            MemoryItem.profile_id == profile_id,
            MemoryItem.status == MemoryStatus.ACTIVE,
            MemoryItem.deleted_at.is_(None),
            UserProfile.deleted_at.is_(None),
            UserProfile.memory_enabled.is_(True),
            or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > utc_now()),
            source_exists,
        )
    )
    if after_id is not None:
        statement = statement.where(MemoryItem.id > after_id)
    rows = list(
        (await session.scalars(statement.order_by(MemoryItem.id).limit(EMBEDDING_BATCH_SIZE))).all()
    )
    documents: list[EmbeddingDocument] = []
    for memory in rows:
        document_text = _bounded_document_text(memory.canonical_key, memory.content)
        documents.append(
            EmbeddingDocument(
                source_kind="memory",
                source_id=memory.id,
                source_version=memory.version,
                text=document_text,
                content_hash=_document_hash(document_text),
            )
        )
    return EmbeddingBatch(
        source_kind="memory",
        documents=tuple(documents),
        next_cursor=rows[-1].id if rows else None,
        source_count=len(rows),
    )


async def next_plan_question_embedding_batch(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    after_id: uuid.UUID | None,
) -> EmbeddingBatch:
    """Read one bounded page of profile-owned plan-question snapshots."""

    statement = (
        select(PlanQuestion, InterviewPlan, InterviewConfig)
        .join(InterviewPlan, InterviewPlan.id == PlanQuestion.plan_id)
        .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
        .where(
            InterviewConfig.profile_id == profile_id,
            InterviewConfig.deleted_at.is_(None),
            InterviewPlan.deleted_at.is_(None),
            PlanQuestion.deleted_at.is_(None),
        )
    )
    if after_id is not None:
        statement = statement.where(PlanQuestion.id > after_id)
    rows = list(
        (
            await session.execute(statement.order_by(PlanQuestion.id).limit(EMBEDDING_BATCH_SIZE))
        ).all()
    )
    documents: list[EmbeddingDocument] = []
    for question, _plan, config in rows:
        document_text = _bounded_document_text(config.role_name, question.prompt_snapshot)
        documents.append(
            EmbeddingDocument(
                source_kind="plan_question",
                source_id=question.id,
                source_version=question.version,
                text=document_text,
                content_hash=_document_hash(document_text),
                plan_id=question.plan_id,
                company_id=config.company_id,
                role_name=config.role_name,
            )
        )
    return EmbeddingBatch(
        source_kind="plan_question",
        documents=tuple(documents),
        next_cursor=rows[-1][0].id if rows else None,
        source_count=len(rows),
    )


async def documents_needing_embeddings(
    session: AsyncSession,
    *,
    embedding_profile_id: uuid.UUID,
    batch: EmbeddingBatch,
) -> tuple[EmbeddingDocument, ...]:
    """Skip already-persisted source snapshots when a job is retried/requeued."""

    if not batch.documents:
        return ()
    source_ids = [document.source_id for document in batch.documents]
    if batch.source_kind == "memory":
        rows = await session.execute(
            select(
                MemoryEmbedding.memory_id,
                MemoryEmbedding.content_hash,
                MemoryEmbedding.source_version,
            ).where(
                MemoryEmbedding.embedding_profile_id == embedding_profile_id,
                MemoryEmbedding.memory_id.in_(source_ids),
                MemoryEmbedding.deleted_at.is_(None),
            )
        )
    else:
        rows = await session.execute(
            select(
                PlanQuestionEmbedding.plan_question_id,
                PlanQuestionEmbedding.content_hash,
                PlanQuestionEmbedding.source_version,
            ).where(
                PlanQuestionEmbedding.embedding_profile_id == embedding_profile_id,
                PlanQuestionEmbedding.plan_question_id.in_(source_ids),
                PlanQuestionEmbedding.deleted_at.is_(None),
            )
        )
    existing = {
        source_id: (content_hash, source_version)
        for source_id, content_hash, source_version in rows.all()
    }
    return tuple(
        document
        for document in batch.documents
        if existing.get(document.source_id) != (document.content_hash, document.source_version)
    )


def _normalise_vector(vector: Sequence[float], *, normalised: bool) -> list[float]:
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ProviderError(
            code="embedding_vector_invalid",
            message="向量服务返回了无效向量",
            retryable=False,
        )
    if not normalised:
        return values
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude <= 1e-12:
        raise ProviderError(
            code="embedding_vector_invalid",
            message="向量服务返回了无效向量",
            retryable=False,
        )
    return [value / magnitude for value in values]


def validated_vectors_for_documents(
    *,
    vectors: Sequence[Sequence[float]],
    documents: Sequence[EmbeddingDocument],
    expected_dimensions: int | None,
    normalised: bool,
) -> tuple[list[list[float]], int]:
    """Validate one provider response without ever serialising its vectors."""

    if len(vectors) != len(documents) or not vectors:
        raise ProviderError(
            code="embedding_vector_invalid",
            message="向量服务返回了无效向量",
            retryable=False,
        )
    dimension = len(vectors[0])
    if not 1 <= dimension <= 2_000:
        raise ProviderError(
            code="embedding_vector_invalid",
            message="向量服务返回了无效向量",
            retryable=False,
        )
    if expected_dimensions is not None and dimension != expected_dimensions:
        raise ProviderError(
            code="embedding_dimension_mismatch",
            message="向量服务返回的维度与当前索引不一致",
            retryable=False,
        )
    normalised_vectors = [_normalise_vector(vector, normalised=normalised) for vector in vectors]
    if any(len(vector) != dimension for vector in normalised_vectors):
        raise ProviderError(
            code="embedding_dimension_mismatch",
            message="向量服务返回的维度与当前索引不一致",
            retryable=False,
        )
    return normalised_vectors, dimension


async def persist_embedding_batch(
    session: AsyncSession,
    *,
    embedding_profile_id: uuid.UUID,
    documents: Sequence[EmbeddingDocument],
    vectors: Sequence[Sequence[float]],
    dimensions: int,
) -> None:
    """Atomically set first verified dimensions and safely upsert one source page."""

    if not documents:
        return
    profile = await session.scalar(
        select(EmbeddingProfile)
        .where(EmbeddingProfile.id == embedding_profile_id)
        .with_for_update()
    )
    if profile is None or profile.status != EmbeddingProfileStatus.BUILDING:
        raise AppError(
            code="embedding_profile_not_building",
            message="向量索引已不处于可重建状态",
            status_code=409,
        )
    if profile.vector_dimensions is None:
        profile.vector_dimensions = dimensions
        profile.touch()
    elif profile.vector_dimensions != dimensions:
        raise ProviderError(
            code="embedding_dimension_mismatch",
            message="向量服务返回的维度与当前索引不一致",
            retryable=False,
        )

    now = utc_now()
    if documents[0].source_kind == "memory":
        values = [
            {
                "id": uuid.uuid4(),
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "version": 1,
                "profile_id": profile.profile_id,
                "embedding_profile_id": profile.id,
                "memory_id": document.source_id,
                "company_id": None,
                "role_name": None,
                "content_hash": document.content_hash,
                "source_version": document.source_version,
                "embedding": list(vector),
                "embedded_at": now,
            }
            for document, vector in zip(documents, vectors, strict=True)
        ]
        statement = insert(MemoryEmbedding).values(values)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    MemoryEmbedding.profile_id,
                    MemoryEmbedding.embedding_profile_id,
                    MemoryEmbedding.memory_id,
                ],
                index_where=MemoryEmbedding.deleted_at.is_(None),
                set_={
                    "company_id": statement.excluded.company_id,
                    "role_name": statement.excluded.role_name,
                    "content_hash": statement.excluded.content_hash,
                    "source_version": statement.excluded.source_version,
                    "embedding": statement.excluded.embedding,
                    "embedded_at": statement.excluded.embedded_at,
                    "updated_at": now,
                    "version": MemoryEmbedding.version + 1,
                },
            )
        )
        return

    values = [
        {
            "id": uuid.uuid4(),
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
            "version": 1,
            "profile_id": profile.profile_id,
            "embedding_profile_id": profile.id,
            "plan_id": document.plan_id,
            "plan_question_id": document.source_id,
            "company_id": document.company_id,
            "role_name": document.role_name,
            "content_hash": document.content_hash,
            "source_version": document.source_version,
            "embedding": list(vector),
            "embedded_at": now,
        }
        for document, vector in zip(documents, vectors, strict=True)
    ]
    statement = insert(PlanQuestionEmbedding).values(values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                PlanQuestionEmbedding.profile_id,
                PlanQuestionEmbedding.embedding_profile_id,
                PlanQuestionEmbedding.plan_question_id,
            ],
            index_where=PlanQuestionEmbedding.deleted_at.is_(None),
            set_={
                "plan_id": statement.excluded.plan_id,
                "company_id": statement.excluded.company_id,
                "role_name": statement.excluded.role_name,
                "content_hash": statement.excluded.content_hash,
                "source_version": statement.excluded.source_version,
                "embedding": statement.excluded.embedding,
                "embedded_at": statement.excluded.embedded_at,
                "updated_at": now,
                "version": PlanQuestionEmbedding.version + 1,
            },
        )
    )


async def persist_verified_embedding_dimensions(
    session: AsyncSession,
    *,
    embedding_profile_id: uuid.UUID,
    dimensions: int,
) -> None:
    """Persist a dimension learned from a harmless empty-index probe.

    This follows the same immutability rule as a normal batch: dimensions can
    move from ``None`` to a verified value only while the profile is BUILDING.
    """

    if not 1 <= dimensions <= 2_000:
        raise ProviderError(
            code="embedding_vector_invalid",
            message="向量服务返回了无效向量",
            retryable=False,
        )
    profile = await session.scalar(
        select(EmbeddingProfile)
        .where(EmbeddingProfile.id == embedding_profile_id)
        .with_for_update()
    )
    if profile is None or profile.status != EmbeddingProfileStatus.BUILDING:
        raise AppError(
            code="embedding_profile_not_building",
            message="向量索引已不处于可重建状态",
            status_code=409,
        )
    if profile.vector_dimensions is None:
        profile.vector_dimensions = dimensions
        profile.touch()
    elif profile.vector_dimensions != dimensions:
        raise ProviderError(
            code="embedding_dimension_mismatch",
            message="向量服务返回的维度与当前索引不一致",
            retryable=False,
        )


async def source_counts_for_profile(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> tuple[int, int]:
    """Return lightweight progress denominators without loading source text."""

    memory_source_exists = (
        select(MemorySource.id)
        .where(
            MemorySource.memory_id == MemoryItem.id,
            MemorySource.deleted_at.is_(None),
        )
        .exists()
    )
    memory_count = await session.scalar(
        select(func.count())
        .select_from(MemoryItem)
        .join(UserProfile, UserProfile.id == MemoryItem.profile_id)
        .where(
            MemoryItem.profile_id == profile_id,
            MemoryItem.status == MemoryStatus.ACTIVE,
            MemoryItem.deleted_at.is_(None),
            UserProfile.deleted_at.is_(None),
            UserProfile.memory_enabled.is_(True),
            or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > utc_now()),
            memory_source_exists,
        )
    )
    plan_question_count = await session.scalar(
        select(func.count())
        .select_from(PlanQuestion)
        .join(InterviewPlan, InterviewPlan.id == PlanQuestion.plan_id)
        .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
        .where(
            InterviewConfig.profile_id == profile_id,
            InterviewConfig.deleted_at.is_(None),
            InterviewPlan.deleted_at.is_(None),
            PlanQuestion.deleted_at.is_(None),
        )
    )
    return int(memory_count or 0), int(plan_question_count or 0)


async def current_sources_need_embeddings(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    embedding_profile_id: uuid.UUID,
) -> bool:
    """Return whether a current retrievable source lacks a matching vector.

    A rebuild reads bounded UUID pages so it never holds source-table locks
    while calling an embedding provider.  Users can therefore edit a memory
    or create a new plan question during the build.  Before promotion, this
    lightweight coverage check detects a missing or version-stale source and
    lets the worker make another idempotent pass instead of publishing a
    knowingly partial vector space.
    """

    memory_source_exists = (
        select(MemorySource.id)
        .where(
            MemorySource.memory_id == MemoryItem.id,
            MemorySource.deleted_at.is_(None),
        )
        .exists()
    )
    memory_gap = await session.scalar(
        select(MemoryItem.id)
        .join(UserProfile, UserProfile.id == MemoryItem.profile_id)
        .outerjoin(
            MemoryEmbedding,
            and_(
                MemoryEmbedding.profile_id == profile_id,
                MemoryEmbedding.embedding_profile_id == embedding_profile_id,
                MemoryEmbedding.memory_id == MemoryItem.id,
                MemoryEmbedding.source_version == MemoryItem.version,
                MemoryEmbedding.deleted_at.is_(None),
            ),
        )
        .where(
            MemoryItem.profile_id == profile_id,
            MemoryItem.status == MemoryStatus.ACTIVE,
            MemoryItem.deleted_at.is_(None),
            UserProfile.deleted_at.is_(None),
            UserProfile.memory_enabled.is_(True),
            or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > utc_now()),
            memory_source_exists,
            MemoryEmbedding.id.is_(None),
        )
        .limit(1)
    )
    if memory_gap is not None:
        return True

    plan_question_gap = await session.scalar(
        select(PlanQuestion.id)
        .join(InterviewPlan, InterviewPlan.id == PlanQuestion.plan_id)
        .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
        .outerjoin(
            PlanQuestionEmbedding,
            and_(
                PlanQuestionEmbedding.profile_id == profile_id,
                PlanQuestionEmbedding.embedding_profile_id == embedding_profile_id,
                PlanQuestionEmbedding.plan_question_id == PlanQuestion.id,
                PlanQuestionEmbedding.plan_id == PlanQuestion.plan_id,
                PlanQuestionEmbedding.source_version == PlanQuestion.version,
                PlanQuestionEmbedding.deleted_at.is_(None),
            ),
        )
        .where(
            InterviewConfig.profile_id == profile_id,
            InterviewConfig.deleted_at.is_(None),
            InterviewPlan.deleted_at.is_(None),
            PlanQuestion.deleted_at.is_(None),
            PlanQuestionEmbedding.id.is_(None),
        )
        .limit(1)
    )
    return plan_question_gap is not None


async def mark_embedding_profile_failed(
    session: AsyncSession,
    embedding_profile_id: uuid.UUID,
    *,
    failure_code: str,
) -> None:
    """Fail only a BUILDING profile; an old ACTIVE profile remains untouched."""

    profile = await session.scalar(
        select(EmbeddingProfile)
        .where(EmbeddingProfile.id == embedding_profile_id)
        .with_for_update()
    )
    if profile is None or profile.status != EmbeddingProfileStatus.BUILDING:
        return
    profile.status = EmbeddingProfileStatus.FAILED
    profile.failed_at = utc_now()
    profile.failure_code = failure_code[:120]
    # Do not persist third-party exception text because providers can echo
    # portions of an input request.  This generic summary is safe for UI/logs.
    profile.failure_summary = "向量索引未能完成；请检查嵌入服务和模型配置后重试。"
    profile.touch()


async def promote_embedding_profile(
    session: AsyncSession,
    *,
    embedding_profile_id: uuid.UUID,
    expected_fingerprint: str,
) -> EmbeddingProfile:
    """Atomically retire the old ACTIVE profile and promote a complete BUILDING one."""

    profile = await session.scalar(
        select(EmbeddingProfile)
        .where(EmbeddingProfile.id == embedding_profile_id)
        .with_for_update()
    )
    if profile is None or profile.status != EmbeddingProfileStatus.BUILDING:
        raise AppError(
            code="embedding_profile_not_building",
            message="向量索引已不处于可启用状态",
            status_code=409,
        )
    if profile.vector_dimensions is None:
        raise AppError(
            code="embedding_dimensions_missing",
            message="向量服务尚未返回可验证的维度",
            status_code=409,
        )
    await verified_embedding_target_for_profile(
        session,
        profile,
        expected_fingerprint=expected_fingerprint,
    )
    existing_active = list(
        (
            await session.scalars(
                select(EmbeddingProfile)
                .where(
                    EmbeddingProfile.profile_id == profile.profile_id,
                    EmbeddingProfile.status == EmbeddingProfileStatus.ACTIVE,
                    EmbeddingProfile.deleted_at.is_(None),
                    EmbeddingProfile.id != profile.id,
                )
                .with_for_update()
            )
        ).all()
    )
    now = utc_now()
    for active in existing_active:
        active.status = EmbeddingProfileStatus.RETIRED
        active.touch(at=now)
    # The partial unique index permits exactly one active profile.  Flush the
    # retirement first rather than relying on unit-of-work update ordering,
    # otherwise PostgreSQL can see the new ACTIVE row before the old one is
    # retired and reject an otherwise valid atomic promotion.
    if existing_active:
        await session.flush()
    profile.status = EmbeddingProfileStatus.ACTIVE
    profile.activated_at = now
    profile.failed_at = None
    profile.failure_code = None
    profile.failure_summary = None
    profile.touch(at=now)
    return profile


async def embed_dimension_probe(
    provider: EmbeddingProvider,
    *,
    expected_dimensions: int | None,
    normalised: bool,
) -> tuple[list[float], int]:
    """Verify dimensions for an otherwise empty source set without user text."""

    response = await provider.embed(EmbeddingRequest(texts=[DIMENSION_PROBE_TEXT]))
    vectors, dimensions = validated_vectors_for_documents(
        vectors=response.vectors,
        documents=(
            EmbeddingDocument(
                source_kind="memory",
                source_id=uuid.UUID(int=0),
                source_version=1,
                content_hash="0" * 64,
                text=DIMENSION_PROBE_TEXT,
            ),
        ),
        expected_dimensions=expected_dimensions,
        normalised=normalised,
    )
    return vectors[0], dimensions
