from fastapi import APIRouter
from app.api.v1.endpoints import (
    reports,
    reviews,
    comments,
    workflow,
    assignments,
    versions,
    publishing,
    search,
    dashboard,
    statistics,
    auth,
    internal
)

api_router = APIRouter()

# Public API routes
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
api_router.include_router(reviews.router, prefix="/reports", tags=["Reviews"]) 
api_router.include_router(comments.router, prefix="/reports", tags=["Comments"])
api_router.include_router(workflow.router, prefix="/reports", tags=["Workflow"])
api_router.include_router(assignments.router, prefix="/assignments", tags=["Assignments"])
api_router.include_router(versions.router, prefix="/reports", tags=["Versions"])
api_router.include_router(publishing.router, prefix="/reports", tags=["Publishing"])
api_router.include_router(search.router, prefix="/search", tags=["Search"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(statistics.router, prefix="/statistics", tags=["Statistics"])

# Internal worker API routes
internal_router = APIRouter()
internal_router.include_router(internal.router, tags=["Internal API"])
