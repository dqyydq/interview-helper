import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import SessionStatus
from app.db.models.interview import InterviewSession
from app.services.evaluation import evaluate_interview


async def handle_evaluate_interview(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
):
    interview = await session.get(InterviewSession, session_id)
    if not interview or interview.deleted_at is not None:
        raise AppError(
            code="interview_session_not_found",
            message="面试会话不存在",
            status_code=404,
        )
    if interview.status not in {SessionStatus.COMPLETED, SessionStatus.EVALUATING}:
        raise AppError(
            code="evaluation_session_not_ready",
            message="面试尚未结束，不能开始评估",
            status_code=409,
        )
    if interview.status == SessionStatus.COMPLETED:
        interview.status = SessionStatus.EVALUATING
        interview.touch()
        await session.commit()
    return await evaluate_interview(session, interview)
