"""Persist source-grounded Researcher candidates without importing them into a bank."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.input_budget import AgentInputBudgetError
from app.agents.question_researcher import (
    QuestionResearcherError,
    ResearcherResult,
    ResearcherSource,
    curate_questions,
)
from app.api.errors import AppError
from app.core.crypto import SecretDecryptionError
from app.db.models.common import DiscoverySourceStatus, ModelRole
from app.db.models.discovery import (
    QuestionDiscoveryCandidate,
    QuestionDiscoveryCandidateEvidence,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
)
from app.db.models.question import Question, QuestionBank
from app.providers.base import ProviderError
from app.providers.factory import build_provider
from app.services.model_connections import resolve_explicit_role_connection
from app.services.questions import prompt_hash


@dataclass(frozen=True, slots=True)
class CurationOutcome:
    """A content-free curation result that the discovery worker can surface safely."""

    status: str
    candidate_count: int
    error_code: str | None = None
    error_summary: str | None = None

    @property
    def has_recoverable_failure(self) -> bool:
        return self.status == "failed"


CancellationCheck = Callable[[], Awaitable[None]]


def _safe_query_context(snapshot: dict) -> dict:
    """Never relay raw pasted URLs or unrelated profile data to the Researcher."""

    allowed = (
        "source_mode",
        "company",
        "round",
        "role",
        "skills",
        "keywords",
        "question_type",
        "difficulty",
    )
    return {key: snapshot[key] for key in allowed if key in snapshot}


async def _run_sources(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
) -> list[QuestionDiscoverySource]:
    rows = await session.scalars(
        select(QuestionDiscoverySource)
        .where(
            QuestionDiscoverySource.profile_id == run.profile_id,
            QuestionDiscoverySource.run_id == run.id,
            QuestionDiscoverySource.deleted_at.is_(None),
            QuestionDiscoverySource.status == DiscoverySourceStatus.FETCHED,
            QuestionDiscoverySource.excerpt.is_not(None),
        )
        .order_by(QuestionDiscoverySource.created_at, QuestionDiscoverySource.id)
    )
    return [source for source in rows.all() if source.excerpt]


def _researcher_sources(sources: Sequence[QuestionDiscoverySource]) -> list[ResearcherSource]:
    return [
        ResearcherSource(
            source_id=source.id,
            title=source.title or source.domain,
            domain=source.domain,
            source_category=source.source_category,
            excerpt=source.excerpt or "",
        )
        for source in sources
    ]


def _evidence_hash(candidate_hash: str, source_id: uuid.UUID, excerpt: str) -> str:
    canonical = "\n".join((candidate_hash, str(source_id), " ".join(excerpt.split())))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _similar_question_ids(
    session: AsyncSession,
    profile_id: uuid.UUID,
    content_hash: str,
) -> list[str]:
    rows = await session.scalars(
        select(Question.id)
        .join(QuestionBank, QuestionBank.id == Question.bank_id)
        .where(
            QuestionBank.profile_id == profile_id,
            QuestionBank.deleted_at.is_(None),
            Question.deleted_at.is_(None),
            Question.normalized_hash == content_hash,
        )
        .order_by(Question.created_at, Question.id)
        .limit(5)
    )
    return [str(question_id) for question_id in rows.all()]


async def _persist_candidates(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    result: ResearcherResult,
    *,
    researcher_connection_id: uuid.UUID,
    researcher_model_name: str,
) -> int:
    existing_hashes = set(
        (
            await session.scalars(
                select(QuestionDiscoveryCandidate.content_hash).where(
                    QuestionDiscoveryCandidate.profile_id == run.profile_id,
                    QuestionDiscoveryCandidate.run_id == run.id,
                    QuestionDiscoveryCandidate.deleted_at.is_(None),
                )
            )
        ).all()
    )
    inserted = 0
    for item in result.candidates:
        content_hash = prompt_hash(item.prompt)
        if content_hash in existing_hashes:
            continue
        candidate = QuestionDiscoveryCandidate(
            profile_id=run.profile_id,
            run_id=run.id,
            prompt=item.prompt,
            question_type=item.question_type,
            difficulty=item.difficulty,
            suggested_tags=list(item.suggested_tags),
            suggested_roles=list(item.suggested_roles),
            suggested_skills=list(item.suggested_skills),
            applicable_companies=list(item.applicable_companies),
            applicable_rounds=list(item.applicable_rounds),
            reference_points=list(item.reference_points),
            follow_up_suggestions=list(item.follow_up_suggestions),
            matching_reason=item.matching_reason,
            confidence=item.confidence,
            researcher_connection_id=researcher_connection_id,
            researcher_model_name=researcher_model_name,
            content_hash=content_hash,
            similar_question_ids=await _similar_question_ids(
                session,
                run.profile_id,
                content_hash,
            ),
            expires_at=run.expires_at,
        )
        session.add(candidate)
        await session.flush()
        session.add_all(
            [
                QuestionDiscoveryCandidateEvidence(
                    profile_id=run.profile_id,
                    run_id=run.id,
                    candidate_id=candidate.id,
                    source_id=evidence.source_id,
                    excerpt=evidence.excerpt,
                    source_locator=evidence.source_locator,
                    confidence=evidence.confidence,
                    evidence_hash=_evidence_hash(
                        content_hash,
                        evidence.source_id,
                        evidence.excerpt,
                    ),
                    expires_at=run.expires_at,
                )
                for evidence in item.evidence
            ]
        )
        existing_hashes.add(content_hash)
        inserted += 1
    await session.flush()
    total = await session.scalar(
        select(func.count())
        .select_from(QuestionDiscoveryCandidate)
        .where(
            QuestionDiscoveryCandidate.profile_id == run.profile_id,
            QuestionDiscoveryCandidate.run_id == run.id,
            QuestionDiscoveryCandidate.deleted_at.is_(None),
        )
    )
    run.candidate_count = int(total or 0)
    run.touch()
    await session.flush()
    return inserted


async def curate_discovery_run(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    *,
    cancellation_check: CancellationCheck | None = None,
) -> CurationOutcome:
    """Ask an explicitly bound Researcher to curate a completed source-card run.

    This service never commits.  The caller owns the run lifecycle transaction and can
    enforce cancellation immediately before and after external model work.
    """

    if cancellation_check is not None:
        await cancellation_check()
    sources = await _run_sources(session, run)
    if not sources:
        return CurationOutcome(status="skipped", candidate_count=0)

    try:
        connection = await resolve_explicit_role_connection(
            session,
            run.profile_id,
            ModelRole.RESEARCHER,
        )
    except AppError as exc:
        if exc.code != "model_role_unbound":
            raise
        return CurationOutcome(
            status="skipped",
            candidate_count=0,
            error_code="discovery_researcher_unbound",
            error_summary="未配置 Researcher 模型，已保留来源卡片供手动整理。",
        )

    provider = None
    try:
        provider = build_provider(connection)
        result = await curate_questions(
            provider,
            _researcher_sources(sources),
            query_context=_safe_query_context(run.query_snapshot),
            context_window_tokens=connection.context_window_tokens,
            max_output_tokens=connection.max_output_tokens,
            tokenizer_type=connection.tokenizer_type,
        )
    except AgentInputBudgetError:
        return CurationOutcome(
            status="failed",
            candidate_count=0,
            error_code="discovery_budget_exceeded",
            error_summary="来源摘要超过 Researcher 的安全上下文预算，已保留来源卡片。",
        )
    except QuestionResearcherError:
        return CurationOutcome(
            status="failed",
            candidate_count=0,
            error_code="discovery_researcher_output_invalid",
            error_summary="Researcher 返回的候选题目无法通过来源证据校验，已保留来源卡片。",
        )
    except (ProviderError, SecretDecryptionError):
        return CurationOutcome(
            status="failed",
            candidate_count=0,
            error_code="discovery_researcher_unavailable",
            error_summary="Researcher 当前不可用，已保留来源卡片供手动整理。",
        )
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()

    if cancellation_check is not None:
        await cancellation_check()
    inserted = await _persist_candidates(
        session,
        run,
        result,
        researcher_connection_id=connection.id,
        researcher_model_name=connection.model_name,
    )
    return CurationOutcome(status="curated", candidate_count=inserted)
