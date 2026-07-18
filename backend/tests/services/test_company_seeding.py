import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.session import async_session_factory, engine
from app.services.seeding import seed_companies


async def clear_companies() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Company))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_company_seeds():
    await clear_companies()
    yield
    await clear_companies()
    await engine.dispose()


@pytest.mark.asyncio
async def test_company_seeds_are_safe_and_idempotent() -> None:
    async with async_session_factory() as session:
        first = await seed_companies(session)
        second = await seed_companies(session)
        company_count = await session.scalar(select(func.count()).select_from(Company))
        pack_count = await session.scalar(select(func.count()).select_from(CompanyStylePack))
        round_count = await session.scalar(select(func.count()).select_from(RoundProfile))
        companies = (await session.scalars(select(Company).order_by(Company.slug))).all()
        packs = (await session.scalars(select(CompanyStylePack))).all()

    assert first.created == 6
    assert first.unchanged == 0
    assert first.upgraded == 0
    assert second.created == 0
    assert second.unchanged == 6
    assert second.upgraded == 0
    assert company_count == 6
    assert pack_count == 6
    assert round_count == 18
    assert all(company.is_system for company in companies)
    assert all(company.profile_id is None for company in companies)
    assert all("不代表官方结论" in (company.description or "") for company in companies)
    assert all(not pack.default_interviewer_behavior for pack in packs)
    assert all(not pack.field_confidence for pack in packs)
