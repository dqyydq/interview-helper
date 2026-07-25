from datetime import UTC, datetime
from uuid import uuid4

from app.db.models.common import (
    DiscoveryCandidateStatus,
    DiscoveryImportStatus,
    DiscoveryProviderType,
    DiscoveryRunStatus,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    EmbeddingProfileStatus,
    EntityBase,
    EvaluationAnchor,
    JobStatus,
    JobType,
    MemoryType,
    MessageRole,
    QuestionStatus,
    SessionStatus,
    Visibility,
)
from app.db.models.memory import MemoryItem
from app.schemas.common import ApiModel


class EnumEnvelope(ApiModel):
    question_status: QuestionStatus
    session_status: SessionStatus
    job_status: JobStatus
    anchor: EvaluationAnchor
    role: MessageRole
    visibility: Visibility


class DiscoveryEnumEnvelope(ApiModel):
    provider_type: DiscoveryProviderType
    source_mode: DiscoverySourceMode
    run_status: DiscoveryRunStatus
    source_status: DiscoverySourceStatus
    candidate_status: DiscoveryCandidateStatus
    import_status: DiscoveryImportStatus
    job_type: JobType


class EmbeddingEnumEnvelope(ApiModel):
    status: EmbeddingProfileStatus
    job_type: JobType


def test_entity_defaults_are_utc_versioned_and_not_deleted() -> None:
    entity = EntityBase()

    assert entity.id is not None
    assert entity.created_at.tzinfo is UTC
    assert entity.updated_at.tzinfo is UTC
    assert entity.deleted_at is None
    assert entity.version == 1


def test_soft_delete_preserves_identity_and_advances_version() -> None:
    entity = EntityBase()
    deleted_at = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)

    entity.soft_delete(at=deleted_at)

    assert entity.deleted_at == deleted_at
    assert entity.updated_at == deleted_at
    assert entity.version == 2


def test_stable_enums_serialize_as_strings() -> None:
    payload = EnumEnvelope(
        question_status=QuestionStatus.ACTIVE,
        session_status=SessionStatus.INTERVIEWING,
        job_status=JobStatus.RUNNING,
        anchor=EvaluationAnchor.SOLID,
        role=MessageRole.ASSISTANT,
        visibility=Visibility.PRIVATE,
    )

    assert payload.model_dump() == {
        "question_status": "active",
        "session_status": "interviewing",
        "job_status": "running",
        "anchor": "solid",
        "role": "assistant",
        "visibility": "private",
    }


def test_discovery_enums_serialize_as_stable_strings() -> None:
    payload = DiscoveryEnumEnvelope(
        provider_type=DiscoveryProviderType.TAVILY,
        source_mode=DiscoverySourceMode.URLS,
        run_status=DiscoveryRunStatus.PARTIAL,
        source_status=DiscoverySourceStatus.BLOCKED,
        candidate_status=DiscoveryCandidateStatus.PROPOSED,
        import_status=DiscoveryImportStatus.SUCCEEDED,
        job_type=JobType.QUESTION_DISCOVERY,
    )

    assert payload.model_dump() == {
        "provider_type": "tavily",
        "source_mode": "urls",
        "run_status": "partial",
        "source_status": "blocked",
        "candidate_status": "proposed",
        "import_status": "succeeded",
        "job_type": "question_discovery",
    }


def test_embedding_enums_serialize_as_stable_strings() -> None:
    payload = EmbeddingEnumEnvelope(
        status=EmbeddingProfileStatus.BUILDING,
        job_type=JobType.EMBEDDING_REINDEX,
    )

    assert payload.model_dump() == {
        "status": "building",
        "job_type": "embedding_reindex",
    }


def test_memory_content_version_is_independent_from_row_revision() -> None:
    item = MemoryItem(
        profile_id=uuid4(),
        memory_type=MemoryType.PROJECT_FACT,
        canonical_key="project:interview-helper",
        content="FastAPI interview simulator",
    )

    item.touch()

    assert item.version == 2
    assert item.memory_version == 1
