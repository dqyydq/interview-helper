import json
import uuid

from app.db.models.common import MessageRole
from app.providers.base import ChatProvider, StructuredOutputRunner
from app.providers.types import ChatMessage, ChatRequest
from app.schemas.evaluation import CoachResponse

SYSTEM_PROMPT = """你是面试复盘教练。只使用输入中的评估报告和必要的原始回答片段。
不要访问或假设简历、长期记忆、其他会话或未提供的消息。
解释必须引用可用 source_message_ids。rewrite 模式必须明确区分 original_answer 与 suggested_answer，
建议答案不能伪装成用户说过的话。practice 模式给出具体练习题，不给 Offer 概率。
不要输出内部思维过程，只返回符合 JSON Schema 的结果。"""


async def run_coach(
    provider: ChatProvider,
    *,
    mode: str,
    report_context: dict,
    allowed_message_ids: set[uuid.UUID],
) -> CoachResponse:
    request = ChatRequest(
        system=SYSTEM_PROMPT,
        messages=[
            ChatMessage(
                role=MessageRole.USER,
                content=json.dumps(report_context, ensure_ascii=False, default=str),
            )
        ],
        temperature=0.2,
        max_tokens=2_048,
    )
    result = await StructuredOutputRunner(provider, max_repairs=1).run(
        request, CoachResponse
    )
    if result.mode != mode:
        raise ValueError("coach response mode does not match the request")
    if set(result.source_message_ids) - allowed_message_ids:
        raise ValueError("coach response cites a message outside the supplied report evidence")
    if mode == "rewrite" and not result.suggested_answer:
        raise ValueError("rewrite response must include suggested_answer")
    return result
