import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.common import QuestionStatus, SourceType
from app.db.models.question import Question, QuestionBank, QuestionTag, QuestionTagLink
from app.db.models.resume import ResumeClaim
from app.services.role_matrix import RoleMatrix


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
    tags = await _question_tags(session, [question.id for question in questions])
    candidates = [
        PlanCandidate(
            stable_key=f"question:{question.id}",
            prompt=question.prompt,
            source_type=SourceType.MANUAL,
            source_ref={"question_id": str(question.id), "bank_id": str(question.bank_id)},
            capability_tags=tuple(tags.get(question.id, [])),
            follow_up_budget=min(3, max(1, len(question.follow_up_suggestions))),
            selection_reason="来自本场选定题库，按近期使用次数降权排序",
            recent_use_count=question.times_used,
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
                    f"你的简历提到了“{claim.content}”。"
                    "请说明实际使用场景、核心原理和一个具体问题。"
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
    source_order = [SourceType.MANUAL, SourceType.RESUME, SourceType.GENERATED]
    grouped = {
        source: sorted(
            (candidate for candidate in candidates if candidate.source_type == source),
            key=lambda item: (item.recent_use_count, item.stable_key),
        )
        for source in source_order
    }
    positive_total = sum(max(0.0, source_weights.get(source.value, 0.0)) for source in source_order)
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
        key=lambda item: (
            item.recent_use_count,
            source_order.index(item.source_type),
            item.stable_key,
        ),
    )
    selected.extend(remaining[: max(0, target_count - len(selected))])
    return selected[:target_count]
