"""Add private practice tasks and explicit session trend semantics.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _entity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "practice_tasks",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("source_report_id", sa.Uuid(), nullable=False),
        sa.Column("action_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("success_criteria", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("last_session_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("action_index >= 0", name="ck_practice_task_action_index"),
        sa.CheckConstraint("priority BETWEEN 1 AND 3", name="ck_practice_task_priority"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'dismissed')",
            name="ck_practice_task_status",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_report_id"],
            ["evaluation_reports.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_session_id"],
            ["interview_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_report_id",
            "action_index",
            name="uq_practice_task_report_action",
        ),
    )
    op.create_index(op.f("ix_practice_tasks_profile_id"), "practice_tasks", ["profile_id"])
    op.create_index(
        op.f("ix_practice_tasks_source_report_id"),
        "practice_tasks",
        ["source_report_id"],
    )
    op.create_index(op.f("ix_practice_tasks_status"), "practice_tasks", ["status"])
    op.create_index(
        op.f("ix_practice_tasks_last_session_id"),
        "practice_tasks",
        ["last_session_id"],
    )

    op.add_column(
        "interview_configs",
        sa.Column("session_kind", sa.String(length=32), nullable=False, server_default="standard"),
    )
    op.add_column(
        "interview_configs",
        sa.Column("practice_task_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_interview_configs_session_kind"),
        "interview_configs",
        ["session_kind"],
    )
    op.create_index(
        op.f("ix_interview_configs_practice_task_id"),
        "interview_configs",
        ["practice_task_id"],
    )
    op.create_foreign_key(
        "fk_interview_configs_practice_task",
        "interview_configs",
        "practice_tasks",
        ["practice_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_interview_configs_session_kind",
        "interview_configs",
        "session_kind IN ('standard', 'quick_trial', 'targeted_practice')",
    )

    op.add_column(
        "interview_sessions",
        sa.Column("session_kind", sa.String(length=32), nullable=False, server_default="standard"),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("include_in_trends", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(
        op.f("ix_interview_sessions_session_kind"),
        "interview_sessions",
        ["session_kind"],
    )
    op.create_index(
        op.f("ix_interview_sessions_include_in_trends"),
        "interview_sessions",
        ["include_in_trends"],
    )
    op.create_check_constraint(
        "ck_interview_sessions_session_kind",
        "interview_sessions",
        "session_kind IN ('standard', 'quick_trial', 'targeted_practice')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_interview_sessions_session_kind",
        "interview_sessions",
        type_="check",
    )
    op.drop_index(op.f("ix_interview_sessions_include_in_trends"), "interview_sessions")
    op.drop_index(op.f("ix_interview_sessions_session_kind"), "interview_sessions")
    op.drop_column("interview_sessions", "include_in_trends")
    op.drop_column("interview_sessions", "session_kind")

    op.drop_constraint(
        "ck_interview_configs_session_kind",
        "interview_configs",
        type_="check",
    )
    op.drop_constraint(
        "fk_interview_configs_practice_task",
        "interview_configs",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_interview_configs_practice_task_id"), "interview_configs")
    op.drop_index(op.f("ix_interview_configs_session_kind"), "interview_configs")
    op.drop_column("interview_configs", "practice_task_id")
    op.drop_column("interview_configs", "session_kind")

    op.drop_table("practice_tasks")
