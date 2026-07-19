import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.evaluator import run_evaluator
from app.api.errors import AppError
from app.db.models.common import (
    EvaluationStatus,
    JobStatus,
    JobType,
    MessageRole,
    ModelRole,
    PlanStatus,
    SessionStatus,
)
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.evaluation import (
    DimensionEvaluation,
    EvaluationReport,
    QuestionEvaluation,
)
from app.db.models.interview import (
    InterviewConfig,
    InterviewMessage,
    InterviewPlan,
    InterviewSession,
    PlanQuestion,
)
from app.db.models.job import BackgroundJob
from app.providers.base import ChatProvider
from app.providers.factory import build_provider
from app.schemas.evaluation import (
    DimensionEvaluationPublic,
    EvaluationDraft,
    EvaluationJobPublic,
    EvaluationReportPublic,
    EvidenceMessagePublic,
    EvidenceReference,
    PracticeAction,
    QuestionEvaluationPublic,
    ReportListItem,
)
from app.services.model_connections import resolve_role_connection

BASE_DIMENSIONS = ["technical_depth", "problem_solving", "communication"]


async def get_report(
    session: AsyncSession,
    profile_id: uuid.UUID,
    report_id: uuid.UUID,
) -> EvaluationReport:
    report = await session.scalar(
        select(EvaluationReport)
        .join(InterviewSession, InterviewSession.id == EvaluationReport.session_id)
        .where(
            EvaluationReport.id == report_id,
            InterviewSession.profile_id == profile_id,
            EvaluationReport.deleted_at.is_(None),
        )
    )
    if not report:
        raise AppError(
            code="evaluation_report_not_found",
            message="评估报告不存在",
            status_code=404,
        )
    return report


async def get_report_for_session(
    session: AsyncSession,
    profile_id: uuid.UUID,
    session_id: uuid.UUID,
) -> EvaluationReport:
    report = await session.scalar(
        select(EvaluationReport)
        .join(InterviewSession, InterviewSession.id == EvaluationReport.session_id)
        .where(
            EvaluationReport.session_id == session_id,
            InterviewSession.profile_id == profile_id,
            EvaluationReport.deleted_at.is_(None),
        )
    )
    if not report:
        raise AppError(
            code="evaluation_report_not_found",
            message="评估报告尚未生成",
            status_code=404,
        )
    return report


async def ensure_report(session: AsyncSession, interview: InterviewSession) -> EvaluationReport:
    report = await session.scalar(
        select(EvaluationReport).where(EvaluationReport.session_id == interview.id)
    )
    if report:
        return report
    report = EvaluationReport(session_id=interview.id)
    session.add(report)
    await session.flush()
    return report


async def _load_evaluation_input(
    session: AsyncSession,
    interview: InterviewSession,
) -> tuple[
    InterviewPlan,
    InterviewConfig,
    CompanyStylePack,
    RoundProfile,
    list[PlanQuestion],
    list[InterviewMessage],
]:
    plan = await session.get(InterviewPlan, interview.plan_id)
    if not plan or plan.status != PlanStatus.FROZEN or not plan.frozen_at:
        raise AppError(
            code="evaluation_plan_not_frozen",
            message="只有冻结后的面试计划可以评估",
            status_code=409,
        )
    config = await session.get(InterviewConfig, plan.config_id)
    style_pack = await session.get(CompanyStylePack, plan.style_pack_id)
    if not config or not style_pack:
        raise AppError(
            code="evaluation_context_missing",
            message="评估上下文不完整",
            status_code=409,
        )
    round_profile = await session.get(RoundProfile, config.round_profile_id)
    if not round_profile:
        raise AppError(code="evaluation_round_missing", message="面试轮次不存在", status_code=409)
    questions = list(
        (
            await session.scalars(
                select(PlanQuestion)
                .where(PlanQuestion.plan_id == plan.id, PlanQuestion.deleted_at.is_(None))
                .order_by(PlanQuestion.sequence)
            )
        ).all()
    )
    messages = list(
        (
            await session.scalars(
                select(InterviewMessage)
                .where(
                    InterviewMessage.session_id == interview.id,
                    InterviewMessage.deleted_at.is_(None),
                )
                .order_by(InterviewMessage.sequence)
            )
        ).all()
    )
    return plan, config, style_pack, round_profile, questions, messages


