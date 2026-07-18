import hashlib
import re
import unicodedata
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import QuestionStatus, SourceType
from app.db.models.question import (
    Question,
    QuestionBank,
    QuestionTag,
    QuestionTagLink,
    QuestionVariant,
)
from app.schemas.common import Page
from app.schemas.question import (
    QuestionBankCreate,
    QuestionBankPublic,
    QuestionBankUpdate,
    QuestionBulkResult,
    QuestionCreate,
    QuestionPublic,
    QuestionSortField,
    QuestionTagPublic,
    QuestionUpdate,
    QuestionVariantCreate,
    QuestionVariantPublic,
    SortOrder,
)


def normalize_prompt(prompt: str) -> str:
    normalized = unicodedata.normalize("NFKC", prompt)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(normalize_prompt(prompt).encode("utf-8")).hexdigest()


def tag_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).strip().casefold()
    slug = re.sub(r"[^\w\-]+", "-", normalized, flags=re.UNICODE).strip("-")
    return slug or hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


async def get_bank(
    session: AsyncSession,
    profile_id: uuid.UUID,
    bank_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> QuestionBank:
    statement = select(QuestionBank).where(
        QuestionBank.id == bank_id,
        QuestionBank.profile_id == profile_id,
    )
    if not include_archived:
        statement = statement.where(QuestionBank.deleted_at.is_(None))
    bank = await session.scalar(statement)
    if not bank:
        raise AppError(code="question_bank_not_found", message="题库不存在", status_code=404)
    return bank


async def bank_public(session: AsyncSession, bank: QuestionBank) -> QuestionBankPublic:
    question_count = await session.scalar(
        select(func.count()).select_from(Question).where(
            Question.bank_id == bank.id,
            Question.status != QuestionStatus.ARCHIVED,
            Question.deleted_at.is_(None),
        )
    )
    return QuestionBankPublic(
        id=bank.id,
        created_at=bank.created_at,
        updated_at=bank.updated_at,
        version=bank.version,
        name=bank.name,
        description=bank.description,
        visibility=bank.visibility,
        question_count=question_count or 0,
        archived=bank.deleted_at is not None,
    )


async def list_banks(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> list[QuestionBankPublic]:
    statement = select(QuestionBank).where(QuestionBank.profile_id == profile_id)
    if not include_archived:
        statement = statement.where(QuestionBank.deleted_at.is_(None))
    banks = (await session.scalars(statement.order_by(QuestionBank.created_at))).all()
    return [await bank_public(session, bank) for bank in banks]


async def create_bank(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: QuestionBankCreate,
) -> QuestionBankPublic:
    duplicate = await session.scalar(
        select(QuestionBank.id).where(
            QuestionBank.profile_id == profile_id,
            func.lower(QuestionBank.name) == payload.name.strip().casefold(),
        )
    )
    if duplicate:
        raise AppError(code="question_bank_duplicate", message="已存在同名题库", status_code=409)
    bank = QuestionBank(
        profile_id=profile_id,
        name=payload.name.strip(),
        description=payload.description,
        visibility=payload.visibility,
    )
    session.add(bank)
    await session.commit()
    await session.refresh(bank)
    return await bank_public(session, bank)


async def update_bank(
    session: AsyncSession,
    bank: QuestionBank,
    payload: QuestionBankUpdate,
) -> QuestionBankPublic:
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = values["name"].strip()
        duplicate = await session.scalar(
            select(QuestionBank.id).where(
                QuestionBank.profile_id == bank.profile_id,
                func.lower(QuestionBank.name) == values["name"].casefold(),
                QuestionBank.id != bank.id,
            )
        )
        if duplicate:
            raise AppError(
                code="question_bank_duplicate",
                message="已存在同名题库",
                status_code=409,
            )
    for key, value in values.items():
        setattr(bank, key, value)
    bank.touch()
    await session.commit()
    await session.refresh(bank)
    return await bank_public(session, bank)


async def archive_bank(session: AsyncSession, bank: QuestionBank) -> None:
    bank.soft_delete()
    await session.commit()


async def get_question(
    session: AsyncSession,
    profile_id: uuid.UUID,
    question_id: uuid.UUID,
) -> Question:
    question = await session.scalar(
        select(Question)
        .join(QuestionBank)
        .where(
            Question.id == question_id,
            QuestionBank.profile_id == profile_id,
            QuestionBank.deleted_at.is_(None),
            Question.deleted_at.is_(None),
        )
    )
    if not question:
        raise AppError(code="question_not_found", message="题目不存在", status_code=404)
    return question


async def _question_tags(
    session: AsyncSession,
    question_id: uuid.UUID,
) -> Sequence[QuestionTag]:
    result = await session.scalars(
        select(QuestionTag)
        .join(QuestionTagLink, QuestionTagLink.tag_id == QuestionTag.id)
        .where(QuestionTagLink.question_id == question_id)
        .order_by(QuestionTag.name)
    )
    return result.all()


async def _question_variants(
    session: AsyncSession,
    question_id: uuid.UUID,
) -> Sequence[QuestionVariant]:
    result = await session.scalars(
        select(QuestionVariant)
        .where(
            QuestionVariant.question_id == question_id,
            QuestionVariant.deleted_at.is_(None),
        )
        .order_by(QuestionVariant.created_at)
    )
    return result.all()


async def question_public(session: AsyncSession, question: Question) -> QuestionPublic:
    tags = await _question_tags(session, question.id)
    variants = await _question_variants(session, question.id)
    return QuestionPublic(
        id=question.id,
        created_at=question.created_at,
        updated_at=question.updated_at,
        version=question.version,
        bank_id=question.bank_id,
        prompt=question.prompt,
        question_type=question.question_type,
        difficulty=question.difficulty,
        status=question.status,
        reference_points=question.reference_points,
        follow_up_suggestions=question.follow_up_suggestions,
        applicable_companies=question.applicable_companies,
        applicable_rounds=question.applicable_rounds,
        source_type=question.source_type,
        source_note=question.source_note,
        user_note=question.user_note,
        times_used=question.times_used,
        tags=[QuestionTagPublic.model_validate(tag) for tag in tags],
        variants=[QuestionVariantPublic.model_validate(variant) for variant in variants],
    )


async def _replace_tags(
    session: AsyncSession,
    question_id: uuid.UUID,
    tag_names: list[str],
) -> None:
    await session.execute(delete(QuestionTagLink).where(QuestionTagLink.question_id == question_id))
    tag_ids: set[uuid.UUID] = set()
    for raw_name in tag_names:
        name = unicodedata.normalize("NFKC", raw_name).strip()
        if not name:
            continue
        slug = tag_slug(name)
        tag = await session.scalar(select(QuestionTag).where(QuestionTag.slug == slug))
        if not tag:
            tag = QuestionTag(name=name, slug=slug)
            session.add(tag)
            await session.flush()
        tag_ids.add(tag.id)
    session.add_all(
        [QuestionTagLink(question_id=question_id, tag_id=tag_id) for tag_id in tag_ids]
    )


async def create_question(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: QuestionCreate,
) -> QuestionPublic:
    bank = await get_bank(session, profile_id, payload.bank_id)
    normalized_hash = prompt_hash(payload.prompt)
    duplicate = await session.scalar(
        select(Question.id).where(
            Question.bank_id == bank.id,
            Question.normalized_hash == normalized_hash,
            Question.deleted_at.is_(None),
        )
    )
    if duplicate:
        raise AppError(code="question_duplicate", message="题库中已存在相同题目", status_code=409)
    values = payload.model_dump(exclude={"prompt", "tag_names"})
    question = Question(
        **values,
        prompt=payload.prompt.strip(),
        normalized_hash=normalized_hash,
        source_type=SourceType.MANUAL,
    )
    session.add(question)
    await session.flush()
    await _replace_tags(session, question.id, payload.tag_names)
    await session.commit()
    await session.refresh(question)
    return await question_public(session, question)


async def update_question(
    session: AsyncSession,
    question: Question,
    payload: QuestionUpdate,
) -> QuestionPublic:
    values = payload.model_dump(exclude_unset=True, exclude={"tag_names"})
    if "prompt" in values:
        values["prompt"] = values["prompt"].strip()
        values["normalized_hash"] = prompt_hash(values["prompt"])
        duplicate = await session.scalar(
            select(Question.id).where(
                Question.bank_id == question.bank_id,
                Question.normalized_hash == values["normalized_hash"],
                Question.id != question.id,
                Question.deleted_at.is_(None),
            )
        )
        if duplicate:
            raise AppError(
                code="question_duplicate",
                message="题库中已存在相同题目",
                status_code=409,
            )
    for key, value in values.items():
        setattr(question, key, value)
    if payload.tag_names is not None:
        await _replace_tags(session, question.id, payload.tag_names)
    question.touch()
    await session.commit()
    await session.refresh(question)
    return await question_public(session, question)


async def list_questions(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    bank_id: uuid.UUID | None = None,
    status: QuestionStatus | None = None,
    question_type: str | None = None,
    difficulty: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    sort_by: QuestionSortField = "created_at",
    sort_order: SortOrder = "desc",
    offset: int = 0,
    limit: int = 50,
) -> Page[QuestionPublic]:
    filters = [
        QuestionBank.profile_id == profile_id,
        QuestionBank.deleted_at.is_(None),
        Question.deleted_at.is_(None),
    ]
    if bank_id:
        filters.append(Question.bank_id == bank_id)
    if status:
        filters.append(Question.status == status)
    else:
        filters.append(Question.status != QuestionStatus.ARCHIVED)
    if question_type:
        filters.append(Question.question_type == question_type)
    if difficulty:
        filters.append(Question.difficulty == difficulty)
    if search:
        filters.append(Question.prompt.ilike(f"%{search.strip()}%"))
    if tag:
        tag_filter = (
            select(QuestionTagLink.question_id)
            .join(QuestionTag, QuestionTag.id == QuestionTagLink.tag_id)
            .where(QuestionTag.slug == tag_slug(tag))
        )
        filters.append(Question.id.in_(tag_filter))

    base = select(Question).join(QuestionBank).where(*filters)
    count = await session.scalar(select(func.count()).select_from(base.subquery()))
    sort_column = getattr(Question, sort_by)
    ordering = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    rows = (
        await session.scalars(base.order_by(ordering, Question.id).offset(offset).limit(limit))
    ).all()
    return Page[QuestionPublic](
        data=[await question_public(session, item) for item in rows],
        count=count or 0,
        offset=offset,
        limit=limit,
    )


async def archive_questions(
    session: AsyncSession,
    profile_id: uuid.UUID,
    question_ids: list[uuid.UUID],
) -> QuestionBulkResult:
    questions = (
        await session.scalars(
            select(Question)
            .join(QuestionBank)
            .where(
                Question.id.in_(question_ids),
                QuestionBank.profile_id == profile_id,
                QuestionBank.deleted_at.is_(None),
                Question.deleted_at.is_(None),
            )
        )
    ).all()
    for question in questions:
        question.status = QuestionStatus.ARCHIVED
        question.touch()
    await session.commit()
    return QuestionBulkResult(updated=len(questions))


async def create_variant(
    session: AsyncSession,
    question: Question,
    payload: QuestionVariantCreate,
) -> QuestionVariantPublic:
    variant = QuestionVariant(
        question_id=question.id,
        prompt=payload.prompt.strip(),
        variant_type=payload.variant_type,
    )
    session.add(variant)
    await session.commit()
    await session.refresh(variant)
    return QuestionVariantPublic.model_validate(variant)
