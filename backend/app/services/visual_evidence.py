"""Ephemeral image-to-evidence extraction for company style-pack drafts."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.input_budget import AgentInputBudgetError
from app.agents.visual_evidence import (
    VisualEvidenceExtractionError,
    VisualEvidenceResult,
    VisualEvidenceRound,
    extract_visual_evidence,
)
from app.api.errors import AppError
from app.core.config import settings
from app.db.models.common import ModelRole
from app.db.models.company import CompanyStylePack, RoundProfile
from app.providers.factory import build_provider
from app.providers.types import ChatImage, ImageMediaType
from app.services import companies
from app.services.model_connections import resolve_explicit_role_connection


@dataclass(frozen=True, slots=True)
class VisualImageUpload:
    media_type: ImageMediaType
    content: bytes


_IMAGE_SIGNATURES: tuple[tuple[ImageMediaType, str, bytes], ...] = (
    (ImageMediaType.JPEG, "image/jpeg", b"\xff\xd8\xff"),
    (ImageMediaType.PNG, "image/png", b"\x89PNG\r\n\x1a\n"),
)


def _detect_image_media_type(content: bytes) -> ImageMediaType | None:
    for media_type, _content_type, signature in _IMAGE_SIGNATURES:
        if content.startswith(signature):
            return media_type
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ImageMediaType.WEBP
    return None


def validate_visual_image_upload(
    *,
    content: bytes,
    content_type: str | None,
) -> VisualImageUpload:
    """Validate one small raster image without writing it to disk.

    MIME type is only a consistency check; signature detection is authoritative.  SVG, PDF and
    animated formats are intentionally excluded from this privacy-sensitive first release.
    """

    if not content:
        raise AppError(code="visual_evidence_image_empty", message="图片内容为空", status_code=422)
    if len(content) > settings.visual_evidence_upload_max_bytes:
        raise AppError(
            code="visual_evidence_image_too_large",
            message="图片超过解析上限，请压缩到 3 MB 以内后重试",
            status_code=413,
        )
    media_type = _detect_image_media_type(content)
    if media_type is None:
        raise AppError(
            code="visual_evidence_image_unsupported",
            message="仅支持 PNG、JPEG 或 WebP 图片",
            status_code=415,
        )
    supplied_type = (content_type or "").split(";", 1)[0].strip().lower()
    if supplied_type and supplied_type != media_type.value:
        raise AppError(
            code="visual_evidence_image_type_mismatch",
            message="图片格式与文件内容不一致，请重新导出图片后重试",
            status_code=415,
        )
    return VisualImageUpload(media_type=media_type, content=content)


async def analyse_visual_evidence(
    session: AsyncSession,
    *,
    profile_id,
    style_pack: CompanyStylePack,
    company_name: str,
    rounds: list[RoundProfile],
    image: VisualImageUpload,
) -> VisualEvidenceResult:
    """Run one visual request against the explicitly bound visual model.

    The image is encoded only for this provider request and is never put in the database, upload
    directory, conversation context, vector index or application logs.
    """

    companies.assert_style_pack_draft(style_pack)
    try:
        connection = await resolve_explicit_role_connection(
            session,
            profile_id,
            ModelRole.VISION_RESEARCHER,
        )
    except AppError as exc:
        if exc.code != "model_role_unbound":
            raise
        raise AppError(
            code="vision_researcher_unbound",
            message="请先在模型设置中为“视觉资料解析”绑定可用模型",
            status_code=409,
        ) from exc

    provider = None
    try:
        provider = build_provider(connection)
        image_payload = base64.b64encode(image.content).decode("ascii")
        async with asyncio.timeout(settings.visual_evidence_request_timeout_seconds):
            return await extract_visual_evidence(
                provider,
                image=ChatImage(
                    source_type="base64",
                    media_type=image.media_type,
                    data=image_payload,
                ),
                company_name=company_name,
                rounds=[
                    VisualEvidenceRound(round_key=item.round_key, name=item.name)
                    for item in rounds
                ],
                context_window_tokens=connection.context_window_tokens,
                max_output_tokens=connection.max_output_tokens,
                tokenizer_type=connection.tokenizer_type,
            )
    except TimeoutError as exc:
        raise AppError(
            code="visual_evidence_timeout",
            message="视觉资料解析等待过久，请稍后重试或换一张更清晰的图片",
            status_code=504,
            retryable=True,
        ) from exc
    except AgentInputBudgetError as exc:
        raise AppError(
            code="visual_evidence_context_budget_exceeded",
            message="当前视觉模型的上下文预算不足，无法安全解析这张图片",
            status_code=422,
        ) from exc
    except VisualEvidenceExtractionError as exc:
        raise AppError(
            code="visual_evidence_output_invalid",
            message="视觉模型未能返回可审核的证据草案，请更换资料或稍后重试",
            status_code=502,
        ) from exc
    finally:
        if provider is not None:
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
