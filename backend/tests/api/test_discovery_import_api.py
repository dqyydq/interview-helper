import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db.models.common import (
    Difficulty,
    DiscoveryProviderType,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    QuestionType,
)
from app.db.models.discovery import (
    DiscoveryConnector,
    QuestionDiscoveryCandidate,
    QuestionDiscoveryCandidateEvidence,
    QuestionDiscoveryImport,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
    QuestionSourceProvenance,
)
from app.db.models.profile import UserProfile
from app.db.models.question import QuestionBank
from app.db.session import async_session_factory, engine
from app.main import app
from app.services.questions import prompt_hash


async def make_importable_candidate() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with async_session_factory() as session:
        profile = UserProfile(display_name=f"Import API profile {uuid.uuid4()}")
        session.add(profile)
        await session.flush()
        connector = DiscoveryConnector(
            profile_id=profile.id,
            name=f"Import API connector {uuid.uuid4()}",
            provider_type=DiscoveryProviderType.TAVILY,
            encrypted_api_key="encrypted",
            capabilities={"supports_extract": True, "safe_extract": True},
        )
        bank = QuestionBank(profile_id=profile.id, name=f"Import API bank {uuid.uuid4()}")
        session.add_all([connector, bank])
        await session.flush()
        run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=1,
            source_mode=DiscoverySourceMode.SEARCH,
        )
        session.add(run)
        await session.flush()
        source = QuestionDiscoverySource(
            profile_id=profile.id,
            run_id=run.id,
            normalized_url="https://interview.acme.cn/notes",
            final_url="https://interview.acme.cn/notes",
            title="Public interview notes",
            domain="interview.acme.cn",
            source_category="community_notes",
            status=DiscoverySourceStatus.FETCHED,
            excerpt="Candidates should explain retrieval evaluation and failure analysis.",
            attribution={"official": False},
        )
        session.add(source)
        await session.flush()
        prompt = "How would you evaluate a retrieval pipeline before launch?"
        candidate = QuestionDiscoveryCandidate(
            profile_id=profile.id,
            run_id=run.id,
            prompt=prompt,
            question_type=QuestionType.SYSTEM_DESIGN,
            difficulty=Difficulty.ADVANCED,
            suggested_tags=["RAG", "evaluation"],
            reference_points=["Offline metrics"],
            follow_up_suggestions=["How would you investigate a regression?"],
            matching_reason="Tests retrieval evaluation depth.",
            confidence=0.9,
            content_hash=prompt_hash(prompt),
        )
        session.add(candidate)
        await session.flush()
        session.add(
            QuestionDiscoveryCandidateEvidence(
                profile_id=profile.id,
                run_id=run.id,
                candidate_id=candidate.id,
                source_id=source.id,
                excerpt="explain retrieval evaluation and failure analysis",
                evidence_hash="a" * 64,
            )
        )
        await session.commit()
        return run.id, bank.id, candidate.id


async def clear_importable_candidate() -> None:
    async with async_session_factory() as session:
        profile_ids = list(
            (
                await session.scalars(
                    select(UserProfile.id).where(
                        UserProfile.display_name.like("Import API profile %")
                    )
                )
            ).all()
        )
        for profile_id in profile_ids:
            await session.execute(
                delete(QuestionSourceProvenance).where(
                    QuestionSourceProvenance.profile_id == profile_id
                )
            )
            await session.execute(
                delete(QuestionDiscoveryImport).where(
                    QuestionDiscoveryImport.profile_id == profile_id
                )
            )
            await session.execute(delete(QuestionBank).where(QuestionBank.profile_id == profile_id))
            await session.execute(
                delete(QuestionDiscoveryRun).where(QuestionDiscoveryRun.profile_id == profile_id)
            )
            await session.execute(
                delete(DiscoveryConnector).where(DiscoveryConnector.profile_id == profile_id)
            )
            await session.execute(delete(UserProfile).where(UserProfile.id == profile_id))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_import_api_rows():
    await clear_importable_candidate()
    yield
    await clear_importable_candidate()
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_endpoint_replays_idempotently_and_exposes_deletable_provenance() -> None:
    run_id, bank_id, candidate_id = await make_importable_candidate()
    body = {
        "bank_id": str(bank_id),
        "items": [{"candidate_id": str(candidate_id), "candidate_revision": 1}],
    }
    headers = {"Idempotency-Key": "import-api-replay-key"}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        imported = await client.post(
            f"/api/question-discoveries/{run_id}/imports",
            json=body,
            headers=headers,
        )
        replay = await client.post(
            f"/api/question-discoveries/{run_id}/imports",
            json=body,
            headers=headers,
        )
        question_id = imported.json()["items"][0]["question_id"]
        question = await client.get(f"/api/questions/{question_id}")
        provenance = await client.get(f"/api/questions/{question_id}/source-provenance")
        provenance_id = provenance.json()[0]["id"]
        deleted = await client.delete(
            f"/api/questions/{question_id}/source-provenance/{provenance_id}"
        )
        after_delete = await client.get(f"/api/questions/{question_id}/source-provenance")

    assert imported.status_code == 200
    assert imported.json()["replayed"] is False
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["items"] == imported.json()["items"]
    assert question.json()["status"] == "draft"
    assert question.json()["source_type"] == "link_import"
    assert provenance.status_code == 200
    assert provenance.json()[0]["normalized_url"] == "https://interview.acme.cn/notes"
    assert deleted.status_code == 204
    assert after_delete.json() == []
