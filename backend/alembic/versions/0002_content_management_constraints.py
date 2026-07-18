"""content management constraints

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("profile_id", sa.Uuid(), nullable=True))
    op.create_index("ix_companies_profile_id", "companies", ["profile_id"], unique=False)
    op.create_foreign_key(
        "fk_companies_profile_id_user_profiles",
        "companies",
        "user_profiles",
        ["profile_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_round_profile_sequence",
        "round_profiles",
        ["style_pack_id", "sequence"],
    )
    op.create_unique_constraint(
        "uq_resume_profile_hash",
        "resumes",
        ["profile_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_resume_profile_hash", "resumes", type_="unique")
    op.drop_constraint("uq_round_profile_sequence", "round_profiles", type_="unique")
    op.drop_constraint(
        "fk_companies_profile_id_user_profiles",
        "companies",
        type_="foreignkey",
    )
    op.drop_index("ix_companies_profile_id", table_name="companies")
    op.drop_column("companies", "profile_id")
