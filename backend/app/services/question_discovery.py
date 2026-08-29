"""Run lifecycle services for bounded, source-aware question discovery.

This module deliberately stops at source cards.  It never asks an LLM to invent a
question, never imports a source into a bank, and never exposes raw pasted URLs in a
run response.  Those operations have their own later lifecycle boundaries.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.db.models.common import (
    DiscoveryRunStatus,
    DiscoverySourceMode,
    JobStatus,
    JobType,
    utc_now,
)
from app.db.models.discovery import (
    DiscoveryConnector,
    QuestionDiscoveryCandidate,
    QuestionDiscoveryCandidateEvidence,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
)
from app.db.models.job import BackgroundJob
from app.discovery.source_urls import verified_source_url
from app.discovery.url_policy import DomainPolicy, URLPolicyError
from app.schemas.discovery import (
    QuestionDiscoveryCandidateEvidencePublic,
    QuestionDiscoveryCandidatePage,
    QuestionDiscoveryCandidatePublic,
    QuestionDiscoveryCreate,
    QuestionDiscoveryRunPage,
    QuestionDiscoveryRunPublic,
    QuestionDiscoverySourcePage,
    QuestionDiscoverySourcePublic,
)
from app.services import discovery_connectors

# This is an intentionally modest, reviewable preset.  It labels a search scope; it
# does not endorse any result as an official interview standard or crawl a site.
CN_INTERVIEW_TECH_PRESET = (
    "nowcoder.com",
    "juejin.cn",
    "zhihu.com",
    "csdn.net",
    "segmentfault.com",
    "leetcode.cn",
)

MAX_URLS = 5
MAX_SEARCH_RESULTS = 20
MAX_SOURCES = 12
TERMINAL_RUN_STATUSES = frozenset(
    {
        DiscoveryRunStatus.SUCCEEDED,
        DiscoveryRunStatus.PARTIAL,
        DiscoveryRunStatus.NO_RESULTS,
        DiscoveryRunStatus.FAILED,
        DiscoveryRunStatus.CANCELLED,
    }
)
ACTIVE_RUN_STATUSES = frozenset(
    {
        DiscoveryRunStatus.QUEUED,
        DiscoveryRunStatus.RUNNING,
        DiscoveryRunStatus.CANCEL_REQUESTED,
    }
)


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def max_urls() -> int:
    """Keep the persisted/request limit bounded even if deployment config drifts."""

    return min(settings.discovery_max_urls, MAX_URLS)


def max_search_results() -> int:
    return min(settings.discovery_max_search_results, MAX_SEARCH_RESULTS)


def max_sources() -> int:
    return min(settings.discovery_max_sources, MAX_SOURCES)


def is_terminal(status: DiscoveryRunStatus | str) -> bool:
    return DiscoveryRunStatus(status) in TERMINAL_RUN_STATUSES


def _safe_error(code: str, message: str, *, status_code: int = 409) -> AppError:
    return AppError(code=code, message=message, status_code=status_code)


def _profile_active_run_lock_key(profile_id: uuid.UUID) -> int:
    """Return a stable, namespaced PostgreSQL advisory-lock key for one profile."""

    digest = hashlib.sha256(f"question-discovery-active-runs:{profile_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _lock_profile_active_runs(session: AsyncSession, profile_id: uuid.UUID) -> None:
    """Serialize active-run count/create decisions for a profile until commit."""

    await session.execute(
        select(func.pg_advisory_xact_lock(_profile_active_run_lock_key(profile_id)))
    )


def _effective_domain_policy(payload: QuestionDiscoveryCreate) -> tuple[DomainPolicy, dict]:
    """Normalize the one run's domain policy before it is stored or used.

    An explicitly supplied allow-list always narrows scope.  Full-web only removes
    the default preset when no custom allow-list was supplied; deny rules still win.
    """

    allow_domains = list(payload.allow_domains)
    if not allow_domains and not payload.full_web:
        allow_domains = list(CN_INTERVIEW_TECH_PRESET)
    try:
        policy = DomainPolicy(
            allow_domains=tuple(allow_domains),
            deny_domains=tuple(payload.deny_domains),
            require_allowlist=False,
        )
    except URLPolicyError as exc:
        raise _safe_error(
            "discovery_domain_policy_invalid",
            "来源域名策略无效，请检查白名单和黑名单。",
            status_code=422,
        ) from exc
    preset = "cn_interview_tech" if not payload.full_web and not payload.allow_domains else None
    return policy, {
        "preset": preset,
        "full_web": payload.full_web,
        "allow_domains": list(policy.allow_domains),
        "deny_domains": list(policy.deny_domains),
    }


def _search_text(payload: QuestionDiscoveryCreate) -> str:
    """Build a provider query from only explicitly submitted discovery fields."""

    if payload.query:
        return payload.query
    values = [payload.company, payload.round, payload.role, *payload.skills, *payload.keywords]
    text = " ".join(value for value in values if value)
    question_type = _enum_value(payload.question_type)
    difficulty = _enum_value(payload.difficulty)
    suffix = " ".join(value for value in (question_type, difficulty, "面试题") if value)
    return " ".join(value for value in (text, suffix) if value).strip()


def build_query_snapshot(
    payload: QuestionDiscoveryCreate,
    connector: DiscoveryConnector,
) -> dict:
    """Create the minimal worker input retained with the run.

    The background job itself stores only ``run_id``.  User supplied URLs need to be
    available to the worker, so they live only in this short-lived run snapshot and
    are stripped from every public run response.
    """

    policy, policy_snapshot = _effective_domain_policy(payload)
    country = payload.country or connector.configuration.get("default_country")
    mode = DiscoverySourceMode(payload.source_mode)
    snapshot = {
        "source_mode": mode.value,
        # Persist the selected fixed-endpoint provider for source attribution only.
        # This is non-sensitive and does not expose a connector name or credential.
        "provider": str(connector.provider_type),
        "company": payload.company,
        "round": payload.round,
        "role": payload.role,
        "skills": list(payload.skills),
        "keywords": list(payload.keywords),
        "question_type": _enum_value(payload.question_type),
        "difficulty": _enum_value(payload.difficulty),
        "country": country,
        "domain_policy": policy_snapshot,
    }
    if mode is DiscoverySourceMode.SEARCH:
        snapshot["search_query"] = _search_text(payload)
    else:
        urls = list(payload.urls[: max_urls()])
        if len(urls) != len(payload.urls):
            raise _safe_error(
                "discovery_url_limit_exceeded",
                "一次最多可处理 5 个链接。",
                status_code=422,
            )
        snapshot["urls"] = urls
    # Force the property to be evaluated now, including normalization and policy
    # validation, rather than postponing an invalid policy to a worker.
    _ = policy.allow_domains
    return snapshot


def public_query_snapshot(snapshot: dict) -> dict:
    """Return a reviewable snapshot without raw pasted URL values."""

    public = {key: value for key, value in snapshot.items() if key != "urls"}
    if "urls" in snapshot:
        public["url_count"] = len(snapshot["urls"])
    return public


def run_public(run: QuestionDiscoveryRun) -> QuestionDiscoveryRunPublic:
    return QuestionDiscoveryRunPublic(
        id=run.id,
        created_at=run.created_at,
        updated_at=run.updated_at,
        version=run.version,
        connector_id=run.connector_id,
        connector_configuration_version=run.connector_configuration_version,
        initiated_by=run.initiated_by,
        source_mode=DiscoverySourceMode(run.source_mode),
        query_snapshot=public_query_snapshot(run.query_snapshot),
        status=DiscoveryRunStatus(run.status),
        stage=run.stage,
        progress=run.progress,
        source_count=run.source_count,
        candidate_count=run.candidate_count,
        failed_source_count=run.failed_source_count,
        error_code=run.error_code,
        error_summary=run.error_summary,
        cancel_requested_at=run.cancel_requested_at,
        completed_at=run.completed_at,
        expires_at=run.expires_at,
    )


def source_public(source: QuestionDiscoverySource) -> QuestionDiscoverySourcePublic:
    return QuestionDiscoverySourcePublic(
        id=source.id,
        created_at=source.created_at,
        updated_at=source.updated_at,
        version=source.version,
        run_id=source.run_id,
        normalized_url=source.normalized_url,
        final_url=source.final_url,
        title=source.title,
        domain=source.domain,
        source_category=source.source_category,
        status=source.status,
        fetched_at=source.fetched_at,
        excerpt=source.excerpt,
        attribution=source.attribution,
        policy_metadata=source.policy_metadata,
        failure_code=source.failure_code,
        failure_summary=source.failure_summary,
        expires_at=source.expires_at,
    )


def candidate_public(candidate: QuestionDiscoveryCandidate) -> QuestionDiscoveryCandidatePublic:
    return QuestionDiscoveryCandidatePublic.model_validate(candidate)


def candidate_evidence_public(
    evidence: QuestionDiscoveryCandidateEvidence,
    source: QuestionDiscoverySource,
) -> QuestionDiscoveryCandidateEvidencePublic:
    return QuestionDiscoveryCandidateEvidencePublic(
        id=evidence.id,
        created_at=evidence.created_at,
        updated_at=evidence.updated_at,
        version=evidence.version,
        run_id=evidence.run_id,
        candidate_id=evidence.candidate_id,
        source_id=evidence.source_id,
        source_title=source.title or source.domain,
        normalized_url=verified_source_url(
            normalized_url=source.normalized_url,
            final_url=source.final_url,
        ),
        source_domain=source.domain,
        source_category=source.source_category,
        excerpt=evidence.excerpt,
        source_locator=evidence.source_locator,
        confidence=evidence.confidence,
    )


async def get_run(
    session: AsyncSession,
    profile_id: uuid.UUID,
    run_id: uuid.UUID,
) -> QuestionDiscoveryRun:
    run = await session.scalar(
        select(QuestionDiscoveryRun).where(
            QuestionDiscoveryRun.id == run_id,
            QuestionDiscoveryRun.profile_id == profile_id,
            QuestionDiscoveryRun.deleted_at.is_(None),
        )
    )
    if run is None:
        raise _safe_error("question_discovery_not_found", "题目发现记录不存在。", status_code=404)
    return run


async def list_runs(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int = 100,
) -> QuestionDiscoveryRunPage:
    filters = (
        QuestionDiscoveryRun.profile_id == profile_id,
        QuestionDiscoveryRun.deleted_at.is_(None),
    )
    count = await session.scalar(
        select(func.count()).select_from(QuestionDiscoveryRun).where(*filters)
    )
    rows = await session.scalars(
        select(QuestionDiscoveryRun)
        .where(*filters)
        .order_by(QuestionDiscoveryRun.created_at.desc(), QuestionDiscoveryRun.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return QuestionDiscoveryRunPage(
        data=[run_public(row) for row in rows.all()],
        count=int(count or 0),
        offset=offset,
        limit=limit,
    )


async def create_run(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: QuestionDiscoveryCreate,
) -> QuestionDiscoveryRun:
    connector = await discovery_connectors.get_connector(session, profile_id, payload.connector_id)
    if not connector.enabled or connector.deleted_at is not None:
        raise _safe_error(
            "discovery_connector_unavailable",
            "题目发现连接器当前不可用，请先检查连接器设置。",
        )
    if not connector.encrypted_api_key:
        raise _safe_error(
            "discovery_connector_missing_api_key",
            "题目发现连接器没有可用密钥。",
        )
    mode = DiscoverySourceMode(payload.source_mode)
    if mode is DiscoverySourceMode.URLS and not (
        connector.capabilities.get("supports_extract")
        and connector.capabilities.get("safe_extract")
    ):
        raise _safe_error(
            "discovery_connector_extract_unsupported",
            "该连接器不支持受控链接提取。",
            status_code=422,
        )

    query_snapshot = build_query_snapshot(payload, connector)
    # The advisory lock is released by the final commit (or request rollback).  The
    # count and queued run/job creation must share this transaction to enforce the
    # profile cap across concurrent API requests and worker processes.
    await _lock_profile_active_runs(session, profile_id)
    active_count = await session.scalar(
        select(func.count())
        .select_from(QuestionDiscoveryRun)
        .where(
            QuestionDiscoveryRun.profile_id == profile_id,
            QuestionDiscoveryRun.deleted_at.is_(None),
            QuestionDiscoveryRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    if int(active_count or 0) >= settings.discovery_max_concurrent_runs_per_profile:
        raise _safe_error(
            "discovery_run_concurrency_limit",
            "当前进行中的题目发现任务已达到上限，请等待已有任务完成。",
        )

    run = QuestionDiscoveryRun(
        profile_id=profile_id,
        connector_id=connector.id,
        connector_configuration_version=connector.configuration_version,
        source_mode=mode,
        query_snapshot=query_snapshot,
        status=DiscoveryRunStatus.QUEUED,
        stage="queued",
        progress=0.0,
    )
    session.add(run)
    await session.flush()
    session.add(
        BackgroundJob(
            profile_id=profile_id,
            job_type=JobType.QUESTION_DISCOVERY,
            status=JobStatus.QUEUED,
            progress=0.0,
            payload={"run_id": str(run.id)},
            # The run id makes this unique while the profile prefix documents that a
            # worker must not treat a naked UUID as authorization.
            idempotency_key=f"question-discovery:{profile_id}:{run.id}",
            max_attempts=1,
        )
    )
    await session.commit()
    await session.refresh(run)
    return run


async def list_sources(
    session: AsyncSession,
    profile_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int = 100,
) -> QuestionDiscoverySourcePage:
    await get_run(session, profile_id, run_id)
    filters = (
        QuestionDiscoverySource.profile_id == profile_id,
        QuestionDiscoverySource.run_id == run_id,
        QuestionDiscoverySource.deleted_at.is_(None),
    )
    count = await session.scalar(
        select(func.count()).select_from(QuestionDiscoverySource).where(*filters)
    )
    rows = await session.scalars(
        select(QuestionDiscoverySource)
        .where(*filters)
        .order_by(QuestionDiscoverySource.created_at, QuestionDiscoverySource.id)
        .offset(offset)
        .limit(limit)
    )
    return QuestionDiscoverySourcePage(
        data=[source_public(row) for row in rows.all()],
        count=int(count or 0),
        offset=offset,
        limit=limit,
    )


async def list_candidates(
    session: AsyncSession,
    profile_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int = 100,
) -> QuestionDiscoveryCandidatePage:
    """Expose an empty, stable endpoint until the Researcher milestone writes rows."""

    await get_run(session, profile_id, run_id)
    filters = (
        QuestionDiscoveryCandidate.profile_id == profile_id,
        QuestionDiscoveryCandidate.run_id == run_id,
        QuestionDiscoveryCandidate.deleted_at.is_(None),
    )
    count = await session.scalar(
        select(func.count()).select_from(QuestionDiscoveryCandidate).where(*filters)
    )
    rows = await session.scalars(
        select(QuestionDiscoveryCandidate)
        .where(*filters)
        .order_by(QuestionDiscoveryCandidate.created_at, QuestionDiscoveryCandidate.id)
        .offset(offset)
        .limit(limit)
    )
    return QuestionDiscoveryCandidatePage(
        data=[candidate_public(row) for row in rows.all()],
        count=int(count or 0),
        offset=offset,
        limit=limit,
    )


async def list_candidate_evidence(
    session: AsyncSession,
    profile_id: uuid.UUID,
    run_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> list[QuestionDiscoveryCandidateEvidencePublic]:
    await get_run(session, profile_id, run_id)
    candidate = await session.scalar(
        select(QuestionDiscoveryCandidate.id).where(
            QuestionDiscoveryCandidate.id == candidate_id,
            QuestionDiscoveryCandidate.profile_id == profile_id,
            QuestionDiscoveryCandidate.run_id == run_id,
            QuestionDiscoveryCandidate.deleted_at.is_(None),
        )
    )
    if candidate is None:
        raise _safe_error("discovery_candidate_not_found", "候选题目不存在。", status_code=404)
    rows = await session.execute(
        select(QuestionDiscoveryCandidateEvidence, QuestionDiscoverySource)
        .join(
            QuestionDiscoverySource,
            QuestionDiscoverySource.id == QuestionDiscoveryCandidateEvidence.source_id,
        )
        .where(
            QuestionDiscoveryCandidateEvidence.profile_id == profile_id,
            QuestionDiscoveryCandidateEvidence.run_id == run_id,
            QuestionDiscoveryCandidateEvidence.candidate_id == candidate_id,
            QuestionDiscoveryCandidateEvidence.deleted_at.is_(None),
            QuestionDiscoverySource.profile_id == profile_id,
            QuestionDiscoverySource.run_id == run_id,
            QuestionDiscoverySource.deleted_at.is_(None),
        )
        .order_by(
            QuestionDiscoveryCandidateEvidence.created_at,
            QuestionDiscoveryCandidateEvidence.id,
        )
    )
    return [candidate_evidence_public(evidence, source) for evidence, source in rows.all()]


async def request_cancel(
    session: AsyncSession,
    profile_id: uuid.UUID,
    run_id: uuid.UUID,
) -> QuestionDiscoveryRun:
    """Use a status-guarded update so a terminal worker cannot be overwritten."""

    now = utc_now()
    result = await session.execute(
        update(QuestionDiscoveryRun)
        .where(
            QuestionDiscoveryRun.id == run_id,
            QuestionDiscoveryRun.profile_id == profile_id,
            QuestionDiscoveryRun.deleted_at.is_(None),
            QuestionDiscoveryRun.status.in_(
                (DiscoveryRunStatus.QUEUED, DiscoveryRunStatus.RUNNING)
            ),
        )
        .values(
            status=DiscoveryRunStatus.CANCEL_REQUESTED,
            cancel_requested_at=now,
            stage="cancelling",
            updated_at=now,
            version=QuestionDiscoveryRun.version + 1,
        )
        .returning(QuestionDiscoveryRun)
    )
    run = result.scalar_one_or_none()
    if run is not None:
        await session.commit()
        return run

    current = await get_run(session, profile_id, run_id)
    if DiscoveryRunStatus(current.status) is DiscoveryRunStatus.CANCEL_REQUESTED:
        return current
    raise _safe_error(
        "question_discovery_not_cancellable",
        "该题目发现任务已结束，无法取消。",
    )


async def delete_run(
    session: AsyncSession,
    profile_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    run = await get_run(session, profile_id, run_id)
    if not is_terminal(run.status):
        raise _safe_error(
            "question_discovery_not_deletable",
            "仅已结束或已取消的题目发现任务可以删除。",
        )

    jobs = await session.scalars(
        select(BackgroundJob).where(
            BackgroundJob.profile_id == profile_id,
            BackgroundJob.job_type == JobType.QUESTION_DISCOVERY,
        )
    )
    for job in jobs.all():
        if str(job.payload.get("run_id", "")) == str(run.id):
            await session.delete(job)
    await session.delete(run)
    await session.commit()


async def get_connector_for_run(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
) -> DiscoveryConnector:
    """Revalidate ownership, deletion and config drift immediately before use."""

    connector = await discovery_connectors.get_connector(session, run.profile_id, run.connector_id)
    if not connector.enabled or connector.deleted_at is not None or not connector.encrypted_api_key:
        raise _safe_error(
            "discovery_connector_unavailable",
            "题目发现连接器当前不可用，请重新配置后发起新的发现任务。",
        )
    if connector.configuration_version != run.connector_configuration_version:
        raise _safe_error(
            "discovery_connector_configuration_changed",
            "题目发现连接器已更新，请重新发起发现任务。",
        )
    return connector


def source_category_for_domain(domain: str) -> str:
    if domain in CN_INTERVIEW_TECH_PRESET:
        return "tech_community"
    return "unknown"


def source_failure_summary(code: str) -> str:
    if code == "url_blocked":
        return "该链接不符合当前来源安全策略。"
    if code == "unreadable":
        return "该来源暂时无法读取。"
    return "该来源处理失败，可修改条件或稍后重试。"


def source_excerpt(content: str) -> str:
    """Persist only a bounded readable excerpt, never a provider's full response."""

    compact = " ".join(content.split())
    return compact[: settings.discovery_max_excerpt_characters]


def source_content_hash(content: str) -> str:
    import hashlib

    return hashlib.sha256(" ".join(content.split()).encode("utf-8")).hexdigest()


def source_policy_metadata(*, matched_allow_domain: str | None, scheme: str, port: int) -> dict:
    return {
        "matched_allow_domain": matched_allow_domain,
        "scheme": scheme,
        "port": port,
    }


def bounded_sources[T](sources: Sequence[T]) -> tuple[T, ...]:
    """One reusable hard cap for both search and pasted URL modes."""

    return tuple(sources[: max_sources()])
