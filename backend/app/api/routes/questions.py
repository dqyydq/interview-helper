import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import SessionDep
from app.db.models.common import Difficulty, QuestionStatus, QuestionType
from app.schemas.common import Page
from app.schemas.question import (
    QuestionBankCreate,
    QuestionBankPublic,
    QuestionBankUpdate,
    QuestionBulkArchive,
    QuestionBulkResult,
    QuestionCreate,
    QuestionPublic,
    QuestionSortField,
    QuestionUpdate,
    QuestionVariantCreate,
    QuestionVariantPublic,
    SortOrder,
)
from app.services import questions as service
from app.services.model_connections import ensure_local_profile

router = APIRouter(tags=["questions"])


@router.get("/question-banks", response_model=list[QuestionBankPublic])
async def list_question_banks(
    session: SessionDep,
    include_archived: bool = Query(default=False),
) -> list[QuestionBankPublic]:
    profile = await ensure_local_profile(session)
    return await service.list_banks(session, profile.id, include_archived=include_archived)


@router.post(
    "/question-banks",
    response_model=QuestionBankPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_question_bank(
    payload: QuestionBankCreate,
    session: SessionDep,
) -> QuestionBankPublic:
    profile = await ensure_local_profile(session)
    return await service.create_bank(session, profile.id, payload)


@router.get("/question-banks/{bank_id}", response_model=QuestionBankPublic)
async def get_question_bank(bank_id: uuid.UUID, session: SessionDep) -> QuestionBankPublic:
    profile = await ensure_local_profile(session)
    bank = await service.get_bank(session, profile.id, bank_id)
    return await service.bank_public(session, bank)


@router.patch("/question-banks/{bank_id}", response_model=QuestionBankPublic)
async def update_question_bank(
    bank_id: uuid.UUID,
    payload: QuestionBankUpdate,
    session: SessionDep,
) -> QuestionBankPublic:
    profile = await ensure_local_profile(session)
    bank = await service.get_bank(session, profile.id, bank_id)
    return await service.update_bank(session, bank, payload)


@router.delete("/question-banks/{bank_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_question_bank(bank_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await ensure_local_profile(session)
    bank = await service.get_bank(session, profile.id, bank_id)
    await service.archive_bank(session, bank)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/questions", response_model=Page[QuestionPublic])
async def list_questions(
    session: SessionDep,
    bank_id: uuid.UUID | None = None,
    question_status: Annotated[QuestionStatus | None, Query(alias="status")] = None,
    question_type: QuestionType | None = None,
    difficulty: Difficulty | None = None,
    tag: str | None = Query(default=None, max_length=100),
    search: str | None = Query(default=None, max_length=500),
    sort_by: QuestionSortField = "created_at",
    sort_order: SortOrder = "desc",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[QuestionPublic]:
    profile = await ensure_local_profile(session)
    return await service.list_questions(
        session,
        profile.id,
        bank_id=bank_id,
        status=question_status,
        question_type=question_type,
        difficulty=difficulty,
        tag=tag,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        offset=offset,
        limit=limit,
    )


@router.post("/questions", response_model=QuestionPublic, status_code=status.HTTP_201_CREATED)
async def create_question(payload: QuestionCreate, session: SessionDep) -> QuestionPublic:
    profile = await ensure_local_profile(session)
    return await service.create_question(session, profile.id, payload)


@router.post("/questions/bulk-archive", response_model=QuestionBulkResult)
async def bulk_archive_questions(
    payload: QuestionBulkArchive,
    session: SessionDep,
) -> QuestionBulkResult:
    profile = await ensure_local_profile(session)
    return await service.archive_questions(session, profile.id, payload.question_ids)


@router.get("/questions/{question_id}", response_model=QuestionPublic)
async def get_question(question_id: uuid.UUID, session: SessionDep) -> QuestionPublic:
    profile = await ensure_local_profile(session)
    question = await service.get_question(session, profile.id, question_id)
    return await service.question_public(session, question)


@router.patch("/questions/{question_id}", response_model=QuestionPublic)
async def update_question(
    question_id: uuid.UUID,
    payload: QuestionUpdate,
    session: SessionDep,
) -> QuestionPublic:
    profile = await ensure_local_profile(session)
    question = await service.get_question(session, profile.id, question_id)
    return await service.update_question(session, question, payload)


@router.delete("/questions/{question_id}", response_model=QuestionBulkResult)
async def archive_question(question_id: uuid.UUID, session: SessionDep) -> QuestionBulkResult:
    profile = await ensure_local_profile(session)
    return await service.archive_questions(session, profile.id, [question_id])


@router.post(
    "/questions/{question_id}/variants",
    response_model=QuestionVariantPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_question_variant(
    question_id: uuid.UUID,
    payload: QuestionVariantCreate,
    session: SessionDep,
) -> QuestionVariantPublic:
    profile = await ensure_local_profile(session)
    question = await service.get_question(session, profile.id, question_id)
    return await service.create_variant(session, question, payload)
