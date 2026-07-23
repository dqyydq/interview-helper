import uuid
from collections import Counter

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import PlannerResult, PlannerSemanticError, run_planner
from app.api.errors import AppError
from app.db.models.common import JobStatus, JobType, ModelRole, PlanStatus, ResumeParseStatus
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.interview import InterviewConfig, InterviewPlan, PlanQuestion
from app.db.models.job import BackgroundJob
from app.db.models.question import QuestionBank
from app.db.models.resume import Resume, ResumeClaim
from app.providers.base import ProviderError
from app.providers.factory import build_provider
from app.schemas.interview_plan import (
    InterviewConfigPublic,
    InterviewPlanCreate,
    InterviewPlanCreateResult,
    InterviewPlanPublic,
    PlanJobPublic,
    PlannerQuestionDraft,
    PlanQuestionPublic,
)
from app.services.model_connections import resolve_role_connection
from app.services.question_retrieval import PlanCandidate, build_candidate_pool, select_candidates
from app.services.role_matrix import RoleMatrix, load_role_matrix

logger = structlog.get_logger(__name__)


def _config_public(config: InterviewConfig) -> InterviewConfigPublic:
    return InterviewConfigPublic.model_validate(config)


def _question_public(question: PlanQuestion) -> PlanQuestionPublic:
    return PlanQuestionPublic.model_validate(question)


def job_public(job: BackgroundJob) -> PlanJobPublic:
    return PlanJobPublic.model_validate(job)


async def plan_public(
    session: AsyncSession,
    plan: InterviewPlan,
) -> InterviewPlanPublic:
    config = await session.get(InterviewConfig, plan.config_id)
    if not config:
        raise AppError(code="interview_config_not_found", message="面试配置不存在", status_code=404)
    questions = list(
        (
            await session.scalars(
                select(PlanQuestion)
                .where(PlanQuestion.plan_id == plan.id, PlanQuestion.deleted_at.is_(None))
                .order_by(PlanQuestion.sequence)
            )
        ).all()
    )
    return InterviewPlanPublic(
        id=plan.id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        version=plan.version,
        config_id=plan.config_id,
        style_pack_id=plan.style_pack_id,
        status=plan.status,
        total_minutes=plan.total_minutes,
        plan_snapshot=plan.plan_snapshot,
        rationale=plan.rationale,
        frozen_at=plan.frozen_at,
        config=_config_public(config),
        questions=[_question_public(question) for question in questions],
    )


