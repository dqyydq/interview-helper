"""Enable the pgvector extension before vector-backed tables are created.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The Compose image supplies pgvector.  Keeping this in Alembic upgrades
    # existing named volumes safely; an init script would skip them forever.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Do not drop a shared extension on downgrade.  Other applications or a
    # manually retained vector table may still depend on it.
    pass
