import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.common import (
    EmbeddingProfileStatus,
    MemoryStatus,
    MemoryType,
    ModelRole,
    utc_now,
)
from app.db.models.embedding import EmbeddingProfile, MemoryEmbedding, PlanQuestionEmbedding
from app.db.models.interview import PlanQuestion
from app.db.models.memory import MemoryItem, MemorySource, MemoryUsage
from app.db.models.profile import UserProfile
from app.memory.types import ROLE_MEMORY_TYPES

# The initial vector path intentionally uses a bounded exact scan.  It is a
# cache enhancement, not a reason to make a live interviewer turn wait for a
# model call, background build, or unbounded database result.
SEMANTIC_CANDIDATE_LIMIT = 64
SEMANTIC_SCORE_WEIGHT = 4.0


@dataclass(frozen=True, slots=True)
class MemoryHit:
    memory: MemoryItem
    score: float
    reason: str


@dataclass(slots=True)
class _RetrievalCandidate:
    memory: MemoryItem
    usage_count: int
    database_rank: float
    semantic_score: float = 0.0


@dataclass(frozen=True, slots=True)
class _SemanticCandidate:
    memory: MemoryItem
    usage_count: int
    score: float


def _terms(value: str) -> set[str]:
    lowered = value.casefold()
    ascii_terms = set(re.findall(r"[a-z0-9_+#.-]{2,}", lowered))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", lowered)
    chinese_terms = {
        run[index : index + 2]
        for run in chinese_runs
        for index in range(max(1, len(run) - 1))
        if len(run[index : index + 2]) == 2
    }
    return ascii_terms | chinese_terms


def _lexical_score(query: str, memory: MemoryItem) -> float:
    query_terms = _terms(query)
    if not query_terms:
        return 0
    document = " ".join(
        [memory.canonical_key, memory.content, str(memory.structured_value)]
    ).casefold()
    matched = sum(term in document for term in query_terms)
    return matched / len(query_terms)


