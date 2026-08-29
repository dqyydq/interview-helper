import json
import math
from dataclasses import dataclass

from app.providers.types import ChatMessage


@dataclass(frozen=True, slots=True)
class CountResult:
    tokens: int
    method: str
    safety_margin: float


class UnifiedTokenCounter:
    """Provider-neutral counter that never reports an unknown input as zero."""

    def __init__(self, tokenizer_type: str = "estimated", safety_margin: float = 1.2) -> None:
        if safety_margin < 1.15:
            raise ValueError("safety_margin must be at least 1.15")
        self.tokenizer_type = tokenizer_type.strip() or "estimated"
        self.safety_margin = safety_margin
        self.method = f"conservative_estimate:{self.tokenizer_type}"

    def count_text(self, text: str) -> CountResult:
        if not text:
            return CountResult(tokens=0, method=self.method, safety_margin=self.safety_margin)
        ascii_count = sum(character.isascii() for character in text)
        non_ascii_count = len(text) - ascii_count
        raw_estimate = math.ceil(ascii_count / 4) + math.ceil(non_ascii_count / 1.5)
        return CountResult(
            tokens=max(1, math.ceil(raw_estimate * self.safety_margin)),
            method=self.method,
            safety_margin=self.safety_margin,
        )

    def count_messages(self, messages: list[ChatMessage]) -> CountResult:
        content_tokens = sum(self.count_text(message.content).tokens for message in messages)
        image_tokens = sum(
            image.estimated_input_tokens for message in messages for image in message.images
        )
        tool_tokens = sum(
            self.count_text(json.dumps(call.model_dump(), ensure_ascii=False)).tokens
            for message in messages
            for call in message.tool_calls
        )
        return CountResult(
            tokens=content_tokens + image_tokens + tool_tokens + (len(messages) * 6),
            method=self.method,
            safety_margin=self.safety_margin,
        )
