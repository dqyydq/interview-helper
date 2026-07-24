import uuid

from app.schemas.interview_plan import InterviewPlanCreate


def _required_fields() -> dict:
    return {
        "company_id": uuid.uuid4(),
        "round_profile_id": uuid.uuid4(),
    }


def test_plan_source_weight_defaults_include_link_import() -> None:
    payload = InterviewPlanCreate(**_required_fields())

    assert payload.source_weights == {
        "manual": 0.4,
        "link_import": 0.1,
        "resume": 0.25,
        "generated": 0.25,
    }


def test_plan_source_weights_accept_link_import_and_legacy_explicit_values() -> None:
    link_import_payload = InterviewPlanCreate(
        **_required_fields(),
        source_weights={"link_import": 1.0},
    )
    legacy_weights = {"manual": 1.0, "resume": 0.0, "generated": 0.0}
    legacy_payload = InterviewPlanCreate(
        **_required_fields(),
        source_weights=legacy_weights,
    )

    assert link_import_payload.source_weights == {"link_import": 1.0}
    assert legacy_payload.source_weights == legacy_weights
