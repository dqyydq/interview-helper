"""Allow a model role to target a fixed local Docker capability.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "model_role_bindings",
        "connection_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.add_column(
        "model_role_bindings",
        sa.Column("local_capability_key", sa.String(length=80), nullable=True),
    )
    op.create_check_constraint(
        "ck_model_role_binding_exactly_one_target",
        "model_role_bindings",
        "num_nonnulls(connection_id, local_capability_key) = 1",
    )
    op.create_check_constraint(
        "ck_model_role_binding_local_capability_role",
        "model_role_bindings",
        "local_capability_key IS NULL "
        "OR (role = 'transcriber' AND local_capability_key = 'sensevoice-small') "
        "OR (role = 'embedding' AND local_capability_key IN ('multilingual-e5-small', 'bge-m3'))",
    )


def downgrade() -> None:
    bind = op.get_bind()
    local_binding_count = bind.execute(
        sa.text("SELECT count(*) FROM model_role_bindings WHERE local_capability_key IS NOT NULL")
    ).scalar_one()
    if local_binding_count:
        raise RuntimeError(
            "Remove local capability bindings before downgrading migration 0007."
        )
    op.drop_constraint(
        "ck_model_role_binding_local_capability_role",
        "model_role_bindings",
        type_="check",
    )
    op.drop_constraint(
        "ck_model_role_binding_exactly_one_target",
        "model_role_bindings",
        type_="check",
    )
    op.drop_column("model_role_bindings", "local_capability_key")
    op.alter_column(
        "model_role_bindings",
        "connection_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
