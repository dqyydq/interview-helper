"""Atomic import of reviewed discovery candidates into local question banks.

The discovery flow deliberately keeps candidates outside a question bank until a
user selects them.  This module is the narrow boundary that turns those reviewed
candidates into draft questions.  It owns a single transaction: either every
requested candidate, its import audit row, and its immutable source snapshots are
persisted, or none of them are.

The module intentionally exposes dataclasses rather than an HTTP schema.  The API
layer can evolve its request/response shape without weakening the profile, revision,
idempotency, and provenance guarantees implemented here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import (
    Difficulty,
    DiscoveryCandidateStatus,
    DiscoveryImportStatus,
    DiscoverySourceStatus,
    QuestionStatus,
    QuestionType,
    SourceType,
    utc_now,
)
from app.db.models.discovery import (
    QuestionDiscoveryCandidate,
    QuestionDiscoveryCandidateEvidence,
    QuestionDiscoveryImport,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
    QuestionSourceProvenance,
)
from app.db.models.question import Question, QuestionBank, QuestionTag, QuestionTagLink
from app.discovery.source_urls import verified_source_url
from app.services.questions import prompt_hash, tag_slug

MAX_IMPORT_ITEMS = 20
MAX_TAGS = 30
MAX_LIST_ITEMS = 50

_ALLOWED_CANDIDATE_STATUSES = frozenset(
    {
        DiscoveryCandidateStatus.PROPOSED,
        DiscoveryCandidateStatus.SELECTED,
        DiscoveryCandidateStatus.IMPORTED,
    }
)


@dataclass(frozen=True, slots=True)
class DiscoveryImportItem:
    """One candidate selected for import, with optional user edits.

    ``None`` means "keep the candidate suggestion".  An explicit empty sequence
    means "clear that optional list", which is useful for the import review form.
    """

    candidate_id: uuid.UUID
    candidate_revision: int
    prompt: str | None = None
    question_type: QuestionType | str | None = None
    difficulty: Difficulty | str | None = None
    tag_names: Sequence[str] | None = None
    reference_points: Sequence[str] | None = None
    follow_up_suggestions: Sequence[str] | None = None
    applicable_companies: Sequence[str] | None = None
    applicable_rounds: Sequence[str] | None = None
    source_note: str | None = None
    user_note: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryImportRequest:
    """The service-level request used by the later discovery import route."""

    run_id: uuid.UUID
    bank_id: uuid.UUID
    idempotency_key: str
    items: Sequence[DiscoveryImportItem]


@dataclass(frozen=True, slots=True)
class ImportedDiscoveryQuestion:
    """A content-free item result suitable for a public API response."""

    candidate_id: uuid.UUID
    candidate_revision: int
    question_id: uuid.UUID
    import_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class DiscoveryImportResult:
    """Result of a successful atomic import or a same-request idempotent replay."""

    run_id: uuid.UUID
    bank_id: uuid.UUID
    batch_id: uuid.UUID
    request_hash: str
    items: tuple[ImportedDiscoveryQuestion, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class _PreparedItem:
    candidate_id: uuid.UUID
    candidate_revision: int
    prompt: str | None
    question_type: QuestionType | None
    difficulty: Difficulty | None
    tag_names: tuple[str, ...] | None
    reference_points: tuple[str, ...] | None
    follow_up_suggestions: tuple[str, ...] | None
    applicable_companies: tuple[str, ...] | None
    applicable_rounds: tuple[str, ...] | None
    source_note: str | None
    user_note: str | None


@dataclass(frozen=True, slots=True)
class _EvidenceSnapshot:
    source_title: str
    normalized_url: str
    source_domain: str
    source_category: str
    fetched_at: datetime
    excerpt: str
    evidence_hash: str
    attribution: dict


@dataclass(frozen=True, slots=True)
class _ValidatedItem:
    prepared: _PreparedItem
    candidate: QuestionDiscoveryCandidate
    prompt: str
    normalized_hash: str
    question_type: QuestionType
    difficulty: Difficulty
    tag_names: tuple[str, ...]
    reference_points: tuple[str, ...]
    follow_up_suggestions: tuple[str, ...]
    applicable_companies: tuple[str, ...]
    applicable_rounds: tuple[str, ...]
    source_note: str | None
    user_note: str | None
    evidence: tuple[_EvidenceSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _ValidatedRequest:
    run: QuestionDiscoveryRun
    bank: QuestionBank
    items: tuple[_ValidatedItem, ...]


def _error(code: str, message: str, *, status_code: int = 409) -> AppError:
    return AppError(code=code, message=message, status_code=status_code)


def _optional_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
    allow_blank: bool = True,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _error(
            "discovery_import_invalid",
            f"{field_name} must be text.",
            status_code=422,
        )
    normalized = value.strip()
    if not normalized and allow_blank:
        return None
    if not normalized:
        raise _error(
            "discovery_import_invalid",
            f"{field_name} cannot be blank.",
            status_code=422,
        )
    if len(normalized) > max_length:
        raise _error(
            "discovery_import_invalid",
            f"{field_name} is too long.",
            status_code=422,
        )
    return normalized


def _text_list(
    value: object,
    *,
    field_name: str,
    max_items: int = MAX_LIST_ITEMS,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            "discovery_import_invalid",
            f"{field_name} must be a list of text values.",
            status_code=422,
        )
    if len(value) > max_items:
        raise _error(
            "discovery_import_invalid",
            f"{field_name} contains too many values.",
            status_code=422,
        )
    normalized: list[str] = []
    for entry in value:
        item = _optional_text(
            entry,
            field_name=field_name,
            max_length=2_000,
            allow_blank=False,
        )
        assert item is not None
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def _tag_list(value: object) -> tuple[str, ...] | None:
    tags = _text_list(value, field_name="tag_names", max_items=MAX_TAGS)
    if tags is None:
        return None
    normalized: list[str] = []
    seen_slugs: set[str] = set()
    for tag in tags:
        slug = tag_slug(tag)
        if slug not in seen_slugs:
            normalized.append(tag)
            seen_slugs.add(slug)
    return tuple(normalized)


def _enum_value[EnumT: Enum](
    value: object,
    enum_type: type[EnumT],
    *,
    field_name: str,
) -> EnumT | None:
    if value is None:
        return None
    try:
        return enum_type(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _error(
            "discovery_import_invalid",
            f"{field_name} is not supported.",
            status_code=422,
        ) from exc


def _prepare_request(request: DiscoveryImportRequest) -> tuple[_PreparedItem, ...]:
    if not isinstance(request.run_id, uuid.UUID) or not isinstance(request.bank_id, uuid.UUID):
        raise _error(
            "discovery_import_invalid",
            "run_id and bank_id must be UUIDs.",
            status_code=422,
        )
    idempotency_key = _optional_text(
        request.idempotency_key,
        field_name="idempotency_key",
        max_length=255,
        allow_blank=False,
    )
    assert idempotency_key is not None
    if isinstance(request.items, (str, bytes)) or not isinstance(request.items, Sequence):
        raise _error(
            "discovery_import_invalid",
            "items must be a list.",
            status_code=422,
        )
    if not request.items or len(request.items) > MAX_IMPORT_ITEMS:
        raise _error(
            "discovery_import_invalid",
            f"items must contain between 1 and {MAX_IMPORT_ITEMS} entries.",
            status_code=422,
        )

    prepared: list[_PreparedItem] = []
    seen_candidate_ids: set[uuid.UUID] = set()
    for item in request.items:
        if not isinstance(item, DiscoveryImportItem):
            raise _error(
                "discovery_import_invalid",
                "items must use DiscoveryImportItem.",
                status_code=422,
            )
        if not isinstance(item.candidate_id, uuid.UUID):
            raise _error(
                "discovery_import_invalid",
                "candidate_id must be a UUID.",
                status_code=422,
            )
        if isinstance(item.candidate_revision, bool) or not isinstance(
            item.candidate_revision,
            int,
        ):
            raise _error(
                "discovery_import_invalid",
                "candidate_revision must be a positive integer.",
                status_code=422,
            )
        if item.candidate_revision < 1:
            raise _error(
                "discovery_import_invalid",
                "candidate_revision must be a positive integer.",
                status_code=422,
            )
        if item.candidate_id in seen_candidate_ids:
            raise _error(
                "discovery_import_invalid",
                "a candidate can appear only once in an import request.",
                status_code=422,
            )
        seen_candidate_ids.add(item.candidate_id)
        prepared.append(
            _PreparedItem(
                candidate_id=item.candidate_id,
                candidate_revision=item.candidate_revision,
                prompt=_optional_text(
                    item.prompt,
                    field_name="prompt",
                    max_length=50_000,
                    allow_blank=False,
                ),
                question_type=_enum_value(
                    item.question_type,
                    QuestionType,
                    field_name="question_type",
                ),
                difficulty=_enum_value(item.difficulty, Difficulty, field_name="difficulty"),
                tag_names=_tag_list(item.tag_names),
                reference_points=_text_list(item.reference_points, field_name="reference_points"),
                follow_up_suggestions=_text_list(
                    item.follow_up_suggestions,
                    field_name="follow_up_suggestions",
                ),
                applicable_companies=_text_list(
                    item.applicable_companies,
                    field_name="applicable_companies",
                ),
                applicable_rounds=_text_list(
                    item.applicable_rounds,
                    field_name="applicable_rounds",
                ),
                source_note=_optional_text(
                    item.source_note,
                    field_name="source_note",
                    max_length=2_000,
                ),
                user_note=_optional_text(
                    item.user_note,
                    field_name="user_note",
                    max_length=10_000,
                ),
            )
        )
    return tuple(prepared)


def _canonical_request_hash(
    request: DiscoveryImportRequest,
    items: Sequence[_PreparedItem],
) -> str:
    """Hash the normalized request body that governs idempotent replay."""

    def enum_text(value: Enum | None) -> str | None:
        return value.value if value is not None else None

    body = {
        "run_id": str(request.run_id),
        "bank_id": str(request.bank_id),
        "items": [
            {
                "candidate_id": str(item.candidate_id),
                "candidate_revision": item.candidate_revision,
                "prompt": item.prompt,
                "question_type": enum_text(item.question_type),
                "difficulty": enum_text(item.difficulty),
                "tag_names": item.tag_names,
                "reference_points": item.reference_points,
                "follow_up_suggestions": item.follow_up_suggestions,
                "applicable_companies": item.applicable_companies,
                "applicable_rounds": item.applicable_rounds,
                "source_note": item.source_note,
                "user_note": item.user_note,
            }
            for item in items
        ],
    }
    encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _advisory_lock_key(profile_id: uuid.UUID, idempotency_key: str) -> int:
    digest = hashlib.sha256(f"{profile_id}:{idempotency_key}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _lock_idempotency_key(
    session: AsyncSession,
    profile_id: uuid.UUID,
    idempotency_key: str,
) -> None:
    """Serialize same-key requests without creating a pre-validation audit row."""

    await session.execute(
        select(func.pg_advisory_xact_lock(_advisory_lock_key(profile_id, idempotency_key)))
    )


async def _existing_imports(
    session: AsyncSession,
    profile_id: uuid.UUID,
    idempotency_key: str,
) -> tuple[QuestionDiscoveryImport, ...]:
    rows = await session.scalars(
        select(QuestionDiscoveryImport)
        .where(
            QuestionDiscoveryImport.profile_id == profile_id,
            QuestionDiscoveryImport.idempotency_key == idempotency_key,
        )
        .order_by(QuestionDiscoveryImport.created_at, QuestionDiscoveryImport.id)
    )
    return tuple(rows.all())


def _replay_result(
    request: DiscoveryImportRequest,
    items: Sequence[_PreparedItem],
    request_hash: str,
    existing: Sequence[QuestionDiscoveryImport],
) -> DiscoveryImportResult:
    if any(row.request_hash != request_hash for row in existing):
        raise _error(
            "discovery_import_conflict",
            "This idempotency key was already used with a different request.",
        )
    if any(
        DiscoveryImportStatus(row.status) is not DiscoveryImportStatus.SUCCEEDED
        for row in existing
    ):
        raise _error(
            "discovery_import_conflict",
            "This idempotency key belongs to an incomplete import.",
        )

    by_candidate = {row.candidate_id: row for row in existing if row.candidate_id is not None}
    requested_ids = {item.candidate_id for item in items}
    if set(by_candidate) != requested_ids or len(existing) != len(items):
        raise _error(
            "discovery_import_conflict",
            "This idempotency key does not match the requested candidate set.",
        )
    if any(row.question_id is None for row in by_candidate.values()):
        raise _error(
            "discovery_import_conflict",
            "The previous import result is no longer available for replay.",
        )

    batch_ids = {row.batch_id for row in existing}
    if len(batch_ids) != 1:
        raise _error(
            "discovery_import_conflict",
            "The previous import audit is inconsistent.",
        )
    result_items = tuple(
        ImportedDiscoveryQuestion(
            candidate_id=item.candidate_id,
            candidate_revision=by_candidate[item.candidate_id].candidate_revision,
            question_id=by_candidate[item.candidate_id].question_id,  # type: ignore[arg-type]
            import_id=by_candidate[item.candidate_id].id,
        )
        for item in items
    )
    return DiscoveryImportResult(
        run_id=request.run_id,
        bank_id=request.bank_id,
        batch_id=batch_ids.pop(),
        request_hash=request_hash,
        items=result_items,
        replayed=True,
    )


async def _load_and_validate(
    session: AsyncSession,
    profile_id: uuid.UUID,
    request: DiscoveryImportRequest,
    prepared_items: Sequence[_PreparedItem],
) -> _ValidatedRequest:
    run = await session.scalar(
        select(QuestionDiscoveryRun)
        .where(
            QuestionDiscoveryRun.id == request.run_id,
            QuestionDiscoveryRun.profile_id == profile_id,
            QuestionDiscoveryRun.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if run is None:
        raise _error(
            "question_discovery_not_found",
            "The discovery run was not found.",
            status_code=404,
        )

    bank = await session.scalar(
        select(QuestionBank)
        .where(
            QuestionBank.id == request.bank_id,
            QuestionBank.profile_id == profile_id,
            QuestionBank.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if bank is None:
        raise _error(
            "question_bank_not_found",
            "The target question bank was not found.",
            status_code=404,
        )

    candidate_ids = [item.candidate_id for item in prepared_items]
    candidates = (
        await session.scalars(
            select(QuestionDiscoveryCandidate)
            .where(
                QuestionDiscoveryCandidate.profile_id == profile_id,
                QuestionDiscoveryCandidate.run_id == run.id,
                QuestionDiscoveryCandidate.id.in_(candidate_ids),
                QuestionDiscoveryCandidate.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).all()
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    if set(candidates_by_id) != set(candidate_ids):
        raise _error(
            "discovery_candidate_not_found",
            "One or more selected candidates do not belong to this discovery run.",
            status_code=404,
        )

    evidence_rows = (
        await session.execute(
            select(QuestionDiscoveryCandidateEvidence, QuestionDiscoverySource)
            .join(
                QuestionDiscoverySource,
                QuestionDiscoverySource.id == QuestionDiscoveryCandidateEvidence.source_id,
            )
            .where(
                QuestionDiscoveryCandidateEvidence.profile_id == profile_id,
                QuestionDiscoveryCandidateEvidence.run_id == run.id,
                QuestionDiscoveryCandidateEvidence.candidate_id.in_(candidate_ids),
                QuestionDiscoveryCandidateEvidence.deleted_at.is_(None),
                QuestionDiscoverySource.profile_id == profile_id,
                QuestionDiscoverySource.run_id == run.id,
                QuestionDiscoverySource.deleted_at.is_(None),
            )
            .order_by(
                QuestionDiscoveryCandidateEvidence.candidate_id,
                QuestionDiscoveryCandidateEvidence.created_at,
                QuestionDiscoveryCandidateEvidence.id,
            )
        )
    ).all()
    evidence_by_candidate: dict[uuid.UUID, list[_EvidenceSnapshot]] = {
        candidate_id: [] for candidate_id in candidate_ids
    }
    for evidence, source in evidence_rows:
        if DiscoverySourceStatus(source.status) is not DiscoverySourceStatus.FETCHED:
            raise _error(
                "discovery_candidate_ungrounded",
                "A candidate references a source that is no longer available for review.",
                status_code=422,
            )
        excerpt = _optional_text(
            evidence.excerpt,
            field_name="evidence excerpt",
            max_length=4_000,
            allow_blank=False,
        )
        assert excerpt is not None
        source_title = (
            source.title.strip() if source.title and source.title.strip() else source.domain
        )
        evidence_by_candidate[evidence.candidate_id].append(
            _EvidenceSnapshot(
                source_title=source_title,
                normalized_url=verified_source_url(
                    normalized_url=source.normalized_url,
                    final_url=source.final_url,
                ),
                source_domain=source.domain,
                source_category=source.source_category,
                fetched_at=source.fetched_at or evidence.created_at,
                excerpt=excerpt,
                evidence_hash=evidence.evidence_hash,
                attribution=deepcopy(source.attribution),
            )
        )

    validated_items: list[_ValidatedItem] = []
    final_hashes: set[str] = set()
    for prepared in prepared_items:
        candidate = candidates_by_id[prepared.candidate_id]
        if candidate.candidate_revision != prepared.candidate_revision:
            raise _error(
                "discovery_candidate_stale",
                "A candidate changed after it was selected. Refresh and review it again.",
            )
        if DiscoveryCandidateStatus(candidate.status) not in _ALLOWED_CANDIDATE_STATUSES:
            raise _error(
                "discovery_candidate_unavailable",
                "A selected candidate is no longer available for import.",
            )
        candidate_prompt = _optional_text(
            candidate.prompt,
            field_name="candidate prompt",
            max_length=50_000,
            allow_blank=False,
        )
        assert candidate_prompt is not None
        if candidate.content_hash != prompt_hash(candidate_prompt):
            raise _error(
                "discovery_candidate_integrity_error",
                "A candidate no longer matches its reviewed content hash.",
            )
        prompt = prepared.prompt or candidate_prompt
        normalized_hash = prompt_hash(prompt)
        if normalized_hash in final_hashes:
            raise _error(
                "question_duplicate",
                "The import request contains duplicate question prompts for this bank.",
            )
        final_hashes.add(normalized_hash)

        evidence = tuple(evidence_by_candidate[candidate.id])
        if not evidence:
            raise _error(
                "discovery_candidate_ungrounded",
                "A selected candidate has no source evidence to preserve.",
                status_code=422,
            )
        question_type = prepared.question_type or QuestionType(candidate.question_type)
        difficulty = prepared.difficulty or Difficulty(candidate.difficulty)
        candidate_tags = _tag_list(candidate.suggested_tags)
        candidate_reference_points = _text_list(
            candidate.reference_points,
            field_name="candidate reference_points",
        )
        candidate_follow_ups = _text_list(
            candidate.follow_up_suggestions,
            field_name="candidate follow_up_suggestions",
        )
        candidate_companies = _text_list(
            candidate.applicable_companies,
            field_name="candidate applicable_companies",
        )
        candidate_rounds = _text_list(
            candidate.applicable_rounds,
            field_name="candidate applicable_rounds",
        )
        assert candidate_tags is not None
        assert candidate_reference_points is not None
        assert candidate_follow_ups is not None
        assert candidate_companies is not None
        assert candidate_rounds is not None
        source_note = (
            prepared.source_note
            if prepared.source_note is not None
            else _optional_text(
                candidate.matching_reason,
                field_name="candidate matching_reason",
                max_length=2_000,
            )
        )
        validated_items.append(
            _ValidatedItem(
                prepared=prepared,
                candidate=candidate,
                prompt=prompt,
                normalized_hash=normalized_hash,
                question_type=question_type,
                difficulty=difficulty,
                tag_names=prepared.tag_names if prepared.tag_names is not None else candidate_tags,
                reference_points=(
                    prepared.reference_points
                    if prepared.reference_points is not None
                    else candidate_reference_points
                ),
                follow_up_suggestions=(
                    prepared.follow_up_suggestions
                    if prepared.follow_up_suggestions is not None
                    else candidate_follow_ups
                ),
                applicable_companies=(
                    prepared.applicable_companies
                    if prepared.applicable_companies is not None
                    else candidate_companies
                ),
                applicable_rounds=(
                    prepared.applicable_rounds
                    if prepared.applicable_rounds is not None
                    else candidate_rounds
                ),
                source_note=source_note,
                user_note=prepared.user_note,
                evidence=evidence,
            )
        )

    duplicate_question_hash = await session.scalar(
        select(Question.id)
        .where(
            Question.bank_id == bank.id,
            Question.normalized_hash.in_(final_hashes),
        )
        .limit(1)
    )
    if duplicate_question_hash is not None:
        raise _error(
            "question_duplicate",
            "The target question bank already contains one of these prompts.",
        )

    duplicate_import = await session.scalar(
        select(QuestionDiscoveryImport.id)
        .where(
            QuestionDiscoveryImport.profile_id == profile_id,
            QuestionDiscoveryImport.bank_id == bank.id,
            QuestionDiscoveryImport.candidate_id.in_(candidate_ids),
            QuestionDiscoveryImport.status == DiscoveryImportStatus.SUCCEEDED,
        )
        .limit(1)
    )
    if duplicate_import is not None:
        raise _error(
            "discovery_import_duplicate",
            "A selected candidate was already imported into this question bank.",
        )
    return _ValidatedRequest(run=run, bank=bank, items=tuple(validated_items))


async def _attach_tags(
    session: AsyncSession,
    question_id: uuid.UUID,
    tag_names: Sequence[str],
) -> None:
    tag_ids: list[uuid.UUID] = []
    for name in tag_names:
        slug = tag_slug(name)
        tag = await session.scalar(
            select(QuestionTag).where(QuestionTag.slug == slug).with_for_update()
        )
        if tag is None:
            tag = QuestionTag(name=name, slug=slug)
            session.add(tag)
            await session.flush()
        tag_ids.append(tag.id)
    session.add_all(
        [QuestionTagLink(question_id=question_id, tag_id=tag_id) for tag_id in sorted(set(tag_ids))]
    )


async def _persist_import(
    session: AsyncSession,
    profile_id: uuid.UUID,
    request: DiscoveryImportRequest,
    request_hash: str,
    validated: _ValidatedRequest,
) -> DiscoveryImportResult:
    batch_id = uuid.uuid4()
    completed_at = utc_now()
    result_items: list[ImportedDiscoveryQuestion] = []
    for item in validated.items:
        question = Question(
            bank_id=validated.bank.id,
            prompt=item.prompt,
            question_type=item.question_type,
            difficulty=item.difficulty,
            status=QuestionStatus.DRAFT,
            reference_points=list(item.reference_points),
            follow_up_suggestions=list(item.follow_up_suggestions),
            applicable_companies=list(item.applicable_companies),
            applicable_rounds=list(item.applicable_rounds),
            source_type=SourceType.LINK_IMPORT,
            source_note=item.source_note,
            user_note=item.user_note,
            normalized_hash=item.normalized_hash,
        )
        session.add(question)
        await session.flush()
        await _attach_tags(session, question.id, item.tag_names)

        import_audit = QuestionDiscoveryImport(
            profile_id=profile_id,
            candidate_id=item.candidate.id,
            candidate_content_hash=item.candidate.content_hash,
            bank_id=validated.bank.id,
            question_id=question.id,
            idempotency_key=request.idempotency_key.strip(),
            request_hash=request_hash,
            candidate_revision=item.prepared.candidate_revision,
            batch_id=batch_id,
            status=DiscoveryImportStatus.SUCCEEDED,
            completed_at=completed_at,
        )
        session.add(import_audit)
        session.add_all(
            [
                QuestionSourceProvenance(
                    profile_id=profile_id,
                    question_id=question.id,
                    discovery_run_id=validated.run.id,
                    candidate_id=item.candidate.id,
                    source_title=evidence.source_title,
                    normalized_url=evidence.normalized_url,
                    source_domain=evidence.source_domain,
                    source_category=evidence.source_category,
                    fetched_at=evidence.fetched_at,
                    excerpt=evidence.excerpt,
                    evidence_hash=evidence.evidence_hash,
                    attribution=deepcopy(evidence.attribution),
                )
                for evidence in item.evidence
            ]
        )
        item.candidate.status = DiscoveryCandidateStatus.IMPORTED
        item.candidate.import_count += 1
        item.candidate.touch(at=completed_at)
        result_items.append(
            ImportedDiscoveryQuestion(
                candidate_id=item.candidate.id,
                candidate_revision=item.prepared.candidate_revision,
                question_id=question.id,
                import_id=import_audit.id,
            )
        )

    await session.flush()
    await session.commit()
    return DiscoveryImportResult(
        run_id=request.run_id,
        bank_id=validated.bank.id,
        batch_id=batch_id,
        request_hash=request_hash,
        items=tuple(result_items),
        replayed=False,
    )


async def import_discovery_candidates(
    session: AsyncSession,
    profile_id: uuid.UUID,
    request: DiscoveryImportRequest,
) -> DiscoveryImportResult:
    """Import selected candidates atomically and retain immutable source evidence.

    The caller must use a fresh request idempotency key for every intentional import
    attempt.  Retrying the exact same key/body returns the original result without
    inserting additional questions; reusing a key with a changed body raises
    ``discovery_import_conflict``.
    """

    prepared_items = _prepare_request(request)
    normalized_key = request.idempotency_key.strip()
    request_hash = _canonical_request_hash(request, prepared_items)
    try:
        await _lock_idempotency_key(session, profile_id, normalized_key)
        existing = await _existing_imports(session, profile_id, normalized_key)
        if existing:
            result = _replay_result(request, prepared_items, request_hash, existing)
            # The advisory lock is transaction-scoped.  A read-only rollback releases
            # it without creating any extra audit state for a replay.
            await session.rollback()
            return result

        validated = await _load_and_validate(session, profile_id, request, prepared_items)
        return await _persist_import(session, profile_id, request, request_hash, validated)
    except AppError:
        await session.rollback()
        raise
    except IntegrityError as exc:
        await session.rollback()
        raise _error(
            "discovery_import_conflict",
            "The import conflicts with a concurrent question-bank change.",
        ) from exc
    except Exception:
        await session.rollback()
        raise
