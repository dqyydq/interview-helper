"""Background retrieval for question-discovery source cards.

The worker calls only configured providers. It never fetches a pasted URL itself,
persists bounded excerpts instead of full web-page bodies, and asks the strictly bound
Researcher only after source cards are ready. Import remains a separate user action.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

import anyio
import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.db.models.common import (
    DiscoveryRunStatus,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    JobStatus,
    JobType,
    utc_now,
)
from app.db.models.discovery import (
    QuestionDiscoveryCandidate,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
)
from app.db.models.job import BackgroundJob
from app.db.session import async_session_factory
from app.discovery.providers.base import (
    DiscoveryProviderError,
    ExtractRequest,
    ExtractResponse,
    SearchProvider,
    SearchQuery,
)
from app.discovery.url_policy import DomainPolicy, URLPolicy, URLPolicyError, ValidatedURL
from app.services import discovery_connectors, question_curation
from app.services import question_discovery as discovery_service

logger = structlog.get_logger(__name__)


class DiscoveryCancelled(Exception):
    """Internal control flow: cancellation is a terminal state, not an error."""


@dataclass(slots=True)
class SourceInput:
    raw_url: str
    validated_url: ValidatedURL
    title_hint: str | None = None
    snippet: str | None = None


def _provider_name(run: QuestionDiscoveryRun) -> str:
    return str(run.query_snapshot.get("provider", "configured"))


def _safe_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _blocked_source_key(raw_url: str) -> str:
    digest = hashlib.sha256(raw_url.encode("utf-8", errors="replace")).hexdigest()
    return f"blocked:{digest}"


def _source_attribution(run: QuestionDiscoveryRun) -> dict:
    return {
        "provider": _provider_name(run),
        "source_mode": DiscoverySourceMode(run.source_mode).value,
    }


def _url_policy_for_run(run: QuestionDiscoveryRun) -> URLPolicy:
    raw_policy = run.query_snapshot.get("domain_policy")
    if not isinstance(raw_policy, dict):
        raise AppError(
            code="discovery_run_policy_invalid",
            message="题目发现任务的来源策略无效。",
        )
    try:
        domain_policy = DomainPolicy(
            allow_domains=tuple(raw_policy.get("allow_domains") or ()),
            deny_domains=tuple(raw_policy.get("deny_domains") or ()),
        )
    except URLPolicyError as exc:
        raise AppError(
            code="discovery_run_policy_invalid",
            message="题目发现任务的来源策略无效。",
        ) from exc
    return URLPolicy(
        domain_policy=domain_policy,
        allow_http_local=settings.environment == "local" and settings.discovery_allow_http_local,
    )


async def _cancel_requested(session: AsyncSession, run: QuestionDiscoveryRun) -> bool:
    status = await session.scalar(
        select(QuestionDiscoveryRun.status)
        .where(
            QuestionDiscoveryRun.id == run.id,
            QuestionDiscoveryRun.profile_id == run.profile_id,
            QuestionDiscoveryRun.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    return status == DiscoveryRunStatus.CANCEL_REQUESTED


async def _raise_if_cancelled(session: AsyncSession, run: QuestionDiscoveryRun) -> None:
    if await _cancel_requested(session, run):
        raise DiscoveryCancelled


def _discovery_job_lease_seconds() -> float:
    """Return a conservative lease for a bounded retrieval and curation run."""

    return max(
        # Curation allows one structured-output repair after the bounded retrieval,
        # so preserve a full extra discovery window as a scheduling/commit margin.
        settings.discovery_run_timeout_seconds * 4,
        settings.worker_heartbeat_stale_after_seconds * 2,
        240.0,
    )


async def _claim_next_discovery_job(session: AsyncSession, worker_id: str) -> uuid.UUID | None:
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.QUESTION_DISCOVERY,
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
    job.progress = 0.02
    job.attempts += 1
    job.locked_at = utc_now()
    job.locked_by = worker_id
    job.error_code = None
    job.error_message = None
    job.touch()
    await session.commit()
    return job.id


async def claim_next_discovery_job(session: AsyncSession, worker_id: str) -> uuid.UUID | None:
    """Public test seam matching the other worker job modules."""

    return await _claim_next_discovery_job(session, worker_id)


async def _load_job_context(
    session: AsyncSession,
    job_id: uuid.UUID,
) -> tuple[BackgroundJob, QuestionDiscoveryRun]:
    job = await session.get(BackgroundJob, job_id)
    if job is None or job.status != JobStatus.RUNNING:
        raise AppError(code="question_discovery_job_not_running", message="题目发现任务不可执行。")
    if set(job.payload) != {"run_id"}:
        raise AppError(
            code="question_discovery_job_payload_invalid",
            message="题目发现任务参数无效。",
        )
    try:
        run_id = uuid.UUID(str(job.payload["run_id"]))
    except (TypeError, ValueError) as exc:
        raise AppError(
            code="question_discovery_job_payload_invalid",
            message="题目发现任务参数无效。",
        ) from exc
    if job.profile_id is None:
        raise AppError(
            code="question_discovery_job_profile_invalid",
            message="题目发现任务归属无效。",
        )
    run = await session.scalar(
        select(QuestionDiscoveryRun).where(
            QuestionDiscoveryRun.id == run_id,
            QuestionDiscoveryRun.profile_id == job.profile_id,
            QuestionDiscoveryRun.deleted_at.is_(None),
        )
    )
    if run is None:
        raise AppError(code="question_discovery_not_found", message="题目发现记录不存在。")
    return job, run


async def _set_running(
    session: AsyncSession,
    job: BackgroundJob,
    run: QuestionDiscoveryRun,
) -> None:
    await _raise_if_cancelled(session, run)
    status = DiscoveryRunStatus(run.status)
    if discovery_service.is_terminal(status):
        return
    if status not in {DiscoveryRunStatus.QUEUED, DiscoveryRunStatus.RUNNING}:
        raise AppError(
            code="question_discovery_state_invalid",
            message="题目发现任务状态无效。",
        )
    run.status = DiscoveryRunStatus.RUNNING
    run.stage = "starting"
    run.progress = max(run.progress, 0.05)
    run.error_code = None
    run.error_summary = None
    run.touch()
    job.progress = max(job.progress, 0.05)
    job.touch()
    await session.commit()


async def _set_stage(
    session: AsyncSession,
    job: BackgroundJob,
    run: QuestionDiscoveryRun,
    *,
    stage: str,
    progress: float,
) -> None:
    await _raise_if_cancelled(session, run)
    run.stage = stage
    run.progress = max(run.progress, progress)
    run.touch()
    job.progress = max(job.progress, progress)
    job.touch()
    await session.commit()


async def _validate_urls(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    url_policy: URLPolicy,
    raw_items: Sequence[tuple[str, str | None, str | None]],
) -> tuple[list[SourceInput], list[str]]:
    """Validate inputs while reserving one shared source-card budget per item."""

    valid: list[SourceInput] = []
    blocked: list[str] = []
    seen_valid: set[str] = set()
    seen_blocked: set[str] = set()
    source_limit = discovery_service.max_sources()
    for raw_url, title_hint, snippet in raw_items:
        if len(valid) + len(blocked) >= source_limit:
            break
        await _raise_if_cancelled(session, run)
        try:
            validated = await anyio.to_thread.run_sync(url_policy.validate, raw_url)
        except URLPolicyError:
            blocked_key = _blocked_source_key(raw_url)
            if blocked_key in seen_blocked:
                continue
            seen_blocked.add(blocked_key)
            blocked.append(raw_url)
            continue
        if validated.normalized_url in seen_valid:
            continue
        seen_valid.add(validated.normalized_url)
        valid.append(
            SourceInput(
                raw_url=raw_url,
                validated_url=validated,
                title_hint=_safe_text(title_hint, 500) or None,
                snippet=_safe_text(snippet, settings.discovery_max_source_characters) or None,
            )
        )
    return valid, blocked


async def _write_blocked_sources(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    raw_urls: Sequence[str],
    *,
    max_cards: int,
) -> None:
    if not raw_urls or max_cards <= 0:
        return
    await _raise_if_cancelled(session, run)
    seen: set[str] = set()
    written = 0
    for raw_url in raw_urls:
        if written >= max_cards:
            break
        normalized_url = _blocked_source_key(raw_url)
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        session.add(
            QuestionDiscoverySource(
                profile_id=run.profile_id,
                run_id=run.id,
                raw_url=_safe_text(raw_url, 2_048) or None,
                normalized_url=normalized_url,
                domain="blocked",
                source_category="unknown",
                status=DiscoverySourceStatus.BLOCKED,
                attribution=_source_attribution(run),
                policy_metadata={},
                failure_code="url_blocked",
                failure_summary=discovery_service.source_failure_summary("url_blocked"),
                expires_at=run.expires_at,
            )
        )
        written += 1
    await _raise_if_cancelled(session, run)
    await session.commit()


def _new_pending_source(run: QuestionDiscoveryRun, source: SourceInput) -> QuestionDiscoverySource:
    validated = source.validated_url
    return QuestionDiscoverySource(
        profile_id=run.profile_id,
        run_id=run.id,
        raw_url=_safe_text(source.raw_url, 2_048) or None,
        normalized_url=validated.normalized_url,
        title=source.title_hint,
        domain=validated.hostname,
        source_category=discovery_service.source_category_for_domain(validated.hostname),
        status=DiscoverySourceStatus.PENDING,
        attribution=_source_attribution(run),
        policy_metadata=discovery_service.source_policy_metadata(
            matched_allow_domain=validated.matched_allow_domain,
            scheme=validated.scheme,
            port=validated.port,
        ),
        expires_at=run.expires_at,
    )


async def _create_pending_sources(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    inputs: Sequence[SourceInput],
) -> list[QuestionDiscoverySource]:
    await _raise_if_cancelled(session, run)
    rows = [_new_pending_source(run, source) for source in inputs]
    session.add_all(rows)
    await _raise_if_cancelled(session, run)
    await session.commit()
    return rows


def _mark_source_failed(source: QuestionDiscoverySource, code: str) -> None:
    source.status = DiscoverySourceStatus.FAILED
    source.failure_code = code
    source.failure_summary = discovery_service.source_failure_summary(code)
    source.touch()


def _mark_source_blocked(source: QuestionDiscoverySource) -> None:
    source.status = DiscoverySourceStatus.BLOCKED
    source.failure_code = "url_blocked"
    source.failure_summary = discovery_service.source_failure_summary("url_blocked")
    source.touch()


async def _source_for_provider_url(
    url_policy: URLPolicy,
    pending_by_normalized: dict[str, QuestionDiscoverySource],
    pending_by_raw: dict[str, QuestionDiscoverySource],
    value: str,
) -> tuple[QuestionDiscoverySource | None, ValidatedURL | None]:
    if value in pending_by_raw:
        source = pending_by_raw[value]
        return source, None
    try:
        validated = await anyio.to_thread.run_sync(url_policy.validate, value)
    except URLPolicyError:
        return None, None
    return pending_by_normalized.get(validated.normalized_url), validated


async def _apply_extract_response(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    url_policy: URLPolicy,
    pending_sources: Sequence[QuestionDiscoverySource],
    response: ExtractResponse,
) -> None:
    """Apply only requested, policy-valid connector output to pre-created cards."""

    await _raise_if_cancelled(session, run)
    by_normalized = {source.normalized_url: source for source in pending_sources}
    by_raw = {source.raw_url: source for source in pending_sources if source.raw_url}
    resolved: set[uuid.UUID] = set()

    for extracted in response.sources:
        source, returned_url = await _source_for_provider_url(
            url_policy,
            by_normalized,
            by_raw,
            extracted.url,
        )
        if source is None:
            continue
        await _raise_if_cancelled(session, run)
        try:
            canonical = await anyio.to_thread.run_sync(
                url_policy.validate,
                extracted.canonical_url,
            )
        except URLPolicyError:
            _mark_source_blocked(source)
            resolved.add(source.id)
            continue
        content = _safe_text(extracted.content, settings.discovery_max_source_characters)
        if not content:
            _mark_source_failed(source, "unreadable")
            resolved.add(source.id)
            continue
        source.final_url = canonical.normalized_url
        source.title = _safe_text(extracted.title, 500) or source.title or canonical.normalized_url
        source.domain = canonical.hostname
        source.source_category = discovery_service.source_category_for_domain(canonical.hostname)
        source.status = DiscoverySourceStatus.FETCHED
        source.fetched_at = utc_now()
        source.content_hash = discovery_service.source_content_hash(content)
        source.excerpt = discovery_service.source_excerpt(content)
        source.policy_metadata = discovery_service.source_policy_metadata(
            matched_allow_domain=canonical.matched_allow_domain,
            scheme=canonical.scheme,
            port=canonical.port,
        )
        source.failure_code = None
        source.failure_summary = None
        source.touch()
        resolved.add(source.id)
        # ``returned_url`` is deliberately only a validation aid.  We do not preserve
        # it separately or trust it more than the connector's canonical URL.
        _ = returned_url

    for failure in response.failures:
        source, _ = await _source_for_provider_url(
            url_policy,
            by_normalized,
            by_raw,
            failure.url,
        )
        if source is not None and source.id not in resolved:
            _mark_source_failed(source, failure.code)
            resolved.add(source.id)

    for source in pending_sources:
        if source.id not in resolved:
            _mark_source_failed(source, "unreadable")
    await _raise_if_cancelled(session, run)
    await session.commit()


async def _write_search_snippets(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    inputs: Sequence[SourceInput],
) -> None:
    """Fallback for a search-only provider without safe Extract capability."""

    await _raise_if_cancelled(session, run)
    rows: list[QuestionDiscoverySource] = []
    for item in inputs:
        source = _new_pending_source(run, item)
        content = item.snippet or ""
        if content:
            source.final_url = item.validated_url.normalized_url
            source.title = item.title_hint or item.validated_url.normalized_url
            source.status = DiscoverySourceStatus.FETCHED
            source.fetched_at = utc_now()
            source.content_hash = discovery_service.source_content_hash(content)
            source.excerpt = discovery_service.source_excerpt(content)
        else:
            _mark_source_failed(source, "unreadable")
        rows.append(source)
    session.add_all(rows)
    await _raise_if_cancelled(session, run)
    await session.commit()


async def _retrieve_urls(
    session: AsyncSession,
    job: BackgroundJob,
    run: QuestionDiscoveryRun,
    provider: SearchProvider,
    url_policy: URLPolicy,
) -> None:
    raw_urls = run.query_snapshot.get("urls")
    if not isinstance(raw_urls, list):
        raise AppError(code="discovery_run_urls_invalid", message="题目发现链接参数无效。")
    raw_items = [(str(url), None, None) for url in raw_urls[: discovery_service.max_urls()]]
    inputs, blocked = await _validate_urls(session, run, url_policy, raw_items)
    await _write_blocked_sources(
        session,
        run,
        blocked,
        max_cards=max(0, discovery_service.max_sources() - len(inputs)),
    )
    if not inputs:
        return
    await _set_stage(session, job, run, stage="extracting", progress=0.35)
    pending = await _create_pending_sources(session, run, inputs)
    await _raise_if_cancelled(session, run)
    response = await provider.extract(
        ExtractRequest(urls=tuple(item.validated_url.url for item in inputs))
    )
    await _apply_extract_response(session, run, url_policy, pending, response)


async def _retrieve_search(
    session: AsyncSession,
    job: BackgroundJob,
    run: QuestionDiscoveryRun,
    provider: SearchProvider,
    url_policy: URLPolicy,
) -> None:
    query = _safe_text(run.query_snapshot.get("search_query"), 500)
    if not query:
        raise AppError(code="discovery_run_query_invalid", message="题目发现检索条件无效。")
    raw_policy = cast(dict, run.query_snapshot["domain_policy"])
    await _set_stage(session, job, run, stage="searching", progress=0.2)
    await _raise_if_cancelled(session, run)
    results = await provider.search(
        SearchQuery(
            query=query,
            max_results=discovery_service.max_search_results(),
            include_domains=tuple(raw_policy.get("allow_domains") or ()),
            exclude_domains=tuple(raw_policy.get("deny_domains") or ()),
            country=_safe_text(run.query_snapshot.get("country"), 64) or None,
        )
    )
    await _raise_if_cancelled(session, run)
    limited_results = results[: discovery_service.max_search_results()]
    raw_items = [(result.url, result.title, result.content) for result in limited_results]
    inputs, blocked = await _validate_urls(session, run, url_policy, raw_items)
    await _write_blocked_sources(
        session,
        run,
        blocked,
        max_cards=max(0, discovery_service.max_sources() - len(inputs)),
    )
    if not inputs:
        return
    if provider.capabilities.supports_extract and provider.capabilities.safe_extract:
        await _set_stage(session, job, run, stage="extracting", progress=0.5)
        pending = await _create_pending_sources(session, run, inputs)
        await _raise_if_cancelled(session, run)
        response = await provider.extract(
            ExtractRequest(urls=tuple(item.validated_url.url for item in inputs))
        )
        await _apply_extract_response(session, run, url_policy, pending, response)
        return
    await _write_search_snippets(session, run, inputs)


async def _source_counts(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
) -> tuple[int, int, int]:
    total = await session.scalar(
        select(func.count())
        .select_from(QuestionDiscoverySource)
        .where(
            QuestionDiscoverySource.profile_id == run.profile_id,
            QuestionDiscoverySource.run_id == run.id,
            QuestionDiscoverySource.deleted_at.is_(None),
        )
    )
    fetched = await session.scalar(
        select(func.count())
        .select_from(QuestionDiscoverySource)
        .where(
            QuestionDiscoverySource.profile_id == run.profile_id,
            QuestionDiscoverySource.run_id == run.id,
            QuestionDiscoverySource.deleted_at.is_(None),
            QuestionDiscoverySource.status == DiscoverySourceStatus.FETCHED,
        )
    )
    failed = await session.scalar(
        select(func.count())
        .select_from(QuestionDiscoverySource)
        .where(
            QuestionDiscoverySource.profile_id == run.profile_id,
            QuestionDiscoverySource.run_id == run.id,
            QuestionDiscoverySource.deleted_at.is_(None),
            QuestionDiscoverySource.status.in_(
                (DiscoverySourceStatus.BLOCKED, DiscoverySourceStatus.FAILED)
            ),
        )
    )
    return int(total or 0), int(fetched or 0), int(failed or 0)


async def _complete_run(
    session: AsyncSession,
    job: BackgroundJob,
    run: QuestionDiscoveryRun,
    *,
    curation: question_curation.CurationOutcome | None = None,
) -> None:
    await _raise_if_cancelled(session, run)
    total, fetched, failed = await _source_counts(session, run)
    candidate_count = await session.scalar(
        select(func.count())
        .select_from(QuestionDiscoveryCandidate)
        .where(
            QuestionDiscoveryCandidate.profile_id == run.profile_id,
            QuestionDiscoveryCandidate.run_id == run.id,
            QuestionDiscoveryCandidate.deleted_at.is_(None),
        )
    )
    if fetched:
        status = DiscoveryRunStatus.PARTIAL if failed else DiscoveryRunStatus.SUCCEEDED
        error_code = curation.error_code if curation else None
        error_summary = curation.error_summary if curation else None
        if curation and curation.has_recoverable_failure:
            status = DiscoveryRunStatus.PARTIAL
    elif failed:
        status = DiscoveryRunStatus.FAILED
        error_code = "discovery_sources_unavailable"
        error_summary = "没有可用的来源卡片，请修改来源或稍后重试。"
    else:
        status = DiscoveryRunStatus.NO_RESULTS
        error_code = None
        error_summary = None

    # Re-read cancellation immediately before the terminal write.  A user request
    # that raced with the last provider response always wins over a success state.
    await _raise_if_cancelled(session, run)
    run.status = status
    run.stage = "complete"
    run.progress = 1.0
    run.source_count = total
    run.failed_source_count = failed
    run.candidate_count = int(candidate_count or 0)
    run.error_code = error_code
    run.error_summary = error_summary
    run.completed_at = utc_now()
    run.touch()
    job.status = JobStatus.COMPLETED
    job.progress = 1.0
    job.result = {
        "run_id": str(run.id),
        "status": status.value,
        "source_count": total,
        "candidate_count": run.candidate_count,
    }
    job.locked_at = None
    job.locked_by = None
    job.touch()
    await session.commit()


def _failure_summary(code: str) -> str:
    if code == "discovery_run_timeout":
        return "题目发现任务超时，请缩小检索范围后重试。"
    if code.startswith("discovery_connector"):
        return "题目发现连接器暂时不可用，请检查连接器设置后重试。"
    return "题目发现任务暂时失败，请稍后重试。"


async def _mark_pending_sources_failed(
    session: AsyncSession,
    run: QuestionDiscoveryRun,
    *,
    code: str,
) -> None:
    now = utc_now()
    await session.execute(
        update(QuestionDiscoverySource)
        .where(
            QuestionDiscoverySource.profile_id == run.profile_id,
            QuestionDiscoverySource.run_id == run.id,
            QuestionDiscoverySource.status == DiscoverySourceStatus.PENDING,
            QuestionDiscoverySource.deleted_at.is_(None),
        )
        .values(
            status=DiscoverySourceStatus.FAILED,
            failure_code=code,
            failure_summary=discovery_service.source_failure_summary("provider_error"),
            updated_at=now,
            version=QuestionDiscoverySource.version + 1,
        )
    )


async def _matching_run_for_job(
    session: AsyncSession,
    job: BackgroundJob,
) -> QuestionDiscoveryRun | None:
    if job.profile_id is None or set(job.payload) != {"run_id"}:
        return None
    try:
        run_id = uuid.UUID(str(job.payload["run_id"]))
    except (TypeError, ValueError):
        return None
    return await session.scalar(
        select(QuestionDiscoveryRun)
        .where(
            QuestionDiscoveryRun.id == run_id,
            QuestionDiscoveryRun.profile_id == job.profile_id,
            QuestionDiscoveryRun.deleted_at.is_(None),
        )
        .with_for_update()
    )


async def _recover_stale_discovery_jobs(session: AsyncSession) -> int:
    """Fail lost discovery jobs before a worker claims another queued job."""

    now = utc_now()
    stale_before = now - timedelta(seconds=_discovery_job_lease_seconds())
    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob)
                .where(
                    BackgroundJob.job_type == JobType.QUESTION_DISCOVERY,
                    BackgroundJob.status == JobStatus.RUNNING,
                    BackgroundJob.locked_at.is_not(None),
                    BackgroundJob.locked_at < stale_before,
                    BackgroundJob.deleted_at.is_(None),
                )
                .order_by(BackgroundJob.locked_at, BackgroundJob.created_at)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    if not jobs:
        return 0

    code = "discovery_worker_lost"
    summary = _failure_summary(code)
    for job in jobs:
        run = await _matching_run_for_job(session, job)
        if run is not None and not discovery_service.is_terminal(run.status):
            await _mark_pending_sources_failed(session, run, code=code)
            source_count, _, failed_source_count = await _source_counts(session, run)
            candidate_count = await session.scalar(
                select(func.count())
                .select_from(QuestionDiscoveryCandidate)
                .where(
                    QuestionDiscoveryCandidate.profile_id == run.profile_id,
                    QuestionDiscoveryCandidate.run_id == run.id,
                    QuestionDiscoveryCandidate.deleted_at.is_(None),
                )
            )
            run.status = DiscoveryRunStatus.FAILED
            run.stage = "failed"
            run.progress = 1.0
            run.source_count = source_count
            run.failed_source_count = failed_source_count
            run.candidate_count = int(candidate_count or 0)
            run.error_code = code
            run.error_summary = summary
            run.completed_at = now
            run.touch(at=now)

        job.status = JobStatus.FAILED
        job.progress = 1.0
        job.error_code = code
        job.error_message = summary
        job.locked_at = None
        job.locked_by = None
        job.touch(at=now)

    await session.commit()
    return len(jobs)


async def _fail_job(job_id: uuid.UUID, run_id: uuid.UUID | None, code: str) -> None:
    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        run = await session.get(QuestionDiscoveryRun, run_id) if run_id is not None else None
        if job is None:
            return
        if run is not None and await _cancel_requested(session, run):
            await _cancel_job(session, job, run)
            return
        if run is not None and not discovery_service.is_terminal(run.status):
            await _mark_pending_sources_failed(session, run, code=code)
            total, _, failed = await _source_counts(session, run)
            run.status = DiscoveryRunStatus.FAILED
            run.stage = "failed"
            run.progress = 1.0
            run.source_count = total
            run.failed_source_count = failed
            run.candidate_count = 0
            run.error_code = code
            run.error_summary = _failure_summary(code)
            run.completed_at = utc_now()
            run.touch()
        job.status = JobStatus.FAILED
        job.progress = 1.0
        job.error_code = code
        job.error_message = _failure_summary(code)
        job.locked_at = None
        job.locked_by = None
        job.touch()
        await session.commit()


async def _cancel_job(
    session: AsyncSession,
    job: BackgroundJob,
    run: QuestionDiscoveryRun,
) -> None:
    total, _, failed = await _source_counts(session, run)
    if not discovery_service.is_terminal(run.status):
        run.status = DiscoveryRunStatus.CANCELLED
        run.stage = "cancelled"
        run.source_count = total
        run.failed_source_count = failed
        run.candidate_count = 0
        run.completed_at = utc_now()
        run.touch()
    job.status = JobStatus.CANCELLED
    job.progress = 1.0
    job.result = {"run_id": str(run.id), "status": DiscoveryRunStatus.CANCELLED.value}
    job.error_code = None
    job.error_message = None
    job.locked_at = None
    job.locked_by = None
    job.touch()
    await session.commit()


async def process_discovery_job(job_id: uuid.UUID) -> None:
    """Process one claimed run with cancellation checkpoints around every side effect."""

    run_id: uuid.UUID | None = None
    provider: SearchProvider | None = None
    try:
        async with async_session_factory() as session:
            job, run = await _load_job_context(session, job_id)
            run_id = run.id
            if await _cancel_requested(session, run):
                await _cancel_job(session, job, run)
                return
            if discovery_service.is_terminal(run.status):
                job.status = (
                    JobStatus.CANCELLED
                    if run.status == DiscoveryRunStatus.CANCELLED
                    else JobStatus.COMPLETED
                )
                job.progress = 1.0
                job.locked_at = None
                job.locked_by = None
                job.touch()
                await session.commit()
                return

            connector = await discovery_service.get_connector_for_run(session, run)
            await _set_running(session, job, run)
            provider = discovery_connectors.build_search_provider(connector)
            url_policy = _url_policy_for_run(run)
            mode = DiscoverySourceMode(run.source_mode)
            with anyio.fail_after(settings.discovery_run_timeout_seconds):
                if mode is DiscoverySourceMode.SEARCH:
                    await _retrieve_search(session, job, run, provider, url_policy)
                else:
                    await _retrieve_urls(session, job, run, provider, url_policy)
            await _set_stage(session, job, run, stage="curating", progress=0.8)
            curation = await question_curation.curate_discovery_run(
                session,
                run,
                cancellation_check=lambda: _raise_if_cancelled(session, run),
            )
            await _raise_if_cancelled(session, run)
            await _complete_run(session, job, run, curation=curation)
    except DiscoveryCancelled:
        if run_id is not None:
            async with async_session_factory() as session:
                job = await session.get(BackgroundJob, job_id)
                run = await session.get(QuestionDiscoveryRun, run_id)
                if job is not None and run is not None:
                    await _cancel_job(session, job, run)
    except DiscoveryProviderError as exc:
        await _fail_job(job_id, run_id, exc.code)
    except TimeoutError:
        await _fail_job(job_id, run_id, "discovery_run_timeout")
    except AppError as exc:
        await _fail_job(job_id, run_id, exc.code)
    except Exception as exc:  # Never persist or expose an untrusted provider response/error body.
        await logger.aexception(
            "question_discovery_job_failed",
            job_id=str(job_id),
            run_id=str(run_id) if run_id else None,
            error_type=type(exc).__name__,
        )
        await _fail_job(job_id, run_id, "question_discovery_internal_error")
    finally:
        if provider is not None:
            try:
                await provider.aclose()
            except Exception as exc:
                await logger.awarning(
                    "question_discovery_provider_close_failed",
                    job_id=str(job_id),
                    error_type=type(exc).__name__,
                )


async def run_once(worker_id: str = "local-worker") -> bool:
    async with async_session_factory() as session:
        recovered = await _recover_stale_discovery_jobs(session)
        job_id = await _claim_next_discovery_job(session, worker_id)
    if job_id is None:
        return recovered > 0
    await process_discovery_job(job_id)
    return True
