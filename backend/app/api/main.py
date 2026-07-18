from fastapi import APIRouter

from app.api.routes import (
    companies,
    health,
    interview_plans,
    interview_sessions,
    model_connections,
    questions,
    resumes,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(model_connections.router)
api_router.include_router(companies.router)
api_router.include_router(questions.router)
api_router.include_router(resumes.router)
api_router.include_router(interview_plans.router)
api_router.include_router(interview_sessions.router)
