from fastapi import APIRouter

from app.api.routes import (
    companies,
    context_diagnostics,
    diagnostics,
    embedding_index,
    health,
    interview_plans,
    interview_sessions,
    local_ai,
    memories,
    model_connections,
    question_discoveries,
    questions,
    report_coach,
    reports,
    resumes,
    transcriptions,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(local_ai.router)
api_router.include_router(model_connections.router)
api_router.include_router(embedding_index.router)
api_router.include_router(question_discoveries.router)
api_router.include_router(companies.router)
api_router.include_router(questions.router)
api_router.include_router(reports.router)
api_router.include_router(report_coach.router)
api_router.include_router(resumes.router)
api_router.include_router(transcriptions.router)
api_router.include_router(interview_plans.router)
api_router.include_router(interview_sessions.router)
api_router.include_router(context_diagnostics.router)
api_router.include_router(diagnostics.router)
api_router.include_router(memories.router)
