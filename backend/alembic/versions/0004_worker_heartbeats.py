"""Add worker heartbeat diagnostics.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_job_type", sa.String(length=48), nullable=True),
        sa.Column("last_error_type", sa.String(length=120), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_worker_heartbeats_worker_id"),
        "worker_heartbeats",
        ["worker_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_worker_heartbeats_last_seen_at"),
        "worker_heartbeats",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_worker_heartbeats_last_seen_at"), table_name="worker_heartbeats")
    op.drop_index(op.f("ix_worker_heartbeats_worker_id"), table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
