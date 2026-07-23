from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.main import api_router
from app.api.middleware import RequestIdMiddleware, SecurityHeadersMiddleware
from app.api.routes.interview_live import router as interview_live_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def frontend_origins() -> list[str]:
    configured = str(settings.frontend_origin).rstrip("/")
    origins = {configured}
    if settings.environment in {"local", "test"}:
        parsed = urlsplit(configured)
        port = f":{parsed.port}" if parsed.port else ""
        origins.update({f"{parsed.scheme}://localhost{port}", f"{parsed.scheme}://127.0.0.1{port}"})
    return sorted(origins)


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=frontend_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    application.include_router(api_router, prefix=settings.api_prefix)
    application.include_router(interview_live_router, prefix=settings.api_prefix)
    register_error_handlers(application)
    return application


app = create_app()