def _recency_score(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    age_days = max(0.0, (utc_now() - value).total_seconds() / 86_400)
    return math.exp(-age_days / 180)


def _context_bonus(
    memory: MemoryItem,
    *,
    company_id: uuid.UUID | None,
    role_name: str | None,
) -> float:
    bonus = 0.0
    memory_company = memory.structured_value.get("company_id")
    memory_role = str(memory.structured_value.get("role_name", "")).casefold()
    if company_id and memory_company == str(company_id):
        bonus += 0.75
    if role_name and memory_role and memory_role == role_name.casefold():
        bonus += 1.0
    return bonus


def _semantic_distance(distance_metric: str):
    """Return the pgvector distance expression for the stored profile contract."""

    if distance_metric == "cosine":
        return MemoryEmbedding.embedding.cosine_distance(PlanQuestionEmbedding.embedding)
    if distance_metric == "l2":
        return MemoryEmbedding.embedding.l2_distance(PlanQuestionEmbedding.embedding)
    if distance_metric == "inner_product":
        return MemoryEmbedding.embedding.max_inner_product(PlanQuestionEmbedding.embedding)
    raise ValueError("unsupported embedding distance metric")


def _semantic_relevance(distance: object, *, distance_metric: str) -> float:
    """Map a pgvector distance to a bounded, ranking-safe relevance score."""

    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    if distance_metric == "cosine":
        # pgvector cosine distance is ``1 - cosine_similarity``.
        return max(0.0, min(1.0, 1.0 - value))
    if distance_metric == "l2":
        return 1.0 / (1.0 + max(0.0, value))
    if distance_metric == "inner_product":
        # pgvector exposes negative inner product as a distance so that lower
        # values sort first.  Clamp to prevent an unnormalised provider from
        # overwhelming the existing confidence/recency ranking.
        return max(0.0, min(1.0, -value))
    return 0.0


async def _read_cached_semantic_candidates(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    plan_question_id: uuid.UUID,
    allowed_types: set[MemoryType],
    usage_counts: object,
    source_exists: object,
) -> list[_SemanticCandidate]:
    """Read only one promoted vector space and its persisted question vector.

    This function deliberately receives a plan-question *ID*, never question
    text.  The query vector is selected inside PostgreSQL from
    ``PlanQuestionEmbedding`` and compared to ``MemoryEmbedding`` in the same
    active profile, so the live request cannot call an embedding provider or
    send user/interview data over the network.
    """

    active_profile = await session.scalar(
        select(EmbeddingProfile).where(
            EmbeddingProfile.profile_id == profile_id,
            EmbeddingProfile.status == EmbeddingProfileStatus.ACTIVE,
            EmbeddingProfile.vector_dimensions.is_not(None),
            EmbeddingProfile.deleted_at.is_(None),
        )
    )
    if active_profile is None:
        return []
    distance_metric = str(active_profile.distance_metric)
    try:
        distance = _semantic_distance(distance_metric).label("semantic_distance")
    except ValueError:
        return []
    rows = (
        await session.execute(
            select(
                MemoryItem,
                func.coalesce(usage_counts.c.usage_count, 0),
                distance,
            )
            .join(
                MemoryEmbedding,
                and_(
                    MemoryEmbedding.memory_id == MemoryItem.id,
                    MemoryEmbedding.profile_id == profile_id,
                    MemoryEmbedding.embedding_profile_id == active_profile.id,
                    MemoryEmbedding.source_version == MemoryItem.version,
                    MemoryEmbedding.deleted_at.is_(None),
                ),
            )
            # The profile/question filters below make this a single cached
            # query vector.  An explicit cross join avoids materialising it in
            # Python (and therefore avoids an asyncpg vector codec dependency
            # on the live interview path).
            .join(PlanQuestionEmbedding, true())
            .join(
                PlanQuestion,
                and_(
                    PlanQuestion.id == PlanQuestionEmbedding.plan_question_id,
                    PlanQuestion.plan_id == PlanQuestionEmbedding.plan_id,
                    PlanQuestion.version == PlanQuestionEmbedding.source_version,
                    PlanQuestion.deleted_at.is_(None),
                ),
            )
            .outerjoin(usage_counts, usage_counts.c.memory_id == MemoryItem.id)
            .where(
                PlanQuestionEmbedding.profile_id == profile_id,
                PlanQuestionEmbedding.embedding_profile_id == active_profile.id,
                PlanQuestionEmbedding.plan_question_id == plan_question_id,
                PlanQuestionEmbedding.deleted_at.is_(None),
                MemoryItem.profile_id == profile_id,
                MemoryItem.status == MemoryStatus.ACTIVE,
                MemoryItem.memory_type.in_(allowed_types),
                MemoryItem.deleted_at.is_(None),
                or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > utc_now()),
                source_exists,
            )
            .order_by(distance.asc(), MemoryItem.pinned.desc(), MemoryItem.id)
            .limit(SEMANTIC_CANDIDATE_LIMIT)
        )
    ).all()
    candidates: list[_SemanticCandidate] = []
    for memory, usage_count, raw_distance in rows:
        relevance = _semantic_relevance(raw_distance, distance_metric=distance_metric)
        if relevance <= 0:
            continue
        candidates.append(
            _SemanticCandidate(
                memory=memory,
                usage_count=int(usage_count or 0),
                score=relevance,
            )
        )
    return candidates


async def _cached_semantic_candidates_or_empty(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    plan_question_id: uuid.UUID | None,
    allowed_types: set[MemoryType],
    usage_counts: object,
    source_exists: object,
) -> list[_SemanticCandidate]:
    """Treat all cache/pgvector failures as an FTS-only retrieval.

    A savepoint keeps a malformed cache row, a missing pgvector capability, or
    an operational query error from poisoning the outer transaction before the
    existing FTS query runs.  ``CancelledError`` is a ``BaseException`` and is
    intentionally not swallowed.
    """

    if plan_question_id is None:
        return []
    try:
        async with session.begin_nested():
            # Exact vector scans deliberately have no ANN index yet because
            # profiles may use different dimensions. Bound their database work
            # tightly, then reset the setting before FTS continues below.
            await session.execute(
                select(
                    func.set_config(
                        "statement_timeout",
                        str(settings.semantic_retrieval_statement_timeout_ms),
                        True,
                    )
                )
            )
            candidates = await _read_cached_semantic_candidates(
                session,
                profile_id=profile_id,
                plan_question_id=plan_question_id,
                allowed_types=allowed_types,
                usage_counts=usage_counts,
                source_exists=source_exists,
            )
            await session.execute(select(func.set_config("statement_timeout", "0", True)))
            return candidates
    except Exception:
        return []


