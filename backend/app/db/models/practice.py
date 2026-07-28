import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlmodel import Field

from app.db.models.common import EntityBase, PracticeTaskStatus


class PracticeTask(EntityBase, table=True):
    """A private, user-confirmed snapshot of one report action item.

    A task deliberately copies the action text from a completed report instead
    of accepting arbitrary client content.  That keeps the report-to-practice
    handoff traceable and makes task creation safely idempotent by action index.
    """

    __tablename__ = "practice_tasks"
    __table_args__ = (
        UniqueConstraint("source_report_id", "action_index", name="uq_practice_task_report_action"),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    source_report_id: uuid.UUID = Field(
        foreign_key="evaluation_reports.id",
        ondelete="CASCADE",
        index=True,
    )
    action_index: int = Field(ge=0, sa_column=Column(Integer, nullable=False))
    title: str = Field(min_length=1, max_length=200, sa_type=Text)
    instruction: str = Field(min_length=1, max_length=2_000, sa_type=Text)
    success_criteria: str = Field(min_length=1, max_length=1_000, sa_type=Text)
    priority: int = Field(default=2, ge=1, le=3)
    status: PracticeTaskStatus = Field(
        default=PracticeTaskStatus.PENDING,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    last_session_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="interview_sessions.id",
        ondelete="SET NULL",
        index=True,
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
