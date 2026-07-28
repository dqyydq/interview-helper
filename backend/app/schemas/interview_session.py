import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.common import MessageRole, SessionKind, SessionStatus
from app.schemas.common import ApiModel, EntityPublic
from app.schemas.interview_plan import InterviewPlanPublic


class InterviewSessionCreate(ApiModel):
    plan_id: uuid.UUID
    excluded_memory_ids: list[uuid.UUID] = Field(default_factory=list)


class TrendInclusionUpdate(ApiModel):
    include_in_trends: bool


class InterviewMessagePublic(EntityPublic):
    session_id: uuid.UUID
    plan_question_id: uuid.UUID | None
    segment_id: uuid.UUID | None
    role: MessageRole
    sequence: int
    content: str
    confirmed: bool
    token_count: int | None
    message_metadata: dict


class InterviewSessionPublic(EntityPublic):
    plan_id: uuid.UUID
    status: SessionStatus
    session_kind: SessionKind
    include_in_trends: bool
    started_at: datetime | None
    ended_at: datetime | None
    current_question_sequence: int | None
    last_event_sequence: int
    failure_code: str | None
    plan: InterviewPlanPublic
    messages: list[InterviewMessagePublic]