def _expected_dimensions(round_profile: RoundProfile) -> list[str]:
    dimensions = [*BASE_DIMENSIONS, *[str(item) for item in round_profile.evaluation_weights]]
    return list(dict.fromkeys(item.strip() for item in dimensions if item.strip()))


def _question_message_ids(
    questions: Sequence[PlanQuestion],
    messages: Sequence[InterviewMessage],
) -> dict[uuid.UUID, set[uuid.UUID]]:
    result = {question.id: set() for question in questions}
    for message in messages:
        if (
            message.role == MessageRole.USER
            and message.confirmed
            and message.plan_question_id in result
        ):
            result[message.plan_question_id].add(message.id)
    return result


def _evaluation_payload(
    *,
    plan: InterviewPlan,
    config: InterviewConfig,
    style_pack: CompanyStylePack,
    round_profile: RoundProfile,
    questions: Sequence[PlanQuestion],
    messages: Sequence[InterviewMessage],
    expected_dimensions: list[str],
) -> dict:
    return {
        "contract": {
            "style_pack_id": str(style_pack.id),
            "style_pack_version": style_pack.pack_version,
            "plan_id": str(plan.id),
            "plan_version": plan.version,
            "role_name": config.role_name,
            "round_name": round_profile.name,
            "expected_dimensions": expected_dimensions,
        },
        "plan_questions": [
            {
                "plan_question_id": str(question.id),
                "sequence": question.sequence,
                "prompt": question.prompt_snapshot,
                "capability_tags": question.capability_tags,
            }
            for question in questions
        ],
        "interview_messages": [
            {
                "message_id": str(message.id),
                "plan_question_id": str(message.plan_question_id)
                if message.plan_question_id
                else None,
                "sequence": message.sequence,
                "role": str(message.role),
                "confirmed": message.confirmed,
                "content": message.content,
            }
            for message in messages
        ],
    }


async def _trend_comparison(
    session: AsyncSession,
    *,
    interview: InterviewSession,
    config: InterviewConfig,
    current_report_id: uuid.UUID,
) -> dict:
    previous = list(
        (
            await session.scalars(
                select(EvaluationReport)
                .join(InterviewSession, InterviewSession.id == EvaluationReport.session_id)
                .join(InterviewPlan, InterviewPlan.id == InterviewSession.plan_id)
                .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
                .where(
                    EvaluationReport.id != current_report_id,
                    EvaluationReport.status == EvaluationStatus.COMPLETED,
                    InterviewSession.profile_id == interview.profile_id,
                    InterviewConfig.company_id == config.company_id,
                    InterviewConfig.role_name == config.role_name,
                    EvaluationReport.deleted_at.is_(None),
                )
                .order_by(EvaluationReport.updated_at.desc())
                .limit(5)
            )
        ).all()
    )
    if not previous:
        return {}
    return {
        "comparable_session_count": len(previous) + 1,
        "previous_overall_anchors": [str(item.overall_anchor) for item in previous],
        "note": "趋势仅比较同公司、同岗位的已完成场次，不参与本场评分。",
    }


async def evaluate_interview(
    session: AsyncSession,
    interview: InterviewSession,
    *,
    provider: ChatProvider | None = None,
) -> EvaluationReport:
    plan, config, style_pack, round_profile, questions, messages = await _load_evaluation_input(
        session, interview
    )
    report = await ensure_report(session, interview)
    report.status = EvaluationStatus.RUNNING
    report.failure_code = None
    report.touch()
    await session.commit()

    connection = None
    actual_provider = provider
    if actual_provider is None:
        connection = await resolve_role_connection(
            session, interview.profile_id, ModelRole.EVALUATOR
        )
        actual_provider = build_provider(connection)
    dimensions = _expected_dimensions(round_profile)
    question_ids = _question_message_ids(questions, messages)
    payload = _evaluation_payload(
        plan=plan,
        config=config,
        style_pack=style_pack,
        round_profile=round_profile,
        questions=questions,
        messages=messages,
        expected_dimensions=dimensions,
    )
    try:
        draft = await run_evaluator(
            actual_provider,
            evaluation_payload=payload,
            question_message_ids=question_ids,
            expected_dimensions=dimensions,
        )
    finally:
        if provider is None:
            close = getattr(actual_provider, "aclose", None)
            if close:
                await close()
    await _persist_draft(
        session,
        report=report,
        interview=interview,
        config=config,
        draft=draft,
        evaluator_connection_id=connection.id if connection else None,
    )
    return report


