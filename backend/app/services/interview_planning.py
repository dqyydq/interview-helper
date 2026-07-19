import uuid
from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import JobStatus, JobType, PlanStatus, ResumeParseStatus
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.interview import InterviewConfig, InterviewPlan, PlanQuestion
from app.db.models.job import BackgroundJob
from app.db.models.question import QuestionBank
from app.db.models.resume import Resume
from app.schemas.interview_plan import (
    InterviewConfigPublic,
    InterviewPlanCreate,
    InterviewPlanCreateResult,
    InterviewPlanPublic,
    PlanJobPublic,
    PlanQuestionPublic,
)
from app.services.question_retrieval import build_candidate_pool, select_candidates
from app.services.role_matrix import load_role_matrix


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


async def generate_plan(session: AsyncSession, plan_id: uuid.UUID) -> InterviewPlan:
    plan = await session.get(InterviewPlan, plan_id)
    if not plan:
        raise AppError(code="interview_plan_not_found", message="面试计划不存在", status_code=404)
    config = await session.get(InterviewConfig, plan.config_id)
    if not config:
        raise AppError(code="interview_config_not_found", message="面试配置不存在", status_code=404)
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
    base_seconds, remainder = divmod(total_seconds, len(selected))
    source_distribution: Counter[str] = Counter()
    capability_coverage: Counter[str] = Counter()
    for index, candidate in enumerate(selected, start=1):
        allocated = base_seconds + (1 if index <= remainder else 0)
        source_distribution[candidate.source_type.value] += 1
        capability_coverage.update(candidate.capability_tags)
        question_id = (
            uuid.UUID(candidate.source_ref["question_id"])
            if candidate.source_type.value == "manual"
            else None
        )
        session.add(
            PlanQuestion(
                plan_id=plan.id,
                question_id=question_id,
                sequence=index,
                source_type=candidate.source_type,
                source_ref=candidate.source_ref,
                prompt_snapshot=candidate.prompt,
                capability_tags=list(candidate.capability_tags),
                allocated_seconds=max(30, allocated),
                follow_up_budget=candidate.follow_up_budget,
                selection_reason=candidate.selection_reason,
            )
        )
    plan.status = PlanStatus.READY
    plan.plan_snapshot = {
        **plan.plan_snapshot,
        "phase": "ready",
        "planner": "deterministic-v1",
        "role_matrix": role_matrix.role_key,
        "role_matrix_schema_version": role_matrix.schema_version,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "source_distribution": dict(source_distribution),
        "capability_coverage": dict(capability_coverage),
    }
    plan.rationale = "先按用户指定来源比例分配题量，再按近期使用次数和稳定来源键确定顺序。"
    plan.touch()
    await session.flush()
    return plan
