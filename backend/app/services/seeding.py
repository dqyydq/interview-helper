from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import REPOSITORY_ROOT
from app.db.models.common import ContentStatus, Visibility
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.schemas.company import CompanySeedResult, RoundProfileCreate

DEFAULT_COMPANY_SEED_DIR = REPOSITORY_ROOT / "seed" / "companies"


class SeedStylePack(BaseModel):
    name: str
    status: ContentStatus = ContentStatus.ACTIVE
    visibility: Visibility = Visibility.PRIVATE
    supported_roles: list[str] = Field(default_factory=list)
    default_interviewer_behavior: dict = Field(default_factory=dict)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    rounds: list[RoundProfileCreate]


class SeedCompany(BaseModel):
    seed_version: int = Field(ge=1)
    name: str
    slug: str
    description: str
    style_pack: SeedStylePack


def load_company_seed(path: Path) -> SeedCompany:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SeedCompany.model_validate(raw)


def list_company_seed_paths(seed_dir: Path) -> list[Path]:
    return sorted(seed_dir.glob("*.yaml"))


async def seed_companies(
    session: AsyncSession,
    seed_dir: Path = DEFAULT_COMPANY_SEED_DIR,
) -> CompanySeedResult:
    created = 0
    unchanged = 0
    upgraded = 0
    company_ids = []

    for path in list_company_seed_paths(seed_dir):
        seed = load_company_seed(path)
        company = await session.scalar(select(Company).where(Company.slug == seed.slug))
        company_created = company is None
        if company is None:
            company = Company(
                profile_id=None,
                name=seed.name,
                slug=seed.slug,
                description=seed.description,
                is_system=True,
            )
            session.add(company)
            await session.flush()
        else:
            changed = company.name != seed.name or company.description != seed.description
            if changed:
                company.name = seed.name
                company.description = seed.description
                company.touch()
        company_ids.append(company.id)

        existing_pack = await session.scalar(
            select(CompanyStylePack).where(
                CompanyStylePack.company_id == company.id,
                CompanyStylePack.pack_version == seed.seed_version,
            )
        )
        if existing_pack:
            unchanged += 1
            continue

        if seed.style_pack.status == ContentStatus.ACTIVE:
            active_packs = await session.scalars(
                select(CompanyStylePack).where(
                    CompanyStylePack.company_id == company.id,
                    CompanyStylePack.status == ContentStatus.ACTIVE,
                    CompanyStylePack.deleted_at.is_(None),
                )
            )
            for active_pack in active_packs.all():
                active_pack.status = ContentStatus.ARCHIVED
                active_pack.touch()

        style_pack = CompanyStylePack(
            company_id=company.id,
            name=seed.style_pack.name,
            pack_version=seed.seed_version,
            supported_roles=seed.style_pack.supported_roles,
            default_interviewer_behavior=seed.style_pack.default_interviewer_behavior,
            field_confidence=seed.style_pack.field_confidence,
            status=seed.style_pack.status,
            visibility=seed.style_pack.visibility,
        )
        session.add(style_pack)
        await session.flush()
        session.add_all(
            [
                RoundProfile(style_pack_id=style_pack.id, **round_seed.model_dump())
                for round_seed in seed.style_pack.rounds
            ]
        )
        if company_created:
            created += 1
        else:
            upgraded += 1

    await session.commit()
    return CompanySeedResult(
        created=created,
        unchanged=unchanged,
        upgraded=upgraded,
        company_ids=company_ids,
    )
