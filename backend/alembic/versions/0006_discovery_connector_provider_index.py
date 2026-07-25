"""Index active discovery connectors by profile and provider.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_discovery_connectors_active_profile_provider",
        "discovery_connectors",
        ["profile_id", "provider_type"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_discovery_connectors_active_profile_provider",
        table_name="discovery_connectors",
    )
