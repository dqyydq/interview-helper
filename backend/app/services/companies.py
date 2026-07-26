import hashlib
import re
import unicodedata
import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import ContentStatus
from app.db.models.company import Company, CompanyStylePack, EvidenceItem, RoundProfile
from app.schemas.company import (
    CompanyCreate,
    CompanyPublic,
    CompanyStylePackPublic,
    CompanyUpdate,
    EvidenceItemCreate,
    EvidenceItemPublic,
    RoundProfileCreate,
    RoundProfilePublic,
    RoundProfileUpdate,
    StylePackRevisionCreate,
    StylePackUpdate,
)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    slug = re.sub(r"[^\w\-]+", "-", normalized, flags=re.UNICODE).strip("-")
    return slug or f"company-{uuid.uuid4().hex[:10]}"


def _accessible_company(profile_id: uuid.UUID):
    return or_(Company.profile_id.is_(None), Company.profile_id == profile_id)


async def list_companies(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> Sequence[Company]:
    statement = select(Company).where(_accessible_company(profile_id))
    if not include_archived:
        statement = statement.where(Company.deleted_at.is_(None))
    result = await session.scalars(statement.order_by(Company.is_system.desc(), Company.name))
    return result.all()


async def get_company(
    session: AsyncSession,
    profile_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> Company:
    statement = select(Company).where(
        Company.id == company_id,
        _accessible_company(profile_id),
    )
    if not include_archived:
        statement = statement.where(Company.deleted_at.is_(None))
    company = await session.scalar(statement)
    if not company:
        raise AppError(code="company_not_found", message="公司不存在", status_code=404)
    return company


async def _latest_style_pack(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> CompanyStylePack | None:
    return await session.scalar(
        select(CompanyStylePack)
        .where(
            CompanyStylePack.company_id == company_id,
            CompanyStylePack.deleted_at.is_(None),
        )
        .order_by(CompanyStylePack.pack_version.desc())
        .limit(1)
    )


async def get_style_pack(
    session: AsyncSession,
    style_pack_id: uuid.UUID,
    profile_id: uuid.UUID | None = None,
) -> CompanyStylePack:
    statement = select(CompanyStylePack).where(
        CompanyStylePack.id == style_pack_id,
        CompanyStylePack.deleted_at.is_(None),
    )
    if profile_id is not None:
        statement = statement.join(Company).where(_accessible_company(profile_id))
    style_pack = await session.scalar(statement)
    if not style_pack:
        raise AppError(code="style_pack_not_found", message="风格包不存在", status_code=404)
    return style_pack


async def _rounds(session: AsyncSession, style_pack_id: uuid.UUID) -> Sequence[RoundProfile]:
    result = await session.scalars(
        select(RoundProfile)
        .where(
            RoundProfile.style_pack_id == style_pack_id,
            RoundProfile.deleted_at.is_(None),
        )
        .order_by(RoundProfile.sequence)
    )
    return result.all()


async def list_style_pack_rounds(
    session: AsyncSession,
    style_pack_id: uuid.UUID,
) -> list[RoundProfile]:
    """Return the current round rows for an already-authorised style pack."""

    return list(await _rounds(session, style_pack_id))


async def _evidence(session: AsyncSession, style_pack_id: uuid.UUID) -> Sequence[EvidenceItem]:
    result = await session.scalars(
        select(EvidenceItem)
        .where(
            EvidenceItem.style_pack_id == style_pack_id,
            EvidenceItem.deleted_at.is_(None),
        )
        .order_by(EvidenceItem.field_path, EvidenceItem.created_at)
    )
    return result.all()


def _round_public(round_profile: RoundProfile) -> RoundProfilePublic:
    return RoundProfilePublic.model_validate(round_profile)


def _evidence_public(item: EvidenceItem) -> EvidenceItemPublic:
    return EvidenceItemPublic.model_validate(item)


async def style_pack_public(
    session: AsyncSession,
    style_pack: CompanyStylePack,
) -> CompanyStylePackPublic:
    rounds = await _rounds(session, style_pack.id)
    evidence = await _evidence(session, style_pack.id)
    if evidence:
        evidence_label = "有来源支持"
    elif style_pack.status == ContentStatus.DRAFT:
        evidence_label = "自定义草案 · 未提供来源"
    else:
        evidence_label = "轮次骨架 · 非风格结论"
    return CompanyStylePackPublic(
        id=style_pack.id,
        created_at=style_pack.created_at,
        updated_at=style_pack.updated_at,
        version=style_pack.version,
        name=style_pack.name,
        pack_version=style_pack.pack_version,
        supported_roles=style_pack.supported_roles,
        default_interviewer_behavior=style_pack.default_interviewer_behavior,
        field_confidence=style_pack.field_confidence,
        status=ContentStatus(style_pack.status),
        visibility=style_pack.visibility,
        evidence_count=len(evidence),
        evidence_label=evidence_label,
        rounds=[_round_public(item) for item in rounds],
        evidence=[_evidence_public(item) for item in evidence],
    )


async def company_public(session: AsyncSession, company: Company) -> CompanyPublic:
    style_pack = await _latest_style_pack(session, company.id)
    return CompanyPublic(
        id=company.id,
        created_at=company.created_at,
        updated_at=company.updated_at,
        version=company.version,
        name=company.name,
        slug=company.slug,
        description=company.description,
        is_system=company.is_system,
        archived=company.deleted_at is not None,
        latest_style_pack=(
            await style_pack_public(session, style_pack) if style_pack is not None else None
        ),
    )


async def create_company(
    session: AsyncSession,
    profile_id: uuid.UUID,
    payload: CompanyCreate,
) -> Company:
    company = Company(
        profile_id=profile_id,
        name=payload.name,
        slug=slugify(payload.slug or payload.name),
        description=payload.description,
        is_system=False,
    )
    session.add(company)
    await session.flush()
    style_pack = CompanyStylePack(
        company_id=company.id,
        pack_version=1,
        status=ContentStatus.DRAFT,
        **payload.style_pack.model_dump(),
    )
    session.add(style_pack)
    await session.flush()
    session.add_all(
        [RoundProfile(style_pack_id=style_pack.id, **item.model_dump()) for item in payload.rounds]
    )
    await session.commit()
    await session.refresh(company)
    return company


async def update_company(
    session: AsyncSession,
    company: Company,
    payload: CompanyUpdate,
) -> Company:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    company.touch()
    await session.commit()
    await session.refresh(company)
    return company


async def archive_company(session: AsyncSession, company: Company) -> None:
    company.soft_delete()
    await session.commit()


async def create_style_pack_revision(
    session: AsyncSession,
    company: Company,
    payload: StylePackRevisionCreate,
) -> CompanyStylePack:
    current = await _latest_style_pack(session, company.id)
    next_version = (current.pack_version + 1) if current else 1
    style_pack = CompanyStylePack(
        company_id=company.id,
        pack_version=next_version,
        name=payload.name,
        supported_roles=payload.supported_roles,
        default_interviewer_behavior=payload.default_interviewer_behavior,
        field_confidence=payload.field_confidence,
        visibility=payload.visibility,
        status=ContentStatus.DRAFT,
    )
    session.add(style_pack)
    await session.flush()

    if payload.rounds is not None:
        rounds = payload.rounds
    elif current:
        rounds = [
            RoundProfileCreate.model_validate(item) for item in await _rounds(session, current.id)
        ]
    else:
        rounds = []
    session.add_all(
        [RoundProfile(style_pack_id=style_pack.id, **item.model_dump()) for item in rounds]
    )
    if current and payload.copy_evidence:
        for item in await _evidence(session, current.id):
            session.add(
                EvidenceItem(
                    style_pack_id=style_pack.id,
                    source_url=item.source_url,
                    source_title=item.source_title,
                    field_path=item.field_path,
                    excerpt=item.excerpt,
                    published_at=item.published_at,
                    fetched_at=item.fetched_at,
                    confidence=item.confidence,
                    source_hash=item.source_hash,
                )
            )
    await session.commit()
    await session.refresh(style_pack)
    return style_pack


def _assert_draft(style_pack: CompanyStylePack) -> None:
    if style_pack.status != ContentStatus.DRAFT:
        raise AppError(
            code="style_pack_immutable",
            message="已启用的风格包不可直接修改，请创建新版本",
            status_code=409,
        )


def assert_style_pack_draft(style_pack: CompanyStylePack) -> None:
    """Expose the same immutable-pack guard to adjacent company workflows."""

    _assert_draft(style_pack)


async def update_style_pack(
    session: AsyncSession,
    style_pack: CompanyStylePack,
    payload: StylePackUpdate,
) -> CompanyStylePack:
    _assert_draft(style_pack)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(style_pack, key, value)
    style_pack.touch()
    await session.commit()
    await session.refresh(style_pack)
    return style_pack


async def activate_style_pack(
    session: AsyncSession,
    style_pack: CompanyStylePack,
) -> CompanyStylePack:
    rounds_count = await session.scalar(
        select(func.count())
        .select_from(RoundProfile)
        .where(
            RoundProfile.style_pack_id == style_pack.id,
            RoundProfile.deleted_at.is_(None),
        )
    )
    if not rounds_count:
        raise AppError(code="rounds_required", message="至少需要一个面试轮次", status_code=409)
    active_packs = await session.scalars(
        select(CompanyStylePack).where(
            CompanyStylePack.company_id == style_pack.company_id,
            CompanyStylePack.status == ContentStatus.ACTIVE,
            CompanyStylePack.id != style_pack.id,
            CompanyStylePack.deleted_at.is_(None),
        )
    )
    for item in active_packs.all():
        item.status = ContentStatus.ARCHIVED
        item.touch()
    style_pack.status = ContentStatus.ACTIVE
    style_pack.touch()
    await session.commit()
    await session.refresh(style_pack)
    return style_pack


async def get_round(
    session: AsyncSession,
    round_id: uuid.UUID,
    profile_id: uuid.UUID | None = None,
) -> RoundProfile:
    statement = select(RoundProfile).where(
        RoundProfile.id == round_id,
        RoundProfile.deleted_at.is_(None),
    )
    if profile_id is not None:
        statement = (
            statement.join(CompanyStylePack).join(Company).where(_accessible_company(profile_id))
        )
    round_profile = await session.scalar(statement)
    if not round_profile:
        raise AppError(code="round_not_found", message="面试轮次不存在", status_code=404)
    return round_profile


async def create_round(
    session: AsyncSession,
    style_pack: CompanyStylePack,
    payload: RoundProfileCreate,
) -> RoundProfile:
    _assert_draft(style_pack)
    round_profile = RoundProfile(style_pack_id=style_pack.id, **payload.model_dump())
    session.add(round_profile)
    await session.commit()
    await session.refresh(round_profile)
    return round_profile


async def update_round(
    session: AsyncSession,
    style_pack: CompanyStylePack,
    round_profile: RoundProfile,
    payload: RoundProfileUpdate,
) -> RoundProfile:
    _assert_draft(style_pack)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(round_profile, key, value)
    round_profile.touch()
    await session.commit()
    await session.refresh(round_profile)
    return round_profile


async def delete_round(
    session: AsyncSession,
    style_pack: CompanyStylePack,
    round_profile: RoundProfile,
) -> None:
    _assert_draft(style_pack)
    round_profile.soft_delete()
    await session.commit()


async def add_evidence(
    session: AsyncSession,
    style_pack: CompanyStylePack,
    payload: EvidenceItemCreate,
) -> EvidenceItem:
    _assert_draft(style_pack)
    source_material = f"{payload.source_url}|{payload.field_path}|{payload.excerpt}"
    item = EvidenceItem(
        style_pack_id=style_pack.id,
        source_url=str(payload.source_url),
        source_title=payload.source_title,
        field_path=payload.field_path,
        excerpt=payload.excerpt,
        published_at=payload.published_at,
        confidence=payload.confidence,
        source_hash=hashlib.sha256(source_material.encode()).hexdigest(),
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item
