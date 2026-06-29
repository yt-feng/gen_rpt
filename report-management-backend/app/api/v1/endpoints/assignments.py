from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_db, get_current_user_placeholder, RoleChecker
from app.core.responses import APIResponse, success_response

router = APIRouter()
allow_admin = RoleChecker(["admin", "manager"])

@router.get("/queue", response_model=APIResponse[list])
async def get_reviewer_queue(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get workload queue for reviewers.
    """
    return success_response(data=[], message="Fetched reviewer queue")

@router.post("/{document_id}/assign", response_model=APIResponse[dict])
async def assign_reviewer(
    document_id: UUID,
    reviewer_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(allow_admin) # Only admin/managers can assign
):
    """
    Assign a reviewer to a document.
    """
    return success_response(data={"document_id": str(document_id), "reviewer_id": reviewer_id}, message="Reviewer assigned")
