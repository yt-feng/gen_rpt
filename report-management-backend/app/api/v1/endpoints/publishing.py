from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_db, get_current_user_placeholder, RoleChecker
from app.core.responses import APIResponse, success_response

router = APIRouter()
allow_admin = RoleChecker(["admin"])

@router.post("/{document_id}/publish", response_model=APIResponse[dict])
async def publish_document(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(allow_admin) # Only admin can publish
):
    """
    Publish request placeholder for Alibaba OSS Integration.
    """
    return success_response(data={"document_id": str(document_id)}, message="Publish request queued")

@router.get("/{document_id}/publish/status", response_model=APIResponse[dict])
async def get_publish_status(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_placeholder)
):
    """
    Get current publish status.
    """
    return success_response(data={"status": "pending"}, message="Fetched publish status")
