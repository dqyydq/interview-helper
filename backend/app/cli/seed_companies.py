import asyncio

from app.db.session import async_session_factory, dispose_engine
from app.services.seeding import seed_companies


async def main() -> None:
    try:
        async with async_session_factory() as session:
            result = await seed_companies(session)
            print(
                f"company seeds: created={result.created} "
                f"upgraded={result.upgraded} unchanged={result.unchanged}"
            )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
