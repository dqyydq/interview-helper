import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.common import QuestionStatus, SourceType
from app.db.models.question import Question, QuestionBank, QuestionTag, QuestionTagLink
from app.db.models.resume import ResumeClaim
from app.services.role_matrix import RoleMatrix

_PLANNABLE_SOURCE_ORDER = (
    SourceType.MANUAL,
    SourceType.LINK_IMPORT,
    SourceType.RESUME,
    SourceType.GENERATED,
)
_SOURCE_ORDER_INDEX = {source: index for index, source in enumerate(_PLANNABLE_SOURCE_ORDER)}


@dataclass(frozen=True, slots=True)
class PlanCandidate:
    stable_key: str
    prompt: str
    source_type: SourceType
    source_ref: dict
    capability_tags: tuple[str, ...] = field(default_factory=tuple)
    follow_up_budget: int = 2
    selection_reason: str = ""
    recent_use_count: int = 0
    target_match_rank: int = 0


def canonical_round_key(company_slug: str, round_key: str) -> str:
    """Build the canonical company/round applicability key."""

    return f"{company_slug.strip().casefold()}:{round_key.strip().casefold()}"


def _applicability_values(values: list) -> set[str]:
    return {
        value.strip().casefold() for value in values if isinstance(value, str) and value.strip()
    }


def question_target_match_rank(
    question: Question,
    *,
    company_slug: str | None,
    round_key: str | None,
) -> int:
    """Return a deterministic applicability rank for a concrete plan target.

    Lower ranks are preferred. Explicit mismatches stay eligible as a fallback so a
    selected question bank never loses material silently because of metadata.
    """

    if not company_slug or not round_key:
        return 0

    company_key = company_slug.strip().casefold()
    target_round_key = canonical_round_key(company_slug, round_key)
    applicable_companies = _applicability_values(question.applicable_companies)
    applicable_rounds = _applicability_values(question.applicable_rounds)
    company_matches = company_key in applicable_companies
    round_matches = target_round_key in applicable_rounds

    # Exact canonical round match is strongest unless an optional company scope
    # explicitly contradicts it.
    if round_matches and (not applicable_companies or company_matches):
        return 0
    # Company-specific questions without a round restriction are broader but still
    # stronger than untagged generic questions.
    if company_matches and not applicable_rounds:
        return 1
    if not applicable_companies and not applicable_rounds:
        return 2
    # Different company or explicitly different round: retain, but demote.
    return 3


def _candidate_sort_key(candidate: PlanCandidate) -> tuple[int, int, int, str]:
    return (
        candidate.target_match_rank,
        candidate.recent_use_count,
        _SOURCE_ORDER_INDEX.get(candidate.source_type, len(_PLANNABLE_SOURCE_ORDER)),
        candidate.stable_key,
    )


async def _question_tags(
    session: AsyncSession,
    question_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[str]]:
    if not question_ids:
        return {}
    rows = await session.execute(
        select(QuestionTagLink.question_id, QuestionTag.slug)
        .join(QuestionTag, QuestionTag.id == QuestionTagLink.tag_id)
        .where(QuestionTagLink.question_id.in_(question_ids))
        .order_by(QuestionTag.slug)
    )
    result: dict[uuid.UUID, list[str]] = {}
    for question_id, slug in rows.all():
        result.setdefault(question_id, []).append(slug)
    return result