async def _persist_draft(
    session: AsyncSession,
    *,
    report: EvaluationReport,
    interview: InterviewSession,
    config: InterviewConfig,
    draft: EvaluationDraft,
    evaluator_connection_id: uuid.UUID | None,
) -> None:
    await session.execute(
        delete(QuestionEvaluation).where(QuestionEvaluation.report_id == report.id)
    )
    await session.execute(
        delete(DimensionEvaluation).where(DimensionEvaluation.report_id == report.id)
    )
    for item in draft.questions:
        session.add(
            QuestionEvaluation(
                report_id=report.id,
                plan_question_id=item.plan_question_id,
                anchor=item.anchor,
                summary=item.summary,
                evidence=[entry.model_dump(mode="json") for entry in item.evidence],
                gaps=item.gaps,
                actions=item.actions,
                confidence=item.confidence,
            )
        )
    for item in draft.dimensions:
        session.add(
            DimensionEvaluation(
                report_id=report.id,
                dimension=item.dimension,
                anchor=item.anchor,
                evidence=[entry.model_dump(mode="json") for entry in item.evidence],
                gaps=item.gaps,
                action=item.action,
                confidence=item.confidence,
            )
        )
    report.overall_anchor = draft.overall_anchor
    report.overview = draft.overview
    report.strengths = draft.strengths
    report.gaps = draft.gaps
    report.action_plan = [item.model_dump(mode="json") for item in draft.action_plan]
    report.trend_comparison = await _trend_comparison(
        session,
        interview=interview,
        config=config,
        current_report_id=report.id,
    )
    report.evaluator_model_connection_id = evaluator_connection_id
    report.status = EvaluationStatus.COMPLETED
    report.failure_code = None
    report.touch()
    interview.status = SessionStatus.COMPLETED
    interview.failure_code = None
    interview.touch()
    await session.commit()
    await session.refresh(report)


async def mark_report_failed(
    session: AsyncSession,
    interview: InterviewSession,
    *,
    code: str,
) -> EvaluationReport:
    report = await ensure_report(session, interview)
    report.status = EvaluationStatus.FAILED
    report.failure_code = code
    report.touch()
    interview.status = SessionStatus.COMPLETED
    interview.failure_code = None
    interview.touch()
    await session.commit()
    return report


def _job_public(job: BackgroundJob) -> EvaluationJobPublic:
    return EvaluationJobPublic.model_validate(job)


