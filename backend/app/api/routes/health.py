from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import DatabaseStatusDep
from app.core.config import settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    version: str
    database: Literal["connected", "unavailable"]


@router.get("/health", response_model=HealthResponse, operation_id="system-health")
async def health(database: DatabaseStatusDep) -> HealthResponse:
    return HealthResponse(
        status="healthy" if database == "connected" else "degraded",
        version=settings.app_version,
        database=database,
    )
