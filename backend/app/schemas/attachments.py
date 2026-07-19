import uuid
from typing import Literal

from pydantic import Field, field_validator

from app.db.models.common import AttachmentType
from app.schemas.common import ApiModel, EntityPublic

MAX_CODE_ATTACHMENT_BYTES = 32 * 1024
MAX_CODE_ATTACHMENTS_PER_ANSWER = 3


class CodeAttachmentInput(ApiModel):
    attachment_type: Literal["code"] = "code"
    language: str = Field(default="text", min_length=1, max_length=40, pattern=r"^[a-z0-9_+#.-]+$")
    content: str = Field(min_length=1, max_length=32_768)
    filename: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("content")
    @classmethod
    def validate_encoded_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_CODE_ATTACHMENT_BYTES:
            raise ValueError("代码附件不能超过 32 KB")
        return value


class AnswerCommitPayload(ApiModel):
    text: str = Field(min_length=1, max_length=50_000)
    attachments: list[CodeAttachmentInput] = Field(
        default_factory=list,
        max_length=MAX_CODE_ATTACHMENTS_PER_ANSWER,
    )


class AnswerAttachmentPublic(EntityPublic):
    message_id: uuid.UUID
    attachment_type: AttachmentType
    filename: str | None
    mime_type: str | None
    language: str | None
    content: str | None
    size_bytes: int