async def get_plan(
    session: AsyncSession,
    profile_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> InterviewPlan:
    plan = await session.scalar(
        select(InterviewPlan)
        .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
        .where(
            InterviewPlan.id == plan_id,
            InterviewConfig.profile_id == profile_id,
            InterviewPlan.deleted_at.is_(None),
        )
    )
    if not plan:
        raise AppError(code="interview_plan_not_found", message="面试计划不存在", status_code=404)
    return plan


async def _validate_sources(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: InterviewPlanCreate,
) -> tuple[CompanyStylePack, RoundProfile]:
    company = await session.scalar(
        select(Company).where(
            Company.id == payload.company_id,
            Company.deleted_at.is_(None),
            (Company.profile_id.is_(None) | (Company.profile_id == profile_id)),
        )
    )
    if not company:
        raise AppError(code="company_not_found", message="公司不存在", status_code=404)
    round_row = await session.execute(
        select(RoundProfile, CompanyStylePack)
        .join(CompanyStylePack, CompanyStylePack.id == RoundProfile.style_pack_id)
        .where(
            RoundProfile.id == payload.round_profile_id,
            RoundProfile.deleted_at.is_(None),
            CompanyStylePack.company_id == company.id,
            CompanyStylePack.deleted_at.is_(None),
        )
    )
    pair = round_row.one_or_none()
    if not pair:
        raise AppError(code="round_profile_not_found", message="面试轮次不存在", status_code=404)
    round_profile, style_pack = pair
    if payload.question_bank_ids:
        rows = list(
            (
                await session.scalars(
                    select(QuestionBank.id).where(
                        QuestionBank.id.in_(payload.question_bank_ids),
                        QuestionBank.profile_id == profile_id,
                        QuestionBank.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        if len(set(rows)) != len(set(payload.question_bank_ids)):
            raise AppError(
                code="question_bank_not_found",
                message="包含不可用题库",
                status_code=404,
            )
    if payload.resume_id:
        resume = await session.scalar(
            select(Resume).where(
                Resume.id == payload.resume_id,
                Resume.profile_id == profile_id,
                Resume.deleted_at.is_(None),
            )
        )
        if not resume:
            raise AppError(code="resume_not_found", message="简历不存在", status_code=404)
        if resume.parse_status != ResumeParseStatus.READY:
            raise AppError(
                code="resume_not_ready",
                message="简历尚未解析完成",
                status_code=409,
            )
    return style_pack, round_profile


async def create_plan_job(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: InterviewPlanCreate,
) -> InterviewPlanCreateResult:
    style_pack, round_profile = await _validate_sources(session, profile_id, payload)
    config = InterviewConfig(
        profile_id=profile_id,
        company_id=payload.company_id,
        round_profile_id=payload.round_profile_id,
        resume_id=payload.resume_id,
        role_name=payload.role_name,
        duration_minutes=payload.duration_minutes,
        target_question_count=payload.target_question_count,
        question_bank_ids=[str(item) for item in payload.question_bank_ids],
        source_weights=payload.source_weights,
        preferences=payload.preferences,
    )
    session.add(config)
    await session.flush()
    plan = InterviewPlan(
        config_id=config.id,
        style_pack_id=style_pack.id,
        status=PlanStatus.DRAFT,
        total_minutes=payload.duration_minutes,
        plan_snapshot={
            "phase": "queued",
            "round_name": round_profile.name,
            "style_pack_version": style_pack.pack_version,
        },
    )
    session.add(plan)
    await session.flush()
    job = BackgroundJob(
        profile_id=profile_id,
        job_type=JobType.PLAN_GENERATION,
        status=JobStatus.QUEUED,
        progress=0,
        payload={"plan_id": str(plan.id)},
        idempotency_key=f"plan-generation:{plan.id}:v1",
        max_attempts=3,
    )
    session.add(job)
    await session.commit()
    await session.refresh(plan)
    await session.refresh(job)
    return InterviewPlanCreateResult(
        plan=await plan_public(session, plan),
        job=job_public(job),
    )


def _round_context(style_pack: CompanyStylePack, round_profile: RoundProfile) -> dict:
    """Keep planner style input bounded to the selected company round only."""

    return {
        "style_pack": {
            "name": style_pack.name,
            "version": style_pack.pack_version,
            "default_interviewer_behavior": style_pack.default_interviewer_behavior,
        },
        "round": {
            "round_key": round_profile.round_key,
            "name": round_profile.name,
            "sequence": round_profile.sequence,
            "opening_style": round_profile.opening_style,
            "topic_weights": round_profile.topic_weights,
            "follow_up_patterns": round_profile.follow_up_patterns,
            "pressure_level": round_profile.pressure_level,
            "answer_expectations": round_profile.answer_expectations,
            "evaluation_weights": round_profile.evaluation_weights,
        },
    }


async def _resume_summary(
    session: AsyncSession,
    resume_id: uuid.UUID | None,
) -> dict | None:
    """Provide a small, source-labelled resume summary to the bounded planner context."""

    if resume_id is None:
        return None
    claims = list(
        (
            await session.scalars(
                select(ResumeClaim)
                .where(
                    ResumeClaim.resume_id == resume_id,
                    ResumeClaim.deleted_at.is_(None),
                )
                .order_by(ResumeClaim.created_at, ResumeClaim.id)
                .limit(12)
            )
        ).all()
    )
    return {
        "resume_id": str(resume_id),
        "claims": [
            {
                "claim_id": str(claim.id),
                "claim_type": claim.claim_type,
                "content": claim.content[:800],
                "confidence": claim.confidence,
            }
            for claim in claims
        ],
    }


def _deterministic_questions(
    candidates: list[PlanCandidate],
    *,
    total_seconds: int,
) -> list[PlannerQuestionDraft]:
    base_seconds, remainder = divmod(total_seconds, len(candidates))
    return [
        PlannerQuestionDraft(
            candidate_key=candidate.stable_key,
            sequence=index,
            allocated_seconds=max(30, base_seconds + (1 if index <= remainder else 0)),
            follow_up_budget=candidate.follow_up_budget,
            selection_reason=candidate.selection_reason,
        )
        for index, candidate in enumerate(candidates, start=1)
    ]


async def _try_model_plan(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    candidates: list[PlanCandidate],
    style_pack: CompanyStylePack,
    round_profile: RoundProfile,
    role_name: str,
    role_matrix: RoleMatrix,
    resume_summary: dict | None,
    total_seconds: int,
) -> tuple[PlannerResult | None, str | None]:
    """Run the bound Planner role; callers retain a deterministic local fallback."""

    provider = None
    try:
        connection = await resolve_role_connection(session, profile_id, ModelRole.PLANNER)
        provider = build_provider(connection)
        result = await run_planner(
            provider,
            candidates=candidates,
            round_context=_round_context(style_pack, round_profile),
            role_name=role_name,
            role_matrix=role_matrix,
            resume_summary=resume_summary,
            duration_seconds=total_seconds,
            context_window_tokens=connection.context_window_tokens,
            max_output_tokens=connection.max_output_tokens,
            tokenizer_type=connection.tokenizer_type,
        )
        return result, None
    except (AppError, ProviderError, PlannerSemanticError, ValueError) as exc:
        reason = getattr(exc, "code", type(exc).__name__)
        logger.warning(
            "planner_fallback",
            profile_id=str(profile_id),
            fallback_reason=reason,
        )
        return None, str(reason)
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()


async def generate_plan(session: AsyncSession, plan_id: uuid.UUID) -> InterviewPlan:
    plan = await session.get(InterviewPlan, plan_id)
    if not plan:
        raise AppError(code="interview_plan_not_found", message="面试计划不存在", status_code=404)
    config = await session.get(InterviewConfig, plan.config_id)
    if not config:
        raise AppError(code="interview_config_not_found", message="面试配置不存在", status_code=404)
    style_pack = await session.get(CompanyStylePack, plan.style_pack_id)
    round_profile = await session.get(RoundProfile, config.round_profile_id)
    if not style_pack or not round_profile:
        raise AppError(
            code="plan_style_context_missing",
            message="Interview plan style context is unavailable.",
            status_code=409,
        )
    role_matrix = load_role_matrix(config.role_name)
    bank_ids = [uuid.UUID(value) for value in config.question_bank_ids]
    candidates = await build_candidate_pool(
        session,
        profile_id=config.profile_id,
        bank_ids=bank_ids,
        resume_id=config.resume_id,
        role_matrix=role_matrix,
    )
    selected = select_candidates(
        candidates,
        target_count=config.target_question_count,
        source_weights=config.source_weights,
    )
    if not selected:
        raise AppError(
            code="plan_candidate_pool_empty",
            message="没有可用于规划的题目",
            status_code=409,
        )

    await session.execute(delete(PlanQuestion).where(PlanQuestion.plan_id == plan.id))
    total_seconds = config.duration_minutes * 60
    model_result, fallback_reason = await _try_model_plan(
        session,
        profile_id=config.profile_id,
        candidates=selected,
        style_pack=style_pack,
        round_profile=round_profile,
        role_name=config.role_name,
        role_matrix=role_matrix,
        resume_summary=await _resume_summary(session, config.resume_id),
        total_seconds=total_seconds,
    )
    if model_result:
        decisions = model_result.questions
        rationale = model_result.rationale
        capability_coverage = Counter(model_result.capability_coverage)
        planner_name = "model-v1"
    else:
        decisions = _deterministic_questions(selected, total_seconds=total_seconds)
        rationale = "The local deterministic fallback ordered the preselected candidate pool."
        capability_coverage = Counter(
            tag for candidate in selected for tag in candidate.capability_tags
        )
        planner_name = "deterministic-v1"

    candidate_by_key = {candidate.stable_key: candidate for candidate in selected}
    source_distribution: Counter[str] = Counter()
    for decision in decisions:
        candidate = candidate_by_key[decision.candidate_key]
        source_distribution[candidate.source_type.value] += 1
        question_id = (
            uuid.UUID(candidate.source_ref["question_id"])
            if candidate.source_type.value == "manual"
            else None
        )
        session.add(
            PlanQuestion(
                plan_id=plan.id,
                question_id=question_id,
                sequence=decision.sequence,
                source_type=candidate.source_type,
                source_ref=candidate.source_ref,
                prompt_snapshot=candidate.prompt,
                capability_tags=list(candidate.capability_tags),
                allocated_seconds=decision.allocated_seconds,
                follow_up_budget=decision.follow_up_budget,
                selection_reason=decision.selection_reason,
            )
        )
    plan.status = PlanStatus.READY
    snapshot = {
        **plan.plan_snapshot,
        "phase": "ready",
        "planner": planner_name,
        "planner_role": ModelRole.PLANNER.value,
        "planner_schema_version": "planner.v1",
        "role_matrix": role_matrix.role_key,
        "role_matrix_schema_version": role_matrix.schema_version,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "source_distribution": dict(source_distribution),
        "capability_coverage": dict(capability_coverage),
    }
    snapshot.pop("planner_fallback_reason", None)
    if fallback_reason:
        snapshot["planner_fallback_reason"] = fallback_reason
    plan.plan_snapshot = snapshot
    plan.rationale = rationale
    plan.touch()
    await session.flush()
    return plan