async def report_public(
    session: AsyncSession,
    report: EvaluationReport,
) -> EvaluationReportPublic:
    questions = list(
        (
            await session.scalars(
                select(QuestionEvaluation)
                .where(QuestionEvaluation.report_id == report.id)
                .order_by(QuestionEvaluation.created_at)
            )
        ).all()
    )
    plan_questions = {
        item.id: item
        for item in (
            await session.scalars(
                select(PlanQuestion).where(
                    PlanQuestion.id.in_([item.plan_question_id for item in questions])
                )
            )
        ).all()
    }
    dimensions = list(
        (
            await session.scalars(
                select(DimensionEvaluation)
                .where(DimensionEvaluation.report_id == report.id)
                .order_by(DimensionEvaluation.dimension)
            )
        ).all()
    )
    evidence_ids = {
        uuid.UUID(str(entry["message_id"]))
        for item in [*questions, *dimensions]
        for entry in item.evidence
        if entry.get("message_id")
    }
    evidence_messages = list(
        (
            await session.scalars(
                select(InterviewMessage)
                .where(
                    InterviewMessage.id.in_(evidence_ids),
                    InterviewMessage.session_id == report.session_id,
                )
                .order_by(InterviewMessage.sequence)
            )
        ).all()
    ) if evidence_ids else []
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.INTERVIEW_EVALUATION,
            BackgroundJob.payload["session_id"].astext == str(report.session_id),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(1)
    )
    return EvaluationReportPublic(
        id=report.id,
        created_at=report.created_at,
        updated_at=report.updated_at,
        version=report.version,
        session_id=report.session_id,
        status=report.status,
        overall_anchor=report.overall_anchor,
        overview=report.overview,
        strengths=list(report.strengths),
        gaps=list(report.gaps),
        action_plan=[PracticeAction.model_validate(item) for item in report.action_plan],
        trend_comparison=report.trend_comparison,
        completed_at=report.updated_at if report.status == EvaluationStatus.COMPLETED else None,
        questions=[
            QuestionEvaluationPublic(
                id=item.id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                version=item.version,
                plan_question_id=item.plan_question_id,
                question_sequence=plan_questions[item.plan_question_id].sequence,
                question_prompt=plan_questions[item.plan_question_id].prompt_snapshot,
                anchor=item.anchor,
                summary=item.summary,
                evidence=[EvidenceReference.model_validate(entry) for entry in item.evidence],
                gaps=list(item.gaps),
                actions=list(item.actions),
                confidence=item.confidence,
            )
            for item in sorted(
                questions,
                key=lambda row: plan_questions[row.plan_question_id].sequence,
            )
        ],
        dimensions=[
            DimensionEvaluationPublic(
                id=item.id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                version=item.version,
                dimension=item.dimension,
                anchor=item.anchor,
                evidence=[EvidenceReference.model_validate(entry) for entry in item.evidence],
                gaps=list(item.gaps),
                action=item.action,
                confidence=item.confidence,
            )
            for item in dimensions
        ],
        evidence_messages=[
            EvidenceMessagePublic(
                id=item.id,
                plan_question_id=item.plan_question_id,
                sequence=item.sequence,
                content=item.content,
            )
            for item in evidence_messages
        ],
        job=_job_public(job) if job else None,
    )


async def list_reports(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> list[ReportListItem]:
    rows = (
        await session.execute(
            select(
                EvaluationReport,
                InterviewConfig,
                Company,
                RoundProfile,
            )
            .join(InterviewSession, InterviewSession.id == EvaluationReport.session_id)
            .join(InterviewPlan, InterviewPlan.id == InterviewSession.plan_id)
            .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
            .join(Company, Company.id == InterviewConfig.company_id)
            .join(RoundProfile, RoundProfile.id == InterviewConfig.round_profile_id)
            .where(
                InterviewSession.profile_id == profile_id,
                EvaluationReport.deleted_at.is_(None),
            )
            .order_by(EvaluationReport.created_at.desc())
        )
    ).all()
    return [
        ReportListItem(
            report_id=report.id,
            session_id=report.session_id,
            status=report.status,
            overall_anchor=report.overall_anchor,
            overview=report.overview,
            created_at=report.created_at,
            updated_at=report.updated_at,
            company_name=company.name,
            round_name=round_profile.name,
            role_name=config.role_name,
        )
        for report, config, company, round_profile in rows
    ]


async def retry_evaluation(
    session: AsyncSession,
    report: EvaluationReport,
) -> BackgroundJob:
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == JobType.INTERVIEW_EVALUATION,
            BackgroundJob.payload["session_id"].astext == str(report.session_id),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(1)
    )
    if not job:
        interview = await session.get(InterviewSession, report.session_id)
        if not interview:
            raise AppError(
                code="interview_session_not_found",
                message="面试会话不存在",
                status_code=404,
            )
        job = BackgroundJob(
            profile_id=interview.profile_id,
            job_type=JobType.INTERVIEW_EVALUATION,
            idempotency_key=f"interview-evaluation:{interview.id}:retry:{report.version}",
            payload={"session_id": str(interview.id)},
        )
        session.add(job)
    else:
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            return job
        job.status = JobStatus.QUEUED
        job.progress = 0
        job.attempts = 0
        job.error_code = None
        job.error_message = None
        job.result = {}
        job.locked_at = None
        job.locked_by = None
        job.touch()
    report.status = EvaluationStatus.PENDING
    report.failure_code = None
    report.touch()
    await session.commit()
    await session.refresh(job)
    return job
