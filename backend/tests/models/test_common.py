from datetime import UTC, datetime
from uuid import uuid4

from app.db.models.common import (
    EntityBase,
    EvaluationAnchor,
    JobStatus,
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
