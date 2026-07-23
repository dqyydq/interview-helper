import uuid

import pytest
from pydantic import ValidationError

from app.context.builder import _message_text
from app.db.models.common import AttachmentType, MessageRole
from app.db.models.interview import AnswerAttachment, InterviewMessage
from app.schemas.attachments import AnswerCommitPayload, CodeAttachmentInput


def test_code_attachment_enforces_utf8_byte_limit_and_attachment_count() -> None:
    with pytest.raises(ValidationError):
        CodeAttachmentInput(language="python", content="界" * 11_000)

    attachment = CodeAttachmentInput(language="python", content="print('ok')")
    with pytest.raises(ValidationError):
        AnswerCommitPayload(text="answer", attachments=[attachment] * 4)

    large_attachment = CodeAttachmentInput(language="text", content="a" * 31_000)
    with pytest.raises(ValidationError):
        AnswerCommitPayload(
            text="b" * 27_000,
            attachments=[large_attachment],
        )


def test_code_attachment_is_rendered_as_non_executable_context_data() -> None:
    message = InterviewMessage(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role=MessageRole.USER,
        sequence=1,
        content="这是我的实现。",
    )
    attachment = AnswerAttachment(
        id=uuid.uuid4(),
        message_id=message.id,
        attachment_type=AttachmentType.CODE,
        language="python",
        content="print('hello')",
        size_bytes=14,
    )

    rendered = _message_text(message, {message.id: [attachment]})

    assert "这是我的实现。" in rendered
    assert "print('hello')" in rendered
    assert '"execution_allowed":false' in rendered
