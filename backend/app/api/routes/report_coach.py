import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.agents.coach import run_coach
from app.api.deps import SessionDep
from app.api.errors import AppError
from app.db.models.common import ModelRole
from app.db.models.evaluation import QuestionEvaluation
from app.db.models.interview import AnswerAttachment, InterviewMessage, InterviewSession
from app.providers.factory import build_provider
from app.schemas.evaluation import CoachModeRequest, CoachResponse, EvidenceReference
from app.services import evaluation as evaluation_service
from app.services.model_connections import ensure_local_profile, resolve_role_connection

router = APIRouter(prefix="/reports", tags=["report-coach"])


@router.post("/{report_id}/coach", response_model=CoachResponse)
async def coach_report(
    report_id: uuid.UUID,
    payload: CoachModeRequest,
    session: SessionDep,
) -> CoachResponse:
    profile = await ensure_local_profile(session)
    report = await evaluation_service.get_report(session, profile.id, report_id)
    question_evaluation = None
    if payload.question_evaluation_id:
        question_evaluation = await session.scalar(
            select(QuestionEvaluation).where(
                QuestionEvaluation.id == payload.question_evaluation_id,
                QuestionEvaluation.report_id == report.id,
            )
        )
        if not question_evaluation:
            raise AppError(
                code="question_evaluation_not_found",
                message="逐题评估不存在",
                status_code=404,
            )
    if payload.mode == "rewrite" and not question_evaluation:
        raise AppError(
            code="coach_question_required",
            message="示范重答需要选择一道题",
            status_code=422,
        )

    evidence = [
        EvidenceReference.model_validate(item)
        for item in (question_evaluation.evidence if question_evaluation else [])
    ]
    evidence_ids = {item.message_id for item in evidence}
    messages = (
        list(
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
        )
        if evidence_ids
        else []
    )
    attachments = (
        list(
            (
                await session.scalars(
                    select(AnswerAttachment)
                    .where(
                        AnswerAttachment.message_id.in_(evidence_ids),
                        AnswerAttachment.deleted_at.is_(None),
                    )
                    .order_by(AnswerAttachment.created_at)
                )
            ).all()
        )
        if evidence_ids
        else []
    )
    attachments_by_message: dict[uuid.UUID, list[AnswerAttachment]] = {}
    for attachment in attachments:
        attachments_by_message.setdefault(attachment.message_id, []).append(attachment)
    interview = await session.get(InterviewSession, report.session_id)
    if not interview:
        raise AppError(
            code="interview_session_not_found",
            message="面试会话不存在",
            status_code=404,
        )
    context = {
        "request": {"mode": payload.mode, "focus": payload.focus},
        "report": {
            "overall_anchor": str(report.overall_anchor),
            "overview": report.overview,
            "strengths": report.strengths,
            "gaps": report.gaps,
            "action_plan": report.action_plan,
        },
        "selected_question": (
            {
                "anchor": str(question_evaluation.anchor),
                "summary": question_evaluation.summary,
                "gaps": question_evaluation.gaps,
                "actions": question_evaluation.actions,
            }
            if question_evaluation
            else None
        ),
        "original_answers": [
            {
                "message_id": str(item.id),
                "content": item.content,
                **(
                    {
                        "attachments": [
                            {
                                "id": str(attachment.id),
                                "language": attachment.language,
                                "filename": attachment.filename,
                                "content": attachment.content,
                                "execution_allowed": False,
                            }
                            for attachment in attachments_by_message.get(item.id, [])
                        ]
                    }
                    if attachments_by_message.get(item.id)
                    else {}
                ),
            }
            for item in messages
        ],
    }
    connection = await resolve_role_connection(session, profile.id, ModelRole.COACH)
    provider = build_provider(connection)
    try:
        return await run_coach(
            provider,
            mode=payload.mode,
            report_context=context,
            allowed_message_ids=evidence_ids,
        )
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()
