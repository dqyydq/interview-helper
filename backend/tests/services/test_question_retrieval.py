import uuid

from app.db.models.common import SourceType
from app.db.models.question import Question
from app.services.question_retrieval import (
    PlanCandidate,
    canonical_round_key,
    question_target_match_rank,
    select_candidates,
)
from app.services.role_matrix import load_role_matrix


def test_role_matrix_has_weighted_capabilities_and_templates() -> None:
    matrix = load_role_matrix("llm_application_engineer")

    assert matrix.role_key == "llm_application_engineer"
    assert len(matrix.capabilities) >= 5
    assert len(matrix.scenario_templates) >= 5
    assert all(template.capability for template in matrix.scenario_templates)


def test_candidate_selection_is_deterministic_and_respects_source_mix() -> None:
    candidates = [
        PlanCandidate(
            stable_key=f"manual:{index}",
            prompt=f"manual {index}",
            source_type=SourceType.MANUAL,
            source_ref={},
            recent_use_count=index,
        )
        for index in range(4)
    ]
    candidates.extend(
        PlanCandidate(
            stable_key=f"resume:{index}",
            prompt=f"resume {index}",
            source_type=SourceType.RESUME,
            source_ref={},
        )
        for index in range(3)
    )
    candidates.extend(
        PlanCandidate(
            stable_key=f"generated:{index}",
            prompt=f"generated {index}",
            source_type=SourceType.GENERATED,
            source_ref={},
        )
        for index in range(3)
    )

    weights = {"manual": 0.4, "resume": 0.3, "generated": 0.3}
    first = select_candidates(candidates, target_count=6, source_weights=weights)
    second = select_candidates(candidates, target_count=6, source_weights=weights)

    assert [item.stable_key for item in first] == [item.stable_key for item in second]
    assert len({item.stable_key for item in first}) == 6
    assert {item.source_type for item in first} == {
        SourceType.MANUAL,
        SourceType.RESUME,
        SourceType.GENERATED,
    }


def test_link_import_candidates_respect_target_applicability_rank() -> None:
    candidates = [
        PlanCandidate(
            stable_key="import:mismatch",
            prompt="Mismatched target",
            source_type=SourceType.LINK_IMPORT,
            source_ref={"question_id": str(uuid.uuid4())},
            target_match_rank=3,
        ),
        PlanCandidate(
            stable_key="import:generic",
            prompt="Generic target",
            source_type=SourceType.LINK_IMPORT,
            source_ref={"question_id": str(uuid.uuid4())},
            target_match_rank=2,
        ),
        PlanCandidate(
            stable_key="import:company",
            prompt="Company target",
            source_type=SourceType.LINK_IMPORT,
            source_ref={"question_id": str(uuid.uuid4())},
            target_match_rank=1,
        ),
        PlanCandidate(
            stable_key="import:exact",
            prompt="Exact target",
            source_type=SourceType.LINK_IMPORT,
            source_ref={"question_id": str(uuid.uuid4())},
            target_match_rank=0,
        ),
    ]

    selected = select_candidates(
        candidates,
        target_count=4,
        source_weights={"link_import": 1.0},
    )

    assert [item.stable_key for item in selected] == [
        "import:exact",
        "import:company",
        "import:generic",
        "import:mismatch",
    ]


def test_question_target_match_rank_uses_canonical_company_and_round_keys() -> None:
    target_company = "ByteDance"
    target_round = "Round_2"

    def question(*, companies: list[str], rounds: list[str]) -> Question:
        return Question(
            bank_id=uuid.uuid4(),
            prompt="How would you verify an LLM release?",
            normalized_hash="a" * 64,
            applicable_companies=companies,
            applicable_rounds=rounds,
        )

    assert canonical_round_key(target_company, target_round) == "bytedance:round_2"
    assert question_target_match_rank(
        question(companies=["bytedance"], rounds=["bytedance:round_2"]),
        company_slug=target_company,
        round_key=target_round,
    ) == 0
    assert question_target_match_rank(
        question(companies=["bytedance"], rounds=[]),
        company_slug=target_company,
        round_key=target_round,
    ) == 1
    assert question_target_match_rank(
        question(companies=[], rounds=[]),
        company_slug=target_company,
        round_key=target_round,
    ) == 2
    assert question_target_match_rank(
        question(companies=["alibaba"], rounds=["alibaba:round_2"]),
        company_slug=target_company,
        round_key=target_round,
    ) == 3
