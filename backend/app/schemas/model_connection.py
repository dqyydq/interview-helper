import uuid
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from app.db.models.common import ConnectionStatus, ModelRole, ProviderType
from app.schemas.common import ApiModel, EntityPublic


class ModelConnectionCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    provider_type: ProviderType
    base_url: AnyHttpUrl
    api_key: str = Field(min_length=1, max_length=16_000, repr=False)
    model_name: str = Field(min_length=1, max_length=255)
    extra_headers: dict[str, str] = Field(default_factory=dict, repr=False)
    context_window_tokens: int = Field(ge=1_024, le=10_000_000)
    max_output_tokens: int = Field(default=4_096, ge=1, le=1_000_000)
    tokenizer_type: str = Field(default="estimated", min_length=1, max_length=80)
    supports_prompt_caching: bool = False
    supports_token_count_endpoint: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Base URL 不能包含凭据、查询参数或片段")
        return value

    @field_validator("extra_headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        invalid_length = any(len(key) > 120 or len(item) > 2_000 for key, item in value.items())
        if len(value) > 32 or invalid_length:
            raise ValueError("额外请求头数量或长度超出限制")
        return value


class ModelConnectionUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_type: ProviderType | None = None
    base_url: AnyHttpUrl | None = None
    api_key: str | None = Field(default=None, min_length=1, max_length=16_000, repr=False)
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    extra_headers: dict[str, str] | None = Field(default=None, repr=False)
    context_window_tokens: int | None = Field(default=None, ge=1_024, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    tokenizer_type: str | None = Field(default=None, min_length=1, max_length=80)
    supports_prompt_caching: bool | None = None
    supports_token_count_endpoint: bool | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        has_sensitive_parts = value is not None and (
            value.username or value.password or value.query or value.fragment
        )
        if has_sensitive_parts:
            raise ValueError("Base URL 不能包含凭据、查询参数或片段")
        return value

    @field_validator("extra_headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is not None and (
            len(value) > 32
            or any(len(key) > 120 or len(item) > 2_000 for key, item in value.items())
        ):
            raise ValueError("额外请求头数量或长度超出限制")
        return value


class ModelConnectionPublic(EntityPublic):
    name: str
    provider_type: ProviderType
    base_url: str
    model_name: str
    context_window_tokens: int
    max_output_tokens: int
    tokenizer_type: str
    supports_prompt_caching: bool
    supports_token_count_endpoint: bool
    status: ConnectionStatus
    has_api_key: bool


class ConnectionTestResult(ApiModel):
    status: ConnectionStatus
    latency_ms: int
    error_code: str | None = None


class RoleBindingUpdate(ApiModel):
    connection_id: uuid.UUID | None = None
    local_capability_key: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_one_target(self) -> "RoleBindingUpdate":
        if (self.connection_id is None) == (self.local_capability_key is None):
            raise ValueError("必须且只能选择一个模型连接或本地能力")
        return self


class RoleBindingPublic(EntityPublic):
    role: ModelRole
    target_kind: Literal["model_connection", "local_capability"]
    connection_id: uuid.UUID | None = None
    connection_name: str | None = None
    model_name: str | None = None
    connection_status: ConnectionStatus | None = None
    local_capability_key: str | None = None


class ModelReadiness(ApiModel):
    ready: bool
    missing_roles: list[ModelRole]
    degraded_roles: list[ModelRole]
