"""Shared, fail-closed input budgeting for structured model agents.

Planner and resume-structuring calls do not have a conversational ContextSnapshot, but
they still send user-managed data to a configured provider.  This module keeps those
calls inside the same conservative token-budget contract as the interview agents.
"""

import json
from collections.abc import Callable

from app.api.errors import AppError
from app.context.token_budget import TokenBudget
from app.context.token_counter import UnifiedTokenCounter
from app.db.models.common import MessageRole
from app.providers.types import ChatMessage, ChatRequest

MAX_STRUCTURED_AGENT_OUTPUT_TOKENS = 4_096
MIN_STRUCTURED_AGENT_OUTPUT_TOKENS = 128


class AgentInputBudgetError(ValueError):
    """A complete source payload cannot safely fit the configured model window."""

    def __init__(
        self,
        *,
        agent_name: str,
        input_tokens: int,
        input_budget_tokens: int,
    ) -> None:
        self.code = f"{agent_name}_context_budget_exceeded"
        self.input_tokens = input_tokens
        self.input_budget_tokens = input_budget_tokens
        super().__init__(
            f"{agent_name} input requires {input_tokens} tokens, exceeding the "
            f"configured budget of {input_budget_tokens} tokens"
        )


def structured_agent_output_tokens(
    *,
    context_window_tokens: int,
    max_output_tokens: int,
) -> int:
    """Reserve an output allowance that respects both the connection and its window.

    The cap keeps a generous structured-output ceiling on large models while retaining
    at least roughly two thirds of a small context window for source data and protocol
    overhead.  TokenBudget remains the final authority on whether the full payload fits.
    """

    return min(
        max_output_tokens,
        MAX_STRUCTURED_AGENT_OUTPUT_TOKENS,
        max(MIN_STRUCTURED_AGENT_OUTPUT_TOKENS, context_window_tokens // 3),
    )


RequestBudgetValidator = Callable[[ChatRequest], None]


def structured_request_budget_validator(
    *,
    agent_name: str,
    context_window_tokens: int,
    max_output_tokens: int,
    tokenizer_type: str,
) -> tuple[int, RequestBudgetValidator]:
    """Build a validator for every provider attempt in a structured-agent call.

    A schema-repair retry includes the previous model response; a Planner semantic-repair
    retry does too.  The validator therefore receives the *current* ChatRequest and must
    run immediately before every provider call, not only when the original source payload
    is assembled.  Callers fail closed rather than truncating source or model output.
    """

    reserved_output_tokens = structured_agent_output_tokens(
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
    )

    def validate(request: ChatRequest) -> None:
        requested_output_tokens = request.max_tokens
        if requested_output_tokens > reserved_output_tokens:
            raise AgentInputBudgetError(
                agent_name=agent_name,
                input_tokens=0,
                input_budget_tokens=0,
            )
        try:
            budget = TokenBudget.create(
                context_window_tokens=context_window_tokens,
                reserved_output_tokens=requested_output_tokens,
            )
        except (AppError, ValueError) as exc:
            # Keep the public/fallback reason content-free and agent-specific.  The original
            # exception is intentionally retained only as a causal chain for local debugging.
            raise AgentInputBudgetError(
                agent_name=agent_name,
                input_tokens=0,
                input_budget_tokens=0,
            ) from exc

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=request.system or ""),
            *request.messages,
        ]
        if request.response_schema is not None:
            messages.append(
                ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=json.dumps(request.response_schema, ensure_ascii=False, sort_keys=True),
                )
            )
        input_tokens = UnifiedTokenCounter(tokenizer_type).count_messages(messages).tokens
        if input_tokens > budget.effective_input_tokens:
            raise AgentInputBudgetError(
                agent_name=agent_name,
                input_tokens=input_tokens,
                input_budget_tokens=budget.effective_input_tokens,
            )

    return reserved_output_tokens, validate
