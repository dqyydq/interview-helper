"""Private, report-derived training queue operations."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import EvaluationStatus, PracticeTaskStatus, utc_now
from app.db.models.evaluation import EvaluationReport
from app.db.models.interview import InterviewSession
from app.db.models.practice import PracticeTask
from app.schemas.evaluation import PracticeAction
from app.schemas.practice import (
    PracticeTaskCreateFromReport,
    PracticeTaskPublic,
    PracticeTaskUpdate,
)


def task_public(task: PracticeTask) -> PracticeTaskPublic:
    return PracticeTaskPublic(
        id=task.id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        version=task.version,
        report_id=task.source_report_id,
        action_index=task.action_index,
        title=task.title,
        instruction=task.instruction,
        success_criteria=task.success_criteria,
        priority=task.priority,
        status=task.status,
        last_session_id=task.last_session_id,
        completed_at=task.completed_at,
    )


async def get_practice_task(
    session: AsyncSession,
    profile_id: uuid.UUID,
    task_id: uuid.UUID,
) -> PracticeTask:
    task = await session.scalar(
        select(PracticeTask).where(
            PracticeTask.id == task_id,
            PracticeTask.profile_id == profile_id,
            PracticeTask.deleted_at.is_(None),
        )
    )
    if not task:
        raise AppError(
            code="practice_task_not_found",
            message="Practice task was not found.",
            status_code=404,
        )
    return task


async def list_practice_tasks(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    status: PracticeTaskStatus | None = None,
) -> list[PracticeTask]:
    statement = select(PracticeTask).where(
        PracticeTask.profile_id == profile_id,
        PracticeTask.deleted_at.is_(None),
    )
    if status is not None:
        statement = statement.where(PracticeTask.status == status)
    statement = statement.order_by(
        PracticeTask.status,
        PracticeTask.priority,
        PracticeTask.created_at.desc(),
    )
    return list((await session.scalars(statement)).all())


async def _report_for_profile(
    session: AsyncSession,
    profile_id: uuid.UUID,
    report_id: uuid.UUID,
) -> EvaluationReport:
    report = await session.scalar(
        select(EvaluationReport)
        .join(InterviewSession, InterviewSession.id == EvaluationReport.session_id)
        .where(
            EvaluationReport.id == report_id,
            EvaluationReport.deleted_at.is_(None),
            InterviewSession.profile_id == profile_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    if not report:
        raise AppError(
            code="evaluation_report_not_found",
            message="Evaluation report was not found.",
            status_code=404,
        )
    return report


def _requested_action_indexes(payload: PracticeTaskCreateFromReport) -> list[int]:
    # Preserve the caller's displayed order while making a repeated index no-op.
    ordered = list(dict.fromkeys(payload.action_indices))
    if any(index < 0 for index in ordered):
        raise AppError(
            code="practice_action_index_invalid",
            message="Action indexes must be zero or greater.",
            status_code=422,
        )
    return ordered


async def create_tasks_from_report(
    session: AsyncSession,
    profile_id: uuid.UUID,
    report_id: uuid.UUID,
    payload: PracticeTaskCreateFromReport,
) -> list[PracticeTask]:
    """Persist selected action-plan snapshots, without accepting client task text."""

    report = await _report_for_profile(session, profile_id, report_id)
    if report.status != EvaluationStatus.COMPLETED:
        raise AppError(
            code="evaluation_report_not_ready",
            message="Practice tasks are available after the evaluation report completes.",
            status_code=409,
        )
    indexes = _requested_action_indexes(payload)
    if any(index >= len(report.action_plan) for index in indexes):
        raise AppError(
            code="practice_action_index_not_found",
            message="One or more selected report actions are unavailable.",
            status_code=422,
        )

    existing = {
        task.action_index: task
        for task in (
            await session.scalars(
                select(PracticeTask).where(
                    PracticeTask.profile_id == profile_id,
                    PracticeTask.source_report_id == report.id,
                    PracticeTask.action_index.in_(indexes),
                    PracticeTask.deleted_at.is_(None),
                )
            )
        ).all()
    }
    for action_index in indexes:
        if action_index in existing:
            continue
        action = PracticeAction.model_validate(report.action_plan[action_index])
        task = PracticeTask(
            profile_id=profile_id,
            source_report_id=report.id,
            action_index=action_index,
            title=action.title,
            instruction=action.instruction,
            success_criteria=action.success_criteria,
            priority=action.priority,
        )
        session.add(task)
        existing[action_index] = task
    await session.commit()

    # Reload so the response remains canonical after an idempotent repeat.
    persisted = {
        task.action_index: task
        for task in (
            await session.scalars(
                select(PracticeTask).where(
                    PracticeTask.profile_id == profile_id,
                    PracticeTask.source_report_id == report.id,
                    PracticeTask.action_index.in_(indexes),
                    PracticeTask.deleted_at.is_(None),
                )
            )
        ).all()
    }
    return [persisted[index] for index in indexes]


_ALLOWED_STATUS_TRANSITIONS: dict[PracticeTaskStatus, set[PracticeTaskStatus]] = {
    PracticeTaskStatus.PENDING: {
        PracticeTaskStatus.PENDING,
        PracticeTaskStatus.IN_PROGRESS,
        PracticeTaskStatus.COMPLETED,
        PracticeTaskStatus.DISMISSED,
    },
    PracticeTaskStatus.IN_PROGRESS: {
        PracticeTaskStatus.PENDING,
        PracticeTaskStatus.IN_PROGRESS,
        PracticeTaskStatus.COMPLETED,
        PracticeTaskStatus.DISMISSED,
    },
    PracticeTaskStatus.COMPLETED: {
        PracticeTaskStatus.COMPLETED,
        PracticeTaskStatus.PENDING,
    },
    PracticeTaskStatus.DISMISSED: {
        PracticeTaskStatus.DISMISSED,
        PracticeTaskStatus.PENDING,
    },
}


async def update_practice_task(
    session: AsyncSession,
    task: PracticeTask,
    payload: PracticeTaskUpdate,
) -> PracticeTask:
    next_status = payload.status
    current_status = PracticeTaskStatus(task.status)
    if next_status not in _ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise AppError(
            code="practice_task_status_transition_invalid",
            message="That practice-task status transition is not available.",
            status_code=409,
        )
    if next_status != current_status:
        task.status = next_status
        task.completed_at = utc_now() if next_status == PracticeTaskStatus.COMPLETED else None
        task.touch()
        await session.commit()
        await session.refresh(task)
    return task


async def get_plannable_practice_task(
    session: AsyncSession,
    profile_id: uuid.UUID,
    task_id: uuid.UUID | None,
) -> PracticeTask | None:
    """Validate a task reference before it becomes part of an interview plan."""

    if task_id is None:
        return None
    task = await get_practice_task(session, profile_id, task_id)
    if task.status in {PracticeTaskStatus.COMPLETED, PracticeTaskStatus.DISMISSED}:
        raise AppError(
            code="practice_task_not_available",
            message="Completed or dismissed practice tasks cannot start a new session.",
            status_code=409,
        )
    return task


def planner_focus(task: PracticeTask | None) -> dict | None:
    """Bound task context that may guide ordering but cannot replace source questions."""

    if task is None:
        return None
    return {
        "task_id": str(task.id),
        "title": task.title[:200],
        "instruction": task.instruction[:1_000],
        "success_criteria": task.success_criteria[:600],
        "priority": task.priority,
    }


async def link_task_to_session(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    task_id: uuid.UUID | None,
    interview_id: uuid.UUID,
) -> PracticeTask | None:
    """Associate a newly created session and move an active task into progress."""

    task = await get_plannable_practice_task(session, profile_id, task_id)
    if task is None:
        return None
    task.last_session_id = interview_id
    if task.status == PracticeTaskStatus.PENDING:
        task.status = PracticeTaskStatus.IN_PROGRESS
    task.completed_at = None
    task.touch()
    return task
