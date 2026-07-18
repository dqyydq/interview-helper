from fastapi import APIRouter

from app.api.routes import health, model_connections

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(model_connections.router)
