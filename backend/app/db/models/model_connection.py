import uuid

from sqlalchemy import Column, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import (
    ConnectionStatus,
    EntityBase,
    ModelRole,
    ProviderType,
)


class ModelConnection(EntityBase, table=True):
    __tablename__ = "model_connections"
    __table_args__ = (
        UniqueConstraint("profile_id", "name", name="uq_model_connection_name"),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    name: str = Field(min_length=1, max_length=120)
    provider_type: ProviderType = Field(sa_column=Column(String(32), nullable=False, index=True))
    base_url: str = Field(min_length=1, max_length=2_048)
    encrypted_api_key: str | None = Field(default=None, max_length=16_000, sa_type=Text)
    model_name: str = Field(min_length=1, max_length=255, index=True)
    extra_headers_encrypted: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    context_window_tokens: int = Field(ge=1_024, le=10_000_000)
    max_output_tokens: int = Field(default=4_096, ge=1, le=1_000_000)
    tokenizer_type: str = Field(default="estimated", min_length=1, max_length=80)
    supports_prompt_caching: bool = Field(default=False, nullable=False)
    supports_token_count_endpoint: bool = Field(default=False, nullable=False)
    status: ConnectionStatus = Field(
        default=ConnectionStatus.UNTESTED,
        sa_column=Column(String(32), nullable=False, index=True),
    )


class ModelRoleBinding(EntityBase, table=True):
    __tablename__ = "model_role_bindings"
    __table_args__ = (
        UniqueConstraint("profile_id", "role", name="uq_model_role_binding"),
    )

    profile_id: uuid.UUID = Field(
        foreign_key="user_profiles.id",
        ondelete="CASCADE",
        index=True,
    )
    role: ModelRole = Field(sa_column=Column(String(32), nullable=False, index=True))
    connection_id: uuid.UUID = Field(
        foreign_key="model_connections.id",
        ondelete="RESTRICT",
        index=True,
    )
