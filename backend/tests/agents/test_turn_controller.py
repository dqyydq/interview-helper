import json

from app.agents.turn_controller import TURN_FOCUSES, build_turn_controller_request


def test_turn_controller_request_is_bounded_and_plan_constrained() -> None:
    request = build_turn_controller_request(
        current_question="current " * 1_000,
        current_capabilities=["architecture", "tradeoff"],
        answer="answer " * 2_000,
        follow_ups_remaining=2,
        next_question="next " * 1_000,
        next_capabilities=["reliability"],
        remaining_seconds=600,
        remaining_main_questions=1,
        unresolved_points=["point " * 200] * 8,
        max_tokens=256,
    )

    assert request.max_tokens == 256
    assert request.temperature == 0
    assert request.system
    payload = json.loads(request.messages[0].content)
    assert payload["contract"]["plan_order_is_fixed"] is True
    assert payload["contract"]["allowed_actions"] == ["follow_up", "advance", "finish"]
    assert len(payload["candidate_answer"]) == 8_000
    assert len(payload["current"]["question"]) == 2_400
    assert len(payload["unresolved_points"]) == 5
    assert set(TURN_FOCUSES)
