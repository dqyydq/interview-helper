"""Explicit, background-only management for semantic memory indexing."""

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.memory.embedding_index import enqueue_embedding_rebuild
from app.schemas.embedding_index import EmbeddingIndexRebuildResult, EmbeddingIndexStatusPublic
from app.services import embedding_indexes, model_connections

router = APIRouter(prefix="/embedding-index", tags=["embedding-index"])


@router.get("", response_model=EmbeddingIndexStatusPublic)
async def get_embedding_index_status(session: SessionDep) -> EmbeddingIndexStatusPublic:
    """Read index state without probing or invoking an embedding model."""

    profile = await model_connections.ensure_local_profile(session)
    return await embedding_indexes.get_embedding_index_status(session, profile.id)


@router.post(
    "/rebuild",
    response_model=EmbeddingIndexRebuildResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rebuild_embedding_index(session: SessionDep) -> EmbeddingIndexRebuildResult:
    """Queue one immutable rebuild; the worker yields while an interview is live."""

    profile = await model_connections.ensure_local_profile(session)
    result = await enqueue_embedding_rebuild(session, profile.id)
    return EmbeddingIndexRebuildResult(
        embedding_profile=embedding_indexes.embedding_profile_public(result.embedding_profile),
        job=embedding_indexes.embedding_index_job_public(result.job),
        created=result.created,
    )
