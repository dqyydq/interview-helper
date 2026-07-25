"""Add immutable embedding profiles and profile-scoped pgvector records.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
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
    # These tenant-identity constraints make the composite FKs below enforce
    # that a vector cannot point at another user's source item.
    op.create_unique_constraint(
        "uq_memory_item_profile_identity",
        "memory_items",
        ["profile_id", "id"],
    )
    op.create_unique_constraint(
        "uq_plan_question_plan_identity",
        "plan_questions",
        ["plan_id", "id"],
    )

    op.create_table(
        "embedding_profiles",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("model_connection_id", sa.Uuid(), nullable=True),
        sa.Column("local_capability_key", sa.String(length=80), nullable=True),
        sa.Column("target_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("model_revision", sa.String(length=255), nullable=False),
        sa.Column("vector_dimensions", sa.Integer(), nullable=True),
        sa.Column("normalized", sa.Boolean(), nullable=False),
        sa.Column("query_instruction", sa.Text(), nullable=False),
        sa.Column("distance_metric", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(model_connection_id, local_capability_key) = 1",
            name="ck_embedding_profile_exactly_one_target",
        ),
        sa.CheckConstraint(
            "local_capability_key IS NULL "
            "OR local_capability_key IN ('multilingual-e5-small', 'bge-m3')",
            name="ck_embedding_profile_local_capability",
        ),
        sa.CheckConstraint(
            "vector_dimensions IS NULL OR vector_dimensions BETWEEN 1 AND 2000",
            name="ck_embedding_profile_vector_dimensions",
        ),
        sa.CheckConstraint(
            "status <> 'active' OR vector_dimensions IS NOT NULL",
            name="ck_embedding_profile_active_dimensions",
        ),
        sa.CheckConstraint(
            "distance_metric IN ('cosine', 'l2', 'inner_product')",
            name="ck_embedding_profile_distance_metric",
        ),
        sa.CheckConstraint(
            "status IN ('building', 'active', 'failed', 'retired')",
            name="ck_embedding_profile_status",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_connection_id"],
            ["model_connections.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "id",
            name="uq_embedding_profile_profile_identity",
        ),
    )
    op.create_index(
        op.f("ix_embedding_profiles_profile_id"),
        "embedding_profiles",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_embedding_profiles_model_connection_id"),
        "embedding_profiles",
        ["model_connection_id"],
    )
    op.create_index(
        op.f("ix_embedding_profiles_target_fingerprint"),
        "embedding_profiles",
        ["target_fingerprint"],
    )
    op.create_index(
        op.f("ix_embedding_profiles_model_name"),
        "embedding_profiles",
        ["model_name"],
    )
    op.create_index(
        op.f("ix_embedding_profiles_status"),
        "embedding_profiles",
        ["status"],
    )
    op.create_index(
        "uq_embedding_profile_active_for_profile",
        "embedding_profiles",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_embedding_profile_building_for_profile",
        "embedding_profiles",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'building' AND deleted_at IS NULL"),
    )

    # Profile configuration is a compatibility contract for every vector it
    # owns.  Lifecycle fields (status and diagnostics) remain mutable, while a
    # model switch must create a new profile and re-index in the background.
    op.execute(
        """
        CREATE FUNCTION enforce_embedding_profile_configuration_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.profile_id IS DISTINCT FROM NEW.profile_id
                OR OLD.model_connection_id IS DISTINCT FROM NEW.model_connection_id
                OR OLD.local_capability_key IS DISTINCT FROM NEW.local_capability_key
                OR OLD.target_fingerprint IS DISTINCT FROM NEW.target_fingerprint
                OR OLD.model_name IS DISTINCT FROM NEW.model_name
                OR OLD.model_revision IS DISTINCT FROM NEW.model_revision
                OR (
                    OLD.vector_dimensions IS DISTINCT FROM NEW.vector_dimensions
                    AND NOT (
                        OLD.vector_dimensions IS NULL
                        AND NEW.vector_dimensions IS NOT NULL
                        AND OLD.status = 'building'
                        AND NEW.status = 'building'
                    )
                )
                OR OLD.normalized IS DISTINCT FROM NEW.normalized
                OR OLD.query_instruction IS DISTINCT FROM NEW.query_instruction
                OR OLD.distance_metric IS DISTINCT FROM NEW.distance_metric
            THEN
                RAISE EXCEPTION
                    'Embedding profile configuration is immutable; create a new profile.'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_embedding_profile_configuration_immutable
        BEFORE UPDATE ON embedding_profiles
        FOR EACH ROW
        EXECUTE FUNCTION enforce_embedding_profile_configuration_immutable();
        """
    )

    op.create_table(
        "memory_embeddings",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_profile_id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("role_name", sa.String(length=160), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["profile_id", "embedding_profile_id"],
            ["embedding_profiles.profile_id", "embedding_profiles.id"],
            name="fk_memory_embedding_profile_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "memory_id"],
            ["memory_items.profile_id", "memory_items.id"],
            name="fk_memory_embedding_memory_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_memory_embeddings_profile_id"),
        "memory_embeddings",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_memory_embeddings_embedding_profile_id"),
        "memory_embeddings",
        ["embedding_profile_id"],
    )
    op.create_index(op.f("ix_memory_embeddings_memory_id"), "memory_embeddings", ["memory_id"])
    op.create_index(op.f("ix_memory_embeddings_company_id"), "memory_embeddings", ["company_id"])
    op.create_index(op.f("ix_memory_embeddings_role_name"), "memory_embeddings", ["role_name"])
    op.create_index(
        op.f("ix_memory_embeddings_content_hash"), "memory_embeddings", ["content_hash"]
    )
    op.create_index(
        "uq_memory_embedding_current_source",
        "memory_embeddings",
        ["profile_id", "embedding_profile_id", "memory_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_memory_embeddings_profile_scope",
        "memory_embeddings",
        ["profile_id", "embedding_profile_id", "company_id", "role_name"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "plan_question_embeddings",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_profile_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_question_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("role_name", sa.String(length=160), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["profile_id", "embedding_profile_id"],
            ["embedding_profiles.profile_id", "embedding_profiles.id"],
            name="fk_plan_question_embedding_profile_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_question_id"],
            ["plan_questions.plan_id", "plan_questions.id"],
            name="fk_plan_question_embedding_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_plan_question_embeddings_profile_id"),
        "plan_question_embeddings",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_plan_question_embeddings_embedding_profile_id"),
        "plan_question_embeddings",
        ["embedding_profile_id"],
    )
    op.create_index(
        op.f("ix_plan_question_embeddings_plan_id"),
        "plan_question_embeddings",
        ["plan_id"],
    )
    op.create_index(
        op.f("ix_plan_question_embeddings_plan_question_id"),
        "plan_question_embeddings",
        ["plan_question_id"],
    )
    op.create_index(
        op.f("ix_plan_question_embeddings_company_id"),
        "plan_question_embeddings",
        ["company_id"],
    )
    op.create_index(
        op.f("ix_plan_question_embeddings_role_name"),
        "plan_question_embeddings",
        ["role_name"],
    )
    op.create_index(
        op.f("ix_plan_question_embeddings_content_hash"),
        "plan_question_embeddings",
        ["content_hash"],
    )
    op.create_index(
        "uq_plan_question_embedding_current_source",
        "plan_question_embeddings",
        ["profile_id", "embedding_profile_id", "plan_question_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_plan_question_embeddings_profile_scope",
        "plan_question_embeddings",
        ["profile_id", "embedding_profile_id", "company_id", "role_name"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("plan_question_embeddings")
    op.drop_table("memory_embeddings")
    op.execute("DROP TRIGGER trg_embedding_profile_configuration_immutable ON embedding_profiles")
    op.execute("DROP FUNCTION enforce_embedding_profile_configuration_immutable()")
    op.drop_table("embedding_profiles")
    op.drop_constraint(
        "uq_plan_question_plan_identity",
        "plan_questions",
        type_="unique",
    )
    op.drop_constraint(
        "uq_memory_item_profile_identity",
        "memory_items",
        type_="unique",
    )
