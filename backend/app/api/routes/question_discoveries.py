"""Discovery connector configuration routes.

Discovery runs, candidates, and imports deliberately live in later milestones. This
module owns only the profile-scoped connector CRUD and diagnostics surface.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import SessionDep
from app.schemas.discovery import (
    DiscoveryConnectorCreate,
    DiscoveryConnectorPublic,
    DiscoveryConnectorTestResult,
    DiscoveryConnectorUpdate,
    DiscoveryImportCreate,
    DiscoveryImportItemPublic,
    DiscoveryImportPublic,
    QuestionDiscoveryCandidateEvidencePublic,
    QuestionDiscoveryCandidatePage,
    QuestionDiscoveryCreate,
    QuestionDiscoveryRunPage,
    QuestionDiscoveryRunPublic,
    QuestionDiscoverySourcePage,
)
from app.services import discovery_connectors as service
from app.services import discovery_imports, question_discovery
from app.services.model_connections import ensure_local_profile

router = APIRouter()
connector_router = APIRouter(prefix="/discovery-connectors", tags=["discovery-connectors"])
run_router = APIRouter(prefix="/question-discoveries", tags=["question-discoveries"])


@connector_router.get("", response_model=list[DiscoveryConnectorPublic])
async def list_discovery_connectors(session: SessionDep) -> list[DiscoveryConnectorPublic]:
    profile = await ensure_local_profile(session)
    connectors = await service.list_connectors(session, profile.id)
    return [service.to_public(connector) for connector in connectors]


@connector_router.post(
    "",
    response_model=DiscoveryConnectorPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_discovery_connector(
    payload: DiscoveryConnectorCreate,
    session: SessionDep,
) -> DiscoveryConnectorPublic:
    profile = await ensure_local_profile(session)
    connector = await service.create_connector(session, profile.id, payload)
    return service.to_public(connector)


@connector_router.get("/{connector_id}", response_model=DiscoveryConnectorPublic)
async def get_discovery_connector(
    connector_id: uuid.UUID,
    session: SessionDep,
) -> DiscoveryConnectorPublic:
    profile = await ensure_local_profile(session)
    connector = await service.get_connector(session, profile.id, connector_id)
    return service.to_public(connector)


@connector_router.patch("/{connector_id}", response_model=DiscoveryConnectorPublic)
async def update_discovery_connector(
    connector_id: uuid.UUID,
    payload: DiscoveryConnectorUpdate,
    session: SessionDep,
) -> DiscoveryConnectorPublic:
    profile = await ensure_local_profile(session)
    connector = await service.get_connector(session, profile.id, connector_id)
    connector = await service.update_connector(session, connector, payload)
    return service.to_public(connector)


@connector_router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_discovery_connector(
    connector_id: uuid.UUID,
    session: SessionDep,
) -> Response:
    profile = await ensure_local_profile(session)
    connector = await service.get_connector(session, profile.id, connector_id)
    await service.delete_connector(session, connector)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@connector_router.post("/{connector_id}/test", response_model=DiscoveryConnectorTestResult)
async def test_discovery_connector(
    connector_id: uuid.UUID,
    session: SessionDep,
) -> DiscoveryConnectorTestResult:
    profile = await ensure_local_profile(session)
    connector = await service.get_connector(session, profile.id, connector_id)
    return await service.test_connector(session, connector)


@run_router.get("", response_model=QuestionDiscoveryRunPage)
async def list_question_discoveries(
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> QuestionDiscoveryRunPage:
    profile = await ensure_local_profile(session)
    return await question_discovery.list_runs(
        session,
        profile.id,
        offset=offset,
        limit=limit,
    )


@run_router.post(
    "",
    response_model=QuestionDiscoveryRunPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_question_discovery(
    payload: QuestionDiscoveryCreate,
    session: SessionDep,
) -> QuestionDiscoveryRunPublic:
    profile = await ensure_local_profile(session)
    run = await question_discovery.create_run(session, profile.id, payload)
    return question_discovery.run_public(run)


@run_router.get("/{run_id}", response_model=QuestionDiscoveryRunPublic)
async def get_question_discovery(
    run_id: uuid.UUID,
    session: SessionDep,
) -> QuestionDiscoveryRunPublic:
    profile = await ensure_local_profile(session)
    run = await question_discovery.get_run(session, profile.id, run_id)
    return question_discovery.run_public(run)


@run_router.get("/{run_id}/sources", response_model=QuestionDiscoverySourcePage)
async def list_question_discovery_sources(
    run_id: uuid.UUID,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> QuestionDiscoverySourcePage:
    profile = await ensure_local_profile(session)
    return await question_discovery.list_sources(
        session,
        profile.id,
        run_id,
        offset=offset,
        limit=limit,
    )


@run_router.get("/{run_id}/candidates", response_model=QuestionDiscoveryCandidatePage)
async def list_question_discovery_candidates(
    run_id: uuid.UUID,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> QuestionDiscoveryCandidatePage:
    profile = await ensure_local_profile(session)
    return await question_discovery.list_candidates(
        session,
        profile.id,
        run_id,
        offset=offset,
        limit=limit,
    )


@run_router.get(
    "/{run_id}/candidates/{candidate_id}/evidence",
    response_model=list[QuestionDiscoveryCandidateEvidencePublic],
)
async def list_question_discovery_candidate_evidence(
    run_id: uuid.UUID,
    candidate_id: uuid.UUID,
    session: SessionDep,
) -> list[QuestionDiscoveryCandidateEvidencePublic]:
    profile = await ensure_local_profile(session)
    return await question_discovery.list_candidate_evidence(
        session,
        profile.id,
        run_id,
        candidate_id,
    )


@run_router.post("/{run_id}/imports", response_model=DiscoveryImportPublic)
async def import_question_discovery_candidates(
    run_id: uuid.UUID,
    payload: DiscoveryImportCreate,
    session: SessionDep,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
) -> DiscoveryImportPublic:
    profile = await ensure_local_profile(session)
    result = await discovery_imports.import_discovery_candidates(
        session,
        profile.id,
        discovery_imports.DiscoveryImportRequest(
            run_id=run_id,
            bank_id=payload.bank_id,
            idempotency_key=idempotency_key,
            items=tuple(
                discovery_imports.DiscoveryImportItem(**item.model_dump()) for item in payload.items
            ),
        ),
    )
    return DiscoveryImportPublic(
        run_id=result.run_id,
        bank_id=result.bank_id,
        batch_id=result.batch_id,
        request_hash=result.request_hash,
        items=[
            DiscoveryImportItemPublic(
                candidate_id=item.candidate_id,
                candidate_revision=item.candidate_revision,
                question_id=item.question_id,
                import_id=item.import_id,
            )
            for item in result.items
        ],
        replayed=result.replayed,
    )


@run_router.post("/{run_id}/cancel", response_model=QuestionDiscoveryRunPublic)
async def cancel_question_discovery(
    run_id: uuid.UUID,
    session: SessionDep,
) -> QuestionDiscoveryRunPublic:
    profile = await ensure_local_profile(session)
    run = await question_discovery.request_cancel(session, profile.id, run_id)
    return question_discovery.run_public(run)


@run_router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question_discovery(run_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await ensure_local_profile(session)
    await question_discovery.delete_run(session, profile.id, run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.include_router(connector_router)
router.include_router(run_router)
