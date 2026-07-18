from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import database_healthcheck, get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_database_status() -> str:
    return await database_healthcheck()


DatabaseStatusDep = Annotated[str, Depends(get_database_status)]
