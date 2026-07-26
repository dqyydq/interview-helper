import base64
import binascii
from enum import StrEnum
from ipaddress import ip_address
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.db.models.common import MessageRole


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ImageMediaType(StrEnum):
    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"
    GIF = "image/gif"


MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BASE64_CHARACTERS = 5_242_880
MAX_MESSAGE_IMAGE_BASE64_CHARACTERS = 8_388_608
IMAGE_INPUT_TOKEN_ESTIMATE = 1_024


class ChatImage(BaseModel):
    """A bounded image attachment shared by every chat-provider adapter.

    The application keeps the existing textual message contract unchanged and
    carries images alongside it. URL sources are restricted to public HTTPS URLs;
    a product-facing upload flow must still run its full DNS/SSRF policy before
    constructing this object. Base64 sources contain raw base64 only (never a
    ``data:`` URI), so each adapter can render its protocol-specific payload.
    """

    source_type: Literal["url", "base64"]
    url: str | None = Field(default=None, min_length=1, max_length=2_048)
    media_type: ImageMediaType | None = None
    data: str | None = Field(default=None, min_length=1, max_length=MAX_IMAGE_BASE64_CHARACTERS)

    @classmethod
    def _is_public_https_url(cls, value: str) -> bool:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        try:
            ip_address(hostname)
        except ValueError:
            pass
        else:
            return False
        normalized_hostname = hostname.casefold()
        return bool(
            parsed.scheme.casefold() == "https"
            and parsed.netloc
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
            and "." in normalized_hostname
            and normalized_hostname not in {"localhost", "localhost.localdomain"}
            and not normalized_hostname.endswith(
                (".localhost", ".local", ".internal", ".invalid", ".test", ".example")
            )
        )

    def model_post_init(self, __context: Any) -> None:
        if self.source_type == "url":
            if self.url is None or self.data is not None or self.media_type is not None:
                raise ValueError("URL image sources must only include a public HTTPS URL")
            if not self._is_public_https_url(self.url):
                raise ValueError("image URL must be a public HTTPS URL without credentials")
            return

        if self.data is None or self.url is not None or self.media_type is None:
            raise ValueError("base64 image sources require data and media_type only")
        try:
            base64.b64decode(self.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image data must be valid raw base64") from exc

    @property
    def estimated_input_tokens(self) -> int:
        """A conservative cross-provider reservation, never an exact token count."""

        return IMAGE_INPUT_TOKEN_ESTIMATE

    def openai_image_url(self) -> str:
        if self.source_type == "url":
            assert self.url is not None
            return self.url
        assert self.data is not None and self.media_type is not None
        return f"data:{self.media_type.value};base64,{self.data}"

    def anthropic_source(self) -> dict[str, str]:
        if self.source_type == "url":
            assert self.url is not None
            return {"type": "url", "url": self.url}
        assert self.data is not None and self.media_type is not None
        return {
            "type": "base64",
            "media_type": self.media_type.value,
            "data": self.data,
        }


class ChatMessage(BaseModel):
    role: MessageRole
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    images: list[ChatImage] = Field(default_factory=list, max_length=MAX_IMAGES_PER_MESSAGE)

    def model_post_init(self, __context: Any) -> None:
        if self.images and self.role is not MessageRole.USER:
            raise ValueError("image inputs are only supported in user messages")
        base64_characters = sum(len(image.data or "") for image in self.images)
        if base64_characters > MAX_MESSAGE_IMAGE_BASE64_CHARACTERS:
            raise ValueError("total base64 image data exceeds the per-message limit")


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
