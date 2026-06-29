from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user_placeholder, RoleChecker
from app.core.responses import APIResponse, success_response

router = APIRouter()
allow_admin = RoleChecker(["admin", "manager"])

@router.get("/system", response_model=APIResponse[dict])
async def get_system_statistics(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(allow_admin)
):
    """
    Get system-wide statistics (generation times, publishing metrics, storage usage).
    """
    return success_response(
        data={"storage_used_mb": 1024, "total_generations": 150},
        message="Fetched system statistics"
    )

@router.get("/reviewers", response_model=APIResponse[list])
async def get_reviewer_performance(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(allow_admin)
):
    """
    Get reviewer performance statistics.
    """
    return success_response(data=[], message="Fetched reviewer performance")
