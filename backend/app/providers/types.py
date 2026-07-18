from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import MessageRole


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: MessageRole
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    system: str | None = None
    max_tokens: int = Field(default=1_024, ge=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    tools: list[ToolDefinition] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None


class Usage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ChatResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None
    provider_request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    COMPLETED = "completed"
    FAILED = "failed"


class StreamEvent(BaseModel):
    type: StreamEventType
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    retryable: bool = False


class ProviderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"


class ProviderHealth(BaseModel):
    status: ProviderHealthStatus
    latency_ms: int = Field(ge=0)
    error_code: str | None = None


class EmbeddingRequest(BaseModel):
    texts: list[str]


class EmbeddingResponse(BaseModel):
    vectors: list[list[float]]
    usage: Usage = Field(default_factory=Usage)
