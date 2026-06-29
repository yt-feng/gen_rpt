from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response

router = APIRouter()

@router.get("/{document_id}/reviews/ai", response_model=APIResponse[dict])
async def get_ai_review(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get AI review summary, findings, and scores.
    """
    return success_response(data={"document_id": str(document_id)}, message="Fetched AI review")

@router.get("/{document_id}/reviews/human", response_model=APIResponse[list])
async def get_human_reviews(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get human review records.
    """
    return success_response(data=[], message="Fetched human reviews")
