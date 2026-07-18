from app.db.models.common import SourceType
from app.services.question_retrieval import PlanCandidate, select_candidates
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
