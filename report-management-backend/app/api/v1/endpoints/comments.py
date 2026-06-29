from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_db, get_current_user_placeholder
from app.core.responses import APIResponse, success_response

router = APIRouter()

@router.get("/{document_id}/comments", response_model=APIResponse[list])
async def list_comments(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    List threaded comments for a document.
    """
    return success_response(data=[], message="Fetched comments")

@router.post("/{document_id}/comments", response_model=APIResponse[dict])
async def create_comment(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Create a new comment or reply.
    """
    return success_response(data={"id": "placeholder"}, message="Created comment")
