import base64
import json
from collections.abc import AsyncIterator

import pytest

from app.agents.visual_evidence import (
    VisualEvidenceExtractionError,
    VisualEvidenceRound,
    extract_visual_evidence,
)
from app.providers.base import ChatProvider
from app.providers.types import (
    ChatImage,
    ChatRequest,
    ChatResponse,
    ImageMediaType,
    ProviderHealth,
    ProviderHealthStatus,
    StreamEvent,
)


class FakeProvider(ChatProvider):
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request.model_copy(deep=True))
        return ChatResponse(content=json.dumps(self.payloads.pop(0), ensure_ascii=False))

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="completed")  # pragma: no cover

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)


def image() -> ChatImage:
    return ChatImage(
        source_type="base64",
        media_type=ImageMediaType.PNG,
        data=base64.b64encode(b"tiny-safe-image").decode("ascii"),
    )


def rounds() -> list[VisualEvidenceRound]:
    return [
        VisualEvidenceRound(round_key="round_1", name="技术一面"),
        VisualEvidenceRound(round_key="round_2", name="综合面"),
    ]


@pytest.mark.asyncio
async def test_visual_evidence_uses_one_image_and_returns_only_allowed_paraphrased_claims() -> None:
    provider = FakeProvider(
        [
            {
                "candidates": [
                    {
                        "field_path": "rounds.round_1.follow_up_patterns",
                        "excerpt": "更常围绕项目取舍、验证方式和失败复盘继续追问。",
                        "confidence": 0.76,
                    }
                ],
                "needs_manual_review": False,
            }
        ]
    )

    result = await extract_visual_evidence(
        provider,
        image=image(),
        company_name="示例公司",
        rounds=rounds(),
        context_window_tokens=32_768,
        max_output_tokens=4_096,
        tokenizer_type="estimated",
    )

    assert result.candidates[0].field_path == "rounds.round_1.follow_up_patterns"
    assert result.candidates[0].confidence == 0.76
    assert result.warning_codes == ("image_not_retained",)
    request = provider.requests[0]
    assert request.response_schema is not None
    assert len(request.messages[0].images) == 1
    assert "untrusted data" in (request.system or "")
    payload = json.loads(request.messages[0].content)
    assert payload["company_name"] == "示例公司"
    assert "rounds.round_2.answer_expectations" in payload["allowed_field_paths"]


@pytest.mark.asyncio
async def test_visual_evidence_rejects_unknown_profile_field_paths() -> None:
    provider = FakeProvider(
        [
            {
                "candidates": [
                    {
                        "field_path": "rounds.other_round.follow_up_patterns",
                        "excerpt": "项目追问较多。",
                        "confidence": 0.6,
                    }
                ],
                "needs_manual_review": False,
            }
        ]
    )

    with pytest.raises(VisualEvidenceExtractionError, match="unavailable field path"):
        await extract_visual_evidence(
            provider,
            image=image(),
            company_name="示例公司",
            rounds=rounds(),
            context_window_tokens=32_768,
            max_output_tokens=4_096,
            tokenizer_type="estimated",
        )


@pytest.mark.asyncio
async def test_visual_evidence_omits_contact_details_and_keeps_the_image_ephemeral() -> None:
    provider = FakeProvider(
        [
            {
                "candidates": [
                    {
                        "field_path": "default_interviewer_behavior",
                        "excerpt": "联系 13800138000 了解更多细节。",
                        "confidence": 0.9,
                    },
                    {
                        "field_path": "rounds.round_2.opening_style",
                        "excerpt": "沟通会先确认候选人的项目边界和职责范围。",
                        "confidence": 0.7,
                    },
                ],
                "needs_manual_review": True,
            }
        ]
    )

    result = await extract_visual_evidence(
        provider,
        image=image(),
        company_name="示例公司",
        rounds=rounds(),
        context_window_tokens=32_768,
        max_output_tokens=4_096,
        tokenizer_type="estimated",
    )

    assert [item.excerpt for item in result.candidates] == [
        "沟通会先确认候选人的项目边界和职责范围。"
    ]
    assert result.warning_codes == (
        "image_not_retained",
        "manual_review_recommended",
        "sensitive_contact_omitted",
    )
