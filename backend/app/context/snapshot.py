import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.context import ContextSnapshot
from app.providers.types import Usage


async def finalize_context_snapshot(
    session: AsyncSession,
    snapshot_id: uuid.UUID | None,
    usage: Usage | None,
    *,
    provider_request_id: str | None = None,
) -> None:
    if not snapshot_id:
        return
    snapshot = await session.get(ContextSnapshot, snapshot_id)
    if not snapshot:
        return
    if usage:
        snapshot.input_tokens = usage.input_tokens or snapshot.input_tokens
        snapshot.output_tokens = usage.output_tokens
    if provider_request_id:
        snapshot.provider_request_id = provider_request_id
    snapshot.touch()
    await session.commit()
