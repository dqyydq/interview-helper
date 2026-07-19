from fastapi import APIRouter

from app.api.routes import (
    companies,
    context_diagnostics,
    health,
    interview_plans,
    interview_sessions,
    memories,
    model_connections,
    questions,
    report_coach,
    reports,
    resumes,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(model_connections.router)
api_router.include_router(companies.router)
api_router.include_router(questions.router)
api_router.include_router(reports.router)
api_router.include_router(report_coach.router)
api_router.include_router(resumes.router)
api_router.include_router(interview_plans.router)
api_router.include_router(interview_sessions.router)
api_router.include_router(context_diagnostics.router)
api_router.include_router(memories.router)
