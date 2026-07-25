import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.db.models.common import EmbeddingProfileStatus, MemoryType
from app.db.models.embedding import EmbeddingProfile, MemoryEmbedding
from app.db.models.memory import MemoryItem
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine


@pytest_asyncio.fixture(autouse=True)
async def dispose_pool_after_test():
    yield
    await engine.dispose()


def _local_profile(*, profile_id, status=EmbeddingProfileStatus.BUILDING, dimensions=None):
    return EmbeddingProfile(
        profile_id=profile_id,
        local_capability_key="multilingual-e5-small",
        target_fingerprint="a" * 64,
        model_name="intfloat/multilingual-e5-small",
        model_revision="bdd905ef05181adf3ebbfaac5cd5bd4ed9a58760",
        vector_dimensions=dimensions,
        status=status,
    )


@pytest.mark.asyncio
async def test_embedding_profile_allows_dimension_discovery_only_while_building() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Embedding lifecycle test")
        session.add(profile)
        await session.flush()

        embedding_profile = _local_profile(profile_id=profile.id)
        session.add(embedding_profile)
        await session.flush()

        embedding_profile.vector_dimensions = 384
        await session.flush()

        embedding_profile.status = EmbeddingProfileStatus.ACTIVE
        await session.flush()

        embedding_profile.vector_dimensions = 1024
        with pytest.raises(IntegrityError):
            await session.flush()

        await session.rollback()


@pytest.mark.asyncio
async def test_memory_embedding_cannot_reference_another_profiles_memory() -> None:
    async with async_session_factory() as session:
        owner = UserProfile(display_name="Embedding owner")
        other_profile = UserProfile(display_name="Embedding other profile")
        session.add_all([owner, other_profile])
        await session.flush()

        embedding_profile = _local_profile(
            profile_id=owner.id,
            status=EmbeddingProfileStatus.ACTIVE,
            dimensions=384,
        )
        foreign_memory = MemoryItem(
            profile_id=other_profile.id,
            memory_type=MemoryType.PROJECT_FACT,
            canonical_key="project:foreign",
            content="This source belongs to another profile.",
        )
        session.add_all([embedding_profile, foreign_memory])
        await session.flush()

        session.add(
            MemoryEmbedding(
                profile_id=owner.id,
                embedding_profile_id=embedding_profile.id,
                memory_id=foreign_memory.id,
                content_hash="b" * 64,
                embedding=[0.1, 0.2, 0.3],
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()

        await session.rollback()
