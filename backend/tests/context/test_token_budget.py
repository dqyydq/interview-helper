import math

import pytest

from app.api.errors import AppError
from app.context.token_budget import ContextLayer, TokenBudget
from app.context.token_counter import UnifiedTokenCounter
from app.db.models.common import MessageRole
from app.providers.types import ChatMessage


def test_unknown_tokenizer_uses_conservative_nonzero_count() -> None:
    counter = UnifiedTokenCounter("unknown")

    text = counter.count_text("上下文 context")
    messages = counter.count_messages(
        [ChatMessage(role=MessageRole.USER, content="这是候选人的回答")]
    )

    assert text.tokens > 0
    assert messages.tokens > text.tokens
    assert text.safety_margin >= 1.15
    assert text.method == "conservative_estimate:unknown"


def test_budget_has_stable_pressure_thresholds_and_layer_targets() -> None:
    budget = TokenBudget.create(
        context_window_tokens=16_000,
        reserved_output_tokens=512,
        protocol_overhead_tokens=256,
    )
    effective = budget.effective_input_tokens

    assert budget.compaction_level(math.ceil(effective * 0.59)) == 0
    assert budget.compaction_level(math.ceil(effective * 0.60)) == 1
    assert budget.compaction_level(math.ceil(effective * 0.75)) == 2
    assert budget.compaction_level(math.ceil(effective * 0.85)) == 3
    assert budget.compaction_level(math.ceil(effective * 0.95)) == 4
    assert budget.layer_target(ContextLayer.RECENT) > budget.layer_target(ContextLayer.SYSTEM)


def test_essential_context_is_never_silently_truncated() -> None:
    budget = TokenBudget.create(
        context_window_tokens=1_024,
        reserved_output_tokens=128,
        protocol_overhead_tokens=256,
    )

    with pytest.raises(AppError, match="当前问题") as error:
        budget.ensure_essential_fits(budget.effective_input_tokens + 1)

    assert error.value.code == "context_budget_exceeded"
