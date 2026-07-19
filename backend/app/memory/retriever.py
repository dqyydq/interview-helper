import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.common import MemoryStatus, MemoryType, ModelRole, utc_now
from app.db.models.memory import MemoryItem, MemorySource, MemoryUsage
from app.db.models.profile import UserProfile
from app.memory.types import ROLE_MEMORY_TYPES


@dataclass(frozen=True, slots=True)
class MemoryHit:
    memory: MemoryItem
    score: float
    reason: str


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


async def retrieve_memories(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    agent_role: ModelRole,
    query: str,
    company_id: uuid.UUID | None = None,
    role_name: str | None = None,
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

    hits: list[MemoryHit] = []
    for memory, usage_count, database_rank in rows:
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
            (float(database_rank or 0) * 4)
            + (lexical * 4)
            + (3.0 if memory.pinned else 0.0)
            + explicit_bonus
            + (memory.confidence * 2)
            + _recency_score(memory.last_verified_at or memory.first_observed_at)
            + _context_bonus(memory, company_id=company_id, role_name=role_name)
            - (min(int(usage_count or 0), 10) * 0.15)
        )
        always_relevant = memory.memory_type in {
            MemoryType.COMMUNICATION_PREFERENCE,
            MemoryType.INTERVIEW_PREFERENCE,
        }
        if (
            lexical <= 0
            and not memory.pinned
            and not always_relevant
            and not _context_bonus(memory, company_id=company_id, role_name=role_name)
        ):
            continue
        reasons = [f"text={lexical:.2f}"]
        if memory.pinned:
            reasons.append("pinned")
        if explicit_bonus:
            reasons.append("explicit")
        hits.append(MemoryHit(memory=memory, score=score, reason=",".join(reasons)))
    hits.sort(key=lambda item: (-item.score, str(item.memory.id)))
    return hits[:limit]
