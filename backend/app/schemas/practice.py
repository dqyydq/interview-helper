import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.common import PracticeTaskStatus
from app.schemas.common import ApiModel, EntityPublic


class PracticeTaskCreateFromReport(ApiModel):
    """The caller selects existing report action-plan indexes, never task text."""

    action_indices: list[int] = Field(min_length=1, max_length=12)


class PracticeTaskUpdate(ApiModel):
    status: PracticeTaskStatus


class PracticeTaskPublic(EntityPublic):
    report_id: uuid.UUID
    action_index: int = Field(ge=0)
    title: str
    instruction: str
    success_criteria: str
    priority: int = Field(ge=1, le=3)
    status: PracticeTaskStatus
    last_session_id: uuid.UUID | None
    completed_at: datetime | None
