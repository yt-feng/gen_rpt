from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response

router = APIRouter()

@router.get("/metrics", response_model=APIResponse[dict])
async def get_dashboard_metrics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get aggregated metrics for the dashboard (e.g. pending reviews, published reports).
    """
    return success_response(
        data={
            "pending_reviews": 5,
            "approved_reports": 12,
            "revision_queue": 3,
            "published_reports": 142
        },
        message="Fetched dashboard metrics"
    )

@router.get("/recent", response_model=APIResponse[list])
async def get_recent_documents(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get recently active documents for the dashboard.
    """
    return success_response(data=[], message="Fetched recent documents")
