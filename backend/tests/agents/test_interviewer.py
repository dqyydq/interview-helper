from app.agents.interviewer import build_interviewer_request
from app.db.models.common import MessageRole
from app.providers.types import ChatMessage


def test_interviewer_prompt_excludes_reference_answers_and_scores() -> None:
    request = build_interviewer_request(
        current_question="如何设计长对话上下文管理？",
        messages=[
            ChatMessage(role=MessageRole.ASSISTANT, content="请介绍你的方案。"),
            ChatMessage(role=MessageRole.USER, content="我会分层压缩并保留证据引用。"),
        ],
        max_tokens=4096,
    )

    assert request.max_tokens == 512
    assert "不要评分" in (request.system or "")
    assert "参考答案" not in " ".join(message.content for message in request.messages)
    assert request.messages[-1].role == MessageRole.USER