async def retrieve_memories(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    agent_role: ModelRole,
    query: str,
    company_id: uuid.UUID | None = None,
    role_name: str | None = None,
    semantic_plan_question_id: uuid.UUID | None = None,
    limit: int = 8,
) -> list[MemoryHit]:
    profile = await session.get(UserProfile, profile_id)
    allowed_types = ROLE_MEMORY_TYPES.get(agent_role, set())
    if not profile or not profile.memory_enabled or not allowed_types or limit <= 0:
        return []

    usage_counts = (
        select(MemoryUsage.memory_id, func.count(MemoryUsage.id).label("usage_count"))
        .where(MemoryUsage.deleted_at.is_(None))
        .group_by(MemoryUsage.memory_id)
        .subquery()
    )
    source_exists = (
        select(MemorySource.id)
        .where(
            MemorySource.memory_id == MemoryItem.id,
            MemorySource.deleted_at.is_(None),
        )
        .exists()
    )
    document = func.to_tsvector(
        "simple",
        func.concat(MemoryItem.canonical_key, " ", MemoryItem.content),
    )
    search_query = func.plainto_tsquery("simple", query or "memory")
    text_rank = func.ts_rank_cd(document, search_query).label("text_rank")
    rows = (
        await session.execute(
            select(
                MemoryItem,
                func.coalesce(usage_counts.c.usage_count, 0),
                text_rank,
            )
            .outerjoin(usage_counts, usage_counts.c.memory_id == MemoryItem.id)
            .where(
                MemoryItem.profile_id == profile_id,
                MemoryItem.status == MemoryStatus.ACTIVE,
                MemoryItem.memory_type.in_(allowed_types),
                MemoryItem.deleted_at.is_(None),
                or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > utc_now()),
                source_exists,
            )
            .order_by(MemoryItem.pinned.desc(), text_rank.desc(), MemoryItem.confidence.desc())
            .limit(200)
        )
    ).all()

    semantic_candidates = await _cached_semantic_candidates_or_empty(
        session,
        profile_id=profile_id,
        plan_question_id=semantic_plan_question_id,
        allowed_types=allowed_types,
        usage_counts=usage_counts,
        source_exists=source_exists,
    )
    candidates = {
        memory.id: _RetrievalCandidate(
            memory=memory,
            usage_count=int(usage_count or 0),
            database_rank=float(database_rank or 0),
        )
        for memory, usage_count, database_rank in rows
    }
    for semantic in semantic_candidates:
        existing = candidates.get(semantic.memory.id)
        if existing is None:
            candidates[semantic.memory.id] = _RetrievalCandidate(
                memory=semantic.memory,
                usage_count=semantic.usage_count,
                database_rank=0.0,
                semantic_score=semantic.score,
            )
        else:
            existing.semantic_score = max(existing.semantic_score, semantic.score)

    hits: list[MemoryHit] = []
    for candidate in candidates.values():
        memory = candidate.memory
        lexical = _lexical_score(query, memory)
        explicit_bonus = (
            1.5
            if memory.memory_type
            in {
                MemoryType.PROJECT_FACT,
                MemoryType.COMMUNICATION_PREFERENCE,
                MemoryType.INTERVIEW_PREFERENCE,
                MemoryType.PRACTICE_GOAL,
            }
            else 0.0
        )
        score = (
            (candidate.database_rank * 4)
            + (lexical * 4)
            + (candidate.semantic_score * SEMANTIC_SCORE_WEIGHT)
            + (3.0 if memory.pinned else 0.0)
            + explicit_bonus
            + (memory.confidence * 2)
            + _recency_score(memory.last_verified_at or memory.first_observed_at)
            + _context_bonus(memory, company_id=company_id, role_name=role_name)
            - (min(candidate.usage_count, 10) * 0.15)
        )
        always_relevant = memory.memory_type in {
            MemoryType.COMMUNICATION_PREFERENCE,
            MemoryType.INTERVIEW_PREFERENCE,
        }
        if (
            lexical <= 0
            and candidate.semantic_score <= 0
            and not memory.pinned
            and not always_relevant
            and not _context_bonus(memory, company_id=company_id, role_name=role_name)
        ):
            continue
        reasons = [f"text={lexical:.2f}"]
        if candidate.semantic_score > 0:
            reasons.append(f"semantic={candidate.semantic_score:.2f}")
        if memory.pinned:
            reasons.append("pinned")
        if explicit_bonus:
            reasons.append("explicit")
        hits.append(MemoryHit(memory=memory, score=score, reason=",".join(reasons)))
    hits.sort(key=lambda item: (-item.score, str(item.memory.id)))
    return hits[:limit]
