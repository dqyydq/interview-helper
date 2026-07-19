import math
from dataclasses import dataclass
from enum import StrEnum

from app.api.errors import AppError


class ContextLayer(StrEnum):
    SYSTEM = "system"
    STATE = "state"
    RECENT = "recent"
    SUMMARIES = "summaries"
    RETRIEVAL = "retrieval"
    OPTIONAL = "optional"


LAYER_TARGETS: dict[ContextLayer, float] = {
    ContextLayer.SYSTEM: 0.10,
    ContextLayer.STATE: 0.15,
    ContextLayer.RECENT: 0.35,
    ContextLayer.SUMMARIES: 0.15,
    ContextLayer.RETRIEVAL: 0.15,
    ContextLayer.OPTIONAL: 0.10,
}


@dataclass(frozen=True, slots=True)
class TokenBudget:
    context_window_tokens: int
    reserved_output_tokens: int
    protocol_overhead_tokens: int
    safety_margin_tokens: int
    effective_input_tokens: int

    @classmethod
    def create(
        cls,
        *,
        context_window_tokens: int,
        reserved_output_tokens: int,
        safety_margin: float = 0.15,
        protocol_overhead_tokens: int | None = None,
    ) -> "TokenBudget":
        if context_window_tokens < 1_024:
            raise ValueError("context_window_tokens must be at least 1024")
        if not 0.15 <= safety_margin <= 0.5:
            raise ValueError("safety_margin must be between 0.15 and 0.5")
        overhead = protocol_overhead_tokens or max(256, math.ceil(context_window_tokens * 0.02))
        available = context_window_tokens - reserved_output_tokens - overhead
        margin_tokens = math.ceil(max(0, available) * safety_margin)
        effective = available - margin_tokens
        if effective <= 0:
            raise AppError(
                code="context_window_too_small",
                message="模型上下文窗口不足以容纳安全提示与输出预留",
                status_code=409,
            )
        return cls(
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
            protocol_overhead_tokens=overhead,
            safety_margin_tokens=margin_tokens,
            effective_input_tokens=effective,
        )

    def layer_target(self, layer: ContextLayer) -> int:
        return math.floor(self.effective_input_tokens * LAYER_TARGETS[layer])

    def pressure(self, tokens: int) -> float:
        return tokens / self.effective_input_tokens

    def compaction_level(self, tokens: int) -> int:
        pressure = self.pressure(tokens)
        if pressure >= 0.95:
            return 4
        if pressure >= 0.85:
            return 3
        if pressure >= 0.75:
            return 2
        if pressure >= 0.60:
            return 1
        return 0

    def ensure_essential_fits(self, tokens: int) -> None:
        if tokens > self.effective_input_tokens:
            raise AppError(
                code="context_budget_exceeded",
                message="当前问题与最近完整回答已超过模型安全上下文，请切换更大窗口模型",
                status_code=409,
                retryable=False,
            )
