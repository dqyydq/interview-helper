"""Add durable realtime interview events.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_realtime_events",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("client_event_id", sa.String(length=120), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["interview_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_realtime_event_sequence"),
        sa.UniqueConstraint("session_id", "event_id", name="uq_realtime_event_id"),
        sa.UniqueConstraint("session_id", "client_event_id", name="uq_realtime_client_event_id"),
    )
    op.create_index(
        op.f("ix_interview_realtime_events_session_id"),
        "interview_realtime_events",
        ["session_id"],
    )
    op.create_index(
        op.f("ix_interview_realtime_events_event_id"),
        "interview_realtime_events",
        ["event_id"],
    )
    op.create_index(
        op.f("ix_interview_realtime_events_client_event_id"),
        "interview_realtime_events",
        ["client_event_id"],
    )
    op.create_index(
        op.f("ix_interview_realtime_events_event_type"),
        "interview_realtime_events",
        ["event_type"],
    )


def downgrade() -> None:
    op.drop_table("interview_realtime_events")
