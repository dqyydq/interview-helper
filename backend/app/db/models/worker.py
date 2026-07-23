from datetime import datetime

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field

from app.db.models.common import EntityBase, utc_now


class WorkerHeartbeat(EntityBase, table=True):
    """A privacy-safe liveness record written by each local worker process."""

    __tablename__ = "worker_heartbeats"

    worker_id: str = Field(min_length=1, max_length=255, unique=True, index=True)
    state: str = Field(
        default="starting",
        max_length=32,
        sa_column=Column(String(32), nullable=False),
    )
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )
    last_seen_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    last_job_type: str | None = Field(default=None, max_length=48)
    last_error_type: str | None = Field(default=None, max_length=120)
    last_error_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
    )
