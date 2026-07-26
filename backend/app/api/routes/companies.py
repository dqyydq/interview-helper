import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from pydantic import AnyHttpUrl

from app.api.deps import SessionDep
from app.api.errors import AppError
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
    VisualEvidenceCandidatePublic,
    VisualEvidenceExtractionPublic,
)
from app.services import companies as service
from app.services import visual_evidence
from app.services.model_connections import ensure_local_profile

router = APIRouter(tags=["companies"])


@router.get("/companies", response_model=list[CompanyPublic])
async def list_companies(
    session: SessionDep,
    include_archived: bool = Query(default=False),
) -> list[CompanyPublic]:
    profile = await ensure_local_profile(session)
    companies = await service.list_companies(
        session,
        profile.id,
        include_archived=include_archived,
    )
    return [await service.company_public(session, item) for item in companies]


@router.post("/companies", response_model=CompanyPublic, status_code=status.HTTP_201_CREATED)
async def create_company(payload: CompanyCreate, session: SessionDep) -> CompanyPublic:
    profile = await ensure_local_profile(session)
    company = await service.create_company(session, profile.id, payload)
    return await service.company_public(session, company)


@router.get("/companies/{company_id}", response_model=CompanyPublic)
async def get_company(company_id: uuid.UUID, session: SessionDep) -> CompanyPublic:
    profile = await ensure_local_profile(session)
    company = await service.get_company(session, profile.id, company_id)
    return await service.company_public(session, company)


@router.patch("/companies/{company_id}", response_model=CompanyPublic)
async def update_company(
    company_id: uuid.UUID,
    payload: CompanyUpdate,
    session: SessionDep,
) -> CompanyPublic:
    profile = await ensure_local_profile(session)
    company = await service.get_company(session, profile.id, company_id)
    company = await service.update_company(session, company, payload)
    return await service.company_public(session, company)


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_company(company_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await ensure_local_profile(session)
    company = await service.get_company(session, profile.id, company_id)
    await service.archive_company(session, company)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/companies/{company_id}/style-packs",
    response_model=CompanyStylePackPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_style_pack_revision(
    company_id: uuid.UUID,
    payload: StylePackRevisionCreate,
    session: SessionDep,
) -> CompanyStylePackPublic:
    profile = await ensure_local_profile(session)
    company = await service.get_company(session, profile.id, company_id)
    style_pack = await service.create_style_pack_revision(session, company, payload)
    return await service.style_pack_public(session, style_pack)


@router.patch("/style-packs/{style_pack_id}", response_model=CompanyStylePackPublic)
async def update_style_pack(
    style_pack_id: uuid.UUID,
    payload: StylePackUpdate,
    session: SessionDep,
) -> CompanyStylePackPublic:
    profile = await ensure_local_profile(session)
    style_pack = await service.get_style_pack(session, style_pack_id, profile.id)
    style_pack = await service.update_style_pack(session, style_pack, payload)
    return await service.style_pack_public(session, style_pack)


@router.post("/style-packs/{style_pack_id}/activate", response_model=CompanyStylePackPublic)
async def activate_style_pack(
    style_pack_id: uuid.UUID,
    session: SessionDep,
) -> CompanyStylePackPublic:
    profile = await ensure_local_profile(session)
    style_pack = await service.get_style_pack(session, style_pack_id, profile.id)
    style_pack = await service.activate_style_pack(session, style_pack)
    return await service.style_pack_public(session, style_pack)


@router.post(
    "/style-packs/{style_pack_id}/rounds",
    response_model=RoundProfilePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_round(
    style_pack_id: uuid.UUID,
    payload: RoundProfileCreate,
    session: SessionDep,
) -> RoundProfilePublic:
    profile = await ensure_local_profile(session)
    style_pack = await service.get_style_pack(session, style_pack_id, profile.id)
    result = await service.create_round(session, style_pack, payload)
    return RoundProfilePublic.model_validate(result)


@router.patch("/rounds/{round_id}", response_model=RoundProfilePublic)
async def update_round(
    round_id: uuid.UUID,
    payload: RoundProfileUpdate,
    session: SessionDep,
) -> RoundProfilePublic:
    profile = await ensure_local_profile(session)
    round_profile = await service.get_round(session, round_id, profile.id)
    style_pack = await service.get_style_pack(session, round_profile.style_pack_id, profile.id)
    result = await service.update_round(session, style_pack, round_profile, payload)
    return RoundProfilePublic.model_validate(result)


@router.delete("/rounds/{round_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_round(round_id: uuid.UUID, session: SessionDep) -> Response:
    profile = await ensure_local_profile(session)
    round_profile = await service.get_round(session, round_id, profile.id)
    style_pack = await service.get_style_pack(session, round_profile.style_pack_id, profile.id)
    await service.delete_round(session, style_pack, round_profile)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/style-packs/{style_pack_id}/evidence",
    response_model=EvidenceItemPublic,
    status_code=status.HTTP_201_CREATED,
)
async def add_evidence(
    style_pack_id: uuid.UUID,
    payload: EvidenceItemCreate,
    session: SessionDep,
) -> EvidenceItemPublic:
    profile = await ensure_local_profile(session)
    style_pack = await service.get_style_pack(session, style_pack_id, profile.id)
    item = await service.add_evidence(session, style_pack, payload)
    return EvidenceItemPublic.model_validate(item)


@router.post(
    "/style-packs/{style_pack_id}/evidence/visual-extract",
    response_model=VisualEvidenceExtractionPublic,
)
async def extract_visual_evidence_drafts(
    style_pack_id: uuid.UUID,
    session: SessionDep,
    image: Annotated[UploadFile, File(description="PNG、JPEG 或 WebP 截图")],
    source_url: Annotated[AnyHttpUrl, Form()],
    source_title: Annotated[str, Form(min_length=1, max_length=500)],
    source_confirmed: Annotated[bool, Form()],
) -> VisualEvidenceExtractionPublic:
    """Turn one transient screenshot into review-only evidence candidates.

    The original file is read with a hard size cap and discarded after the visual model response;
    this endpoint never creates an evidence record by itself.
    """

    if not source_confirmed:
        raise AppError(
            code="visual_evidence_source_unconfirmed",
            message="请先确认资料已脱敏且你有权将其发送给当前视觉模型",
            status_code=422,
        )
    normalized_title = source_title.strip()
    if not normalized_title:
        raise AppError(
            code="visual_evidence_source_title_empty",
            message="请填写来源标题，方便后续审核证据",
            status_code=422,
        )
    raw_image = await image.read(visual_evidence.settings.visual_evidence_upload_max_bytes + 1)
    await image.close()
    validated_image = visual_evidence.validate_visual_image_upload(
        content=raw_image,
        content_type=image.content_type,
    )
    profile = await ensure_local_profile(session)
    style_pack = await service.get_style_pack(session, style_pack_id, profile.id)
    company = await service.get_company(session, profile.id, style_pack.company_id)
    rounds = await service.list_style_pack_rounds(session, style_pack.id)
    result = await visual_evidence.analyse_visual_evidence(
        session,
        profile_id=profile.id,
        style_pack=style_pack,
        company_name=company.name,
        rounds=rounds,
        image=validated_image,
    )
    return VisualEvidenceExtractionPublic(
        source_url=source_url,
        source_title=normalized_title,
        candidates=[
            VisualEvidenceCandidatePublic(
                field_path=item.field_path,
                excerpt=item.excerpt,
                confidence=item.confidence,
            )
            for item in result.candidates
        ],
        allowed_field_paths=list(result.allowed_field_paths),
        warning_codes=list(result.warning_codes),
        image_retained=False,
    )