async def build_candidate_pool(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    bank_ids: list[uuid.UUID],
    resume_id: uuid.UUID | None,
    role_matrix: RoleMatrix,
    company_slug: str | None = None,
    round_key: str | None = None,
) -> list[PlanCandidate]:
    question_statement = (
        select(Question)
        .join(QuestionBank, QuestionBank.id == Question.bank_id)
        .where(
            QuestionBank.profile_id == profile_id,
            QuestionBank.deleted_at.is_(None),
            Question.deleted_at.is_(None),
            Question.status == QuestionStatus.ACTIVE,
        )
        .order_by(Question.times_used, Question.created_at, Question.id)
    )
    if bank_ids:
        question_statement = question_statement.where(Question.bank_id.in_(bank_ids))
    else:
        question_statement = question_statement.where(Question.id.is_(None))
    questions = list((await session.scalars(question_statement)).all())
    questions.sort(
        key=lambda question: (
            question_target_match_rank(
                question,
                company_slug=company_slug,
                round_key=round_key,
            ),
            question.times_used,
            question.created_at,
            question.id,
        )
    )
    tags = await _question_tags(session, [question.id for question in questions])
    candidates = [
        PlanCandidate(
            stable_key=f"question:{question.id}",
            prompt=question.prompt,
            source_type=SourceType(question.source_type),
            source_ref={"question_id": str(question.id), "bank_id": str(question.bank_id)},
            capability_tags=tuple(tags.get(question.id, [])),
            follow_up_budget=min(3, max(1, len(question.follow_up_suggestions))),
            selection_reason="来自本场选定题库，按近期使用次数降权排序",
            recent_use_count=question.times_used,
            target_match_rank=question_target_match_rank(
                question,
                company_slug=company_slug,
                round_key=round_key,
            ),
        )
        for question in questions
    ]

    if resume_id:
        claims = list(
            (
                await session.scalars(
                    select(ResumeClaim)
                    .where(
                        ResumeClaim.resume_id == resume_id,
                        ResumeClaim.deleted_at.is_(None),
                    )
                    .order_by(ResumeClaim.created_at, ResumeClaim.id)
                    .limit(20)
                )
            ).all()
        )
        for claim in claims:
            if claim.claim_type == "project":
                prompt = (
                    f"请围绕简历中的这段项目经历展开说明：{claim.content}。"
                    "重点说明目标、方案、你的贡献和技术取舍。"
                )
                capability = "delivery"
            else:
                prompt = (
                    f"你的简历提到了“{claim.content}”。请说明实际使用场景、核心原理和一个具体问题。"
                )
                capability = "llm_fundamentals"
            candidates.append(
                PlanCandidate(
                    stable_key=f"resume-claim:{claim.id}",
                    prompt=prompt,
                    source_type=SourceType.RESUME,
                    source_ref={"resume_claim_id": str(claim.id), "resume_id": str(resume_id)},
                    capability_tags=(capability,),
                    follow_up_budget=2,
                    selection_reason="基于已解析简历中的显式 claim 生成专项追问",
                )
            )

    candidates.extend(
        PlanCandidate(
            stable_key=f"template:{template.key}",
            prompt=template.prompt,
            source_type=SourceType.GENERATED,
            source_ref={
                "template_key": template.key,
                "role_matrix": role_matrix.role_key,
                "schema_version": role_matrix.schema_version,
            },
            capability_tags=(template.capability,),
            follow_up_budget=2,
            selection_reason="来自岗位能力矩阵的内置场景模板",
        )
        for template in role_matrix.scenario_templates
    )
    return candidates


def select_candidates(
    candidates: list[PlanCandidate],
    *,
    target_count: int,
    source_weights: dict[str, float],
) -> list[PlanCandidate]:
    if target_count <= 0:
        return []
    source_order = _PLANNABLE_SOURCE_ORDER
    grouped = {
        source: sorted(
            (candidate for candidate in candidates if candidate.source_type == source),
            key=_candidate_sort_key,
        )
        for source in source_order
    }
    positive_total = sum(max(0.0, source_weights.get(source.value, 0.0)) for source in source_order)
    if positive_total <= 0:
        return sorted(candidates, key=_candidate_sort_key)[:target_count]
    quotas = {
        source: int(target_count * max(0.0, source_weights.get(source.value, 0.0)) / positive_total)
        for source in source_order
    }
    while sum(quotas.values()) < target_count:
        source = max(
            source_order,
            key=lambda item: (
                source_weights.get(item.value, 0.0) - quotas[item] / max(target_count, 1),
                -source_order.index(item),
            ),
        )
        quotas[source] += 1

    selected: list[PlanCandidate] = []
    used_keys: set[str] = set()
    for source in source_order:
        for candidate in grouped[source][: quotas[source]]:
            if candidate.stable_key not in used_keys:
                selected.append(candidate)
                used_keys.add(candidate.stable_key)
    remaining = sorted(
        (candidate for candidate in candidates if candidate.stable_key not in used_keys),
        key=_candidate_sort_key,
    )
    selected.extend(remaining[: max(0, target_count - len(selected))])
    return selected[:target_count]
